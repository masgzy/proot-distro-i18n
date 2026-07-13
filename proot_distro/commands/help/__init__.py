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

# Architecture: Top-level help command. The per-command pages live in
# pages.py (Chinese) and pages_en.py (English) as plain data; this
# module picks the right one based on the active language and renders
# the no-args overview page. It also exposes HELP_COMMANDS so cli.py
# can dispatch a one-page render for a single command.
#
# Performance note: pages.py / pages_en.py are imported lazily (only
# when help is actually rendered), because they carry ~850 lines of
# string literals each and add ~75ms to import time. The HELP_COMMANDS
# table is built from a static name list so cli.py can dispatch
# without triggering the pages import.

from proot_distro.constants import PROGRAM_NAME, RUNTIME_DIR
from proot_distro.message import C, msg
from proot_distro.commands.help.render import (
    commands_block,
    footer,
    paragraph,
    render_page,
    section,
    shell_block,
    term_width,
    usage_line,
)
from proot_distro.i18n import _, get_language


# Static list of all command names that have a help page. Used to build
# HELP_COMMANDS without importing pages.py / pages_en.py at module load.
_HELP_PAGE_NAMES = (
    "build", "push", "backup", "clear-cache", "copy", "install",
    "list", "login", "mirror", "ps", "kill", "remove", "rename",
    "reset", "restore", "run", "sync",
)


def _get_pages():
    """Return the help-pages dict for the current language.

    Lazily imports pages.py (zh_CN) or pages_en.py (en) on first call.
    zh_CN → pages.py (Chinese); everything else → pages_en.py (English).
    """
    lang = get_language()
    if lang == "zh_CN":
        from proot_distro.commands.help import pages as _p
        return _p.HELP_PAGES, _p.TOP_COMMANDS
    from proot_distro.commands.help import pages_en as _p
    return _p.HELP_PAGES, _p.TOP_COMMANDS


def _make_help_fn(name):
    def help_fn():
        pages, _top = _get_pages()
        if name in pages:
            render_page(pages[name])
        else:
            # Fallback: try the other language's pages
            from proot_distro.commands.help import pages as _zh
            from proot_distro.commands.help import pages_en as _en
            merged = {**_zh.HELP_PAGES, **_en.HELP_PAGES}
            if name in merged:
                render_page(merged[name])
            else:
                raise KeyError(f"no help page for command '{name}'")
    return help_fn


# Map every command name to a zero-arg renderer. Imported by the CLI
# dispatcher so per-command --help calls one entry from this table.
HELP_COMMANDS = {name: _make_help_fn(name) for name in _HELP_PAGE_NAMES}


def command_help(args=None) -> None:
    """Render the top-level help page (no command argument).

    Dispatches to the Chinese or English overview based on the active
    language. *args* is accepted for signature uniformity with the
    other command_X handlers but is intentionally ignored.
    """
    if get_language() == "zh_CN":
        _command_help_zh()
    else:
        _command_help_en()


def _command_help_zh(args=None) -> None:
    """中文 overview 页。"""
    width = term_width()
    _pages, top_commands = _get_pages()

    section(_("用法"))
    usage_line("[COMMAND] [ARGUMENTS]", width)

    section(_("描述"))
    paragraph(
        _("PRoot-Distro 是 proot 用户空间模拟器（chroot、mount --bind、"
          "binfmt_misc）的封装工具。它提供了一种便捷的方式来管理 Linux "
          "容器，并借助 Docker registry 支持提供任意发行版。"),
        width,
    )

    section(_("命令"))
    commands_block(top_commands, width)

    section(_("获取帮助"))
    paragraph(
        _("运行 '{cmd} <command> --help' 查看任意命令的详情。\n\n"
          "完整文档: https://github.com/termux/proot-distro/blob/master/README.md",
          cmd=PROGRAM_NAME),
        width,
    )

    section(_("快速开始"))
    paragraph(
        _("通用发行版镜像的使用非常直观。下面以 Ubuntu 24.04 为例:"),
        width,
    )
    msg()
    shell_block(
        [f"{PROGRAM_NAME} install ubuntu:24.04",
         f"{PROGRAM_NAME} login ubuntu"], width,
    )
    msg()
    paragraph(
        _("某些镜像针对特定用途预配置，内含启动脚本。常见于服务器软件:"),
        width,
    )
    msg()
    shell_block(
        [f"{PROGRAM_NAME} install nextcloud:32",
         f"{PROGRAM_NAME} run --redirect-ports nextcloud"], width,
    )
    msg()
    paragraph(
        _("Docker Hub 上非官方提供的镜像需指定组织或用户名:"),
        width,
    )
    msg()
    shell_block(
        [f"{PROGRAM_NAME} install termux/termux-docker"], width,
    )
    msg()
    paragraph(
        _("不再需要某个容器时，用以下命令删除:"),
        width,
    )
    msg()
    shell_block([f"{PROGRAM_NAME} remove ubuntu"], width)
    msg()
    paragraph(
        _("可以在 Docker Hub (https://hub.docker.com/) 或互联网其他地方"
          "发现现有镜像。也可以用 '{cmd} build' 从 Dockerfile 自建镜像。",
          cmd=PROGRAM_NAME),
        width,
    )

    section(_("数据位置"))
    msg(f"  {C['YELLOW']}{RUNTIME_DIR}{C['RST']}")

    section(_("故障排查"))
    paragraph(
        _("如果你的终端（主题）与颜色显示不兼容，设置以下环境变量:"),
        width,
    )
    msg()
    shell_block(["export PD_FORCE_NO_COLORS=true"], width)
    msg()
    paragraph(
        _("拉取私有 Docker/OCI 镜像时，在运行 install 之前以 "
          "'username:password' 格式通过 PD_DOCKER_AUTH 设置凭据:"),
        width,
    )
    msg()
    shell_block(
        ["export PD_DOCKER_AUTH=user:password",
         f"{PROGRAM_NAME} install ghcr.io/myorg/private-image:tag"],
        width,
    )
    msg()
    paragraph(
        _("镜像源拉取失败时，可用 '{cmd} mirror' 子命令切换源:\n\n"
          "{cmd} mirror test       # 并发探测所有内置镜像源\n"
          "{cmd} mirror use <ID>   # 切换到指定镜像源\n"
          "{cmd} mirror show       # 查看当前生效 URL",
          cmd=PROGRAM_NAME),
        width,
    )
    msg()
    paragraph(
        _("工具问题请反馈至 https://github.com/termux/proot-distro/issues"),
        width,
    )

    footer(width)


