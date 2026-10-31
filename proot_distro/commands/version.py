#
# Proot-Distro - manage proot containers.
#
# Created by Sylirre <sylirre@termux.dev> for Termux project.
# Development assisted by Claude Code (https://claude.ai/code).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
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

"""``pd version`` (alias ``pd v``) subcommand: show version and build.

Prints the fork identity line with the short git commit the running
copy was built from, plus the facts that decide how it behaves here:
the Python it runs on, which container runtime it picked (nproot
preferred, stock proot as fallback, PD_PROOT_BIN as override) and the
active UI language. Bug reports start from this block.

The build hash is best effort by design: PD_BUILD_HASH wins (CI can
stamp the artifact), then ``git rev-parse`` resolved from this package
(checkout installs, the common case on Termux), then "unknown" (wheel
installs carry no repository). Every probe is defensive -- a version
display must never be the thing that crashes the CLI -- and the whole
command is exempt from the proot-on-PATH startup probe, so it renders
on a pristine system exactly like ``help`` does.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys

from proot_distro.arch import find_proot_bin
from proot_distro.constants import PROGRAM_VERSION
from proot_distro.i18n import _, get_language

__all__ = ("command_version", "build_hash")

FORK_NAME = "proot-distro-i18n"
FORK_URL = "https://github.com/masgzy/proot-distro-i18n"


def build_hash() -> str:
    """Return the short git commit of the running copy, best effort.

    Priority: PD_BUILD_HASH (CI stamp) > ``git rev-parse --short=8 HEAD``
    walked from this package's parent directory (works for checkouts,
    the common install on Termux) > "unknown". Same convention as
    nproot's ``--version`` (8-hex short hash), so support tickets line
    up across both repos.
    """
    stamped = os.environ.get("PD_BUILD_HASH", "").strip()
    if stamped:
        return stamped

    try:
        import proot_distro

        repo = os.path.dirname(
            os.path.dirname(os.path.abspath(proot_distro.__file__)))
        result = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--short=8", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        # No git, no repo, dubious ownership, timeout -- the fallback
        # below is the answer, never an exception.
        pass

    return "unknown"


def _runtime_display() -> str:
    """Render the container-runtime line: override > nproot/proot > none."""
    override = os.path.expanduser(os.environ.get("PD_PROOT_BIN") or "")
    if override:
        return _("Container runtime: {value} (PD_PROOT_BIN)", value=override)
    found = find_proot_bin()
    if found:
        return _("Container runtime: {value}", value=found)
    return _("Container runtime: {value}", value=_("not found"))


def _emit(*parts: str) -> None:
    """print() that stays quiet when the reader closed the pipe.

    ``pd version | grep -q build`` closes stdout as soon as it matches;
    a plain print would then die with a BrokenPipeError traceback and a
    non-zero status *after* the answer was already printed. Redirecting
    stdout to devnull on EPIPE lets the remaining lines (and the
    interpreter's shutdown flush) land harmlessly.
    """
    try:
        print(*parts)
    except BrokenPipeError:
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass


def command_version(args=None) -> None:
    """Print the version block (signature matches other commands; *args*
    is unused because this command takes no options).

    Everything goes to stdout through plain print(), like the data
    output of `list` / `ps` -- msg() would land on stderr and break
    `pd version | grep build` pipelines.
    """
    _emit(_("{name} {version} (build {hash})",
            name=FORK_NAME, version=PROGRAM_VERSION, hash=build_hash()))
    _emit()
    _emit(_("Python version: {value}", value=platform.python_version()))
    _emit(_runtime_display())
    _emit(_("UI language: {value}", value=get_language()))
    _emit(_("Source code: {value}", value=FORK_URL))
