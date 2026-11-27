#
# Proot-Distro - manage proot containers.
#
# Created for the proot-distro-i18n fork.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
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

"""容器内软件源一键切换到国内镜像（Aliyun / 清华 TUNA）。

一键切换，两条入口
==================

1. ``pd setting put distro_mirror true`` 之后，每次 ``install`` 解包
   完成即自动切换（install.py 调 :func:`switch_distro_mirror`）；
2. ``pd mirror distro <容器>`` 手动切换已安装的容器，
   ``pd mirror distro <容器> --revert`` 恢复原始源。

工作方式
========

全程在 host 侧直接改容器 rootfs 里的文本配置文件，不进容器、不跑
包管理器，所以切换对 debian/alpine/arch/fedora/... 都一样快，也不
依赖容器内有没有 python/shell。

每个发行版家族一个适配器（apt 的域名替换、pacman 的 mirrorlist
重写、dnf/yum 的 mirrorlist 注释 + baseurl 启用、zypp 的域替换、
xbps 的域替换、portage 的 GENTOO_MIRRORS 改写），识别靠 rootfs 里
的 ``/etc/os-release``（ID，必要时 ID_LIKE 兜底）。

镜像源选择（2026-11 校准，均为同构路径映射，改动最小）：

* Aliyun（阿里云）：debian / ubuntu（含 ubuntu-ports）/ kali /
  alpine / fedora / arch / opensuse / rocky / alma；
* TUNA（清华大学）：void / gentoo（阿里云无这两个发行版的镜像）。

安全性
======

容器里的 root 用户可以把 ``/etc/apt/sources.list`` 换成指向 host
的符号链接，proot 不拦——所以这里的每个文件读写都走 dirfd.py 的
openat 通道：

* 目标一律 lstat + O_NOFOLLOW：常规文件才读写，符号链接、FIFO、
  设备文件直接跳过并告警；
* 原内容先备份到 ``<名字>.pd.bak``（手工 O_EXCL：已存在的备份是
  更早的原版，永不覆盖）；新内容写临时名再 rename 到位——rename
  替换的只是目录项本身，不会追进符号链接；
* ``--revert`` 把 ``.pd.bak`` rename 回原名，同样是目录项操作。

备份文件是恢复的唯一凭据，容器卸载（``pd remove``）随 rootfs 一起
消失；一次切换里改过的每个文件各有一份自己的备份。
"""

from __future__ import annotations

import os
import re
import stat

from proot_distro import dirfd
from proot_distro.i18n import _
from proot_distro.message import log_error, log_info, msg, warn

__all__ = (
    "DEFAULT_MIRROR_PROVIDER", "SUPPORTED_DISTROS",
    "mirror_provider_host", "mirror_provider_ids",
    "mirror_provider_label", "mirror_provider_supports",
    "resolve_provider_for_family", "revert_distro_mirror",
    "switch_distro_mirror",
)
_BAK_SUFFIX = ".pd.bak"
_TMP_SUFFIX = ".pd.tmp"

# 写入临时名的唯一后缀：同目录里可能同时跑两个 pd（或客人在 readdir
# 里盯着固定名字使坏），固定名会互相覆盖/被指哪换哪。进程号 + 随机
# 字节让名字不可预测，发布前再比对目录项与写入 fd 的 inode。
_UNIQUE_TMP = ".{pid}.{rnd}"

# ---------------------------------------------------------------------------
# 镜像源提供方：8 家站点，一个注册表。
#
# 所有站点均于 2026-11-21 逐目录 HEAD 探测核对（debian / debian-security
# / ubuntu / ubuntu-ports / kali / alpine / archlinux / manjaro / fedora
# / rockylinux / almalinux / opensuse / voidlinux / gentoo），布局一致的
# 才进表；个别站点缺哪个家族，表里就是 None，切换时会落到别家并说明。
#
# CERNET（mirrors.cernet.edu.cn）是教育网联合镜像站（MirrorZ）：按
# 请求来源网络 302 就近调度到成员站点，路径布局与各成员一致，apt/apk/
# dnf/pacman 跟随重定向没有额外代价。
#
# ---------------------------------------------------------------------------

