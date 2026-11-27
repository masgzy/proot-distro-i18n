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

# Architecture: ANSI color constants and a minimal msg() helper. Colors are
# enabled only when stderr is a TTY and no disabling variable is set. The C
# dict maps symbolic names to escape sequences so callers don't deal with
# raw ANSI codes. Every entry starts with _RST so transitions implicitly
# reset attributes.
#
# Palette: Catppuccin (https://catppuccin.com/palette/). Mocha by default,
# Latte on a light terminal -- CLI_THEME=dark|light overrides, COLORFGBG
# is probed when it is not set. The semantic hues are the official values:
# green #a6e3a1 / peach #fab387 / red #f38ba8 / blue #89b4fa / sky #89dceb
# / yellow #f9e2af / mauve #cba6f7 / pink #f5c2e7 (Mocha; Latte's darker
# counterparts below). The 24-bit sequences are only used when the
# terminal advertises truecolor (COLORTERM); anything else -- TERM=xterm,
# tmux without truecolor, a redirect -- falls back to the same hues as
# the nearest of the 16 base ANSI colors, so the semantic mapping (red
# errors, green success, cyan info) survives every level of degradation.
# NO_COLOR, CLICOLOR=0 and PD_FORCE_NO_COLORS disable color outright;
# PD_FORCE_COLORS (or FORCE_COLOR) forces it on for pipes and CI.
#
# tty_safe_for_writes() lets callers detect when stderr's TTY has been
# taken over by another process for interactive input — e.g., the pinentry
# triggered by piping a backup into 'gpg -c'. In that state any of our
# writes (info lines, progress bars) would land on top of the other
# program's display. msg() and the progress bars (progress.py) consult
# this so that piping into curses/no-echo consumers stays clean.
#
# A process-global "quiet" flag is also exported: set_quiet(True) (called
# by command handlers when --quiet was passed) makes is_quiet() return True
# so progress-bar code paths in helpers can skip their writes. msg() itself
# is unaffected — error messages must always reach the user.

import os
import sys

from proot_distro.i18n import _

try:
    import termios
except ImportError:
    termios = None

_RST       = "\033[0m"
_BOLD      = "\033[1m"
_ITALIC    = "\033[3m"
_UNDERLINE = "\033[4m"
_RED       = "\033[31m"
_GREEN     = "\033[32m"
_YELLOW    = "\033[33m"
_BLUE      = "\033[34m"
_MAGENTA   = "\033[35m"
_CYAN      = "\033[36m"
_WHITE     = "\033[37m"

# --- Catppuccin palettes (hex -> 24-bit SGR sequences) -----------------------

# (Mocha) the dark flavor; see https://catppuccin.com/palette/ for the
# full 26-color set -- these are the ten this CLI speaks.
_MOCHA = {
    "mauve":   (203, 166, 247),  # #cba6f7  titles, emphasis
    "text":    (205, 214, 244),  # #cdd6f4  body
    "subtext": (166, 173, 200),  # #a6adc8  secondary
    "green":   (166, 227, 161),  # #a6e3a1  success
    "peach":   (250, 179, 135),  # #fab387  warning
    "red":     (243, 139, 168),  # #f38ba8  error
    "blue":    (137, 180, 250),  # #89b4fa  paths, URLs
    "yellow":  (249, 226, 175),  # #f9e2af  numbers, counts
    "sky":     (137, 220, 235),  # #89dceb  info
    "pink":    (245, 194, 231),  # #f5c2e7  keywords
}

# (Latte) the light flavor, for a terminal on a light background.
_LATTE = {
    "mauve":   (136, 57, 239),   # #8839ef
    "text":    (76, 79, 105),    # #4c4f69
    "subtext": (108, 111, 133),  # #6c6f85
    "green":   (64, 160, 43),    # #40a02b
    "peach":   (254, 100, 11),   # #fe640b
    "red":     (210, 15, 57),    # #d20f39
    "blue":    (30, 102, 245),   # #1e66f5
    "yellow":  (223, 142, 29),   # #df8e1d
    "sky":     (4, 165, 229),    # #04a5e5
    "pink":    (234, 118, 203),  # #ea76cb
}

