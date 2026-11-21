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

The settings:

* ``passthrough`` (default OFF): host Termux passthrough. off -- a
  normal-type container's PATH has the host Termux ``bin`` dir
  stripped and the profile.d snippet drops the append block, so
  Termux binaries are not reachable from inside the container. on --
  the host bin dir is appended last on the guest PATH. Sessions must
  be restarted for a change to apply.
* ``distro_mirror`` (default OFF): when it is on, the moment an
  ``install`` finishes the rootfs' package-manager sources are
  switched to a Chinese mirror, so the first ``apt update`` inside a
  fresh container is already fast.
* ``dns`` (default "auto"): what ``/etc/resolv.conf`` says inside a
  container. auto -- follow the host's own resolv.conf (fallback:
  the static 8.8.8.8/8.8.4.4 the upstream ships). A comma-separated
  list of up to 3 IP addresses pins the servers instead
  (``pd setting put dns 223.5.5.5,119.29.29.29``). Honored at
  install and re-applied at each login -- but only while the file is
  still one this program wrote; a resolv.conf the guest's own root
  edited is left alone.
"""

from __future__ import annotations

import ipaddress

from proot_distro.config import clear_user_key, load_user_config, save_user_config
from proot_distro.i18n import _

__all__ = (
    "SETTING_KIND_BOOL", "SETTING_KIND_IP_LIST", "SETTINGS",
    "SettingError", "list_settings", "normalize_bool", "normalize_value",
    "passthrough_enabled", "reset_settings", "set_setting",
)

# Value kinds. A kind names the normalizer applied to user input; see
# normalize_value().
SETTING_KIND_BOOL = "bool"
SETTING_KIND_IP_LIST = "ip_list"

# ``dns`` 能接受的自动档拼写（= 跟随宿主 resolv.conf）。
_DNS_AUTO_TOKENS = ("auto", "default", "host")

# musl 与 glibc 的 resolver 各自最多读 3 个 nameserver（MAXNS=3）。
_MAX_DNS_SERVERS = 3


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


def normalize_ip_list(raw: str):
    """Normalize a ``dns`` value: ``auto`` or up to 3 IP addresses.

    Accepts comma- or whitespace-separated IPv4/IPv6 addresses, each
    validated with ``ipaddress`` (leading/trailing blanks stripped),
    deduplicated preserving order. More than 3 addresses is an error
    -- musl and glibc each read at most 3 nameservers (MAXNS=3), so a
    fourth is a promise the guest resolver never honors; the error
    names the cap so the user can drop one instead of guessing.

    The auto spellings (auto/default/host) return the string
    ``"auto"`` -- follow the host's resolv.conf, falling back to the
    static servers when the host has none. An empty value is an
    error like any other unrecognized one: `pd setting reset dns`
    is the way back to auto, and an empty string means nothing.
    """
    token = str(raw).strip()
    if token.lower() in _DNS_AUTO_TOKENS:
        return "auto"
    if not token:
        raise SettingError(_(
            "empty 'dns' value. Give up to {n} IP addresses "
            "('223.5.5.5,119.29.29.29'), 'auto' to follow the host, "
            "or use `setting reset dns`.".format(n=_MAX_DNS_SERVERS)
        ))
    seen, servers = set(), []
    pieces = token.replace(",", " ").split()
    for piece in pieces:
        try:
            ip = ipaddress.ip_address(piece)
        except ValueError:
            raise SettingError(_(
                "'{piece}' is not a valid IP address for 'dns'. "
                "Example: 223.5.5.5,119.29.29.29; or 'auto' to "
                "follow the host.".format(piece=piece)
            )) from None
        text = str(ip)
        if text not in seen:
            seen.add(text)
            servers.append(text)
    if not servers:
        raise SettingError(_("empty 'dns' value."))
    if len(servers) > _MAX_DNS_SERVERS:
        raise SettingError(_(
            "{n} addresses given for 'dns'; the guest resolvers "
            "(musl, glibc) each read at most 3 nameservers.".format(
                n=len(servers))
        ))
    return servers


# The registry: setting name -> spec. Order is the display order of
# `pd setting ls`. The config.json key is the setting name itself.
# `doc` is shown by `ls` verbatim through _() (translated like every
# other message); `default` is the value when config.json says nothing.
SETTINGS: dict = {
    "passthrough": {
        "kind": SETTING_KIND_BOOL,
        "default": False,
        "doc": (
            "host Termux passthrough. off (default): Termux binaries "
            "are removed from the container PATH and cannot be "
            "executed. on: container programs first, Termux binaries "
            "as the fallback. Applies to new sessions."
        ),
    },
    "distro_mirror": {
        "kind": SETTING_KIND_BOOL,
        "default": False,
        "doc": (
            "switch a freshly installed container's package sources "
            "to a Chinese mirror (Aliyun/TUNA). off (default): keep "
            "the image's own sources. on: 'install' rewrites apt/apk/"
            "dnf/pacman/zypp sources right after extraction; switch "
            "an existing container with 'mirror distro', revert it "
            "with 'mirror distro <name> --revert'."
        ),
    },
    "dns": {
        "kind": SETTING_KIND_IP_LIST,
        "default": "auto",
        "doc": (
            "what /etc/resolv.conf says inside a container. auto "
            "(default): follow the host's resolver config, falling "
            "back to the static 8.8.8.8/8.8.4.4. Or up to 3 IP "
            "addresses ('223.5.5.5,119.29.29.29') to pin the "
            "nameservers. Applied at install and re-applied by every "
            "login while the file is still ours; a resolv.conf the "
            "guest edited itself is never touched."
        ),
    },
}

# Normalizers by kind, so normalize_value() stays the single dispatch
# point for future kinds.
_NORMALIZERS = {
    SETTING_KIND_BOOL: normalize_bool,
    SETTING_KIND_IP_LIST: normalize_ip_list,
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
    if spec["kind"] == SETTING_KIND_IP_LIST:
        # "auto" or a list of valid, unique, ≤3 IP strings; anything
        # else (hand-edited junk) reads as unset.
        if raw == "auto":
            return raw
        if isinstance(raw, list):
            try:
                merged = normalize_ip_list(" ".join(str(x) for x in raw))
            except SettingError:
                return None
            return merged if isinstance(merged, list) else None
        if isinstance(raw, str):
            try:
                merged = normalize_ip_list(raw)
            except SettingError:
                return None
            return merged
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
    """Whether host Termux binaries ride the guest PATH (default: no)."""
    return bool(get_setting("passthrough"))


def distro_mirror_enabled() -> bool:
    """Whether install should switch the guest's package sources (default: no)."""
    return bool(get_setting("distro_mirror"))


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