# id -> (中文显示名, 主机名)。默认清华（用户指定 distro 默认清华源）。
_MIRROR_HOSTS = {
    "tuna":    ("清华大学（TUNA）", "mirrors.tuna.tsinghua.edu.cn"),
    "bfsu":    ("北京外国语大学", "mirrors.bfsu.edu.cn"),
    "nju":     ("南京大学", "mirrors.nju.edu.cn"),
    "ustc":    ("中国科学技术大学", "mirrors.ustc.edu.cn"),
    "cernet":  ("教育网联合镜像站（CERNET，就近调度）",
                "mirrors.cernet.edu.cn"),
    "aliyun":  ("阿里云", "mirrors.aliyun.com"),
    "huawei":  ("华为云", "repo.huaweicloud.com"),
    "tencent": ("腾讯云", "mirrors.cloud.tencent.com"),
}

DEFAULT_MIRROR_PROVIDER = "tuna"

# 家族 -> 每个提供方上的 URL 前缀（"" = 与官方路径同构，直接换主机名；
# None = 该站不镜像此家族）。apt 一栏覆盖 debian/ubuntu/kali 的全部
# 官方域名（含 ubuntu-ports、debian-security）。
_PROVIDER_FAMILIES = {
    "tuna": {
        "apt": "", "alpine": "", "suse": "/opensuse", "arch": "/archlinux",
        "manjaro": "/manjaro", "fedora": "", "rocky": None,
        "almalinux": None, "void": "/voidlinux", "gentoo": "/gentoo",
    },
    "bfsu": {
        "apt": "", "alpine": "", "suse": "/opensuse", "arch": "/archlinux",
        "manjaro": None, "fedora": "", "rocky": None,
        "almalinux": None, "void": "/voidlinux", "gentoo": "/gentoo",
    },
    "nju": {
        "apt": "", "alpine": "", "suse": "/opensuse", "arch": "/archlinux",
        "manjaro": "/manjaro", "fedora": "", "rocky": None,
        "almalinux": "/almalinux", "void": "/voidlinux", "gentoo": "/gentoo",
    },
    "ustc": {
        "apt": "", "alpine": "", "suse": "/opensuse", "arch": "/archlinux",
        "manjaro": "/manjaro", "fedora": "", "rocky": None,
        "almalinux": None, "void": None, "gentoo": "/gentoo",
    },
    "cernet": {
        "apt": "", "alpine": "", "suse": "/opensuse", "arch": "/archlinux",
        "manjaro": "/manjaro", "fedora": "", "rocky": "/rockylinux",
        "almalinux": "/almalinux", "void": "/voidlinux", "gentoo": "/gentoo",
    },
    "aliyun": {
        "apt": "", "alpine": "", "suse": "/opensuse", "arch": "/archlinux",
        "manjaro": "/manjaro", "fedora": "", "rocky": "/rockylinux",
        "almalinux": "/almalinux", "void": None, "gentoo": None,
    },
    "huawei": {
        "apt": "", "alpine": "", "suse": "/opensuse", "arch": "/archlinux",
        "manjaro": "/manjaro", "fedora": "", "rocky": "/rockylinux",
        "almalinux": "/almalinux", "void": "/voidlinux", "gentoo": "/gentoo",
    },
    "tencent": {
        "apt": "", "alpine": "", "suse": "/opensuse", "arch": "/archlinux",
        "manjaro": "/manjaro", "fedora": "", "rocky": None,
        "almalinux": "/almalinux", "void": None, "gentoo": "/gentoo",
    },
}

# 请求的提供方不镜像某家族时的回退链：默认（清华）→ 华为云（实测唯一
# 8 家全覆盖的商业站）→ CERNET（教育网聚合，同样全覆盖）。每条家族都
# 在链上找得到归宿，切换永不因“选了北外装 Manjaro”而无源可用。
_PROVIDER_FALLBACKS = ("huawei", "cernet")

_MIRROR_PROVIDER_IDS = tuple(_MIRROR_HOSTS)


def mirror_provider_ids() -> tuple:
    """可选择的镜像提供方 id（展示顺序即此顺序，默认清华排首位）。"""
    return _MIRROR_PROVIDER_IDS


def mirror_provider_label(provider_id: str) -> str:
    """提供方的中文显示名；未知 id 返回中性占位。"""
    entry = _MIRROR_HOSTS.get(provider_id)
    return entry[0] if entry else provider_id


def mirror_provider_host(provider_id: str) -> "str | None":
    """提供方主机名；未知 id 返回 None。"""
    entry = _MIRROR_HOSTS.get(provider_id)
    return entry[1] if entry else None


def mirror_provider_supports(provider_id: str, family: str) -> bool:
    """该提供方是否镜像这个家族（apt/alpine/arch/...）。"""
    families = _PROVIDER_FAMILIES.get(provider_id)
    return bool(families) and families.get(family) is not None


