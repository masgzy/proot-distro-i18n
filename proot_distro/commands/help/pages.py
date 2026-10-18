#
# Proot-Distro - manage proot containers.
#
# Created by Sylirre <sylirre@termux.dev> for Termux project.
# Development assisted by Claude Code (https://claude.ai/code).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

# Architecture: All help-page content as plain Python data. Each entry
# in HELP_PAGES is a dict consumed by render.render_page. Termux-only
# options are gated with `*([...] if IS_TERMUX else [])` so the help
# stays in sync with what argparse actually accepts on the current host.
#
# v3 (2026-07-08): 全文汉化，遵循 Termux 中文社区术语习惯。
# 保留英文的项：命令名、usage 语法、option flag (如 -h, --help)、
# 环境变量名、URL、镜像引用示例。

from proot_distro.constants import (
    IS_TERMUX, PROGRAM_NAME, CANONICAL_PROGRAM_NAME, TERMUX_APP_PACKAGE,
)


# What a guest may ask about where it is running. Both `login` and `run`
# say it, so it is written once.
_IDENTITY_NOTE = (
    "The guest environment carries PD_CONTAINER (the container's name), "
    "PD_IMAGE (the image reference it was installed from), PD_IMAGE_ID "
    "(the image's config digest) and container=proot-distro, in every "
    "mode. PD_IMAGE and PD_IMAGE_ID are unset for a container installed "
    "from a plain rootfs tarball, which carries no image metadata. Your "
    "own --env entries override them; the image's Env cannot."
)


