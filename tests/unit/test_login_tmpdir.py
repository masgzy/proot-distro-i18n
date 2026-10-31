# Tests for the verified PROOT_TMP_DIR handoff: _resolve_proot_tmp_dir()
# and the env builders' export of it on Termux.
#
# proot reads PROOT_TMP_DIR (never TMPDIR) and nproot treats a set value
# as authoritative -- an unusable one fails the run.  The launcher must
# therefore validate before exporting, and fall back when the default
# location is missing or unwritable.

import os

import pytest

from proot_distro.commands import login
from proot_distro.commands.login import env as login_env

from _builders import simple_image_manifest


@pytest.fixture
def termux_paths(tmp_path, monkeypatch):
    """Point the login module's Termux constants at a scratch tree."""
    prefix = tmp_path / "usr"
    home = tmp_path / "home"
    prefix.mkdir()
    home.mkdir()
    monkeypatch.setattr(login, "TERMUX_PREFIX", str(prefix))
    monkeypatch.setattr(login, "TERMUX_HOME", str(home))
    monkeypatch.setattr(login, "IS_TERMUX", True)
    monkeypatch.setattr(login, "_PROOT_TMP_DIR", None)
    monkeypatch.delenv("PROOT_TMP_DIR", raising=False)
    return prefix, home


def _cache(value):
    """Pretend the resolver already ran, so builders reuse @value."""
    login._PROOT_TMP_DIR = value


_requires_user = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="permission-based cases mean nothing to root",
)


# --- _resolve_proot_tmp_dir --------------------------------------------------

def test_prefix_tmp_created_when_missing(termux_paths):
    prefix, _home = termux_paths
    assert login._resolve_proot_tmp_dir() == f"{prefix}/tmp"
    assert os.path.isdir(f"{prefix}/tmp")


def test_host_proot_tmp_dir_honored(termux_paths, tmp_path, monkeypatch):
    prefix, home = termux_paths
    host = tmp_path / "host-tmp"
    host.mkdir()
    monkeypatch.setenv("PROOT_TMP_DIR", str(host))
    assert login._resolve_proot_tmp_dir() == str(host)
    assert not (home / ".proot-distro").exists()


def test_missing_host_dir_is_created(termux_paths, tmp_path, monkeypatch):
    _prefix, home = termux_paths
    host = tmp_path / "host-new" / "tmp"
    monkeypatch.setenv("PROOT_TMP_DIR", str(host))
    assert login._resolve_proot_tmp_dir() == str(host)
    assert host.is_dir()
    assert not (home / ".proot-distro").exists()


@_requires_user
def test_unwritable_prefix_tmp_falls_back(termux_paths, capsys):
    prefix, home = termux_paths
    (prefix / "tmp").mkdir()
    os.chmod(prefix / "tmp", 0o500)
    try:
        assert login._resolve_proot_tmp_dir() == (
            str(home / ".proot-distro" / "tmp")
        )
        assert (home / ".proot-distro" / "tmp").is_dir()
    finally:
        os.chmod(prefix / "tmp", 0o700)
    assert "not writable" in capsys.readouterr().err


@_requires_user
def test_everything_unusable_answers_prefix_tmp(termux_paths):
    prefix, home = termux_paths
    os.chmod(prefix, 0o555)      # $PREFIX/tmp cannot be created
    os.chmod(home, 0o555)        # ~/.proot-distro/tmp cannot be created
    try:
        assert login._resolve_proot_tmp_dir() == f"{prefix}/tmp"
    finally:
        os.chmod(home, 0o755)
        os.chmod(prefix, 0o755)


def test_resolution_is_cached(termux_paths):
    prefix, _home = termux_paths
    first = login._resolve_proot_tmp_dir()
    # A stale looking env must not change the cached answer.
    os.environ["PROOT_TMP_DIR"] = "/somewhere-else"
    assert login._resolve_proot_tmp_dir() == first
    del os.environ["PROOT_TMP_DIR"]


# --- the builders export it --------------------------------------------------

def test_termux_env_exports_in_minimal_mode(builders, termux_paths):
    builders.make_container("box")
    _cache(str(termux_paths[0] / "tmp"))
    env = login._build_termux_env("box", [], minimal=True, isolated=False)
    assert "PROOT_TMP_DIR" in env


def test_normal_env_exports_on_termux(builders, termux_paths):
    builders.make_container("box")
    _cache(str(termux_paths[0] / "tmp"))
    env = login._build_normal_env("box", "root", "/root", [],
                                  minimal=False, isolated=False)
    assert "PROOT_TMP_DIR" in env


def test_normal_env_exports_in_minimal_mode(builders, termux_paths):
    builders.make_container("box")
    _cache(str(termux_paths[0] / "tmp"))
    env = login._build_normal_env("box", "root", "/root", [],
                                  minimal=True, isolated=False)
    assert "PROOT_TMP_DIR" in env


def test_no_export_off_termux(builders, tmp_path, monkeypatch):
    builders.make_container("box")
    monkeypatch.setattr(login, "IS_TERMUX", False)
    monkeypatch.setattr(login, "_PROOT_TMP_DIR", None)
    for builder in (login._build_termux_env,
                    login._build_normal_env):
        if builder is login._build_termux_env:
            env = builder("box", [], minimal=False, isolated=False)
        else:
            env = builder("box", "root", "/root", [],
                          minimal=False, isolated=False)
        assert "PROOT_TMP_DIR" not in env


def test_extra_env_wins_over_the_resolver(builders, termux_paths):
    builders.make_container("box")
    _cache(str(termux_paths[0] / "tmp"))
    env = login._build_termux_env("box", ["PROOT_TMP_DIR=/custom"],
                                  minimal=False, isolated=False)
    assert env["PROOT_TMP_DIR"] == "/custom"


def test_image_env_cannot_override_the_resolver(builders, termux_paths):
    # The location is the launcher's to vouch for; an image must not
    # point proot's temporary files (the extracted loader!) wherever it
    # likes.  IMAGE_ENV_BLOCKED is what enforces that.
    manifest = simple_image_manifest(env=["PROOT_TMP_DIR=/evil"])
    builders.make_container("box", manifest=manifest)
    _cache(str(termux_paths[0] / "tmp"))
    env = login._build_termux_env("box", [], minimal=False, isolated=False)
    assert env["PROOT_TMP_DIR"] == str(termux_paths[0] / "tmp")
    # ... because the PROOT_* family is refused from image Env wholesale.
    assert login_env.is_host_exec_var("PROOT_TMP_DIR")
