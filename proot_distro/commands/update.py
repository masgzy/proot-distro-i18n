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

"""``pd update`` subcommand: update this program and the nproot runtime.

Two targets, one command:

``pd`` (this Python package)
    GitHub Releases of this fork ship a prebuilt universal wheel.
    The latest one is downloaded and handed to ``pip`` as a local
    file. Installing a wheel never builds anything from source, which
    is exactly why the command exists: ``pip install git+URL`` runs
    the PEP 517 backend on the device and can stall for a long time
    at its "Preparing metadata" stage on Termux. When a release
    carries no wheel, PyPI is consulted as a fallback before giving
    up with manual instructions.

``nproot`` (the container runtime binary)
    GitHub Releases of masgzy/nproot ship ``nproot-android-<arch>.zip``
    holding the binary plus its sibling files (``preload/`` shims,
    ``host-census``). The archive for the device CPU is downloaded,
    verified against a strict member whitelist, and installed next to
    wherever the current nproot was found (or into the user bin dir
    when none is installed). Replacing a running binary works because
    each file is unlinked before its replacement is renamed into
    place -- the old inode stays alive for the running process.

Authentication is optional: public repos need no token, and one is
read from ``PD_GITHUB_TOKEN`` / ``GITHUB_TOKEN`` / ``GH_TOKEN`` only
when the user exports it (API rate limits on shared networks are the
typical reason to). A token is never hardcoded, never logged.

``--check`` reports what is available without changing anything.
Everything runs over verified TLS (see helpers.download); redirects
are policy-checked like every other download in this program.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error

from proot_distro.arch import (
    USER_BIN_DIR, find_nproot_bin, get_device_cpu_arch,
)
from proot_distro.constants import (
    IS_TERMUX, PROGRAM_NAME, PROGRAM_VERSION, TERMUX_PREFIX,
)
from proot_distro.helpers.download import (
    NETWORK_ERRORS, download_file, http_opener, new_request,
)
from proot_distro.i18n import _
from proot_distro.message import crit_error, log_info, msg, warn

__all__ = ("command_update",)

PD_REPO = "masgzy/proot-distro-i18n"
NPROOT_REPO = "masgzy/nproot"

# The asset every release of the runtime ships, one per device arch.
_NPROOT_ASSET_ARCH = {
    "aarch64": "arm64",
    "x86_64": "x86_64",
}

# Files an nproot release archive may contain. Everything else is a
# signature of tampering or of a layout change this code was not
# reviewed against -- refuse the update rather than unpack blindly.
# "preload/<name>" entries carry the no-ptrace shims next to the
# binary, which its launcher resolves relative to /proc/self/exe.
_NPROOT_ENTRY_RE = re.compile(
    r"\A(?:nproot|host-census|preload/libnproot[^/]*\.so)\Z"
)

# The version token nproot --version prints (8-hex, possibly "-dirty").
_NPROOT_VERSION_RE = re.compile(r"\b([0-9a-f]{8})(-dirty)?\b")

_PIP_TIMEOUT = 600  # seconds; pip on a phone can be slow


# ---------------------------------------------------------------------------
# GitHub / PyPI plumbing
# ---------------------------------------------------------------------------

def _github_token() -> str:
    """The user's optional API token; presence only, never logged."""
    for var in ("PD_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return ""


def _github_api(url: str) -> dict:
    """GET a GitHub API URL as parsed JSON, with clean error messages.

    The optional token rides the Authorization header only when the
    user exported one. A rate-limited reply is translated into a hint
    naming the variable that would raise the limit, instead of GitHub's
    raw 403 body.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{PROGRAM_NAME}/{PROGRAM_VERSION}",
    }
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = new_request(url, headers=headers)
    try:
        with http_opener().open(req, timeout=30) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(2000).decode("utf-8", "replace")
        except Exception:
            pass
        if exc.code in (403, 429) and "rate limit" in detail.lower():
            raise RuntimeError(_(
                "GitHub API rate limit reached while asking '{url}'. "
                "Wait a while, or export PD_GITHUB_TOKEN (a GitHub "
                "personal access token) to raise the limit.",
                url=url,
            )) from exc
        raise RuntimeError(_(
            "GitHub API request failed: HTTP {code} for '{url}'.",
            code=exc.code, url=url,
        )) from exc
    except NETWORK_ERRORS as exc:
        raise RuntimeError(_(
            "GitHub API request failed for '{url}': {exc}",
            url=url, exc=exc,
        )) from exc
    if not isinstance(data, dict):
        raise RuntimeError(_(
            "GitHub API returned an unexpected payload for '{url}'.",
            url=url,
        ))
    return data


def _latest_release(repo: str) -> dict:
    """The /releases/latest payload for *repo*, minimally validated."""
    data = _github_api(f"https://api.github.com/repos/{repo}/releases/latest")
    tag = data.get("tag_name")
    assets = data.get("assets")
    if not isinstance(tag, str) or not isinstance(assets, list):
        raise RuntimeError(_(
            "GitHub API returned an unexpected release payload for "
            "'{repo}'.", repo=repo,
        ))
    return data


def _parse_version(text: str) -> tuple:
    """'v5.10.2' -> (5, 10, 2); 'rolling' -> () (nothing comparable)."""
    token = str(text).strip().lstrip("vV")
    parts = []
    for piece in token.split("."):
        if not piece.isdigit():
            break
        parts.append(int(piece))
    return tuple(parts)


def _version_label(text: str) -> str:
    return str(text).strip() or _("unknown")


# ---------------------------------------------------------------------------
# pd (this package)
# ---------------------------------------------------------------------------

def _pick_wheel_asset(release: dict) -> tuple:
    """(version, asset_url, asset_name) of the release's wheel, if any."""
    for asset in release.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        url = asset.get("browser_download_url")
        if (isinstance(name, str) and isinstance(url, str)
                and name.startswith("proot_distro_i18n-")
                and name.endswith("-py3-none-any.whl")):
            version = name[len("proot_distro_i18n-"):-len("-py3-none-any.whl")]
            return version, url, name
    return "", "", ""


def _wheel_url_on_pypi() -> tuple:
    """(version, asset_url, name) of the newest PyPI wheel, best effort."""
    req = new_request(
        "https://pypi.org/pypi/proot-distro-i18n/json",
        headers={"User-Agent": f"{PROGRAM_NAME}/{PROGRAM_VERSION}"},
    )
    try:
        with http_opener().open(req, timeout=30) as resp:
            data = json.load(resp)
    except NETWORK_ERRORS:
        return "", "", ""
    info = data.get("info") or {}
    version = info.get("version") or ""
    if not version:
        return "", "", ""
    for asset in (data.get("urls") or []):
        name = asset.get("filename") or ""
        url = asset.get("url") or ""
        if (asset.get("packagetype") == "bdist_wheel"
                and name.startswith("proot_distro_i18n-")
                and name.endswith("-py3-none-any.whl")):
            return version, url, name
    return "", "", ""


def _pip_install(wheel_path: str) -> None:
    """pip install a local wheel, translating common failures.

    --no-deps: the package has no dependencies (pyproject declares an
    empty list), so resolution is pure risk for zero benefit.
    --force-reinstall: a same-version or older wheel must still go
    through when the user asked for it. On distributions with an
    externally-managed environment marker the same command is retried
    with --break-system-packages, which is how the user must have
    installed this program in the first place.
    """
    cmd = [sys.executable, "-m", "pip", "install", "--no-deps",
           "--force-reinstall", wheel_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=_PIP_TIMEOUT, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(_(
            "pip did not finish within {secs} seconds; its output was "
            "discarded. Try again, or install the downloaded wheel by "
            "hand.", secs=_PIP_TIMEOUT,
        )) from exc
    if result.returncode == 0:
        return
    output = (result.stdout or "") + (result.stderr or "")
    if "externally-managed-environment" in output:
        result = subprocess.run(
            cmd + ["--break-system-packages"],
            capture_output=True, text=True,
            timeout=_PIP_TIMEOUT, check=False,
        )
        if result.returncode == 0:
            return
        output = (result.stdout or "") + (result.stderr or "")
    tail = "\n".join(output.strip().splitlines()[-8:])
    raise RuntimeError(_(
        "pip failed to install the wheel:\n{tail}", tail=tail,
    ))


def _update_pd(check_only: bool, insecure: bool, force: bool) -> bool:
    """Bring this package up to the latest release wheel.

    Returns True when a change was applied (or would be, under
    --check), False when nothing was needed.
    """
    release = _latest_release(PD_REPO)
    tag = release.get("tag_name") or "?"
    version, url, name = _pick_wheel_asset(release)

    current = _parse_version(PROGRAM_VERSION)
    latest = _parse_version(version)

    if not version:
        # No wheel in the release: PyPI fallback, then honest failure.
        version, url, name = _wheel_url_on_pypi()
        latest = _parse_version(version)
        if not version:
            warn(_("{repo} has no wheel in its latest release ({tag}) and "
                   "PyPI has none either.", repo=PD_REPO, tag=tag))
            log_info(_("Update manually: pip install --upgrade "
                       "proot-distro-i18n, or pip install "
                       "git+https://github.com/{repo}.git",
                       repo=PD_REPO))
            return False

    if current and latest and not force and current >= latest:
        log_info(_("{name} {current} is up to date (latest release: "
                   "{latest}).", name=PD_REPO,
                   current=PROGRAM_VERSION, latest=version))
        return False

    if check_only:
        msg(_("{repo}: {current} -> {latest}  ({asset})",
              repo=PD_REPO, current=_version_label(PROGRAM_VERSION),
              latest=version, asset=name))
        return True

    if not current and PROGRAM_VERSION == "rolling" and not force:
        warn(_("This copy of {name} runs from a source checkout "
               "(version '{rolling}'); pip would install a second, "
               "wheel-based copy next to it. Use --force to do that, "
               "or update the checkout with git.",
               name=PD_REPO, rolling=PROGRAM_VERSION))
        return False

    log_info(_("Downloading {asset} ...", asset=name))
    tmpdir = tempfile.mkdtemp(prefix="pd-update-")
    try:
        wheel = os.path.join(tmpdir, name)
        download_file(url, wheel, insecure=insecure)
        log_info(_("Installing with pip ..."))
        _pip_install(wheel)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    msg()
    log_info(_("{name} updated to {version}. Restart any running "
               "'{cmd}' processes to load the new code.",
               name=PD_REPO, version=version, cmd=PROGRAM_NAME))
    msg()
    return True


# ---------------------------------------------------------------------------
# nproot (the runtime binary)
# ---------------------------------------------------------------------------

def _read_nproot_version(path: str) -> str:
    """The 8-hex build token `nproot --version` prints, or ''.

    Any failure (missing binary, exec format on a foreign-arch file,
    timeout) degrades to '' -- the caller then installs regardless,
    which is the safe direction for an update command.
    """
    try:
        result = subprocess.run(
            [path, "--version"], capture_output=True, text=True,
            timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    match = _NPROOT_VERSION_RE.search(result.stdout or "")
    return match.group(1) if match else ""


def _nproot_asset_for_device(release: dict) -> tuple:
    """(tag, url, name) of this device's runtime asset, or ('', '', '')."""
    tag = release.get("tag_name") or ""
    arch = _NPROOT_ASSET_ARCH.get(get_device_cpu_arch())
    if arch is None:
        return "", "", ""
    wanted = f"nproot-android-{arch}.zip"
    for asset in release.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        url = asset.get("browser_download_url")
        if name == wanted and isinstance(url, str):
            return tag, url, name
    return tag, "", ""


def _extract_nproot_zip(zip_path: str, staging: str) -> list:
    """Unpack the whitelisted members of a release archive into staging.

    A release archive is third-party content like any other download.
    Only the three file kinds the runtime actually uses are accepted,
    each member's name is checked for traversal (absolute paths, '..',
    backslashes, empty pieces), and symlink members are refused -- a
    link inside the archive could otherwise point the install write at
    an arbitrary host file. Refusing an unexpected member aborts the
    whole update rather than unpacking the rest.
    """
    import zipfile

    installed = []
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = info.filename
            if name.endswith("/"):
                continue  # directory entry ("preload/")
            if name.startswith("/") or "\\" in name or ":" in name:
                raise RuntimeError(_(
                    "refusing archive entry with an absolute or unusual "
                    "path: {name}", name=name,
                ))
            parts = [p for p in name.split("/") if p not in ("", ".")]
            if not parts or ".." in parts or posixpath.normpath(name) != name:
                raise RuntimeError(_(
                    "refusing archive entry that escapes its directory: "
                    "{name}", name=name,
                ))
            if not _NPROOT_ENTRY_RE.match(name):
                raise RuntimeError(_(
                    "refusing archive with an unexpected member: {name}. "
                    "This looks like a layout change or tampered "
                    "archive; the update was aborted.", name=name,
                ))
            mode = info.external_attr >> 16
            if mode and stat.S_ISLNK(mode):
                raise RuntimeError(_(
                    "refusing archive with a symlink member: {name}",
                    name=name,
                ))
            target = os.path.join(staging, *parts)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with archive.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            os.chmod(target, 0o755)
            installed.append(os.path.join(*parts))
    if "nproot" not in installed:
        raise RuntimeError(_(
            "the archive did not contain the nproot binary; update "
            "aborted.",
        ))
    return installed


def _default_nproot_dir() -> str:
    """Where to install when no runtime binary exists yet."""
    if IS_TERMUX:
        return f"{TERMUX_PREFIX}/bin"
    return os.path.expanduser(USER_BIN_DIR)


def _install_nproot_file(staging: str, rel: str, target_dir: str) -> None:
    """Move one staged file into place, replacing a running binary.

    rename(2) over a file a live process is executing is legal on
    Linux: the running process keeps its inode and new execs get the
    new one, and unlike open-for-write there is no ETXTBSY to route
    around -- which is what makes updating an nproot that is holding
    sessions open safe. The replacement is written under a temporary
    name in the target directory and os.replace()d in one step, so a
    crash mid-install leaves either the old or the new file, never a
    truncated one and never a window with nothing at all.
    """
    dest = os.path.join(target_dir, rel)
    if os.path.isdir(dest) and not os.path.islink(dest):
        raise RuntimeError(_(
            "cannot install {dest}: a directory is in the way.",
            dest=dest,
        ))
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = dest + ".pd-update-tmp"
    shutil.copyfile(os.path.join(staging, rel), tmp)
    os.chmod(tmp, 0o755)
    try:
        os.replace(tmp, dest)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise RuntimeError(_(
            "cannot install {dest}: {exc}", dest=dest, exc=exc,
        )) from exc


def _update_nproot(check_only: bool, insecure: bool, force: bool) -> bool:
    """Install/refresh the nproot runtime from its latest release.

    Returns True when an update was applied (or is available, under
    --check), False when already current. Skips the whole target with
    a notice when no asset exists for this device (e.g. a desktop
    x86_64 box, where the release ships only Android builds).
    """
    release = _latest_release(NPROOT_REPO)
    tag, url, name = _nproot_asset_for_device(release)
    if not url:
        device = get_device_cpu_arch()
        if check_only:
            msg(_("{repo}: no release asset for this device ({device}).",
                  repo=NPROOT_REPO, device=device))
        else:
            warn(_("{repo} ships no asset for this device ({device}); "
                   "the runtime was left alone.",
                   repo=NPROOT_REPO, device=device))
        return False

    current_path = find_nproot_bin()
    current_hash = _read_nproot_version(current_path) if current_path else ""

    if check_only:
        state = (_("not installed") if not current_path
                 else current_hash or _("unknown build"))
        msg(_("{repo}: {state} -> {tag}  ({asset})",
              repo=NPROOT_REPO, state=state, tag=tag, asset=name))
        return True

    # Same build already installed: unless forced, that IS up to date.
    # The hash is compared from the freshly downloaded binary, so a
    # re-published tag with new content is still honored.
    log_info(_("Downloading {asset} ...", asset=name))
    tmpdir = tempfile.mkdtemp(prefix="pd-nproot-update-")
    try:
        zip_path = os.path.join(tmpdir, name)
        download_file(url, zip_path, insecure=insecure)
        staging = os.path.join(tmpdir, "staging")
        os.makedirs(staging, exist_ok=True)
        installed = _extract_nproot_zip(zip_path, staging)
        new_hash = _read_nproot_version(os.path.join(staging, "nproot"))
        if (new_hash and current_hash and new_hash == current_hash
                and not force):
            log_info(_("nproot {hash} is already installed (latest "
                       "release: {tag}).", hash=current_hash, tag=tag))
            return False

        target_dir = (os.path.dirname(os.path.abspath(current_path))
                      if current_path else _default_nproot_dir())
        if not os.path.isdir(target_dir):
            try:
                os.makedirs(target_dir, exist_ok=True)
            except OSError as exc:
                raise RuntimeError(_(
                    "install directory '{dir}' cannot be created: {exc}",
                    dir=target_dir, exc=exc,
                )) from exc
        if not os.access(target_dir, os.W_OK):
            raise RuntimeError(_(
                "install directory '{dir}' is not writable. Re-run as "
                "the user who owns it, or install the downloaded files "
                "by hand.", dir=target_dir,
            ))

        for rel in installed:
            _install_nproot_file(staging, rel, target_dir)

        installed_path = os.path.join(target_dir, "nproot")
        msg()
        if current_path:
            log_info(_("nproot updated: {old} -> {new} ({tag}).",
                       old=current_hash or _("unknown build"),
                       new=new_hash or tag, tag=tag))
        else:
            log_info(_("nproot {new} ({tag}) installed to '{path}'. "
                       "{cmd} will prefer it over proot from the next "
                       "session on.", new=new_hash or tag, tag=tag,
                       path=installed_path, cmd=PROGRAM_NAME))
        msg()
        return True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def command_update(args) -> None:
    """``pd update`` dispatcher: run the selected targets in order."""
    check_only = bool(getattr(args, "check", False))
    insecure = bool(getattr(args, "allow_insecure", False))
    force = bool(getattr(args, "force", False))
    pd_only = bool(getattr(args, "pd_only", False))
    runtime_only = bool(getattr(args, "runtime_only", False))

    msg()
    try:
        applied = False
        if not runtime_only:
            applied = _update_pd(check_only, insecure, force) or applied
        if not pd_only:
            applied = _update_nproot(check_only, insecure, force) or applied
        if check_only:
            if applied:
                log_info(_("Updates available: run '{cmd} update' to "
                           "apply.", cmd=PROGRAM_NAME))
            else:
                log_info(_("Everything up to date."))
    except KeyboardInterrupt:
        log_info(_("Aborted by user."))
        msg()
        sys.exit(1)
    except (RuntimeError, *NETWORK_ERRORS) as exc:
        # RedirectRefused and every other download failure ride in here
        # (NETWORK_ERRORS is a tuple; it unpacks flat -- a nested tuple
        # inside except raises TypeError in 3.12+); one clean line
        # instead of a traceback, whatever broke.
        crit_error(str(exc))
        msg()
        sys.exit(1)
    msg()
