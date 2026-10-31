# Opt-in live test: real proot execution end-to-end. Skipped unless
# RUN_LIVE_TESTS=1, and additionally skipped when proot is not installed.
#
#   RUN_LIVE_TESTS=1 python -m pytest tests/live/test_live_proot.py -q

import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from proot_distro.commands.install import command_install
from proot_distro.commands.remove import command_remove
from proot_distro.paths import container_rootfs

pytestmark = pytest.mark.live


def _runtime_works() -> bool:
    """Whether the installed proot can actually run on this kernel.

    Debian and Ubuntu still ship proot 5.1.0, whose seccomp accelerator
    predates the fixes that went into 5.3.0: on a modern kernel the
    tracee dies with SIGSYS and even `proot -0 /bin/true` exits 255.
    A runtime that cannot run anything must skip the live suite rather
    than fail it -- the Termux container job covers the real end-to-end
    with a current runtime.
    """
    true_bin = shutil.which("true")
    if true_bin is None:
        return False
    try:
        return subprocess.run(
            ["proot", "-0", true_bin], capture_output=True, timeout=30,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(shutil.which("proot") is None, reason="proot not installed")
@pytest.mark.skipif(not _runtime_works(),
                    reason="installed proot cannot run on this kernel "
                           "(pre-5.3.0 seccomp)")
def test_live_proot_run_echo():
    name = "live-proot"
    try:
        command_install(SimpleNamespace(
            image_ref="alpine:latest", custom_container_name=name,
            override_arch=None,
        ))
        assert os.path.isdir(container_rootfs(name))

        # `login` execs proot (replacing the process), so run it in a child.
        # The child inherits this process's sandbox XDG_* env, so it resolves
        # the same container store.
        code = (
            "import sys;"
            "from proot_distro.cli import main;"
            f"sys.argv=['pd','login','{name}','--','echo','PROOT_OK'];"
            "main()"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, env=os.environ.copy(), timeout=120,
        )
        assert "PROOT_OK" in result.stdout, (
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    finally:
        if os.path.isdir(container_rootfs(name)):
            command_remove(SimpleNamespace(target=name, verbose=False))
