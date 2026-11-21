#
# Proot-Distro - manage proot containers.
#
# Tests for the first-run autohold sentinel: the entry is created through
# an O_NOFOLLOW walk + O_EXCL create, so a guest-planted symlink under
# the RUNTIME_DIR name cannot be written through, and a dangling link is
# treated as "already there" rather than as an invitation to truncate.
#

import os

import pytest

from proot_distro import autohold
from proot_distro import statedir as statedir_mod
from proot_distro.constants import RUNTIME_DIR


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(autohold, "RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(
        autohold, "_HOLD_SENTINEL", str(runtime / ".autohold-checked"))
    monkeypatch.setattr(autohold, "_HOLD_SENTINEL_NAME",
                       ".autohold-checked")
    # open_state_dir 只认状态树根；把临时 runtime 也标进去。
    monkeypatch.setattr(
        statedir_mod, "STATE_ROOTS",
        tuple(sorted({str(runtime), RUNTIME_DIR}, key=len)))
    # The check itself only matters on Termux; the sentinel writing is
    # the part under test here.
    monkeypatch.setattr(autohold, "IS_TERMUX", True)
    return runtime


def _disable_checks(monkeypatch):
    # Stop maybe_auto_hold before any subprocess: only the sentinel
    # write is under test.
    monkeypatch.setenv("PD_NO_AUTO_HOLD", "0")
    monkeypatch.setattr(autohold, "_run", lambda *a, **kw: None)


def test_mark_done_creates_the_sentinel():
    autohold._mark_done()
    sentinel = os.path.join(str(isolated_sentinel_dir()), ".autohold-checked")
    assert os.path.exists(sentinel)
    with open(sentinel, "rb") as fh:
        assert fh.read() == b"checked\n"


def isolated_sentinel_dir():
    return autohold.RUNTIME_DIR


def test_mark_done_is_idempotent():
    # 重复调用不报错，哨兵始终在；内容固定（重写无害）。
    autohold._mark_done()
    autohold._mark_done()
    path = os.path.join(str(isolated_sentinel_dir()), ".autohold-checked")
    with open(path, "rb") as fh:
        assert fh.read() == b"checked\n"
    assert autohold._sentinel_present() is True


def test_planted_symlink_is_not_written_through(tmp_path):
    victim = tmp_path / "host-file"
    victim.write_text("HOST DATA")
    planted = os.path.join(str(isolated_sentinel_dir()),
                           ".autohold-checked")
    os.symlink(victim, planted)
    autohold._mark_done()
    # 预埋的链接被换掉（目录项操作，open_new_at 的“先摘预占再 O_EXCL
    # 新建”），但绝没有被写穿：宿主文件完好。
    assert not os.path.islink(planted)
    assert os.path.exists(planted)
    assert victim.read_text() == "HOST DATA"


def test_dangling_sentinel_link_is_treated_as_present():
    planted = os.path.join(str(isolated_sentinel_dir()),
                           ".autohold-checked")
    os.symlink("/nonexistent/target", planted)
    assert autohold._sentinel_present() is True
    # 写入侧对悬空链接同样不写穿：摘条目、建常规文件。
    autohold._mark_done()
    assert not os.path.islink(planted)
    assert os.path.exists("/nonexistent/target") is False
