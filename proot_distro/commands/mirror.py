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

"""``proot-distro mirror`` subcommand: manage Docker registry mirrors.

Sub-actions
===========

- ``mirror ls`` / ``mirror list``
    List all mirrors (built-in + user-defined) + the currently active one.

- ``mirror test [ID|URL] [--use-best]``
    Probe reachability. Without args, probe all mirrors concurrently.
    With an ID, test one. With a URL, test arbitrary URL.
    When testing all and at least one is reachable, prompt the user
    (Y/n) to switch to the fastest reachable mirror. ``--use-best``
    skips the prompt and switches directly (script-friendly).
    ``--no-use`` disables the prompt entirely.

- ``mirror use ID`` / ``mirror set ID``
    Pick a mirror by id or display name.

- ``mirror set URL`` (URL form)
    When the token looks like ``https://...`` it is stored as a custom URL.

- ``mirror unset`` / ``mirror clear``
    Clear the current selection, fall back to the default.

- ``mirror show``
    Print only the active mirror's URL (script-friendly).

Environment
===========

``PD_REGISTRY_URL`` —— when set, overrides every other source. ``mirror ls``
annotates this state, and ``mirror use``/``unset`` warn that the env var
still wins until unset.

User-defined mirrors
====================

Users can add custom mirrors by creating a JSON file at
``~/.config/proot-distro/mirrors.json`` (Termux: ``$PREFIX/etc/proot-distro/mirrors.json``).
The format is a list of objects with the same fields as BUILTIN_MIRRORS:

.. code-block:: json

    [
      {
        "id": "my-mirror",
        "name": "My Mirror",
        "url": "https://my-mirror.example.com",
        "region": "CN",
        "note": "公司内部加速器"
      }
    ]

An ``id`` that matches a built-in mirror overrides that mirror's URL/note.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional

from proot_distro.constants import PROGRAM_NAME
from proot_distro.i18n import _
from proot_distro.message import C, crit_error, log_info, msg, warn
from proot_distro.mirrors import (
    BUILTIN_MIRRORS,
    DEFAULT_MIRROR_ID,
    MIRRORS_CONFIG_FILE,
    USER_MIRRORS_FILE,
    clear_active_mirror,
    get_all_mirrors,
    get_default_mirror,
    get_mirror_by_name,
    get_mirror_index,
    load_active_mirror,
    save_active_mirror,
    test_all_mirrors,
    test_mirror,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Region → ANSI color mapping for the listing badge.
_REGION_COLORS = {
    "CN":     C["GREEN"],
    "US":     C["YELLOW"],
    "CF":     C["BCYAN"],
    "Fastly": C["BMAGENTA"],
}


def _region_badge(region: str) -> str:
    """Return a colored ``[REGION]`` badge."""
    color = _REGION_COLORS.get(region, C["YELLOW"])
    return f"{color}[{region}]{C['RST']}"


def _find_mirror_by_id_or_name(token: str) -> Optional[dict]:
    """Match by id (exact) -> name (exact, case-insensitive) -> id (substring).

    Searches built-in + user-defined mirrors.
    """
    if not token:
        return None
    t = token.strip().lower()
    index = get_mirror_index()
    by_name = get_mirror_by_name()
    # 精确 id
    if t in index:
        return index[t]
    # 精确 name（不区分大小写）
    if t in by_name:
        return by_name[t]
    # 子串匹配 id（fallback）
    for m in get_all_mirrors():
        if t in m["id"].lower():
            return m
    return None


def _is_url(token: str) -> bool:
    """Return True if *token* looks like a URL we should store as custom mirror.

    Strict check: must start with http:// or https://. The old heuristic
    (``"." in token and "/" not in token``) misclassified values like
    ``"1.0"`` or ``"v1.2"`` as URLs.
    """
    return token.startswith(("http://", "https://"))


# ---------------------------------------------------------------------------
# Sub-actions
# ---------------------------------------------------------------------------

def _env_registry_url() -> Optional[str]:
    """Return PD_REGISTRY_URL if set (stripped of trailing slash); else None."""
    val = os.environ.get("PD_REGISTRY_URL")
    if not val:
        return None
    return val.rstrip("/")


def _action_list(
    quiet: bool = False,
    reachable_only: bool = False,
    timeout: float = 6.0,
    insecure: bool = False,
) -> int:
    """List all mirrors (built-in + user-defined).

    When *reachable_only* is True, probes each mirror first and only
    shows those that answered. Slow but useful for picking a working
    mirror in a flaky network.
    """
    active = load_active_mirror()
    active_url = active["url"] if active else None
    env_url = _env_registry_url()
    all_mirrors = get_all_mirrors()
    builtin_ids = {m["id"] for m in BUILTIN_MIRRORS}
    user_mirrors = [m for m in all_mirrors if m["id"] not in builtin_ids]

    # 可选: 先探测, 过滤掉不可达的
    mirrors_to_show = all_mirrors
    if reachable_only:
        log_info(_("Probing mirrors to filter unreachable ones (timeout {timeout}s)...",
                   timeout=timeout))
        results = test_all_mirrors(timeout=timeout)
        reachable_ids = {m["id"] for m, ok, _, _ in results if ok}
        mirrors_to_show = [m for m in all_mirrors if m["id"] in reachable_ids]
        msg()

    if quiet:
        for m in mirrors_to_show:
            print(m["url"])
        return 0

    # ---- 顶部状态区 ----
    msg()
    msg(f"  {C['UBCYAN']}{'─' * 52}{C['RST']}")
    msg(f"  {C['UBCYAN']}{_('Docker 镜像源管理')}{C['RST']}")
    msg(f"  {C['UBCYAN']}{'─' * 52}{C['RST']}")
    msg()

    # 生效源（env > config > default）
    if env_url:
        msg(f"  {C['CYAN']}{_('当前生效')}{C['RST']}  "
            f"{C['BYELLOW']}⚠ {_('环境变量覆盖')}{C['RST']}")
        msg(f"  {C['BGREEN']}{env_url}{C['RST']}")
        msg(f"  {C['YELLOW']}{_('PD_REGISTRY_URL 已设置，`mirror use`/`unset` 不会生效')}{C['RST']}")
    elif active:
        tag = f"{C['BGREEN']}★{C['RST']}" if active.get("id") != "custom" else f"{C['BYELLOW']}◇{C['RST']}"
        msg(f"  {C['CYAN']}{_('当前生效')}{C['RST']}  "
            f"{tag} {C['BGREEN']}{active.get('name', '?')} "
            f"{C['CYAN']}({active.get('id', '?')}){C['RST']}")
        msg(f"          {C['BBLUE']}{active_url}{C['RST']}")
    else:
        default = get_default_mirror()
        msg(f"  {C['CYAN']}{_('当前生效')}{C['RST']}  "
            f"{C['BYELLOW']}○ {_('未设置（默认: {default}）', default=default['name'])}{C['RST']}")

    # 过滤提示
    if reachable_only:
        msg(f"  {C['CYAN']}{_('过滤')}{C['RST']}    "
            f"{C['BYELLOW']}{_('仅显示可达 ({n}/{total})', n=len(mirrors_to_show), total=len(all_mirrors))}{C['RST']}")

    msg()
    msg(f"  {C['UBCYAN']}{'─' * 52}{C['RST']}")
    msg()

    if not mirrors_to_show:
        msg(f"  {C['YELLOW']}{_('未找到可达的镜像源。')}{C['RST']}")
        msg()
        return 1

    # ---- 内置镜像源列表 ----
    builtin_to_show = [m for m in mirrors_to_show if m["id"] in builtin_ids]
    if builtin_to_show:
        msg(f"  {C['BCYAN']}{_('内置镜像源')} ({len(builtin_to_show)}){C['RST']}")
        msg()
        for m in builtin_to_show:
            _print_mirror_entry(m, active_url, env_url)
        msg()

    # ---- 用户自定义镜像源列表 ----
    user_to_show = [m for m in mirrors_to_show if m["id"] not in builtin_ids]
    if user_to_show:
        msg(f"  {C['BMAGENTA']}{_('用户自定义镜像源')} ({len(user_to_show)}){C['RST']}")
        msg(f"  {C['MAGENTA']}{USER_MIRRORS_FILE}{C['RST']}")
        msg()
        for m in user_to_show:
            _print_mirror_entry(m, active_url, env_url)
        msg()

    # ---- 底部操作提示 ----
    msg(f"  {C['UBCYAN']}{'─' * 52}{C['RST']}")
    msg(f"  {C['BCYAN']}{_('快速操作')}{C['RST']}")
    msg(f"    {C['GREEN']}{PROGRAM_NAME} mirror test{C['RST']}            {_('测试所有镜像源')}")
    msg(f"    {C['GREEN']}{PROGRAM_NAME} mirror test --use-best{C['RST']}    {_('自动切换到最快源')}")
    msg(f"    {C['GREEN']}{PROGRAM_NAME} mirror use <ID>{C['RST']}        {_('切换到指定镜像源')}")
    msg(f"    {C['GREEN']}{PROGRAM_NAME} mirror show{C['RST']}            {_('查看当前生效 URL')}")
    msg()

    # ---- 用户镜像源文件提示 ----
    if not user_mirrors:
        msg(f"  {C['IWHITE']}{_('提示: 可在 {file} 添加自定义镜像源', file=USER_MIRRORS_FILE)}{C['RST']}")
        msg()

    return 0


def _print_mirror_entry(m: dict, active_url: Optional[str],
                        env_url: Optional[str]) -> None:
    """Print one mirror entry with colored output."""
    if env_url:
        is_active = (m["url"] == env_url)
    else:
        is_active = (active_url == m["url"])
    mark = f"{C['BGREEN']}★{C['RST']}" if is_active else f"{C['CYAN']}·{C['RST']}"
    region = m.get("region", "?")
    badge = _region_badge(region)
    # 第一行: 标记 + ID + 名称 + 区域标签
    msg(f"  {mark} {C['UGREEN']}{m['id']:<16}{C['RST']} "
        f"{C['BCYAN']}{m['name']:<18}{C['RST']} {badge}")
    # 第二行: URL
    msg(f"      {C['BBLUE']}{m['url']}{C['RST']}")
    # 第三行: 备注
    note = m.get("note", "")
    if note:
        msg(f"      {C['IWHITE']}{note}{C['RST']}")
    msg()


def _action_test(
    target: Optional[str],
    timeout: float,
    insecure: bool,
    *,
    use_best: bool = False,
    no_use: bool = False,
    json_output: bool = False,
) -> int:
    """Probe mirror reachability.

    When testing all mirrors and at least one is reachable, the user is
    prompted (Y/n) to switch to the fastest reachable one — unless
    ``use_best`` is True (auto-switch, no prompt) or ``no_use`` is True
    (no switch, no prompt). ``json_output`` implies ``no_use`` and
    emits JSON to stdout instead of the colored table.
    """
    all_mirrors = get_all_mirrors()
    mirror_index = get_mirror_index()

    if not target:
        if not json_output:
            log_info(_("Probing {n} mirrors concurrently (timeout {timeout}s)...",
                       n=len(all_mirrors), timeout=timeout))
        results = test_all_mirrors(timeout=timeout)

        if json_output:
            # JSON 输出到 stdout，便于脚本解析
            payload = {
                "mirrors": [
                    {
                        "id": m["id"],
                        "name": m["name"],
                        "url": m["url"],
                        "region": m["region"],
                        "reachable": ok,
                        "latency_ms": round(lat, 1) if ok else None,
                        "detail": detail,
                    }
                    for m, ok, lat, detail in results
                ],
                "reachable_count": sum(1 for r in results if r[1]),
                "total": len(results),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0 if payload["reachable_count"] > 0 else 1

        msg()
        msg(f"  {C['UBCYAN']}{'─' * 52}{C['RST']}")
        msg(f"  {C['UBCYAN']}{_('探测结果（按延迟升序）')}{C['RST']}")
        msg(f"  {C['UBCYAN']}{'─' * 52}{C['RST']}")
        msg()
        headers = (_("状态"), _("ID"), _("名称"), _("延迟"), _("详情"))
        rows = []
        for m, ok, lat, detail in results:
            if ok:
                status = f"{C['BGREEN']}✓ OK{C['RST']}"
                lat_str = f"{C['GREEN']}{lat:.0f}ms{C['RST']}"
            else:
                status = f"{C['BRED']}✗ FAIL{C['RST']}"
                lat_str = f"{C['YELLOW']}-{C['RST']}"
            rows.append((
                status,
                f"{C['UGREEN']}{m['id']}{C['RST']}",
                f"{C['CYAN']}{m['name']}{C['RST']}",
                lat_str,
                f"{C['IWHITE']}{detail}{C['RST']}" if ok else f"{C['YELLOW']}{detail}{C['RST']}",
            ))
        _print_table(headers, rows)
        ok_count = sum(1 for r in results if r[1])
        msg()
        color = C["BGREEN"] if ok_count > 0 else C["BRED"]
        msg(f"  {C['CYAN']}{_('可用: {ok}/{total}', ok=ok_count, total=len(results))}{C['RST']}"
            f"  {color}{'●' * ok_count}{'○' * (len(results) - ok_count)}{C['RST']}")
        if ok_count == 0:
            msg()
            warn(_("所有镜像源均不可达，请检查网络。"))
            return 1

        # Pick the fastest reachable mirror (results are already sorted
        # by latency, reachable first).
        best_mirror = next(r[0] for r in results if r[1])
        msg()
        return _maybe_switch_to_best(best_mirror, use_best=use_best, no_use=no_use)

    # Single target
    if target in mirror_index:
        m = mirror_index[target]
        url = m["url"]
        if not json_output:
            log_info(_("Probing {name} ({url})...", name=m["name"], url=url))
    elif _is_url(target) or "/" in target:
        url = target
        if not json_output:
            log_info(_("Probing custom URL: {url}...", url=url))
        m = None
    else:
        m = _find_mirror_by_id_or_name(target)
        if m is None:
            msg()
            crit_error(_("Unknown mirror ID or URL: '{target}'. "
                         "Use `{cmd}` to list available mirrors.",
                         target=target, cmd=f"{PROGRAM_NAME} mirror ls"))
            return 1
        url = m["url"]
        if not json_output:
            log_info(_("Probing {name} ({url})...", name=m["name"], url=url))

    ok, lat, detail = test_mirror(url, timeout=timeout, insecure=insecure)

    if json_output:
        payload = {
            "target": target,
            "url": url,
            "reachable": ok,
            "latency_ms": round(lat, 1) if ok else None,
            "detail": detail,
        }
        if m is not None:
            payload["id"] = m.get("id")
            payload["name"] = m.get("name")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    msg()
    if not ok:
        msg(f"  {C['BRED']}✗ {_('FAIL')}{C['RST']}  "
            f"{C['YELLOW']}{detail}{C['RST']}")
        return 1

    msg(f"  {C['BGREEN']}✓ {_('OK')}{C['RST']}    "
        f"{C['CYAN']}{detail}{C['RST']}  "
        f"{C['GREEN']}{lat:.0f}ms{C['RST']}")

    # For single-target probes, only offer to switch if the probed mirror
    # is *not* already the active one, and the URL is reachable.
    active = load_active_mirror()
    env_url = _env_registry_url()
    already_active = (
        (env_url is None) and
        active is not None and
        active["url"].rstrip("/") == url.rstrip("/")
    )
    if already_active:
        return 0
    if m is None:
        # Custom URL probe — synthesize a mirror dict so we can save it
        m = {
            "id": "custom",
            "name": url,
            "url": url.rstrip("/"),
            "region": "?",
            "note": _("User custom"),
        }
    msg()
    return _maybe_switch_to_best(m, use_best=use_best, no_use=no_use)


def _maybe_switch_to_best(
    best_mirror: dict,
    *,
    use_best: bool,
    no_use: bool,
) -> int:
    """Offer to switch to *best_mirror*; respect use_best/no_use flags.

    Returns 0 on switch-or-skip-success, 1 on user-decline or env-block.
    """
    # PD_REGISTRY_URL env var wins over any config write — warn and exit
    # without writing, so the user understands why their pick didn't
    # take effect.
    env_url = _env_registry_url()
    if env_url is not None:
        warn(_("PD_REGISTRY_URL is set to {url}; `mirror use` would be ignored. "
               "Run `unset PD_REGISTRY_URL` first to take effect.",
               url=env_url))
        return 1

    name = best_mirror.get("name", "?")
    url = best_mirror.get("url", "?")
    mid = best_mirror.get("id", "?")

    if no_use:
        # Silent skip
        return 0

    if use_best:
        # Auto-switch without prompt (script-friendly)
        save_active_mirror(best_mirror)
        log_info(_("Switched to fastest mirror: {name} ({url})", name=name, url=url))
        msg()
        return 0

    # Interactive prompt — default Yes (Y/n)
    if not sys.stdin.isatty():
        # Non-interactive context (pipe/script): don't block, just skip
        # but inform the user.
        log_info(_("Fastest mirror: {name} ({url}). Use `--use-best` to switch automatically.",
                   name=name, url=url))
        msg()
        return 0

    prompt = _("Switch to {name} ({mid}, {lat_hint})? [Y/n] ",
               name=name, mid=mid, lat_hint=_("latency OK"))
    sys.stderr.write(f"{C['CYAN']}{prompt}{C['RST']}")
    sys.stderr.flush()
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    # Empty / y / yes → yes; n / no → no
    if answer in ("n", "no"):
        msg()
        log_info(_("Skipped. Mirror config unchanged."))
        msg()
        return 0
    # Save
    save_active_mirror(best_mirror)
    msg()
    log_info(_("Mirror switched: {name} ({url})", name=name, url=url))
    msg()
    return 0


def _action_use(token: str) -> int:
    """Pick a mirror."""
    if not token:
        msg()
        crit_error(_("No mirror specified. Usage: `{cmd} mirror use <ID|URL>`",
                     cmd=PROGRAM_NAME))
        return 1

    if _is_url(token) or "/" in token:
        url = token.rstrip("/")
        m = {
            "id": "custom",
            "name": url,
            "url": url,
            "region": "?",
            "note": _("User custom"),
        }
        save_active_mirror(m)
        msg()
        log_info(_("Custom mirror set: {url}", url=url))
        _warn_if_env_overrides()
        msg()
        return 0

    m = _find_mirror_by_id_or_name(token)
    if m is None:
        msg()
        crit_error(_("Unknown mirror ID: '{token}'. "
                     "Use `{cmd}` to list available mirrors.",
                     token=token, cmd=f"{PROGRAM_NAME} mirror ls"))
        return 1
    save_active_mirror(m)
    msg()
    log_info(_("Mirror switched: {name} ({url})", name=m["name"], url=m["url"]))
    _warn_if_env_overrides()
    msg()
    return 0


def _action_unset() -> int:
    """Clear the current selection."""
    if clear_active_mirror():
        msg()
        default = get_default_mirror()
        log_info(_("Mirror config cleared, falling back to default ({default}).",
                    default=default["name"]))
        _warn_if_env_overrides()
        msg()
    else:
        msg()
        log_info(_("No mirror configured, nothing to clear."))
        _warn_if_env_overrides()
        msg()
    return 0


def _action_show() -> int:
    """Print only the active URL (script-friendly).

    Honors PD_REGISTRY_URL > config > default — same priority as
    resolve_registry_url(). Useful for shell:
        REGISTRY=$(proot-distro mirror show)
    """
    env_url = _env_registry_url()
    if env_url:
        print(env_url)
        return 0
    active = load_active_mirror()
    url = active["url"] if active else get_default_mirror()["url"]
    print(url)
    return 0


def _warn_if_env_overrides() -> None:
    """Emit a warning when PD_REGISTRY_URL is set, so the user understands
    why their `mirror use`/`unset` did not take effect."""
    env_url = _env_registry_url()
    if env_url is None:
        return
    warn(_("PD_REGISTRY_URL={url} is set and overrides the config file. "
           "Run `unset PD_REGISTRY_URL` (or `export PD_REGISTRY_URL=`) "
           "to let `mirror use` take effect.",
           url=env_url))


# ---------------------------------------------------------------------------
# Table printer (ANSI-aware)
# ---------------------------------------------------------------------------

def _print_table(headers, rows) -> None:
    """Print a column-aligned table with ANSI color support.

    Calculates visible widths (excluding escape sequences) so colored
    cells still align correctly.
    """
    def _vis_len(s: str) -> int:
        n = 0
        in_esc = False
        for ch in s:
            if ch == "\033":
                in_esc = True
            elif in_esc and ch == "m":
                in_esc = False
            elif not in_esc:
                n += 1
        return n

    def _pad(s: str, width: int) -> str:
        return s + " " * max(0, width - _vis_len(s))

    col_count = len(headers)
    widths = [len(h) for h in headers]
    for row in rows:
        vis = [_vis_len(c) for c in row]
        for i in range(col_count):
            widths[i] = max(widths[i], vis[i])

    head_cells = [_pad(h, widths[i]) for i, h in enumerate(headers)]
    msg(f"  {C['UBCYAN']}{'  '.join(head_cells)}{C['RST']}")
    msg(f"  {C['CYAN']}{'  '.join('-' * w for w in widths)}{C['RST']}")
    for row in rows:
        cells = [_pad(c, widths[i]) for i, c in enumerate(row)]
        msg(f"  {'  '.join(cells)}")


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def command_mirror(args) -> None:
    """``proot-distro mirror`` dispatcher."""
    action = getattr(args, "mirror_action", None) or "ls"
    quiet = bool(getattr(args, "quiet", False))
    insecure = bool(getattr(args, "allow_insecure", False))
    timeout = float(getattr(args, "timeout", 6.0))
    target = getattr(args, "target", None)
    use_best = bool(getattr(args, "use_best", False))
    no_use = bool(getattr(args, "no_use", False))
    json_output = bool(getattr(args, "json_output", False))
    reachable_only = bool(getattr(args, "reachable_only", False))

    rc: int
    if action in ("ls", "list"):
        rc = _action_list(quiet=quiet, reachable_only=reachable_only,
                          timeout=timeout, insecure=insecure)
    elif action == "test":
        # --json 隐含 --no-use（脚本友好，不交互）
        if json_output:
            no_use = True
        rc = _action_test(
            target, timeout=timeout, insecure=insecure,
            use_best=use_best, no_use=no_use, json_output=json_output,
        )
    elif action in ("use", "set"):
        rc = _action_use(target or "")
    elif action in ("unset", "clear", "off"):
        rc = _action_unset()
    elif action == "show":
        rc = _action_show()
    else:
        msg()
        crit_error(_("Unknown mirror sub-action: '{action}'. "
                     "Available: ls, test, use, unset, show",
                     action=action))
        rc = 1
    if rc:
        sys.exit(rc)


__all__ = ("command_mirror",)
