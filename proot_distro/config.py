#
# Proot-Distro - manage proot containers.
#
# Created by Sylirre <sylirre@termux.dev> for Termux project.
# Development assisted by Claude Code (https://claude.ai/code).
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

"""用户偏好配置文件（``config.json``）的读写。

单一配置文件承载所有用户级偏好（当前只有 ``language``），与
``mirror.json``（镜像源选择）、``mirrors.json``（自定义镜像源）并列
放在同一个配置目录：

- Termux:   ``$PREFIX/etc/proot-distro/config.json``
- 桌面 Linux: ``$XDG_CONFIG_HOME/proot-distro/config.json``
            （XDG_CONFIG_HOME 未设时回退 ``~/.config/``）

文件格式为 JSON 对象，键是偏好名，值由各功能自行约定（``language``
的值是 ``AVAILABLE_LANGUAGES`` 里的语言代码）。未知的键在写回时
原样保留，因此本模块做的是**部分更新**（merge 语义），不会把其他
功能的设置擦掉。

读路径永不抛错：文件缺失、损坏、类型不对一律返回空 dict——配置
读不出来就当没配置，程序按默认值继续跑。写路径使用
``atomic.replace`` 风格的原子替换，避免并发写产生半截文件。
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os

from proot_distro import dirfd
from proot_distro.constants import IS_TERMUX, PROGRAM_NAME, TERMUX_PREFIX
from proot_distro.statedir import read_state_file

__all__ = (
    "CONFIG_DIR", "CONFIG_FILE",
    "load_user_config", "save_user_config", "clear_user_key",
    "read_json_in_config", "write_json_in_config",
)
# ---------------------------------------------------------------------------
# 配置目录（与 mirrors.py 同一套规则，单一事实来源在这里）
# ---------------------------------------------------------------------------

if IS_TERMUX:
    CONFIG_DIR = os.path.join(TERMUX_PREFIX, "etc", PROGRAM_NAME)
else:
    CONFIG_DIR = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config", PROGRAM_NAME
    )

CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

# Termux 上的用户级回退：$PREFIX/etc 坐在 pkg 管的地盘上，pip 装的
# pd、root 残留的目录或只读 overlay 都能把主路径变成“不可写”——实测
# `setting put` 就死在 Errno 5。家园目录（~/.config）永远是用户自己的；
# 主路径不可写时写入改道到这里，读则两条路径都看（主优先），
# 旧安装的配置不丢，新写入也不再崩。两个常量都在导入期定死（不随
# HOME 的运行期变化），同一条纪律。
if IS_TERMUX:
    FALLBACK_CONFIG_FILE = os.path.join(
        os.environ.get("XDG_CONFIG_HOME")
        or os.path.join(TERMUX_HOME, ".config"),
        PROGRAM_NAME, "config.json")
else:
    FALLBACK_CONFIG_FILE = ""   # 桌面上没有回退（XDG 本来就是用户自己的）


def _config_writable(path: str) -> bool:
    """*path* 所在目录现在能否写入（探针式的诚实答案）。

    开目录拿描述符、试写进一个随机临时名再删除——存在权限/只读
    overlay/不可达都会在这里如实返回 False。探测代价一次 O_CREAT
    + unlink，只在写入路径上花。
    """
    dir_fd = _open_config_dir(path, create=True)
    if dir_fd is None:
        return False
    probe = f".wtest.{os.getpid()}.{os.urandom(4).hex()}.tmp"
    try:
        try:
            fd, _st = dirfd.open_new_at(dir_fd, probe, 0o600, readable=True)
        except OSError:
            return False
        try:
            os.close(fd)
        finally:
            dirfd.unlink_quietly(dir_fd, probe)
        return True
    finally:
        os.close(dir_fd)


def effective_config_file() -> str:
    """写入应当去的 config.json 路径（主路径不可写时落到用户级）。

    主路径在 Termux 上是 $PREFIX/etc/proot-distro/config.json。
    不可写（Errno 5 的根因）时改道 ~/.config/proot-distro/config.json
    ——家园目录不受 pkg 管辖，setting put 不再因系统路径拒写而崩。
    桌面上没有回退（XDG 本来就是用户自己的）。
    """
    if not FALLBACK_CONFIG_FILE:
        return CONFIG_FILE
    if _config_writable(CONFIG_FILE):
        return CONFIG_FILE
    return FALLBACK_CONFIG_FILE


def config_candidates_for_read() -> tuple:
    """读 config.json 的候选路径（主路径优先，回退在后）。

    写入改道后配置可能存在任一路径上；两处都读，主路径胜出。
    """
    if not FALLBACK_CONFIG_FILE or CONFIG_FILE == FALLBACK_CONFIG_FILE:
        return (CONFIG_FILE,)
    return (CONFIG_FILE, FALLBACK_CONFIG_FILE)


# ---------------------------------------------------------------------------
# 硬化 I/O：配置目录在 Termux 上坐在客人可写的 $PREFIX 里
# ---------------------------------------------------------------------------

def _config_dir_parts(path: str = None) -> tuple:
    """目标配置文件所在目录的绝对路径分量（无尾斜杠，无空段）。

    默认取当下生效的 CONFIG_FILE（测试会替换它），也可显式传入
    其它配置文件（mirror.json 等）的完整路径。
    """
    if path is None:
        path = CONFIG_FILE
    norm = os.path.normpath(os.path.dirname(path) or os.curdir)
    return tuple(p for p in norm.split(os.sep) if p)


def _open_config_dir(path: str = None, *, create: bool = False):
    """O_NOFOLLOW 逐层下钻拿到配置目录的描述符；拒绝/不存在 → None。

    按名字的 ``os.makedirs + open`` 会让客人种在 ``$PREFIX/etc`` 或
    ``proot-distro`` 层上的符号链接把读写指向任意宿主目录，而 FIFO
    则会让读挂死。这里从根目录开始逐层 opendir_at（O_NOFOLLOW），
    调用方拿到的是验证过的 inode 描述符。桌面 Linux 上同样成立。
    """
    return dirfd.opendir_under(os.sep, _config_dir_parts(path), create=create)


def read_json_in_config(path: str):
    """读配置文件 *path* 的 JSON；不存在/不是常规文件/坏 JSON → None。

    走 ``_open_config_dir`` + ``open_regular_at``：链接与 FIFO 被拒
    绝（后者在 open_regular_at 的 fstat 那里就拦下，不会挂住），
    读量以上限封顶（一个陌生人可替换的文件决定不了要读进多少字节）。
    """
    dir_fd = _open_config_dir(path)
    if dir_fd is None:
        return None
    try:
        try:
            fd, _st = dirfd.open_regular_at(
                dir_fd, os.path.basename(path), os.O_RDONLY)
        except OSError:
            return None
        try:
            try:
                return json.loads(
                    read_state_file(fd).decode("utf-8", "replace"))
            except (OSError, ValueError):
                return None
        finally:
            os.close(fd)
    finally:
        os.close(dir_fd)


def write_json_in_config(path: str, payload) -> bool:
    """把 *payload* 原子地写为配置文件 *path*；成功与否见返回值。

    与 ``atomic_write`` 的状态树分支同一套纪律：唯一临时名（进程号
    + 随机后缀）、O_EXCL 创建、写入后比对目录项与描述符的
    (st_dev, st_ino)——中途被客人换掉就拒绝发布——再 rename 到位。
    """
    dir_fd = _open_config_dir(path, create=True)
    if dir_fd is None:
        return False
    name = os.path.basename(path)
    tmp = dirfd.temp_name(
        name, f".{os.getpid()}.{os.urandom(4).hex()}.tmp")
    try:
        fd, _st = dirfd.open_new_at(dir_fd, tmp, 0o600, readable=True)
    except OSError:
        os.close(dir_fd)
        return False
    try:
        data = json.dumps(payload, ensure_ascii=False, indent=2,
                          sort_keys=True).encode("utf-8") + b"\n"
        os.write(fd, data)
        written = os.fstat(fd)
        os.close(fd)
        fd = None
        try:
            entry = dirfd.lstat_at(dir_fd, tmp)
        except OSError:
            entry = None
        if (entry is None
                or (entry.st_dev, entry.st_ino)
                != (written.st_dev, written.st_ino)):
            raise OSError(
                11, "the temporary file was replaced while it was being "
                "written", tmp)  # errno.ESTALE
        os.replace(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        return True
    except OSError:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        dirfd.unlink_quietly(dir_fd, tmp)
        return False
    finally:
        dirfd.unlink_quietly(dir_fd, tmp)
        os.close(dir_fd)


# 同进程内的嵌套重入计数：save_user_config 拿着锁时，clear_user_key
# 这类外层再进 config_lock() 不至于对自己 flock 死锁。
_lock_depth = 0


@contextlib.contextmanager
def config_lock():
    """串行化同一用户的配置读-改-写（尽力而为的 flock；可重入）。

    没有它，并发的 ``lang use`` 与 ``setting put`` 各自读旧态、各自
    原子替换，后落盘者把先落盘者的键整个丢掉——原子替换只保证
    不出半截文件，不保证不丢更新。锁文件本身也在客人可写目录里，
    所以它只能挡住两个诚实进程互踩，挡不住恶意替换（拿不到锁或
    锁文件被换就当作无锁继续，绝不因此拒跑）。
    """
    global _lock_depth
    if _lock_depth:
        _lock_depth += 1
        try:
            yield
        finally:
            _lock_depth -= 1
        return
    fd = None
    try:
        dir_fd = _open_config_dir(create=True)
        if dir_fd is not None:
            try:
                try:
                    fd = os.open(
                        "config.lock",
                        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                        0o600, dir_fd=dir_fd,
                    )
                except OSError:
                    fd = None
            finally:
                os.close(dir_fd)
    except OSError:
        fd = None
    if fd is None:
        _lock_depth += 1
        try:
            yield
        finally:
            _lock_depth -= 1
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError:
            pass
        _lock_depth += 1
        try:
            yield
        finally:
            _lock_depth -= 1
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------

def load_user_config() -> dict:
    """读整个 config.json；缺失/损坏/类型不对一律返回 {}。

    每次调用都重新读盘——config 文件极小、调用频率极低（命令入口、
    ``lang`` 子命令），不值得缓存；不缓存也让 ``lang use`` 后同一
    进程内立即可见。

    读取本身是硬化的（见 read_json_in_config）：配置目录在 Termux
    上坐在客人可写的 $PREFIX 里，链接与 FIFO 都不会被判为可读的
    常规文件。
    """
    for path in config_candidates_for_read():
        data = read_json_in_config(path)
        if data is not None:
            return data if isinstance(data, dict) else {}
    return {}


# ---------------------------------------------------------------------------
# 写
# ---------------------------------------------------------------------------

def save_user_config(update: dict, *, remove_keys: tuple = ()) -> dict:
    """部分更新 config.json（merge 语义），返回更新后的完整配置。

    *update* 中的键覆盖旧值（值为 None 的键会被删除——与
    ``remove_keys`` 效果相同，方便调用方直接传 None 表达"清除"）；
    其他已有键原样保留。目录不存在时自动创建。

    写入走项目统一的原子替换语义（见 write_json_in_config），并且整
    个读-改-写抱在 ``config_lock()`` 里：并发的两个写入者不再互丢
    对方的键。
    """
    with config_lock():
        data = load_user_config()
        for key, value in update.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        for key in remove_keys:
            data.pop(key, None)

        # 写入目标：主路径可写则写主路径（旧配置原位更新），否则
        # 改道用户级回退路径（Errno 5 的根治——见
        # effective_config_file）。改道时先并入回退路径上已有的键
        # （若有），不丢用户早前的设置。
        target = effective_config_file()
        if target != CONFIG_FILE:
            existing = read_json_in_config(target)
            if isinstance(existing, dict):
                for key, value in existing.items():
                    data.setdefault(key, value)

        if not write_json_in_config(target, data):
            # 让调用方以 OSError 的方式报错，和以前一致；文案走 _() 以
            # 便随界面语言翻译，并把原因（目录不可达/不可写）点名。
            from proot_distro.i18n import _

            raise OSError(  # errno.EIO
                5,
                _("cannot write {path}: the configuration directory is "
                  "not reachable or not writable.", path=target),
            )
    return data


def clear_user_key(key: str) -> bool:
    """删除配置里的单个键；返回是否真的存在（并删除了）。

    删除走 ``save_user_config`` 的 ``remove_keys`` 通道——让同一次
    读-改-写完成删除。曾经在这里先 ``del data[key]`` 再把剩余字典当
    ``update`` 传回去：``save_user_config`` 会先从磁盘重读旧状态再
    合并，结果被删的键又从磁盘上"复活"了——只要文件里还有别的键
    （比如 ``language`` 与一个设置并存）就必现。写回后如果配置变空
    则顺手删掉整个文件，保持配置目录整洁——与 ``lang reset`` 的
    "清除偏好"语义匹配。
    """
    if key not in load_user_config():
        return False
    with config_lock():
        save_user_config({}, remove_keys=(key,))
        if not load_user_config():
            # The key was the last one: no config left to keep, drop the
            # file the way `lang reset` has always tidied up. 按目录描述符
            # 删条目本身，而不是按名字——同上，客人在同目录里可写。
            dir_fd = _open_config_dir()
            if dir_fd is not None:
                try:
                    dirfd.unlink_quietly(dir_fd, os.path.basename(CONFIG_FILE))
                finally:
                    os.close(dir_fd)
    return True
