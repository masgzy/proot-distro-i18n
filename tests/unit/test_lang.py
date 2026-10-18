#
# Proot-Distro - manage proot containers.
#
# Tests for the `lang` subcommand, the JSON translation loader, the
# language-source priority chain and the user-config module.
#

import importlib
import json
import os

import pytest

from proot_distro import config as config_mod
from proot_distro import i18n


@pytest.fixture(autouse=True)
def _restore_language():
    """本文件的用例会切换 i18n 全局状态——结束后一律恢复。

    i18n 的语言是进程级全局（conftest 固定 PD_LANG=en），若测试把
    它切成 zh_CN 不还原，后续模块的英文断言会连锁失败。
    """
    before = (i18n.get_language(), i18n.get_language_source())
    yield
    i18n.set_language(before[0], before[1])


# ---------------------------------------------------------------------------
# normalize_locale
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("zh", "zh_CN"),
    ("zh-CN", "zh_CN"),
    ("zh_CN", "zh_CN"),
    ("zh_CN.UTF-8", "zh_CN"),
    ("ZH", "zh_CN"),
    ("zh_TW", "zh_CN"),          # 暂无繁体翻译，统一简体
    ("en", "en"),
    ("en_US", "en"),
    ("en-GB.UTF-8", "en"),
    ("C", "en"),
    ("POSIX", "en"),
    ("", None),
    (None, None),
    ("ja", None),                # 未支持
    ("fr_FR", None),
])
def test_normalize_locale(raw, expected):
    assert i18n.normalize_locale(raw) == expected


# ---------------------------------------------------------------------------
# 优先级链
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_lang_env(monkeypatch, tmp_path):
    """清掉两级 env、重定向 config，返回 i18n 模块（重载以重置全局态）。"""
    for var in ("PROOT_DISTRO_LANG", "PD_LANG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(config_mod, "CONFIG_FILE",
                        str(tmp_path / "cfg" / "config.json"))
    return i18n


def _detect(clean_lang_env):
    return i18n.detect_language()


def test_priority_default(clean_lang_env):
    assert _detect(clean_lang_env) == ("zh_CN", i18n.SOURCE_DEFAULT)


def test_priority_config(clean_lang_env):
    os.makedirs(config_mod.CONFIG_DIR)
    with open(config_mod.CONFIG_FILE, "w") as fh:
        json.dump({"language": "en"}, fh)
    assert _detect(clean_lang_env) == ("en", i18n.SOURCE_CONFIG)


def test_priority_config_invalid_falls_through(clean_lang_env):
    os.makedirs(config_mod.CONFIG_DIR)
    with open(config_mod.CONFIG_FILE, "w") as fh:
        json.dump({"language": "ja"}, fh)   # 不支持 → 跳过该级
    assert _detect(clean_lang_env) == ("zh_CN", i18n.SOURCE_DEFAULT)


def test_priority_pd_lang_beats_config(clean_lang_env):
    os.makedirs(config_mod.CONFIG_DIR)
    with open(config_mod.CONFIG_FILE, "w") as fh:
        json.dump({"language": "en"}, fh)
    monkey = pytest.MonkeyPatch()
    monkey.setenv("PD_LANG", "zh")
    try:
        assert _detect(clean_lang_env) == ("zh_CN", i18n.SOURCE_ENV_LEGACY)
    finally:
        monkey.undo()


def test_priority_pdl_env_beats_pd_lang(clean_lang_env, monkeypatch):
    monkeypatch.setenv("PD_LANG", "en")
    monkeypatch.setenv("PROOT_DISTRO_LANG", "zh_CN")
    assert _detect(clean_lang_env) == ("zh_CN", i18n.SOURCE_ENV_PD)


def test_priority_invalid_env_falls_through(clean_lang_env, monkeypatch):
    monkeypatch.setenv("PROOT_DISTRO_LANG", "ja")   # 无效 → 跳过
    monkeypatch.setenv("PD_LANG", "en")
    assert _detect(clean_lang_env) == ("en", i18n.SOURCE_ENV_LEGACY)


def test_invalid_env_only_falls_to_default(clean_lang_env, monkeypatch):
    monkeypatch.setenv("PROOT_DISTRO_LANG", "xx")
    assert _detect(clean_lang_env) == ("zh_CN", i18n.SOURCE_DEFAULT)


