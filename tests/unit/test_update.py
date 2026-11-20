#
# Proot-Distro - manage proot containers.
#
# Tests for the `update` subcommand: version parsing, release-asset
# selection for both targets, the whitelist-checked archive extractor
# (path traversal, symlinks, unexpected members), the nproot version
# probe, install-over-a-running-binary mechanics, and the up-to-date
# decisions. Network and pip are never touched -- the download and
# install steps are stubbed out.
#

import shutil
import stat
import zipfile

import pytest

from proot_distro.commands import update as update_mod


# ---------------------------------------------------------------------------
# version parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("5.10.2", (5, 10, 2)),
    ("v5.10.2", (5, 10, 2)),
    ("V1.2.3.4", (1, 2, 3, 4)),
    ("1.2", (1, 2)),
    ("1.2.rc1", (1, 2)),       # stops at the first non-numeric part
    ("rolling", ()),
    ("", ()),
    ("v", ()),
])
def test_parse_version(text, expected):
    assert update_mod._parse_version(text) == expected


# ---------------------------------------------------------------------------
# release asset selection
# ---------------------------------------------------------------------------

def _release(tag="v5.10.2", assets=()):
    return {"tag_name": tag, "assets": list(assets)}


def _asset(name, url=None):
    return {"name": name,
            "browser_download_url": url or f"https://example.invalid/{name}"}


def test_pick_wheel_asset():
    release = _release(assets=[
        _asset("Source.tar.gz"), _asset("checksums.txt"),
        _asset("proot_distro_i18n-5.10.2-py3-none-any.whl",
               "https://example.invalid/w.whl"),
    ])
    assert update_mod._pick_wheel_asset(release) == (
        "5.10.2", "https://example.invalid/w.whl",
        "proot_distro_i18n-5.10.2-py3-none-any.whl")


def test_pick_wheel_asset_missing():
    release = _release(assets=[_asset("Source.tar.gz")])
    assert update_mod._pick_wheel_asset(release) == ("", "", "")


def test_nproot_asset_for_device(monkeypatch):
    monkeypatch.setattr(update_mod, "get_device_cpu_arch", lambda: "aarch64")
    release = _release(tag="v5.1.107.105", assets=[
        _asset("nproot-android-arm64.zip", "https://example.invalid/z.zip"),
    ])
    assert update_mod._nproot_asset_for_device(release) == (
        "v5.1.107.105", "https://example.invalid/z.zip",
        "nproot-android-arm64.zip")


def test_nproot_asset_wrong_arch(monkeypatch):
    monkeypatch.setattr(update_mod, "get_device_cpu_arch", lambda: "riscv64")
    assert update_mod._nproot_asset_for_device(_release()) == ("", "", "")


def test_nproot_asset_missing_for_arch(monkeypatch):
    monkeypatch.setattr(update_mod, "get_device_cpu_arch", lambda: "x86_64")
    release = _release(assets=[_asset("nproot-android-arm64.zip")])
    tag, url, name = update_mod._nproot_asset_for_device(release)
    assert tag and not url and not name


