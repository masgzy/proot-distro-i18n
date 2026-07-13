#
# Proot-Distro - manage proot containers.
#
# Created by Sylirre <sylirre@termux.dev> for Termux project.
# Development assisted by Claude Code (https://claude.ai/code).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

# Architecture: The pull pipeline.
#
#   1. Check the local manifest cache. If present, decide whether all
#      layer blobs are already on disk (fully-offline branch) or only
#      a token + the missing layers need to be fetched.
#   2. On manifest miss, resolve the registry manifest. Manifest-list
#      indexes are unwrapped to the arch-specific child manifest.
#   3. For each layer: skip when cached, otherwise download_blob. Apply
#      the layer onto the supplied rootfs directory.
#   4. Return a small metadata dict the caller can use to write
#      containers/<name>/manifest.json and surface image labels.

import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from proot_distro.constants import PROGRAM_NAME
from proot_distro.message import log_info, log_error
from proot_distro.progress import fmt_size
from proot_distro.helpers.download import retry_http
from proot_distro.helpers.docker.cache import (
    all_layers_cached,
    layer_cache_path,
    load_manifest_cache,
    save_manifest_cache,
)
from proot_distro.helpers.docker.layers import apply_layer, download_blob
from proot_distro.helpers.docker.media import (
    DOCKER_MANIFEST_LIST_MEDIA,
    DOCKER_MANIFEST_MEDIA,
    OCI_INDEX_MEDIA,
    OCI_MANIFEST_MEDIA,
)
from proot_distro.helpers.docker.refs import ARCH_TO_DOCKER, parse_image_ref
from proot_distro.helpers.docker.transport import (
    auth_denied_msg,
    auth_note,
    get_auth_token,
    opener,
    _ua,
)
from proot_distro.i18n import _


# Manifest media types treated as an index (multi-arch list).
_MANIFEST_LIST_TYPES = frozenset({
    DOCKER_MANIFEST_LIST_MEDIA, OCI_INDEX_MEDIA,
})

# Accepted manifest media types, ordered by preference (index first).
_ACCEPT_HEADER = ", ".join([
    OCI_INDEX_MEDIA,
    DOCKER_MANIFEST_LIST_MEDIA,
    OCI_MANIFEST_MEDIA,
    DOCKER_MANIFEST_MEDIA,
])


def _get_manifest(
    repo: str, ref: str, token: str, base: str,
    insecure: bool = False,
) -> dict:
    url = f"{base}/v2/{repo}/manifests/{ref}"
    headers = {**_ua(), "Accept": _ACCEPT_HEADER}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)

    def _attempt():
        with opener(insecure).open(req) as resp:
            return resp.read(), resp.headers.get("Content-Type", "")

    body, ct = retry_http(_attempt, what=f"Fetching manifest {ref}")
    data = json.loads(body)
    # Prefer the Content-Type header; fall back to the mediaType field.
    data["_ct"] = ct.split(";")[0].strip() or data.get("mediaType", "")
    return data


def _pick_platform(
    entries: list, arch: str, variant: str, image_ref: str
) -> dict:
    """Find the manifest list entry matching arch (and optionally variant)."""
    # Exact match first (arch + non-empty variant must match).
    for entry in entries:
        plat = entry.get("platform", {})
        if plat.get("os", "linux") != "linux":
            continue
        if plat.get("architecture") != arch:
            continue
        if variant and plat.get("variant", "") not in (variant, ""):
            continue
        return entry

    # Variant-agnostic fallback.
    for entry in entries:
        plat = entry.get("platform", {})
        if (plat.get("os", "linux") == "linux"
                and plat.get("architecture") == arch):
            return entry

    available = []
    for e in entries:
        plat = e.get("platform", {})
        if plat.get("os", "linux") != "linux":
            continue
        a = plat.get("architecture", "?")
        v = plat.get("variant", "")
        available.append(f"{a}/{v}" if v else a)
    raise RuntimeError(
        f"No image found for architecture '{arch}' in '{image_ref}'. "
        f"Available Linux platforms: {', '.join(available) or 'none'}. "
        f"Visit https://hub.docker.com to look for alternatives."
    )


