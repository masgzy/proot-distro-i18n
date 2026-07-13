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

# Architecture: CLI entry point. Most of the heavy lifting now lives
# elsewhere:
#
#   parser.py             — argparse construction + ALIAS_TO_CANONICAL.
#   commands/help/        — HELP_COMMANDS dispatcher + per-command pages.
#   commands/*            — one module (or subpackage) per subcommand.
#
# main() routes signals, checks the runtime environment (root warn,
# nested-proot rejection, proot probe), runs the parser, validates
# required positionals, and dispatches to the matching command.

import os
import shutil
import signal
import subprocess
import sys

from proot_distro.constants import IS_TERMUX, PROGRAM_NAME
from proot_distro.i18n import _
from proot_distro.message import C, msg, set_quiet, crit_error
from proot_distro.arch import get_proot_bin
from proot_distro.parser import (
    ALIAS_TO_CANONICAL, REQUIRED_ARGS, build_parser,
)
from proot_distro.commands.help import command_help, HELP_COMMANDS
from proot_distro.commands.install import command_install
from proot_distro.commands.remove import command_remove
from proot_distro.commands.rename import command_rename
from proot_distro.commands.reset import command_reset
from proot_distro.commands.login import command_login
from proot_distro.commands.list import command_list
from proot_distro.commands.backup import command_backup
from proot_distro.commands.restore import command_restore
from proot_distro.commands.clear_cache import command_clear_cache
from proot_distro.commands.copy import command_copy
from proot_distro.commands.sync import command_sync
from proot_distro.commands.run import command_run
from proot_distro.commands.build import command_build
from proot_distro.commands.push import command_push
from proot_distro.commands.ps import command_ps
from proot_distro.commands.kill import command_kill
from proot_distro.commands.mirror import command_mirror


_COMMAND_HANDLERS = {
    "install":     command_install,
    "remove":      command_remove,
    "rename":      command_rename,
    "reset":       command_reset,
    "login":       command_login,
    "list":        command_list,
    "backup":      command_backup,
    "restore":     command_restore,
    "clear-cache": command_clear_cache,
    "copy":        command_copy,
    "sync":        command_sync,
    "run":         command_run,
    "build":       command_build,
    "push":        command_push,
    "ps":          command_ps,
    "kill":        command_kill,
    "mirror":      command_mirror,
    "help":        command_help,
}


def _sigquit_to_keyboard_interrupt(_signum, _frame):
    raise KeyboardInterrupt()


def _warn_if_root() -> None:
    if os.getuid() == 0:
        msg()
        msg(f"{C['BRED']}{_('Warning')}: {PROGRAM_NAME} "
            f"{_('should not be executed as root user. Do not send bug reports about messed up Termux environment, lost data and bricked devices.')}"
            f"{C['RST']}")
        msg()


def _refuse_nested_proot() -> None:
    """Exit when we're running inside a proot — nested proot is unsupported.

    Single read of /proc/self/status; the TracerPid line is enough to
    decide. We avoid the second /proc/<pid>/status open when TracerPid
    is 0 (the common case), short-circuiting the hot path.

    Defensive parsing: if /proc/self/status is malformed (TracerPid line
    missing or non-numeric), we skip the check rather than crashing.
    """
    try:
        with open(f"/proc/{os.getpid()}/status") as fh:
            for line in fh:
                if not line.startswith("TracerPid:"):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                try:
                    tracer_pid = int(parts[1])
                except ValueError:
                    continue
                if tracer_pid == 0:
                    return  # common case: not traced
                try:
                    with open(f"/proc/{tracer_pid}/status") as tfh:
                        for tline in tfh:
                            if tline.startswith("Name:") and "proot" in tline:
                                crit_error(f"{PROGRAM_NAME} "
                                           f"{_('should not be executed under PRoot.')}")
                                sys.exit(1)
                except OSError:
                    return
                return
    except OSError:
        pass


# Commands that never invoke proot — exempt from the proot-on-PATH
# startup probe. Documented inline so future commands can opt in.
_PROOT_EXEMPT_COMMANDS = frozenset({"build", "push", "kill", "ps", "mirror", "help"})


