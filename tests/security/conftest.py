#
# Shared fixtures for the containment/security tests.
#

import pytest

from proot_distro.commands import login as _login


@pytest.fixture(autouse=True)
def _stand_in_runtime(monkeypatch):
    """A proot stand-in for every test that drives login to its exec.

    These tests are about which inode, rootfs, environment or argv a
    session ends up with -- not about the binary. The startup probe
    `arch.get_proot_bin()` performs, however, exits when the host has
    no nproot/proot installed, so CI (which builds one) was the only
    place the suite ran green. Patching the name *login itself calls*
    keeps every assertion intact while removing the ambient-install
    requirement; tests that probe arch's own resolution rules patch or
    call arch directly and are untouched by this.
    """
    monkeypatch.setattr(_login, "get_proot_bin", lambda: "/usr/bin/proot")
