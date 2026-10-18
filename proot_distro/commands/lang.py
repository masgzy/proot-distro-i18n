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

"""``proot-distro lang`` subcommand: set the UI language.

Sub-actions
===========

- ``lang get`` / ``lang show`` / ``lang status``
    Show the effective language and where it comes from
    (``--lang`` flag > ``PROOT_DISTRO_LANG`` > ``PD_LANG`` > config file
    > built-in default).

- ``lang use <locale>`` / ``lang set <locale>``
    Normalize the locale (``zh``, ``zh-CN``, ``zh_CN.UTF-8`` all become
    ``zh_CN``), persist it into ``config.json`` and switch immediately.
    Unknown locales fail with a hint pointing at ``lang list``.

- ``lang list`` / ``lang ls``
    List every language with a built-in translation, marking the active
    one.

- ``lang reset`` / ``lang unset`` / ``lang clear``
    Remove the ``language`` key from ``config.json`` and re-detect
    (env vars may still override; if so a warning is printed).

Why
===

Termux's locale support is crippled: ``LANG`` is usually ``C`` or unset
and Android never reports the user's real language, so automatic locale
detection cannot work. ``lang use zh_CN`` gives users an explicit,
persistent switch instead.

Environment
===========

``PROOT_DISTRO_LANG`` / ``PD_LANG`` -- override the config file for this
session. ``lang use``/``lang reset`` warn when either is set, because the
env var keeps winning until unset.
"""

from __future__ import annotations

import sys

from proot_distro.config import CONFIG_FILE
from proot_distro.constants import PROGRAM_NAME
from proot_distro.i18n import (
    AVAILABLE_LANGUAGES, DEFAULT_LANGUAGE, SOURCE_CONFIG, SOURCE_DEFAULT,
    SOURCE_ENV_LEGACY, SOURCE_ENV_PD, SOURCE_FLAG, _, get_language,
    get_language_source, normalize_locale, set_language,
)
from proot_distro.message import crit_error, log_info, msg
import os

__all__ = ("command_lang",)


# ---------------------------------------------------------------------------
# 来源显示
# ---------------------------------------------------------------------------

def _source_display(source: str) -> str:
    """把来源 token 渲染成用户可读的描述。"""
    if source == SOURCE_FLAG:
        return _("command-line flag --lang")
    if source == SOURCE_ENV_PD:
        return _("environment variable PROOT_DISTRO_LANG")
    if source == SOURCE_ENV_LEGACY:
        return _("environment variable PD_LANG")
    if source == SOURCE_CONFIG:
        return _("config file ({path})", path=CONFIG_FILE)
    return _("built-in default (this fork defaults to Chinese; "
             "Termux cannot detect the system locale)")


def _warn_if_env_overrides() -> None:
    """当环境变量会压过配置文件时给出提醒。

    ``lang use`` 写的是配置文件，但 ``PROOT_DISTRO_LANG`` / ``PD_LANG``
    优先级更高——不提醒的话用户会以为命令没生效。
    """
    env_vars = [v for v in (SOURCE_ENV_PD, SOURCE_ENV_LEGACY)
                if os.environ.get(v)]
    if not env_vars:
        return
    names = " / ".join(env_vars)
    msg()
    log_info(_("{env} is set and overrides the config file. "
               "Unset it to make the persisted choice take effect.",
               env=names))


# ---------------------------------------------------------------------------
# 子动作
# ---------------------------------------------------------------------------

def _action_get() -> int:
    """显示当前生效语言及来源。"""
    lang = get_language()
    source = get_language_source()
    info = AVAILABLE_LANGUAGES.get(lang, {})
    display = info.get("name", lang)
    msg()
    msg(_("Current language: {lang} ({code})", lang=display, code=lang))
    msg(_("Source: {source}", source=_source_display(source)))
    msg(_("Persisted preference: {value}",
         value=_("none (falls back to default)") if source != SOURCE_CONFIG
         else lang))
    msg()
    log_info(_("Change it with `{cmd} lang use <locale>`, list choices "
               "with `{cmd} lang list`.",
               cmd=PROGRAM_NAME, cmd2=PROGRAM_NAME))
    msg()
    return 0


def _action_use(token: str) -> int:
    """规范化并持久化语言偏好。"""
    lang = normalize_locale(token)
    if lang is None:
        msg()
        crit_error(_("Unknown locale: '{token}'. Supported: {list}.",
                     token=token,
                     list=", ".join(sorted(AVAILABLE_LANGUAGES))))
        msg()
        log_info(_("Use `{cmd} lang list` to see all built-in languages.",
                   cmd=PROGRAM_NAME))
        msg()
        return 1

    from proot_distro.config import save_user_config
    save_user_config({"language": lang})

    # 当前进程立即生效（含本条确认消息的翻译）
    set_language(lang, SOURCE_CONFIG)
    info = AVAILABLE_LANGUAGES.get(lang, {})
    msg()
    log_info(_("Language set to {name} ({code}) and saved to {path}.",
              name=info.get("name", lang), code=lang, path=CONFIG_FILE))
    _warn_if_env_overrides()
    msg()
    return 0


def _action_list() -> int:
    """列出所有已内置翻译的语言。"""
    current = get_language()
    msg()
    msg(_("Available languages:"))
    for code in sorted(AVAILABLE_LANGUAGES):
        info = AVAILABLE_LANGUAGES[code]
        mark = (f"  {_('← current')}" if code == current else "")
        note = info.get("note", "")
        msg(f"  {code:<8} {info.get('name', code)}"
            + (f"  ({note})" if note else "")
            + mark)
    msg()
    log_info(_("Default: {code}. Use `{cmd} lang use <locale>` to switch, "
               "`{cmd} lang reset` to clear.",
              code=DEFAULT_LANGUAGE, cmd=PROGRAM_NAME, cmd2=PROGRAM_NAME))
    msg()
    return 0


def _action_reset() -> int:
    """清除持久化偏好，回退默认逻辑。"""
    from proot_distro.config import clear_user_key
    removed = clear_user_key("language")
    # 重新探测：env 仍在则 env 生效，否则默认
    set_language()
    msg()
    if removed:
        log_info(_("Language preference cleared. Now using: {code} "
                   "({source}).",
                   code=get_language(),
                   source=_source_display(get_language_source())))
    else:
        log_info(_("No language preference saved, nothing to clear. "
                   "Now using: {code} ({source}).",
                   code=get_language(),
                   source=_source_display(get_language_source())))
    _warn_if_env_overrides()
    msg()
    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def command_lang(args) -> None:
    """``proot-distro lang`` dispatcher."""
    action = getattr(args, "lang_action", None) or "get"
    token = getattr(args, "locale", None)

    rc: int
    if action in ("get", "show", "status"):
        rc = _action_get()
    elif action in ("use", "set", "switch"):
        if not token:
            msg()
            crit_error(_("No locale specified. Usage: `{cmd} lang use <locale>`",
                         cmd=PROGRAM_NAME))
            msg()
            log_info(_("Example: {cmd} lang use zh_CN",
                       cmd=PROGRAM_NAME))
            msg()
            rc = 1
        else:
            rc = _action_use(token)
    elif action in ("list", "ls"):
        rc = _action_list()
    elif action in ("reset", "unset", "clear"):
        rc = _action_reset()
    else:
        msg()
        crit_error(_("Unknown lang sub-action: '{action}'. "
                     "Available: get, use, list, reset", action=action))
        rc = 1
    if rc:
        sys.exit(rc)