def test_github_token_precedence(monkeypatch):
    for var in ("PD_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    assert update_mod._github_token() == ""
    monkeypatch.setenv("GITHUB_TOKEN", "  tok-b  ")
    monkeypatch.setenv("GH_TOKEN", "tok-c")
    assert update_mod._github_token() == "tok-b"  # first in the chain
    monkeypatch.setenv("PD_GITHUB_TOKEN", "tok-a")
    assert update_mod._github_token() == "tok-a"


# ---------------------------------------------------------------------------
# the archive extractor (the security boundary)
# ---------------------------------------------------------------------------

def _write_zip(path, entries):
    """entries: {name: bytes}, or {name: (bytes, mode)} for links."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, value in entries.items():
            if isinstance(value, tuple):
                data, mode = value
                info = zipfile.ZipInfo(name)
                info.external_attr = (mode & 0xFFFF) << 16
                zf.writestr(info, data)
            else:
                zf.writestr(name, value)


def test_extract_accepts_the_release_layout(tmp_path):
    archive = tmp_path / "r.zip"
    _write_zip(archive, {
        "nproot": b"ELF...",
        "host-census": b"ELF...",
        "preload/": b"",
        "preload/libnproot.so": b"ELF...",
        "preload/libnproot-musl.so": b"ELF...",
    })
    staging = tmp_path / "s"
    staging.mkdir()
    installed = update_mod._extract_nproot_zip(str(archive), str(staging))
    assert sorted(installed) == ["host-census", "nproot",
                                 "preload/libnproot-musl.so",
                                 "preload/libnproot.so"]
    assert (staging / "nproot").read_bytes() == b"ELF..."
    assert (staging / "preload" / "libnproot.so").read_bytes() == b"ELF..."
    assert stat.S_IMODE((staging / "nproot").stat().st_mode) & 0o111


def test_extract_refuses_path_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    _write_zip(archive, {"nproot": b"x", "../escape": b"owned"})
    with pytest.raises(RuntimeError, match="escapes"):
        update_mod._extract_nproot_zip(str(archive), str(tmp_path / "s"))
    assert not (tmp_path / "escape").exists()


def test_extract_refuses_absolute_member(tmp_path):
    archive = tmp_path / "evil.zip"
    _write_zip(archive, {"nproot": b"x", "/etc/passwd": b"owned"})
    with pytest.raises(RuntimeError, match="absolute"):
        update_mod._extract_nproot_zip(str(archive), str(tmp_path / "s"))


def test_extract_refuses_unexpected_member(tmp_path):
    archive = tmp_path / "surprise.zip"
    _write_zip(archive, {"nproot": b"x", "install.sh": b"#!/bin/sh\nrm -rf /"})
    with pytest.raises(RuntimeError, match="unexpected member"):
        update_mod._extract_nproot_zip(str(archive), str(tmp_path / "s"))


def test_extract_refuses_symlink_member(tmp_path):
    archive = tmp_path / "link.zip"
    _write_zip(archive, {
        "nproot": b"x",
        "preload/libnproot.so": (b"/etc/shadow", 0o120777),
    })
    with pytest.raises(RuntimeError, match="symlink"):
        update_mod._extract_nproot_zip(str(archive), str(tmp_path / "s"))


def test_extract_refuses_archive_without_the_binary(tmp_path):
    archive = tmp_path / "incomplete.zip"
    _write_zip(archive, {"host-census": b"x"})
    with pytest.raises(RuntimeError, match="did not contain"):
        update_mod._extract_nproot_zip(str(archive), str(tmp_path / "s"))


# ---------------------------------------------------------------------------
# the nproot --version probe
# ---------------------------------------------------------------------------

def _fake_bin(tmp_path, body, name="tool"):
    script = tmp_path / name
    script.write_text(body)
    script.chmod(0o755)
    return str(script)


def test_read_nproot_version_parses_the_hash(tmp_path):
    path = _fake_bin(tmp_path, "#!/bin/sh\necho 'nproot -- no-ptrace proot "
                              "/ neo proot 4d971ed1-dirty'\n")
    assert update_mod._read_nproot_version(path) == "4d971ed1"


def test_read_nproot_version_without_hash(tmp_path):
    path = _fake_bin(tmp_path, "#!/bin/sh\necho 'nothing here'\n")
    assert update_mod._read_nproot_version(path) == ""


def test_read_nproot_version_survives_failures(tmp_path):
    assert update_mod._read_nproot_version(str(tmp_path / "missing")) == ""
    path = _fake_bin(tmp_path, "#!/bin/sh\nexit 3\n")
    assert update_mod._read_nproot_version(path) == ""
    path = _fake_bin(tmp_path, "#!/bin/sh\nkill -TERM $$\n")
    assert update_mod._read_nproot_version(path) == ""


# ---------------------------------------------------------------------------
# install: replace over an existing binary
# ---------------------------------------------------------------------------

def test_install_file_replaces_content_atomically(tmp_path):
    target = tmp_path / "bin"
    target.mkdir()
    (target / "nproot").write_text("old")
    staging = tmp_path / "stage"
    staging.mkdir()
    (staging / "nproot").write_text("new")

    update_mod._install_nproot_file(str(staging), "nproot", str(target))
    assert (target / "nproot").read_text() == "new"
    assert not (target / "nproot.pd-update-tmp").exists()
    assert stat.S_IMODE((target / "nproot").stat().st_mode) & 0o111


def test_install_file_creates_preload_subdir(tmp_path):
    target = tmp_path / "bin"
    staging = tmp_path / "stage"
    (staging / "preload").mkdir(parents=True)
    (staging / "preload" / "libnproot.so").write_text("shim")

    update_mod._install_nproot_file(str(staging), "preload/libnproot.so",
                                    str(target))
    assert (target / "preload" / "libnproot.so").read_text() == "shim"


def test_install_file_refuses_a_directory_in_the_way(tmp_path):
    target = tmp_path / "bin"
    (target / "nproot").mkdir(parents=True)  # a directory named nproot
    staging = tmp_path / "stage"
    staging.mkdir()
    (staging / "nproot").write_text("new")
    with pytest.raises(RuntimeError, match="directory is in the way"):
        update_mod._install_nproot_file(str(staging), "nproot", str(target))


# ---------------------------------------------------------------------------
# the pd target's decisions (network stubbed)
# ---------------------------------------------------------------------------

_WHEEL_5103 = "proot_distro_i18n-5.10.3-py3-none-any.whl"


def test_update_pd_up_to_date(monkeypatch):
    monkeypatch.setattr(update_mod, "_latest_release",
                        lambda repo: _release(tag="v5.10.2", assets=[
                            _asset("proot_distro_i18n-5.10.2-py3-none-any.whl")]))
    monkeypatch.setattr(update_mod, "PROGRAM_VERSION", "5.10.2")
    assert update_mod._update_pd(check_only=False, insecure=False,
                                 force=False) is False


def test_update_pd_check_reports_available(monkeypatch):
    monkeypatch.setattr(update_mod, "_latest_release",
                        lambda repo: _release(tag="v5.10.3", assets=[
                            _asset(_WHEEL_5103)]))
    monkeypatch.setattr(update_mod, "PROGRAM_VERSION", "5.10.2")
    assert update_mod._update_pd(check_only=True, insecure=False,
                                 force=False) is True


def test_update_pd_installs_newer_wheel(monkeypatch, tmp_path):
    monkeypatch.setattr(update_mod, "_latest_release",
                        lambda repo: _release(tag="v5.10.3", assets=[
                            _asset(_WHEEL_5103)]))
    monkeypatch.setattr(update_mod, "PROGRAM_VERSION", "5.10.2")
    monkeypatch.setattr(update_mod, "download_file",
                        lambda url, dest, insecure=False: None)
    ran = {}
    monkeypatch.setattr(update_mod, "_pip_install",
                        lambda wheel: ran.setdefault("wheel", wheel))
    assert update_mod._update_pd(check_only=False, insecure=False,
                                 force=False) is True
    assert ran["wheel"].endswith(_WHEEL_5103)


def test_update_pd_older_release_needs_force(monkeypatch):
    monkeypatch.setattr(update_mod, "_latest_release",
                        lambda repo: _release(tag="v5.10.1", assets=[
                            _asset("proot_distro_i18n-5.10.1-py3-none-any.whl")]))
    monkeypatch.setattr(update_mod, "PROGRAM_VERSION", "5.10.2")
    assert update_mod._update_pd(check_only=False, insecure=False,
                                 force=False) is False


def test_update_pd_rolling_checkout_refuses_without_force(monkeypatch):
    monkeypatch.setattr(update_mod, "_latest_release",
                        lambda repo: _release(tag="v5.10.3", assets=[
                            _asset(_WHEEL_5103)]))
    monkeypatch.setattr(update_mod, "PROGRAM_VERSION", "rolling")
    assert update_mod._update_pd(check_only=False, insecure=False,
                                 force=False) is False


def test_update_pd_falls_back_to_pypi(monkeypatch):
    # A release without wheels, and PyPI answering: the PyPI wheel wins.
    monkeypatch.setattr(update_mod, "_latest_release",
                        lambda repo: _release(assets=[]))
    monkeypatch.setattr(update_mod, "_wheel_url_on_pypi",
                        lambda: ("5.10.4", "https://example.invalid/f.whl",
                                 "proot_distro_i18n-5.10.4-py3-none-any.whl"))
    monkeypatch.setattr(update_mod, "PROGRAM_VERSION", "5.10.2")
    assert update_mod._update_pd(check_only=True, insecure=False,
                                 force=False) is True


def test_update_pd_no_wheel_anywhere_is_an_honest_no(monkeypatch):
    monkeypatch.setattr(update_mod, "_latest_release",
                        lambda repo: _release(assets=[]))
    monkeypatch.setattr(update_mod, "_wheel_url_on_pypi", lambda: ("", "", ""))
    assert update_mod._update_pd(check_only=False, insecure=False,
                                 force=False) is False


# ---------------------------------------------------------------------------
# the nproot target's decisions (network stubbed)
# ---------------------------------------------------------------------------

def _arm64_release():
    return _release(tag="v5.1.107.105",
                    assets=[_asset("nproot-android-arm64.zip")])


def _fake_arm64_device(monkeypatch):
    monkeypatch.setattr(update_mod, "get_device_cpu_arch", lambda: "aarch64")
    monkeypatch.setattr(update_mod, "_latest_release",
                        lambda repo: _arm64_release())


def _fake_download(monkeypatch, archive_path):
    monkeypatch.setattr(
        update_mod, "download_file",
        lambda url, dest, insecure=False: shutil.copyfile(archive_path, dest))


def test_update_nproot_skips_when_no_asset(monkeypatch):
    monkeypatch.setattr(update_mod, "get_device_cpu_arch", lambda: "riscv64")
    monkeypatch.setattr(update_mod, "_latest_release",
                        lambda repo: _release(assets=[]))
    assert update_mod._update_nproot(check_only=True, insecure=False,
                                     force=False) is False


def test_update_nproot_check_only(monkeypatch):
    _fake_arm64_device(monkeypatch)
    monkeypatch.setattr(update_mod, "find_nproot_bin", lambda: None)
    assert update_mod._update_nproot(check_only=True, insecure=False,
                                     force=False) is True


def test_update_nproot_same_hash_is_skipped(monkeypatch, tmp_path):
    # Downloaded binary reports the build the installed one already has.
    _fake_arm64_device(monkeypatch)
    monkeypatch.setattr(update_mod, "find_nproot_bin",
                        lambda: "/fake/dir/nproot")

    archive = tmp_path / "nproot-android-arm64.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("nproot", b"ELF...")
    _fake_download(monkeypatch, archive)
    monkeypatch.setattr(update_mod, "_read_nproot_version",
                        lambda path: "4d971ed1")

    assert update_mod._update_nproot(check_only=False, insecure=False,
                                     force=False) is False


def test_update_nproot_installs_when_hash_differs(monkeypatch, tmp_path):
    _fake_arm64_device(monkeypatch)
    target = tmp_path / "inst"
    target.mkdir()
    (target / "nproot").write_text("old binary")
    monkeypatch.setattr(update_mod, "find_nproot_bin",
                        lambda: str(target / "nproot"))

    archive = tmp_path / "nproot-android-arm64.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("nproot", b"ELF-new")
        zf.writestr("preload/libnproot.so", b"shim")
    _fake_download(monkeypatch, archive)

    def fake_version(path):
        return "bbbbbbbb" if "staging" in path else "aaaaaaaa"

    monkeypatch.setattr(update_mod, "_read_nproot_version", fake_version)

    assert update_mod._update_nproot(check_only=False, insecure=False,
                                     force=False) is True
    assert (target / "nproot").read_bytes() == b"ELF-new"
    assert (target / "preload" / "libnproot.so").read_bytes() == b"shim"
    assert (target / "nproot").read_bytes() != b"old binary"


def test_update_nproot_fresh_install_into_default_dir(monkeypatch, tmp_path):
    _fake_arm64_device(monkeypatch)
    monkeypatch.setattr(update_mod, "find_nproot_bin", lambda: None)
    monkeypatch.setattr(update_mod, "_default_nproot_dir",
                        lambda: str(tmp_path / "fresh"))

    archive = tmp_path / "nproot-android-arm64.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("nproot", b"ELF")
    _fake_download(monkeypatch, archive)
    monkeypatch.setattr(update_mod, "_read_nproot_version",
                        lambda path: "feed0001")

    assert update_mod._update_nproot(check_only=False, insecure=False,
                                     force=False) is True
    assert (tmp_path / "fresh" / "nproot").read_bytes() == b"ELF"
