# proot-distro 与 pd 的 Fish 补全
#
# 安装:
#   cp proot-distro.fish ~/.config/fish/completions/proot-distro.fish
#   cp proot-distro.fish ~/.config/fish/completions/pd.fish

# ---------------------------------------------------------------------------
# 辅助: 解析已安装容器目录
# ---------------------------------------------------------------------------
function __proot_distro_containers
    set -l dir
    if __proot_distro_is_termux
        set -l prefix
        if set -q TERMUX__PREFIX
            set prefix $TERMUX__PREFIX
        else
            set prefix /data/data/com.termux/files/usr
        end
        set dir "$prefix/var/lib/proot-distro/containers"
    else if set -q XDG_DATA_HOME
        set dir "$XDG_DATA_HOME/proot-distro/containers"
    else
        set dir "$HOME/.local/share/proot-distro/containers"
    end
    if test -d "$dir"
        for d in "$dir"/*/
            set -l name (basename "$d")
            if test -d "$dir/$name/rootfs"
                echo $name
            end
        end
    end
end

# ---------------------------------------------------------------------------
# Termux/Android 检测 — 与 constants.py 中的 _detect_termux() 保持一致。
# 三个独立指标中至少两个匹配时返回 0 (true)。
# ---------------------------------------------------------------------------
function __proot_distro_is_termux
    set -l score 0
    if test -f /system/build.prop; or test -d /data/app
        set score (math $score + 1)
    end
    if set -q TERMUX_APP__APP_VERSION_NAME; or set -q TERMUX_VERSION
        set score (math $score + 1)
    end
    set -l prefix
    if set -q TERMUX__PREFIX
        set prefix $TERMUX__PREFIX
    else
        set prefix /data/data/com.termux/files/usr
    end
    if test -r "$prefix" -a -x "$prefix"
        set score (math $score + 1)
    end
    test $score -ge 2
end

# ---------------------------------------------------------------------------
# 辅助: 尚未看到任何子命令时为 true
# ---------------------------------------------------------------------------
function __proot_distro_no_subcommand
    not __fish_seen_subcommand_from \
        install remove rename reset login list ps kill backup restore \
        clear-cache copy sync run build push mirror help
end

# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n __proot_distro_no_subcommand -a install     -d '从 Docker 镜像或本地归档安装容器'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a remove      -d '删除已安装的容器'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a rename      -d '重命名容器'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a reset       -d '从原始镜像重新安装容器'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a login       -d '在容器内打开 shell'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a list        -d '列出已安装的容器'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a ps          -d '列出活跃的容器会话'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a kill        -d '停止活跃的容器会话'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a backup      -d '把容器备份为 tar 归档'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a restore     -d '从 tar 归档恢复容器'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a clear-cache -d '清空下载缓存'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a copy        -d '在宿主与容器之间复制文件'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a sync        -d '在宿主与容器之间同步文件'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a run         -d '在容器中运行镜像的 entrypoint/cmd'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a build       -d '从 Dockerfile 构建 OCI 镜像'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a push        -d '把本地构建的镜像推送到仓库'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a help        -d '显示帮助'

# 全局帮助标志（子命令之前）
complete -c proot-distro -f -n __proot_distro_no_subcommand -s h -l help   -d '显示帮助'

# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from install' \
    -s n -l name       -r -d '以自定义容器名安装'
complete -c proot-distro -f -n '__fish_seen_subcommand_from install' \
    -s a -l architecture -r -d '目标 CPU 架构' \
    -a 'aarch64\tAArch64 arm\tARM(32-bit) i686\tx86(32-bit) riscv64\tRISC-V x86_64\tx86_64'
complete -c proot-distro -f -n '__fish_seen_subcommand_from install' \
    -l allow-insecure  -d '允许从仅 HTTP（不安全）的仓库拉取'
complete -c proot-distro -f -n '__fish_seen_subcommand_from install' \
    -s q -l quiet      -d '抑制非错误输出'
complete -c proot-distro -f -n '__fish_seen_subcommand_from install' \
    -s h -l help       -d '显示帮助'

# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from remove' \
    -a '(__proot_distro_containers)' -d '容器'
complete -c proot-distro -f -n '__fish_seen_subcommand_from remove' \
    -s v -l verbose    -d '打印每个被删除的文件'
complete -c proot-distro -f -n '__fish_seen_subcommand_from remove' \
    -s q -l quiet      -d '抑制非错误输出'
complete -c proot-distro -f -n '__fish_seen_subcommand_from remove' \
    -s h -l help       -d '显示帮助'

# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from rename' \
    -a '(__proot_distro_containers)' -d '容器'
complete -c proot-distro -f -n '__fish_seen_subcommand_from rename' \
    -s q -l quiet      -d '抑制非错误输出'
complete -c proot-distro -f -n '__fish_seen_subcommand_from rename' \
    -s h -l help       -d '显示帮助'

# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from reset' \
    -a '(__proot_distro_containers)' -d '容器'
complete -c proot-distro -f -n '__fish_seen_subcommand_from reset' \
    -s q -l quiet      -d '抑制非错误输出'
complete -c proot-distro -f -n '__fish_seen_subcommand_from reset' \
    -s h -l help       -d '显示帮助'

# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -a '(__proot_distro_containers)' -d '容器'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -s u -l user       -r -d '以该用户身份运行（默认: root）'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -s P -l redirect-ports -d '把 1024 以下端口重定向到非特权范围'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login; and __proot_distro_is_termux' \
    -l isolated           -d '隔离模式: 不带宿主环境变量或 Termux 路径'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login; and __proot_distro_is_termux' \
    -l minimal            -d '类似 --isolated，但还禁用 Android 系统绑定'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -l shared-home        -d '把 home 挂载进容器'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -l shared-tmp         -d '与宿主共享 /tmp'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -l shared-x11         -d '共享 X11 socket（/tmp/.X11-unix）'
complete -c proot-distro -n '__fish_seen_subcommand_from login' \
    -s b -l bind       -r -d '把 PATH[:DEST] bind 挂载进容器（可重复）'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login; and __proot_distro_is_termux' \
    -l no-link2symlink    -d '禁用 proot link2symlink 扩展'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login; and __proot_distro_is_termux' \
    -l no-sysvipc         -d '禁用 SysV IPC 模拟'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login; and __proot_distro_is_termux' \
    -l no-kill-on-exit    -d '会话结束时不清子进程'
complete -c proot-distro -n '__fish_seen_subcommand_from login' \
    -l emulator        -r -d 'QEMU 用户模式模拟器二进制路径'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -l kernel          -r -d 'uname 报告的伪内核版本字符串'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -l hostname        -r -d '容器内可见的 hostname'
complete -c proot-distro -n '__fish_seen_subcommand_from login' \
    -s w -l work-dir   -r -d '容器内初始工作目录'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -s e -l env        -r -d '设置环境变量 VAR=VALUE（可重复）'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -s d -l detach        -d '在后台启动会话'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -l get-proot-cmd      -d '打印 proot 命令行后退出'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -s h -l help          -d '显示帮助'

# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from list' \
    -s q -l quiet      -d '抑制非错误输出'
complete -c proot-distro -f -n '__fish_seen_subcommand_from list' \
    -s h -l help       -d '显示帮助'

# ---------------------------------------------------------------------------
# ps
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from ps' \
    -s q -l quiet      -d 'Print only PIDs, one per line'
complete -c proot-distro -f -n '__fish_seen_subcommand_from ps' \
    -s h -l help       -d '显示帮助'

# ---------------------------------------------------------------------------
# kill
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from kill' \
    -a '(__proot_distro_containers)' -d '容器'
complete -c proot-distro -f -n '__fish_seen_subcommand_from kill' \
    -s s -l signal     -r -d 'Signal to send instead of SIGTERM' \
    -a 'TERM KILL HUP INT QUIT USR1 USR2'
complete -c proot-distro -f -n '__fish_seen_subcommand_from kill' \
    -l all             -d 'Stop every active session'
complete -c proot-distro -f -n '__fish_seen_subcommand_from kill' \
    -s h -l help       -d '显示帮助'

# ---------------------------------------------------------------------------
# backup
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from backup' \
    -a '(__proot_distro_containers)' -d '容器'
complete -c proot-distro -n '__fish_seen_subcommand_from backup' \
    -s o -l output     -r -d 'Write archive to FILE instead of stdout'
complete -c proot-distro -f -n '__fish_seen_subcommand_from backup' \
    -s c -l compress   -r -d 'Compression algorithm' \
    -a 'gzip\tgzip bzip2\tbzip2 xz\txz none\tNo compression'
complete -c proot-distro -f -n '__fish_seen_subcommand_from backup' \
    -s v -l verbose    -d '打印每个归档的文件'
complete -c proot-distro -f -n '__fish_seen_subcommand_from backup' \
    -s q -l quiet      -d '抑制非错误输出'
complete -c proot-distro -f -n '__fish_seen_subcommand_from backup' \
    -s h -l help       -d '显示帮助'

# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------
complete -c proot-distro -n '__fish_seen_subcommand_from restore' \
    -s v -l verbose    -d '打印每个解压的文件'
complete -c proot-distro -n '__fish_seen_subcommand_from restore' \
    -s q -l quiet      -d '抑制非错误输出'
complete -c proot-distro -n '__fish_seen_subcommand_from restore' \
    -s h -l help       -d '显示帮助'

# ---------------------------------------------------------------------------
# clear-cache
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from clear-cache' \
    -s v -l verbose    -d 'List removed files'
complete -c proot-distro -f -n '__fish_seen_subcommand_from clear-cache' \
    -s q -l quiet      -d '抑制非错误输出'
complete -c proot-distro -f -n '__fish_seen_subcommand_from clear-cache' \
    -s h -l help       -d '显示帮助'

# ---------------------------------------------------------------------------
# copy
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from copy' \
    -a '(__proot_distro_containers)' -d 'Container (use container:path notation)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from copy' \
    -s v -l verbose    -d '打印每个复制的文件'
complete -c proot-distro -f -n '__fish_seen_subcommand_from copy' \
    -s q -l quiet      -d '抑制非错误输出'
complete -c proot-distro -f -n '__fish_seen_subcommand_from copy' \
    -s m -l move       -d 'Move instead of copy'
complete -c proot-distro -f -n '__fish_seen_subcommand_from copy' \
    -s r -l recursive  -d 'Copy directories recursively'
complete -c proot-distro -f -n '__fish_seen_subcommand_from copy' \
    -s h -l help       -d '显示帮助'

# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from sync' \
    -a '(__proot_distro_containers)' -d 'Container (use container:path notation)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from sync' \
    -s v -l verbose    -d 'Print each synced file'
complete -c proot-distro -f -n '__fish_seen_subcommand_from sync' \
    -s q -l quiet      -d '抑制非错误输出'
complete -c proot-distro -f -n '__fish_seen_subcommand_from sync' \
    -s c -l checksum      -d 'Use CRC32 checksum instead of size+mtime'
complete -c proot-distro -f -n '__fish_seen_subcommand_from sync' \
    -s d -l delete        -d 'Remove destination entries absent from source'
complete -c proot-distro -f -n '__fish_seen_subcommand_from sync' \
    -s h -l help          -d '显示帮助'

# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -a '(__proot_distro_containers)' -d '容器'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -s u -l user       -r -d '以该用户身份运行（默认: root）'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -s P -l redirect-ports -d '把 1024 以下端口重定向到非特权范围'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run; and __proot_distro_is_termux' \
    -l isolated           -d '隔离模式: 不带宿主环境变量或 Termux 路径'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run; and __proot_distro_is_termux' \
    -l minimal            -d '类似 --isolated，但还禁用 Android 系统绑定'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l shared-home        -d '把 home 挂载进容器'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l shared-tmp         -d '与宿主共享 /tmp'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l shared-x11         -d '共享 X11 socket（/tmp/.X11-unix）'
complete -c proot-distro -n '__fish_seen_subcommand_from run' \
    -s b -l bind       -r -d '把 PATH[:DEST] bind 挂载进容器（可重复）'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run; and __proot_distro_is_termux' \
    -l no-link2symlink    -d '禁用 proot link2symlink 扩展'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run; and __proot_distro_is_termux' \
    -l no-sysvipc         -d '禁用 SysV IPC 模拟'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run; and __proot_distro_is_termux' \
    -l no-kill-on-exit    -d '会话结束时不清子进程'
complete -c proot-distro -n '__fish_seen_subcommand_from run' \
    -l emulator        -r -d 'QEMU 用户模式模拟器二进制路径'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l kernel          -r -d 'uname 报告的伪内核版本字符串'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l hostname        -r -d '容器内可见的 hostname'
complete -c proot-distro -n '__fish_seen_subcommand_from run' \
    -s w -l work-dir   -r -d '容器内初始工作目录'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -s e -l env        -r -d '设置环境变量 VAR=VALUE（可重复）'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -s d -l detach        -d '在后台启动会话'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l get-proot-cmd      -d '打印 proot 命令行后退出'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -s h -l help          -d '显示帮助'

# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
complete -c proot-distro -n '__fish_seen_subcommand_from build' \
    -s f -l file       -r -d 'Path to Dockerfile (- reads from stdin)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from build' \
    -s t -l tag        -r -d '要赋予的镜像引用（可重复）'
complete -c proot-distro -f -n '__fish_seen_subcommand_from build' \
    -l build-arg       -r -d 'Set a build-time ARG (repeatable)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from build' \
    -s a -l architecture -r -d '目标 CPU 架构' \
    -a 'aarch64\tAArch64 arm\tARM(32-bit) i686\tx86(32-bit) riscv64\tRISC-V x86_64\tx86_64'
complete -c proot-distro -f -n '__fish_seen_subcommand_from build' \
    -l target          -r -d 'Stop after this named build stage'
complete -c proot-distro -n '__fish_seen_subcommand_from build' \
    -l emulator        -r -d 'QEMU 用户模式模拟器二进制路径'
complete -c proot-distro -n '__fish_seen_subcommand_from build' \
    -s o -l output     -r -d 'Write OCI tarball to FILE (repeatable)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from build' \
    -l install-as      -r -d 'Install image as a local container after build' \
    -a '(__proot_distro_containers)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from build' \
    -l no-cache           -d 'Disable per-instruction build cache'
complete -c proot-distro -f -n '__fish_seen_subcommand_from build' \
    -s v -l verbose       -d '回显每条指令并流式输出 RUN 结果'
complete -c proot-distro -f -n '__fish_seen_subcommand_from build' \
    -s q -l quiet         -d '抑制非错误输出'
complete -c proot-distro -f -n '__fish_seen_subcommand_from build' \
    -s h -l help          -d '显示帮助'

# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from push' \
    -s a -l architecture -r -d '目标 CPU 架构' \
    -a 'aarch64\tAArch64 arm\tARM(32-bit) i686\tx86(32-bit) riscv64\tRISC-V x86_64\tx86_64'
complete -c proot-distro -f -n '__fish_seen_subcommand_from push' \
    -l allow-insecure  -d 'Allow pushing to an HTTP-only or untrusted-TLS registry'
complete -c proot-distro -f -n '__fish_seen_subcommand_from push' \
    -s q -l quiet      -d '抑制非错误输出'
complete -c proot-distro -f -n '__fish_seen_subcommand_from push' \
    -s h -l help       -d '显示帮助'

# ---------------------------------------------------------------------------
# mirror
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from mirror'     -a 'ls'  -d '列出内置镜像源'
complete -c proot-distro -f -n '__fish_seen_subcommand_from mirror'     -a 'list' -d '列出内置镜像源（同 ls）'
complete -c proot-distro -f -n '__fish_seen_subcommand_from mirror'     -a 'test' -d '探测镜像源可达性'
complete -c proot-distro -f -n '__fish_seen_subcommand_from mirror'     -a 'use' -d '选中一个镜像源'
complete -c proot-distro -f -n '__fish_seen_subcommand_from mirror'     -a 'set' -d '选中一个镜像源（同 use）'
complete -c proot-distro -f -n '__fish_seen_subcommand_from mirror'     -a 'unset' -d '清除当前选中'
complete -c proot-distro -f -n '__fish_seen_subcommand_from mirror'     -a 'clear' -d '清除当前选中（同 unset）'
complete -c proot-distro -f -n '__fish_seen_subcommand_from mirror'     -a 'off' -d '清除当前选中（同 unset）'
complete -c proot-distro -f -n '__fish_seen_subcommand_from mirror'     -a 'show' -d '打印当前生效 URL'
complete -c proot-distro -f -n '__fish_seen_subcommand_from mirror'     -a 'daocloud 1panel-live 1ms xuanyuan xuanyuan-run netease tencent docker-cn docker-hub'     -d '内置镜像源 ID'
complete -c proot-distro -f -n '__fish_seen_subcommand_from mirror'     -s t -l timeout -r -d '探测超时（秒，默认 6）'
complete -c proot-distro -f -n '__fish_seen_subcommand_from mirror'     -l allow-insecure -d '允许 HTTPS 跳过证书校验'
complete -c proot-distro -f -n '__fish_seen_subcommand_from mirror'     -l use-best -d 'test 后自动切换到最快源'
complete -c proot-distro -f -n '__fish_seen_subcommand_from mirror'     -l no-use -d 'test 后不提示也不切换'
complete -c proot-distro -f -n '__fish_seen_subcommand_from mirror'     -s q -l quiet -d '安静模式'
complete -c proot-distro -f -n '__fish_seen_subcommand_from mirror'     -s h -l help -d '显示帮助'

# ---------------------------------------------------------------------------
# help
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from help' \
    -a 'install remove rename reset login list ps kill backup restore clear-cache copy sync run build push' \
    -d 'Topic'

# ---------------------------------------------------------------------------
# pd (same entry point, duplicate all completions)
# ---------------------------------------------------------------------------
complete -c pd -f -n __proot_distro_no_subcommand -a install     -d '从 Docker 镜像或本地归档安装容器'
complete -c pd -f -n __proot_distro_no_subcommand -a remove      -d '删除已安装的容器'
complete -c pd -f -n __proot_distro_no_subcommand -a rename      -d '重命名容器'
complete -c pd -f -n __proot_distro_no_subcommand -a reset       -d '从原始镜像重新安装容器'
complete -c pd -f -n __proot_distro_no_subcommand -a login       -d '在容器内打开 shell'
complete -c pd -f -n __proot_distro_no_subcommand -a list        -d '列出已安装的容器'
complete -c pd -f -n __proot_distro_no_subcommand -a ps          -d '列出活跃的容器会话'
complete -c pd -f -n __proot_distro_no_subcommand -a kill        -d '停止活跃的容器会话'
complete -c pd -f -n __proot_distro_no_subcommand -a backup      -d '把容器备份为 tar 归档'
complete -c pd -f -n __proot_distro_no_subcommand -a restore     -d '从 tar 归档恢复容器'
complete -c pd -f -n __proot_distro_no_subcommand -a clear-cache -d '清空下载缓存'
complete -c pd -f -n __proot_distro_no_subcommand -a copy        -d '在宿主与容器之间复制文件'
complete -c pd -f -n __proot_distro_no_subcommand -a sync        -d '在宿主与容器之间同步文件'
complete -c pd -f -n __proot_distro_no_subcommand -a run         -d '在容器中运行镜像的 entrypoint/cmd'
complete -c pd -f -n __proot_distro_no_subcommand -a build       -d '从 Dockerfile 构建 OCI 镜像'
complete -c pd -f -n __proot_distro_no_subcommand -a push        -d '把本地构建的镜像推送到仓库'
complete -c pd -f -n __proot_distro_no_subcommand -a help        -d '显示帮助'
complete -c pd -f -n __proot_distro_no_subcommand -s h -l help   -d '显示帮助'

complete -c pd -f -n '__fish_seen_subcommand_from install' -s n -l name         -r -d '以自定义容器名安装'
complete -c pd -f -n '__fish_seen_subcommand_from install' -s a -l architecture -r -d '目标 CPU 架构' -a 'aarch64 arm i686 riscv64 x86_64'
complete -c pd -f -n '__fish_seen_subcommand_from install' -s q -l quiet           -d '抑制非错误输出'
complete -c pd -f -n '__fish_seen_subcommand_from install' -s h -l help             -d '显示帮助'

complete -c pd -f -n '__fish_seen_subcommand_from remove' -a '(__proot_distro_containers)' -d '容器'
complete -c pd -f -n '__fish_seen_subcommand_from remove' -s v -l verbose -d '打印每个被删除的文件'
complete -c pd -f -n '__fish_seen_subcommand_from remove' -s q -l quiet   -d '抑制非错误输出'
complete -c pd -f -n '__fish_seen_subcommand_from remove' -s h -l help    -d '显示帮助'

complete -c pd -f -n '__fish_seen_subcommand_from rename' -a '(__proot_distro_containers)' -d '容器'
complete -c pd -f -n '__fish_seen_subcommand_from rename' -s q -l quiet -d '抑制非错误输出'
complete -c pd -f -n '__fish_seen_subcommand_from rename' -s h -l help -d '显示帮助'

complete -c pd -f -n '__fish_seen_subcommand_from reset' -a '(__proot_distro_containers)' -d '容器'
complete -c pd -f -n '__fish_seen_subcommand_from reset' -s q -l quiet -d '抑制非错误输出'
complete -c pd -f -n '__fish_seen_subcommand_from reset' -s h -l help -d '显示帮助'

complete -c pd -f -n '__fish_seen_subcommand_from login' -a '(__proot_distro_containers)' -d '容器'
complete -c pd -f -n '__fish_seen_subcommand_from login' -s u -l user         -r -d 'Run as this user'
complete -c pd -f -n '__fish_seen_subcommand_from login' -s P -l redirect-ports   -d 'Redirect ports below 1024'
complete -c pd -f -n '__fish_seen_subcommand_from login; and __proot_distro_is_termux' -l isolated -d 'Isolated mode'
complete -c pd -f -n '__fish_seen_subcommand_from login; and __proot_distro_is_termux' -l minimal  -d 'Minimal isolated mode'
complete -c pd -f -n '__fish_seen_subcommand_from login' -l shared-home          -d 'Mount home inside container'
complete -c pd -f -n '__fish_seen_subcommand_from login' -l shared-tmp           -d 'Share /tmp with host'
complete -c pd -f -n '__fish_seen_subcommand_from login' -l shared-x11           -d 'Share X11 socket'
complete -c pd -n   '__fish_seen_subcommand_from login' -s b -l bind          -r -d 'Bind-mount path (repeatable)'
complete -c pd -f -n '__fish_seen_subcommand_from login; and __proot_distro_is_termux' -l no-link2symlink -d 'Disable link2symlink'
complete -c pd -f -n '__fish_seen_subcommand_from login; and __proot_distro_is_termux' -l no-sysvipc     -d 'Disable SysV IPC'
complete -c pd -f -n '__fish_seen_subcommand_from login; and __proot_distro_is_termux' -l no-kill-on-exit -d 'Do not kill on exit'
complete -c pd -n   '__fish_seen_subcommand_from login' -l emulator            -r -d 'Emulator binary path'
complete -c pd -f -n '__fish_seen_subcommand_from login' -l kernel             -r -d 'Fake kernel release'
complete -c pd -f -n '__fish_seen_subcommand_from login' -l hostname           -r -d 'Container hostname'
complete -c pd -n   '__fish_seen_subcommand_from login' -s w -l work-dir       -r -d 'Working directory'
complete -c pd -f -n '__fish_seen_subcommand_from login' -s e -l env           -r -d 'Environment variable'
complete -c pd -f -n '__fish_seen_subcommand_from login' -s d -l detach           -d 'Start session in background'
complete -c pd -f -n '__fish_seen_subcommand_from login' -l get-proot-cmd         -d 'Print proot command'
complete -c pd -f -n '__fish_seen_subcommand_from login' -s h -l help             -d '显示帮助'

complete -c pd -f -n '__fish_seen_subcommand_from list' -s q -l quiet -d '抑制非错误输出'
complete -c pd -f -n '__fish_seen_subcommand_from list' -s h -l help  -d '显示帮助'

complete -c pd -f -n '__fish_seen_subcommand_from ps' -s q -l quiet -d 'Print only PIDs, one per line'
complete -c pd -f -n '__fish_seen_subcommand_from ps' -s h -l help  -d '显示帮助'

complete -c pd -f -n '__fish_seen_subcommand_from kill' -a '(__proot_distro_containers)' -d '容器'
complete -c pd -f -n '__fish_seen_subcommand_from kill' -s s -l signal -r -d 'Signal to send instead of SIGTERM' -a 'TERM KILL HUP INT QUIT USR1 USR2'
complete -c pd -f -n '__fish_seen_subcommand_from kill' -l all          -d 'Stop every active session'
complete -c pd -f -n '__fish_seen_subcommand_from kill' -s h -l help    -d '显示帮助'

complete -c pd -f -n '__fish_seen_subcommand_from backup' -a '(__proot_distro_containers)' -d '容器'
complete -c pd -n   '__fish_seen_subcommand_from backup' -s o -l output    -r -d 'Output archive file'
complete -c pd -f -n '__fish_seen_subcommand_from backup' -s c -l compress -r -d 'Compression type' -a 'gzip bzip2 xz none'
complete -c pd -f -n '__fish_seen_subcommand_from backup' -s v -l verbose -d 'Verbose output'
complete -c pd -f -n '__fish_seen_subcommand_from backup' -s q -l quiet   -d '抑制非错误输出'
complete -c pd -f -n '__fish_seen_subcommand_from backup' -s h -l help    -d '显示帮助'

complete -c pd -n '__fish_seen_subcommand_from restore' -s v -l verbose -d 'Verbose output'
complete -c pd -n '__fish_seen_subcommand_from restore' -s q -l quiet   -d '抑制非错误输出'
complete -c pd -n '__fish_seen_subcommand_from restore' -s h -l help    -d '显示帮助'

complete -c pd -f -n '__fish_seen_subcommand_from clear-cache' -s v -l verbose -d 'Verbose output'
complete -c pd -f -n '__fish_seen_subcommand_from clear-cache' -s q -l quiet   -d '抑制非错误输出'
complete -c pd -f -n '__fish_seen_subcommand_from clear-cache' -s h -l help    -d '显示帮助'

complete -c pd -f -n '__fish_seen_subcommand_from copy' -a '(__proot_distro_containers)' -d '容器'
complete -c pd -f -n '__fish_seen_subcommand_from copy' -s v -l verbose   -d 'Verbose output'
complete -c pd -f -n '__fish_seen_subcommand_from copy' -s q -l quiet     -d '抑制非错误输出'
complete -c pd -f -n '__fish_seen_subcommand_from copy' -s m -l move      -d 'Move instead of copy'
complete -c pd -f -n '__fish_seen_subcommand_from copy' -s r -l recursive -d 'Recursive copy'
complete -c pd -f -n '__fish_seen_subcommand_from copy' -s h -l help      -d '显示帮助'

complete -c pd -f -n '__fish_seen_subcommand_from sync' -a '(__proot_distro_containers)' -d '容器'
complete -c pd -f -n '__fish_seen_subcommand_from sync' -s v -l verbose      -d 'Verbose output'
complete -c pd -f -n '__fish_seen_subcommand_from sync' -s q -l quiet        -d '抑制非错误输出'
complete -c pd -f -n '__fish_seen_subcommand_from sync' -s c -l checksum     -d 'Use CRC32 checksum'
complete -c pd -f -n '__fish_seen_subcommand_from sync' -s d -l delete       -d 'Delete extra destination files'
complete -c pd -f -n '__fish_seen_subcommand_from sync' -s h -l help         -d '显示帮助'

complete -c pd -f -n '__fish_seen_subcommand_from run' -a '(__proot_distro_containers)' -d '容器'
complete -c pd -f -n '__fish_seen_subcommand_from run' -s u -l user         -r -d 'Run as this user'
complete -c pd -f -n '__fish_seen_subcommand_from run' -s P -l redirect-ports   -d 'Redirect ports below 1024'
complete -c pd -f -n '__fish_seen_subcommand_from run; and __proot_distro_is_termux' -l isolated -d 'Isolated mode'
complete -c pd -f -n '__fish_seen_subcommand_from run; and __proot_distro_is_termux' -l minimal  -d 'Minimal isolated mode'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l shared-home          -d 'Mount home inside container'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l shared-tmp           -d 'Share /tmp with host'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l shared-x11           -d 'Share X11 socket'
complete -c pd -n   '__fish_seen_subcommand_from run' -s b -l bind          -r -d 'Bind-mount path (repeatable)'
complete -c pd -f -n '__fish_seen_subcommand_from run; and __proot_distro_is_termux' -l no-link2symlink -d 'Disable link2symlink'
complete -c pd -f -n '__fish_seen_subcommand_from run; and __proot_distro_is_termux' -l no-sysvipc     -d 'Disable SysV IPC'
complete -c pd -f -n '__fish_seen_subcommand_from run; and __proot_distro_is_termux' -l no-kill-on-exit -d 'Do not kill on exit'
complete -c pd -n   '__fish_seen_subcommand_from run' -l emulator            -r -d 'Emulator binary path'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l kernel             -r -d 'Fake kernel release'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l hostname           -r -d 'Container hostname'
complete -c pd -n   '__fish_seen_subcommand_from run' -s w -l work-dir       -r -d 'Working directory'
complete -c pd -f -n '__fish_seen_subcommand_from run' -s e -l env           -r -d 'Environment variable'
complete -c pd -f -n '__fish_seen_subcommand_from run' -s d -l detach           -d 'Start session in background'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l get-proot-cmd         -d 'Print proot command'
complete -c pd -f -n '__fish_seen_subcommand_from run' -s h -l help             -d '显示帮助'

complete -c pd -f -n '__fish_seen_subcommand_from build' -s f -l file         -r -d 'Path to Dockerfile (- reads from stdin)'
complete -c pd -f -n '__fish_seen_subcommand_from build' -s t -l tag          -r -d '要赋予的镜像引用（可重复）'
complete -c pd -f -n '__fish_seen_subcommand_from build' -l build-arg         -r -d 'Set a build-time ARG (repeatable)'
complete -c pd -f -n '__fish_seen_subcommand_from build' -s a -l architecture -r -d '目标 CPU 架构' -a 'aarch64 arm i686 riscv64 x86_64'
complete -c pd -f -n '__fish_seen_subcommand_from build' -l target            -r -d 'Stop after this named build stage'
complete -c pd -n   '__fish_seen_subcommand_from build' -l emulator           -r -d 'Emulator binary path'
complete -c pd -n   '__fish_seen_subcommand_from build' -s o -l output        -r -d 'Write OCI tarball to FILE (repeatable)'
complete -c pd -f -n '__fish_seen_subcommand_from build' -l install-as        -r -d 'Install image as a local container after build' -a '(__proot_distro_containers)'
complete -c pd -f -n '__fish_seen_subcommand_from build' -l no-cache             -d 'Disable per-instruction build cache'
complete -c pd -f -n '__fish_seen_subcommand_from build' -s v -l verbose          -d '回显每条指令并流式输出 RUN 结果'
complete -c pd -f -n '__fish_seen_subcommand_from build' -s q -l quiet            -d '抑制非错误输出'
complete -c pd -f -n '__fish_seen_subcommand_from build' -s h -l help             -d '显示帮助'

complete -c pd -f -n '__fish_seen_subcommand_from push' -s a -l architecture -r -d '目标 CPU 架构' -a 'aarch64 arm i686 riscv64 x86_64'
complete -c pd -f -n '__fish_seen_subcommand_from push' -l allow-insecure       -d 'Allow pushing to an HTTP-only or untrusted-TLS registry'
complete -c pd -f -n '__fish_seen_subcommand_from push' -s q -l quiet           -d '抑制非错误输出'
complete -c pd -f -n '__fish_seen_subcommand_from push' -s h -l help            -d '显示帮助'

complete -c pd -f -n '__fish_seen_subcommand_from help' \
    -a 'install remove rename reset login list ps kill backup restore clear-cache copy sync run build push' -d 'Topic'
