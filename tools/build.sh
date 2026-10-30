#!/bin/sh
# The rebuild pipeline (PLAN 6.1): every stage in order, each one reporting PASS / FAIL / SKIP with a reason.
#
# The point of this script is not that it produces a playable build today — it cannot, because there is no `ucc`
# in this project's toolbox (PLAN 3.6) and payload *generation* is a compiler's job. The point is that the five
# stages around the compiler are either proven or explicitly missing, and that the missing ones say so instead of
# being discovered late. Every stage prints the command that would run it.
#
#   tools/build.sh --dry-run                      # everything that needs no compiler
#   SG1_DUMP=~/sg1/dump tools/build.sh            # + the dump-side stages (target `full`)
#   SG1_DUMP=~/sg1/dump tools/build.sh --target hybrid   # the packages a hybrid build carries, with a manifest
#   SG1_DUMP=~/sg1/dump UCC=~/tools/ucc/ucc tools/build.sh    # + compile (not available: see docs/REBUILD.md)
#
# Two targets (PLAN 6.4): `hybrid` is our scripts + the original assets, `full` is our scripts + re-extracted
# assets. What each can do *today* differs, and the script says which stages it ran rather than implying the rest.
#
# Output: build/rebuild/<UTC>/ with one log per stage and summary.txt. Nothing is committed.
set -u

out_root="${REBUILD_OUT:-build/rebuild}"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
out="$out_root/$stamp"
mkdir -p "$out"
summary="$out/summary.txt"
fail=0
dry=0

target=full
while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run) dry=1 ;;
        --target)
            shift
            case "${1:-}" in
                hybrid|full) target=$1 ;;
                *) echo "--target takes hybrid or full"; exit 2 ;;
            esac
            ;;
        --target=*) target=${1#--target=}
            case "$target" in hybrid|full) ;; *) echo "--target takes hybrid or full"; exit 2 ;; esac ;;
        *) echo "unknown argument: $1"; exit 2 ;;
    esac
    shift
done

say() { printf '%s\n' "$*" | tee -a "$summary"; }

stage() {  # stage NAME COMMAND...
    name=$1; shift
    log="$out/$name.log"
    say "RUN   $name: $*"
    if "$@" > "$log" 2>&1; then
        say "PASS  $name"
    else
        rc=$?
        say "FAIL  $name  rc=$rc  see $log"
        fail=1
    fi
}

skip() { say "SKIP  $1  ($2)"; }

say "rebuild $stamp  target $target"
say "repository $(git rev-parse --short HEAD 2>/dev/null || echo 'not a git checkout')"
say "python $(python3 -V 2>&1 | cut -d' ' -f2)"
say "dump ${SG1_DUMP:-<unset>}"
say "compiler ${UCC:-<unset>}"
say ""

# 1. The container format. Proven by a round trip, not by inspection: every package is re-laid through the
#    writer's own placement path and must come back byte-identical.
stage writer-relay python3 tools/pkg-write.py "${SG1_DUMP:-$HOME/sg1/dump}/Stargate_SG-1_-_The_Alliance-2005-12-15/System" --relay

# 2. Rebuild identity (PLAN 6.1/6.3). The writer assembles each package from its own model and must reproduce the
#    original byte for byte. That is also why determinism needs no separate test today: the payloads are copied,
#    so there is nothing that could differ between runs. The real determinism test arrives with a compiler, which
#    is exactly the stage below that is missing — saying "6.3 done" on the strength of a copied payload would be
#    the kind of claim this project avoids.
if [ -d "${SG1_DUMP:-$HOME/sg1/dump}/Stargate_SG-1_-_The_Alliance-2005-12-15/System" ]; then
    stage rebuild-identity sh -c 'python3 tools/pkg-write.py "'"${SG1_DUMP:-$HOME/sg1/dump}"'/Stargate_SG-1_-_The_Alliance-2005-12-15/System" --rebuild > "'"$out"'/rebuild.txt" 2>&1 &&
        grep -q "18 of 18 packages rebuild byte for byte" "'"$out"'/rebuild.txt"'
