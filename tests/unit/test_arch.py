# Tests for proot_distro.arch — ELF e_machine detection, arch normalization,
# and emulator argument selection.

import os
import stat

import pytest

from proot_distro import arch


def _header(tmp_path, builders, *a, **kw):
    """The bytes _elf_arch() judges: written by the builder, read back."""
    p = tmp_path / "bin"
    builders.write_elf(str(p), *a, **kw)
    return p.read_bytes()[:arch._ELF_HEADER_BYTES]


@pytest.mark.parametrize("name", ["i686", "arm", "x86_64", "aarch64", "riscv64"])
def test_elf_arch_detects_each(tmp_path, builders, name):
    assert arch._elf_arch(_header(tmp_path, builders, name)) == name


def test_elf_arch_big_endian(tmp_path, builders):
    header = _header(tmp_path, builders, "aarch64", little_endian=False)
    assert arch._elf_arch(header) == "aarch64"


def test_elf_arch_truncated_returns_empty(tmp_path, builders):
    header = _header(tmp_path, builders, "x86_64", truncated=True)
    assert arch._elf_arch(header) == ""


def test_elf_arch_bad_magic_returns_empty(tmp_path, builders):
    header = _header(tmp_path, builders, "x86_64", valid_magic=False)
    assert arch._elf_arch(header) == ""


def test_elf_arch_unknown_machine_returns_empty():
    # Valid ELF magic but e_machine = 0 (unmapped).
    assert arch._elf_arch(b"\x7fELF\x02\x01" + b"\x00" * 14) == ""


def test_elf_arch_of_nothing_read_returns_empty():
    # What read_guest_bytes() answers for a file that is not there, not
    # readable, or not a regular file.
    assert arch._elf_arch(None) == ""
    assert arch._elf_arch(b"") == ""


def test_detect_installed_arch_from_rootfs(tmp_path, builders):
    root = tmp_path / "rootfs"
    builders.make_rootfs(str(root), arch="aarch64")
    assert arch.detect_installed_arch(str(root)) == "aarch64"


def test_detect_installed_arch_unknown(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert arch.detect_installed_arch(str(empty)) == "unknown"


def test_detect_installed_arch_by_container_name(builders):
    builders.make_container("box", arch="x86_64")
    # A bare name (no os.sep) resolves through container_rootfs.
    assert arch.detect_installed_arch("box") == "x86_64"


@pytest.mark.parametrize("raw,expected", [
    ("aarch64", "aarch64"),
    ("x86_64", "x86_64"),
    ("riscv64", "riscv64"),
    ("arm64", "aarch64"),
    ("amd64", "x86_64"),
    ("386", "i686"),
    ("arm", "arm"),
    ("arm/v7", "arm"),
    ("linux/arm64", "aarch64"),
    ("linux/amd64", "x86_64"),
    ("  amd64  ", "x86_64"),
])
def test_normalize_arch_accepts(raw, expected):
    assert arch.normalize_arch(raw) == expected


@pytest.mark.parametrize("raw", ["sparc", "", "linux/", "ppc64le", "x86"])
def test_normalize_arch_rejects(raw):
    assert arch.normalize_arch(raw) is None


def test_emulator_args_native_is_empty():
    assert arch.get_emulator_args("x86_64", "x86_64") == []


def test_emulator_args_32_on_64_native(monkeypatch):
    monkeypatch.setattr(arch, "supports_32bit", lambda: True)
    assert arch.get_emulator_args("arm", "aarch64") == []
    assert arch.get_emulator_args("i686", "x86_64") == []


def test_emulator_args_missing_qemu_exits(monkeypatch, capsys):
    monkeypatch.setattr(arch.shutil, "which", lambda _bin: None)
    with pytest.raises(SystemExit) as exc:
        arch.get_emulator_args("aarch64", "x86_64")
    assert exc.value.code == 1
    assert "emulator package" in capsys.readouterr().err


def test_emulator_args_unsupported_arch_exits(capsys):
    with pytest.raises(SystemExit) as exc:
        arch.get_emulator_args("m68k", "x86_64")
    assert exc.value.code == 1
    assert "unsupported architecture" in capsys.readouterr().err


def test_emulator_override_must_exist(capsys):
    with pytest.raises(SystemExit) as exc:
        arch.get_emulator_args("aarch64", "x86_64",
                               emulator_override="/no/such/emu")
    assert exc.value.code == 1
    assert "not found or not executable" in capsys.readouterr().err


def test_emulator_override_used_when_valid(tmp_path):
    emu = tmp_path / "qemu-fake"
    emu.write_text("#!/bin/sh\n")
    emu.chmod(emu.stat().st_mode | stat.S_IXUSR)
    args = arch.get_emulator_args("aarch64", "x86_64",
                                  emulator_override=str(emu))
    assert args[:2] == ["-q", str(emu)]
    # Any extra entries are android system --bind args.
    assert all(a.startswith("--bind=") for a in args[2:])


def test_emulator_override_relative_is_made_absolute(tmp_path, monkeypatch):
    # Both callers exec proot from inside the rootfs, so a relative
    # emulator path would be resolved there rather than here.
    emu = tmp_path / "qemu-fake"
    emu.write_text("#!/bin/sh\n")
    emu.chmod(emu.stat().st_mode | stat.S_IXUSR)
    monkeypatch.chdir(tmp_path)

    args = arch.get_emulator_args("aarch64", "x86_64",
                                  emulator_override="qemu-fake")
    assert args[0] == "-q"
    assert os.path.isabs(args[1])
    assert os.path.samefile(args[1], str(emu))


def test_emulator_from_a_relative_path_entry_is_made_absolute(
    tmp_path, monkeypatch
):
    # A relative PATH entry makes shutil.which() answer relatively.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    emu = bin_dir / "qemu-aarch64"
    emu.write_text("#!/bin/sh\n")
    emu.chmod(emu.stat().st_mode | stat.S_IXUSR)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "bin")

    args = arch.get_emulator_args("aarch64", "x86_64")
    assert args[0] == "-q"
    assert os.path.isabs(args[1])
    assert os.path.samefile(args[1], str(emu))


