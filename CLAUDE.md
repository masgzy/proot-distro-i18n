# CLAUDE.md

Claude Code 在本仓库工作时的开发指南。

## 概述

`proot-distro-i18n` 是原版 `proot-distro` 的中文汉化分支，基于上游
v5.4.1。这是一个纯 Python 工具，用于管理基于 proot 的无 root Linux
容器。主要目标是 Termux on Android；也支持普通 Linux 主机（使用 XDG
基础目录，无 Android 专用绑定）。直接实现 OCI / Docker registry 协议，
在本地组装容器文件系统。

**无第三方 Python 依赖。** `pyproject.toml` 是版本真相来源；
`PROGRAM_VERSION` 通过 `importlib.metadata` 读取，回退到 `"rolling"`。
入口脚本 `proot-distro.py` 和控制台脚本 `proot-distro` / `pd` 都指向
`proot_distro.cli:main`。Bash/Zsh/Fish 补全位于 `proot_distro/completions/`。

仓库地址：https://github.com/masgzy/proot-distro-i18n

## 与上游的差异

本分支在上游基础上增加了以下功能：

1. **国际化（i18n）**：`i18n.py` + `locales/` 目录，默认中文（zh_CN），
   通过 `PD_LANG=en` 切换英文。所有用户可见字符串通过 `_()` 包裹，
   翻译以 GNU gettext `.po`/`.mo` 形式分发。
2. **镜像源管理**：`mirrors.py` + `commands/mirror.py`，新增 `mirror`
   子命令（ls/test/use/unset/show），内置 17 个镜像源，支持用户自定义
   JSON 配置文件。
3. **性能优化**：`lru_cache` 缓存路径计算和架构检测、并发探测镜像源、
   `frozenset` proot 豁免集合、nested-proot 探测短路等。
4. **帮助系统双语**：`commands/help/pages.py`（中文）+
   `pages_en.py`（英文），按 `PD_LANG` 自动切换。
5. **信号处理增强**：`SIGTERM`/`SIGHUP` 也路由到 `KeyboardInterrupt`，
   确保终端关闭或 kill 默认信号也能触发清理逻辑。
6. **help 优先渲染**：`-h`/`--help` 在 proot-on-PATH 检查之前分发，
   确保未安装 proot 时也能查看帮助。
7. **session.py 优化**：先探测存活再读 JSON，避免为即将清理的死会话
   做昂贵的 JSON 解析。
8. **login 优化**：`read_passwd_entry()` 单次读取 `/etc/passwd`，
   替代上游多次 `read_passwd_field()` 调用。

## 纯 Python 策略

系统查询不使用子进程：ANSI 而非 `tput`，`pwd`/`grp` 而非 `id`，
`struct.unpack` 解析 ELF 而非 `file`，`ctypes.personality()` 而非
`lscpu`，`urllib` 而非 `docker`/`curl`，`tarfile` 而非 `tar`。唯一
运行的外部程序是 `proot`（通过 `os.execvpe`）和——在 Termux 上安装时
仅当用户同意时——`pkg install -y -q proot`。

## 模块布局（`proot_distro/`）

顶层工具模块（各自负责一个关注点）：

- `constants.py` — `IS_TERMUX`、`TERMUX_PREFIX/HOME/APP_PACKAGE`、
  `RUNTIME_DIR`、`BASE_CACHE_DIR`、`CONTAINERS_DIR`、`SESSIONS_DIR`、
  `LAYER_CACHE_DIR`、`MANIFEST_CACHE_DIR`、`DEFAULT_PATH_ENV`、
  `DEFAULT_FAKE_KERNEL_*`。
- `i18n.py` — **自定义**：国际化基础设施。`_()` 翻译函数、
  `set_language()`、`get_language()`。默认 zh_CN，通过 `PD_LANG`
  环境变量切换。翻译文件位于 `locales/zh_CN/LC_MESSAGES/`。
- `mirrors.py` — **自定义**：镜像源管理。`BUILTIN_MIRRORS` 内置表、
  `resolve_registry_url()` 解析生效源、`test_mirror()` 探测可达性、
  配置文件读写（atomic-replace）。
