#!/bin/sh
# Which shader programs each mission compiles (PLAN 6 / docs/NATIVE_NOTES.md).
#
# Boots one mission per index with the GDB stub armed, breaks on `_XGAssembleShader@44` (0x004857d0), and
# records the stops. The point is the *union*: the mission table (docs/MAP_LOADS.md) knows which indices load,
# and this says which of the 60 programs each load compiles.
#
#   tools/xemu/shader-union.sh OUTDIR 10 15 20 ...
#
# Per-index output is $OUTDIR/<index>.json plus a log; `tools/xemu/shader-union.sh --summary OUTDIR` folds the
# JSONs into a per-program table. One boot at a time: the HDD image takes a write lock.
set -u

REPO=$(cd "$(dirname "$0")/../.." && pwd)
DRIVE="$REPO/tools/xemu/drive.py"
BP="$REPO/tools/xemu/bp-args.py"
ISO=${SG1_ISO:-$HOME/sg1/work/sg1.iso}
BIOS=${SG1_BIOS:-$HOME/nfs/sg1-dump/bios}
HDD=${SG1_HDD:-$HOME/nfs/sg1-dump/bios/xbox_hdd.qcow2}
PORT=12345
ASSEMBLER=0x004857d0

summary() {
    out=$1
    python3 - "$out" <<'PY'
import csv, glob, json, os, sys
out = sys.argv[1]
progs = {int(r["address"], 16): r["version"]
         for r in csv.DictReader(open(os.path.join(os.environ.get("SG1_REPO", "."), "inventory", "shader_programs.csv")))}
rows = []
for path in sorted(glob.glob(os.path.join(out, "*.json"))):
    idx = os.path.basename(path)[:-5]
    d = json.load(open(path))
    hit = set()
    for s in d.get("stops", []):
        for w in s.get("stack", []):
            v = int(w, 16)
            if v in progs:
                hit.add(v)
        v = s.get("regs", {}).get("edx")
        if isinstance(v, str) and int(v, 16) in progs:
            hit.add(int(v, 16))
    rows.append((idx, hit))
    print("index %-4s %2d stops -> %d programs" % (idx, len(d.get("stops", [])), len(hit)))
union = set().union(*[h for _, h in rows]) if rows else set()
print("union over %d boot(s): %d programs" % (len(rows), len(union)))
for va in sorted(union):
    seen = [i for i, h in rows if va in h]
    print("  %#010x %-8s %s" % (va, progs[va], ",".join(seen)))
PY
}

if [ "${1:-}" = "--summary" ]; then
    SG1_REPO="$REPO" summary "$2"
    exit 0
fi

OUT=${1:?usage: shader-union.sh OUTDIR INDEX [INDEX ...] | --summary OUTDIR}
shift
[ $# -gt 0 ] || { echo "no indices given"; exit 2; }
mkdir -p "$OUT"

XA=$(ls -t /run/user/1000/xauth_* 2>/dev/null | head -1)
export XDG_RUNTIME_DIR=/run/user/1000 DISPLAY=:0 XAUTHORITY="$XA" APPIMAGE_EXTRACT_AND_RUN=1
unset WAYLAND_DISPLAY

for idx in "$@"; do
    [ -f "$OUT/$idx.json" ] && { echo "index $idx: already recorded"; continue; }
    echo "== index $idx =="
    pids=$(pgrep -f "appimage_extract[e]d|qemu-system-[xi]")
    [ -n "$pids" ] && kill $pids 2>/dev/null
    sleep 3
    steps="--step=wait=150"
    i=0
    while [ $i -lt "$idx" ]; do steps="$steps --step=pad=down"; i=$((i+1)); done
    steps="$steps --step=pad=a --step=wait=240 --step=cpu=20"
    rm -rf "$OUT/drive-$idx"
    ( python3 "$DRIVE" --iso="$ISO" --bios="$BIOS" --hdd="$HDD" --out="$OUT/drive-$idx" \
        --gdb $PORT $steps > "$OUT/$idx.log" 2>&1 & )
    ok=0
    for _ in $(seq 40); do
        sleep 2
        ss -ltn 2>/dev/null | grep -q $PORT && { ok=1; break; }
    done
    if [ $ok -ne 1 ]; then
        echo "index $idx: no stub (see $OUT/$idx.log)"
        continue
    fi
    sleep 20
    python3 "$BP" --port $PORT --wait-user 300 --sample-interval 0.5 --break $ASSEMBLER \
        --stops 40 --window ${SG1_WINDOW:-640} --stack 0x60 --json "$OUT/$idx.json" > "$OUT/$idx.bp.log" 2>&1
    tail -2 "$OUT/$idx.bp.log"
    pids=$(pgrep -f "appimage_extract[e]d|qemu-system-[xi]")
    [ -n "$pids" ] && kill $pids 2>/dev/null
    sleep 2
done
echo "done; summarise with: $0 --summary $OUT"
