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

1.  语言来源优先级链（高 → 低）：

    - ``--lang`` 命令行标志 —— 单次命令的临时覆盖（``cli.main`` 解析后
      调 ``set_language``）；
    - ``PROOT_DISTRO_LANG`` 环境变量 —— 会话级 / 脚本级覆盖；
    - ``PD_LANG`` 环境变量 —— 历史兼容开关，语义同上；
    - 配置文件（``config.json`` 的 ``language`` 键，见 ``lang use``）；
    - 内置默认 ``zh_CN``（本 fork 定位中文用户；Termux 的 locale 机制
      残缺，无法自动感知系统语言，故不做 ``LC_*`` 探测）。

    任何一级的值无法识别时跳过该级、继续向下探测。

2.  翻译以**纯 JSON** 分发：``locales/<locale>.json`` 是 ``{原文: 译文}``
    的扁平字典，是唯一的翻译源文件——不需要 gettext 编译步骤，直接
    编辑、直接生效、直接分发。``en`` 不需要 JSON 文件（源码字符串
    本身就是英文，直通）。

3.  拿不到 JSON（如翻译文件被裁剪）不会抛错——``_()`` 直接返回原
    字符串。开发期零摩擦。

4.  为避免每个模块都写一遍 ``import gettext; _ = ...``，本模块导出
    一个可直接使用的 ``_`` 函数；所有模块从 ``proot_distro.i18n``
    导入即可。

5.  为保持向后兼容，未翻译的字符串默认就是英文（即源码里仍写
    英文字面量，只是在外包一层 ``_()``），这样 fallback 路径不需
    要任何特殊处理。
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

__all__ = (
    "_", "set_language", "get_language", "get_language_source",
    "detect_language", "normalize_locale", "AVAILABLE_LANGUAGES",
    "DEFAULT_LANGUAGE", "N_",
)

# ---------------------------------------------------------------------------
# 语言注册表
# ---------------------------------------------------------------------------

# 内置默认语言。本 fork 面向中文用户，Termux 无法感知系统 locale，
# 因此默认中文；要英文请 `lang use en`、`--lang en` 或 PD_LANG=en。
DEFAULT_LANGUAGE = "zh_CN"

# 已内置翻译的语言。新增语言 = 在 locales/ 下放 <locale>.json 并在这里
# 登记一行；`lang list` / `lang use` 直接以此为准。
AVAILABLE_LANGUAGES: dict = {
    "en": {
        "name": "English",
        "note": "源语言（直通，无翻译文件）",
    },
    "zh_CN": {
        "name": "简体中文",
        "note": "Chinese (Simplified)",
    },
}

# ---------------------------------------------------------------------------
# locale 规范化
# ---------------------------------------------------------------------------

# 这些值全部视为英文（含 locale 常见的 C/POSIX 与 en 变体）。
_EN_ALIASES = frozenset({"C", "POSIX", "ENGLISH"})


def normalize_locale(value: str | None) -> str | None:
    """把用户输入的 locale 规范化为受支持的语言代码。

    ``zh`` / ``zh-CN`` / ``zh_CN.UTF-8`` / ``zh_CN@pinyin`` 等一律映射到
    ``zh_CN``；``C`` / ``POSIX`` / ``en`` / ``en_US`` 等映射到 ``en``。
    返回 ``AVAILABLE_LANGUAGES`` 中的代码；无法识别返回 ``None``。
    """
    if not value:
        return None
    head = str(value).strip().split(".", 1)[0]   # 去 .UTF-8 等编码后缀
    head = head.split("@", 1)[0]                 # 去 @euro 等修饰符
    head = head.strip().replace("-", "_")
    if not head:
        return None
    upper = head.upper()
    if upper in _EN_ALIASES or upper.startswith("EN"):
        return "en"
    if upper.startswith("ZH"):
        # zh_TW / zh_HK 等暂无繁体翻译，统一映射简体
        return "zh_CN"
    return head if head in AVAILABLE_LANGUAGES else None


# ---------------------------------------------------------------------------
# 语言来源探测
# ---------------------------------------------------------------------------

# 来源 token：--lang 标志 / 两个环境变量 / 配置文件 / 内置默认。
SOURCE_FLAG = "--lang"
SOURCE_ENV_PD = "PROOT_DISTRO_LANG"
SOURCE_ENV_LEGACY = "PD_LANG"
SOURCE_CONFIG = "config"
SOURCE_DEFAULT = "default"

# 环境变量探测顺序：PROOT_DISTRO_LANG 优先于历史兼容的 PD_LANG。
_ENV_VARS = (SOURCE_ENV_PD, SOURCE_ENV_LEGACY)