- `message.py` — 颜色字典 `C`、`msg`、`log_info/error`、`warn`、
  `crit_error`、`set_quiet`/`is_quiet`、`tty_safe_for_writes`。
  `msg()` 仅在 TTY 时写 `\r\033[K` 清行（v5.4.1 修复）。
- `progress.py` — `fmt_size`、`ByteCounter`、`draw_bytes_bar`、
  `draw_count_bar`、`clear_bar`、`progress_active`。
- `arch.py` — `get_device_cpu_arch`（`@lru_cache`）、
  `detect_installed_arch`（ELF magic）、`normalize_arch`、
  `get_emulator_args`、`get_proot_bin`（**v5.4.1 新增**，支持
  `PD_PROOT_BIN` 环境变量）、`ARCH_UNAME_M`。
- `atomic.py` — `atomic_replace()`：mkstemp + `os.replace`；
  在 `BaseException` 时清理（Ctrl-C 永不留半写文件）。
- `l2s.py` — `--link2symlink` 助手（SIGINT/SIGQUIT 屏蔽）。
- `locking.py` — `ContainerLock`、`BuildLock`（POSIX flock）。
- `session.py` — `ps` 用的活跃会话注册表：`register_session`
  （可继承 flock 在 `execvpe` 后存活，与容器锁一样；记录 `detach`
  标志等会话元数据）、`active_sessions`（读取 `SESSIONS_DIR`，
  通过共享 flock 探测清理死条目）。
- `names.py` — `_NAME_RE`、`is_valid_name`、`require_valid_name`。
- `parser.py` — argparse、`ALIAS_TO_CANONICAL`、`REQUIRED_ARGS`、
  `_PdArgumentParser`（出错时显示对应命令帮助）。
- `paths.py` — `container_dir/_rootfs/_manifest`（`@lru_cache`）、
  `[name:]path` spec 解析器、`container_locks_for_spec_pair`。
- `sysdata.py` — `setup_fake_sysdata`、`fake_proc_bindings`。
- `cli.py` — `main()`：SIGQUIT/SIGTERM/SIGHUP 路由、root 警告、
  嵌套 proot 拒绝、proot 探测、解析、分发。`ensure_proot_installed()`
  为公共函数（v5.4.1 重构），供启动检查和 build 共用。

命令（`commands/`）：`backup`、`build`、`clear_cache`、`copy`、
`install`（+`install_local`）、`kill`、`list`、`ps`、`push`、`remove`、
`rename`、`reset`、`restore`、`run`、`sync`、`mirror`（**自定义**）；
子包 `help/{pages,pages_en,render}` 和
`login/{bindings,detach,env,migrate,passwd,proot_cmd,quoting}`。

助手（`helpers/`）：`build_cache`、`dockerfile`、`download`、
`layer_diff`、`oci_writer`、`rootfs`、`tar_extract`；子包
`build_engine/{constants,copy_step,dockerignore,engine,errors,handlers,
parsing,run_step,stage,users}` 和 `docker/{cache,layers,media,pull,
push,refs,transport}`。

## 关键路径

| 常量 | Termux | 非 Termux |
|---|---|---|
| `RUNTIME_DIR` | `$TERMUX_PREFIX/var/lib/proot-distro` | `$XDG_DATA_HOME/proot-distro` |
| `BASE_CACHE_DIR` | `$RUNTIME_DIR/cache` | `$XDG_CACHE_HOME/proot-distro` |
| `CONTAINERS_DIR` | `$RUNTIME_DIR/containers` | 同左 |
| `SESSIONS_DIR` | `$RUNTIME_DIR/sessions` | 同左 |
| `LEGACY_ROOTFS_DIR` | `$RUNTIME_DIR/installed-rootfs`（仅迁移） | 同左 |
| `LAYER_CACHE_DIR` | `$BASE_CACHE_DIR/oci_layers` | 同左 |
| `MANIFEST_CACHE_DIR` | `$BASE_CACHE_DIR/oci_manifests` | 同左 |
| Build cache index | `$BASE_CACHE_DIR/build_cache_index.json` | 同左 |
| 镜像源配置 | `$PREFIX/etc/proot-distro/mirror.json` | `$XDG_CONFIG_HOME/proot-distro/mirror.json` |

