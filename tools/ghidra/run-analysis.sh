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
headless="$home/support/analyzeHeadless"
[ -x "$headless" ] || { echo "analyzeHeadless not found (set GHIDRA_HOME)" >&2; exit 2; }

proj="$PWD/build/ghidra"
mkdir -p "$proj" symbols
python3 tools/ghidra/make-spec.py "$game/default.xbe" --out "$proj"

"$headless" "$proj" sg1 \
    -import "$game/default.xbe" \
    -processor "x86:LE:32:default" \
    -loader BinaryLoader \
    -loader-baseAddr 0x10000 \
    -scriptPath "$PWD/tools/ghidra/ghidra_scripts" \
    -preScript XbePre.java "$proj/sections.tsv" "$game/default.xbe" 0x10184 \
    -postScript XbePost.java "$proj/asserts.tsv" "$PWD/symbols/xbox_functions.csv" 0x10184 \
        "$PWD/symbols/xbe_overrides.csv" \
    -postScript ExportEvidence.java "$proj/evidence" \
    -deleteProject
echo "symbols: symbols/xbox_functions.csv"