def _config_language() -> str | None:
    """读配置文件里的语言偏好；没有或无效返回 None。

    延迟导入 config 模块：config 不依赖 i18n，无循环；延迟是为了让
    单元测试可以方便地 monkeypatch 配置读取。
    """
    try:
        from proot_distro.config import load_user_config
    except Exception:
        return None
    lang = load_user_config().get("language")
    if isinstance(lang, str):
        return normalize_locale(lang)
    return None


def detect_language() -> tuple:
    """按优先级链探测语言，返回 ``(lang, source)``。

    纯探测函数，不修改任何全局状态。任何一级的值无法识别时跳过
    该级继续向下；全链无效时落到 ``DEFAULT_LANGUAGE``。
    """
    for var in _ENV_VARS:
        lang = normalize_locale(os.environ.get(var))
        if lang:
            return lang, var
    lang = _config_language()
    if lang:
        return lang, SOURCE_CONFIG
    return DEFAULT_LANGUAGE, SOURCE_DEFAULT


# 当前激活的语言代码与来源
_current_lang: str = DEFAULT_LANGUAGE
_current_source: str = SOURCE_DEFAULT


# ---------------------------------------------------------------------------
# 翻译加载（JSON）
# ---------------------------------------------------------------------------

# locales 目录：相对于本文件所在目录
_LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")

# 已加载的翻译表缓存：{lang: dict}
_translation_cache: dict = {}


def _load_translation(lang: str) -> dict:
    """加载指定语言的翻译表（扁平 dict）；找不到返回空表。

    空表意味着 ``_()`` 直通返回原字符串——与 gettext 的
    NullTranslations 行为一致，缺翻译不会抛错。
    """
    if lang == "en":
        return {}  # 源语言直通，无需查表
    if lang in _translation_cache:
        return _translation_cache[lang]
    table: dict = {}
    path = os.path.join(_LOCALE_DIR, f"{lang}.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            table = data
    except (OSError, ValueError):
        pass  # 文件不存在或损坏：直通
    _translation_cache[lang] = table
    return table


def set_language(lang: str | None = None, source: str | None = None) -> str:
    """切换当前语言并立即生效。

    *lang* 为 ``None`` 时重新按优先级链探测。*source* 记录语言来源
    token（供 ``lang get`` 显示）；``lang use`` 在写入配置文件后也会
    用它让当前进程立即切换。返回切换后的语言代码。
    """
    global _current_lang, _current_source
    if lang:
        normalized = normalize_locale(lang)
        if normalized is None:
            normalized = DEFAULT_LANGUAGE
            source = source or SOURCE_DEFAULT
        _current_lang = normalized
        _current_source = source or SOURCE_DEFAULT
    else:
        _current_lang, _current_source = detect_language()
    # 两个缓存都清：dict 缓存 + _table 的 lru_cache（否则热路径上
    # 拿到的还是旧表的引用，语言切换不生效）
    _translation_cache.clear()
    _table.cache_clear()
    return _current_lang


def get_language() -> str:
    """返回当前激活的语言代码（如 ``'zh_CN'`` 或 ``'en'``）。"""
    return _current_lang


def get_language_source() -> str:
    """返回当前语言的来源 token（``--lang``/``PROOT_DISTRO_LANG``/...）。"""
    return _current_source


@lru_cache(maxsize=1)
def _table() -> dict:
    """返回当前语言的翻译表；用 lru_cache 避免热路径全局变量查找。

    ``set_language()`` 会 ``_translation_cache.clear()``——但本函数的
    lru_cache 也要一起清，所以 set_language 里同时调用
    ``_table.cache_clear()``。
    """
    return _load_translation(_current_lang)


def _(s: str, *args, **kwargs) -> str:
    """翻译字符串。

    用法::

        from proot_distro.i18n import _
        msg(_("Hello, {name}!").format(name=name))

    支持 ``str.format`` 风格的占位符。如果 *s* 不在翻译表中，
    直接返回 *s* 本身（与 gettext NullTranslations 行为一致）。

    格式化带安全网：译文里若混入了无法匹配的占位符（手工编辑时
    多写了一个 ``{`` 之类），``str.format`` 抛出的 KeyError 会让整
    条命令崩掉——这里拦住并退回未格式化的译文，坏占位符顶多显示
    出来，不致命。
    """
    if not s:
        return s
    translated = _table().get(s, s)
    if args or kwargs:
        try:
            return translated.format(*args, **kwargs)
        except (KeyError, IndexError, ValueError):
            return translated
    return translated


def N_(s: str) -> str:
    """仅标记字符串可翻译、但不立即翻译。

    用于在模块顶层声明 msgid，让扫描脚本能扫到，但实际翻译时机由
    调用方决定（典型场景：dict 字面量）。
    """
    return s


# 模块导入时立即初始化一次（按优先级链探测 env > config > 默认；
# --lang 标志由 cli.main() 在参数解析后通过 set_language 覆盖）
set_language()
