#
# Proot-Distro - manage proot containers.
#
# Tests for distro_mirror: the per-family rewrites, the os-release
# detection, and the switch/revert round trip over a fake rootfs.
#

import json
import os

import pytest

from proot_distro import distro_mirror as dm


# ---------------------------------------------------------------------------
# rewrite rules (pure functions)
# ---------------------------------------------------------------------------

def test_apt_domains_move_to_aliyun_paths_intact():
    text = (
        "deb http://deb.debian.org/debian trixie main\n"
        "deb https://deb.debian.org/debian-security trixie-security main\n"
        "deb http://ports.ubuntu.com/ubuntu-ports noble main\n"
    )
    out = dm._rewrite_for_family("apt", text)
    assert "https://mirrors.aliyun.com/debian trixie" in out
    assert "https://mirrors.aliyun.com/debian-security" in out
    assert "https://mirrors.aliyun.com/ubuntu-ports" in out
    # the suites and components were not touched
    assert "trixie main" in out and "noble main" in out


def test_apt_no_known_domain_means_no_change():
    text = "deb https://my.lan/debian trixie main\n"
    assert dm._rewrite_for_family("apt", text) is None


def test_alpine_repositories():
    text = ("https://dl-cdn.alpinelinux.org/alpine/v3.24/main\n"
            "https://dl-cdn.alpinelinux.org/alpine/v3.24/community\n")
    out = dm._rewrite_for_family("alpine", text)
    assert out.count("https://mirrors.aliyun.com/alpine/v3.24/") == 2


def test_arch_mirrorlist_is_rewritten():
    out = dm._rewrite_for_family("arch", "Server = https://geo.example/$repo/os/$arch\n")
    assert "Server = https://mirrors.aliyun.com/archlinux/$repo/os/$arch" in out


def test_manjaro_uses_the_stable_branch():
    out = dm._rewrite_for_family("manjaro", "# anything\n")
    assert "mirrors.aliyun.com/manjaro/stable/$repo/os/$arch" in out


def test_rocky_mirrorlist_commented_and_baseurl_enabled():
    text = (
        "[baseos]\n"
        "mirrorlist=https://mirrors.rockylinux.org/mirrorlist?arch=$basearch\n"
        "# baseurl=http://dl.rockylinux.org/$contentdir/$releasever/BaseOS/$basearch/os/\n"
        "gpgkey=https://dl.rockylinux.org/$contentdir/RPM-GPG-KEY-Rocky-9\n"
    )
    out = dm._rewrite_for_family("rocky", text)
    assert "# mirrorlist=https://mirrors.rockylinux.org/" in out
    assert "baseurl=https://mirrors.aliyun.com/rockylinux/$releasever/BaseOS/" in out
    # $contentdir maps onto the mirror root
    assert "gpgkey=https://mirrors.aliyun.com/rockylinux/RPM-GPG-KEY-Rocky-9" in out
    # the commented mirrorlist survives for the revert story
    assert "mirrors.rockylinux.org" in out


def test_almalinux_repo_uses_aliyun():
    text = ("mirrorlist=https://mirrors.almalinux.org/mirrorlist/9/baseos\n"
            "# baseurl=https://repo.almalinux.org/almalinux/$releasever/BaseOS/$basearch/os/\n")
    out = dm._rewrite_for_family("almalinux", text)
    assert "https://mirrors.aliyun.com/almalinux/$releasever/BaseOS/" in out


def test_fedora_repo_is_rewritten_around_aliyun():
    text = ("[fedora]\n"
            "metalink=https://mirrors.fedoraproject.org/metalink?repo=fedora-$releasever\n")
    out = dm._rewrite_for_family("fedora", text)
    assert "baseurl=https://mirrors.aliyun.com/fedora/releases/$releasever/" in out
    assert "gpgcheck=1" in out


def test_fedora_updates_variant():
    text = "[updates]\nmetalink=https://mirrors.fedoraproject.org/metalink?repo=updates-released-f$releasever\n"
    out = dm._rewrite_for_family("fedora", text)
    assert "fedora/updates/$releasever" in out


def test_suse_and_void_domain_rules():
    out = dm._rewrite_for_family(
        "suse", "https://download.opensuse.org/tumbleweed/repo/oss/\n")
    assert "https://mirrors.aliyun.com/opensuse/tumbleweed/repo/oss/" in out
    out = dm._rewrite_for_family(
        "void", "repository=https://repo-default.voidlinux.org/current/aarch64\n")
    assert "https://mirrors.tuna.tsinghua.edu.cn/voidlinux/current/aarch64" in out


def test_gentoo_make_conf():
    text = 'MAKEOPTS="-j4"\n'
    out = dm._rewrite_for_family("gentoo", text)
    assert 'GENTOO_MIRRORS="https://mirrors.tuna.tsinghua.edu.cn/gentoo' in out
    text = 'GENTOO_MIRRORS="https://distfiles.gentoo.org"\n'
    out = dm._rewrite_for_family("gentoo", text)
    assert "distfiles.gentoo.org" not in out


