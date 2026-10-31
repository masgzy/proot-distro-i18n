#
# Proot-Distro - manage proot containers.
#
# Tests for the `version` subcommand: the build-hash probe chain
# (PD_BUILD_HASH > git > unknown) and the rendered block.
#

import io
import subprocess
import sys
from unittest import mock

import pytest

from proot_distro import i18n
from proot_distro.commands import version as version_mod


@pytest.fixture(autouse=True)
def _restore_language():
    """用例可能切换 i18n 全局状态——结束后一律恢复。"""
    before = (i18n.get_language(), i18n.get_language_source())
    yield
    i18n.set_language(before[0], before[1])


# ---------------------------------------------------------------------------
# build_hash
# ---------------------------------------------------------------------------

def test_build_hash_env_stamp_wins(monkeypatch):
    monkeypatch.setenv("PD_BUILD_HASH", "deadbeef")
    assert version_mod.build_hash() == "deadbeef"


def test_build_hash_env_stamp_is_stripped(monkeypatch):
    monkeypatch.setenv("PD_BUILD_HASH", "  deadbeef\n")
    assert version_mod.build_hash() == "deadbeef"


def test_build_hash_git_probe(monkeypatch):
    monkeypatch.delenv("PD_BUILD_HASH", raising=False)
    # An editable/wheel build bakes a stamp file into the tree; the git
    # probe is only reachable without one, so hide it explicitly.
    monkeypatch.setattr(version_mod, "_read_stamped_hash", lambda: "")
    fake = mock.Mock(returncode=0, stdout="12345678\n")
    with mock.patch.object(subprocess, "run", return_value=fake) as run:
        assert version_mod.build_hash() == "12345678"
    assert run.call_args.args[0][:4] == ["git", "-C", "--short=8"][:3] or \
        run.call_args.args[0][0] == "git"


def test_build_hash_git_failure_falls_back_to_unknown(monkeypatch):
    monkeypatch.delenv("PD_BUILD_HASH", raising=False)
    monkeypatch.setattr(version_mod, "_read_stamped_hash", lambda: "")
    fake = mock.Mock(returncode=128, stdout="")
    with mock.patch.object(subprocess, "run", return_value=fake):
        assert version_mod.build_hash() == "unknown"


def test_build_hash_git_crash_falls_back_to_unknown(monkeypatch):
    monkeypatch.delenv("PD_BUILD_HASH", raising=False)
    monkeypatch.setattr(version_mod, "_read_stamped_hash", lambda: "")
    with mock.patch.object(subprocess, "run",
                           side_effect=OSError("no git here")):
        assert version_mod.build_hash() == "unknown"


# ---------------------------------------------------------------------------
# build_hash: the _build_hash.py stamp the PEP 517 backend bakes into
# wheels and sdists (pip installs used to report "unknown")
# ---------------------------------------------------------------------------

def test_build_hash_stamp_file_beats_git(monkeypatch):
    monkeypatch.delenv("PD_BUILD_HASH", raising=False)
    monkeypatch.setattr(version_mod, "_read_stamped_hash",
                        lambda: "cafebabe")
    # git would answer here, but the stamp is the truth for an
    # installed copy: it was baked while the sources still had .git.
    assert version_mod.build_hash() == "cafebabe"


def test_build_hash_env_still_beats_stamp_file(monkeypatch):
    monkeypatch.setenv("PD_BUILD_HASH", "deadbeef")
    monkeypatch.setattr(version_mod, "_read_stamped_hash",
                        lambda: "cafebabe")
    assert version_mod.build_hash() == "deadbeef"


def test_build_hash_stamp_literal_unknown_is_ignored(monkeypatch):
    # A backend that could not resolve anything stamps "unknown"; that
    # value must not shadow a working git probe in a checkout.
    monkeypatch.delenv("PD_BUILD_HASH", raising=False)
    monkeypatch.setattr(version_mod, "_read_stamped_hash", lambda: "unknown")
    fake = mock.Mock(returncode=0, stdout="12345678\n")
    with mock.patch.object(subprocess, "run", return_value=fake):
        assert version_mod.build_hash() == "12345678"



# ---------------------------------------------------------------------------
# command_version output
# ---------------------------------------------------------------------------

def _render(monkeypatch, proot_path, lang="en"):
    monkeypatch.setenv("PD_BUILD_HASH", "cafe1234")
    monkeypatch.setattr(version_mod, "find_proot_bin",
                        lambda: proot_path, raising=True)
    i18n.set_language(lang, "test")
    buf = io.StringIO()
    with mock.patch.object(sys, "stdout", buf):
        version_mod.command_version(None)
    return buf.getvalue()


def test_version_block_without_runtime(monkeypatch):
    out = _render(monkeypatch, None)
    assert "proot-distro-i18n" in out
    assert "(build cafe1234)" in out
    assert "not found" in out


def test_version_block_with_runtime(monkeypatch):
    out = _render(monkeypatch, "/opt/nproot")
    assert "Container runtime: /opt/nproot" in out
    assert "not found" not in out


def test_version_block_runtime_override_marked(monkeypatch):
    monkeypatch.setenv("PD_PROOT_BIN", "~/.local/bin/nproot")
    monkeypatch.setattr(version_mod.os.path, "expanduser",
                        lambda p: p.replace("~", "/home/u"), raising=True)
    out = _render(monkeypatch, "/opt/nproot")
    assert "PD_PROOT_BIN" in out
    assert "/home/u/.local/bin/nproot" in out


def test_version_block_localized(monkeypatch):
    out = _render(monkeypatch, "/opt/nproot", lang="zh_CN")
    assert "容器运行时：/opt/nproot" in out
    assert "构建 cafe1234" in out


def test_version_command_goes_to_stdout_not_stderr(monkeypatch, capsys):
    """msg() 写 stderr 会破坏 `pd version | grep build`；必须走 stdout。"""
    monkeypatch.setenv("PD_BUILD_HASH", "cafe1234")
    monkeypatch.setattr(version_mod, "find_proot_bin",
                        lambda: None, raising=True)
    version_mod.command_version(None)
    captured = capsys.readouterr()
    assert "build cafe1234" in captured.out
    assert captured.err == ""
