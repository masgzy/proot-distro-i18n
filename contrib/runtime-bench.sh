#!/data/data/com.termux/files/usr/bin/bash
#
# runtime-bench.sh -- compare nproot and the stock proot through pd.
#
# Usage:
#   bash runtime-bench.sh [container] [reps]
#   container  an installed pd container (default: the first one)
#   reps       timed repetitions per workload, best kept (default: 3)
#
# Every workload is timed on the HOST around a single `pd run` per
# repetition, so pd's own Python startup is a constant both runtimes
# pay; the difference is what proot/nproot add per syscall, per exec
# and per file access.  Each workload first runs once as a warmup
# (page cache, loader extraction) and is then timed <reps> times; the
# minimum is reported, being the least scheduler-noisy sample.
#
# Requires bash >= 5 (EPOCHREALTIME).  A runtime missing from the
# system is simply skipped from the table.  Timing on a phone: plug
# the charger in, keep the screen on, and close the other apps, or
# thermal throttling will flatten the difference.
#
# What the workloads exercise:
#   baseline   the session itself (constant to subtract mentally)
#   spawn      300 fork+exec pairs -- the runtime's execve path
#   scan       getdents/stat storm over /usr
#   read       open/read/close storm over real files (tar of /usr)
#   shell      50 full guest shell startups -- worst case per-exec
#
# Reading the numbers: nproot's advantage is the no-ptrace design
# (seccomp trap + process_vm instead of ptrace stops), so it shows on
# syscall-dense workloads and on spawning; on big sequential reads
# both runtimes are within noise.  A ratio near 1.0 means "same", not
# "broken".

set -u

CONTAINER=${1:-}
REPS=${2:-3}
PD=${PD:-pd}

command -v "$PD" >/dev/null 2>&1 || { echo "pd not on PATH" >&2; exit 1; }

if [ -z "$CONTAINER" ]; then
        CONTAINER=$("$PD" list 2>/dev/null | awk '/^[A-Za-z0-9._-]+$/{print $1; exit}')
        [ -n "$CONTAINER" ] || { echo "no installed container found; pass one: $0 <container>" >&2; exit 1; }
fi

now_us() { echo "${EPOCHREALTIME/./}"; }

run_one() {  # run_one <proot-bin> <code>
        PD_PROOT_BIN=$1 "$PD" run "$CONTAINER" -- /bin/sh -c "$2" \
                >/dev/null 2>&1
}

bench() {  # bench <proot-bin> <code> -> best wall time in ms
        local bin=$1 code=$2 i t0 t1 best=0
        run_one "$bin" "$code"          # warmup
        for i in $(seq 1 "$REPS"); do
                t0=$(now_us)
                run_one "$bin" "$code"
                t1=$(now_us)
                if [ "$best" = 0 ] || [ $((t1 - t0)) -lt "$best" ]; then
                        best=$(( t1 - t0 ))
                fi
        done
        echo $(( best / 1000 ))
}

# Runtimes: PD_PROOT_BIN wants an absolute path to a real file.
PROOT_NP=""; PROOT_SP=""
if command -v nproot >/dev/null 2>&1; then
        PROOT_NP=$(command -v nproot)
fi
if command -v proot >/dev/null 2>&1; then
        PROOT_SP=$(command -v proot)
fi
[ -n "$PROOT_NP" ] || [ -n "$PROOT_SP" ] || {
        echo "neither nproot nor proot found" >&2; exit 1; }

echo "container: $CONTAINER   reps: $REPS"
echo "runtime versions:"
if [ -n "$PROOT_NP" ]; then
        echo "  nproot: $("$PROOT_NP" -V 2>/dev/null | head -1)"
fi
if [ -n "$PROOT_SP" ]; then
        echo "  proot:  $("$PROOT_SP" --version 2>/dev/null | head -1 || echo unknown)"
fi
echo

LABELS=(
        "baseline (session only)"
        "spawn 300x true"
        "scan: find /usr | wc -l"
        "read: tar -cf - /usr | wc -c"
        "shell: 50x sh -c exit"
)
CODES=(
        "true"
        'i=0; while [ $i -lt 300 ]; do true; i=$((i+1)); done'
        'find /usr 2>/dev/null | wc -l'
        'tar -cf - /usr 2>/dev/null | wc -c'
        'i=0; while [ $i -lt 50 ]; do /bin/sh -c exit; i=$((i+1)); done'
)

NP_COL=""; SP_COL=""
i=0
for label in "${LABELS[@]}"; do
        code=${CODES[$i]}
        if [ -n "$PROOT_NP" ]; then
                NP_COL="$NP_COL $(bench "$PROOT_NP" "$code")"
        fi
        if [ -n "$PROOT_SP" ]; then
                SP_COL="$SP_COL $(bench "$PROOT_SP" "$code")"
        fi
        i=$((i+1))
done

# Render with only the columns whose runtime exists.
pick_col() {  # pick_col <var-name>
        if [ "$1" = PROOT_NP ] && [ -n "$PROOT_NP" ]; then return 0; fi
        if [ "$1" = PROOT_SP ] && [ -n "$PROOT_SP" ]; then return 0; fi
        return 1
}

if pick_col PROOT_NP && pick_col PROOT_SP; then
        printf "%-28s %10s %10s %8s\n" "workload (best of $REPS)" \
                "nproot ms" "proot ms" "np/proot"
        i=1
        for label in "${LABELS[@]}"; do
                np=$(echo "$NP_COL" | awk "{print \$$i}")
                sp=$(echo "$SP_COL" | awk "{print \$$i}")
                ratio=$(awk "BEGIN{ if ($sp > 0) printf \"%.2f\", $np / $sp; else print \"-\" }")
                printf "%-28s %10s %10s %8s\n" "$label" "$np" "$sp" "$ratio"
                i=$((i+1))
        done
else
        only=PROOT_NP; pick_col PROOT_SP && only=PROOT_SP
        if [ "$only" = PROOT_NP ]; then col=$NP_COL; name=nproot; else col=$SP_COL; name=proot; fi
        printf "%-28s %10s\n" "workload (best of $REPS)" "$name ms"
        i=1
        for label in "${LABELS[@]}"; do
                v=$(echo "$col" | awk "{print \$$i}")
                printf "%-28s %10s\n" "$label" "$v"
                i=$((i+1))
        done
fi
