#!/bin/sh
# Decompile one module's functions to C (game-derived: stays under work/, never committed).
#
# Usage: tools/ghidra/export-c.sh DUMP_ROOT GHIDRA_HOME [PREFIX[,PREFIX]]
#   Writes $(WORK)/native/<prefix>/<address>_<name>.c plus an index CSV next to it.
set -eu
root="${1:?usage: $0 DUMP_ROOT GHIDRA_HOME [PREFIXES]}"
home="${2:?usage: $0 DUMP_ROOT GHIDRA_HOME [PREFIXES]}"
prefixes="${3:-stargategame_,stargatevehicles_}"
game="$root/Stargate_SG-1_-_The_Alliance-2005-12-15"
headless="$home/support/analyzeHeadless"
[ -x "$headless" ] || { echo "analyzeHeadless not found under $home" >&2; exit 2; }
[ -f "$game/default.xbe" ] || { echo "no default.xbe under $game" >&2; exit 2; }

work="${WORK:-$HOME/sg1/work}/native"
mkdir -p "$work"
proj="$PWD/build/ghidra-export"
mkdir -p "$proj"

"$headless" "$proj" export \
    -import "$game/default.xbe" \
    -processor "x86:LE:32:default" \
    -loader BinaryLoader \
    -loader-baseAddr 0x10000 \
    -scriptPath "$PWD/tools/ghidra/ghidra_scripts" \
    -preScript XbePre.java "$PWD/build/ghidra/sections.tsv" "$game/default.xbe" 0x10184 \
    -postScript ExportC.java "$PWD/symbols/xbox_functions.csv" "$prefixes" "$work" \
        "$work/functions.csv" \
    -deleteProject
echo "C: $work"
