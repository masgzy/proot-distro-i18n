# Unit tests for cli.ensure_proot_installed — the startup probe that
# accepts nproot (preferred) or the stock proot, or a PD_PROOT_BIN
# override, and refuses when none is usable.

import pytest

from proot_distro import arch
from proot_distro import cli


def _make_exec(tmp_path, name):
    exe = tmp_path / name
    exe.write_text("#!/bin/sh\n")
    exe.chmod(exe.stat().st_mode | 0o111)
    return exe


@pytest.fixture(autouse=True)
def _hermetic_probe_dirs(tmp_path, monkeypatch):
    """Point the explicit probe dirs at scratch space, so the host's
    real /usr/bin or Termux prefix cannot leak into expectations."""
    sysbin = tmp_path / "sysbin"
    userbin = tmp_path / "userbin"
    sysbin.mkdir()
    userbin.mkdir()
    monkeypatch.setattr(arch, "SYSTEM_BIN_DIRS", (str(sysbin),))
    monkeypatch.setattr(arch, "USER_BIN_DIR", str(userbin))


def test_probe_passes_with_nproot_alone(tmp_path, monkeypatch):
    # The no-ptrace fork alone is enough; no exit, no install offer.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_exec(bin_dir, "nproot")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("PD_PROOT_BIN", raising=False)

    cli.ensure_proot_installed()  # must return normally


def test_probe_passes_with_proot_alone(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_exec(bin_dir, "proot")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("PD_PROOT_BIN", raising=False)

    cli.ensure_proot_installed()


def test_probe_passes_with_pd_override_only(tmp_path, monkeypatch):
    # A valid PD_PROOT_BIN short-circuits the PATH probe entirely: the
    # core may live anywhere, under any name.
    exe = _make_exec(tmp_path, "my-proot")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("PD_PROOT_BIN", str(exe))

    cli.ensure_proot_installed()


def test_probe_exits_without_either(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("PD_PROOT_BIN", raising=False)

    with pytest.raises(SystemExit) as exc:
        cli.ensure_proot_installed()
    assert exc.value.code == 1
    assert "nproot or proot" in capsys.readouterr().err
