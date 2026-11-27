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

def test_apt_domains_move_to_mirror_paths_intact():
    text = (
        "deb http://deb.debian.org/debian trixie main\n"
        "deb https://deb.debian.org/debian-security trixie-security main\n"
        "deb http://ports.ubuntu.com/ubuntu-ports noble main\n"
    )
    # 默认提供方是清华 TUNA。
    out = dm._rewrite_for_family("apt", text)
    assert "https://mirrors.tuna.tsinghua.edu.cn/debian trixie" in out
    assert "https://mirrors.tuna.tsinghua.edu.cn/debian-security" in out
    assert "https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports" in out
    # the suites and components were not touched
    assert "trixie main" in out and "noble main" in out


def test_apt_domains_honor_the_chosen_provider():
    # --mirror aliyun：换家不换路径，suite/components 原样。
    text = "deb http://deb.debian.org/debian trixie main\n"
    out = dm._rewrite_for_family("apt", text, provider="aliyun")
    assert "https://mirrors.aliyun.com/debian trixie" in out
    out = dm._rewrite_for_family("apt", text, provider="ustc")
    assert "https://mirrors.ustc.edu.cn/debian trixie" in out


def test_apt_no_known_domain_means_no_change():
    text = "deb https://my.lan/debian trixie main\n"
    assert dm._rewrite_for_family("apt", text) is None


def test_alpine_repositories():
    text = ("https://dl-cdn.alpinelinux.org/alpine/v3.24/main\n"
            "https://dl-cdn.alpinelinux.org/alpine/v3.24/community\n")
    out = dm._rewrite_for_family("alpine", text)
    assert out.count("https://mirrors.tuna.tsinghua.edu.cn/alpine/v3.24/") == 2


def test_arch_mirrorlist_is_rewritten():
    out = dm._rewrite_for_family("arch", "Server = https://geo.example/$repo/os/$arch\n")
    assert "Server = https://mirrors.tuna.tsinghua.edu.cn/archlinux/$repo/os/$arch" in out


def test_manjaro_uses_the_stable_branch():
    out = dm._rewrite_for_family("manjaro", "# anything\n")
    # Manjaro 的仓库布局是 <mirror>/manjaro/<branch>/$repo/$arch——
    # 没有 /os/（那是 Arch 的层级；照抄它切完后 pacman 全部 404）。
    assert "mirrors.tuna.tsinghua.edu.cn/manjaro/stable/$repo/$arch" in out
    assert "/os/$arch" not in out


def test_rocky_mirrorlist_commented_and_baseurl_enabled():
    text = (
        "[baseos]\n"
        "mirrorlist=https://mirrors.rockylinux.org/mirrorlist?arch=$basearch\n"
        "# baseurl=http://dl.rockylinux.org/$contentdir/$releasever/BaseOS/$basearch/os/\n"
        "gpgkey=https://dl.rockylinux.org/$contentdir/RPM-GPG-KEY-Rocky-9\n"
    )
    # 默认清华不镜像 rocky：回退链落到华为云。
    out = dm._rewrite_for_family("rocky", text)
    assert "# mirrorlist=https://mirrors.rockylinux.org/" in out
    assert "baseurl=https://repo.huaweicloud.com/rockylinux/$releasever/BaseOS/" in out
    # $contentdir maps onto the mirror root
    assert "gpgkey=https://repo.huaweicloud.com/rockylinux/RPM-GPG-KEY-Rocky-9" in out
    # the commented mirrorlist survives for the revert story
    assert "mirrors.rockylinux.org" in out


def test_rocky_honors_an_explicit_provider_that_carries_it():
    text = "# baseurl=http://dl.rockylinux.org/$contentdir/$releasever/BaseOS/$basearch/os/\n"
    out = dm._rewrite_for_family("rocky", text, provider="aliyun")
    assert "baseurl=https://mirrors.aliyun.com/rockylinux/$releasever/BaseOS/" in out


def test_almalinux_repo_default_falls_back():
    text = ("mirrorlist=https://mirrors.almalinux.org/mirrorlist/9/baseos\n"
            "# baseurl=https://repo.almalinux.org/almalinux/$releasever/BaseOS/$basearch/os/\n")
    # 默认清华不镜像 almalinux：回退链的华为云接手。
    out = dm._rewrite_for_family("almalinux", text)
    assert "https://repo.huaweicloud.com/almalinux/$releasever/BaseOS/" in out


def test_fedora_repo_is_rewritten_around_tuna():
    text = ("[fedora]\n"
            "metalink=https://mirrors.fedoraproject.org/metalink?repo=fedora-$releasever\n")
    out = dm._rewrite_for_family("fedora", text)
    assert "baseurl=https://mirrors.tuna.tsinghua.edu.cn/fedora/releases/$releasever/" in out
    assert "gpgcheck=1" in out


