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

"""PEP 517 backend: bake the build hash, then delegate to setuptools.

pip builds from a throwaway clone (``pip install git+URL``) or from an
sdist, and the *installed* package carries no repository to probe --
which is why a pip-installed pd used to report build ``unknown`` in
``pd version``.  This backend therefore resolves the short commit hash
while the sources still sit next to their ``.git`` (or while the
sdist's already-stamped file is there to inherit) and writes it to
``proot_distro/_build_hash.py``, which ships inside the wheel and is
the first thing ``build_hash()`` reads at runtime.

Resolution order here: PD_BUILD_HASH (an explicit CI stamp) > ``git
rev-parse`` > an existing stamp (sdist -> wheel inherits it) >
``unknown``.  Everything is best effort: a stamping failure must never
fail the build, so every probe is wrapped and a missing stamp simply
means the runtime probe chain takes over.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

_ROOT = pathlib.Path(__file__).parent.resolve()
_STAMP = _ROOT / "proot_distro" / "_build_hash.py"


def _resolve_hash() -> str:
    """Best-effort short hash for this build, exactly like version.py."""
    stamped = os.environ.get("PD_BUILD_HASH", "").strip()
    if stamped:
        return stamped

    try:
        result = subprocess.run(
            ["git", "-C", str(_ROOT), "rev-parse", "--short=8", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        # No git, no repo, dubious ownership, timeout -- fall through.
        pass

    if _STAMP.exists():
        # Rebuilding from an sdist: inherit the stamp baked earlier.
        try:
            for line in _STAMP.read_text(encoding="utf-8").splitlines():
                if line.startswith("__build_hash__"):
                    value = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if value:
                        return value
        except Exception:
            pass

    return "unknown"


def _stamp() -> None:
    try:
        _STAMP.write_text(
            f'__build_hash__ = "{_resolve_hash()}"\n', encoding="utf-8")
    except Exception:
        pass


_stamp()

# Re-export the PEP 517 hooks; setuptools>=77 (see pyproject) has all
# of them, editable ones included.
from setuptools.build_meta import (  # noqa: E402
    build_editable,
    build_sdist,
    build_wheel,
    get_requires_for_build_editable,
    get_requires_for_build_sdist,
    get_requires_for_build_wheel,
    prepare_metadata_for_build_editable,
    prepare_metadata_for_build_wheel,
)
