# proot-distro 与 pd 的 Bash 补全
#
# 系统级安装:
#   cp proot-distro.bash /usr/share/bash-completion/completions/proot-distro
# 当前用户安装:
#   mkdir -p ~/.local/share/bash-completion/completions
#   cp proot-distro.bash ~/.local/share/bash-completion/completions/proot-distro

_proot_distro_get_containers() {
    local dir
    if _proot_distro_is_termux; then
        dir="${TERMUX__PREFIX:-/data/data/com.termux/files/usr}/var/lib/proot-distro/containers"
    else
        dir="${XDG_DATA_HOME:-${HOME}/.local/share}/proot-distro/containers"
    fi
    if [[ -d "${dir}" ]]; then
        local d
        for d in "${dir}"/*/; do
            [[ -d "${d}rootfs" ]] && printf '%s\n' "${d%/}" | sed 's|.*/||'
        done
    fi
}

# 在 Termux/Android 上返回 0 (true): 三个独立指标中至少两个匹配，
# 与 constants.py 中的 _detect_termux() 保持一致。
_proot_distro_is_termux() {
    local score=0
    [[ -f /system/build.prop || -d /data/app ]] && ((score++))
    [[ -n "${TERMUX_APP__APP_VERSION_NAME}" || -n "${TERMUX_VERSION}" ]] && ((score++))
    local prefix="${TERMUX__PREFIX:-/data/data/com.termux/files/usr}"
    [[ -r "${prefix}" && -x "${prefix}" ]] && ((score++))
    [[ ${score} -ge 2 ]]
}

# 返回内置镜像源 ID 列表（供 mirror 子命令补全使用）。
_proot_distro_get_mirrors() {
    printf '%s\n' daocloud 1panel-live 1ms xuanyuan xuanyuan-run netease tencent docker-cn docker-hub
}