# ---------------------------------------------------------------------------
# get_proot_bin — the string both exec paths hand to a PATH the image sets
# ---------------------------------------------------------------------------

def test_proot_bin_is_absolute(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    proot = bin_dir / "proot"
    proot.write_text("#!/bin/sh\n")
    proot.chmod(proot.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("PD_PROOT_BIN", raising=False)

    assert arch.get_proot_bin() == str(proot)


def test_proot_bin_from_a_relative_path_entry_is_made_absolute(
    tmp_path, monkeypatch
):
    # A relative PATH entry makes shutil.which() answer relatively; the
    # answer is resolved here, while the cwd is still this process's own.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    proot = bin_dir / "proot"
    proot.write_text("#!/bin/sh\n")
    proot.chmod(proot.stat().st_mode | stat.S_IXUSR)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "bin")
    monkeypatch.delenv("PD_PROOT_BIN", raising=False)

    resolved = arch.get_proot_bin()
    assert os.path.isabs(resolved)
    assert os.path.samefile(resolved, str(proot))


def test_proot_bin_never_falls_back_to_a_bare_name(tmp_path, monkeypatch,
                                                   capsys):
    # "proot" with no directory in it would be looked up again by the
    # exec, in the PATH of the *child* environment — which an image's Env
    # supplies.
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("PD_PROOT_BIN", raising=False)

    with pytest.raises(SystemExit) as exc:
        arch.get_proot_bin()
    assert exc.value.code == 1
    assert "does not exist" in capsys.readouterr().err


def test_proot_bin_override_is_made_absolute(tmp_path, monkeypatch):
    proot = tmp_path / "proot-static"
    proot.write_text("#!/bin/sh\n")
    proot.chmod(proot.stat().st_mode | stat.S_IXUSR)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PD_PROOT_BIN", "proot-static")

    assert arch.get_proot_bin() == str(proot)


# ---------------------------------------------------------------------------
# nproot preference — find_proot_bin() and its users
# ---------------------------------------------------------------------------

def _make_exec(tmp_path, name):
    exe = tmp_path / name
    exe.write_text("#!/bin/sh\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


@pytest.fixture(autouse=True)
def _hermetic_probe_dirs(tmp_path, monkeypatch):
    """Point the explicit probe dirs at scratch space.

    find_proot_bin() no longer walks PATH alone, so the host's real
    /usr/bin or Termux prefix must not leak into expectations.
    """
    sysbin = tmp_path / "sysbin"
    userbin = tmp_path / "userbin"
    sysbin.mkdir()
    userbin.mkdir()
    monkeypatch.setattr(arch, "SYSTEM_BIN_DIRS", (str(sysbin),))
    monkeypatch.setattr(arch, "USER_BIN_DIR", str(userbin))
    return sysbin, userbin


def test_find_proot_bin_prefers_nproot(tmp_path, monkeypatch):
    # nproot, the no-ptrace proot this tool targets, wins when both it
    # and the stock proot are installed.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_exec(bin_dir, "proot")
    nproot = _make_exec(bin_dir, "nproot")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("PD_PROOT_BIN", raising=False)

    assert arch.find_proot_bin() == str(nproot)
    assert arch.get_proot_bin() == str(nproot)


def test_find_proot_bin_falls_back_to_proot(tmp_path, monkeypatch):
    # Only the stock proot: the answer is unchanged from upstream.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    proot = _make_exec(bin_dir, "proot")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("PD_PROOT_BIN", raising=False)

    assert arch.find_proot_bin() == str(proot)
    assert arch.get_proot_bin() == str(proot)


def test_find_proot_bin_answers_none_on_empty_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("PD_PROOT_BIN", raising=False)

    assert arch.find_proot_bin() is None


def test_find_proot_bin_prefers_system_dir_over_path(
    tmp_path, monkeypatch
):
    # A stale copy in a personal PATH entry (last session's download)
    # must not shadow the properly installed one.
    sysbin, _userbin = tmp_path / "sysbin", tmp_path / "userbin"
    installed = _make_exec(sysbin, "nproot")
    (tmp_path / "path").mkdir()
    _make_exec(tmp_path / "path", "nproot")
    monkeypatch.setenv("PATH", str(tmp_path / "path"))

    assert arch.find_proot_bin() == str(installed)


def test_find_proot_bin_path_beats_user_local_dir(tmp_path, monkeypatch):
    userbin = tmp_path / "userbin"
    (tmp_path / "path").mkdir()
    path_nproot = _make_exec(tmp_path / "path", "nproot")
    _make_exec(userbin, "nproot")
    monkeypatch.setenv("PATH", str(tmp_path / "path"))

    assert arch.find_proot_bin() == str(path_nproot)


def test_find_proot_bin_user_local_dir_used_when_path_misses(
    tmp_path, monkeypatch
):
    # ~/.local/bin regularly exists while a non-login PATH omits it.
    userbin = tmp_path / "userbin"
    local_nproot = _make_exec(userbin, "nproot")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    assert arch.find_proot_bin() == str(local_nproot)


def test_find_proot_bin_skips_non_executable_system_hit(
    tmp_path, monkeypatch
):
    # A leftover non-executable file in an early directory must not
    # stop the search.
    sysbin, _userbin = tmp_path / "sysbin", tmp_path / "userbin"
    (sysbin / "nproot").write_text("not executable")
    (tmp_path / "path").mkdir()
    path_nproot = _make_exec(tmp_path / "path", "nproot")
    monkeypatch.setenv("PATH", str(tmp_path / "path"))

    assert arch.find_proot_bin() == str(path_nproot)


def test_proot_bin_override_beats_nproot(tmp_path, monkeypatch):
    # PD_PROOT_BIN stays the explicit, highest-priority choice even when
    # an nproot sits on PATH.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_exec(bin_dir, "nproot")
    custom = _make_exec(tmp_path, "my-proot")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("PD_PROOT_BIN", str(custom))

    assert arch.get_proot_bin() == str(custom)


def test_proot_bin_missing_names_both_candidates(tmp_path, monkeypatch,
                                                 capsys):
    # The refusal names what it looked for, so a user with only the
    # no-ptrace fork in mind is not told to install an unrelated tool.
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("PD_PROOT_BIN", raising=False)

    with pytest.raises(SystemExit) as exc:
        arch.get_proot_bin()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "nproot or proot" in err
