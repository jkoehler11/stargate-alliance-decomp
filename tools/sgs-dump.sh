#!/bin/sh
# Dump the property-tag schema of every checkpoint package in the dump.
#
# A `.sgs` is an ordinary UE2 package, so `Sg1Tool save` reads it with the same library as the game
# packages. Output is one CSV per checkpoint (names, types, widths -- never values), which
# tools/sgs-schema.py then reduces to inventory/sgs_schema.csv.
#
# Usage: tools/sgs-dump.sh DUMP_ROOT OUT_DIR   (needs a Release build of tools/Sg1Tool)
set -eu
root="${1:?usage: $0 DUMP_ROOT OUT_DIR}"
out="${2:?usage: $0 DUMP_ROOT OUT_DIR}"
tool="${SG1_TOOL:-tools/Sg1Tool/bin/Release/net10.0/Sg1Tool.dll}"
dotnet="${DOTNET:-dotnet}"
game="$root/Stargate_SG-1_-_The_Alliance-2005-12-15"
[ -f "$tool" ] || { echo "Sg1Tool not built: $tool (dotnet build tools/Sg1Tool -c Release)" >&2; exit 2; }
mkdir -p "$out"
n=0
for f in "$game"/System/Profiles/*/*.sgs; do
    [ -f "$f" ] || continue
    base=$(basename "$f" .sgs)
    "$dotnet" "$tool" save --path "$f" --out "$out/$base.csv" >/dev/null
    n=$((n + 1))
done
echo "$n checkpoint packages -> $out"