## Termux 检测（`constants._detect_termux`）

当**三个条件中两个**成立时为 True：Android 信号（`platform.platform()`
提到 android，或 `/system/build.prop`/`/data/app` 存在）；Termux 环境变量
（`TERMUX_APP__APP_VERSION_NAME` 或 `TERMUX_VERSION`）；`TERMUX_PREFIX`
可读 + 可执行。在导入时计算一次；驱动路径选择、`DEFAULT_PATH_ENV`、
argparse 中 Termux 专用标志（`--isolated`、`--minimal`、`--no-link2symlink`、
`--no-sysvipc`、`--no-kill-on-exit`）的可用性，以及 `login`/`build`
在非 Termux 主机上跳过 proot 扩展 + Android 绑定。

## 容器存储和类型

```
containers/<name>/manifest.json   ← image_ref, arch, manifest, image_config
containers/<name>/rootfs/         ← 组装好的文件系统
```

目录名是唯一标识符。纯 tarball 安装**不**写 `manifest.json`。旧版
`installed-rootfs/<name>` 布局在首次 `login` 时迁移
（`commands/login/migrate.py`），然后重写 l2s 符号链接目标。

发行版类型在登录时检测：
`rootfs/data/data/com.termux/files/usr/bin/login` 作为**文件**存在
（不是目录——proot 可能在并发会话期间物化 bind-mount 目标目录）
⇒ `termux`；否则 `normal`。`termux`：不用 `--link2symlink`，不用
`--change-id`；硬编码 HOME/PATH/PREFIX/TMPDIR；镜像 Env + Android
宿主变量像 `normal` 一样应用；Android 系统绑定 + 共享存储 + Dalvik/ART
缓存（`/data/app`、`/data/dalvik-cache`、
`/data/misc/apexdata/com.android.art/dalvik-cache`）在非隔离时开启
（隔离/精简时关闭）；宿主的 Termux 应用目录 `/data/data/com.termux`
**永不**绑定（容器自带，只在其 rootfs 内创建 `cache` 目录）；Termux
prefix 不绑定（容器在相同路径有自己的）。**跨架构被拒绝**——宿主和
容器共享 `TERMUX_PREFIX`，宿主二进制会遮蔽容器的。

## 命令和锁

| 命令 | 别名 | 锁 |
|---|---|---|
| `install` | `add`, `i`, `in`, `ins` | 容器独占 |
| `remove` | `rm` | 容器独占 |
| `rename`, `reset` | — | 容器独占 |
| `login` | `sh` | 容器共享（fd 由 proot 继承） |
| `run` | — | 容器共享（fd 由 proot 继承） |
| `list` | `li`, `ls` | 无 |
| `ps` | — | 无（读取会话注册表，清理死条目） |
| `kill` | — | 无（读取会话注册表，发信号给 PID） |
| `backup` | `bak`, `bkp` | 容器共享 |
| `restore` | — | 容器独占，按首个 TarInfo 延迟加锁 |
| `clear-cache` | `clear`, `cl` | 无 |
| `copy` | `cp` | 源共享，目标独占 |
| `sync` | — | 源共享，目标独占 |
| `build`, `push` | — | `BuildLock`，键为 `(image_ref, arch)` |
| `mirror` | — | 无（仅 HTTP 探测，不调用 proot） |
| `help` | `h`, `he`, `hel` | 无 |

`install` 接受镜像引用、本地路径（必须以 `/`、`./`、`../` 或 `~`
开头）或 `http(s)://` URL。`--user` 接受名称、数字 uid 或
`user:group`。

## CLI 流程（`cli.main()`）

1. SIGQUIT/SIGTERM/SIGHUP → `KeyboardInterrupt`，使所有现有 `except`
   块像处理 Ctrl-C 一样处理它们（进度条清理、部分文件删除、"已被
   用户中断"）。**本版额外将 SIGTERM 和 SIGHUP 也路由进来**。
