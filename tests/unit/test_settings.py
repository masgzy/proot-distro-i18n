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

def test_default_is_passthrough_off(isolated_config):
    # Default off since 5.10.3: a container keeps itself to itself,
    # and a user who wants Termux binaries on the guest PATH opts in.
    assert settings.passthrough_enabled() is False
    assert not isolated_config.joinpath("config.json").exists()


def test_put_true_round_trip(isolated_config):
    settings.set_setting("passthrough", "true")
    assert settings.passthrough_enabled() is True
    stored = json.loads(isolated_config.joinpath("config.json").read_text())
    assert stored["passthrough"] is True  # a real JSON boolean


def test_put_normalizes_every_spelling(isolated_config):
    for raw, expect in [("F", False), ("1", True), ("OFF", False),
                        ("t", True), ("no", False), ("ON", True)]:
        settings.set_setting("passthrough", raw)
        assert settings.get_setting("passthrough") is expect


def test_hand_written_string_value_is_normalized(isolated_config):
    isolated_config.joinpath("config.json").write_text(
        json.dumps({"passthrough": "true"}))
    assert settings.passthrough_enabled() is True


def test_hand_written_junk_value_reads_as_default(isolated_config):
    # config.json reads never break the program: an unrecognizable
    # value is treated as unset and the default applies.
    isolated_config.joinpath("config.json").write_text(
        json.dumps({"passthrough": 42}))
    assert settings.passthrough_enabled() is False


def test_corrupted_config_reads_as_default(isolated_config):
    isolated_config.joinpath("config.json").write_text("{not json")
    assert settings.passthrough_enabled() is False


def test_language_key_is_never_touched_by_settings(isolated_config):
    config_mod.save_user_config({"language": "zh_CN"})
    settings.set_setting("passthrough", "true")
    assert config_mod.load_user_config()["language"] == "zh_CN"

    settings.reset_settings()  # everything this registry owns
    assert config_mod.load_user_config() == {"language": "zh_CN"}


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------

def test_reset_single_setting(isolated_config):
    settings.set_setting("passthrough", "true")
    assert settings.reset_settings("passthrough") == ["passthrough"]
    assert settings.passthrough_enabled() is False


def test_reset_unknown_name_is_an_error(isolated_config):
    with pytest.raises(settings.SettingError):
        settings.reset_settings("nonexistent")


def test_reset_all_when_nothing_stored(isolated_config):
    assert settings.reset_settings() == []


def test_reset_all_removes_every_owned_key(isolated_config):
    settings.set_setting("passthrough", "true")
    settings.set_setting("distro_mirror", "true")
    settings.set_setting("dns", "223.5.5.5,119.29.29.29")
    removed = settings.reset_settings()
    assert removed == ["passthrough", "distro_mirror", "dns"]
    assert not isolated_config.joinpath("config.json").exists()


# ---------------------------------------------------------------------------
# list rendering rows
# ---------------------------------------------------------------------------

def test_list_settings_rows(isolated_config):
    rows = settings.list_settings()
    assert [r[0] for r in rows] == ["passthrough", "distro_mirror", "dns"]
    name, current, default, is_default, doc = rows[0]
    assert name == "passthrough"
    assert current is False and default is False and is_default is True
    assert doc

    # dns：auto 跟随宿主；可固定为最多 3 个 IP。
    _name, current, default, is_default, doc = rows[2]
    assert _name == "dns"
    assert current == "auto" and default == "auto" and is_default is True

    settings.set_setting("passthrough", "true")
    name, current, default, is_default, _doc = settings.list_settings()[0]
    assert current is True and is_default is False


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


# ---------------------------------------------------------------------------
# the dns setting (ip_list kind)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("auto", "auto"),
    ("AUTO", "auto"),
    ("default", "auto"),
    ("host", "auto"),
    ("223.5.5.5", ["223.5.5.5"]),
    ("223.5.5.5,119.29.29.29", ["223.5.5.5", "119.29.29.29"]),
    ("223.5.5.5 119.29.29.29", ["223.5.5.5", "119.29.29.29"]),
    (" 223.5.5.5 , 119.29.29.29 ", ["223.5.5.5", "119.29.29.29"]),
    ("223.5.5.5,223.5.5.5", ["223.5.5.5"]),          # dedupe
    ("2001:4860:4860::8888", ["2001:4860:4860::8888"]),
    ("1.1.1.1,2.2.2.2,3.3.3.3", ["1.1.1.1", "2.2.2.2", "3.3.3.3"]),
])
def test_normalize_dns_accepts_ips_and_auto(raw, expected):
    assert settings.normalize_ip_list(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "not-an-ip", "223.5.5", "1.1.1.1,banana",
    "1.1.1.1,2.2.2.2,3.3.3.3,4.4.4.4",   # 第 4 个：musl/glibc 都只读 3 个
    "999.999.999.999",
])
def test_normalize_dns_refuses_junk(raw):
    with pytest.raises(settings.SettingError):
        settings.normalize_ip_list(raw)


def test_dns_setting_roundtrip(isolated_config):
    assert settings.set_setting("dns", "223.5.5.5,119.29.29.29") == [
        "223.5.5.5", "119.29.29.29"]
    assert settings.get_setting("dns") == ["223.5.5.5", "119.29.29.29"]
    settings.set_setting("dns", "auto")
    assert settings.get_setting("dns") == "auto"
    assert settings.reset_settings("dns") == ["dns"]
    assert settings.get_setting("dns") == "auto"


def test_dns_stored_junk_reads_as_unset(isolated_config):
    # 手改坏的 config.json 读作未设置，绝不炸读路径。
    config_mod.save_user_config({"dns": "still not an ip"})
    assert settings.get_setting("dns") == "auto"
    config_mod.save_user_config({"dns": ["1.2.3.4", "banana"]})
    assert settings.get_setting("dns") == "auto"
