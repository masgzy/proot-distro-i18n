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

import json
import os

from proot_distro.constants import IS_TERMUX, PROGRAM_NAME, TERMUX_PREFIX

__all__ = (
    "CONFIG_DIR", "CONFIG_FILE",
    "load_user_config", "save_user_config", "clear_user_key",
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


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------

def load_user_config() -> dict:
    """读整个 config.json；缺失/损坏/类型不对一律返回 {}。

    每次调用都重新读盘——config 文件极小、调用频率极低（命令入口、
    ``lang`` 子命令），不值得缓存；不缓存也让 ``lang use`` 后同一
    进程内立即可见。
    """
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# 写
# ---------------------------------------------------------------------------

def save_user_config(update: dict, *, remove_keys: tuple = ()) -> dict:
    """部分更新 config.json（merge 语义），返回更新后的完整配置。

    *update* 中的键覆盖旧值（值为 None 的键会被删除——与
    ``remove_keys`` 效果相同，方便调用方直接传 None 表达"清除"）；
    其他已有键原样保留。目录不存在时自动创建。
    """
    data = load_user_config()
    for key, value in update.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    for key in remove_keys:
        data.pop(key, None)

    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp_path = CONFIG_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp_path, CONFIG_FILE)
    return data


def clear_user_key(key: str) -> bool:
    """删除配置里的单个键；返回是否真的存在（并删除了）。

    写回后如果配置变空则顺手删掉整个文件，保持配置目录整洁——
    与 ``lang reset`` 的"清除偏好"语义匹配。
    """
    data = load_user_config()
    if key not in data:
        return False
    del data[key]
    if data:
        save_user_config(data)
    else:
        try:
            os.remove(CONFIG_FILE)
        except OSError:
            pass
    return True
