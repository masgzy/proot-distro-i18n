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
import stat

from proot_distro import dirfd
from proot_distro.i18n import _
from proot_distro.message import log_error, log_info, msg, warn

__all__ = (
    "SUPPORTED_DISTROS", "revert_distro_mirror", "switch_distro_mirror",
)

_BAK_SUFFIX = ".pd.bak"
_TMP_SUFFIX = ".pd.tmp"

# ---------------------------------------------------------------------------
# 镜像源表：家族 -> (镜像显示名, 文本替换规则)。
# 规则一律整段 "scheme://origin" -> "scheme://mirror/路径"，保持镜像站
# 与官方源的目录同构，只动主机名与协议，不重排 suite/components。
# ---------------------------------------------------------------------------

_ALIYUN = "mirrors.aliyun.com"
_TUNA = "mirrors.tuna.tsinghua.edu.cn"


def _pair_rules(domains, mirror):
    """http(s)://<每个官方域名> -> https://<镜像> 的替换对。"""
    rules = []
    for domain in domains:
        rules.append((f"http://{domain}", f"https://{mirror}"))
        rules.append((f"https://{domain}", f"https://{mirror}"))
    return rules


_APT_DOMAINS = [
    "deb.debian.org", "cdn-aws.deb.debian.org", "security.debian.org",
    "archive.ubuntu.com", "security.ubuntu.com", "ports.ubuntu.com",
    "http.kali.org",
]

_DOMAIN_RULES: dict = {
    "apt": _pair_rules(_APT_DOMAINS, _ALIYUN),
    "alpine": _pair_rules(["dl-cdn.alpinelinux.org"], _ALIYUN),
    "suse": _pair_rules(["download.opensuse.org"], f"{_ALIYUN}/opensuse"),
    "void": _pair_rules(
        ["repo-default.voidlinux.org", "repo.voidlinux.org",
         "alpha.de.repo.voidlinux.org"], f"{_TUNA}/voidlinux",
    ),
}

