# proot-distro-i18n 测试套件

`proot-distro` 的端到端和单元测试。测试套件默认**离线且确定性**运行：
无网络、无真实 `proot` 执行、不访问真实的 proot-distro 安装。

## 运行

```sh
# 从仓库根目录
pip install -e '.[test]'      # 或仅确保 pytest 可用
python -m pytest -q           # 完整离线套件
python -m pytest tests/security -q   # 仅安全测试
RUN_LIVE_TESTS=1 python -m pytest -q tests/live   # 可选的网络/proot 测试
```

## 布局

- `unit/` — 纯函数正确性（名称、路径、架构/ELF 检测、Dockerfile
  解析 + `${VAR}` 展开、摘要验证、缓存键、层 diff/whiteouts、OCI
  写入器、引号、环境注入、argparse 等）。
- `security/` — 恶意/畸形输入的防御。验证第三方 tar / OCI 镜像 /
  备份归档无法逃逸目标 rootfs 或容器目录（通过成员名的路径遍历、
  硬链接/符号链接 linkname、伪造的 `index.json` 摘要、COPY/ADD 源、
  `--bind` spec）。
- `integration/` — 真实的 `command_*` 流水线，使用
  `types.SimpleNamespace` 参数，在沙箱化运行时树上运行（install /
  backup→restore / build→install / copy / sync / rename / reset /
  clear-cache / CLI 分发 / `login --get-proot-cmd`）。
- `live/` — 可选冒烟测试，标记 `@pytest.mark.live`，除非
  `RUN_LIVE_TESTS=1` 否则跳过（真实 Docker Hub 拉取、真实 proot 运行）。

## 隔离原理

`proot_distro.constants` 在**导入时**计算所有运行时路径和 `IS_TERMUX`，
大多数模块按值绑定。因此 `conftest.py` 在导入任何 proot_distro 模块
*之前*将 `XDG_DATA_HOME` / `XDG_CACHE_HOME` / `HOME` 指向一次性沙箱并
清除 Termux/auth 环境变量，然后在测试间清除运行时/缓存树。如果解析出
的路径不在沙箱内或 `IS_TERMUX` 为 True，守卫会拒绝运行。

共享构建器位于 `_builders.py`：`make_tar`（任意成员，包括恶意的）、
`make_oci_archive`、`make_layer_blob` / `seed_cached_layer`、
`elf_bytes`、`make_rootfs`、`make_container`、`tree_snapshot`。

## 已知覆盖限制

- 真实 `login` / `run` `os.execvpe` 进入 proot + 发行版 shell 无法
  单元测试；通过 `--get-proot-cmd`（断言生成的 proot argv）和可选
  live 测试覆盖。
- `run_step.do_run` 的实际 proot 调用仅由 live build 测试验证；
  其可分离的助手直接覆盖。
- Termux 专用（`IS_TERMUX=True`）分支通过直接调用纯函数和针对性
  per-module `monkeypatch` 模块的 `IS_TERMUX` 属性来覆盖，因为测试
  主机是非 Termux 的。