def _resolve_single_manifest(
    image_ref: str, arch: str, insecure: bool = False
) -> tuple:
    """Return (single_image_manifest, token, repo, base) for the arch."""
    registry, repo, tag = parse_image_ref(image_ref)

    log_info(_("Authenticating with registry{_arg0}...", _arg0 = auth_note()))
    token, base = get_auth_token(repo, registry, insecure=insecure)

    log_info(_("Fetching manifest for '{image_ref}'...", image_ref = image_ref))
    manifest = _get_manifest(repo, tag, token, base, insecure)

    if manifest["_ct"] in _MANIFEST_LIST_TYPES or "manifests" in manifest:
        docker_arch, docker_variant = ARCH_TO_DOCKER.get(arch, (arch, ""))
        target = _pick_platform(
            manifest.get("manifests", []),
            docker_arch,
            docker_variant,
            image_ref,
        )
        log_info(_("Fetching {arch} manifest...", arch = arch))
        manifest = _get_manifest(
            repo, target["digest"], token, base, insecure
        )

    return manifest, token, repo, base


def _fetch_config_blob(
    repo: str, cfg_digest: str, token: str, base: str,
    insecure: bool = False,
) -> dict:
    """Fetch the image config blob; return parsed dict (empty on error)."""
    if not cfg_digest:
        return {}
    try:
        url = f"{base}/v2/{repo}/blobs/{cfg_digest}"
        headers = {**_ua()}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers)

        def _attempt():
            with opener(insecure).open(req) as resp:
                return resp.read()

        return json.loads(retry_http(_attempt, what="Fetching image config"))
    except Exception:
        return {}


def _get_download_workers() -> int:
    """Return the number of concurrent download threads.

    Reads ``PD_PARALLEL_DOWNLOADS`` (default 4). Values ≤ 1 fall back to
    serial download. Capped at 8 to avoid overwhelming the registry or
    the local filesystem with too many concurrent writes.
    """
    val = os.environ.get("PD_PARALLEL_DOWNLOADS", "")
    try:
        n = int(val)
    except (ValueError, TypeError):
        return 4
    return max(1, min(n, 8))


