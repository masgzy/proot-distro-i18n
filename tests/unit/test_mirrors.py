#
# Proot-Distro - manage proot containers.
#
# Tests for mirrors.py's persistence and trust boundary: saving the
# active mirror actually lands on disk (the old EBADF bug never wrote a
# byte), builtin ids resolve to the builtin URL whatever the files say,
# and a non-https or userinfo-carrying URL from the guest-writable
# config is refused at resolve time.
#

import json
import os

import pytest

from proot_distro import config as config_mod
from proot_distro import mirrors


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    """Redirect the whole config directory into the test's tmp dir."""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(cfg_dir))
    monkeypatch.setattr(config_mod, "CONFIG_FILE",
                        str(cfg_dir / "config.json"))
    monkeypatch.setattr(mirrors, "MIRRORS_CONFIG_FILE",
                        str(cfg_dir / "mirror.json"))
    monkeypatch.setattr(mirrors, "USER_MIRRORS_FILE",
                        str(cfg_dir / "mirrors.json"))
    mirrors._invalidate_active_cache()
    mirrors._user_mirrors_cache.update(mtime=None, data=[])
    return cfg_dir


# ---------------------------------------------------------------------------
# persistence (the closefd fix)
# ---------------------------------------------------------------------------

def test_save_active_mirror_actually_lands_on_disk():
    # 曾经 atomic_replace 交出的整数 fd 被当路径名传给 open()，内层
    # with 默认 closefd=True 提前关掉它——生成器在已关闭的 fd 上
    # fstat，EBADF，mirror.json 从来没写进去过。
    mirrors.save_active_mirror(
        dict(id="custom", name="Mine",
             url="https://mirror.example.test", region="CN", note=""))
    data = json.loads(_read("mirror.json"))
    assert data["id"] == "custom"
    assert data["url"] == "https://mirror.example.test"
    # 读回：当前选中即这条自定义源。
    mirrors._invalidate_active_cache()
    active = mirrors.load_active_mirror()
    assert active and active["url"] == "https://mirror.example.test"


def _path(name):
    from proot_distro.config import CONFIG_DIR
    return os.path.join(str(CONFIG_DIR), name)


def _read(name):
    with open(_path(name), "r", encoding="utf-8") as fh:
        return fh.read()


def _write(name, payload):
    with open(_path(name), "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def test_save_and_clear_roundtrip():
    mirrors.save_active_mirror(
        dict(id="daocloud", name="DaoCloud",
             url="https://docker.m.daocloud.io", region="CN", note=""))
    mirrors._invalidate_active_cache()
    assert mirrors.load_active_mirror()["id"] == "daocloud"
    assert mirrors.clear_active_mirror() is True
    mirrors._invalidate_active_cache()
    assert mirrors.load_active_mirror() is None


# ---------------------------------------------------------------------------
# the trust boundary at resolve time
# ---------------------------------------------------------------------------

def test_builtin_id_resolves_to_the_builtin_url_not_an_override():
    # mirror.json 是客人可替换的文件：同 id 附带的 url 覆盖必须被
    # 忽略，被换的文件顶多能把拉取指向另一台内置镜像。
    _write("mirror.json", {
        "id": "daocloud", "url": "https://evil.example.test",
    })
    mirrors._invalidate_active_cache()
    active = mirrors.load_active_mirror()
    assert active["url"] == "https://docker.m.daocloud.io"


def test_non_https_or_userinfo_url_is_refused():
    for bad in ("http://mirror.example.test",
                "https://user:pass@mirror.example.test",
                "https://mirror.example.test/?x=1",
                "not a url", ""):
        assert mirrors._usable_registry_url(bad) is False
    assert mirrors._usable_registry_url("https://a.b.c") is True
    assert mirrors._usable_registry_url("https://a.b.c/docker") is True


def test_custom_url_from_the_file_is_refused_unless_https(monkeypatch):
    monkeypatch.delenv("PD_REGISTRY_URL", raising=False)
    _write("mirror.json", {
        "id": "custom", "url": "http://mirror.example.test",
    })
    mirrors._invalidate_active_cache()
    # 明文 http 的“选中”在拉取侧被当作未选中，回落默认源。
    assert mirrors.resolve_registry_url() == "https://docker.m.daocloud.io"


def test_env_var_still_wins(monkeypatch):
    _write("mirror.json", {
        "id": "custom", "url": "https://mirror.example.test",
    })
    monkeypatch.setenv("PD_REGISTRY_URL", "http://127.0.0.1:5000")
    mirrors._invalidate_active_cache()
    # 用户自己的环境变量是用户自己的选择：明文也照用。
    assert mirrors.resolve_registry_url() == "http://127.0.0.1:5000"
