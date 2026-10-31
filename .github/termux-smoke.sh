#!/data/data/com.termux/files/usr/bin/bash
#
# Termux smoke suite for proot-distro-i18n.
#
# Runs INSIDE the termux/termux-docker:aarch64 container (native arm64
# CI runner), with the repo mounted read-only at /work and /tmp mounted
# as a read-only tmpfs -- the host-side equivalent of the Android
# layout this fork exists for: no writable /tmp, so every proot temp
# path must fall back to $PREFIX/tmp.
#
# EXPECTED_HASH must carry the git describe-style short hash of the
# checked-out commit (git rev-parse --short=8 HEAD).

set -u

PREFIX=/data/data/com.termux/files/usr
LOG=$PREFIX/tmp
mkdir -p "$LOG"
PASS=0
FAIL=0
ok()  { PASS=$((PASS+1)); echo "[PASS] $1"; }
bad() { FAIL=$((FAIL+1)); echo "[FAIL] $1"; }
PD() { python3 /work/proot-distro.py "$@"; }

# The image entrypoint drops privileges to the system user through a
# whitelist of env vars -- EXPECTED_HASH does not survive it -- so the
# workflow passes the hash as the first argument; the environment stays
# as the fallback for local runs.
EXPECTED_HASH=${1:-${EXPECTED_HASH:-}}

# Pin the UI language to English so the grep assertions below are
# stable (the repo's own pytest suite pins it the same way via
# conftest). `lang get` in T4c proves the language machinery itself.
export PD_LANG=en
export PD_BUILD_HASH="$EXPECTED_HASH"
# Pin the official registry: the fork defaults to a mirror when no
# config exists, and mirrors 401 on images they have not cached --
# exactly what T5a must not depend on.
export PD_REGISTRY_URL="https://registry-1.docker.io"

echo "== T0: this really is the Termux environment =="
if [ -x "$PREFIX/bin/sh" ] && [ -x /system/bin/linker64 ]; then
    ok T0
else
    bad T0
fi

echo "== T1: bootstrap runtime (python + proot from the Termux repo) =="
# pkg's mirror auto-picker has landed on dead community mirrors before
# (the 401/InRelease flake); pin the official Termux mirror and retry,
# so the bootstrap does not depend on mirror roulette.  The success
# test is the binaries themselves, not the exit status.
if [ -w "$PREFIX/etc/apt" ]; then
    echo "deb https://packages.termux.dev/apt/termux-main stable main" \
        > "$PREFIX/etc/apt/sources.list"
fi
attempts=0
until pkg update -y >>"$LOG/pkg.log" 2>&1 \
      && pkg install -y python proot >>"$LOG/pkg.log" 2>&1; do
    attempts=$((attempts + 1))
    [ "$attempts" -ge 3 ] && break
    sleep 5
done
if command -v python3 >/dev/null 2>&1 \
   && command -v proot >/dev/null 2>&1; then
    ok T1
else
    bad T1
    tail -20 "$LOG/pkg.log"
fi

echo "== T2: pd version shows the build hash and the runtime =="
V=$(PD version)
echo "$V"
printf '%s\n' "$V" | grep -q "(build $EXPECTED_HASH)" && ok T2a || bad T2a
printf '%s\n' "$V" | grep -q "runtime.*proot" && ok T2b || bad T2b

echo "== T3: pd --version / -V route to the same block =="
PD --version | grep -q "(build $EXPECTED_HASH)" && ok T3a || bad T3a
PD -V | grep -q "(build $EXPECTED_HASH)" && ok T3b || bad T3b
PD v | grep -q "(build $EXPECTED_HASH)" && ok T3c || bad T3c

echo "== T4: help, list, lang render =="
PD help >/dev/null 2>&1 && ok T4a || bad T4a
PD list >/dev/null 2>&1 && ok T4b || bad T4b
PD lang get >/dev/null 2>&1 && ok T4c || bad T4c

echo "== T5: end-to-end — install alpine and log in (read-only /tmp) =="
if PD install alpine >"$LOG/install.log" 2>&1; then
    ok T5a
else
    bad T5a
    tail -30 "$LOG/install.log"
fi
OUT=$(PD login alpine -- cat /etc/os-release 2>"$LOG/login.err")
printf '%s\n' "$OUT" | grep -q "ID=alpine" \
    && ok T5b || { bad T5b; tail -30 "$LOG/login.err"; }
OUT=$(PD login alpine -- sh -c 'echo E2E-OK')
printf '%s\n' "$OUT" | grep -q "E2E-OK" && ok T5c || bad T5c

echo "== T6: runtime line reflects PD_PROOT_BIN override =="
OUT=$(PD_PROOT_BIN="$PREFIX/bin/proot" PD version)
printf '%s\n' "$OUT" | grep -q "PD_PROOT_BIN" && ok T6 || bad T6

PD rm alpine >/dev/null 2>&1 || true

echo
echo "RESULT: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
