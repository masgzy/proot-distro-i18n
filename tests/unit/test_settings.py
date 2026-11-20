#
# Proot-Distro - manage proot containers.
#
# Tests for the `setting` subcommand and the settings registry it
# drives: boolean normalization, storage round-trips through
# config.json, reset scoping (language is never touched), the ls
# rendering, and the login-side passthrough consumer.
#

import json

import pytest

from proot_distro import config as config_mod
from proot_distro import settings
from proot_distro.commands import login as login_mod


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    """Redirect config.json into the test's tmp directory."""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(cfg_dir))
    monkeypatch.setattr(config_mod, "CONFIG_FILE", str(cfg_dir / "config.json"))
    return cfg_dir


# ---------------------------------------------------------------------------
# normalize_bool
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("TRUE", True), ("True", True),
    ("1", True), ("t", True), ("T", True),
    ("yes", True), ("YES", True), ("on", True), ("On", True),
    (" true ", True),
    ("false", False), ("FALSE", False),
    ("0", False), ("f", False), ("F", False),
    ("no", False), ("NO", False), ("off", False), ("OFF", False),
    (" false ", False),
])
def test_normalize_bool_accepts_the_usual_spellings(raw, expected):
    assert settings.normalize_bool(raw) is expected


@pytest.mark.parametrize("raw", ["", "2", "maybe", "y", "n", "yes!", "01"])
def test_normalize_bool_refuses_everything_else(raw):
    with pytest.raises(settings.SettingError):
        settings.normalize_bool(raw)


def test_unknown_setting_name_is_an_error():
    with pytest.raises(settings.SettingError):
        settings.normalize_value("nonexistent", "true")


# ---------------------------------------------------------------------------
# storage round-trips
# ---------------------------------------------------------------------------

def test_default_is_passthrough_on(isolated_config):
    assert settings.passthrough_enabled() is True
    assert not isolated_config.joinpath("config.json").exists()


def test_put_false_round_trip(isolated_config):
    settings.set_setting("passthrough", "false")
    assert settings.passthrough_enabled() is False
    stored = json.loads(isolated_config.joinpath("config.json").read_text())
    assert stored["passthrough"] is False  # a real JSON boolean


def test_put_normalizes_every_spelling(isolated_config):
    for raw, expect in [("F", False), ("1", True), ("OFF", False),
                        ("t", True), ("no", False), ("ON", True)]:
        settings.set_setting("passthrough", raw)
        assert settings.get_setting("passthrough") is expect


def test_hand_written_string_value_is_normalized(isolated_config):
    isolated_config.joinpath("config.json").write_text(
        json.dumps({"passthrough": "false"}))
    assert settings.passthrough_enabled() is False


def test_hand_written_junk_value_reads_as_default(isolated_config):
    # config.json reads never break the program: an unrecognizable
    # value is treated as unset and the default applies.
    isolated_config.joinpath("config.json").write_text(
        json.dumps({"passthrough": 42}))
    assert settings.passthrough_enabled() is True


def test_corrupted_config_reads_as_default(isolated_config):
    isolated_config.joinpath("config.json").write_text("{not json")
    assert settings.passthrough_enabled() is True


def test_language_key_is_never_touched_by_settings(isolated_config):
    config_mod.save_user_config({"language": "zh_CN"})
    settings.set_setting("passthrough", "false")
    assert config_mod.load_user_config()["language"] == "zh_CN"

    settings.reset_settings()  # everything this registry owns
    assert config_mod.load_user_config() == {"language": "zh_CN"}


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------

def test_reset_single_setting(isolated_config):
    settings.set_setting("passthrough", "false")
    assert settings.reset_settings("passthrough") == ["passthrough"]
    assert settings.passthrough_enabled() is True


def test_reset_unknown_name_is_an_error(isolated_config):
    with pytest.raises(settings.SettingError):
        settings.reset_settings("nonexistent")


def test_reset_all_when_nothing_stored(isolated_config):
    assert settings.reset_settings() == []


def test_reset_all_removes_every_owned_key(isolated_config):
    settings.set_setting("passthrough", "false")
    assert settings.reset_settings() == ["passthrough"]
    assert not isolated_config.joinpath("config.json").exists()


# ---------------------------------------------------------------------------
# list rendering rows
# ---------------------------------------------------------------------------

def test_list_settings_rows(isolated_config):
    rows = settings.list_settings()
    assert [r[0] for r in rows] == ["passthrough"]
    name, current, default, is_default, doc = rows[0]
    assert name == "passthrough"
    assert current is True and default is True and is_default is True
    assert doc

    settings.set_setting("passthrough", "false")
    name, current, default, is_default, _doc = settings.list_settings()[0]
    assert current is False and is_default is False


def test_get_setting_unknown_name_raises_keyerror():
    with pytest.raises(KeyError):
        settings.get_setting("nonexistent")


# ---------------------------------------------------------------------------
# the login-side consumer
# ---------------------------------------------------------------------------

TERMUX_BIN = "/data/data/com.termux/files/usr/bin"


def _with_passthrough(monkeypatch, value):
    monkeypatch.setattr(settings, "passthrough_enabled", lambda: value)


def test_compose_path_appends_termux_bin_by_default(monkeypatch):
    _with_passthrough(monkeypatch, True)
    assert login_mod._compose_guest_path(
        "/usr/bin:/bin", "normal") == f"/usr/bin:/bin:{TERMUX_BIN}"


def test_compose_path_dedupes_and_moves_termux_bin_last(monkeypatch):
    # DEFAULT_PATH_ENV carries the Termux bin mid-list; one occurrence,
    # at the end, is the answer for every shape of input.
    _with_passthrough(monkeypatch, True)
    cases = {
        f"/x:{TERMUX_BIN}:/y": f"/x:/y:{TERMUX_BIN}",
        f"{TERMUX_BIN}": TERMUX_BIN,
        "/x::/y": f"/x:/y:{TERMUX_BIN}",
    }
    for path, expected in cases.items():
        assert login_mod._compose_guest_path(path, "normal") == expected


def test_compose_path_off_strips_termux_bin(monkeypatch):
    _with_passthrough(monkeypatch, False)
    assert login_mod._compose_guest_path(
        f"/x:{TERMUX_BIN}:/y", "normal") == "/x:/y"


def test_compose_path_off_keeps_termux_type_container(monkeypatch):
    # A Termux image IS the Termux prefix; stripping would leave the
    # guest no PATH at all.
    _with_passthrough(monkeypatch, False)
    assert login_mod._compose_guest_path(
        TERMUX_BIN, "termux") == TERMUX_BIN


def test_compose_path_off_leaves_empty_but_not_broken(monkeypatch):
    _with_passthrough(monkeypatch, False)
    assert login_mod._compose_guest_path(TERMUX_BIN, "normal") == ""