def ensure_proot_installed() -> None:
    """Verify proot is on PATH; offer to install it on Termux (TTY only).

    Exits the process when proot is unavailable and cannot be installed
    (or the user declines the offer). Shared by the startup probe and by
    `build`, which defers this check until it knows the Dockerfile
    actually contains a RUN-family instruction.
    """
    if os.environ.get("PD_PROOT_BIN"):
        get_proot_bin()  # validates, crit_error()+exit(1) if bad
        return

    if shutil.which("proot") is not None:
        return

    msg()
    crit_error(_("proot utility does not exist on your system."))
    msg()

    if not IS_TERMUX:
        sys.exit(1)

    if not sys.stdin.isatty():
        msg(f"{C['CYAN']}{_('Install it with: ')}"
            f"{C['GREEN']}pkg install proot{C['RST']}")
        msg()
        sys.exit(1)

    sys.stderr.write(
        f"{C['CYAN']}{_('Would you like to install it now? [y/N] ')}{C['RST']}"
    )
    sys.stderr.flush()
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer not in ("y", "yes"):
        msg()
        msg(f"{C['CYAN']}{_('Install it manually with: ')}"
            f"{C['GREEN']}pkg install proot{C['RST']}")
        msg()
        sys.exit(1)

    msg()
    try:
        subprocess.run(["pkg", "install", "-y", "-q", "proot"], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        msg()
        crit_error(_("failed to install proot: {exc}", exc=exc))
        msg()
        sys.exit(1)


def _ensure_proot_available(first_canonical: str) -> None:
    """Verify proot is on PATH; offer to install it on Termux.

    `build` and `push` are exempt from this startup probe: `build` may
    run a Dockerfile with no RUN instructions in pure-Python mode, and
    `push` only reads from the local manifest/layer cache and uploads
    to a registry. `build` runs its own check after parsing the
    Dockerfile and refuses only when RUN (or ONBUILD RUN) is actually
    present. `kill` and `ps` are exempt too: they only inspect and
    signal already-running sessions via the session registry and never
    invoke proot. `mirror` is exempt as well: it only does HTTP probes
    against registries and never invokes proot, so users can fix a
    broken registry source even without proot installed. `help` is
    exempt because help text should always render.
    """
    if first_canonical in _PROOT_EXEMPT_COMMANDS:
        return
    ensure_proot_installed()


def _dispatch_help(raw_args) -> bool:
    """Render per-command help when ``-h``/``--help``/``--usage`` is given.

    Intercepting before argparse runs ensures missing required
    positionals never produce an error instead of help. Returns True
    iff help was rendered (and the caller should exit cleanly).
    """
    if len(raw_args) < 2 or raw_args[1] not in ("-h", "--help", "--usage"):
        return False
    cmd = ALIAS_TO_CANONICAL.get(raw_args[0], raw_args[0])
    if cmd in HELP_COMMANDS:
        HELP_COMMANDS[cmd]()
        return True
    return False


def _reject_unknown_command(raw_args) -> None:
    """Exit with help text when the first arg names no known command."""
    if not raw_args:
        return
    first = raw_args[0]
    if (
        not first.startswith("-")
        and first not in _COMMAND_HANDLERS
        and first not in ALIAS_TO_CANONICAL
    ):
        msg()
        crit_error(_("unknown command '{first}'.", first=first))
        command_help()
        msg()
        sys.exit(1)


def _split_separator(canonical, raw_args, args):
    """Set args.login_cmd / args.run_args from tokens after a literal '--'."""
    if canonical == "login":
        if "--" in raw_args:
            sep_idx = raw_args.index("--")
            args.login_cmd = raw_args[sep_idx + 1:]
        else:
            args.login_cmd = []
    elif canonical == "run":
        if "--" in raw_args:
            sep_idx = raw_args.index("--")
            args.run_args = raw_args[sep_idx + 1:]
        else:
            args.run_args = []


def main() -> None:
    """CLI entry point — installed as both `proot-distro` and `pd`.

    Validates the runtime environment, parses arguments, and dispatches
    to the chosen command's handler.
    """
    # Route SIGQUIT (Ctrl-\), SIGTERM (kill default) and SIGHUP
    # (terminal closed) through KeyboardInterrupt so every
    # `except KeyboardInterrupt` block in the codebase handles them the
    # same as SIGINT (Ctrl-C). The default disposition of these signals
    # is to terminate the process immediately, which would skip
    # progress-bar cleanup, partial-file removal, and "Aborted by
    # user" messaging that the Ctrl-C handlers already provide.
    for sig in (signal.SIGQUIT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _sigquit_to_keyboard_interrupt)

    _warn_if_root()
    _refuse_nested_proot()

    first_canonical = ""
    if len(sys.argv) >= 2:
        first_canonical = ALIAS_TO_CANONICAL.get(sys.argv[1], sys.argv[1])

    # Help short-circuit: any -h/--help/--usage (top-level or per-command)
    # is dispatched BEFORE the proot-on-PATH probe. Rationale: help text
    # should always render, even on a fresh system where proot is not yet
    # installed — the user needs to read `install --help` to know what to
    # do. The probe still runs for non-help invocations.
    if len(sys.argv) < 2 or sys.argv[1] in (
        "-h", "--help", "help", "hel", "he", "h"
    ):
        command_help()
        sys.exit(0)

    raw_args = sys.argv[1:]
    if _dispatch_help(raw_args):
        sys.exit(0)

    _ensure_proot_available(first_canonical)

    # Validate the command before argparse runs. An unknown subcommand
    # name causes _SubParsersAction to raise ArgumentError, which
    # parse_known_args routes through self.error() — printing
    # argparse's own message before our custom error handler runs.
    _reject_unknown_command(raw_args)

    parser = build_parser()
    args, unknown = parser.parse_known_args(raw_args)

    command = args.command
    if command is None:
        msg()
        crit_error(_("unknown command '{first}'.", first=raw_args[0]))
        command_help()
        msg()
        sys.exit(1)

    canonical = ALIAS_TO_CANONICAL.get(command, command)

    if getattr(args, "help", False):
        if canonical in HELP_COMMANDS:
            HELP_COMMANDS[canonical]()
        else:
            command_help()
        sys.exit(0)

    # For login and run, anything after a literal '--' is the inner
    # command and must not be flagged as unknown — re-parse only the
    # portion before '--' to get a clean unknown list.
    check_unknown = unknown
    if canonical in ("login", "run") and "--" in raw_args:
        sep_idx = raw_args.index("--")
        _parsed, check_unknown = parser.parse_known_args(raw_args[:sep_idx])
    if check_unknown:
        bad = check_unknown[0]
        kind = (_("unrecognized option") if bad.startswith("-")
                else _("unexpected argument"))
        msg()
        crit_error(_("{kind}: '{bad}'.", kind=kind, bad=bad))
        if canonical in HELP_COMMANDS:
            HELP_COMMANDS[canonical]()
        msg()
        sys.exit(1)

    for arg_name, error_msg in REQUIRED_ARGS.get(canonical, []):
        if getattr(args, arg_name, None) is None:
            msg()
            crit_error(_(error_msg))
            if canonical in HELP_COMMANDS:
                HELP_COMMANDS[canonical]()
            sys.exit(1)

    _split_separator(canonical, raw_args, args)

    # Enable quiet mode globally before dispatch so helpers
    # (helpers/docker, helpers/download, etc.) silence their info
    # lines and progress bars too. `list`, `ps` and `mirror` have different
    # --quiet semantics (print names / PIDs / URLs only) and do not use
    # log_info(), so the global flag is left off for them.
    if canonical not in ("list", "ps", "mirror") and getattr(args, "quiet", False):
        set_quiet(True)

    handler = _COMMAND_HANDLERS.get(canonical)
    if handler is None:
        crit_error(_("unknown command '{command}'.", command=command))
        sys.exit(1)

    handler(args)
