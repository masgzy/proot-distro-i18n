#
# Proot-Distro - manage proot containers.
#
# Tests for the user-level config fallback: on Termux the primary config
# directory ($PREFIX/etc/proot-distro) can be unwritable (pip-installed pd,
# root-owned leftovers, read-only overlay) — the layout that killed
# `setting put distro_mirror 1` with Errno 5 on a real device. Writes must
# fall back to ~/.config/proot-distro/config.json; reads must consult both.
#

import os

import pytest

from proot_distro import config as config_mod


@pytest.fixture
def termux_layout(monkeypatch, tmp_path):
    """A Termux-shaped pair of config paths, primary unwritable.

    The primary's parent path is occupied by a regular file, so the
    O_NOFOLLOW descent into the "directory" fails — exactly what a
    root-owned or otherwise unwritable $PREFIX/etc/proot-distro looks
    like to the hardened I/O layer.
    """
    home = tmp_path / "home"
    usr_etc = tmp_path / "usr" / "etc"
    home.mkdir()
    usr_etc.mkdir(parents=True)
    primary = usr_etc / "proot-distro"
    primary.write_text("root-owned artifact, not a directory\n")

    fallback = home / ".config" / "proot-distro" / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(primary))
    monkeypatch.setattr(
        config_mod, "CONFIG_FILE", str(primary / "config.json"))
    monkeypatch.setattr(
        config_mod, "FALLBACK_CONFIG_FILE", str(fallback))
    return {"primary": str(primary / "config.json"),
            "fallback": str(fallback), "home": str(home)}


def test_probe_reports_an_unwritable_primary(termux_layout):
    assert config_mod._config_writable(config_mod.CONFIG_FILE) is False


def test_effective_config_file_falls_back(termux_layout):
    assert config_mod.effective_config_file() == \
        config_mod.FALLBACK_CONFIG_FILE


def test_effective_config_file_prefers_a_writable_primary(
        monkeypatch, tmp_path):
    primary = tmp_path / "cfg"
    primary.mkdir()
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(primary))
    monkeypatch.setattr(
        config_mod, "CONFIG_FILE", str(primary / "config.json"))
    monkeypatch.setattr(
        config_mod, "FALLBACK_CONFIG_FILE",
        str(tmp_path / "fb" / "config.json"))
    assert config_mod.effective_config_file() == \
        config_mod.CONFIG_FILE


def test_save_user_config_does_not_crash_on_dead_primary(termux_layout):
    # The on-device bug: this call raised OSError [Errno 5].
    data = config_mod.save_user_config({"distro_mirror": True})
    assert data == {"distro_mirror": True}
    assert os.path.exists(config_mod.FALLBACK_CONFIG_FILE)


def test_save_merges_keys_across_the_fallback(termux_layout):
    config_mod.save_user_config({"distro_mirror": True})
    data = config_mod.save_user_config({"language": "zh_CN"})
    assert data == {"distro_mirror": True, "language": "zh_CN"}
    stored = config_mod.read_json_in_config(
        config_mod.FALLBACK_CONFIG_FILE)
    assert stored["language"] == "zh_CN"


def test_reads_consult_primary_then_fallback(termux_layout):
    # A config that exists only at the (unwritable) primary must still
    # be read: the primary may be readable but not writable.
    import json
    os.unlink(config_mod.CONFIG_DIR)
    os.mkdir(config_mod.CONFIG_DIR)
    with open(config_mod.CONFIG_FILE, "w") as fh:
        json.dump({"language": "en"}, fh)
    # Make it unwritable to keep the fallback active for writes.
    os.chmod(config_mod.CONFIG_FILE, 0o444)
    os.chmod(config_mod.CONFIG_DIR, 0o555)
    try:
        # Read prefers the primary's answer.
        assert config_mod.load_user_config() == {"language": "en"}
    finally:
        os.chmod(config_mod.CONFIG_DIR, 0o755)
        os.chmod(config_mod.CONFIG_FILE, 0o644)