def _download_layers_concurrent(
    layers_to_download: list,
    repo: str,
    token: str,
    base: str,
    insecure: bool,
    n_layers: int,
    image_ref: str,
) -> dict:
    """Download multiple layer blobs concurrently.

    *layers_to_download* is a list of ``(index, layer_dict)`` tuples for
    layers that are not in the local cache.

    Returns ``{digest: local_path}``. On the first failure, remaining
    un-started futures are cancelled and the error is re-raised so the
    caller can translate 401/403 into ``auth_denied_msg``.
    """
    n = len(layers_to_download)
    max_workers = min(_get_download_workers(), n)

    if max_workers <= 1 or n <= 1:
        # Serial path — keeps per-layer progress bars.
        results = {}
        for idx, layer in layers_to_download:
            digest = layer["digest"]
            short_id = digest.split(":")[-1][:12]
            size = layer.get("size", 0)
            size_str = f" ({fmt_size(size)})" if size else ""
            log_info(f"{short_id}: Downloading layer "
                     f"{idx + 1}/{n_layers}{size_str}...")
            try:
                results[digest] = download_blob(
                    repo, digest, token, base, insecure,
                )
            except urllib.error.HTTPError as dl_err:
                if dl_err.code in (401, 403):
                    raise RuntimeError(
                        auth_denied_msg(image_ref, dl_err.code)
                    ) from dl_err
                raise
        return results

    # Concurrent path — quiet per-layer bars to avoid terminal clobbering.
    log_info(_("Downloading {n} layer(s) with {workers} parallel workers...",
               n=n, workers=max_workers))

    results: dict = {}
    first_error: tuple | None = None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_info: dict = {}
        for idx, layer in layers_to_download:
            digest = layer["digest"]
            short_id = digest.split(":")[-1][:12]
            size = layer.get("size", 0)
            size_str = f" ({fmt_size(size)})" if size else ""
            log_info(f"{short_id}: Queued layer {idx + 1}/{n_layers}{size_str}")
            future = pool.submit(
                download_blob,
                repo, digest, token, base, insecure,
                quiet=True,
            )
            future_to_info[future] = (idx, digest, short_id)

        done = 0
        for future in as_completed(future_to_info):
            idx, digest, short_id = future_to_info[future]
            if first_error is not None:
                # An earlier future already failed; just drain remaining.
                continue
            try:
                path = future.result()
                results[digest] = path
                done += 1
                log_info(f"{short_id}: Downloaded "
                         f"({done}/{n})")
            except Exception as exc:
                first_error = (idx, digest, short_id, exc)
                # Cancel un-started futures so we don't waste bandwidth.
                for f in future_to_info:
                    f.cancel()

    if first_error is not None:
        idx, digest, short_id, exc = first_error
        if isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403):
            raise RuntimeError(
                auth_denied_msg(image_ref, exc.code)
            ) from exc
        raise RuntimeError(
            f"Failed to download layer {short_id} for '{image_ref}': {exc}"
        ) from exc

    return results


