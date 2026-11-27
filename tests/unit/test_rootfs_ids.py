#
# Proot-Distro - manage proot containers.
#
# Tests for the post-extraction rootfs fixups around identity: the
# Termux uid/gid registration whose host lookup must survive a host
# with no /etc/passwd at all (real Android), which is what left guests
# with the "I have no name!" prompt.
#

import os

import pytest

from proot_distro.helpers import rootfs


# ---------------------------------------------------------------------------
# name derivation (the "I have no name!" fix)
# ---------------------------------------------------------------------------

def test_username_prefers_the_host_table():
    # The host we run tests on has a passwd; its own name wins.
    import pwd
    assert rootfs._termux_username(os.getuid()) == \
        pwd.getpwuid(os.getuid()).pw_name


def test_username_falls_back_to_u_prefix_for_app_uid(monkeypatch):
    import pwd
    monkeypatch.setattr(pwd, "getpwuid",
                        lambda u: (_ for _ in ()).throw(KeyError(u)))
    # Android app uid range: Termux spells these "u10123".
    assert rootfs._termux_username(10123) == "u10123"
    assert rootfs._termux_groupname(10123) == "u10123"


def test_username_falls_back_to_aid_prefix_for_system_uid(monkeypatch):
    import pwd
    monkeypatch.setattr(pwd, "getpwuid",
                        lambda u: (_ for _ in ()).throw(KeyError(u)))
    # aid_* range (system ids such as inet: 3003).
    assert rootfs._termux_username(3003) == "aid_3003"
    assert rootfs._termux_groupname(3003) == "aid_3003"


def test_username_root_is_root(monkeypatch):
    import pwd
    monkeypatch.setattr(pwd, "getpwuid",
                        lambda u: (_ for _ in ()).throw(KeyError(u)))
    assert rootfs._termux_username(0) == "root"
    assert rootfs._termux_groupname(0) == "root"


def test_username_survives_a_damaged_nsswitch(monkeypatch):
    import pwd
    # A broken nsswitch raises something other than KeyError; the
    # derivation must still produce a name, not a crash.
    monkeypatch.setattr(pwd, "getpwuid",
                        lambda u: (_ for _ in ()).throw(RuntimeError("nss")))
    assert rootfs._termux_username(10123) == "u10123"


def test_groupname_falls_back_for_a_gid_without_an_entry(monkeypatch):
    import grp
    monkeypatch.setattr(grp, "getgrgid",
                        lambda g: (_ for _ in ()).throw(KeyError(g)))
    assert rootfs._termux_groupname(10150) == "u10150"


# ---------------------------------------------------------------------------
# the registration writes the fallback entry end to end
# ---------------------------------------------------------------------------

@pytest.fixture
def etc_fd(tmp_path):
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "passwd").write_text("root:x:0:0:root:/root:/bin/sh\n")
    fd = os.open(str(etc), os.O_RDONLY | os.O_DIRECTORY)
    yield fd, etc
    os.close(fd)


def test_registration_writes_with_a_derived_name(etc_fd, monkeypatch):
    import pwd
    fd, etc = etc_fd
    monkeypatch.setattr(pwd, "getpwuid",
                        lambda u: (_ for _ in ()).throw(KeyError(u)))
    monkeypatch.setattr(rootfs.os, "getuid", lambda: 10123)
    monkeypatch.setattr(rootfs.os, "getgid", lambda: 10123)
    monkeypatch.setattr(rootfs.os, "getgroups", lambda: [10123])
    rootfs.register_android_ids_at(fd)
    text = (etc / "passwd").read_text()
    assert "aid_u10123:x:10123:10123:Termux:/:/sbin/nologin" in text
    shadow = (etc / "shadow").read_text()
    assert "aid_u10123:*:" in shadow
    group = (etc / "group").read_text()
    assert "aid_u10123:x:10123:" in group


def test_registration_creates_a_missing_passwd(etc_fd, monkeypatch):
    # A minimal image may ship no passwd at all; the registration
    # (called after install's own exists check would have refused, but
    # the function itself is total) creates one and writes the entry.
    fd, etc = etc_fd
    os.unlink(etc / "passwd")
    import pwd
    monkeypatch.setattr(pwd, "getpwuid",
                        lambda u: (_ for _ in ()).throw(KeyError(u)))
    monkeypatch.setattr(rootfs.os, "getuid", lambda: 10123)
    monkeypatch.setattr(rootfs.os, "getgid", lambda: 10123)
    monkeypatch.setattr(rootfs.os, "getgroups", lambda: [10123])
    rootfs.register_android_ids_at(fd)
    assert "aid_u10123" in (etc / "passwd").read_text()