HELP_PAGES = {
    "build": {
        "usage": "build [OPTIONS] [PATH]",
        "summary": (
            "从 Dockerfile 构建兼容 OCI/Docker 的镜像。"
            "\n\n"
            "PATH 是包含 Dockerfile 的构建上下文目录（默认: '.'）。"
            "所有 COPY/ADD 的源路径都相对于该目录解析。上下文中的 "
            "'.dockerignore' 文件可用于排除 COPY/ADD 的匹配项。"
            "\n\n"
            "默认情况下，镜像会以 --tag 指定的标签（默认: PATH 的"
            " basename 加 ':latest'）存入本地清单缓存。存入后，"
            f"'{PROGRAM_NAME} install <tag>' 会优先按标签查缓存，"
            "完全离线安装。"
            "\n\n"
            "使用 --output FILE 可额外输出一个独立的 OCI image-layout "
            "tarball，'docker load' 和 "
            f"'{PROGRAM_NAME} install FILE' 都能识别。"
            "\n\n"
            "使用 --install-as NAME 可在构建完成后一步把镜像装成容器。"
        ),
        "options": [
            ("-h, --help", "显示本帮助。"),
            ("-f, --file [PATH]",
             "使用 PATH 处的 Dockerfile，而非 <PATH>/Dockerfile。"
             "传 '-' 可从标准输入读取 Dockerfile。"),
            ("-t, --tag [REF]",
             "要赋予的镜像引用。可重复指定。默认为 "
             "'<basename(PATH)>:latest'。"),
            ("--build-arg [K=V]",
             "设置构建期 ARG。只有 Dockerfile 中声明过的 ARG 才生效。"
             "可重复指定。"),
            ("-a, --architecture [ARCH]",
             "目标 CPU 架构（默认: 宿主架构）。"
             f"接受 {PROGRAM_NAME} 命名（aarch64, arm, i686, "
             "riscv64, x86_64）或 Docker platform 字符串 "
             "（linux/arm64, linux/amd64, ...）。"),
            ("--target [STAGE]",
             "多阶段构建时，在指定 stage 之后停止。"),
            ("--emulator [PATH]",
             "覆盖用于跨架构构建的 QEMU 用户模式二进制。"),
            ("-o, --output [FILE]",
             "把构建好的镜像以 OCI tarball 写入 FILE。"
             "压缩方式由扩展名推断（.oci.tar, .oci.tar.gz, .oci.tar.xz）。"
             "可重复指定。"),
            ("--install-as [NAME]",
             "构建完成后把镜像安装为名为 NAME 的容器。"),
            ("--no-cache",
             "禁用构建步骤缓存。每条指令都重新执行。"),
            ("-v, --verbose",
             "回显每条指令，并将 RUN 输出流式打印到终端。"),
            ("-q, --quiet",
             "抑制非错误输出。与 --verbose 互斥。"),
        ],
        "examples": [
            f"{PROGRAM_NAME} build -t myapp:1.0 .",
            f"{PROGRAM_NAME} build -t myapp:1.0 --output myapp.oci.tar.gz .",
            f"{PROGRAM_NAME} build -t myapp --install-as myapp .",
            f"{PROGRAM_NAME} build -f Dockerfile.arm "
                f"--architecture aarch64 .",
        ],
        "footer": [
            {
                "title": "PROOT 依赖",
                "intro": (
                    "如果 Dockerfile 含有任何 RUN（或 ONBUILD RUN）指令，"
                    "宿主必须安装 proot——因为 RUN 会在 proot 下对构建中的"
                    " rootfs 执行给定命令。仅由 FROM、COPY、ADD、ENV、ARG、"
                    "LABEL、USER、WORKDIR、CMD、ENTRYPOINT、EXPOSE、VOLUME、"
                    "STOPSIGNAL、HEALTHCHECK、SHELL、MAINTAINER 和 "
                    "ONBUILD<非 RUN> 组成的 Dockerfile 走纯 Python 模式，"
                    "无需 proot。"
                ),
            },
            {
                "title": "构建完成后",
                "intro": (
                    "未指定 --output 和 --install-as 时，镜像仅存入本地缓存。"
                    f"'{PROGRAM_NAME} install <tag>' 会优先按标签查缓存；"
                    "当清单与所有层都已缓存时，安装过程无需联网。"
                ),
            },
            {
                "title": "限制",
                "intro": (
                    "RUN 步骤在 proot 下运行，并非真正的容器运行时。"
                    "没有 PID、网络、IPC 隔离，没有 cgroups，没有 seccomp 配置。"
                    "BuildKit 专属特性（RUN --mount、--network、--security；"
                    "COPY --link、--parents）会被拒绝并报错。不生成多架构"
                    " manifest list——每个架构需单独构建。"
                ),
            },
        ],
    },

    "push": {
        "usage": "push [OPTIONS] IMAGE",
        "summary": (
            "把本地构建的镜像推送到 Docker/OCI 仓库。镜像必须先由 '"
            f"{PROGRAM_NAME} build -t IMAGE' 产生；清单和 blob 直接从"
            "本地缓存读取。"
            "\n\n"
            "IMAGE 与传给 'build -t' 的引用相同，例如 "
            "'myuser/myapp:1.0'（Docker Hub）或 "
            "'ghcr.io/myorg/myapp:1.0'（自定义仓库）。未带 tag 时"
            "自动追加 ':latest'。"
            "\n\n"
            "默认架构与宿主一致。使用 --architecture 可推送为其他目标"
            "架构构建的镜像（清单缓存按 IMAGE+arch 作键）。"
            "\n\n"
            "仓库中已存在的层和镜像配置 blob 会通过 HEAD 请求检测并跳过，"
            "因此重新推送未变更的镜像时只传输很小的清单。"
            "\n\n"
            "私有仓库需要鉴权。在运行 push 之前设置 "
            "PD_DOCKER_AUTH=\"user:password\"（或 "
            "\"user:personal-access-token\"）。允许匿名 push 的自托管"
            "仓库则无需设置 PD_DOCKER_AUTH。"
        ),
        "options": [
            ("-h, --help", "显示本帮助。"),
            ("-a, --architecture [ARCH]",
             "推送为指定架构构建的清单。"
             f"接受 {PROGRAM_NAME} 命名（aarch64, arm, i686, "
             "riscv64, x86_64）或 Docker platform 字符串 "
             "（linux/arm64, linux/amd64, ...）。默认: 宿主架构。"),
            ("--allow-insecure",
             "允许到目标仓库的不安全传输：走明文 HTTP 的自定义仓库，"
             "或 TLS 证书不受信任、过期、自签、hostname 不匹配的 HTTPS "
             "仓库。仅在你信任的网络路径上对可信仓库使用。"),
            ("-q, --quiet", "抑制非错误输出。"),
        ],
        "examples": [
            f"{PROGRAM_NAME} push myuser/myapp:1.0",
            f"{PROGRAM_NAME} push ghcr.io/myorg/myapp:1.0",
            f"{PROGRAM_NAME} push --architecture aarch64 myuser/myapp:1.0",
            f"{PROGRAM_NAME} push --allow-insecure 192.168.1.10:5000/app:1.0",
        ],
        "footer": [
            {
                "title": "鉴权",
                "intro": (
                    "在运行 push 之前以 'username:password' 格式设置 "
                    "PD_DOCKER_AUTH。冒号是必需的；不带用户名的裸 token "
                    "无法使用，因为仓库鉴权需要用 Basic 凭据换 token。"
                    "对于 GitHub Container Registry，password 处使用具有"
                    " 'write:packages' scope 的 personal access token。"
                ),
                "examples": [
                    "export PD_DOCKER_AUTH=user:password",
                    f"{PROGRAM_NAME} push ghcr.io/myorg/myapp:1.0",
                ],
            },
            {
                "title": "注意事项",
                "intro": (
                    "不生成多架构 manifest list。要发布多架构镜像，请为每个"
                    "架构分别 build + push 到同一 tag——仓库会用最新推送的"
                    "清单覆盖该 tag。生成指向多个单架构清单的 manifest "
                    "index 不在范围内。"
                ),
            },
        ],
    },

    "backup": {
        "usage": "backup [OPTIONS] CONTAINER",
        "aliases": ("bak", "bkp"),
        "summary": (
            "把指定容器备份为 TAR 归档。压缩方式由输出文件扩展名或 "
            "--compress 选项决定。输出到 stdout 时默认不压缩。"
        ),
        "options": [
            ("-h, --help", "显示本帮助。"),
            ("-c, --compress [TYPE]",
             "强制使用指定压缩算法，覆盖文件扩展名推断。"
             "可选值: gzip, bzip2, xz, none。"),
            ("-o, --output [FILE]",
             "把归档写入 FILE 而非 stdout。未指定 --compress 时，"
             "压缩方式由文件扩展名推断，如 tar.gz 或 txz。"),
            ("-v, --verbose",
             "每加入一个文件就打印其文件名。"),
            ("-q, --quiet",
             "抑制非错误输出。与 --verbose 互斥。"),
        ],
        "examples": [
            f"{PROGRAM_NAME} backup ubuntu --output ~/ubuntu.tar.xz",
        ],
    },

    "clear-cache": {
        "usage": "clear-cache [OPTIONS]",
        "aliases": ("clear", "cl"),
        "summary": (
            "清空下载缓存中的所有文件（如 Docker 镜像层）。\n\n"
            "默认清空整个缓存；--orphan 仅删除不再被引用的孤立层，"
            "--build-cache 清空构建缓存（按步索引及其产物层）。"
        ),
        "options": [
            ("-h, --help", "显示本帮助。"),
            ("-v, --verbose", "每删除一个文件就打印其路径。"),
            ("-q, --quiet",
             "抑制非错误输出。与 --verbose 互斥。"),
            ("--orphan",
             "仅删除没有任何东西再引用的层 blob，保留缓存其余部分。"),
            ("--build-cache",
             "清空构建缓存：按步索引（build_cache_index.json）"
             "及其记录的各层。这会让下一次构建重新运行每一步。"),
        ],
        "examples": [
            f"{PROGRAM_NAME} clear-cache --orphan",
            f"{PROGRAM_NAME} clear-cache --build-cache",
        ],
        "footer": [
            {
                "title": "孤立层（ORPHAN LAYERS）",
                "intro": (
                    "层 blob 在被某个缓存镜像列出、或被某条构建缓存"
                    "条目引用期间属于“被引用”状态；其余即为孤立层，"
                    "由失败的构建、替换了某步输出的重新构建、或从"
                    "本地 OCI 归档安装的镜像遗留在磁盘上。已安装的"
                    "容器不持有任何层（它们的 rootfs 是独立副本），"
                    "因此永不受影响。由于构建缓存也算作一种引用，"
                    "--orphan 永远不会缩减它；那正是 --build-cache "
                    "的职责。在另一个 " + PROGRAM_NAME + " 命令持有锁"
                    "时，清理会拒绝运行，因为进行中的构建在磁盘上有"
                    "尚无引用的层。"
                ),
            },
            {
                "title": "构建缓存（BUILD CACHE）",
                "intro": (
                    "构建执行的每条 RUN 都会连同其产物层一起记录在 "
                    "build_cache_index.json 中，这样后续相同父层、"
                    "相同指令与输入的构建会直接应用该层而不是重新"
                    "运行该步。这些条目永远不会被驱逐，而每次编辑 "
                    "Dockerfile 都会让之前的条目失效，所以索引及其"
                    "各层是缓存中只增不减的部分。--build-cache 正是"
                    "在不丢弃已下载基础镜像的前提下回收它们的方式"
                    "（普通 clear-cache 会连它们一起清掉）。它会先"
                    "删除索引，再在索引不再计入引用的情况下清扫层"
                    "缓存，于是你仍保留的镜像的各层得以保留，构建"
                    "自身的中间产物则被清除。下一次构建会重新运行"
                    "每一步。若只想对单次构建跳过缓存查找而不丢弃"
                    f"任何东西，请改用 '{PROGRAM_NAME} build "
                    f"--no-cache'。"
                ),
            },
        ],
    },

    "copy": {
        "usage": "copy [OPTIONS] [DIST:]SRC [DIST:]DEST",
        "aliases": ("cp",),
        "summary": (
            "在宿主文件系统与 proot 容器之间复制文件。源和目标都可以是"
            "本地路径或 'container:path' 引用。"
        ),
        "options": [
            ("-h, --help", "显示本帮助。"),
            ("-m, --move",
             "成功复制后删除源文件。"),
            ("-r, --recursive", "递归模式，用于复制目录。"),
            ("-v, --verbose", "每复制一个文件就打印其路径。"),
            ("-q, --quiet",
             "抑制非错误输出。与 --verbose 互斥。"),
        ],
        "examples": [
            f"{PROGRAM_NAME} copy ./file.txt ubuntu:/root/file.txt",
        ],
        "footer": [
            {
                "title": "注意事项",
                "intro": (
                    "目录 '.' 或 '..' 仅可作为源，不可作为目标。"
                    "不支持 glob 通配模式。"
                ),
            },
        ],
    },

    "install": {
        "usage": "install [OPTIONS] (IMAGE:TAG or URL or FILE)",
        "aliases": ("add", "i", "in", "ins"),
        "summary": (
            "从给定来源创建 proot 容器：Docker 镜像、OCI 镜像归档、"
            "rootfs tarball 或提供上述归档的 web URL。"
            "\n\n"
            "从 Docker 镜像安装需指定引用，例如 'ubuntu:24.04'。"
            "官方镜像可仅用名称（'ubuntu'），用户镜像需用 'user/image' "
            "形式。未指定 tag（版本）时使用 'latest'。"
            "\n\n"
            "默认从 Docker Hub 拉取镜像。自定义仓库需作为镜像引用的一部分"
            "指定，例如 'ghcr.io/foo/bar:tag'。"
            "\n\n"
            "层会缓存在本地，后续安装同一镜像时复用。"
            "\n\n"
            "容器名由 Docker 镜像名或 rootfs 归档文件名推导。要安装同一"
            "发行版的多个实例，需用命令行选项覆盖名称。"
            "\n\n"
            "可以安装与宿主 CPU 架构不同的发行版，此时需要 QEMU 用户模式"
            "模拟器才能运行。"
            "\n\n"
            "私有镜像需要鉴权。在运行 install 之前设置环境变量 "
            "PD_DOCKER_AUTH=\"user:password\"。某些仓库用 personal "
            "access token 代替密码。"
        ),
        "options": [
            ("-h, --help", "显示本帮助。"),
            ("-n, --name [NAME]",
             "为容器设置自定义名称。必须以字母或数字开头，其后只能包含"
             "拉丁字母、数字及 .、-、_ 三种符号。"
             "默认值为镜像名去掉 tag 和仓库前缀。"),
            ("-a, --architecture [ARCH]",
             "覆盖目标 CPU 架构。接受原生命名（aarch64, arm, i686, "
             "riscv64, x86_64）或 Docker platform 字符串（linux/arm64, "
             "linux/amd64, linux/arm/v7, linux/386, linux/riscv64）。"),
            ("--allow-insecure",
             "允许不安全传输：走明文 HTTP 的自定义仓库，或 TLS 证书不"
             "受信任、过期、自签、hostname 不匹配的 HTTPS 端点（仓库或"
             "下载 URL）。仅在你信任的网络路径上对可信来源使用。"),
            ("-q, --quiet", "抑制非错误输出。"),
        ],
        "examples": [
            f"{PROGRAM_NAME} install ubuntu:24.04",
            f"{PROGRAM_NAME} install -a x86_64 debian",
            f"{PROGRAM_NAME} install -n dist https://example.com/rootfs.tar",
            f"{PROGRAM_NAME} install -n dist ~/rootfs.tgz"
        ],
        "footer": [
            {
                "title": "去哪里找镜像",
                "intro": (
                    f"{CANONICAL_PROGRAM_NAME} 本身不提供镜像。用户需自行"
                    "寻找所需发行版镜像，但不保证特定发行版一定以 Docker "
                    "镜像、rootfs 归档或对应宿主 CPU 架构的形式存在。"
                    "\n\n"
                    "常见的可查找发行版的网络资源:"
                ),
                "bullets": [
                    ("Docker Hub: https://hub.docker.com/", None),
                    ("AWS Gallery: https://gallery.ecr.aws/", None),
                    ("GitHub GHCR: https://github.com/search （记得勾选 Packages 过滤）", None),
                    ("发行版官方网站", None),
                ]
            },
            {
                "title": "推荐镜像",
                "intro": (
                    "此列表仅供参考，并非全部。Docker Hub 和其他容器镜像"
                    "仓库提供了数百种裸发行版及打包应用的构建。"
                ),
                "bullets": [
                    ("alpine:latest", None),
                    ("archlinux/archlinux:latest", None),
                    ("danhunsaker/archlinuxarm:latest", None),
                    ("chimeralinux/chimera:latest", None),
                    ("debian:stable", None),
                    ("fedora:latest", None),
                    ("gentoo/stage3:latest", None),
                    ("manjarolinux/base:latest", None),
                    ("nixos/nix:latest", None),
                    ("opensuse/leap:latest", None),
                    ("opensuse/tumbleweed:latest", None),
                    ("rockylinux/rockylinux:latest", None),
                    ("aclemons/slackware:current", None),
                    ("termux/termux-docker:latest", None),
                    ("arfshl/trisquel:latest", None),
                    ("ubuntu:24.04", None),
                    ("ghcr.io/void-linux/void-musl:latest", None),
                ]
            },
        ],
    },

    "search": {
        "usage": "search [OPTIONS] QUERY",
        "aliases": ("se", "s"),
        "summary": (
            "在 Docker Hub 上搜索与 QUERY 匹配的镜像，等效于 "
            "'docker search'。"
            "\n\n"
            "结果以表格形式列出仓库名、描述、星标数、拉取次数，"
            "以及是否为官方镜像。列出的任何名称都可直接用于 "
            f"'{PROGRAM_NAME} install'。"
            "\n\n"
            "仅支持搜索 Docker Hub。搜索不属于 OCI 分发协议的一部分，"
            "因此自定义仓库（ghcr.io、gallery.ecr.aws、自建仓库）"
            "无法用此方式查询——请使用它们各自的网页界面。"
        ),
        "options": [
            ("-h, --help", "显示本帮助。"),
            ("-l, --limit [N]",
             "最多显示的结果数。默认 25，最大 1000。Docker Hub 每次"
             "请求返回 100 条结果，更大的 limit 会逐页获取。"),
            ("-q, --quiet",
             "仅打印仓库名，每行一个，不输出表格。适合用管道"
             "传给其他命令。"),
        ],
        "examples": [
            f"{PROGRAM_NAME} search ubuntu",
            f"{PROGRAM_NAME} search --limit 50 'linux distro'",
            f"{PROGRAM_NAME} search -q alpine | head -1 | "
                f"xargs {PROGRAM_NAME} install",
        ],
        "footer": [
            {
                "title": "说明",
                "intro": (
                    "仅搜索公开仓库，且不会发送 PD_DOCKER_AUTH："
                    "Hub 的搜索端点忽略凭据，因此即使配置了认证，"
                    "私有镜像也不会出现在结果中。但仍可直接按引用"
                    "安装它们。"
                    "\n\n"
                    "搜索结果不会说明镜像支持哪些 CPU 架构，"
                    "也无法说明它是否包含可用的 root 文件系统。"
                    "两者只有在拉取镜像后才能确定。"
                    "\n\n"
                    "星标数和拉取次数是 Docker Hub 自身的人气统计，"
                    "按原样显示。"
                ),
            },
        ],
    },

    "list": {
        "usage": "list [OPTIONS]",
        "aliases": ("li", "ls"),
        "summary": "列出所有已安装的 proot 容器。",
        "options": [
            ("-h, --help", "显示本帮助。"),
            ("-q, --quiet", "仅打印容器名，每行一个。"),
        ],
    },

    "login": {
        "usage": "login [OPTIONS] CONTAINER [-- COMMAND]",
        "aliases": ("sh",),
        "summary": (
            "启动交互式 shell，以 /etc/passwd 中配置的指定账户登录。"
            "也可在命令行分隔符 '--' 之后指定自定义命令替代默认 shell。"
            + (
                "\n\n"
                "默认情况下容器不与宿主文件系统隔离。强烈建议启用隔离"
                "模式后再运行破坏性命令。"
                if IS_TERMUX else ""
            )
        ),
        "options": [
            ("-h, --help", "显示本帮助。"),
            ("-u, --user [USER]",
             "切换到的用户身份（替代 root）。接受的形式: "
             "'name'（/etc/passwd 中的用户名）、"
             "'name:group'（用户名和组名）、"
             "'uid'（数字 UID）、"
             "'uid:gid'（数字 UID 和 GID）。"),
            ("-P, --redirect-ports",
             "把特权端口绑定替换为更高端口号（22 -> 2022, 80 -> 2080 等）。"
             f"端口偏移量硬编码在 proot 可执行文件中，无法通过 {PROGRAM_NAME} 配置。"),
            *([("--isolated",
                "启用隔离模式。除非使用 QEMU 用户模式模拟或用户手动指定要"
                " bind 的目录，否则不创建任何宿主文件系统绑定。")] if IS_TERMUX else []),
            *([("--minimal",
                "启用最精简配置的隔离模式。仅 bind /dev、/proc 和 /sys。"
                "除 link2symlink 外所有 proot 扩展均禁用。无 /proc 系统"
                "数据 workaround，无内核版本覆盖。特定功能只能通过命令行"
                "选项启用。可能比其他模式性能更好。")] if IS_TERMUX else []),
            ("--shared-home",
             "把宿主 home 目录 bind 进容器。"
             + (" 优先于隔离模式。默认模式中已包含。"
                if IS_TERMUX else "")),
            ("--shared-tmp",
             "把宿主 tmp 目录 bind 到 /tmp。"
             + (" 优先于隔离模式。默认模式中已包含。"
                if IS_TERMUX else "")),
            ("--shared-x11",
             "把宿主 X11 socket 目录 bind 到 /tmp/.X11-unix。"
             + (" 优先于隔离模式。会被 --shared-tmp 继承。默认模式中已包含。"
                if IS_TERMUX else "")),
            ("-b, --bind [SRC:DEST]",
             "自定义文件系统绑定。可多次指定。"
             + (" 优先于隔离模式。" if IS_TERMUX else "")),
            *([("--no-link2symlink",
                "禁用 proot 的硬链接模拟。仅推荐 SELinux 处于 permissive "
                "模式的设备使用。")] if IS_TERMUX else []),
            *([("--no-sysvipc",
                "禁用 proot 的 System V IPC 模拟。仅推荐内核已启用该特性"
                "且 SELinux 设为 permissive 模式的设备使用。")] if IS_TERMUX else []),
            *([("--no-kill-on-exit",
                "无限挂起直到所有会话进程退出。")] if IS_TERMUX else []),
            ("--emulator [FILE]",
             "覆盖用于跨架构执行的 QEMU 模拟器二进制。仅支持 QEMU 用户"
             "模式和 Blink 模拟器。FILE 必须可执行。"),
            ("--kernel [TEXT]",
             "自定义 uname 报告的内核版本字符串。"),
            ("--hostname [TEXT]", "自定义系统 hostname。"),
            ("-w, --work-dir [PATH]", "设置初始工作目录。"),
            ("-e, --env VAR=VALUE",
             "设置环境变量。可多次指定。"),
            ("-d, --detach",
             "在后台启动会话并立即返回。标准输入输出重定向到 /dev/null。"
             f"会话由 '{PROGRAM_NAME} ps' 列出，可用 'kill PID' 停止。"),
            ("--get-proot-cmd",
             "打印完整组装好的 proot 命令行后退出，不实际运行。"
             "输出可直接复制粘贴到终端。"),
        ],
        "footer": [
            *([{
                "title": "宿主绑定",
                "intro": (
                    "未指定 --isolated 时，以下宿主路径会被 bind 进容器:"
                ),
                "bullets": [
                    ("/apex", None),
                    ("/data/dalvik-cache", None),
                    (f"/data/data/{TERMUX_APP_PACKAGE}", None),
                    ("/linkerconfig/ld.config.txt", None),
                    ("/linkerconfig/com.android.art/ld.config.txt", None),
                    ("/mnt/sdcard", None),
                    ("/odm", None),
                    ("/product", None),
                    ("/sdcard", None),
                    ("/storage/emulated/0", None),
                    ("/storage/self/primary", None),
                    ("/system", None),
                    ("/system_ext", None),
                    ("/vendor", None),
                ],
            }] if IS_TERMUX else []),
            {
                "title": "注意事项",
                "intro": (
                    (
                        "如果 termux-api 等宿主工具不工作，请确保 PATH 包含"
                        " Termux bin 目录，以及必要的特殊环境变量如 "
                        "ANDROID_ART_ROOT, ANDROID_DATA, ANDROID_I18N_ROOT, "
                        "ANDROID_ROOT, ANDROID_TZDATA_ROOT, BOOTCLASSPATH, "
                        "EXTERNAL_STORAGE。合法值可在 Termux shell 中获取。"
                        "\n\n"
                        "若 Termux app 未取得必要权限，/sdcard 等宿主存储"
                        "绑定可能被禁用。"
                        "\n\n"
                        if IS_TERMUX else ""
                    ) +
                    f"{CANONICAL_PROGRAM_NAME} 不对任何用户选用的发行版镜像"
                    "能否正常工作做任何保证。观察到的 bug 可能源于 proot"
                    "（第三方依赖）的设计缺陷，或与给定容器运行时的根本性"
                    "不兼容。例如: 无法提供 udev 所需的 /dev、/proc、/sys "
                    "下受限接口访问；无法提供 bwrap 所需的 cgroups 或 "
                    "Linux namespaces。"
                    "\n\n"
                    "ARMv9 CPU 设备需要 QEMU 用户模式模拟器才能执行 32 位"
                    "程序，因为该架构不再包含必要的指令集。"
                ),
            },
        ],
    },

    "mirror": {
        "usage": "mirror [OPTIONS] {ls|test|use|unset|show} [ID|URL]",
        "aliases": ("mir", "mr"),
        "summary": (
            "管理内置 Docker registry 镜像源。原版 proot-distro 把单一"
            " registry URL 硬编码在 transport.py，无法切换。本子命令把"
            "「换源」做成一等公民: 可见、可测、可切。"
            "\n\n"
            "内置 17 个镜像源（DaoCloud、1Panel、毫秒镜像、轩辕镜像 ×2、"
            "腾讯云、Docker Hub 官方、DockerProxy ×2、Registry Cyou、"
            "Jiaxin、HubFast、Unsee、Cnxiaobai、gh-proxy ×3），通过 web 搜索"
            "校准到 2026-07-08。"
            "\n\n"
            "用户可在 ~/.config/proot-distro/mirrors.json 添加自定义镜像源，"
            "同 id 的条目会覆盖内置源。"
            "\n\n"
            "优先级: PD_REGISTRY_URL 环境变量 > 用户配置文件 > 内置默认"
            "（DaoCloud）。"
        ),
        "options": [
            ("-h, --help", "显示本帮助。"),
            ("-t, --timeout [SECONDS]",
             "test 时的探测超时（默认 6 秒）。"),
            ("--allow-insecure",
             "允许 HTTPS 跳过证书校验。仅用于自签证书的私有镜像源。"),
            ("--use-best",
             "test 完成后自动切换到最快的可达镜像源，不提示。"
             "脚本友好。"),
            ("--no-use",
             "test 完成后不提示也不切换。"),
            ("--json",
             "test 以 JSON 输出（脚本友好；隐含 --no-use）。"),
            ("--reachable-only",
             "ls: 仅显示可达的镜像源（会先探测，可能较慢）。"),
            ("-q, --quiet",
             "安静模式。ls 时仅输出 URL 列表，每行一个。"),
        ],
        "examples": [
            f"{PROGRAM_NAME} mirror ls",
            f"{PROGRAM_NAME} mirror ls --reachable-only",
            f"{PROGRAM_NAME} mirror test",
            f"{PROGRAM_NAME} mirror test --use-best",
            f"{PROGRAM_NAME} mirror test --json",
            f"{PROGRAM_NAME} mirror use 1panel-live",
            f"{PROGRAM_NAME} mirror use https://my-private-mirror.com",
            f"{PROGRAM_NAME} mirror unset",
            f"{PROGRAM_NAME} mirror show",
        ],
        "footer": [
            {
                "title": "子动作",
                "intro": (
                    "ls / list: 列出所有镜像源（内置 + 用户自定义）+ 当前生效源。\n"
                    "test [ID|URL]: 探测可达性。不带参数并发测全部，"
                    "测完提示 Y/n 切换到最快源。\n"
                    "use / set ID|URL: 选中一个镜像源（按 id、name 或 URL）。\n"
                    "unset / clear / off: 清除当前选中，回到默认。\n"
                    "show: 仅打印当前生效 URL（脚本友好，例如 "
                    "$(pd mirror show)）。"
                ),
            },
            {
                "title": "PD_REGISTRY_URL 环境变量",
                "intro": (
                    "PD_REGISTRY_URL 优先级最高，覆盖一切配置。设了此变量时:"
                    "mirror ls 会标注「⚠ 环境变量覆盖」；mirror use/unset "
                    "会警告配置文件不生效；mirror show 优先输出 env URL。"
                    "要恢复配置文件生效，请执行 unset PD_REGISTRY_URL。"
                ),
            },
            {
                "title": "探测协议",
                "intro": (
                    "遵循 Docker Registry HTTP API V2: 发起 GET /v2/，"
                    "2xx/4xx 都视为可达（401/403 表示需要鉴权但服务在），"
                    "仅网络错误或超时视为不可达。并发探测使用 "
                    "ThreadPoolExecutor(max_workers=8)。"
                ),
            },
            {
                "title": "配置文件",
                "intro": (
                    "桌面 Linux: ~/.config/proot-distro/mirror.json "
                    "（或 $XDG_CONFIG_HOME/proot-distro/mirror.json）。\n"
                    "Termux/Android: $PREFIX/etc/proot-distro/mirror.json。\n"
                    "写入采用 atomic-replace 模式（tmp + os.replace），"
                    "保证中断不会留下半写配置。"
                ),
            },
        ],
    },

    "lang": {
        "usage": "lang {get|use|list|reset} [LOCALE]",
        "summary": (
            "查看与设置界面语言（本 fork 特有；原版无此命令，界面硬编码"
            "英文）。\n\n"
            "Termux 的 locale 支持是残缺的：LANG 通常为 C 或未设置，"
            "Android 也不会把用户真实语言上报给终端应用，因此任何基于"
            "系统 locale 的自动语言检测都行不通。lang use 提供一个"
            "显式、持久化的开关，一次性解决这个痛点。"
            "\n\n"
            "翻译以纯 JSON 文件（locales/<locale>.json）内置分发，无需"
            "编译步骤。"
            "\n\n"
            "语言来源优先级: --lang 标志 > PROOT_DISTRO_LANG 环境变量 > "
            "PD_LANG 环境变量 > 配置文件 > 内置默认（zh_CN）。"
        ),
        "options": [
            ("-h, --help", "显示本帮助。"),
        ],
        "examples": [
            f"{PROGRAM_NAME} lang get",
            f"{PROGRAM_NAME} lang use zh_CN",
            f"{PROGRAM_NAME} lang use en",
            f"{PROGRAM_NAME} lang list",
            f"{PROGRAM_NAME} lang reset",
            f"{PROGRAM_NAME} --lang en {PROGRAM_NAME}",
        ],
        "footer": [
            {
                "title": "子动作",
                "intro": (
                    "get / show / status: 显示当前生效语言及来源"
                    "（--lang / 环境变量 / 配置文件 / 默认）。\n"
                    "use / set / switch <LOCALE>: 持久化语言偏好到"
                    "配置文件并立即生效。zh、zh-CN、zh_CN.UTF-8 等"
                    "写法自动规范化为 zh_CN；C、POSIX、en、en_US 等"
                    "自动规范化为 en。\n"
                    "list / ls: 列出所有已内置翻译的语言。\n"
                    "reset / unset / clear: 清除持久化偏好，回退默认"
                    "逻辑（环境变量仍会优先生效，会给出提醒）。"
                ),
            },
            {
                "title": "全局 --lang 标志",
                "intro": (
                    "单次命令的临时覆盖，优先级最高，顶层与子命令位置"
                    "均可写: pd --lang en install debian 或 "
                    "pd install --lang en debian。不写入配置文件，"
                    "仅影响本次运行。"
                ),
            },
            {
                "title": "环境变量",
                "intro": (
                    "PROOT_DISTRO_LANG / PD_LANG: 会话级或脚本级覆盖，"
                    "优先于配置文件。设了它们时 lang use / lang reset "
                    "会提醒配置不会立即生效。要恢复配置生效，请 unset。"
                ),
            },
            {
                "title": "配置文件",
                "intro": (
                    "桌面 Linux: ~/.config/proot-distro/config.json "
                    "（或 $XDG_CONFIG_HOME/proot-distro/config.json）"
                    "的 language 键。\n"
                    "Termux/Android: $PREFIX/etc/proot-distro/config.json。\n"
                    "写入采用 tmp + os.replace 原子替换，不会留下半写"
                    "配置。"
                ),
            },
        ],
    },

    "ps": {
        "usage": "ps [OPTIONS]",
        "summary": (
            "列出活跃的容器会话。每个正在运行的 'login' 和 'run' 会话"
            "都会显示其 PID、容器、会话类型、登录用户、运行时长及正在"
            "执行的命令。"
        ),
        "options": [
            ("-h, --help", "显示本帮助。"),
            ("-q, --quiet",
             "仅打印每个活跃会话的 PID，每行一个。"),
        ]
    },

    "kill": {
        "usage": "kill [OPTIONS] (PID | CONTAINER | --all)",
        "summary": (
            f"通过向进程树发送信号来终止 {CANONICAL_PROGRAM_NAME} 会话。"
            "影响范围由目标类型决定。用 PID 终止单个容器会话，或用容器名"
            "停止该容器内创建的所有进程。"
            "\n\n"
            "默认信号为 TERM。"
        ),
        "options": [
            ("-h, --help", "显示本帮助。"),
            ("-s, --signal [SIGNAL]",
             "替代默认 SIGTERM 的信号。接受名称（SIGTERM, KILL, HUP）"
             "或数字（15, 9, 1）。"),
            ("--all", "目标为系统范围内所有会话。"),
        ],
        "examples": [
            f"{PROGRAM_NAME} kill 12345",
            f"{PROGRAM_NAME} kill --signal KILL nextcloud",
        ],
        "footer": [
            {
                "title": "NOTES",
                "intro": (
                    "PRoot itself ignores every signal but QUIT, KILL "
                    "and the job control ones, so TERM, INT or HUP "
                    "reach the guest processes only. When they do not "
                    "end the session, the container root is sent QUIT "
                    "(which makes proot kill every traced process) and "
                    "anything still left is sent KILL. Job control "
                    "signals (STOP, CONT, TSTP) and USR1/USR2 are "
                    "delivered as asked and never escalated."
                ),
            },
        ],
    },

    "remove": {
        "usage": "remove [OPTIONS] CONTAINER|IMAGE",
        "aliases": ("rm",),
        "summary": (
            "永久删除指定的 proot 容器。不会请求确认，请谨慎操作。"
        ),
        "options": [
            ("-h, --help", "显示本帮助。"),
            ("-v, --verbose", "每删除一个文件就打印其路径。"),
            ("-q, --quiet",
             "抑制非错误输出。与 --verbose 互斥。"),
        ],
        "examples": [
            f"{PROGRAM_NAME} remove ubuntu",
            f"{PROGRAM_NAME} remove --image ubuntu:24.04",
            f"{PROGRAM_NAME} rm -i -a aarch64 myapp:latest",
        ],
    },

    "rename": {
        "usage": "rename OLDNAME NEWNAME",
        "summary": "重命名已安装的 proot 容器。",
        "options": [
            ("-h, --help", "显示本帮助。"),
            ("-q, --quiet", "抑制非错误输出。"),
        ],
        "footer": [
            {
                "title": "注意事项",
                "intro": (
                    "重命名会更新容器内所有 proot link2symlink 条目，"
                    "对大型 rootfs 树可能耗时较久。出于数据完整性原因，"
                    "用户不得用 CTRL-C 中断进程。"
                ),
            },
        ],
    },

    "reset": {
        "usage": "reset CONTAINER",
        "summary": (
            "使用已存储的 Docker 镜像清单从零重建指定容器。容器内的所有"
            "当前数据都将丢失。"
            "\n\n"
            "仅适用于从 Docker 镜像创建的容器。"
        ),
        "options": [
            ("-h, --help", "显示本帮助。"),
            ("-q, --quiet", "抑制非错误输出。"),
        ],
    },

    "restore": {
        "usage": "restore [OPTIONS] [BACKUP_FILE]",
        "summary": (
            "从备份归档恢复容器。未指定备份文件时，从 stdin 读取归档数据。"
        ),
        "options": [
            ("-h, --help", "显示本帮助。"),
            ("-v, --verbose", "每解压一个文件就打印其路径。"),
            ("-q, --quiet",
             "抑制非错误输出。与 --verbose 互斥。"),
        ],
        "footer": [
            {
                "title": "注意事项",
                "intro": (
                    "压缩方式由文件头自动检测。支持: gzip, bzip2, xz, "
                    "未压缩 tar。对文件和 stdin 输入均适用。"
                    "\n\n"
                    "每个归档只恢复一个容器。包含多个容器或不包含任何容器"
                    " rootfs 的归档会被拒绝。"
                ),
            },
        ],
    },

    "run": {
        "usage": "run [OPTIONS] CONTAINER [-- ARG ...]",
        "summary": (
            "运行容器 Docker 镜像清单中定义的 Entrypoint 和/或 Cmd。"
            "'--' 之后的参数会追加到 Entrypoint 之后（替换镜像定义的 Cmd）。"
            "若 Entrypoint 和 Cmd 均未定义且未提供参数，则报错。"
            "\n\n"
            "主要面向服务器镜像使用。"
        ),
        "options": [
            ("-h, --help", "显示本帮助。"),
            ("-u, --user [USER]",
             "切换到的用户身份（替代 root）。接受的形式: "
             "'name'（/etc/passwd 中的用户名）、"
             "'name:group'（用户名和组名）、"
             "'uid'（数字 UID）、"
             "'uid:gid'（数字 UID 和 GID）。"),
            ("-P, --redirect-ports",
             "把特权端口绑定替换为更高端口号（22 -> 2022, 80 -> 2080 等）。"
             f"端口偏移量硬编码在 proot 可执行文件中，无法通过 {PROGRAM_NAME} 配置。"),
            *([("--isolated",
                "启用隔离模式。除非使用 QEMU 用户模式模拟或用户手动指定要"
                " bind 的目录，否则不创建任何宿主文件系统绑定。")] if IS_TERMUX else []),
            *([("--minimal",
                "启用最精简配置的隔离模式。仅 bind /dev、/proc 和 /sys。"
                "除 link2symlink 外所有 proot 扩展均禁用。无 /proc 系统"
                "数据 workaround，无内核版本覆盖。特定功能只能通过命令行"
                "选项启用。可能比其他模式性能更好。")] if IS_TERMUX else []),
            ("--shared-home",
             "把宿主 home 目录 bind 进容器。"
             + (" 优先于隔离模式。默认模式中已包含。"
                if IS_TERMUX else "")),
            ("--shared-tmp",
             "把宿主 tmp 目录 bind 到 /tmp。"
             + (" 优先于隔离模式。默认模式中已包含。"
                if IS_TERMUX else "")),
            ("--shared-x11",
             "把宿主 X11 socket 目录 bind 到 /tmp/.X11-unix。"
             + (" 优先于隔离模式。会被 --shared-tmp 继承。默认模式中已包含。"
                if IS_TERMUX else "")),
            ("-b, --bind [SRC:DEST]",
             "自定义文件系统绑定。可多次指定。"
             + (" 优先于隔离模式。" if IS_TERMUX else "")),
            *([("--no-link2symlink",
                "禁用 proot 的硬链接模拟。仅推荐 SELinux 处于 permissive "
                "模式的设备使用。")] if IS_TERMUX else []),
            *([("--no-sysvipc",
                "禁用 proot 的 System V IPC 模拟。仅推荐内核已启用该特性"
                "且 SELinux 设为 permissive 模式的设备使用。")] if IS_TERMUX else []),
            *([("--no-kill-on-exit",
                "无限挂起直到所有会话进程退出。")] if IS_TERMUX else []),
            ("--emulator [FILE]",
             "覆盖用于跨架构执行的 QEMU 模拟器二进制。仅支持 QEMU 用户"
             "模式和 Blink 模拟器。FILE 必须可执行。"),
            ("--kernel [TEXT]",
             "自定义 uname 报告的内核版本字符串。"),
            ("--hostname [TEXT]", "自定义系统 hostname。"),
            ("-w, --work-dir [PATH]", "设置初始工作目录。"),
            ("-e, --env VAR=VALUE",
             "设置环境变量。可多次指定。"),
            ("-d, --detach",
             "在后台启动会话并立即返回。标准输入输出重定向到 /dev/null。"
             f"会话由 '{PROGRAM_NAME} ps' 列出，可用 'kill PID' 停止。"),
            ("--get-proot-cmd",
             "打印完整组装好的 proot 命令行后退出，不实际运行。"
             "输出可直接复制粘贴到终端。"),
        ],
        "examples": [
            f"{PROGRAM_NAME} run nextcloud --redirect-ports",
            f"{PROGRAM_NAME} run nextcloud --detach",
            f"{PROGRAM_NAME} run ubuntu --isolated -- /bin/echo hi",
        ],
        "footer": [
            {
                "title": "注意事项",
                "intro": (
                    f"{CANONICAL_PROGRAM_NAME} 不对任何用户选用的发行版镜像"
                    "能否正常工作做任何保证。观察到的 bug 可能源于 proot"
                    "（第三方依赖）的设计缺陷，或与给定容器运行时的根本性"
                    "不兼容。例如: 无法提供 udev 所需的 /dev、/proc、/sys "
                    "下受限接口访问；无法提供 bwrap 所需的 cgroups 或 "
                    "Linux namespaces。"
                    "\n\n"
                    "ARMv9 CPU 设备需要 QEMU 用户模式模拟器才能执行 32 位"
                    "程序，因为该架构不再包含必要的指令集。"
                ),
            },
        ],
    },

    "sync": {
        "usage": "sync [OPTIONS] [DIST:]SRC [DIST:]DEST",
        "summary": (
            "通过仅复制已修改文件、删除源中已不存在的文件，高效地同步"
            "宿主与容器之间的目录。默认按大小和修改时间戳比较文件，"
            "也可使用更严格的校验和比对。"
            "\n\n"
            "源和目标都可以是本地路径或 'container:path' 引用。"
        ),
        "options": [
            ("-h, --help", "显示本帮助。"),
            ("-c, --checksum",
             "按大小 + CRC32 校验和比较文件（替代大小 + 修改时间）。"
             "更慢但精度更高。"),
            ("-d, --delete",
             "同步完成后，删除目标中在源里没有对应项的文件和目录。"
             "仅当源为目录时生效。"),
            ("-v, --verbose", "每同步或删除一项就打印其路径。"),
            ("-q, --quiet",
             "抑制非错误输出。与 --verbose 互斥。"),
        ],
        "examples": [
            f"{PROGRAM_NAME} sync ./dotfiles/ ubuntu:/root/",
            f"{PROGRAM_NAME} sync --delete ./app/ ubuntu:/opt/app/"
        ],
        "footer": [
            {
                "title": "NOTES",
                "intro": (
                    "Hard links become independent copies, so a tree "
                    "that shares content between names grows on the "
                    "way over. Special files (devices, FIFOs, sockets) "
                    "are skipped, and ownership is never changed. "
                    "An entry that cannot be read or written is "
                    "reported and stepped over; the exit status is "
                    "non-zero when any were. '--checksum' compares a "
                    "CRC32, which detects changed content but is not "
                    "an integrity check."
                ),
            },
        ],
    },
}