def pull_image(
    image_ref: str, rootfs_dir: str, arch: str, insecure: bool = False
) -> dict:
    """Pull an OCI/Docker image and extract all layers into *rootfs_dir*.

    The manifest is checked in the local cache first. If cached and
    every layer is present, the install runs entirely without network
    access. If the manifest is cached but some layers are missing, only
    an auth token is fetched before downloading the missing layers.

    Layer downloads run concurrently (default 4 threads, configurable
    via ``PD_PARALLEL_DOWNLOADS``). Layers are applied to *rootfs_dir*
    in manifest order — OCI layers are ordered diffs and must be stacked
    sequentially, but their *downloads* are independent.

    Registry traffic uses verified HTTPS unless *insecure* is set. With
    *insecure* a custom registry is reached over HTTPS with certificate
    verification disabled, falling back to plain HTTP when the registry only
    speaks HTTP (Docker Hub stays verified-HTTPS regardless). When enforcing
    HTTPS, an untrusted certificate or an HTTP-only registry surfaces a
    RuntimeError pointing the user at ``--allow-insecure``.

    Returns ``{"manifest": ..., "image_config": ...}``. The caller is
    expected to persist these into ``containers/<name>/manifest.json``
    so `run`, `reset`, and `login` can later read image_config.
    """
    token = None
    base = None

    manifest, repo, image_config = load_manifest_cache(image_ref, arch)
    registry = parse_image_ref(image_ref)[0]

    if manifest is not None:
        layers = manifest.get("layers", [])
        if all_layers_cached(layers):
            log_info(_("Image '{image_ref}' ({arch}) is cached.", image_ref = image_ref, arch = arch))
        else:
            # Count missing layers in a single pass instead of re-iterating.
            missing_layers = [
                (i, layer) for i, layer in enumerate(layers)
                if not os.path.isfile(layer_cache_path(layer["digest"]))
            ]
            missing = len(missing_layers)
            log_info(_("Downloading {missing} missing layer(s) for '{image_ref}' ({arch})...",
                        missing=missing, image_ref=image_ref, arch=arch))
            try:
                log_info(_("Authenticating with registry{_arg0}...", _arg0 = auth_note()))
                token, base = get_auth_token(
                    repo, registry, insecure=insecure
                )
            except (urllib.error.URLError, OSError) as net_err:
                if isinstance(net_err, urllib.error.HTTPError):
                    if net_err.code in (401, 403):
                        raise RuntimeError(
                            auth_denied_msg(image_ref, net_err.code)
                        ) from net_err
                    if net_err.code == 404:
                        raise RuntimeError(
                            f"Image not found: '{image_ref}' does not "
                            f"exist on the registry."
                        ) from net_err
                log_error(f"{missing} of {len(layers)} layer(s) for "
                          f"'{image_ref}' ({arch}) are not in the local "
                          f"cache.")
                raise RuntimeError(
                    f"Network error: {net_err}\n"
                    f"Tip: run '{PROGRAM_NAME} mirror test' to check "
                    f"mirror reachability and switch to a faster one "
                    f"with '{PROGRAM_NAME} mirror use <ID>'."
                ) from net_err
    else:
        # 缓存未命中 — 必须从 registry 拉取 manifest。
        # 原版 bug: 此分支缺失，manifest 为 None 时直接到 layers =
        # manifest.get(...) 导致 AttributeError。
        try:
            manifest, token, repo, base = _resolve_single_manifest(
                image_ref, arch, insecure
            )
        except (urllib.error.URLError, OSError) as net_err:
            if isinstance(net_err, urllib.error.HTTPError):
                if net_err.code in (401, 403):
                    raise RuntimeError(
                        auth_denied_msg(image_ref, net_err.code)
                    ) from net_err
                if net_err.code == 404:
                    raise RuntimeError(
                        f"Image not found: '{image_ref}' does not exist "
                        f"on the registry."
                    ) from net_err
            log_error(_("No cached manifest found for '{image_ref}' ({arch}).", image_ref = image_ref, arch = arch))
            raise RuntimeError(
                f"Network error: {net_err}\n"
                f"Tip: run '{PROGRAM_NAME} mirror test' to check "
                f"mirror reachability and switch to a faster one "
                f"with '{PROGRAM_NAME} mirror use <ID>'."
            ) from net_err
        cfg_digest = manifest.get("config", {}).get("digest", "")
        image_config = _fetch_config_blob(
            repo, cfg_digest, token, base, insecure
        )
        save_manifest_cache(image_ref, arch, manifest, repo, image_config)

    layers = manifest.get("layers", [])
    if not layers:
        raise RuntimeError(
            f"Manifest for '{image_ref}' contains no filesystem layers."
        )

    n_layers = len(layers)

    # --- Phase 1: validate all layers + partition cached vs. to-download ---
    to_download: list = []
    layer_paths: dict = {}
    for i, layer in enumerate(layers):
        digest = layer["digest"]
        media_type = layer.get("mediaType", "")
        if "zstd" in media_type:
            raise RuntimeError(
                f"Layer {i + 1}/{n_layers} uses zstd compression which is "
                "not supported by Python's tarfile module. "
                "Try a different image tag that ships gzip-compressed layers."
            )

        cached_path = layer_cache_path(digest)
        if os.path.isfile(cached_path):
            short_id = digest.split(":")[-1][:12]
            log_info(f"{short_id}: Layer {i + 1}/{n_layers} already cached, "
                     f"skipping download.")
            layer_paths[digest] = cached_path
        else:
            to_download.append((i, layer))

    # --- Phase 2: download missing layers (concurrently) ---
    if to_download:
        downloaded = _download_layers_concurrent(
            to_download, repo, token or "", base, insecure,
            n_layers, image_ref,
        )
        layer_paths.update(downloaded)

    # --- Phase 3: apply layers in manifest order ---
    for i, layer in enumerate(layers):
        digest = layer["digest"]
        short_id = digest.split(":")[-1][:12]
        log_info(_("{short_id}: Applying layer {index}/{n_layers}...",
                    short_id=short_id, index=i + 1, n_layers=n_layers))
        apply_layer(layer_paths[digest], rootfs_dir)

    return {
        "manifest": manifest,
        "image_config": image_config,
    }