def _family_prefix(provider_id: str, family: str) -> str:
    """该提供方上此家族的 URL 前缀；不镜像时为 None。"""
    families = _PROVIDER_FAMILIES.get(provider_id)
    if not families:
        return None
    return families.get(family)


def resolve_provider_for_family(provider_id: "str | None", family: str):
    """为 *family* 选出实际使用的提供方。

    返回 ``(provider_id, fell_back)``。*provider_id* 为 None 时取默认
    （清华）。所请求的站点不镜像该家族时按 ``_PROVIDER_FALLBACKS``
    落到别家（fell_back=True，调用方负责把“选了 A、用了 B”讲清楚）；
    链上全都没有（不可能发生——每条家族都被至少两家覆盖）时退回默认
    提供方本身。
    """
    requested = provider_id or DEFAULT_MIRROR_PROVIDER
    if mirror_provider_supports(requested, family):
        return requested, False
    for candidate in (DEFAULT_MIRROR_PROVIDER,) + _PROVIDER_FALLBACKS:
        if candidate != requested and \
                mirror_provider_supports(candidate, family):
            return candidate, True
    return DEFAULT_MIRROR_PROVIDER, True


def _pair_rules(domains, mirror_base):
    """http(s)://<每个官方域名> -> <mirror_base> 的替换对。"""
    rules = []
    for domain in domains:
        rules.append((f"http://{domain}", mirror_base))
        rules.append((f"https://{domain}", mirror_base))
    return rules


_APT_DOMAINS = [
    "deb.debian.org", "cdn-aws.deb.debian.org", "security.debian.org",
    "archive.ubuntu.com", "security.ubuntu.com", "ports.ubuntu.com",
    "http.kali.org",
]

_ALPINE_DOMAINS = ["dl-cdn.alpinelinux.org"]
_SUSE_DOMAINS = ["download.opensuse.org"]
_VOID_DOMAINS = [
    "repo-default.voidlinux.org", "repo.voidlinux.org",
    "alpha.de.repo.voidlinux.org",
]

# 家族的官方域名清单（域替换型家族）。
_FAMILY_DOMAINS = {
    "apt": _APT_DOMAINS,
    "alpine": _ALPINE_DOMAINS,
    "suse": _SUSE_DOMAINS,
    "void": _VOID_DOMAINS,
}

# os-release 的 ID -> 家族。ID_LIKE 兜底走 _FAMILY_BY_LIKE。
_FAMILY_BY_ID = {
    "debian": "apt", "ubuntu": "apt", "kali": "apt", "devuan": "apt",
    "alpine": "alpine",
    "arch": "arch", "manjaro": "manjaro",
    "fedora": "fedora",
    "rocky": "rocky", "almalinux": "almalinux",
    "opensuse-leap": "suse", "opensuse-tumbleweed": "suse",
    "opensuse": "suse",
    "void": "void",
    "gentoo": "gentoo",
}

_FAMILY_BY_LIKE = (
    ("debian", "apt"), ("suse", "suse"), ("fedora", "fedora"),
)

SUPPORTED_DISTROS = (
    "Debian / Ubuntu / Kali（apt）",
    "Alpine（apk）",
    "Arch Linux / Manjaro（pacman）",
    "Fedora（dnf）",
    "Rocky Linux / AlmaLinux（dnf）",
    "openSUSE（zypp）",
    "Void Linux（xbps）",
    "Gentoo（portage）",
)


# ---------------------------------------------------------------------------
# os-release 探测
# ---------------------------------------------------------------------------

def detect_family(etc_fd: int, rootfs_fd: int = None) -> "str | None":
    """Read /etc/os-release (then /usr/lib/os-release) → adapter family.

    /usr/lib/os-release 的旧路径是 ``../usr/lib/os-release``：名字里
    带 ``/`` 与 ``..``，openat 的 O_NOFOLLOW 只保护最后一段，中间的
    ``usr``、``lib`` 客人可以种链接穿过去——和本模块自宫的“每个
    目录都 lstat + O_NOFOLLOW”矛盾。现在改由调用方传入 rootfs 的
    描述符，逐层下钻 ("usr", "lib") 后再打开 os-release。
    """
    for name in ("os-release",):
        try:
            fd, _st = dirfd.open_regular_at(etc_fd, name, os.O_RDONLY)
        except OSError:
            fd = None
        if fd is not None:
            family = _family_from_os_release(fd)
            if family is not None:
                return family
    if rootfs_fd is not None:
        try:
            lib_fd = dirfd.descend_at(rootfs_fd, ("usr", "lib"))
        except OSError:
            lib_fd = None
        if lib_fd is not None:
            try:
                try:
                    fd, _st = dirfd.open_regular_at(
                        lib_fd, "os-release", os.O_RDONLY)
                except OSError:
                    fd = None
                if fd is not None:
                    return _family_from_os_release(fd)
            finally:
                os.close(lib_fd)
    return None


