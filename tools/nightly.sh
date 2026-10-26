#!/bin/sh
# Nightly regression (PLAN 7.5): the checks CI cannot run, run on a machine that has the game.
#
# CI is deliberately dump-free (AGENTS.md forbids it downloading the game), so the dump-side checks and the
# emulator boot are only ever run by a human remembering to. This is the script that remembers: it runs the
# dump-free gate, then every dump-side check that exists, then optionally the xemu boot, writes one summary
# per run, and exits non-zero if any stage failed.
#
#   tools/nightly.sh                       # dump-free gate only
#   SG1_DUMP=~/sg1/dump tools/nightly.sh   # + manifest, inventory drift, symbol map
#   tools/nightly.sh --xemu --iso=... --bios=... --hdd=...   # + the boot smoke test
#
# Output: $NIGHTLY_OUT (default build/nightly)/<UTC timestamp>/ with one log per stage and a summary.txt.
# Nothing here commits or modifies tracked files; `git status --porcelain` is reported, not repaired.
set -u

out_root="${NIGHTLY_OUT:-build/nightly}"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
out="$out_root/$stamp"
mkdir -p "$out"
summary="$out/summary.txt"
fail=0
: > "$summary"

say() { printf '%s\n' "$*" | tee -a "$summary"; }

stage() {  # stage NAME COMMAND...
    name=$1
    shift
    log="$out/$name.log"
    start=$(date +%s)
    if "$@" > "$log" 2>&1; then
        say "PASS  $name  ($(($(date +%s) - start))s)"
    else
        rc=$?
        say "FAIL  $name  ($(($(date +%s) - start))s)  rc=$rc  see $log"
        fail=1
    fi
}

xemu=0
iso=""; bios=""; hdd=""
for arg in "$@"; do
    case "$arg" in
        --xemu) xemu=1 ;;
        --iso=*) iso=${arg#*=} ;;
        --bios=*) bios=${arg#*=} ;;
        --hdd=*) hdd=${arg#*=} ;;
        *) say "unknown argument: $arg"; exit 2 ;;
    esac
done

say "nightly $stamp"
say "repository $(git rev-parse --short HEAD 2>/dev/null || echo 'not a git checkout')"
say "python $(python3 -V 2>&1 | cut -d' ' -f2)"
say "dump ${SG1_DUMP:-<unset>}"
say ""

stage dump-free-gate make ci

if [ -n "${SG1_DUMP:-}" ]; then
    stage verify-dump make verify-dump SG1_DUMP="$SG1_DUMP"
    # Regenerating the inventory would rewrite tracked CSVs; the drift is what matters, so run the
    # generators into a scratch dir where they support it and diff, otherwise just report git status.
    if [ -x tools/xbe.py ]; then
        stage class-tables make class-tables SG1_DUMP="$SG1_DUMP"
        stage libraries make libraries SG1_DUMP="$SG1_DUMP"
    fi
    # The defaults coverage artifact is dump-gated, so `make ci` cannot hold it: reproduce it here instead and
    # fail when the committed CSV no longer matches a fresh pass.
    stage defaults-emit python3 tools/defaults-emit.py \
        "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/System" --check
    # Dump-gated for the same reason: the declarations live in the packages.
    stage native-reconcile python3 tools/native-reconcile.py \
        "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/System" -o "$out/native"
    # The rebuild pipeline's dump-side stages (docs/REBUILD.md); the compiler stages SKIP with a reason.
    stage rebuild SG1_DUMP="$SG1_DUMP" REBUILD_OUT="$out/rebuild" tools/build.sh --dry-run
else
    say "SKIP  dump-side stages (set SG1_DUMP)"
fi

if [ "$xemu" = 1 ]; then
    if [ -n "$iso" ] && [ -n "$bios" ] && [ -n "$hdd" ]; then
        stage xemu-boot python3 tools/xemu/smoke.py --iso="$iso" --bios="$bios" --hdd="$hdd" \
            --out="$out/xemu" --seconds="${XEMU_SECONDS:-110}"
    else
        say "SKIP  xemu-boot (needs --iso= --bios= --hdd=)"
    fi
fi

dirty=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
say ""
say "tracked files modified by this run: $dirty"
say "RESULT $([ "$fail" = 0 ] && echo PASS || echo FAIL)"
say "log directory: $out"
# keep the last 30 runs
ls -1dt "$out_root"/*/ 2>/dev/null | tail -n +31 | xargs -r rm -rf
exit "$fail"
