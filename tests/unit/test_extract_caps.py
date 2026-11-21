#
# Proot-Distro - manage proot containers.
#
# Tests for the extraction size caps (tar bombs): a member whose declared
# size exceeds MAX_EXTRACT_BYTES is refused before its data is written,
# and an archive with more than MAX_EXTRACT_MEMBERS members is refused at
# the count. Both surface as TarError, the class install already reports
# as "Failed to install".
#
# The caps are read from helpers.download at call time (inside
# _extract_stream), so the tests drive the real code path by lowering
# them for the duration of one call.
#

import io
import os
import tarfile

import pytest

from proot_distro.helpers import download as download_mod
from proot_distro.helpers import tar_extract
from proot_distro.helpers.download import (
    MAX_EXTRACT_BYTES, MAX_EXTRACT_MEMBERS,
)


@pytest.fixture
def root_fd(tmp_path):
    fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield fd
    finally:
        os.close(fd)


def _make_archive(path, members):
    """members: [(name, size, content)] -- content padded to size."""
    with tarfile.open(str(path), "w") as tf:
        for name, size, content in members:
            info = tarfile.TarInfo(name)
            info.size = size
            tf.addfile(info, io.BytesIO(content.ljust(size, b"\0")
                                         [:size]))



def test_a_declared_size_bomb_is_refused_before_writing(
        tmp_path, root_fd, monkeypatch):
    # One member declaring 4 KB of content with the cap lowered to
    # 1 KB: refused before the member's data is written.
    monkeypatch.setattr(download_mod, "MAX_EXTRACT_BYTES", 1024)
    archive = tmp_path / "bomb.tar"
    _make_archive(archive, [("bomb", 4096, b"data")])
    with pytest.raises(tarfile.TarError):
        tar_extract.extract_tar_to_rootfs(str(archive), root_fd)
    assert not (tmp_path / "bomb").exists()


def test_many_small_members_are_refused_at_the_count(
        tmp_path, root_fd, monkeypatch):
    monkeypatch.setattr(download_mod, "MAX_EXTRACT_MEMBERS", 2)
    archive = tmp_path / "swarm.tar"
    _make_archive(archive, [
        (f"f{i}", 1, b"x") for i in range(3)
    ])
    with pytest.raises(tarfile.TarError):
        tar_extract.extract_tar_to_rootfs(str(archive), root_fd)
    # 前两个在拒绝点之前已经落盘（逐成员拒绝，不是预扫描）。
    assert (tmp_path / "f0").exists()
    assert (tmp_path / "f1").exists()
    assert not (tmp_path / "f2").exists()


def test_a_small_archive_below_the_caps_still_extracts(
        tmp_path, root_fd, monkeypatch):
    monkeypatch.setattr(download_mod, "MAX_EXTRACT_BYTES", 1024)
    monkeypatch.setattr(download_mod, "MAX_EXTRACT_MEMBERS", 2)
    archive = tmp_path / "ok.tar"
    _make_archive(archive, [("only", 5, b"hello")])
    tar_extract.extract_tar_to_rootfs(str(archive), root_fd)
    assert (tmp_path / "only").read_bytes() == b"hello"


def test_shipped_caps_are_sane():
    # 262144 members / 32 GiB：合法 rootfs 展开几 GB、几万条目，
    # 留一个数量级余量；炸弹（声称或展开几百 GB / 百万小文件）
    # 在写盘阶段就被拦断。
    assert MAX_EXTRACT_MEMBERS == 262144
    assert MAX_EXTRACT_BYTES == 32 * 1024 ** 3