def test_fedora_updates_variant():
    # 旧版 fedora-updates.repo 的 repo id 是 [updates]，新版是
    # [fedora-updates]——两个都得认。
    text = "[updates]\nmetalink=https://mirrors.fedoraproject.org/metalink?repo=updates-released-f$releasever\n"
    out = dm._rewrite_for_family("fedora", text)
    assert "fedora/updates/$releasever" in out


def test_fedora_new_style_updates_id():
    text = ("[fedora-updates]\n"
            "metalink=https://mirrors.fedoraproject.org/metalink?"
            "repo=updates-released-f$releasever\n")
    out = dm._rewrite_for_family("fedora", text)
    assert "fedora/updates/$releasever" in out
    assert "[fedora-updates]" in out


def test_fedora_leaves_unknown_repo_files_alone():
    # h264/modular/用户自建 repo 不在本程序能安全合成的范围里：
    # 曾经“含 fedoraproject.org 就整文件採平成 [mirror-cn]”，现在不动。
    text = ("[fedora-cisco-openh264]\n"
            "metalink=https://mirrors.fedoraproject.org/metalink?"
            "repo=openh264-$releasever\n")
    assert dm._rewrite_for_family("fedora", text) is None


def test_suse_and_void_domain_rules():
    out = dm._rewrite_for_family(
        "suse", "https://download.opensuse.org/tumbleweed/repo/oss/\n")
    assert "https://mirrors.tuna.tsinghua.edu.cn/opensuse/tumbleweed/repo/oss/" in out
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


def test_provider_registry_and_fallbacks():
    # 8 家提供方全部注册，默认清华。
    ids = dm.mirror_provider_ids()
    assert len(ids) == 8
    assert dm.DEFAULT_MIRROR_PROVIDER == "tuna"
    assert dm.mirror_provider_host("tuna") == "mirrors.tuna.tsinghua.edu.cn"
    # 回退链：清华不镜像 rocky 时落到华为云。
    chosen, fell_back = dm.resolve_provider_for_family(None, "rocky")
    assert chosen == "huawei" and fell_back is True
    chosen, fell_back = dm.resolve_provider_for_family(None, "apt")
    assert chosen == "tuna" and fell_back is False
    # 选了不镜像该家族的站也走回退链。
    chosen, fell_back = dm.resolve_provider_for_family("bfsu", "manjaro")
    assert fell_back is True
    assert dm.mirror_provider_supports("bfsu", "manjaro") is False
    assert dm.mirror_provider_label("tuna") == "清华大学（TUNA）"


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
    assert "https://mirrors.tuna.tsinghua.edu.cn/debian trixie" in listing
    bak = debian_rootfs / "etc/apt/sources.list.pd.bak"
    assert bak.exists()
    assert "http://deb.debian.org/debian trixie" in bak.read_text()
    sources = (debian_rootfs / "etc/apt/sources.list.d/bookworm.sources")
    assert "https://mirrors.tuna.tsinghua.edu.cn/debian" in sources.read_text()
    assert (debian_rootfs / "etc/apt/sources.list.d/bookworm.sources.pd.bak").exists()


def test_second_switch_keeps_the_first_backup(debian_rootfs):
    assert dm.switch_distro_mirror(str(debian_rootfs), "box") is True
    bak = debian_rootfs / "etc/apt/sources.list.pd.bak"
    first = bak.read_text()
    # 重复切换是幂等成功（脚本化重跑不必因“已是国内镜像”而非零退出），
    # 且没有新文件被改写。
    assert dm.switch_distro_mirror(str(debian_rootfs), "box") is True
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
    # 符号链接被跳过、没有任何文件被改写：幂等意义上的“无事可做”
    # 也是成功退出（曾经是失败码），但链接本身绝不能被写穿。
    assert dm.switch_distro_mirror(str(rootfs), "box") is True
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


def test_switch_honors_provider_argument(debian_rootfs):
    # switch_distro_mirror(provider=...) 直通 _rewrite_for_family。
    assert dm.switch_distro_mirror(
        str(debian_rootfs), "box", provider="aliyun") is True
    assert "https://mirrors.aliyun.com/debian trixie" in (
        debian_rootfs / "etc/apt/sources.list").read_text()


