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

"""国际化 (i18n) 基础设施。

设计要点
========

1.  默认中文 (zh_CN)，通过环境变量 ``LANG`` / ``LC_ALL`` /
    ``LC_MESSAGES`` / ``PD_LANG`` 回退英文。任何以 ``C`` 或 ``POSIX``
    开头的 locale，或显式 ``PD_LANG=en``，都会切到英文。

2.  翻译以 GNU gettext 形式分发：``locales/zh_CN/LC_MESSAGES/
    proot_distro.mo`` 是编译产物；同目录的 ``.po`` 是源文件。

3.  在打包环境下若拿不到 ``.mo``（如直接 ``python -m`` 运行源码），
    不会抛错——``_()`` 直接返回原字符串。这样开发期零摩擦。

4.  为避免每个模块都写一遍 ``import gettext; _ = ...``，本模块导出
    一个可直接使用的 ``_`` 函数；所有模块从 ``proot_distro.i18n``
    导入即可。

5.  为保持向后兼容，未翻译的字符串默认就是英文（即源码里仍写
    英文字面量，只是在外包一层 ``_()``），这样 fallback 路径不需
    要任何特殊处理。
"""

from __future__ import annotations

import gettext
import os
from functools import lru_cache

__all__ = ("_", "set_language", "get_language", "N_")

# 翻译域名
_DOMAIN = "proot_distro"

# Locales 目录：相对于本文件所在目录
_LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")


def _detect_language() -> str:
    """探测目标语言。

    策略（v7 调整，适配 Termux 无 GNU locale 支持的环境）：

    1.  ``PD_LANG`` —— **唯一**的环境变量开关。
        - ``PD_LANG=en`` / ``PD_LANG=C`` / ``PD_LANG=POSIX`` → 英文
        - ``PD_LANG=zh`` / ``PD_LANG=zh_CN`` / 任何含 ``zh`` 的值 → 中文
        - 其他值 → 英文（兼容性，便于未来支持更多语言）
    2.  默认 ``zh_CN``（中文）—— **不**再检查 ``LC_ALL`` / ``LC_MESSAGES`` /
        ``LANG``，因为 Termux/Android 上这些变量要么未设、要么是
        ``C``，不可靠。用户要切英文请显式设 ``PD_LANG=en``。

    历史：v1-v6 会按 ``PD_LANG > LC_ALL > LC_MESSAGES > LANG`` 探测，
    导致 Termux 默认环境（``LANG=C``）下显示英文。v7 改为只看
    ``PD_LANG``，默认中文。
    """
    val = os.environ.get("PD_LANG")
    if val:
        head = val.split(".", 1)[0].strip()
        if head in ("C", "POSIX", "en", "en_US", "en_GB"):
            return "en"
        # zh/zh_CN/zh_TW/zh_CN.UTF-8 等都映射到 zh_CN
        if head.lower().startswith("zh"):
            return "zh_CN"
        # 其他值（如 ja, ko, fr）暂不支持，回退英文
        return "en"
    # 默认中文 —— 不再检查 LC_*/LANG
    return "zh_CN"


# 当前激活的语言代码
_current_lang: str = _detect_language()

# 全局翻译对象；None 表示直通（不翻译）
_translation: gettext.NullTranslations | None = None


def _load_translation(lang: str) -> gettext.NullTranslations:
    """加载指定语言的翻译；找不到则返回 NullTranslations。

    使用 ``NullTranslations`` 而非 ``GNUTranslations``，是因为前者
    在缺翻译时直接返回原串、不会抛 KeyError，开发期更稳。
    """
    if lang == "en" or not os.path.isdir(_LOCALE_DIR):
        return gettext.NullTranslations()
    try:
        return gettext.translation(
            _DOMAIN, localedir=_LOCALE_DIR, languages=[lang], fallback=True,
        )
    except (OSError, FileNotFoundError):
        return gettext.NullTranslations()


def set_language(lang: str | None = None) -> str:
    """切换当前语言并立即生效。

    *lang* 为 ``None`` 时重新探测环境变量。返回切换后的语言代码。

    后续所有 ``_()`` 调用都会走新翻译——这是通过让 ``_()`` 透过
    ``lru_cache`` 包装的 getter 间接拿到当前 translation 实现的。
    """
    global _current_lang, _translation
    _current_lang = lang if lang else _detect_language()
    _translation = _load_translation(_current_lang)
    # 清缓存，让 _() 立刻看到新翻译
    _gettext_cached.cache_clear()
    return _current_lang


def get_language() -> str:
    """返回当前激活的语言代码（如 ``'zh_CN'`` 或 ``'en'``）。"""
    return _current_lang


@lru_cache(maxsize=1)
def _gettext_cached() -> gettext.NullTranslations:
    """返回当前翻译对象；用 lru_cache 避免每次 ``_()`` 都查全局变量。

    在 ``set_language()`` 时会 ``cache_clear()``。
    """
    global _translation
    if _translation is None:
        _translation = _load_translation(_current_lang)
    return _translation


def _(s: str, *args, **kwargs) -> str:
    """翻译字符串。

    用法::

        from proot_distro.i18n import _
        msg(_("Hello, {name}!").format(name=name))

    支持 ``str.format`` 风格的占位符。如果 *s* 不在翻译表中，
    直接返回 *s* 本身（NullTranslations 行为）。

    为了避免热路径上每次都做属性查找，这里用一次 ``lru_cache`` 间接
    拿到 translation 对象再调 ``gettext()``。
    """
    if not s:
        return s
    t = _gettext_cached()
    translated = t.gettext(s) if t is not None else s
    if args or kwargs:
        return translated.format(*args, **kwargs)
    return translated


def N_(s: str) -> str:
    """仅标记字符串可翻译、但不立即翻译。

    用于在模块顶层声明 msgid，让 ``xgettext`` / ``pybabel extract``
    能扫到，但实际翻译时机由调用方决定（典型场景：dict 字面量）。
    """
    return s


# 模块导入时立即初始化一次
set_language(_current_lang)
