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

"""首次运行守护：防止 ``apt upgrade`` 覆盖 pip 安装的 i18n 版。

背景
====

本 fork 通过 ``pip install git+https://...`` 安装，入口脚本写到
``$PREFIX/bin/proot-distro``——与官方 apt 包 ``proot-distro`` 的文件
路径完全相同。一旦用户执行 ``apt upgrade``，官方包会把入口脚本换回
Bash 版，i18n 版静默消失。

解法
====

首次**成功**运行本程序后（由 ``cli.main`` 在命令正常完成时调用，
失败路径不会走到这里），做一次检测：

1. 官方 apt 包 ``proot-distro`` 已安装（``dpkg-query -W``）；
2. 尚未被 hold（``apt-mark showhold``）；
3. 当前入口脚本确实是 Python 版（头部 shebang 含 ``python``），
   即 pip 版真的覆盖了官方入口——若用户只装了官方版、或 pip 版
   装在别处，绝不碰用户的包状态。

三条全满足时执行 ``apt-mark hold proot-distro`` 并打印提醒（含
恢复方法）。任何一条不满足、或任何子进程调用失败，都静默放弃。

检测做过一次就写标记文件（``RUNTIME_DIR/.autohold-checked``），
之后永不重复。设 ``PD_NO_AUTO_HOLD=1`` 可彻底禁用（不检测、不写
标记、零子进程开销）。
"""

from __future__ import annotations

import os
import subprocess
import sys

from proot_distro.constants import IS_TERMUX, RUNTIME_DIR
from proot_distro.i18n import _
from proot_distro.message import C, msg

__all__ = ("maybe_auto_hold",)

_HOLD_SENTINEL = os.path.join(RUNTIME_DIR, ".autohold-checked")
_PKG = "proot-distro"
_TIMEOUT = 15


def _entry_is_python() -> bool:
    """判断当前入口脚本是否为 Python（pip 版覆盖官方 Bash 入口的标志）。"""
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0 and os.path.sep not in argv0:
        # 相对裸名（如 `proot-distro`）：从 PATH 解析
        for d in os.environ.get("PATH", "").split(os.pathsep):
            cand = os.path.join(d, argv0)
            if os.path.isfile(cand):
                argv0 = cand
                break
    try:
        with open(argv0, "rb") as fh:
            head = fh.readline(256).decode("utf-8", "replace")
    except (OSError, ValueError):
        return False
    return "python" in head.lower()


def _run(cmd: list, **kw) -> subprocess.CompletedProcess | None:
    """跑一个子进程；任何失败返回 None（守护逻辑永不抛错）。"""
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_TIMEOUT, **kw)
    except (OSError, subprocess.TimeoutExpired):
        return None


def maybe_auto_hold() -> None:
    """见模块 docstring。所有路径都必须安静、无异常、幂等。"""
    # 1. 显式禁用：直接返回，不写标记（用户改主意后仍可再触发）
    if os.environ.get("PD_NO_AUTO_HOLD", "").strip().lower() in (
        "1", "true", "yes", "on"
    ):
        return
    # 2. 只在 Termux 上有意义；标记文件已写过则跳过
    if not IS_TERMUX or os.path.exists(_HOLD_SENTINEL):
        return

    try:
        # 3. 官方包存在？
        r = _run(["dpkg-query", "-W", "-f=${Status}", _PKG])
        if r is None or r.returncode != 0 or "installed" not in (r.stdout or ""):
            _mark_done()
            return
        # 4. 已被 hold？
        r = _run(["apt-mark", "showhold"])
        if r is None or r.returncode != 0:
            _mark_done()
            return
        if _PKG in (r.stdout or "").split():
            _mark_done()
            return
        # 5. 当前入口是 Python 版（官方入口确实被覆盖）？
        if not _entry_is_python():
            _mark_done()
            return

        # 条件全满足：hold 官方包，防止 apt upgrade 覆盖 pip 版
        r = _run(["apt-mark", "hold", _PKG])
        if r is None or r.returncode != 0:
            _mark_done()
            return

        msg()
        msg(f"{C['YELLOW']}{_('Detected: this pip-installed {prog} build '
              'overwrites the official apt package entry point.', prog=_PKG)}"
            f"{C['RST']}")
        msg(f"{C['GREEN']}{_('Running: apt-mark hold {pkg} — so `apt upgrade` '
              'will not overwrite it with the official version.', pkg=_PKG)}"
            f"{C['RST']}")
        msg(_("To restore the official version later:"))
        msg(f"  {C['CYAN']}apt-mark unhold {_PKG} && "
            f"apt install --reinstall {_PKG}{C['RST']}")
        msg(_("(Set PD_NO_AUTO_HOLD=1 to disable this check.)"))
        msg()
    finally:
        _mark_done()


def _mark_done() -> None:
    """写"已检测"标记；失败静默（顶多下次再查一遍）。"""
    try:
        os.makedirs(RUNTIME_DIR, exist_ok=True)
        with open(_HOLD_SENTINEL, "w", encoding="utf-8") as fh:
            fh.write("checked\n")
    except OSError:
        pass
