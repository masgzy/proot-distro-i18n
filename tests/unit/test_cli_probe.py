# Unit tests for cli.ensure_proot_installed — the startup probe that
# accepts nproot (preferred) or the stock proot, or a PD_PROOT_BIN
# override, and refuses when none is usable.

import pytest

from proot_distro import cli


def _make_exec(tmp_path, name):
    exe = tmp_path / name
    exe.write_text("#!/bin/sh\n")
    exe.chmod(exe.stat().st_mode | 0o111)
    return exe


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
