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

# Architecture: All global constants and path variables for proot-distro.
# On Termux/Android, paths are rooted under TERMUX__PREFIX (env var). On a
# regular Linux host, XDG base directories (~/.local/share, ~/.cache) are
# used instead. IS_TERMUX is computed once at import time and drives both
# path selection and runtime behaviour (e.g. isolated-mode default).

import os
import platform
from importlib.metadata import version, PackageNotFoundError

PROGRAM_AUTHOR = "Termux (@sylirre)"
PROGRAM_NAME = "proot-distro"
CANONICAL_PROGRAM_NAME = "PRoot-Distro"

try:
    PROGRAM_VERSION = version(PROGRAM_NAME)
except PackageNotFoundError:
    PROGRAM_VERSION = "rolling"

os.umask(0o022)


# ---------------------------------------------------------------------------
# Termux / Android detection
# ---------------------------------------------------------------------------

TERMUX_APP_PACKAGE = os.environ.get("TERMUX_APP__PACKAGE_NAME", "com.termux")
TERMUX_HOME = os.environ.get(
    "TERMUX__HOME", f"/data/data/{TERMUX_APP_PACKAGE}/files/home"
)
TERMUX_PREFIX = os.environ.get(
    "TERMUX__PREFIX", f"/data/data/{TERMUX_APP_PACKAGE}/files/usr"
)

def _detect_termux() -> bool:
    """Return True when at least two Termux/Android indicators are present."""
    checks = (
        ("android" in platform.platform().lower()
            or os.path.exists("/system/build.prop")
            or os.path.exists("/data/app")),
        bool(os.environ.get("TERMUX_APP__APP_VERSION_NAME")
            or os.environ.get("TERMUX_VERSION")),
        os.access(TERMUX_PREFIX, os.R_OK | os.X_OK),
    )
    return sum(checks) >= 2


IS_TERMUX: bool = _detect_termux()


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

if IS_TERMUX:
    RUNTIME_DIR = os.path.join(TERMUX_PREFIX, "var", "lib", PROGRAM_NAME)
    BASE_CACHE_DIR = os.path.join(RUNTIME_DIR, "cache")
else:
    _xdg_data = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    _xdg_cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    RUNTIME_DIR = os.path.join(_xdg_data, PROGRAM_NAME)
    BASE_CACHE_DIR = os.path.join(_xdg_cache, PROGRAM_NAME)

# New container storage layout: containers/<name>/manifest.json + rootfs/
CONTAINERS_DIR = os.path.join(RUNTIME_DIR, "containers")

# Active proot session registry (one JSON file per live session).
SESSIONS_DIR = os.path.join(RUNTIME_DIR, "sessions")

# Legacy rootfs path — used only for migrating old installations.
LEGACY_ROOTFS_DIR = os.path.join(RUNTIME_DIR, "installed-rootfs")

# Layer and manifest caches (subdirectories of the base cache).
LAYER_CACHE_DIR = os.path.join(BASE_CACHE_DIR, "oci_layers")
MANIFEST_CACHE_DIR = os.path.join(BASE_CACHE_DIR, "oci_manifests")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# 静态兜底 DNS。上游 proot-distro 写的是 8.8.8.8/8.8.4.4，其 UDP 53
# 在国内网络常被黑洞，是容器里 apt/apk 解析失败的根因（见
# helpers/resolv.py）。本 fork 面向中文用户，静态兜底换成在 53 端口
# 公网直发的阿里公共 DNS 与腾讯 DNSPod；宿主 resolv.conf 能读到时优
# 先跟宿主，这两个只在所有探测都落空时兜底。
DEFAULT_PRIMARY_NS = "223.5.5.5"    # AliDNS 阿里公共 DNS
DEFAULT_SECONDARY_NS = "119.29.29.29"  # DNSPod 腾讯公共 DNS

if IS_TERMUX:
    DEFAULT_PATH_ENV = (
        "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        ":/usr/local/games:/usr/games"
        f":{TERMUX_PREFIX}/bin:/system/bin:/system/xbin"
    )
else:
    DEFAULT_PATH_ENV = (
        "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        ":/usr/local/games:/usr/games"
    )

DEFAULT_FAKE_KERNEL_RELEASE = f"6.17.0-{CANONICAL_PROGRAM_NAME}"
DEFAULT_FAKE_KERNEL_VERSION = (
    "#1 SMP PREEMPT_DYNAMIC Fri, 10 Oct 2025 00:00:00 +0000"
)
