#!/bin/sh
# Decompile one module's attributed address list in one Ghidra round.
#
# A module is a scattered set of addresses (`tools/module-split.py --lists`), not a range, and
# `ExportAt.java` takes the whole @file in a single invocation. So the pass is ONE import of
# default.xbe plus ONE `-process` round per module list -- not one invocation per address. This
# driver does exactly that and times every round, so the cost of the pass is measured instead of
# estimated. Output is game-derived and stays under $(WORK); only the round summary may be committed.
#
# Usage: tools/ghidra/export-module.sh DUMP_ROOT GHIDRA_HOME <module|listfile>[,more...]
#   Writes $(WORK)/module-export/<name>/<address>_<name>.c and one row per round in
#   $(WORK)/module-export/rounds.csv (module,addresses,seconds,exported,inside,missing,unnamed,
#   decompile_failed,c_bytes) -- the summary is the committable half; the .c files are not.
#   The project is kept at build/ghidra-module: reuse it for the next round or module, and delete
#   that directory to force a fresh analysis.
set -eu
root="${1:?usage: $0 DUMP_ROOT GHIDRA_HOME module[,more...]}"
home="${2:?usage: $0 DUMP_ROOT GHIDRA_HOME module[,more...]}"
modules="${3:?usage: $0 DUMP_ROOT GHIDRA_HOME module[,more...]}"
game="$root/Stargate_SG-1_-_The_Alliance-2005-12-15"
[ -f "$game/default.xbe" ] || { echo "no default.xbe under $game" >&2; exit 2; }
headless="$home/support/analyzeHeadless"
[ -x "$headless" ] || { echo "analyzeHeadless not found under $home" >&2; exit 2; }
if [ -z "${JAVA_HOME:-}" ]; then
    for d in "${HOME:-/root}"/tools/jdk-*; do
        [ -x "$d/bin/java" ] && JAVA_HOME="$d"
    done
fi
if [ -n "${JAVA_HOME:-}" ]; then
    export JAVA_HOME
    PATH="$JAVA_HOME/bin:$PATH"
    export PATH
fi

work="${WORK:-$HOME/sg1/work}"
proj="$PWD/build/ghidra-module"
outroot="$work/module-export"
mkdir -p "$proj" "$outroot" symbols
summary="$outroot/rounds.csv"
[ -f "$summary" ] || echo "module,addresses,seconds,exported,inside,missing,unnamed,decompile_failed,c_bytes" > "$summary"

if [ ! -f "$proj/sg1.gpr" ]; then
    entry=$(python3 -c "import sys; sys.path.insert(0, 'tools'); from xbe import Xbe; \
x = Xbe('$game/default.xbe'); print('0x%x' % x.entry) if x.entry is not None else sys.exit(1)") || {
        echo "cannot resolve the XBE entry point; refusing to guess" >&2
        exit 2
    }
    echo "analysis: importing default.xbe (entry $entry) into $proj"
    python3 tools/ghidra/make-spec.py "$game/default.xbe" --out "$proj"
    # Same recipe as tools/ghidra/run-analysis.sh, minus -deleteProject: the project is the pass's
    # reusable half, and re-analysing per module would pay the import once per module.
    "$headless" "$proj" sg1 \
        -import "$game/default.xbe" \
        -processor "x86:LE:32:default" \
        -loader BinaryLoader \
        -loader-baseAddr 0x10000 \
        -scriptPath "$PWD/tools/ghidra/ghidra_scripts" \
        -preScript XbePre.java "$proj/sections.tsv" "$game/default.xbe" "$entry" \
        -postScript SeedGaps.java "$proj/asserts.tsv" 0x1000 8 "$PWD/inventory/xbe_block_seeds.csv" \
            "$PWD/inventory/xbe_gap_seeds.csv" \
        -postScript XbePost.java "$proj/asserts.tsv" "$PWD/symbols/xbox_functions.csv" "$entry" \
            "$PWD/symbols/xbe_overrides.csv,$PWD/symbols/xbe_dispatch.csv" \
        || { echo "analysis failed" >&2; exit 2; }
else
    echo "analysis: reusing $proj/sg1.gpr"
fi

for m in $(echo "$modules" | tr ',' ' '); do
    case "$m" in
        */*) list="$m"; name=$(basename "$m" .txt) ;;
        *)   list="$work/module-lists/$m.txt"; name="$m" ;;
    esac
    [ -s "$list" ] || { echo "no address list for '$m' ($list) -- run: make module-lists" >&2; exit 2; }
    out="$outroot/$name"
    mkdir -p "$out"
    log="$outroot/$name.log"
    addresses=$(grep -c . "$list" || true)
    t0=$(date +%s)
    "$headless" "$proj" sg1 -process default.xbe -noanalysis \
        -scriptPath "$PWD/tools/ghidra/ghidra_scripts" \
        -postScript ExportAt.java "@$list" "$out" "$PWD/symbols/xbox_functions.csv" > "$log" 2>&1 \
        || { echo "round for $name failed, see $log" >&2; tail -5 "$log" >&2; exit 2; }
    t1=$(date +%s)
    # Headless prefixes script output with "INFO  ExportAt.java> ", so match the message, not the line.
    exported=$(grep -c 'ExportAt.java> wrote ' "$log" || true)
    inside=$(grep -c 'ExportAt.java> .*: inside ' "$log" || true)
    missing=$(grep -c 'ExportAt.java> .*: no function at this address' "$log" || true)
    # Still-nameless bodies and bodies whose decompile aborted are the pass's two residues, so the row
    # carries both: a round that exports everything but decompiles nothing would otherwise look clean.
    unnamed=$(ls "$out"/[0-9]*_FUN_*.c 2>/dev/null | wc -l || true)
    failed=$(grep -l 'decompile failed' "$out"/*.c 2>/dev/null | wc -l || true)
    bytes=$(cat "$out"/*.c 2>/dev/null | wc -c || true)
    echo "$name,$addresses,$((t1 - t0)),$exported,$inside,$missing,$unnamed,$failed,$bytes" >> "$summary"
    echo "$name: $exported exported, $inside inside another function, $missing missing of $addresses in $((t1 - t0))s -> $out"
done
echo "summary: $summary"
