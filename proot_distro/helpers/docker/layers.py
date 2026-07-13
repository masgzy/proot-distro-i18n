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

# Architecture: Blob-level operations — download a single layer to the
# local cache, and apply a cached layer to a rootfs directory. The
# applier handles OCI whiteouts (§6.1.2), defers hard-link copies until
# all regular files are written, and stamps directory mtimes last so
# they don't get clobbered by intermediate file writes.

import hashlib
import os
import urllib.request

from proot_distro.atomic import atomic_replace
from proot_distro.progress import clear_bar, draw_bytes_bar
from proot_distro.helpers.download import retry_http
from proot_distro.helpers.docker.cache import layer_cache_path
from proot_distro.helpers.docker.transport import (
    opener, _ua,
)
from proot_distro.helpers.tar_extract import extract_tar_to_rootfs


# Download chunk size: 256 KiB. Larger than the original 64 KiB to
# reduce syscall overhead (fewer read/write/progress-bar calls per MiB).
# 256 KiB is a good balance — large enough for efficiency, small enough
# that progress updates remain responsive and memory stays low.
_CHUNK_SIZE = 1 << 18


def download_blob(
    repo: str, digest: str, token: str, base: str,
    insecure: bool = False, *,
    timeout: float = 120.0,
    quiet: bool = False,
) -> str:
    """Download a blob to the layer cache; return the local file path.

    Streams the bytes through sha256 and verifies the result against the
    expected *digest* before promoting the .tmp file. The cache therefore
    only ever contains intact layers.

    *timeout* (default 120s) is passed to the opener so a hung connection
    raises URLError instead of blocking indefinitely. Layers can be large,
    so the timeout is longer than the 30s used for small API requests.

    *quiet* suppresses per-chunk progress-bar output. Set to True when
    downloading multiple layers concurrently so their bars don't clobber
    each other on the terminal.
    """
    dest = layer_cache_path(digest)
    if os.path.isfile(dest):
        return dest

    if ":" not in digest:
        raise RuntimeError(f"Malformed layer digest '{digest}'.")
    algo, expected_hex = digest.split(":", 1)
    if algo.lower() != "sha256":
        raise RuntimeError(
            f"Unsupported layer digest algorithm '{algo}' (only sha256 "
            f"is supported)."
        )

    url = f"{base}/v2/{repo}/blobs/{digest}"
    headers = {**_ua()}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)

    def _attempt():
        # Fresh hasher per attempt: a retry re-downloads the whole blob, so
        # state from a failed partial download must not carry over.
        hasher = hashlib.sha256()
        try:
            with atomic_replace(dest) as tmp:
                with opener(insecure).open(req, timeout=timeout) as resp, \
                        open(tmp, "wb") as fh:
                    total = int(resp.headers.get("Content-Length", 0))
                    downloaded = 0
                    while True:
                        chunk = resp.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        fh.write(chunk)
                        hasher.update(chunk)
                        downloaded += len(chunk)
                        if not quiet:
                            draw_bytes_bar(downloaded, total, noun="downloaded")
                actual_hex = hasher.hexdigest()
                if actual_hex != expected_hex.lower():
                    raise RuntimeError(
                        f"Layer integrity check failed for digest '{digest}': "
                        f"expected {expected_hex}, got {actual_hex}."
                    )
        finally:
            if not quiet:
                clear_bar()
        return dest

    short_id = digest.split(":")[-1][:12]
    return retry_http(_attempt, what=f"Downloading layer {short_id}")


def apply_layer(layer_path: str, rootfs_dir: str) -> None:
    """Apply one OCI/Docker layer (gzipped tar) onto rootfs_dir.

    Thin wrapper around extract_tar_to_rootfs that turns on OCI
    whiteout handling (.wh.<name> deletes sibling, .wh..wh..opq
    clears the parent dir). See that function for the full set of
    invariants enforced during extraction.
    """
    extract_tar_to_rootfs(layer_path, rootfs_dir, handle_whiteouts=True)