2. Root 警告（非致命）；嵌套 proot 拒绝（读取 `/proc/<pid>/status`，
   跟随一个 TracerPid 跳跃）。
3. proot 探测；在 Termux + TTY 时提供 `pkg install`。**`build`、
   `push`、`kill`、`ps`、`mirror` 和 `help` 豁免**（`kill`/`ps` 仅
   信号或读取运行中的会话；`mirror` 仅做 HTTP 探测；`build` 通过
   `build_engine.needs_proot()` 自行门控——仅当有 RUN 系列指令时
   才检查（**v5.4.1 新增**）；`help` 应总能渲染。**本版将 `-h`/
   `--help` 提前到 proot 探测之前分发**。
4. 每命令 `-h`/`--help`/`--usage` 在 argparse **之前**拦截，使缺失
   必填位置参数不会报错而是显示帮助。未知子命令也在预解析阶段拒绝。
5. `parse_known_args()` + 手动处理字面量 `--` 之后的 token
   （`login`/`run` 内部命令）。
6. `REQUIRED_ARGS` 检查。`restore` 故意缺席——它根据 stdin TTY 状态
   决定。
7. `--quiet`：在分发前 `set_quiet(True)`，除非命令是 `list`（其
   `--quiet` 含义不同：仅输出容器名）、`ps`（仅 PID）或 `mirror`
   （仅 URL）。`log_info()` 变为空操作；错误/警告/`msg()` 总是显示。

## 锁定

`ContainerLock` → `RUNTIME_DIR/locks/<name>.lock`。`BuildLock` →
`RUNTIME_DIR/locks/build/<sha256-prefix>.lock`，键为
`sha256("<image_ref>_<arch>")` 的前 16 个十六进制字符（与 manifest
缓存键相同）。

非阻塞 `flock(2)`。冲突 ⇒ 立即退出，报告持有者的 PID + 命令。通过
`_held_exclusive` 实现可重入——`reset` 获取锁后为同名调用 `install`；
install 的获取检测到路径并跳过。Login/run 传 `inheritable=True` 清除
`O_CLOEXEC`，使 fd 在 `os.execvpe` 后存活。`disown()` 标记锁使
`release()` 关闭 fd **但不** `LOCK_UN`——用于 `--detach`，fork 出的
守护进程继承同一个打开文件描述且必须在前台退出 `with` 块后保持锁
（flock 在任何副本 `LOCK_UN` 时释放，或所有副本关闭后释放）。多锁
通过 `ExitStack` 按排序路径顺序获取。`BuildLock` 仅覆盖输出
`(image_ref, arch)`；不同 tag 的并发构建仍可能在共享缓存上竞争，
安全因为每个写入者使用 `atomic.atomic_replace()` 且 `build_cache`
在索引的 RMW 上持有自己的 flock。

## 架构

`detect_installed_arch(rootfs)` 从常见 shell 二进制读取 ELF e_machine。
`normalize_arch()` 接受原生名称、裸 Docker 名称（`arm64`/`amd64`/
`386`）和 `linux/` 前缀形式。原生 32-on-64：`aarch64` 当
`personality(PER_LINUX32)` 成功时运行 `arm`；`x86_64` 总是运行
`i686`。否则 `get_emulator_args()` 选择 `qemu-<arch>` 并为 QEMU 的
loader 绑定 Android 系统路径。proot 的 `--kernel-release` `uname_m`
字段来自 `ARCH_UNAME_M`，不是宿主 uname，使模拟容器自报正确。

`get_proot_bin()`（**v5.4.1 新增**）返回要调用的 proot 可执行文件，
支持 `PD_PROOT_BIN` 环境变量覆盖，验证模式与 `--emulator` 相同。

## Docker / OCI 仓库（`helpers/docker/`）