def _family_from_os_release(fd: int) -> "str | None":
    """一个已打开的 os-release fd → 家族；读不出认不出的 → None。"""
    try:
        with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as fh:
            fields = {}
            for line in fh:
                key, sep, val = line.partition("=")
                if sep:
                    fields[key.strip()] = val.strip().strip('"')
    except OSError:
        return None
    dist_id = fields.get("ID", "").lower()
    if dist_id in _FAMILY_BY_ID:
        return _FAMILY_BY_ID[dist_id]
    id_like = fields.get("ID_LIKE", "").lower()
    for token, family in _FAMILY_BY_LIKE:
        if token in id_like:
            return family
    if dist_id:
        return None
    return None


# ---------------------------------------------------------------------------
# dirfd 读写原语：lstat + O_NOFOLLOW + 备份 + 临时名 rename
# ---------------------------------------------------------------------------

def _is_regular(fd: int, name: str) -> bool:
    try:
        st = dirfd.lstat_at(fd, name)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode)


def _read_at(fd: int, name: str) -> "str | None":
    """The text of a regular file at *fd*; None when missing/not regular."""
    if not _is_regular(fd, name):
        return None
    try:
        fh, _st = dirfd.open_regular_at(fd, name, os.O_RDONLY)
    except OSError:
        return None
    try:
        with os.fdopen(fh, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _open_excl(fd: int, name: str):
    """O_CREAT|O_EXCL|O_NOFOLLOW open for writing; FileExistsError stands."""
    return os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                   | os.O_NOFOLLOW, 0o644, dir_fd=fd)