# ---------------------------------------------------------------------------
# config.py
# ---------------------------------------------------------------------------

def test_load_missing_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_FILE", str(tmp_path / "x.json"))
    assert config_mod.load_user_config() == {}


def test_save_merge_preserves_other_keys(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    cfg_file = cfg / "config.json"
    cfg_file.write_text(json.dumps({"other_key": [1, 2]}), encoding="utf-8")
    monkeypatch.setattr(config_mod, "CONFIG_FILE", str(cfg_file))

    data = config_mod.save_user_config({"language": "en"})
    assert data == {"other_key": [1, 2], "language": "en"}
    on_disk = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert on_disk["other_key"] == [1, 2]


def test_save_none_value_removes_key(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"language": "en", "keep": 1}))
    monkeypatch.setattr(config_mod, "CONFIG_FILE", str(cfg_file))
    data = config_mod.save_user_config({"language": None})
    assert data == {"keep": 1}


def test_clear_user_key_removes_file_when_empty(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"language": "en"}))
    monkeypatch.setattr(config_mod, "CONFIG_FILE", str(cfg_file))
    assert config_mod.clear_user_key("language") is True
    assert not cfg_file.exists()          # 变空顺手删文件
    assert config_mod.clear_user_key("language") is False


def test_broken_config_is_ignored(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text("{not json")
    monkeypatch.setattr(config_mod, "CONFIG_FILE", str(cfg_file))
    assert config_mod.load_user_config() == {}


# ---------------------------------------------------------------------------
# JSON 翻译表加载
# ---------------------------------------------------------------------------

def test_translation_table_zh(clean_lang_env):
    table = i18n._load_translation("zh_CN")
    assert isinstance(table, dict) and len(table) > 200
    # 抽一条已知翻译
    assert table.get("Adding: '{arc}'") == "添加: '{arc}'"


def test_translation_en_is_passthrough(clean_lang_env):
    assert i18n._load_translation("en") == {}
    i18n.set_language("en")
    assert i18n._("Adding: '{arc}'") == "Adding: '{arc}'"


def test_set_language_switches_and_caches(clean_lang_env):
    i18n.set_language("en")
    assert i18n._("Adding: '{arc}'") == "Adding: '{arc}'"
    assert i18n.get_language() == "en"
    assert i18n.get_language_source() == i18n.SOURCE_DEFAULT

    i18n.set_language("zh_CN", i18n.SOURCE_CONFIG)
    assert i18n._("Adding: '{arc}'") == "添加: '{arc}'"
    assert i18n.get_language_source() == i18n.SOURCE_CONFIG


def test_set_language_unknown_falls_to_default(clean_lang_env):
    i18n.set_language("qq_Nonsense")
    assert i18n.get_language() == i18n.DEFAULT_LANGUAGE


def test_format_args_still_work(clean_lang_env):
    i18n.set_language("zh_CN")
    assert i18n._("Adding: '{arc}'", arc="alpine") == "添加: 'alpine'"


# ---------------------------------------------------------------------------
# --lang 解析（argparse 注入）
# ---------------------------------------------------------------------------

def test_lang_flag_top_level_and_after_command():
    from proot_distro.parser import build_parser

    for argv in (["--lang", "en", "lang", "get"],
                 ["lang", "get", "--lang", "en"],
                 ["--lang=en", "lang", "get"]):
        args, _ = build_parser().parse_known_args(argv)
        assert getattr(args, "cli_lang", None) == "en"
        assert args.command == "lang"


def test_lang_flag_absent_not_in_namespace():
    from proot_distro.parser import build_parser
    args, _ = build_parser().parse_known_args(["lang", "get"])
    assert getattr(args, "cli_lang", None) is None


def test_lang_action_choices():
    from proot_distro.parser import build_parser
    parser = build_parser()
    for action, locale in (("get", None), ("use", "en"), ("list", None),
                           ("reset", None)):
        argv = ["lang"] + ([action] if action else [])
        if locale:
            argv.append(locale)
        args, _ = parser.parse_known_args(argv)
        assert args.lang_action == action
        assert getattr(args, "locale", None) == locale