_proot_distro() {
    local cur prev words cword
    _init_completion || return

    local -r _all_commands="install remove rename reset login list ps kill backup restore
        clear-cache copy sync run build push mirror help"

    # Complete the subcommand itself
    if [[ ${cword} -eq 1 ]]; then
        COMPREPLY=($(compgen -W "${_all_commands}" -- "${cur}"))
        return
    fi

    local command="${words[1]}"

    case "${command}" in

        # -----------------------------------------------------------------------
        install)
            case "${prev}" in
                -n|--name)
                    return ;;
                -a|--architecture)
                    COMPREPLY=($(compgen -W "aarch64 arm i686 riscv64 x86_64" -- "${cur}"))
                    return ;;
            esac
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "-n --name -a --architecture --allow-insecure -q --quiet -h --help" -- "${cur}"))
            elif [[ "${cur}" == /* || "${cur}" == ./* || "${cur}" == ../* ]]; then
                _filedir
            fi
            ;;

        # -----------------------------------------------------------------------
        remove)
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "--verbose --quiet --help" -- "${cur}"))
            else
                COMPREPLY=($(compgen -W "$(_proot_distro_get_containers)" -- "${cur}"))
            fi
            ;;

        # -----------------------------------------------------------------------
        rename)
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "--quiet --help" -- "${cur}"))
            else
                COMPREPLY=($(compgen -W "$(_proot_distro_get_containers)" -- "${cur}"))
            fi
            ;;

        # -----------------------------------------------------------------------
        reset)
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "--quiet --help" -- "${cur}"))
            else
                COMPREPLY=($(compgen -W "$(_proot_distro_get_containers)" -- "${cur}"))
            fi
            ;;

        # -----------------------------------------------------------------------
        login)
            # After --, complete host-side commands
            local i
            for ((i = 2; i < cword; i++)); do
                if [[ "${words[i]}" == "--" ]]; then
                    _command_offset $((i + 1))
                    return
                fi
            done
            case "${prev}" in
                -u|--user)      return ;;
                -b|--bind)      _filedir;    return ;;
                --emulator)     _filedir;    return ;;
                --kernel)       return ;;
                --hostname)     return ;;
                -w|--work-dir)  _filedir -d; return ;;
                -e|--env)       return ;;
            esac
            if [[ "${cur}" == -* ]]; then
                local opts="-u --user -P --redirect-ports --shared-home --shared-tmp --shared-x11
                    -b --bind --emulator --kernel --hostname -w --work-dir
                    -e --env -d --detach --get-proot-cmd -h --help"
                _proot_distro_is_termux && \
                    opts+=" --isolated --minimal --no-link2symlink --no-sysvipc --no-kill-on-exit"
                COMPREPLY=($(compgen -W "${opts}" -- "${cur}"))
            else
                COMPREPLY=($(compgen -W "$(_proot_distro_get_containers)" -- "${cur}"))
            fi
            ;;

        # -----------------------------------------------------------------------
        list)
            COMPREPLY=($(compgen -W "-q --quiet -h --help" -- "${cur}"))
            ;;

        # -----------------------------------------------------------------------
        ps)
            COMPREPLY=($(compgen -W "-q --quiet -h --help" -- "${cur}"))
            ;;

        # -----------------------------------------------------------------------
        kill)
            case "${prev}" in
                -s|--signal)
                    COMPREPLY=($(compgen -W "TERM KILL HUP INT QUIT USR1 USR2" -- "${cur}"))
                    return ;;
            esac
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "-s --signal --all -h --help" -- "${cur}"))
            else
                COMPREPLY=($(compgen -W "$(_proot_distro_get_containers)" -- "${cur}"))
            fi
            ;;

        # -----------------------------------------------------------------------
        backup)
            case "${prev}" in
                -o|--output)
                    _filedir
                    return ;;
                -c|--compress)
                    COMPREPLY=($(compgen -W "gzip bzip2 xz none" -- "${cur}"))
                    return ;;
            esac
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "-o --output -c --compress -v --verbose -q --quiet -h --help" -- "${cur}"))
            else
                COMPREPLY=($(compgen -W "$(_proot_distro_get_containers)" -- "${cur}"))
            fi
            ;;

        # -----------------------------------------------------------------------
        restore)
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "--verbose --quiet --help" -- "${cur}"))
            else
                _filedir '@(tar|tar.gz|tgz|tar.bz2|tbz2|tar.xz|txz)'
            fi
            ;;

        # -----------------------------------------------------------------------
        clear-cache)
            COMPREPLY=($(compgen -W "--verbose --quiet --help" -- "${cur}"))
            ;;

        # -----------------------------------------------------------------------
        copy)
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "--verbose --quiet --move --recursive --help" -- "${cur}"))
            else
                # Support container:path notation: complete container names
                # (no colon yet) or paths (colon already present → filesystem)
                if [[ "${cur}" == *:* ]]; then
                    _filedir
                else
                    local containers
                    containers=$(_proot_distro_get_containers)
                    COMPREPLY=($(compgen -W "${containers}" -- "${cur}"))
                    # Also allow plain host paths
                    local -a files
                    _filedir
                    COMPREPLY+=("${files[@]}")
                fi
            fi
            ;;

        # -----------------------------------------------------------------------
        sync)
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "-v --verbose -q --quiet -c --checksum -d --delete -h --help" -- "${cur}"))
            else
                if [[ "${cur}" == *:* ]]; then
                    _filedir
                else
                    local containers
                    containers=$(_proot_distro_get_containers)
                    COMPREPLY=($(compgen -W "${containers}" -- "${cur}"))
                    local -a files
                    _filedir
                    COMPREPLY+=("${files[@]}")
                fi
            fi
            ;;

        # -----------------------------------------------------------------------
        run)
            # After --, complete host-side commands
            local i
            for ((i = 2; i < cword; i++)); do
                if [[ "${words[i]}" == "--" ]]; then
                    _command_offset $((i + 1))
                    return
                fi
            done
            case "${prev}" in
                -u|--user)      return ;;
                -b|--bind)      _filedir;    return ;;
                --emulator)     _filedir;    return ;;
                --kernel)       return ;;
                --hostname)     return ;;
                -w|--work-dir)  _filedir -d; return ;;
                -e|--env)       return ;;
            esac
            if [[ "${cur}" == -* ]]; then
                local opts="-u --user -P --redirect-ports --shared-home --shared-tmp --shared-x11
                    -b --bind --emulator --kernel --hostname -w --work-dir
                    -e --env -d --detach --get-proot-cmd -h --help"
                _proot_distro_is_termux && \
                    opts+=" --isolated --minimal --no-link2symlink --no-sysvipc --no-kill-on-exit"
                COMPREPLY=($(compgen -W "${opts}" -- "${cur}"))
            else
                COMPREPLY=($(compgen -W "$(_proot_distro_get_containers)" -- "${cur}"))
            fi
            ;;

        # -----------------------------------------------------------------------
        build)
            case "${prev}" in
                -f|--file)
                    _filedir
                    return ;;
                -t|--tag)
                    return ;;
                --build-arg)
                    return ;;
                -a|--architecture)
                    COMPREPLY=($(compgen -W "aarch64 arm i686 riscv64 x86_64" -- "${cur}"))
                    return ;;
                --target)
                    return ;;
                --emulator)
                    _filedir
                    return ;;
                -o|--output)
                    _filedir
                    return ;;
                --install-as)
                    COMPREPLY=($(compgen -W "$(_proot_distro_get_containers)" -- "${cur}"))
                    return ;;
            esac
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "-f --file -t --tag --build-arg -a --architecture
                    --target --emulator -o --output --install-as --no-cache
                    -v --verbose -q --quiet -h --help" -- "${cur}"))
            else
                _filedir -d
            fi
            ;;

        # -----------------------------------------------------------------------
        push)
            case "${prev}" in
                -a|--architecture)
                    COMPREPLY=($(compgen -W "aarch64 arm i686 riscv64 x86_64" -- "${cur}"))
                    return ;;
            esac
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "-a --architecture --allow-insecure -q --quiet -h --help" -- "${cur}"))
            fi
            ;;

        # -----------------------------------------------------------------------
        mirror)
            case "${prev}" in
                -t|--timeout)
                    return ;;
            esac
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "-t --timeout --allow-insecure --use-best --no-use -q --quiet -h --help" -- "${cur}"))
            else
                # 第一个位置参数: 子动作; 第二个: ID|URL
                # 简化: 如果当前词是已知动作, 补动作; 否则补镜像 ID
                local actions="ls list test use set unset clear off show"
                if [[ " ${actions} " == *" ${cur} "* ]]; then
                    COMPREPLY=($(compgen -W "${actions}" -- "${cur}"))
                else
                    # 看上一个词是不是动作, 是就补 ID
                    local prev_word="${words[cword-1]}"
                    if [[ " ${actions} " == *" ${prev_word} "* ]]; then
                        COMPREPLY=($(compgen -W "$(_proot_distro_get_mirrors)" -- "${cur}"))
                    else
                        COMPREPLY=($(compgen -W "${actions}" -- "${cur}"))
                    fi
                fi
            fi
            ;;

        # -----------------------------------------------------------------------
        help)
            local topics="install remove rename reset login list ps kill backup restore clear-cache copy sync run build push mirror"
            COMPREPLY=($(compgen -W "${topics}" -- "${cur}"))
            ;;

    esac
}

complete -F _proot_distro proot-distro
complete -F _proot_distro pd