def _write_text_at(fd: int, name: str, text: str) -> bool:
    """Replace *name* at *fd* with *text*, via temp + rename.

    临时名带进程号 + 随机字节（两个并发 pd / 客人在 readdir 里盯
    固定名字都不可预测）；写入后比对目录项与写入 fd 的
    (st_dev, st_ino)——中途被换掉就拒绝发布（ESTALE 语义），
    不把客人种的链接 rename 到目标名字上。
    """
    temp = dirfd.temp_name(
        name, _UNIQUE_TMP.format(pid=os.getpid(), rnd=os.urandom(4).hex())
        + _TMP_SUFFIX)
    fh = None
    try:
        fh = _open_excl(fd, temp)
        with os.fdopen(fh, "w", encoding="utf-8") as f:
            fh = None
            f.write(text)
            st = os.fstat(f.fileno())
        try:
            entry = dirfd.lstat_at(fd, temp)
        except OSError:
            entry = None
        if (entry is None
                or (entry.st_dev, entry.st_ino) != (st.st_dev, st.st_ino)):
            raise OSError(11, "temporary file was replaced during write",
                          temp)  # errno.ESTALE
        os.rename(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
        return True
    except OSError as exc:
        if fh is not None:
            os.close(fh)
        warn(_("cannot write {name}: {exc}", name=name, exc=exc))
        dirfd.unlink_quietly(fd, temp)
        return False


def _backup_at(fd: int, name: str, original_text: str) -> bool:
    """Write <name>.pd.bak once; an existing backup is never overwritten."""
    bak = name + _BAK_SUFFIX
    if dirfd.exists_at(fd, bak):
        return True
    try:
        fh = _open_excl(fd, bak)
    except FileExistsError:
        # Lost a race with the guest creating one: whatever is there now
        # is not ours to replace or delete -- and the switch can carry
        # on without a backup of our own.
        return True
    except OSError as exc:
        warn(_("cannot back up {name}: {exc}", name=name, exc=exc))
        return False
    try:
        with os.fdopen(fh, "w", encoding="utf-8") as f:
            fh = None
            f.write(original_text)
        return True
    except OSError as exc:
        if fh is not None:
            os.close(fh)
        dirfd.unlink_quietly(fd, bak)
        warn(_("cannot back up {name}: {exc}", name=name, exc=exc))
        return False


# ---------------------------------------------------------------------------
# 家族适配器：输入 etc_fd（rootfs 的 etc 目录 fd），返回改动文件清单
# ---------------------------------------------------------------------------

def _replace_domains(text: str, rules) -> str:
    """按规则替换域名，但只在域名边界上匹配。

    纯 ``str.replace`` 会把 ``http://deb.debian.org.attacker.cn``
    “成功”替换成 ``https://mirrors.aliyun.com.attacker.cn``——切换
    声称成功，恶意后缀域名却还在；同一台主机名前缀也同理。这里用
    左右环视锁住边界：替换的必须恰好是规则里的那个 origin，
    后面跟的不是域名继续符（字母/数字/点/连字符）。
    """
    for old, new in rules:
        scheme, _, rest = old.partition("://")
        host = rest
        pattern = re.compile(
            r"(?<![\w.\-])" + re.escape(scheme) + r"://"
            + re.escape(host) + r"(?![\w.\-])"
        )
        text = pattern.sub(new.replace("\\", r"\\\\"), text)
    return text


def _family_domain_files(etc_fd: int, family: str,
                         rootfs_fd: int = None) -> list:
    """The source files this family keeps, as (parts, from_root) entries.

    *parts* is a tuple of path components below etc -- or below the
    rootfs root, when *from_root* -- opened level by level with
    O_NOFOLLOW: os.open("a/b", dir_fd=...) follows a symlink planted at
    *a*, which is exactly the hole a guest root can prepare. The first
    entry is always etc itself (or the leaf file's own directory), so
    the caller can read and rewrite through one descriptor per file.

    The returned entries name *directories to scan*; the switch scans
    them for the files its rewrites know how to handle.
    """
    if family == "apt":
        # etc/apt itself too: sources.list lives one level down.
        return [(), ("apt",), ("apt", "sources.list.d")]
    if family == "alpine":
        # /etc/apk/repositories：曾经返回 [()]（只扫 etc 自己），
        # 而 _switchable_name 只在 prefix "apk" 下认 "repositories"——
        # Alpine 的切换因此是彻底的空转，直到 revert 列表里那行
        # ("apk",) 才暴露出本意。
        return [("apk",)]
    if family == "suse":
        return [("zypp", "repos.d")]
    if family == "void":
        return [("xbps.d",), ("usr", "share", "xbps.d")]
    if family in ("arch", "manjaro"):
        return [("pacman.d",)]
    if family in ("fedora", "rocky", "almalinux"):
        return [("yum.repos.d",)]
    if family == "gentoo":
        return [("portage",)]
    return []


def _scan_source_dirs(etc_fd: int, rootfs_fd: int, dirs) -> list:
    """Open every directory in *dirs* and list the switchable files.

    Returns (dir_fd_or_None_to_close, relative_prefix, name) rows; the
    ones directly in etc carry a None fd (the caller reuses etc_fd).
    A directory that does not exist, or one that is a symlink or a
    plain file, contributes nothing -- there is nothing to switch
    behind it.

    同一个目录的多个文件共享同一个 fd：关闭时要按 fd 去重——曾经
    每个“文件”关一次，第二个 os.close(fd) 就是 EBADF，把已经切完
    源的整次调用拖成失败退出码（常见于 sources.list.d 里有两个
    .list 的 Ubuntu）。listdir 失败的目录也在这里把已打开的 fd
    关掉，不再泄漏。
    """
    rows = []
    opened = set()
    for parts in dirs:
        from_root = bool(parts) and parts[0] == "usr"
        base_fd = rootfs_fd if from_root else etc_fd
        fd = None
        try:
            fd = dirfd.descend_at(base_fd, parts) if parts else None
        except OSError:
            continue
        prefix = "/".join(parts)
        list_fd = fd if fd is not None else etc_fd
        try:
            for entry in dirfd.listdir_at(list_fd):
                if entry.endswith(_BAK_SUFFIX) or entry.endswith(_TMP_SUFFIX):
                    continue
                if not _switchable_name(prefix, entry):
                    continue
                rows.append((fd, prefix, entry))
        except OSError:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                fd = None
        if fd is not None:
            opened.add(fd)
    # 描述符集合挂在 rows 的第一个元素上：调用方结束后按集合关闭。
    return rows, opened


def _switchable_name(prefix: str, name: str) -> bool:
    """Whether *name* under *prefix* is a file the switch would rewrite."""
    if prefix == "apt" or prefix == "":
        return (name == "sources.list" or name.endswith(".list")
                or name.endswith(".sources"))
    if prefix == "apt/sources.list.d":
        return name.endswith(".list") or name.endswith(".sources")
    if prefix == "apk":
        return name == "repositories"
    if prefix in ("zypp/repos.d", "yum.repos.d"):
        return name.endswith(".repo")
    if prefix == "xbps.d" or prefix == "usr/share/xbps.d":
        return "repository" in name
    if prefix == "pacman.d":
        return name == "mirrorlist"
    if prefix == "portage":
        return name == "make.conf"
    return False


def _rewrite_for_family(family: str, text: str, filename: str = "",
                        provider: "str | None" = None) -> "str | None":
    """The switched text for *family*, or None when nothing matched.

    *filename* 供需要按文件区分处理的家族（Fedora）判断自己面对
    的是哪一个 repo 文件；*provider* 是镜像提供方 id（None = 默认
    清华，见 ``resolve_provider_for_family``）。
    """
    chosen, _fell_back = resolve_provider_for_family(provider, family)
    host = mirror_provider_host(chosen)
    if host is None:
        return None
    prefix = _family_prefix(chosen, family)
    mirror_base = f"https://{host}{prefix}"

    if family in _FAMILY_DOMAINS:
        rules = _pair_rules(_FAMILY_DOMAINS[family], mirror_base)
        if not any(old in text for old, _new in rules):
            return None
        return _replace_domains(text, rules)
    if family in ("arch", "manjaro"):
        if family == "arch":
            # Arch 官方布局：<mirror>/archlinux/$repo/os/$arch。
            server = f"{mirror_base}/$repo/os/$arch"
        else:
            # Manjaro 的仓库布局与 Arch 不同：<mirror>/manjaro/<branch>/
            # $repo/$arch——没有 /os/（那是 Arch 的目录层级）。曾经照抄
            # Arch 的 /os/，切完后 pacman 全部 404。
            server = f"{mirror_base}/stable/$repo/$arch"
        body = (
            f"# 由 proot-distro-i18n 切换到国内镜像（{server}）\n"
            f"# 原始文件已备份为 mirrorlist{_BAK_SUFFIX}\n"
            f"Server = {server}\n"
        )
        return body
    if family == "fedora":
        return _rewrite_fedora(text, filename, provider=chosen)
    if family in ("rocky", "almalinux"):
        bases = (
            [f"https://dl.rockylinux.org/$contentdir",
             f"http://dl.rockylinux.org/$contentdir"]
            if family == "rocky"
            else [f"https://repo.almalinux.org/almalinux",
                  f"http://repo.almalinux.org/almalinux"]
        )
        mirror = mirror_base
        out = []
        touched = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("mirrorlist=") \
                    or stripped.startswith("metalink="):
                out.append("# " + line)
                touched = True
                continue
            if stripped.startswith("# baseurl=") \
                    or stripped.startswith("#baseurl="):
                line = stripped.lstrip("#").strip()
                line = "baseurl=" + line.split("=", 1)[1]
                stripped = line
            if stripped.startswith("baseurl=") \
                    or stripped.startswith("gpgkey="):
                for old in bases:
                    if old in line:
                        line = line.replace(old, mirror)
                        touched = True
            out.append(line)
        if not touched:
            return None
        return "\n".join(out) + "\n"
    if family == "gentoo":
        # GENTOO_MIRRORS 惯例写两个（portage 择优选）：所选提供方 +
        # 一个也镜像 gentoo 的回退站；所选站不镜像 gentoo 时（阿里云）
        # resolve 已经换了，这里拿到的一定是镜像它的那家。
        secondary_id = next(
            (pid for pid in (DEFAULT_MIRROR_PROVIDER,) + _PROVIDER_FALLBACKS
             if pid != chosen and mirror_provider_supports(pid, "gentoo")),
            DEFAULT_MIRROR_PROVIDER,
        )
        secondary_host = mirror_provider_host(secondary_id) or host
        mirror_line = (
            f'GENTOO_MIRRORS="{mirror_base} '
            f'https://{secondary_host}/gentoo"'
        )
        out = []
        replaced = False
        for line in text.splitlines():
            if line.strip().startswith("GENTOO_MIRRORS"):
                out.append(mirror_line)
                replaced = True
            else:
                out.append(line)
        if not replaced:
            out.append(mirror_line)
        return "\n".join(out) + "\n"
    return None


# ---------------------------------------------------------------------------
# Fedora 专属适配器（逐文件）
# ---------------------------------------------------------------------------

def _rewrite_fedora(text: str, filename: str,
                   provider: "str | None" = None) -> "str | None":
    """Fedora 的逐文件重写：只处理本程序能映射的那两个官方 repo。

    曾经的实现是“文件里出现 fedoraproject.org 就整文件换成一段
    [mirror-cn]”：fedora-updates.repo、fedora-cisco-openh264.repo、
    modular、用户自建 repo 全部被採平成同一个 repo id、同一组
    baseurl（updates 与 base 靠文件名里有没有 "updates" 猜）。现在：

    * 只重写 repo id 为 ``fedora`` / ``fedora-updates`` 的文件
      （其余文件一律不动）；
    * 生成的段沿用该文件自己的 repo id 与官方密钥路径
      （``file:///etc/pki/rpm-gpg/...``，镜像站不必可信赖地托管密钥）；
    * updates 走 fedora-updates 布局，base 走 releases 布局；
    * *provider* 决定镜像主机（默认清华），两个布局对每个提供方
      都是 ``<mirror>/fedora/...`` 同构。
    """
    chosen, _fell_back = resolve_provider_for_family(provider, "fedora")
    host = mirror_provider_host(chosen)
    if host is None:
        return None
    label = mirror_provider_label(chosen)
    ids = [line.strip()[1:-1].strip().lower()
           for line in text.splitlines()
           if line.strip().startswith("[") and line.strip().endswith("]")]
    repo_id = ids[0] if ids else ""
    # fedora 与 fedora-updates 是本程序能正确合成的两个官方 repo；
    # 旧版 fedora-updates.repo 的 id 叫 [updates]，新版叫
    # [fedora-updates]——两个都认。其余（h264、modular、用户自建）不属于
    # 能安全合成的范围，交还给用户。
    if repo_id not in ("fedora", "fedora-updates", "updates"):
        return None
    if "fedoraproject.org" not in text:
        return None
    updates = repo_id in ("fedora-updates", "updates")
    if updates:
        base = (f"https://{host}/fedora/updates/$releasever/"
                f"Everything/$basearch/Packages/")
        name = "Fedora $releasever - Updates - $basearch"
        key = ("file:///etc/pki/rpm-gpg/"
               "RPM-GPG-KEY-fedora-$releasever-primary")
    else:
        base = (f"https://{host}/fedora/releases/$releasever/"
                f"Everything/$basearch/os/")
        name = "Fedora $releasever - $basearch"
        key = ("file:///etc/pki/rpm-gpg/"
               "RPM-GPG-KEY-fedora-$releasever-primary")
    body = (
        f"#{repo_id}（由 proot-distro-i18n 切换到{label}镜像）\n"
        f"[{repo_id}]\n"
        f"name={name}\n"
        f"baseurl={base}\n"
        f"enabled=1\n"
        f"gpgcheck=1\n"
        f"gpgkey={key}\n"
    )
    return body



def switch_distro_mirror(rootfs: str, container_name: str,
                         dist_names: "set | None" = None, *,
                         root_fd: int = None,
                         provider: "str | None" = None) -> bool:
    """Switch *container_name*'s package sources to a Chinese mirror.

    *rootfs* is the container's rootfs path; *dist_names* is the set of
    distribution names the manifest records, kept for callers that want
    to display it -- identification here is os-release-driven.
    *root_fd* 是调用方钉住的 rootfs 描述符（install 在持有描述符的
    try 块里直接传，避免闭锁后按名字重解一次客人可换的路径）；
    None 时才按名字打开。
    *provider* 是镜像提供方 id（``tuna``/``bfsu``/``nju``/``ustc``/
    ``cernet``/``aliyun``/``huawei``/``tencent``）；None 时取默认清华。
    所选站不镜像此家族时按回退链落到别家，并提示"选了 A、用了 B"。

    Returns True when at least one source file was rewritten **or
    nothing needed doing**（已指向国内镜像的重复执行是幂等成功，
    脚本化调用不必因“已经是了”而失败）; never raises -- every
    failure is reported and the install that triggered it carries on.
    """
    own_root_fd = None
    rootfs_fd = None
    etc_fd = None
    try:
        if root_fd is not None:
            rootfs_fd = dirfd.reopen(root_fd)
        else:
            own_root_fd = rootfs_fd = dirfd.opendir(rootfs)
        try:
            etc_fd = dirfd.opendir_at(rootfs_fd, "etc")
        except OSError as exc:
            warn(_("container {container} has no /etc; skipping the "
                   "mirror switch: {exc}",
                   container=container_name, exc=exc))
            return False

        family = detect_family(etc_fd, rootfs_fd)
        if family is None:
            warn(_("no mirror mapping for this distro (identified via "
                   "/etc/os-release). Supported: {supported}",
                   supported="; ".join(SUPPORTED_DISTROS)))
            return False

        dirs = _family_domain_files(etc_fd, family)
        if not dirs:
            warn(_("no package-source files found for this distro; "
                   "skipping the switch."))
            return False

        chosen, fell_back = resolve_provider_for_family(provider, family)
        label = mirror_provider_label(chosen)
        if fell_back:
            warn(_("the {requested} mirror does not carry this distro; "
                   "falling back to the {actual} mirror.",
                   requested=mirror_provider_label(
                       provider or DEFAULT_MIRROR_PROVIDER),
                   actual=label))
        log_info(_("switching {container}'s package sources to the "
                   "{label} mirror...",
                   container=container_name, label=label))

        changed = []
        rows, opened = _scan_source_dirs(etc_fd, rootfs_fd, dirs)
        try:
            for fd, prefix, name in rows:
                target_fd = etc_fd if fd is None else fd
                original = _read_at(target_fd, name)
                if original is None:
                    continue
                rewritten = _rewrite_for_family(family, original, name,
                                                 provider=chosen)
                if rewritten is None or rewritten == original:
                    continue
                if not _backup_at(target_fd, name, original):
                    continue
                if _write_text_at(target_fd, name, rewritten):
                    shown = (f"/etc/{prefix}/{name}" if prefix
                             else f"/etc/{name}")
                    changed.append(shown)
        finally:
            for fd in opened:
                try:
                    os.close(fd)
                except OSError:
                    pass

        if not changed:
            # 幂等成功，不是失败：源已指向国内镜像（或本来就没有官方
            # 域名可换）时，脚本化重跑不应当拿到非零退出码。
            log_info(_("sources already point at a Chinese mirror, or no "
                       "known upstream domain matched; nothing changed."))
            return True

        msg()
        log_info(_("rewrote {n} file(s):", n=len(changed)))
        for shown in changed:
            msg(f"  {shown}")
        log_info(_("originals saved as <file>{bak}; restore them with "
                   "'mirror distro {container} --revert'.",
                   bak=_BAK_SUFFIX, container=container_name))
        msg()
        return True
    except OSError as exc:
        log_error(_("the mirror switch failed: {exc}", exc=exc))
        return False
    finally:
        if etc_fd is not None:
            os.close(etc_fd)
        if own_root_fd is not None:
            os.close(own_root_fd)


def revert_distro_mirror(rootfs: str, container_name: str) -> bool:
    """Restore every source file with a .pd.bak back to its original."""
    rootfs_fd = None
    etc_fd = None
    try:
        rootfs_fd = dirfd.opendir(rootfs)
        try:
            etc_fd = dirfd.opendir_at(rootfs_fd, "etc")
        except OSError as exc:
            warn(_("container {container} has no /etc: {exc}",
                   container=container_name, exc=exc))
            return False

        restored = []
        # Every directory a switch may have written a backup into,
        # descended level by level (a mid-path symlink must never be
        # followed); ../usr/share/xbps.d used to be opened as one
        # two-dot path, which openat would happily walk through a
        # planted symlink.
        for parts, from_root in (
            ((), False), (("apt",), False),
            (("apt", "sources.list.d"), False), (("apk",), False),
            (("pacman.d",), False), (("yum.repos.d",), False),
            (("zypp", "repos.d"), False), (("xbps.d",), False),
            (("portage",), False), (("usr", "share", "xbps.d"), True),
        ):
            base_fd = rootfs_fd if from_root else etc_fd
            try:
                target_fd = dirfd.descend_at(base_fd, parts) if parts \
                    else None
            except OSError:
                continue
            try:
                scan_fd = target_fd if target_fd is not None else etc_fd
                for entry in dirfd.listdir_at(scan_fd):
                    if not entry.endswith(_BAK_SUFFIX):
                        continue
                    original = entry[:-len(_BAK_SUFFIX)]
                    if not original:
                        continue
                    # Rename the backup over the current entry -- a
                    # directory-entry swap, safe against planted links.
                    os.rename(entry, original,
                              src_dir_fd=scan_fd, dst_dir_fd=scan_fd)
                    prefix = "/".join(parts)
                    shown = (f"/etc/{prefix}/{original}" if prefix
                             else f"/etc/{original}")
                    restored.append(shown)
            finally:
                if target_fd is not None:
                    os.close(target_fd)

        if not restored:
            log_info(_("{container} has no backups ({bak}) to restore.",
                       container=container_name, bak="*" + _BAK_SUFFIX))
            return False
        log_info(_("restored {n} file(s):", n=len(restored)))
        for shown in restored:
            msg(f"  {shown}")
        return True
    except OSError as exc:
        log_error(_("the restore failed: {exc}", exc=exc))
        return False
    finally:
        if etc_fd is not None:
            os.close(etc_fd)
        if rootfs_fd is not None:
            os.close(rootfs_fd)