def test_switch_is_idempotent_when_already_on_mirror(debian_rootfs):
    assert dm.switch_distro_mirror(str(debian_rootfs), "box") is True
    # switched sources contain no known upstream domain any more —
    # 重复执行是幂等成功，不是失败。
    assert dm.switch_distro_mirror(str(debian_rootfs), "box") is True
    assert "mirrors.tuna.tsinghua.edu.cn" in (
        debian_rootfs / "etc/apt/sources.list").read_text()


# ---------------------------------------------------------------------------
# the setting drives install -- wiring check, not a real install
# ---------------------------------------------------------------------------

def test_distro_mirror_setting_defaults_off(isolated_config=None):
    from proot_distro import config as config_mod
    from proot_distro import settings as settings_mod
    assert settings_mod.distro_mirror_enabled() is False


# ---------------------------------------------------------------------------
# alpine end-to-end discovery (the /etc/apk/repositories fix)
# ---------------------------------------------------------------------------

def test_alpine_repositories_are_discovered_and_switched(tmp_path):
    # 曾经 family=="alpine" 扫的是 etc 自己，而 _switchable_name 只在
    # prefix "apk" 下认 "repositories"——Alpine 的切换是彻底的空转。
    rootfs = tmp_path / "rootfs"
    (rootfs / "etc/apk").mkdir(parents=True)
    (rootfs / "usr/lib").mkdir(parents=True)
    (rootfs / "etc/os-release").write_text("ID=alpine\n")
    (rootfs / "etc/apk/repositories").write_text(
        "https://dl-cdn.alpinelinux.org/alpine/v3.20/main\n"
        "https://dl-cdn.alpinelinux.org/alpine/v3.20/community\n")
    assert dm.switch_distro_mirror(str(rootfs), "alp") is True
    text = (rootfs / "etc/apk/repositories").read_text()
    assert text.count("https://mirrors.tuna.tsinghua.edu.cn/alpine/v3.20/") == 2
    assert (rootfs / "etc/apk/repositories.pd.bak").exists()


def test_osrelease_fallback_uses_usr_lib_under_the_rootfs_fd(tmp_path):
    # /usr/lib/os-release 兜底不再走带 ".." 的 openat 路径（中间组件
    # 可被客人种链接穿过）：逐层下钻 ("usr","lib")。
    rootfs = tmp_path / "rootfs"
    (rootfs / "etc").mkdir(parents=True)
    (rootfs / "usr/lib").mkdir(parents=True)
    (rootfs / "usr/lib/os-release").write_text("ID=ubuntu\n")
    import os as _os
    fd = _os.open(str(rootfs), _os.O_RDONLY | _os.O_DIRECTORY)
    try:
        etc_fd = dm.dirfd.opendir_at(fd, "etc")
        try:
            assert dm.detect_family(etc_fd, fd) == "apt"
        finally:
            _os.close(etc_fd)
    finally:
        _os.close(fd)


def test_two_files_in_one_directory_do_not_ebadf(tmp_path):
    # rows 曾按“文件”持有共享的目录 fd、每文件关一次：第二个
    # os.close(fd) 是 EBADF，把整次成功切换拖成失败退出码。
    rootfs = tmp_path / "rootfs"
    (rootfs / "etc/apt/sources.list.d").mkdir(parents=True)
    (rootfs / "etc/os-release").write_text("ID=ubuntu\n")
    (rootfs / "etc/apt/sources.list.d/a.list").write_text(
        "deb http://archive.ubuntu.com/ubuntu noble universe\n")
    (rootfs / "etc/apt/sources.list.d/b.list").write_text(
        "deb http://security.ubuntu.com/ubuntu noble-security main\n")
    assert dm.switch_distro_mirror(str(rootfs), "ub") is True
    assert "tuna.tsinghua.edu.cn" in (rootfs / "etc/apt/sources.list.d/a.list").read_text()
    assert "tuna.tsinghua.edu.cn" in (rootfs / "etc/apt/sources.list.d/b.list").read_text()


def test_domain_replacement_respects_boundaries():
    # 子串替换会把 deb.debian.org.attacker.cn “成功”换成
    # mirrors.tuna...attacker.cn——边界必须锁死。
    rules = dm._pair_rules(
        dm._FAMILY_DOMAINS["apt"],
        "https://mirrors.tuna.tsinghua.edu.cn")
    text = ("deb http://deb.debian.org.attacker.cn/ bookworm main\n"
            "deb http://deb.debian.org/debian bookworm main\n")
    out = dm._replace_domains(text, rules)
    assert "deb.debian.org.attacker.cn" in out       # 恶意后缀域名原样保留
    assert "mirrors.tuna.tsinghua.edu.cn.attacker.cn" not in out
    assert "https://mirrors.tuna.tsinghua.edu.cn/debian bookworm" in out
