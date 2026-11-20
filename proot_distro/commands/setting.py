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

"""``pd setting`` subcommand: view and change user-facing settings.

Sub-actions
===========

- ``setting put <item> <value>`` / ``setting set``
    Normalize the value per the item's kind, persist it into
    ``config.json`` and confirm. Boolean items accept true/false,
    1/0, t/f, yes/no, on/off (any case) and are stored as real JSON
    booleans.

- ``setting reset [item]``
    Drop the stored value(s) back to the built-in defaults. Only
    registry-owned keys are touched -- ``language`` (``pd lang``) is
    never affected.

- ``setting ls`` / ``setting list`` (default when no action is given)
    One line per setting: name, current value, whether it is the
    default, and a short description.

- ``setting help``
    Render the full help page of this command.

Settings apply to *new* sessions: the values they govern (PATH
composition, the profile.d snippet) are decided when a session
starts, so a change is picked up by the next ``login``/``run``.
"""

from __future__ import annotations

import sys

from proot_distro.config import CONFIG_FILE
from proot_distro.constants import PROGRAM_NAME
from proot_distro.i18n import _
from proot_distro.message import crit_error, log_info, msg
from proot_distro.settings import (
    SettingError, list_settings, reset_settings, set_setting,
)

__all__ = ("command_setting",)


def _usage_hint() -> None:
    """Print the one-line action summary after an error."""
    log_info(_("Usage: {cmd} setting put <item> <value> | "
               "{cmd} setting reset [item] | {cmd} setting ls",
               cmd=PROGRAM_NAME))


def _action_put(item, value) -> int:
    """Validate, persist, confirm."""
    if not item or not value:
        msg()
        crit_error(_("both an item and a value are required "
                     "(e.g. '{example}').",
                     example=f"{PROGRAM_NAME} setting put passthrough false"))
        _usage_hint()
        msg()
        return 1
    try:
        old = None
        from proot_distro.settings import SETTINGS, get_setting
        if item in SETTINGS:
            old = get_setting(item)
        new = set_setting(item, value)
    except SettingError as exc:
        msg()
        crit_error(str(exc))
        _usage_hint()
        msg()
        return 1
    msg()
    log_info(_("{item} set to {value} (was {old}); saved to {path}.",
               item=item, value=_render_value(new),
               old=(_("unset (default)") if old is None
                    else _render_value(old)),
               path=CONFIG_FILE))
    log_info(_("Settings apply to new sessions: the next "
               "'{cmd} login' or '{cmd} run' picks this up.",
               cmd=PROGRAM_NAME))
    msg()
    return 0


def _action_reset(item) -> int:
    """Drop one or all settings back to defaults."""
    try:
        removed = reset_settings(item)
    except SettingError as exc:
        msg()
        crit_error(str(exc))
        _usage_hint()
        msg()
        return 1
    msg()
    if item:
        if removed:
            log_info(_("{item} reset to its default.", item=item))
        else:
            log_info(_("{item} has no stored value; it already is "
                       "at its default.", item=item))
    else:
        if removed:
            log_info(_("Settings reset to defaults: {items}.",
                      items=", ".join(removed)))
        else:
            log_info(_("No stored settings to reset."))
    msg()
    return 0


def _render_value(value) -> str:
    """A JSON-ish rendering of a stored value for the list."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)

def _action_list() -> int:
    """One line per setting plus its description."""
    msg()
    msg(_("Current settings (stored in {path}):", path=CONFIG_FILE))
    for name, current, default, is_default, doc in list_settings():
        marker = "" if is_default else f"  {_('← changed from default')}"
        msg(f"  {name:<14} {_render_value(current):<6}"
            f"  ({_('default')}: {_render_value(default)}){marker}")
        msg(f"    {_(doc)}")
    msg()
    log_info(_("Change one with `{cmd} setting put <item> <value>`; "
               "`{cmd} setting reset` goes back to the defaults.",
               cmd=PROGRAM_NAME))
    msg()
    return 0


def command_setting(args) -> None:
    """``pd setting`` dispatcher."""
    action = getattr(args, "setting_action", None) or "ls"
    item = getattr(args, "item", None)
    value = getattr(args, "value", None)

    if action in ("put", "set"):
        rc = _action_put(item, value)
    elif action == "reset":
        rc = _action_reset(item)
    elif action in ("ls", "list"):
        rc = _action_list()
    elif action == "help":
        from proot_distro.commands.help import HELP_COMMANDS
        HELP_COMMANDS["setting"]()
        rc = 0
    else:  # argparse choices make this unreachable; kept for safety
        msg()
        crit_error(_("Unknown setting sub-action: '{action}'.",
                     action=action))
        rc = 1
    if rc:
        sys.exit(rc)