拉取是 manifest-cache-first：已缓存 + 所有层存在 ⇒ 完全离线；已缓存
+ 缺失层 ⇒ 获取 token + 缺失层；否则完整流水线（token → manifest →
arch 解包 → config blob → layers）。缓存写入使用 `atomic_replace`。
层摘要通过 `hashlib.sha256` 流式验证后再提升。摘要在转换为文件系统
路径前通过 `validate_digest()` 验证（层缓存、OCI blob 布局），使
`../foo:bar` 这样的恶意引用无法逃逸缓存根目录。拒绝 `zstd` mediaType
（Python `tarfile` 不支持 zstd）。Whiteouts（`.wh..wh..opq` 清空父
目录；`.wh.<name>` 删除同级条目）、硬链接 linkname 过滤和成员名遍历
防护位于 `helpers/tar_extract.py`。

**本版新增多线程层下载**：`_download_layers_concurrent()` 使用
`ThreadPoolExecutor` 并行下载缺失层，`download_blob` 新增 `timeout`
和 `quiet` 参数。

鉴权（`transport.py`）：`PD_DOCKER_AUTH=user:pass` 作为 HTTP Basic
转发到 token 端点；冒号必须存在（裸 token 抛 `RuntimeError`）。
`AuthStrippingRedirectHandler` 在跨主机重定向时丢弃 `Authorization`
（Docker Hub CDN blob URL 拒绝 Bearer，返回 HTTP 400）。
`get_auth_token(repo, registry, actions)` 接受 `"pull"`（默认）或
`"pull,push"`。

推送（`push.py`）从本地缓存加载 `(manifest, repo, image_config)`，
重新规范化并验证 SHA 与 `manifest.config.digest` 匹配，HEAD 探测每个
blob，通过 POST-uploads + 单体 PUT 上传缺失的（无分块、无跨仓库挂载、
无多架构索引）。401/403 ⇒ `push_denied_msg`。

## 镜像源管理（`mirrors.py` + `commands/mirror.py`）— 自定义

原版 proot-distro 把单一 registry URL 硬编码在 `transport.py` 中。
本版新增 `mirror` 子命令，把"换源"做成一等公民。

- `BUILTIN_MIRRORS`：内置 17 个镜像源（DaoCloud、1Panel、毫秒镜像、
  轩辕镜像等），每个含 id/name/url/region/note。
- `resolve_registry_url()`：按 `PD_REGISTRY_URL` 环境变量 > 配置文件 >
  默认值的优先级解析生效 URL。
- `test_mirror()`：并发探测（`ThreadPoolExecutor`），复用
  `insecure_ssl_context`。
- 配置文件：XDG `~/.config/proot-distro/mirror.json`（桌面）或
  `$PREFIX/etc/proot-distro/mirror.json`（Termux），atomic-replace 写入。
- 用户自定义镜像源：`~/.config/proot-distro/mirrors.json`（JSON 数组），
  同 id 覆盖内置源。

## 登录环境（`commands/login/`）

`child_env` 显式构建并传给 `os.execvpe`——不用 `env -i` 包装，宿主环境
**不**传播。`normal` 类型优先级（后者覆盖前者）：PATH/MOZ_FAKE_NO_SANDBOX/
PULSE_SERVER 基线（非 minimal）→ 镜像 `Env`（由 `IMAGE_ENV_BLOCKED`
过滤：Android 变量、MOZ/PULSE、TERM/COLORTERM）→ Android 宿主变量
（`ANDROID_HOST_ENV_VARS`，Termux + 非隔离非精简）→ 用户 `--env` →
HOME/USER（非 minimal）→ TERM/COLORTERM。镜像 `Env` 和 `--env` 在
**每种**模式下都应用（包括隔离和精简）；只有 Android 宿主变量受默认
模式门控。非 Termux 主机不继承宿主变量。PATH 不被阻止但
`TERMUX_PREFIX/bin` 在镜像 Env 之后去重 + 追加（非隔离、非精简）。
`termux` 类型在其硬编码 HOME/PATH/PREFIX/TMPDIR 基线上使用相同的
镜像 Env + Android 宿主变量逻辑。