# Top-level command table for the no-args help screen.
# 元组格式: (name, description) 或 (name, description, warning)
TOP_COMMANDS = [
    ("help", "显示本帮助。"),
    ("install", "从 OCI 镜像或 rootfs 归档安装发行版。"),
    ("list", "列出已创建的容器。"),
    ("login", "在容器内启动交互式 shell。"),
    ("run", "运行容器的 entrypoint（服务器或 distroless 镜像）。"),
    ("ps", "列出活跃的容器会话。"),
    ("kill", "停止活跃的容器会话。"),
    ("remove", "删除容器。", "数据将被销毁!"),
    ("rename", "重命名容器。"),
    ("reset", "从零重新安装容器。", "数据将被销毁!"),
    ("backup", "把容器保存为 TAR 归档。"),
    ("restore", "从 TAR 归档恢复容器。", "数据将被销毁!"),
    ("clear-cache", "删除缓存的下载内容。"),
    ("copy", "在容器与宿主之间复制文件。"),
    ("sync", "在容器与宿主之间同步文件。"),
    ("build", "从 Dockerfile 构建 OCI 镜像。"),
    ("push", "把本地构建的镜像推送到仓库。"),
    ("search", "在 Docker Hub 上搜索镜像。"),
    ("mirror", "管理 Docker registry 镜像源（ls/test/use/unset/show）。"),
    ("lang", "查看与设置界面语言（get/use/list/reset）。"),
]