# The 16-color fallback, used whenever the terminal has not advertised
# truecolor. One base hue per semantic color, mirroring the mapping the
# palette entries were chosen for.
_BASE16 = {
    "mauve": _MAGENTA, "text": _WHITE, "subtext": _WHITE,
    "green": _GREEN, "peach": _YELLOW, "red": _RED,
    "blue": _BLUE, "yellow": _YELLOW, "sky": _CYAN, "pink": _MAGENTA,
}

_ATTRIBUTES = (("bold", _BOLD), ("italic", _ITALIC),
               ("underline", _UNDERLINE))


def _sgr(rgb, attrs) -> str:
    """The escape sequence that resets, applies *attrs*, then *rgb*.

    *rgb* is an (r, g, b) tuple from the Catppuccin palettes, rendered
    as 24-bit color -- or, on the 16-color fallback, a ready-made base
    sequence from _BASE16 that is appended as it is.
    """
    seq = _RST
    for name, code in _ATTRIBUTES:
        if name in attrs:
            seq += code
    if rgb is not None:
        if isinstance(rgb, tuple):
            seq += f"\033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"
        else:
            seq += rgb
    return seq


def _detect_theme() -> str:
    """'dark' or 'light', from CLI_THEME, else COLORFGBG, else dark.

    COLORFGBG is 'foreground;background' (e.g. Termux's '15;0'). Only
    the background half decides: colors 0-6 and 8 are dark backgrounds,
    7 and 9-15 are light ones. Terminals that send nothing stay on the
    dark palette, the one most of this CLI's users will be on.
    """
    override = os.environ.get("CLI_THEME", "").strip().lower()
    if override in ("dark", "light"):
        return override
    fgbg = os.environ.get("COLORFGBG", "")
    try:
        bg = int(fgbg.split(";")[-1])
    except (ValueError, IndexError):
        return "dark"
    return "dark" if bg in (0, 1, 2, 3, 4, 5, 6, 8) else "light"


def _truecolor_enabled() -> bool:
    return "truecolor" in os.environ.get("COLORTERM", "").lower() or \
        "24bit" in os.environ.get("COLORTERM", "").lower()


def _semantic_colors() -> dict:
    """Base hue per semantic name, palette or 16-color fallback."""
    if _truecolor_enabled():
        palette = _LATTE if _detect_theme() == "light" else _MOCHA
        return palette
    return _BASE16


def _build_colors() -> dict:
    """The C table: every symbolic name the CLI uses, mapped to a sequence.

    Composite names carry their attributes explicitly; the hue comes from
    _semantic_colors(), so a truecolor terminal gets the Catppuccin
    palette and every other terminal the matching base hue.
    """
    palette = _semantic_colors()
    table = {"RST": _RST}
    for name, attrs in (
        ("RED", ()), ("GREEN", ()), ("YELLOW", ()), ("BLUE", ()),
        ("MAGENTA", ()), ("CYAN", ()), ("WHITE", ()),
        ("BRED", ("bold",)), ("IRED", ("italic",)),
        ("URED", ("underline",)), ("UBRED", ("underline", "bold")),
        ("BGREEN", ("bold",)), ("IGREEN", ("italic",)),
        ("UGREEN", ("underline",)), ("UBGREEN", ("underline", "bold")),
        ("BYELLOW", ("bold",)), ("IYELLOW", ("italic",)),
        ("UYELLOW", ("underline",)), ("UBYELLOW", ("underline", "bold")),
        ("BBLUE", ("bold",)), ("IBLUE", ("italic",)),
        ("UBLUE", ("underline",)), ("UBBLUE", ("underline", "bold")),
        ("BMAGENTA", ("bold",)), ("IMAGENTA", ("italic",)),
        ("UMAGENTA", ("underline",)),
        ("UBMAGENTA", ("underline", "bold")),
        ("BCYAN", ("bold",)), ("ICYAN", ("italic",)),
        ("UCYAN", ("underline",)), ("UBCYAN", ("underline", "bold")),
        ("BWHITE", ("bold",)), ("IWHITE", ("italic",)),
        ("UWHITE", ("underline",)), ("UBWHITE", ("underline", "bold")),
    ):
        # Which semantic name does this symbolic color stand for? The
        # base names map one-to-one; the composite ones keep their hue.
        semantic = {
            "RED": "red", "GREEN": "green", "YELLOW": "yellow",
            "BLUE": "blue", "MAGENTA": "pink", "CYAN": "sky",
            "WHITE": "text",
        }.get(name)
        if semantic is None:
            for base, sem in (
                ("RED", "red"), ("GREEN", "green"), ("YELLOW", "yellow"),
                ("BLUE", "blue"), ("MAGENTA", "pink"), ("CYAN", "sky"),
                ("WHITE", "text"),
            ):
                if base in name:
                    semantic = sem
                    break
        table[name] = _sgr(palette.get(semantic), attrs)
    return table