else
    skip rebuild-identity "no package directory at ${SG1_DUMP:-$HOME/sg1/dump}"
fi

# 3. The source tree the compiler would consume: one .uc per class, plus the defaults the packages carry.
if [ -d "${SG1_DUMP:-$HOME/sg1/dump}/Stargate_SG-1_-_The_Alliance-2005-12-15/System" ]; then
    stage source-tree python3 tools/pkg-script-text.py \
        "${SG1_DUMP:-$HOME/sg1/dump}/Stargate_SG-1_-_The_Alliance-2005-12-15/System" --tree --write "$out/source-tree"
    stage merge-defaults python3 tools/defaults-emit.py \
        "${SG1_DUMP:-$HOME/sg1/dump}/Stargate_SG-1_-_The_Alliance-2005-12-15/System" --merge "$out/source-tree"
else
    skip source-tree "needs the dump (the classes ship their own source inside the packages)"
    skip merge-defaults "needs the dump"
fi

# 4. The target. A hybrid build carries the original assets and our scripts; the packages it hands the engine are
#    the writer's own assembled output, which is what the manifest proves. It does *not* compile: there is no `ucc`
#    (PLAN 3.6), so the script half is the merged source tree beside the packages, not packages made from it.
if [ "$target" = hybrid ]; then
    system="${SG1_DUMP:-$HOME/sg1/dump}/Stargate_SG-1_-_The_Alliance-2005-12-15/System"
    mkdir -p "$out/workdir/System"
    stage place-packages python3 tools/pkg-write.py "$system" --rebuild --write "$out/workdir/System"
    if [ -f "$system/Packages.md5" ]; then
        stage place-check-data sh -c "cp '$system/Packages.md5' '$out/workdir/System/Packages.md5'"
    else
        skip place-check-data "no Packages.md5 beside the packages"
    fi
    stage manifest python3 tools/build-manifest.py "$out/workdir/System" "$system" -o "$out"
    if [ -d "$out/source-tree" ]; then
        stage source-count sh -c "test \$(find '$out/source-tree' -name '*.uc' | wc -l) -eq 2319"
    else
        skip source-count "no source tree was written"
    fi
    skip compile "not a hybrid stage: a hybrid build places the original payloads (PLAN 6.1's remaining half)"
    skip repack "not a hybrid stage: nothing was generated to pack"
    skip boot "needs an HDD image built from these packages (tools/xemu/smoke.py exists: docs/XEMU.md)"
else
# 4. Compile. This is the stage the project cannot run yet, and the only one that needs a compiler.
if [ -n "${UCC:-}" ] && [ -x "${UCC:-}" ]; then
    if [ "$dry" = 1 ]; then
        skip compile "dry run: would invoke $UCC"
    else
        stage compile sh -c '"'"${UCC}"'" make -n >/dev/null'
    fi
else
    skip compile "no ucc matching package version 130 / licensee 4 (PLAN 3.6; docs/PATH.md calls this the critical path)"
fi

# 5. Repack and place. The writer can re-lay a package; it cannot invent a payload, so this is blocked on 4.
skip repack "needs compiled payloads (PLAN 6.1's remaining half; the container itself is proven by writer-relay)"

# 6. The package check. The route is decided (bypass, not regeneration: docs/PACKAGE_CHECK.md) but the bypass is
#    not implemented, so a repacked package would be rejected.
skip package-check "bypass decided, not implemented (docs/PACKAGE_CHECK.md)"

# 7. Boot. Needs a repacked HDD image, so it follows 4-6; the boot harness itself exists (tools/xemu/smoke.py).
skip boot "needs a repacked image (tools/xemu/smoke.py exists and boots the retail image: docs/XEMU.md)"
fi

say ""
say "stages that ran: $([ "$fail" = 0 ] && echo PASS || echo FAIL)"
say "RESULT $([ "$fail" = 0 ] && echo PASS || echo FAIL)"
say "log directory: $out"
exit "$fail"
