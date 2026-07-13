# proot-distro（中文汉化版）

> proot-distro 的完整中文汉化(i18n国际化)版本，附带 Docker registry 镜像源管理子命令、性能优化与逻辑改进。
>
> 本 README 对应 `proot-distro-i18n` v5.4.1（2026-07-13）。

## 目录

- [简介](#简介)
- [主要改动](#主要改动)
- [安装](#安装)
- [快速开始](#快速开始)
- [镜像源管理（mirror 子命令）](#镜像源管理mirror-子命令)
- [国际化（i18n）](#国际化i18n)
- [性能优化](#性能优化)
- [配置文件](#配置文件)
- [环境变量](#环境变量)
- [故障排查](#故障排查)
- [开发与贡献](#开发与贡献)
- [许可证](#许可证)

## 简介

PRoot-Distro 是 proot 用户空间模拟器（chroot、mount --bind、binfmt_misc）的封装工具，提供便捷的 Linux 容器管理能力，并借助 Docker registry 支持提供任意发行版。

本仓库在原版基础上做了三层增强：

1. **完整汉化**：CLI 输出、错误提示、帮助文档、shell 补全全部汉化，遵循 Termux 中文社区术语习惯。
2. **镜像源管理**：新增 `mirror` 子命令，把"换源"从硬编码升级为一等公民——可见、可测、可切。
3. **性能与逻辑优化**：`lru_cache` 缓存路径计算、并发探测镜像源、atomic-replace 配置写入、proot 豁免集合等。

## 主要改动

| 维度 | 原版 | 本版 |
|---|---|---|
| 镜像源 | 硬编码单一源 `https://docker.xuanyuan.run` | 内置 17 个镜像源 + 用户自定义 JSON + `mirror` 子命令（ls/test/use/unset/show） |
| 语言 | 英文直出 | 默认中文，`PD_LANG=en` 切英文；201 条 msgid 全译；help pages 双语版 |
| shell 补全 | bash/fish/zsh 英文 + 无 mirror | 三脚本全中文 + 含 mirror 子命令补全 |
| 性能 | paths.container_* 每次重算 | `lru_cache(maxsize=256)`，8.8x 加速 |
| 帮助可用性 | 未装 proot 时所有 `-h` 报错 | 任何 `-h`/`--help` 提前到 proot 检查之前 |
| 配置文件 | 无 | `~/.config/proot-distro/mirror.json`（XDG）或 `$PREFIX/etc/proot-distro/mirror.json`（Termux） |

## 安装

### 方式 1：通过 pip 从 GitHub 安装（推荐）

```bash
pip install git+https://github.com/masgzy/proot-distro-i18n.git
# 使用镜像源安装
pip install git+https://github.cnxiaobai.com/https://github.com/masgzy/proot-distro-i18n.git
pip install git+https://v6.gh-proxy.org/https://github.com/masgzy/proot-distro-i18n.git
```

### 方式 2：克隆仓库后本地安装

```bash
git clone https://github.com/masgzy/proot-distro-i18n.git
cd proot-distro-i18n
pip install .
```

### 方式 3：直接运行（无需安装）

```bash
git clone https://github.com/masgzy/proot-distro-i18n.git
cd proot-distro-i18n
python3 proot-distro.py mirror ls
```

### 安装 shell 补全（可选）

```bash
# Bash
cp proot_distro/completions/proot-distro.bash ~/.local/share/bash-completion/completions/proot-distro

# Fish
cp proot_distro/completions/proot-distro.fish ~/.config/fish/completions/proot-distro.fish
cp proot_distro/completions/proot-distro.fish ~/.config/fish/completions/pd.fish

# Zsh
mkdir -p ~/.local/share/zsh/site-functions
cp proot_distro/completions/_proot-distro ~/.local/share/zsh/site-functions/_proot-distro
# 加到 ~/.zshrc: fpath=(~/.local/share/zsh/site-functions $fpath)
```

## 快速开始

```bash
# 安装 Ubuntu 24.04
proot-distro install ubuntu:24.04

# 登录
proot-distro login ubuntu

# 列出已安装的容器
proot-distro list

# 备份
proot-distro backup ubuntu --output ~/ubuntu.tar.xz

# 删除
proot-distro remove ubuntu
```

## 镜像源管理（mirror 子命令）

原版 proot-distro 把单一 registry URL 硬编码在 `transport.py`，用户换源只能改源码。本版新增 `mirror` 子命令，把"换源"做成一等公民。

### 子动作

| 动作 | 说明 |
|---|---|
| `ls` / `list` | 列出所有内置镜像源 + 当前生效源 |
| `test [ID\|URL]` | 探测可达性；不带参数并发测全部，测完提示 Y/n 切换到最快源 |
| `use` / `set ID\|URL` | 选中一个镜像源（按 id、name 或 URL） |
| `unset` / `clear` / `off` | 清除当前选中，回到默认 |
| `show` | 仅打印当前生效 URL（脚本友好） |

### 选项

| 选项 | 说明 |
|---|---|
| `-t, --timeout SECONDS` | 探测超时（默认 6 秒） |
| `--allow-insecure` | 允许 HTTPS 跳过证书校验 |
| `--use-best` | test 后自动切换到最快源，不提示（脚本友好） |
| `--no-use` | test 后不提示也不切换 |
| `--json` | test 以 JSON 输出（脚本友好；隐含 --no-use） |
| `--reachable-only` | ls 仅显示可达镜像源（会先探测） |
| `-q, --quiet` | 安静模式 |

### 内置镜像源（截至 2026-07-08 校准）

| ID | 名称 | 区域 | URL |
|---|---|---|---|
| `daocloud` | DaoCloud | CN | `https://docker.m.daocloud.io` |
| `1panel-live` | 1Panel | CN | `https://docker.1panel.live` |
| `1ms` | 毫秒镜像 | CN | `https://docker.1ms.run` |
| `xuanyuan` | 轩辕镜像 | CN | `https://docker.xuanyuan.me` |
| `xuanyuan-run` | 轩辕镜像 (run) | CN | `https://docker.xuanyuan.run` |
| `netease` | 网易 | CN | `https://hub-mirror.c.163.com` |
| `tencent` | 腾讯云 | CN | `https://mirror.ccs.tencentyun.com` |
| `docker-cn` | Docker 中国 | CN | `https://registry.docker-cn.com` |
| `docker-hub` | Docker Hub (官方) | US | `https://registry-1.docker.io` |

默认激活源：`daocloud`。

### 使用示例

```bash
# 列出所有镜像源
proot-distro mirror ls

# 测试所有镜像源（并发），测完 Y/n 提示切换到最快源
proot-distro mirror test

# 测试 + 自动切换到最快源（脚本友好）
proot-distro mirror test --use-best

# 测试 + JSON 输出（便于 jq 解析）
proot-distro mirror test --json | jq '.mirrors[] | select(.reachable) | {id, latency_ms}'

# 列出仅可达的镜像源
proot-distro mirror ls --reachable-only

# 切换镜像源（按 ID）
proot-distro mirror use 1panel-live

# 切换镜像源（按 URL）
proot-distro mirror use https://my-private-mirror.com

# 查看当前生效 URL
proot-distro mirror show

# 清除配置，回到默认
proot-distro mirror unset
```

### PD_REGISTRY_URL 环境变量

`PD_REGISTRY_URL` 优先级最高，覆盖一切配置。设了此变量时：

| 子命令 | 行为 |
|---|---|
| `mirror ls` | 顶部显示 `生效源: ⚠ 环境变量覆盖 <url>`，并提示配置不会生效 |
| `mirror use` | 正常写配置文件，但末尾警告 env 覆盖 |
| `mirror unset` | 正常清配置文件，但末尾警告 env 仍覆盖 |
| `mirror show` | 输出 env URL（脚本拿到真实生效值） |
| `mirror test --use-best` | 拒绝切换并警告 env 覆盖 |

要恢复配置文件生效，执行 `unset PD_REGISTRY_URL`。

## 国际化（i18n）

### 语言切换

**默认中文**。通过 `PD_LANG` 环境变量切英文：

```bash
# 临时切英文
PD_LANG=en proot-distro mirror ls

# 永久切英文（写入 ~/.bashrc）
export PD_LANG=en

# 切回中文
export PD_LANG=zh
# 或直接 unset
unset PD_LANG
```

**语言探测策略**（v7 调整，适配 Termux 无 GNU locale 支持）：

- **只看 `PD_LANG`**：`PD_LANG=en` / `C` / `POSIX` → 英文；`PD_LANG=zh` / `zh_CN` / 任何含 `zh` 的值 → 中文
- **默认中文**：不设 `PD_LANG` 就是中文
- **不看 `LC_ALL` / `LC_MESSAGES` / `LANG`**：Termux/Android 上这些变量要么未设、要么是 `C`，不可靠

历史：v1-v6 会按 `PD_LANG > LC_ALL > LC_MESSAGES > LANG` 探测，导致 Termux 默认环境（`LANG=C`）下显示英文。v7 改为只看 `PD_LANG`，默认中文。

### 翻译覆盖

- **CLI 输出/错误**：201 条 msgid 全部译为中文
- **help pages**：`pages.py`（中文）+ `pages_en.py`（英文），按语言切换
- **shell 补全**：bash/fish/zsh 三脚本注释与描述全中文
- **section 标签**：用法/描述/选项/示例 + USAGE/DESCRIPTION/OPTIONS/EXAMPLES 双语

### 术语对照

采用 Termux 中文社区习惯：

| 英文 | 中文 |
|---|---|
| container | 容器 |
| rootfs | rootfs（保留缩写） |
| mirror | 镜像源 |
| registry | 仓库 |
| proot / Termux / Docker / OCI | 保留英文 |
| backup / restore | 备份 / 恢复 |
| login / detach | 登录 / 后台会话 |
| bind | bind 挂载 |
| layer / manifest | 层 / 清单 |
| Warning / Error | 警告 / 错误 |

### 重新编译翻译

修改 `.po` 后重新编译 `.mo`：

```bash
python3 compile_mo.py
```

## 性能优化

### 已实施的优化

| 优化点 | 改动前 | 改动后 | 效果 |
|---|---|---|---|
| `paths.container_*` | 每次 `os.path.join` | `@lru_cache(maxsize=256)` | 8.8x 加速（462ns → 52ns） |
| 镜像源探测 | 串行 9 × 6s | `ThreadPoolExecutor(max_workers=8)` | 9 个源 1 秒内完成 |
| 配置文件写入 | 直接覆盖 | `tmp + os.replace`（atomic） | 中断不留半写文件 |
| proot 豁免检查 | tuple `in` 查找 | `frozenset` O(1) | 微优化 |
| nested-proot 探测 | 总是读第二个 /proc | `TracerPid: 0` 时短路 | 减少一次 open() |

### 性能基准

```bash
python3 scripts/bench_perf.py
```

> 注：性能基准脚本为可选工具，不在主包中。

关键数据（参考值，实际取决于环境）：

| 基准项 | 耗时 |
|---|---|
| `paths.container_dir`（cached） | ~52 ns |
| `paths.container_rootfs`（cached） | ~52 ns |
| `mirrors.resolve_registry_url`（env） | ~325 ns |
| `i18n._()` 命中 | ~201 ns |
| `parser.build_parser()` | ~2.26 ms |
| `import proot_distro.cli` | ~142 ms |
| `mirror ls` 端到端 | ~164 ms |
| `mirror test`（9 源并发） | ~1-2 s |

## 配置文件

### 镜像源配置

**桌面 Linux**：
```
~/.config/proot-distro/mirror.json
```
（或 `$XDG_CONFIG_HOME/proot-distro/mirror.json`）

**Termux/Android**：
```
$PREFIX/etc/proot-distro/mirror.json
```

文件格式：
```json
{
  "id": "1panel-live",
  "name": "1Panel",
  "url": "https://docker.1panel.live",
  "region": "CN",
  "note": "1Panel 社区加速器",
  "updated_at": 1720000000
}
```

写入采用 atomic-replace 模式（`tmp + os.replace`），保证中断不会留下半写配置。

### 容器存储

**桌面 Linux**：`~/.local/share/proot-distro/containers/<name>/`
**Termux/Android**：`$PREFIX/var/lib/proot-distro/containers/<name>/`

每个容器目录含 `manifest.json`（元数据）和 `rootfs/`（根文件系统）。

## 环境变量

| 变量 | 用途 | 默认 |
|---|---|---|
| `PD_LANG` | 覆盖语言（`zh_CN` / `en`） | 探测 `LC_ALL`/`LANG` |
| `PD_REGISTRY_URL` | 覆盖 registry URL（优先级最高） | 无 |
| `PD_DOCKER_AUTH` | Docker 仓库鉴权 `username:password` | 无 |
| `PD_FORCE_NO_COLORS` | 设为 `true` 禁用颜色 | 无 |
| `TERMUX__PREFIX` | Termux prefix 路径 | `/data/data/com.termux/files/usr` |
| `TERMUX__HOME` | Termux home 路径 | `$PREFIX/files/home` |

## 故障排查

### 镜像源拉取失败

```bash
# 1. 测试哪些镜像源可用
proot-distro mirror test

# 2. 切换到最快的可用源
proot-distro mirror test --use-best
# 或手动切换
proot-distro mirror use <ID>

# 3. 如果还失败，检查 PD_REGISTRY_URL 是否覆盖了配置
echo $PD_REGISTRY_URL
unset PD_REGISTRY_URL

# 4. 临时用环境变量覆盖（不改配置）
PD_REGISTRY_URL=https://my-mirror.com proot-distro install ubuntu:24.04
```

### proot 未安装

```bash
# Termux
pkg install proot

# 或让 proot-distro 自动安装（交互式）
proot-distro install ubuntu:24.04
```

### 颜色显示异常

```bash
export PD_FORCE_NO_COLORS=true
```

### 中文显示乱码

确保终端 locale 支持 UTF-8：
```bash
export LANG=zh_CN.UTF-8
# 或
export LC_ALL=zh_CN.UTF-8
```

### 嵌套 proot

proot-distro 不应在 proot 内运行。如果你已经在 proot 容器里，退出后再用。

## 开发与贡献

### 项目结构

```
proot-distro-i18n/
├── proot-distro.py            # 入口脚本
├── pyproject.toml             # 项目配置
├── compile_mo.py              # .po → .mo 编译器
├── proot_distro/
│   ├── __init__.py
│   ├── cli.py                 # CLI 入口
│   ├── i18n.py                # 国际化基础设施
│   ├── mirrors.py             # 镜像源表 + 配置读写 + 探测
│   ├── message.py             # 消息输出 + 颜色
│   ├── parser.py              # argparse 构建
│   ├── paths.py               # 容器路径助手（lru_cache）
│   ├── constants.py           # 全局常量
│   ├── arch.py                # 架构检测 + get_proot_bin()
│   ├── commands/
│   │   ├── mirror.py          # mirror 子命令
│   │   ├── help/
│   │   │   ├── __init__.py    # help dispatcher（双语）
│   │   │   ├── pages.py       # 中文 help pages
│   │   │   ├── pages_en.py    # 英文 help pages
│   │   │   └── render.py      # help 渲染器
│   │   └── ...                # 其他子命令
│   ├── helpers/
│   │   └── docker/            # Docker registry 实现
│   ├── completions/           # shell 补全（bash/fish/zsh）
│   └── locales/
│       └── zh_CN/LC_MESSAGES/
│           ├── proot_distro.po   # 翻译源
│           └── proot_distro.mo   # 编译产物
├── tests/                     # 测试套件
└── .github/                   # CI/CD 配置
```

### 工具脚本

| 文件 | 用途 |
|---|---|
| `compile_mo.py` | 纯 Python `.po → .mo` 编译器（项目根目录） |

### 添加新镜像源

**方式 1：用户自定义 JSON（推荐，无需改代码）**

在 `~/.config/proot-distro/mirrors.json`（Termux: `$PREFIX/etc/proot-distro/mirrors.json`）创建一个 JSON 数组：

```json
[
  {
    "id": "my-mirror",
    "name": "我的镜像源",
    "url": "https://my-mirror.com",
    "region": "CN",
    "note": "描述"
  }
]
```

同 `id` 的条目会覆盖内置源。`mirror ls` / `mirror test` / `mirror use` 会自动包含用户自定义源。

**方式 2：编辑内置表（适合贡献者）**

编辑 `proot_distro/mirrors.py` 的 `BUILTIN_MIRRORS` 列表：

```python
{
    "id": "my-mirror",
    "name": "我的镜像源",
    "url": "https://my-mirror.com",
    "region": "CN",
    "note": "描述",
},
```

### 添加新翻译

1. 在源码里用 `_("...")` 包裹字符串
2. 在 `proot_distro/locales/zh_CN/LC_MESSAGES/proot_distro.po` 里添加 `msgid` + `msgstr`
3. 运行 `python3 compile_mo.py` 重新编译 `.mo`

## 许可证

GPL-3.0

## 致谢

- 原版 proot-distro 作者：Sylirre <sylirre@termux.dev>
- Termux 项目
- 所有镜像源维护者（DaoCloud、1Panel、毫秒镜像、轩辕镜像、gh-proxy、cnxiaobai等）
- GLM5.2