_COLORS = _build_colors()
_EMPTY = {k: "" for k in _COLORS}


def _init_colors(stream: str = "stderr") -> dict:
    """The C table for this run, or its no-color counterpart.

    NO_COLOR is the community convention (https://no-color.org/), present
    means disabled; CLICOLOR=0 agrees with it; PD_FORCE_NO_COLORS is this
    program's own switch. Any of the four wins over everything. PD_FORCE_COLORS
    and FORCE_COLOR turn color on for a pipe or a CI log, where the TTY
    probe below would otherwise disable it.

    *stream* picks which stream's TTY-ness decides (stderr by default,
    "stdout" for the data-printing commands: their output is piped in
    the classic `pd version | grep` even while stderr sits on a terminal).
    """
    env = os.environ
    if (env.get("NO_COLOR") is not None
            or env.get("CLICOLOR", "") == "0"
            or env.get("PD_FORCE_NO_COLORS")):
        return _EMPTY
    forced = bool(env.get("PD_FORCE_COLORS")
                  or (env.get("FORCE_COLOR")
                      and env.get("FORCE_COLOR") != "0"))
    probe_fd = sys.stdout if stream == "stdout" else sys.stderr
    try:
        is_tty = probe_fd.isatty()
    except (AttributeError, OSError, ValueError):
        is_tty = False
    if not (is_tty or forced):
        return _EMPTY
    return _COLORS


C = _init_colors()

# stdout 变体：数据输出命令（version 的版块）自己打印到 stdout，
# stderr 的 TTY 探测管不到它们——`pd version | grep` 的经典场景里
# stdout 是管道而 stderr 还在终端上，两者必须各自回答。开关与
# _init_colors 同一套，只是 TTY 探测看 stdout。
C_OUT = _init_colors(stream="stdout")


def tty_safe_for_writes() -> bool:
    """Return False when stderr's TTY is currently being used by another
    process for interactive input (a password prompt or a full-screen
    curses UI). Return True otherwise.

    A TTY in canonical mode with ECHO enabled is in its normal "shell"
    state. Programs that read a passphrase clear ECHO; programs that
    drive a full-screen UI clear ICANON. Either signal means another
    process has taken control of the TTY and our writes — particularly
    destructive escapes like '\\r' or '\\033[K' from a progress bar —
    would corrupt that program's display. The check looks only at the
    TTY's termios state, so it triggers for any such consumer (pinentry,
    sudo, ssh-askpass, less, etc.) without naming any specific program.
    Non-TTY stderr (file/pipe) always returns True so plain redirection
    is unaffected.
    """
    if termios is None:
        return True
    try:
        fd = sys.stderr.fileno()
    except (AttributeError, OSError, ValueError):
        return True
    try:
        if not os.isatty(fd):
            return True
    except OSError:
        return True
    try:
        attrs = termios.tcgetattr(fd)
    except (OSError, termios.error):
        return True
    lflag = attrs[3]
    return bool(lflag & termios.ECHO) and bool(lflag & termios.ICANON)


def terminal_width(default: int = 80) -> int:
    """Return the terminal's column count, or *default* when unknown.

    stderr is probed first because that is where the commands that lay
    out columns (`ps`, `list --image`, `search`) render; stdout is the fallback
    for the case where only it is still attached to the terminal.
    """
    for fd in (2, 1):
        try:
            cols = os.get_terminal_size(fd).columns
        except (OSError, ValueError):
            continue
        if cols > 0:
            return cols
    return default


