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

"""内置 Docker registry 镜像源表 + 用户配置持久化。

设计要点
========

1.  ``BUILTIN_MIRRORS`` —— 内置镜像源清单，按 2025-2026 年公开可用
    性整理。每条记录包含 name/url/region/note。

2.  ``USER_MIRRORS_FILE`` —— 用户自定义镜像源 JSON 文件路径。格式同
    ``BUILTIN_MIRRORS``（一个 list[dict]，每条含 id/name/url/region/note）。
    用户可通过该文件添加自有镜像源或覆盖内置源的 URL/备注。
    与 ``MIRRORS_CONFIG_FILE``（记录当前选中的源）互不干扰。

3.  ``get_all_mirrors()`` —— 合并内置 + 用户镜像源，同 id 时用户覆盖
    内置。供 ``mirror ls``、``mirror test``、``mirror use`` 统一使用。

4.  ``load_active_mirror()`` / ``save_active_mirror()`` —— 在
    ``MIRRORS_CONFIG_FILE`` 上读写当前选中的镜像源。

5.  ``resolve_registry_url()`` —— 给 transport 层用的统一入口。

6.  ``test_mirror()`` / ``test_all_mirrors()`` —— 探测可达性。

镜像源变更历史
==============

镜像源可用性变化频繁，本表会随 web 搜索结果定期更新。
最近一次校准：2026-07-08。
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

from proot_distro.constants import IS_TERMUX, PROGRAM_NAME
from proot_distro.helpers.download import insecure_ssl_context
from proot_distro.i18n import _

# ---------------------------------------------------------------------------
# 配置文件路径
# ---------------------------------------------------------------------------

if IS_TERMUX:
    # Termux: 走 $PREFIX/etc/<prog>/
    _CONFIG_DIR = os.path.join(os.environ.get("TERMUX__PREFIX", "/data/data/com.termux/files/usr"),
                               "etc", PROGRAM_NAME)
else:
    # 桌面 Linux: 走 XDG_CONFIG_HOME 或 ~/.config/<prog>/
    _CONFIG_DIR = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config", PROGRAM_NAME
    )

# 当前选中的镜像源（单条记录）
MIRRORS_CONFIG_FILE = os.path.join(_CONFIG_DIR, "mirror.json")

# 用户自定义镜像源列表（可添加/覆盖内置源）
USER_MIRRORS_FILE = os.path.join(_CONFIG_DIR, "mirrors.json")


# ---------------------------------------------------------------------------
# 内置镜像源表
# ---------------------------------------------------------------------------

# 每条记录: id / display_name / url / region / note
# id 用作配置文件里的稳定标识符；URL 不带尾斜杠。
# 维护规则：仅收录 2025-2026 年公开报告仍可用的源；私有云(阿里云专属
# 加速器等)需用户自行填 URL，不进内置表。
BUILTIN_MIRRORS: List[Dict[str, str]] = [
    {
        "id": "daocloud",
        "name": "DaoCloud",
        "url": "https://docker.m.daocloud.io",
        "region": "CN",
        "note": "公共加速器，支持 Hub/GCR/Quay，白名单 + 限流",
    },
    {
        "id": "1panel-live",
        "name": "1Panel",
        "url": "https://docker.1panel.live",
        "region": "CN",
        "note": "社区加速器，Docker Hub 透明代理",
    },
    {
        "id": "1ms",
        "name": "毫秒镜像",
        "url": "https://docker.1ms.run",
        "region": "CN",
        "note": "国内加速，支持 Hub/GCR/Quay，部分镜像有限制",
    },
    {
        "id": "xuanyuan",
        "name": "轩辕镜像",
        "url": "https://docker.xuanyuan.me",
        "region": "CN",
        "note": "社区维护，Docker Hub 代理",
    },
    {
        "id": "xuanyuan-run",
        "name": "轩辕镜像 (run)",
        "url": "https://docker.xuanyuan.run",
        "region": "CN",
        "note": "轩辕镜像备用域名，Docker Hub 代理",
    },
    {
        "id": "tencent",
        "name": "腾讯云",
        "url": "https://mirror.ccs.tencentyun.com",
        "region": "CN",
        "note": "仅腾讯云 ECS 内网可用，延迟最低",
    },
    {
        "id": "docker-hub",
        "name": "Docker Hub 官方",
        "url": "https://registry-1.docker.io",
        "region": "US",
        "note": "官方源，无加速，国内通常不可达",
    },
    {
        "id": "dockerproxy-link",
        "name": "DockerProxy Link",
        "url": "https://dockerproxy.link",
        "region": "CN",
        "note": "Docker Hub 代理，稳定性一般，建议作为备用",
    },
    {
        "id": "dockerproxy-net",
        "name": "DockerProxy Net",
        "url": "https://dockerproxy.net",
        "region": "CN",
        "note": "Docker Hub 代理，与 Link 互为备份",
    },
    {
        "id": "registry-cyou",
        "name": "Registry Cyou",
        "url": "https://registry.cyou",
        "region": "CN",
        "note": "个人维护的公共代理，可用性待验证",
    },
    {
        "id": "jiaxin",
        "name": "Jiaxin 镜像",
        "url": "https://docker.jiaxin.site",
        "region": "CN",
        "note": "个人站点，建议测试后使用",
    },
    {
        "id": "hubfast",
        "name": "HubFast",
        "url": "https://free.hubfast.cn",
        "region": "CN",
        "note": "免费公共加速器，可能限流",
    },
    {
        "id": "unsee",
        "name": "Unsee 镜像",
        "url": "https://docker-0.unsee.tech",
        "region": "CN",
        "note": "Docker Hub 备用代理，可用性不稳定",
    },
    {
        "id": "cnxiaobai",
        "name": "Cnxiaobai",
        "url": "https://github.cnxiaobai.com",
        "region": "US",
        "note": "Docker Hub 代理",
    },
    {
        "id": "gpv4",
        "name": "gh-proxy v4",
        "url": "https://v4.gh-proxy.org/docker",
        "region": "CF",
        "note": "gh-proxy 项目，Cloudflare CDN，仅 IPv4",
    },
    {
        "id": "gpv6",
        "name": "gh-proxy v6",
        "url": "https://v6.gh-proxy.org/docker",
        "region": "CF",
        "note": "gh-proxy 项目，Cloudflare CDN，IPv4/IPv6 双栈",
    },
    {
        "id": "gpfastly",
        "name": "gh-proxy fastly",
        "url": "https://cdn.gh-proxy.org/docker",
        "region": "Fastly",
        "note": "gh-proxy 项目，Fastly CDN，海外线路",
    },
]

# 默认镜像源（首次运行、用户未配置时使用）
DEFAULT_MIRROR_ID = "daocloud"


# ---------------------------------------------------------------------------
# 内置索引（仅 BUILTIN_MIRRORS，用于默认值查找）
# ---------------------------------------------------------------------------

_BUILTIN_INDEX: Dict[str, Dict[str, str]] = {m["id"]: m for m in BUILTIN_MIRRORS}
_BUILTIN_BY_NAME: Dict[str, Dict[str, str]] = {m["name"].lower(): m for m in BUILTIN_MIRRORS}

# 向后兼容别名：旧代码可能仍 import _MIRROR_INDEX / _MIRROR_BY_NAME
_MIRROR_INDEX = _BUILTIN_INDEX
_MIRROR_BY_NAME = _BUILTIN_BY_NAME


# ---------------------------------------------------------------------------
# 用户自定义镜像源（mirrors.json）
# ---------------------------------------------------------------------------

# mtime 缓存，避免每次 get_all_mirrors() 都重读文件
_user_mirrors_cache: dict = {"mtime": None, "data": []}


def _load_user_mirrors() -> List[Dict[str, str]]:
    """读取用户自定义镜像源列表；文件不存在或损坏时返回空列表。

    使用 mtime 缓存：如果 ``USER_MIRRORS_FILE`` 自上次读取后未修改，
    直接返回缓存值。
    """
    try:
        st = os.stat(USER_MIRRORS_FILE)
    except OSError:
        if _user_mirrors_cache["mtime"] is not None:
            _user_mirrors_cache.update(mtime=None, data=[])
        return []

    if _user_mirrors_cache["mtime"] == st.st_mtime:
        return _user_mirrors_cache["data"]

    try:
        with open(USER_MIRRORS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return _user_mirrors_cache["data"]

    if not isinstance(data, list):
        return _user_mirrors_cache["data"]

    # 只接受含 id 和 url 的 dict 条目
    user_mirrors: List[Dict[str, str]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if "id" not in entry or "url" not in entry:
            continue
        # 补全可选字段
        entry.setdefault("name", entry["id"])
        entry.setdefault("region", "?")
        entry.setdefault("note", "")
        user_mirrors.append(entry)

    _user_mirrors_cache["mtime"] = st.st_mtime
    _user_mirrors_cache["data"] = user_mirrors
    return user_mirrors


def get_all_mirrors() -> List[Dict[str, str]]:
    """返回内置 + 用户自定义镜像源列表。

    用户镜像源中与内置源同 id 的条目会覆盖内置源（允许用户修改 URL
    或备注）。返回顺序：内置源原始顺序在前，用户新增的在后。
    """
    by_id: Dict[str, Dict[str, str]] = {}
    # 先放内置源，保持原始顺序
    for m in BUILTIN_MIRRORS:
        by_id[m["id"]] = m
    # 再放用户源，同 id 覆盖
    for m in _load_user_mirrors():
        by_id[m["id"]] = m
    return list(by_id.values())


def get_mirror_index() -> Dict[str, Dict[str, str]]:
    """返回 {id: mirror_dict}，包含内置 + 用户镜像源。"""
    return {m["id"]: m for m in get_all_mirrors()}


def get_mirror_by_name() -> Dict[str, Dict[str, str]]:
    """返回 {name_lower: mirror_dict}，包含内置 + 用户镜像源。"""
    return {m["name"].lower(): m for m in get_all_mirrors()}


def get_default_mirror() -> Dict[str, str]:
    """返回默认镜像源 dict（始终取内置表，不受用户覆盖影响）。"""
    return _BUILTIN_INDEX[DEFAULT_MIRROR_ID]


# ---------------------------------------------------------------------------
# 配置文件读写（带 mtime 缓存）
# ---------------------------------------------------------------------------

# mtime-based 缓存：避免 Docker pull 流程中每次 registry_base_url("") 都重读
# 配置文件。同一进程内 mirror use/unset 写完后主动清缓存，保证即时生效。
_active_cache: dict = {"mtime": None, "value": None}


def _ensure_config_dir() -> None:
    os.makedirs(_CONFIG_DIR, exist_ok=True)


def _invalidate_active_cache() -> None:
    """清掉 mtime 缓存。save/clear 后调用，确保下次读取拿到最新值。"""
    _active_cache["mtime"] = None
    _active_cache["value"] = None


def load_active_mirror() -> Optional[Dict[str, str]]:
    """读取当前激活的镜像源；未配置返回 None。

    使用 mtime 缓存：如果配置文件自上次读取后未修改，直接返回缓存值。
    这避免 Docker pull 流程中每个 registry 请求都重读 JSON。
    """
    try:
        st = os.stat(MIRRORS_CONFIG_FILE)
    except OSError:
        # 文件不存在 — 清缓存返回 None
        if _active_cache["mtime"] is not None:
            _invalidate_active_cache()
        return None

    # 缓存命中
    if _active_cache["mtime"] == st.st_mtime and _active_cache["value"] is not None:
        return _active_cache["value"]

    # 缓存未命中 — 读文件
    try:
        with open(MIRRORS_CONFIG_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        _invalidate_active_cache()
        return None
    if not isinstance(data, dict):
        _invalidate_active_cache()
        return None

    mirror_id = data.get("id")
    url = data.get("url")
    result: Optional[Dict[str, str]] = None
    # 优先按 id 在合并后的镜像源表里找（含用户自定义）
    all_index = get_mirror_index()
    if mirror_id and mirror_id in all_index:
        m = dict(all_index[mirror_id])
        if url:
            m["url"] = url
        result = m
    elif url:
        result = {
            "id": "custom",
            "name": data.get("name") or url,
            "url": url,
            "region": data.get("region", "?"),
            "note": data.get("note", _("User custom")),
        }

    _active_cache["mtime"] = st.st_mtime
    _active_cache["value"] = result
    return result


def save_active_mirror(mirror: Dict[str, str]) -> None:
    """持久化当前镜像源选择。

    使用 atomic_replace（mkstemp 生成唯一 tmp 名）避免并发写竞态。
    """
    from proot_distro.atomic import atomic_replace
    _ensure_config_dir()
    payload = {
        "id": mirror.get("id", "custom"),
        "name": mirror.get("name", mirror.get("url", "")),
        "url": mirror.get("url", ""),
        "region": mirror.get("region", "?"),
        "note": mirror.get("note", ""),
        "updated_at": int(time.time()),
    }
    with atomic_replace(MIRRORS_CONFIG_FILE) as tmp:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
    _invalidate_active_cache()


def clear_active_mirror() -> bool:
    """删除配置文件；返回是否实际删除。"""
    try:
        os.remove(MIRRORS_CONFIG_FILE)
        _invalidate_active_cache()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


# ---------------------------------------------------------------------------
# transport 层入口
# ---------------------------------------------------------------------------

def resolve_registry_url() -> str:
    """返回 transport 层应当使用的 registry base URL。

    优先级：
    1. ``PD_REGISTRY_URL`` 环境变量（最高，便于一次性覆盖）
    2. 用户配置文件里的当前选中镜像源
    3. 内置默认 (DaoCloud)

    注意：每次调用都会读 config 文件——为了避免在拉取流程中
    重复 I/O，调用方（如 transport.registry_base_url）应当缓存
    返回值。本函数自身不缓存，以使 ``mirror use`` 立即生效。
    """
    env_url = os.environ.get("PD_REGISTRY_URL")
    if env_url:
        return env_url.rstrip("/")
    active = load_active_mirror()
    if active and active.get("url"):
        return active["url"].rstrip("/")
    return get_default_mirror()["url"].rstrip("/")


# ---------------------------------------------------------------------------
# 镜像源探测
# ---------------------------------------------------------------------------

def test_mirror(url: str, timeout: float = 6.0,
                insecure: bool = False) -> Tuple[bool, float, str]:
    """Probe a registry mirror's reachability.

    Follows Docker Registry HTTP API V2: issue GET ``/v2/``. 2xx/4xx
    both count as reachable (401/403 means auth required but the
    service is up); only network errors or timeouts count as failure.

    Returns ``(ok, latency_ms, detail)``.
    """
    if not url:
        return False, 0.0, _("Empty URL")
    target = url.rstrip("/") + "/v2/"
    if not target.startswith(("http://", "https://")):
        target = "https://" + target

    ctx = None
    if insecure or target.startswith("http://"):
        ctx = insecure_ssl_context()

    req = urllib.request.Request(target, method="GET", headers={
        "User-Agent": f"{PROGRAM_NAME}/mirror-test",
        "Accept": "application/vnd.docker.distribution.manifest.v2+json",
    })
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            resp.read(64)
        latency = (time.monotonic() - t0) * 1000.0
        return True, latency, _("HTTP {code} OK", code=resp.status)
    except urllib.error.HTTPError as exc:
        latency = (time.monotonic() - t0) * 1000.0
        if 400 <= exc.code < 500:
            return True, latency, _("HTTP {code} (auth required)", code=exc.code)
        return False, latency, _("HTTP {code}", code=exc.code)
    except urllib.error.URLError as exc:
        latency = (time.monotonic() - t0) * 1000.0
        reason = exc.reason if hasattr(exc, "reason") else str(exc)
        if isinstance(reason, socket.timeout):
            return False, latency, _("Connection timeout ({timeout}s)", timeout=timeout)
        return False, latency, _("Network error: {reason}", reason=reason)
    except socket.timeout:
        latency = (time.monotonic() - t0) * 1000.0
        return False, latency, _("Connection timeout ({timeout}s)", timeout=timeout)
    except (OSError, ssl.SSLError) as exc:
        latency = (time.monotonic() - t0) * 1000.0
        return False, latency, _("Low-level error: {reason}", reason=str(exc))


def test_all_mirrors(timeout: float = 6.0,
                     workers: int = 8) -> List[Tuple[Dict[str, str], bool, float, str]]:
    """并发探测所有镜像源（内置 + 用户自定义），返回按延迟升序的结果列表。

    每个元素：``(mirror_dict, ok, latency_ms, detail)``。
    不可达的排在可达之后。
    """
    all_mirrors = get_all_mirrors()
    results: List[Tuple[Dict[str, str], bool, float, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(test_mirror, m["url"], timeout): m for m in all_mirrors
        }
        for fut in as_completed(futures):
            m = futures[fut]
            try:
                ok, lat, detail = fut.result()
            except Exception as exc:  # noqa: BLE001
                ok, lat, detail = False, 0.0, str(exc)
            results.append((m, ok, lat, detail))
    # 可达优先；可达内按延迟升序
    results.sort(key=lambda r: (not r[1], r[2]))
    return results


__all__ = (
    "BUILTIN_MIRRORS",
    "DEFAULT_MIRROR_ID",
    "MIRRORS_CONFIG_FILE",
    "USER_MIRRORS_FILE",
    "get_all_mirrors",
    "get_mirror_index",
    "get_mirror_by_name",
    "get_default_mirror",
    "load_active_mirror",
    "save_active_mirror",
    "clear_active_mirror",
    "resolve_registry_url",
    "test_mirror",
    "test_all_mirrors",
)
