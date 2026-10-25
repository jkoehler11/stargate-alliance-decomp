#!/bin/sh
# Import default.xbe into Ghidra headless, carve the real XBE sections, auto-analyse, then name
# functions from the assert strings. Output: build/ghidra/sg1.gpr (project) + symbols/xbox_functions.csv.
#
# Usage: tools/ghidra/run-analysis.sh [DUMP_ROOT] [GHIDRA_HOME]
#   DUMP_ROOT   default: $SG1_DUMP (must contain Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe)
#   GHIDRA_HOME default: $GHIDRA_HOME, else ../ghidra/ghidra_*/support
set -eu
root="${1:-${SG1_DUMP:-}}"
[ -n "$root" ] || { echo "usage: $0 DUMP_ROOT [GHIDRA_HOME]" >&2; exit 2; }
game="$root/Stargate_SG-1_-_The_Alliance-2005-12-15"
[ -f "$game/default.xbe" ] || { echo "no default.xbe under $game" >&2; exit 2; }
home="${2:-${GHIDRA_HOME:-}}"
if [ -z "$home" ]; then
    # No GHIDRA_HOME given: take the newest ghidra_* under $HOME/tools, if there is one.
    home=$(ls -d "${HOME:-/root}"/tools/ghidra_* 2>/dev/null | sort -V | tail -1)
fi
headless="$home/support/analyzeHeadless"
# The container's filesystem is backed by the NAS and occasionally returns ENOENT for a file that
# is there a second later, so a single stat is not a reliable test on this host.
attempt=0
while [ ! -f "$headless" ] && [ "$attempt" -lt 10 ]; do
    attempt=$((attempt + 1))
    sleep 1
done
[ -f "$headless" ] || { echo "analyzeHeadless not found under '${home:-<unset>}' (set GHIDRA_HOME)" >&2; exit 2; }
[ -x "$headless" ] || { echo "analyzeHeadless is not executable: $headless" >&2; exit 2; }

# Ghidra 12.2 refuses JDK 21 and older; use JAVA_HOME when set, else the newest jdk-* under
# $HOME/tools (this container's install), and fail loudly rather than let Ghidra guess.
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

# The entry point comes from the header, through the same reader the rest of the tooling uses: `0x128`
# XOR the debug-build key. It used to be passed as `0x10184`, the *certificate* address, so every run
# seeded the analysis somewhere in the header region and the real CRT entry at 0x0033F317 was never
# analysed (docs/XBE_LAYOUT.md).
ENTRY=$(python3 -c "import sys; sys.path.insert(0, 'tools'); from xbe import Xbe; \
x = Xbe('$game/default.xbe'); print('0x%x' % x.entry) if x.entry is not None else sys.exit(1)") || {
    echo "cannot resolve the XBE entry point from '$game/default.xbe'; refusing to guess" >&2
    exit 2
}
echo "entry point: $ENTRY"

proj="$PWD/build/ghidra"
mkdir -p "$proj" symbols
python3 tools/ghidra/make-spec.py "$game/default.xbe" --out "$proj"

"$headless" "$proj" sg1 \
    -import "$game/default.xbe" \
    -processor "x86:LE:32:default" \
    -loader BinaryLoader \
    -loader-baseAddr 0x10000 \
    -scriptPath "$PWD/tools/ghidra/ghidra_scripts" \
    -preScript XbePre.java "$proj/sections.tsv" "$game/default.xbe" "$ENTRY" \
    -postScript SeedGaps.java "$proj/asserts.tsv" 0x1000 8 "$PWD/inventory/xbe_block_seeds.csv" \
        "$PWD/inventory/xbe_gap_seeds.csv" \
    -postScript XbePost.java "$proj/asserts.tsv" "$PWD/symbols/xbox_functions.csv" "$ENTRY" \
        "$PWD/symbols/xbe_overrides.csv,$PWD/symbols/xbe_dispatch.csv" \
    -postScript ExportEvidence.java "$proj/evidence" \
    -deleteProject
echo "symbols: symbols/xbox_functions.csv"