`inject_termux_profile()` 写入 `/etc/profile.d/termux-profile.sh`，使
`su - other` 不会丢失 proot-distro 设置的变量：PATH 用 POSIX case-guard
追加；其余用 `export K='V'`（含 `'\''` 惯用法），但每会话和 proot 内部
变量除外（HOME/USER/TERM/COLORTERM/PATH/PROOT_*/LD_*）。键先匹配标识符
正则 `^[A-Za-z_][A-Za-z0-9_]*$`；任何会破坏源脚本的内容（空格、`;`、
引号…）被静默丢弃。旧版 `termux-prefix.sh` 先删除。

`minimal` 清除几乎所有内容：镜像 `Env` + `--env` + `TERM`（默认
`xterm-256color`）+ 继承的 `COLORTERM`；无基线 PATH、无 MOZ/PULSE、
无 Android 宿主变量、无 HOME/USER。`PROOT_L2S_DIR` 钉到 `rootfs/.l2s`
（提前创建）用于 Termux 上的 `normal`，使并发会话一致。`LD_PRELOAD`
在 exec 前剥离。

## Run / build

`command_run()` 从 `manifest.json` 读取 `Entrypoint`/`Cmd`/`WorkingDir`，
按 Docker 语义构建 `inner`，通过 `args._run_inner` 委托给 `command_login`。
`--work-dir` 覆盖 `WorkingDir`；默认为 `/`（不是用户 home）。

`-d`/`--detach`（login + run，通过 `_add_login_or_run_common`）将
会话后台化：所有设置完成后，`_command_login_inner` 将最终 exec 委托给
`commands/login/detach.spawn_detached` 而非 `register_session` +
`execvpe`。这是一个双 fork 守护进程（`setsid`、std fds → `/dev/null`）；
`register_session` 在孙进程中运行，使 `getpid()` 已等于未来 proot PID，
管道将 PID 传回前台。孙进程继承前台的容器锁 fd，所以前台调用
`lock.disown()`（跳过 `LOCK_UN`）让守护进程保持锁。`--get-proot-cmd`
在 detach 分支前短路。会话在 `ps` 中 TYPE 标记为 `login*`/`run*`；
用 `proot-distro kill` 停止。

`command_kill()`（`commands/kill.py`）通过向**整个客机进程树**发信号
来停止会话，而不仅仅是根 proot——`proot` 的 `--kill-on-exit` 清理仅在
优雅退出时运行（所以 `kill -9` 会孤立客机），且在非 Termux 上完全不存在。
目标是 PID、容器名（其所有会话）或 `--all`，始终通过 `active_sessions()`
解析，使只有被追踪的 proot 会话能被命中。它读取 `/proc/<pid>/status`
的 `PPid:` 构建 `pid→ppid` 映射（`_read_pid_ppid`），遍历每个根下的
传递闭包（`_collect_tree`，纯函数 + 环路安全），两轮 `os.kill` 每个成员
（不含 self/pid 0/pid 1）。默认信号为 `SIGTERM`；`-s/--signal` 接受名称
或数字。PID 重用安全带：仅当 `/proc/<root>/comm` 读为 `proot` 时才遍历。
不加锁；纯 Python（无 `pkill`/`pgrep`）。

`command_build()` 解析 Dockerfile，运行 `BuildEngine`，写入 manifest
缓存（变体 A — 小 JSON；层 blob 已在 `LAYER_CACHE_DIR`），可选写 OCI
tarball（变体 B — 同时包含标准 OCI 布局**和** Docker 旧版 `manifest.json`
使 `docker load` 可用）和/或为 `--install-as` 调用 `command_install`。

**v5.4.1 新增**：build 在解析 Dockerfile 后通过 `needs_proot(instructions)`
门控——仅当含 RUN 系列指令时才调用 `ensure_proot_installed()`。

`helpers/dockerfile.py` 处理续行、解析器指令（`syntax`/`escape`）、
ADD/COPY/RUN 中的 here-doc、JSON exec 格式检测，以及 `$VAR`/
`${VAR:-default}` 系列的 `expand_vars()`。

`BuildEngine` 预扫描全局 ARG 和命名阶段（提前验证 `--target`），然后
分发给 `HANDLERS`（元数据）、`do_run` 或 `do_copy_or_add`。FROM 解析
`scratch`、命名阶段（重放缓存层）或通过 `pull_image()` 拉取外部镜像。
基础镜像 `OnBuild` 触发器在 FROM 后执行。