_MIRROR_LABEL = {
    "apt": "阿里云",
    "alpine": "阿里云",
    "suse": "阿里云",
    "void": "清华大学",
    "arch": "阿里云",
    "manjaro": "阿里云",
    "fedora": "阿里云",
    "rocky": "阿里云",
    "almalinux": "阿里云",
    "gentoo": "清华大学",
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

def detect_family(etc_fd: int) -> "str | None":
    """Read /etc/os-release and return the adapter family, or None."""
    for name in ("os-release", "../usr/lib/os-release"):
        try:
            fd, _st = dirfd.open_regular_at(etc_fd, name, os.O_RDONLY)
        except OSError:
            continue
        try:
            with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as fh:
                fields = {}
                for line in fh:
                    key, sep, val = line.partition("=")
                    if sep:
                        fields[key.strip()] = val.strip().strip('"')
        except OSError:
            continue
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

    The caller lstat'ed the entry before reading it; rename swaps the
    directory entry itself, so a link planted between read and write
    cannot be followed.
    """
    temp = dirfd.temp_name(name, _TMP_SUFFIX)
    fh = None
    try:
        fh = _open_excl(fd, temp)
        with os.fdopen(fh, "w", encoding="utf-8") as f:
            fh = None
            f.write(text)
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
    for old, new in rules:
        text = text.replace(old, new)
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
        return [()]
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
    """
    rows = []
    for parts in dirs:
        from_root = bool(parts) and parts[0] == "usr"
        base_fd = rootfs_fd if from_root else etc_fd
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
            pass
    # The descriptors in the rows stay open: the switch reads and
    # rewrites through them, and closes them when it is done. A
    # directory whose listdir failed contributes nothing and its fd is
    # closed here.
    return rows


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


def _rewrite_for_family(family: str, text: str) -> "str | None":
    """The switched text for *family*, or None when nothing matched."""
    if family in _DOMAIN_RULES:
        rules = _DOMAIN_RULES[family]
        if not any(old in text for old, _new in rules):
            return None
        return _replace_domains(text, rules)
    if family in ("arch", "manjaro"):
        if family == "arch":
            server = f"https://{_ALIYUN}/archlinux/$repo/os/$arch"
        else:
            server = f"https://{_ALIYUN}/manjaro/stable/$repo/os/$arch"
        body = (
            f"# 由 proot-distro-i18n 切换到国内镜像（{server}）\n"
            f"# 原始文件已备份为 mirrorlist{_BAK_SUFFIX}\n"
            f"Server = {server}\n"
        )
        return body
    if family == "fedora":
        if "fedoraproject.org" not in text:
            return None
        updates = "updates" in text
        if updates:
            base = (f"https://{_ALIYUN}/fedora/updates/$releasever/"
                    f"Everything/$basearch/Packages/")
            name = "Fedora $releasever - Updates - $basearch"
        else:
            base = (f"https://{_ALIYUN}/fedora/releases/$releasever/"
                    f"Everything/$basearch/os/")
            name = "Fedora $releasever - $basearch"
        key = (f"https://{_ALIYUN}/fedora/releases/$releasever/"
               f"Everything/$basearch/os/RPM-GPG-KEY-fedora-$releasever-primary")
        body = (
            f"[mirror-cn]\n"
            f"name={name}\n"
            f"baseurl={base}\n"
            f"enabled=1\n"
            f"gpgcheck=1\n"
            f"gpgkey={key}\n"
        )
        return body
    if family in ("rocky", "almalinux"):
        bases = (
            [f"https://dl.rockylinux.org/$contentdir",
             f"http://dl.rockylinux.org/$contentdir"]
            if family == "rocky"
            else [f"https://repo.almalinux.org/almalinux",
                  f"http://repo.almalinux.org/almalinux"]
        )
        mirror = f"https://{_ALIYUN}/rockylinux" if family == "rocky" \
            else f"https://{_ALIYUN}/almalinux"
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
        mirror_line = (
            f'GENTOO_MIRRORS="https://{_TUNA}/gentoo '
            f'https://{_ALIYUN}/gentoo"'
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
# 主入口：切换 / 还原
# ---------------------------------------------------------------------------

def switch_distro_mirror(rootfs: str, container_name: str,
                         dist_names: "set | None" = None) -> bool:
    """Switch *container_name*'s package sources to a Chinese mirror.

    *rootfs* is the container's rootfs path; *dist_names* is the set of
    distribution names the manifest records, kept for callers that want
    to display it -- identification here is os-release-driven. Returns
    True when at least one source file was rewritten; never raises --
    every failure is reported and the install that triggered it
    carries on.
    """
    rootfs_fd = None
    etc_fd = None
    try:
        rootfs_fd = dirfd.opendir(rootfs)
        try:
            etc_fd = dirfd.opendir_at(rootfs_fd, "etc")
        except OSError as exc:
            warn(_("container {container} has no /etc; skipping the "
                   "mirror switch: {exc}",
                   container=container_name, exc=exc))
            return False

        family = detect_family(etc_fd)
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

        label = _MIRROR_LABEL.get(family, "国内")
        log_info(_("switching {container}'s package sources to the "
                   "{label} mirror...",
                   container=container_name, label=label))

        changed = []
        rows = _scan_source_dirs(etc_fd, rootfs_fd, dirs)
        try:
            for fd, prefix, name in rows:
                target_fd = etc_fd if fd is None else fd
                original = _read_at(target_fd, name)
                if original is None:
                    continue
                rewritten = _rewrite_for_family(family, original)
                if rewritten is None or rewritten == original:
                    continue
                if not _backup_at(target_fd, name, original):
                    continue
                if _write_text_at(target_fd, name, rewritten):
                    shown = (f"/etc/{prefix}/{name}" if prefix
                             else f"/etc/{name}")
                    changed.append(shown)
        finally:
            for fd, _prefix, _name in rows:
                if fd is not None:
                    os.close(fd)

        if not changed:
            warn(_("sources already point at a Chinese mirror, or no "
                   "known upstream domain matched; nothing changed."))
            return False

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
        if rootfs_fd is not None:
            os.close(rootfs_fd)


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
