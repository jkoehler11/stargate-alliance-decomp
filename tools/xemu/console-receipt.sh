#!/bin/sh
# Read a console submit on both sides of the key, in one boot (PLAN 7.3 / U15).
#
# `ret` is the only submit key this build honours, and on the stock disc the console has no window: so the
# receipt is a memory read plus a frame. This runs the measured sequence -- keyboard before the title, `M00`,
# the tutorial modal dismissed, one `Tab`, a dummy `spc` (the open swallows the first press), the command typed
# one key at a time -- takes a whole-RAM dump while the line is still unsubmitted, sends `ret`, takes the frame
# one second later, and dumps again. Then it prints the console state of both dumps, so the control and the
# receipt come from the same object in the same boot.
#
#   tools/xemu/console-receipt.sh OUTDIR [COMMAND]     (default COMMAND: hist)
#
# Environment: WORK (default ~/sg1/work), SG1_BIOS (default ~/nfs/sg1-dump/bios), DISPLAY/XAUTHORITY.
# Options via env: SG1_ISO, SG1_HDD, SG1_EEPROM, SG1_GDB (default 12390), SG1_KEEP=1 to keep the dumps,
# SG1_DRY_RUN=1 to print the key list and steps without booting.
#
# Exit 0 only when the post dump carries the receipt and the pre dump does not. The dumps are 128 MiB each and
# are removed unless SG1_KEEP=1; the frames stay.
set -eu
OUT=${1:?usage: console-receipt.sh OUTDIR [COMMAND]}
CMD=${2:-hist}
WORK=${WORK:-$HOME/sg1/work}
ISO=${SG1_ISO:-$WORK/xemu/sg1.iso}
HDD=${SG1_HDD:-$WORK/hdd-val.qcow2}
EEPROM=${SG1_EEPROM:-$WORK/xemu/smoke1/eeprom.bin}
BIOS=${SG1_BIOS:-$HOME/nfs/sg1-dump/bios}
GDB=${SG1_GDB:-12390}
HERE=$(cd "$(dirname "$0")/../.." && pwd)

# A `gkey` step takes ONE key name, so the command has to arrive key by key: `for k in hist` is a single step
# named `hist`, which drive.py refuses, and the run then types nothing at all. `SG1_DRY_RUN=1` prints the key
# list and the steps without a boot, which is the only way to check this half without four minutes of xemu.
KEYS=$(python3 - "$CMD" <<'PY'
import sys
print(" ".join("spc" if c == " " else c for c in sys.argv[1]))
PY
)
if [ "${SG1_DRY_RUN:-0}" = 1 ]; then
  echo "command: $CMD"
  echo "keys:    $KEYS"
  printf 'steps:'
  for k in $KEYS; do printf ' gkey=%s' "$k"; done
  echo
  exit 0
fi

[ -f "$ISO" ] || { echo "no iso at $ISO" >&2; exit 2; }
[ -f "$HDD" ] || { echo "no hdd at $HDD" >&2; exit 2; }
[ -f "$EEPROM" ] || { echo "no eeprom at $EEPROM (no profile: Continue opens SELECT PROFILE)" >&2; exit 2; }
free_kb=$(df -Pk "$(dirname "$OUT")" | awk 'NR==2 {print $4}')
[ "$free_kb" -gt 2500000 ] || { echo "under 2.5 GB free next to $OUT" >&2; exit 2; }

mkdir -p "$OUT"
cp --reflink=auto "$HDD" "$OUT/hdd.qcow2"
export DISPLAY=${DISPLAY:-:0}
if [ -z "${XAUTHORITY:-}" ]; then
  XAUTHORITY=$(ls /run/user/1000/xauth_* 2>/dev/null | head -1) && export XAUTHORITY
fi
export APPIMAGE_EXTRACT_AND_RUN=1

set -- --iso="$ISO" --bios="$BIOS" --hdd="$OUT/hdd.qcow2" --out="$OUT" --eeprom="$EEPROM" \
       --fuzz=10 --seconds=75 --gdb="$GDB"
set -- "$@" "--step=monitor=device_add usb-kbd,port=1.3.2,id=kbd"
set -- "$@" --step=wait=62 --step=shot=00-title --step=noise=3
set -- "$@" --step=pad=a --step=wait=4 --step=shot=01-list
i=0
while [ $i -lt 10 ]; do                       # ten d-pad presses is M00 (map-index.py --name M00)
  set -- "$@" --step=pad=down --step=wait=0.9
  i=$((i+1))
done
set -- "$@" --step=pad=a --step=wait=6 --step=pad=a --step=wait=70 --step=shot=02-mission
set -- "$@" --step=pad=a --step=wait=4 --step=shot=03-dismissed

# one Tab opens the console; the dummy key spends bIgnoreKeys; then the command, one key per step
set -- "$@" --step=gkey=tab --step=wait=2 --step=shot=04-tab
set -- "$@" --step=gkey=spc --step=wait=0.6
for k in $KEYS; do
  set -- "$@" --step=gkey="$k"
done
set -- "$@" --step=wait=1 --step=shot=05-typed
set -- "$@" --step="monitor=info registers"
set -- "$@" "--step=monitor=pmemsave 0 0x8000000 \"$OUT/pre.bin\""

# the submit, and the frame one second later: a 2 s subtitle would still be up, so a blank frame is a reading
set -- "$@" --step=gkey=ret --step=wait=1 --step=shot=06-ret1s
set -- "$@" --step=wait=3 --step=shot=07-ret4s
set -- "$@" --step="monitor=info registers"
set -- "$@" "--step=monitor=pmemsave 0 0x8000000 \"$OUT/post.bin\""

python3 "$HERE/tools/xemu/drive.py" "$@" > "$OUT/run.log" 2>&1 || echo "EXIT $?" >> "$OUT/run.log"
rm -f "$OUT/hdd.qcow2"

CR3=$(grep -o 'CR3=[0-9a-fA-F]*' "$OUT/run.log" | tail -1 | cut -d= -f2)
[ -n "$CR3" ] || { echo "no CR3 in $OUT/run.log; the boot did not reach a step" >&2; exit 2; }
echo "== control: $OUT/pre.bin (line typed, not submitted)"
python3 "$HERE/tools/xemu/console-state.py" "$OUT/pre.bin" --cr3 "0x$CR3" --history --state
echo "== receipt: $OUT/post.bin (3 s after ret)"
python3 "$HERE/tools/xemu/console-state.py" "$OUT/post.bin" --cr3 "0x$CR3" --history --state --receipt "$CMD"
rc=$?
[ "${SG1_KEEP:-0}" = 1 ] || rm -f "$OUT/pre.bin" "$OUT/post.bin"
exit $rc