Termux 下 RUN 使用 `--link2symlink`。为保持产出的层可移植，
`layer_diff.snapshot()` 跳过 `<rootfs>/.l2s/`，`_add_entry()` 跟踪指向
它的符号链接以将 backing file 内容打包为常规文件（硬链接语义丢失，
内容保留）。构建步骤隔离运行且非交互（`stdin=/dev/null`，除非 here-doc）。

构建缓存：`compute_recipe_hash(parent_digest, instr, extra)` 键入
`build_cache_index.json`。命中 ⇒ 应用缓存层，跳过 proot。
`build_cache.record()` 在索引上持有自己的 flock。`clear-cache` 删除
`BASE_CACHE_DIR` 下的顶层条目，包括索引。

## 备份 / 恢复

纯 `tarfile`。归档格式：`<name>/manifest.json` + `<name>/rootfs/...`。
备份应用 `_fix_permissions()`（chmod-000 子目录变可读），过滤设备/FIFO/
套接字，uid/gid/uname/gname 清零；拒绝在无 `--output` 时写入 TTY。
恢复自动检测压缩（`tarfile r|*` 文件；stdin 时 magic-byte peek），通过
`_dest_path()` 将成员路由到 `containers/<name>/...`，重新根化旧版
`installed-rootfs/<name>`。遍历被阻止（`..`/`.`/空被丢弃；容器名必须
匹配 `_NAME_RE`）。每个容器的首个条目触发 rootfs 清除 + 加锁。

两者都在 `tty_safe_for_writes()` 返回 False 时跳过 stderr 写入——当
sibling pinentry/curses 占用 TTY（termios 中 ECHO 或 ICANON 被清除）时，
`msg()` 和进度行被静默丢弃，使 `backup | gpg -c` 不会破坏密码输入提示。

## 帮助系统

数据在 `commands/help/pages.py`（中文，`HELP_PAGES`、`TOP_COMMANDS`）
和 `pages_en.py`（英文）中；`render.py` 格式化。`term_width()` 钳到
`[32, 92]`，低于 60 列时选项垂直堆叠（Termux 手机）。`HELP_COMMANDS`
将每个名称映射到一个零参数渲染器供 CLI 分发。`__init__.py` 按
`get_language()` 选择 pages 或 pages_en，惰性导入（仅在渲染帮助时）。

## 国际化（i18n）— 自定义

- `i18n.py` 导出 `_()` 翻译函数，所有模块从 `proot_distro.i18n` 导入。
- 默认中文（zh_CN），通过 `PD_LANG=en` 切换英文。
- 翻译文件：`locales/zh_CN/LC_MESSAGES/proot_distro.po`（源）→
  `.mo`（编译产物）。编译器为项目根目录的 `compile_mo.py`。
- `set_language()` 可运行时切换语言；`get_language()` 返回当前语言。
- `N_()` 仅标记字符串可翻译但不立即翻译（用于 dict 字面量）。
- 开发期无 `.mo` 文件时不报错——`_()` 直接返回原字符串。

## 约定

- 包内每个 Python 文件带许可证头部。
- 容器名：`^[A-Za-z0-9][A-Za-z0-9_.\-]*$`，在每个入口点通过
  `names.require_valid_name()` 强制（镜像引用派生的别名、`--install-as`、
  restore 中的归档成员）。
- `--bind`：源 ⇒ `os.path.abspath`；目标必须是绝对路径（或省略）。
  与已有目标重叠 ⇒ 黄色警告，仍然添加。
- 每个缓存写入者必须使用 `atomic.atomic_replace()`。
- 新命令需接入 `cli._COMMAND_HANDLERS`、`parser`（标记 `_pd_command`）、
  如有位置参数则加入 `REQUIRED_ARGS`、`commands/help/pages.HELP_PAGES`
  和 `pages_en.HELP_PAGES`、`ALIAS_TO_CANONICAL`（别名）。
- 新增用户可见字符串必须用 `_("...")` 包裹，并在 `.po` 文件中添加翻译。