# ---------------------------------------------------------------------------
# os-release detection
# ---------------------------------------------------------------------------

def _etc_with_osrelease(tmp_path, body):
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "os-release").write_text(body)
    return os.open(str(etc), os.O_RDONLY | os.O_DIRECTORY)


def test_detect_family_reads_id(tmp_path):
    fd = _etc_with_osrelease(tmp_path, 'ID=debian\nVERSION_ID=13\n')
    try:
        assert dm.detect_family(fd) == "apt"
    finally:
        os.close(fd)


def test_detect_family_falls_back_to_id_like(tmp_path):
    fd = _etc_with_osrelease(tmp_path, 'ID=whatever\nID_LIKE="debian"\n')
    try:
        assert dm.detect_family(fd) == "apt"
    finally:
        os.close(fd)


def test_detect_family_unknown_id_is_none(tmp_path):
    fd = _etc_with_osrelease(tmp_path, 'ID=plan9\n')
    try:
        assert dm.detect_family(fd) is None
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# switch / revert round trip
# ---------------------------------------------------------------------------

@pytest.fixture
def debian_rootfs(tmp_path):
    rootfs = tmp_path / "rootfs"
    (rootfs / "etc/apt/sources.list.d").mkdir(parents=True)
    (rootfs / "etc/os-release").write_text("ID=debian\n")
    (rootfs / "etc/apt/sources.list").write_text(
        "deb http://deb.debian.org/debian trixie main\n")
    (rootfs / "etc/apt/sources.list.d/bookworm.sources").write_text(
        "Types: deb\nURIs: http://deb.debian.org/debian\nSuites: trixie\n")
    return rootfs


def test_switch_rewrites_and_backs_up(debian_rootfs):
    assert dm.switch_distro_mirror(str(debian_rootfs), "box") is True
    listing = (debian_rootfs / "etc/apt/sources.list").read_text()
    assert "https://mirrors.aliyun.com/debian trixie" in listing
    bak = debian_rootfs / "etc/apt/sources.list.pd.bak"
    assert bak.exists()
    assert "http://deb.debian.org/debian trixie" in bak.read_text()
    sources = (debian_rootfs / "etc/apt/sources.list.d/bookworm.sources")
    assert "https://mirrors.aliyun.com/debian" in sources.read_text()
    assert (debian_rootfs / "etc/apt/sources.list.d/bookworm.sources.pd.bak").exists()


def test_second_switch_keeps_the_first_backup(debian_rootfs):
    assert dm.switch_distro_mirror(str(debian_rootfs), "box") is True
    bak = debian_rootfs / "etc/apt/sources.list.pd.bak"
    first = bak.read_text()
    assert dm.switch_distro_mirror(str(debian_rootfs), "box") is False
    # the backup still holds the original, not the switched copy
    assert bak.read_text() == first


def test_revert_restores_the_original(debian_rootfs):
    original = (debian_rootfs / "etc/apt/sources.list").read_text()
    assert dm.switch_distro_mirror(str(debian_rootfs), "box") is True
    assert dm.revert_distro_mirror(str(debian_rootfs), "box") is True
    assert (debian_rootfs / "etc/apt/sources.list").read_text() == original
    assert not (debian_rootfs / "etc/apt/sources.list.pd.bak").exists()


def test_switch_refuses_a_symlinked_source_file(tmp_path):
    # A guest root can point sources.list at a host file; the switch
    # must not write through it.
    rootfs = tmp_path / "rootfs"
    (rootfs / "etc/apt").mkdir(parents=True)
    (rootfs / "etc/os-release").write_text("ID=debian\n")
    target = rootfs / "host-file"
    target.write_text("deb http://deb.debian.org/debian trixie main\n")
    sources = rootfs / "etc/apt/sources.list"
    sources.symlink_to(target)
    assert dm.switch_distro_mirror(str(rootfs), "box") is False
    # the link and its target are untouched
    assert sources.is_symlink()
    assert "http://deb.debian.org" in target.read_text()


def test_switch_on_an_unknown_distro_is_a_noop(tmp_path):
    rootfs = tmp_path / "rootfs"
    (rootfs / "etc").mkdir(parents=True)
    (rootfs / "etc/os-release").write_text("ID=plan9\n")
    assert dm.switch_distro_mirror(str(rootfs), "box") is False


def test_switch_without_etc_is_a_noop(tmp_path):
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    assert dm.switch_distro_mirror(str(rootfs), "box") is False


def test_switch_is_idempotent_when_already_on_aliyun(debian_rootfs):
    assert dm.switch_distro_mirror(str(debian_rootfs), "box") is True
    # switched sources contain no known upstream domain any more
    assert dm.switch_distro_mirror(str(debian_rootfs), "box") is False


# ---------------------------------------------------------------------------
# the setting drives install -- wiring check, not a real install
# ---------------------------------------------------------------------------

def test_distro_mirror_setting_defaults_off(isolated_config=None):
    from proot_distro import config as config_mod
    from proot_distro import settings as settings_mod
    assert settings_mod.distro_mirror_enabled() is False