_quiet = False


def set_quiet(value: bool) -> None:
    """Enable or disable quiet mode for the rest of the process.

    Called by command handlers that accept --quiet. When enabled, log_info()
    becomes a no-op and is_quiet() returns True so progress-bar code
    paths in helpers can also bail out.
    """
    global _quiet
    _quiet = bool(value)


def is_quiet() -> bool:
    """Return True when quiet mode has been enabled for this process."""
    return _quiet


def msg(*args):
    """Print *args* to stderr after clearing any partial progress line.

    Suppressed when the TTY is currently being used by another program
    (pinentry, curses) — see tty_safe_for_writes for the rationale.

    The `\r\033[K` progress-line eraser is only emitted when stderr is
    a TTY: progress bars are drawn exclusively on a TTY, so off a TTY
    there is no partial line to clear and the escape would only inject
    control characters into a redirected log.
    """
    if not tty_safe_for_writes():
        return
    try:
        is_tty = sys.stderr.isatty()
    except (AttributeError, OSError, ValueError):
        is_tty = False
    if is_tty:
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()
    print(*args, file=sys.stderr)


def log_info(text: str):
    """Emit a `[*] text` info line. No-op under --quiet.

    *text* must already be passed through ``_()`` by the caller; this
    function only handles coloring and the quiet bypass.
    """
    if _quiet:
        return
    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}{text}{C['RST']}")


def log_error(text: str):
    """Emit a `[!] text` error line. Always shown — even under --quiet."""
    msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}{text}{C['RST']}")


def warn(text: str):
    """Emit a 'Warning: text' line in yellow."""
    msg(f"{C['BYELLOW']}{_('Warning')}: {C['YELLOW']}{text}{C['RST']}")


def crit_error(text: str):
    """Emit an 'Error: text' line in red. Caller typically calls sys.exit(1)."""
    msg(f"{C['BRED']}{_('Error')}: {C['RED']}{text}{C['RST']}")


# Characters that get a readable escape rather than a numeric one.
_QUOTE_MAP = {
    "\\": "\\\\",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\x1b": "\\e",
}


def quote_path(text: str) -> str:
    """Render *text* printable, C-style escapes for control characters.

    Names inside a container rootfs, an archive, an image's metadata or a
    registry's reply are chosen by whoever controls that content, and the
    commands print them freely: `copy` and `sync` log every entry under
    `--verbose`, the skip warning names the file it passed over, and an
    OSError carries the name it failed on. A name holding ESC repaints the
    terminal, hides the lines around it, or hands the emulator whatever
    sequence follows it, so nothing below 0x20 — nor DEL — is allowed
    through literally. Backslash is escaped too, so an escape this produced
    cannot be confused with the same characters spelled out in a name.

    A lone surrogate is escaped as well, as `\\udcff`. It is how a byte
    that is not UTF-8 arrives -- out of argv, the environment, or a JSON
    document's `\\udcff` -- and no stream can encode one: stderr renders
    it with backslashreplace, which is where the spelling comes from, but
    stdout is strict and raised UnicodeEncodeError on it.

    Only the untrusted text is passed through here, never a whole message:
    the colour codes the log helpers wrap it in are control characters by
    definition.
    """
    out = []
    for ch in text:
        escaped = _QUOTE_MAP.get(ch)
        if escaped is not None:
            out.append(escaped)
        elif ch < " " or ch == "\x7f":
            out.append(f"\\x{ord(ch):02x}")
        elif "\ud800" <= ch <= "\udfff":
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def quote_error(exc: BaseException) -> str:
    """The reason *exc* gives, safe to print next to a name of our own.

    An OSError's `strerror` is the message alone — "No such file or
    directory" — with no filename in it, which is what callers want: they
    name the entry themselves, through quote_path, and adding str(exc)
    would repeat it. Everything else here has only str(): a TarError, a
    BuildError, a RuntimeError raised while an image was unpacked. Those
    interpolate names into their message raw, so the whole string is
    untrusted and goes through quote_path.
    """
    return quote_path(getattr(exc, "strerror", None) or str(exc))
