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

"""User-facing settings registry behind the ``pd setting`` subcommand.

One place declares every setting the user can put, reset, and list;
one place answers "what is it set to right now" for the code paths
that honor it (currently ``login``). Values live in the same
``config.json`` the ``lang`` command uses, so all reads and writes
ride the merge semantics and atomic replace of
``proot_distro.config`` -- a malformed file reads as "no settings"
and every default applies.

The registry is deliberately tiny: a mapping of setting name to a
spec. Adding a setting is adding one entry here plus the code that
honors it; ``pd setting put/reset/ls`` need no changes. Specs carry
the pieces the CLI renders (a doc line per item) and the pieces the
storage cares about (the value kind). Kinds other than ``bool`` can
join by extending ``normalize_value``; nothing else in the file
assumes booleans.

``language`` is also stored in config.json but is NOT a setting: it
belongs to ``pd lang`` and is never touched here. ``reset`` only
removes keys this registry owns, so ``pd setting reset`` cannot
silently change the UI language.

The flagship setting is ``passthrough`` (default on):

* on  -- a normal-type container keeps the host Termux ``bin`` dir
  appended last on the guest PATH (container programs first, Termux
  binaries as the fallback), and the profile.d snippet re-appends it
  for every login shell.
* off -- that PATH entry is stripped instead, and the snippet drops
  the append block, so Termux binaries are no longer reachable from
  the container. The Termux prefix itself stays bound into the guest
  (files are still readable at absolute paths); only PATH resolution
  changes. Sessions must be restarted for a change to apply -- PATH
  and the snippet are both decided when a session starts.
"""

from __future__ import annotations

from proot_distro.config import clear_user_key, load_user_config, save_user_config

__all__ = (
    "SETTING_KIND_BOOL", "SETTINGS",
    "SettingError", "list_settings", "normalize_bool", "normalize_value",
    "passthrough_enabled", "reset_settings", "set_setting",
)

# Value kinds. A kind names the normalizer applied to user input; see
# normalize_value().
SETTING_KIND_BOOL = "bool"


class SettingError(ValueError):
    """A setting name or value the CLI must report, not raise for.

    Raised by set_setting()/normalize_value() for an unknown item or a
    value the item's kind cannot accept; command_setting catches it and
    turns it into a one-line error with the accepted forms. Carries no
    state beyond the message on purpose.
    """


def normalize_bool(raw: str) -> bool:
    """Accept the usual spellings of on/off and return a real bool.

    ``true/false``, ``1/0``, ``t/f`` (any case), plus ``yes/no`` and
    ``on/off`` as courtesy aliases -- the forms a user plausibly types
    after `pd setting put passthrough`. Anything else, including a
    numeric ``2`` or an empty string, is a SettingError that names the
    accepted forms; nothing here ever "guesses" a default for a value
    it does not recognize.
    """
    token = str(raw).strip().lower()
    if token in ("true", "1", "t", "yes", "on"):
        return True
    if token in ("false", "0", "f", "no", "off"):
        return False
    raise SettingError(
        "invalid boolean value '{raw}'. Accepted: true/false, 1/0, "
        "t/f (any case), yes/no, on/off.".format(raw=raw)
    )


# The registry: setting name -> spec. Order is the display order of
# `pd setting ls`. The config.json key is the setting name itself.
# `doc` is shown by `ls` verbatim through _() (translated like every
# other message); `default` is the value when config.json says nothing.
SETTINGS: dict = {
    "passthrough": {
        "kind": SETTING_KIND_BOOL,
        "default": True,
        "doc": (
            "host Termux passthrough. on: container programs first, "
            "Termux binaries as the fallback (the current behavior). "
            "off: Termux binaries are removed from the container PATH "
            "and cannot be executed. Applies to new sessions."
        ),
    },
}

# Normalizers by kind, so normalize_value() stays the single dispatch
# point for future kinds.
_NORMALIZERS = {
    SETTING_KIND_BOOL: normalize_bool,
}


def normalize_value(name: str, raw: str):
    """Normalize *raw* per the kind of setting *name* (validates both)."""
    spec = SETTINGS.get(name)
    if spec is None:
        raise SettingError(
            "unknown setting '{name}'. Known settings: {known}.".format(
                name=name, known=", ".join(SETTINGS),
            )
        )
    return _NORMALIZERS[spec["kind"]](raw)


def _read_stored(name: str):
    """The stored value for *name*, or None when unset/invalid."""
    data = load_user_config()
    if name not in data:
        return None
    spec = SETTINGS.get(name)
    if spec is None:
        return None
    raw = data[name]
    if spec["kind"] == SETTING_KIND_BOOL:
        # A stored bool is what set_setting writes; anything else that
        # got into the file by hand is normalized, and unrecognizable
        # junk is treated as unset rather than fatal -- config reads
        # never break the program.
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            try:
                return normalize_bool(raw)
            except SettingError:
                return None
        return None
    return raw


def get_setting(name: str):
    """Current value of *name*: stored value or the spec default.

    Unknown names raise KeyError -- a caller asking for a setting by
    name has it spelled wrong in the code, and that must be loud.
    """
    if name not in SETTINGS:
        raise KeyError(name)
    stored = _read_stored(name)
    if stored is not None:
        return stored
    return SETTINGS[name]["default"]


def passthrough_enabled() -> bool:
    """Whether host Termux binaries ride the guest PATH (default: yes)."""
    return bool(get_setting("passthrough"))


def set_setting(name: str, raw: str):
    """Normalize and persist *raw* as the value of *name*; returns it."""
    value = normalize_value(name, raw)
    save_user_config({name: value})
    return value


def reset_settings(name: str | None = None) -> list:
    """Drop stored value(s) back to defaults; return the removed names.

    With *name*, only that setting (unknown names raise SettingError).
    Without, every registry-owned key -- and nothing else in the file,
    so ``language`` and any future foreign keys survive.
    """
    if name is not None and name not in SETTINGS:
        raise SettingError(
            "unknown setting '{name}'. Known settings: {known}.".format(
                name=name, known=", ".join(SETTINGS),
            )
        )
    targets = [name] if name is not None else list(SETTINGS)
    # clear_user_key() drops the whole file once it would be empty --
    # the same tidy-up `lang reset` does -- so a reset leaves no
    # config.json behind unless `language` still needs the space.
    return [key for key in targets if clear_user_key(key)]


def list_settings() -> list:
    """(name, current, default, is_default, doc) rows for `setting ls`.

    Current values come off disk, defaults off the registry; the CLI
    renders them. Unknown-shape stored values surface as the default
    here too -- `ls` never dies on a hand-edited config.json.
    """
    rows = []
    for name, spec in SETTINGS.items():
        stored = _read_stored(name)
        default = spec["default"]
        current = default if stored is None else stored
        rows.append((name, current, default, stored is None, spec["doc"]))
    return rows