def _command_help_en(args=None) -> None:
    """English overview page (original proot-distro text)."""
    width = term_width()
    _pages, top_commands = _get_pages()

    section("USAGE")
    usage_line("[COMMAND] [ARGUMENTS]", width)

    section("DESCRIPTION")
    paragraph(
        "PRoot-Distro is a wrapper utility for proot user-space "
        "emulator of chroot, mount --bind and binfmt_misc. This "
        "utility provides a convenient way for working with Linux "
        "containers, leveraging support of Docker registries to "
        "provide distributions of any kind.",
        width,
    )

    section("COMMANDS")
    commands_block(top_commands, width)

    section("GETTING HELP")
    paragraph(
        f"Run '{PROGRAM_NAME} <command> --help' for details on any command.\n\n"
        f"Read documentation at https://github.com/termux/proot-distro/blob/master/README.md",
        width,
    )

    section("QUICK START")
    paragraph(
        "Usage of generic distribution images is straightforward. "
        "Below is an example for Ubuntu 24.04:",
        width,
    )
    msg()
    shell_block(
        [f"{PROGRAM_NAME} install ubuntu:24.04",
         f"{PROGRAM_NAME} login ubuntu"], width,
    )
    msg()
    paragraph(
        "Some images come preconfigured for specific purposes and "
        "contain built-in startup script. Often this is a case for "
        "server software:",
        width,
    )
    msg()
    shell_block(
        [f"{PROGRAM_NAME} install nextcloud:32",
         f"{PROGRAM_NAME} run --redirect-ports nextcloud"], width,
    )
    msg()
    paragraph(
        "Images that are not officially provided by Docker Hub "
        "require specifying organization or user name:",
        width,
    )
    msg()
    shell_block(
        [f"{PROGRAM_NAME} install termux/termux-docker"], width,
    )
    msg()
    paragraph(
        "If you no longer need a specific container, delete it with:",
        width,
    )
    msg()
    shell_block([f"{PROGRAM_NAME} remove ubuntu"], width)
    msg()
    paragraph(
        "You can discover existing images on Docker Hub "
        "(https://hub.docker.com/) or other places on the Internet. "
        "You can also build your own image from a Dockerfile with "
        f"'{PROGRAM_NAME} build'.",
        width,
    )

    section("DATA LOCATION")
    msg(f"  {C['YELLOW']}{RUNTIME_DIR}{C['RST']}")

    section("TROUBLESHOOTING")
    paragraph(
        "If your terminal (theme) does not work well with colors, "
        "set this environment variable:",
        width,
    )
    msg()
    shell_block(["export PD_FORCE_NO_COLORS=true"], width)
    msg()
    paragraph(
        "To pull private Docker/OCI images, set credentials via "
        "PD_DOCKER_AUTH in 'username:password' format before "
        "running the install command:",
        width,
    )
    msg()
    shell_block(
        ["export PD_DOCKER_AUTH=user:password",
         f"{PROGRAM_NAME} install ghcr.io/myorg/private-image:tag"],
        width,
    )
    msg()
    paragraph(
        f"If registry pulls fail, use the '{PROGRAM_NAME} mirror' subcommand to "
        "switch mirrors:\n\n"
        f"{PROGRAM_NAME} mirror test       # probe all built-in mirrors concurrently\n"
        f"{PROGRAM_NAME} mirror use <ID>   # switch to a specific mirror\n"
        f"{PROGRAM_NAME} mirror show       # print the currently effective URL",
        width,
    )
    msg()
    paragraph(
        "Report utility issues to "
        "https://github.com/termux/proot-distro/issues",
        width,
    )

    footer(width)


__all__ = ("command_help", "HELP_COMMANDS")
