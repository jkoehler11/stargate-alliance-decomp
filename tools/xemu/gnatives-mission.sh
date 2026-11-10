#!/bin/sh
# Boot to a loaded mission and dump the 16 KB `GNatives` table out of the guest over its GDB stub (U7).
#
# The first snapshot of the table was taken at the main menu (`$WORK/gnatives/runtime.bin`, 356 live slots);
# natives are registered with the class that declares them, so the open question is whether a loaded mission
# registers slots the front end never touches. This script produces the second snapshot and nothing else: the
# derived diff is `tools/gnatives-mission.py` / `make gnatives-mission`.
#
# Usage: gnatives-mission.sh [INDEX]        # INDEX is the map list's index (10 = M00, see map-index.py)
#   Environment: WORK (default $HOME/sg1/work), SG1_BIOS (default $HOME/nfs/sg1-dump/bios),
#                SG1_DUMP (the dump root; its Stargate_SG-1_-_The_Alliance-2005-12-15/ holds default.xbe)
#   GDB port 12377 is this script's own; one port per boot, so a second run never shares a stub.
#   SG1_BOOT (default 150) is the seconds before the first press. 135 is the measured minimum, and a boot whose
#   xdg/ shader cache is empty can still be fading there: measured 2026-11-10, a first press at 135 s moved 331
#   pixels (threshold 710) and the whole sequence then landed on the main menu -- the `A` it needed was swallowed.
#
# What it leaves behind, all under `$WORK/mission-gnatives/`, none of it committed (guest memory):
#   index-NN/gnatives.bin   the 16 KB dump (linear, not physical: see the --virtual note below)        index-NN/run.log      the harness's step log
#   index-NN/*.png          title/highlight/mission frames             index-NN/dump.txt     the gate's verdict
# It removes nothing: the HDD copy is ~1 GB, so delete `index-NN/` yourself once the diff is written
# (docs/XEMU.md's disk rule: thirty-three kept copies filled an 820 GB disk).
set -eu
index="${1:-10}"
W="${WORK:-$HOME/sg1/work}"
CL=$(cd "$(dirname "$0")/../.." && pwd)
DUMP="${SG1_DUMP:-$HOME/nfs/sg1-dump}"
BIOS="${SG1_BIOS:-$HOME/nfs/sg1-dump/bios}"
ISO="$W/xemu/sg1.iso"
HDD="$W/hdd-val.qcow2"
EEPROM="$W/xemu/smoke1/eeprom.bin"
OUT="$W/mission-gnatives/index-$index"
PORT=12377
[ -f "$ISO" ] || { echo "no disc at $ISO" >&2; exit 2; }
[ -f "$HDD" ] || { echo "no prepared HDD at $HDD" >&2; exit 2; }
[ -f "$EEPROM" ] || { echo "no profile EEPROM at $EEPROM (without one, Continue opens SELECT PROFILE)" >&2; exit 2; }
df -Pk "$W" | awk 'NR == 2 { exit ($4 < 1200000) }' || {
  echo "less than 1.2 GB free under $W: prune a boot first (docs/XEMU.md)" >&2; exit 2; }
export DISPLAY="${DISPLAY:-:0}"
if [ -z "${XAUTHORITY:-}" ]; then
  XA=$(ls /run/user/1000/xauth_* 2>/dev/null | head -1) || true
  [ -n "$XA" ] && export XAUTHORITY="$XA"
fi
export APPIMAGE_EXTRACT_AND_RUN=1
cd "$CL"
sh tools/xemu/clean-xemu.sh > /dev/null 2>&1 || true
[ "$(pgrep -xc AppRun || true)" = 0 ] || { echo "an emulator is still up (AppRun); refusing to share the image" >&2; exit 2; }
rm -rf "$OUT"; mkdir -p "$OUT"
cp --reflink=auto "$HDD" "$OUT/hdd.qcow2"
# `Continue` -> ten d-pad presses -> `A` selects M00; the presses are spaced because the guest coalesces two
# identical keys inside ~2 s (docs/XEMU.md).
set -- --step=shot=00-title --step=noise=3 --step=pad=a --step=wait=4
i=0
while [ "$i" -lt "$index" ]; do
  set -- "$@" --step=pad=down --step=wait=0.5
  i=$((i+1))
done
set -- "$@" --step=shot=01-highlight --step=pad=a --step=wait=60 --step=shot=02-mission --step=cpu=2
echo "start $(date)" > "$OUT/dump.txt"
python3 -u tools/xemu/drive.py --iso="$ISO" --bios="$BIOS" --hdd="$OUT/hdd.qcow2" --out="$OUT" \
  --eeprom="$EEPROM" --fuzz=10 --seconds="${SG1_BOOT:-150}" --crop=480x520+760+210 --pad-hold=0.35 \
  --gdb=$PORT --linger=1200 "$@" > "$OUT/run.log" 2>&1 &
pid=$!
n=0
while [ "$n" -lt 400 ]; do
  grep -q lingering "$OUT/run.log" 2>/dev/null && break
  kill -0 "$pid" 2>/dev/null || break
  sleep 2
  n=$((n+1))
done
if ! grep -q lingering "$OUT/run.log" 2>/dev/null; then
  echo "the sequence never reached its linger: read $OUT/run.log" >&2
  kill -9 "$pid" 2>/dev/null || true
  pkill -x AppRun 2>/dev/null || true
  exit 2
fi
# The frame gate comes before the read: a boot that stayed on the main menu still repaints, still hands out its
# stub, and its table would be the menu's again -- a second dump that proves nothing.
# The read is `--virtual`: the table is a `.data` address, and a physical read of the same number lands on a
# different frame -- measured 2026-11-10, where the array's first 3.9 KB of a physical window were the table and
# its last page was another frame's zeros (0x635000 is a page boundary 3,846 slots into the array).
python3 tools/xemu/frame-diff.py "$OUT/00-title.png" "$OUT/02-mission.png" >> "$OUT/dump.txt" 2>&1 \
  || echo "frame-diff rc=$?" >> "$OUT/dump.txt"
python3 tools/xemu/guest-halt.py "$OUT/02-mission.png" >> "$OUT/dump.txt" 2>&1 \
  || echo "guest-halt rc=$?" >> "$OUT/dump.txt"
if grep -qi "unchanged\|BIOS" "$OUT/dump.txt"; then
  echo "the post-load frame is not a loaded level -- see $OUT/dump.txt" >&2
  kill -9 "$pid" 2>/dev/null || true
  pkill -x AppRun 2>/dev/null || true
  exit 2
fi
python3 tools/xemu/mem.py --port $PORT --virtual --dump 0x6313e8:0x4000 -o "$OUT/gnatives.bin" >> "$OUT/dump.txt" 2>&1 \
  || echo "mem.py rc=$?" >> "$OUT/dump.txt"
ls -l "$OUT/gnatives.bin" >> "$OUT/dump.txt" 2>&1 || true
kill -9 "$pid" 2>/dev/null || true
pkill -x AppRun 2>/dev/null || true
sleep 2
echo "emu=$(pgrep -xc AppRun || true) pads=$(grep -c 'SG1 virtual pad' /proc/bus/input/devices || true)" >> "$OUT/dump.txt"
echo "done $(date)" >> "$OUT/dump.txt"
echo "dumped $OUT/gnatives.bin"
echo "next: make gnatives-mission   (SG1_DUMP=<dump root>)"
