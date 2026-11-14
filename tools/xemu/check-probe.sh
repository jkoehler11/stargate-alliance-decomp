#!/bin/sh
# Which path invokes the package check at runtime? (PLAN 6.2 / `Packages.md5`)
#
# The static half of 6.2 omits nothing: the database is a package, its records pair with files, the quick
# transform is reproduced and the file is re-emitted byte for byte. What it cannot say is *when* the game
# looks at it. This probe answers that with breakpoints instead of another read of the image:
#
#   * the three readers that carry the `Packages.md5` string -- `0x00121490`, `0x00121730`, `0x00125a90`,
#     the last of which parses the records;
#   * `0x00126860`, the `MD5` exec dispatcher the three sit behind, so a path that reaches the file by
#     another door is not missed;
#   * `0x00187350` `Engine_UnPackageCheckInfo`, the records class's own body in `Engine`;
#
# and it arms them for a whole boot: power-on -> title -> map list -> one mission loaded and settled, which
# is every load the game performs. `bp-args.py --control-self` supplies the witness that the arms landed --
# a window where the control fires and none of these does is a true negative, and one where the control does
# not fire says nothing at all.
#
# Measured 2026-11-14 (two boots): the control fired in both, `0x00187350` fired exactly twice in both --
# from `0x00013ec7`, inside `Core_UnObjBas` `0x00012620`, the first caller the static table lists -- and the
# three readers and the dispatcher fired **zero** times, including across the loaded `M00`. What runs at boot
# is a constructor: the body allocates `0x520` bytes (`push 0x520`; `call [eax]` through `0x005bf510`) and
# calls `0x0003af60` on it, asserting through `PackageCheckInfo.cpp` when that returns null. So the class is
# built and its file is never opened.
#
# Usage: check-probe.sh [--index N] [--port P] [--seconds S] [--settle S] [--hdd P] [--eeprom P] [--out DIR]
#   --index N     map-list index to load (default 10 = M00; `tools/xemu/map-index.py --name` resolves a name)
#   --port P      GDB stub port (default 12421; one port per boot, so two runs never share it)
#   --seconds S   boot window before the first press (default 120)
#   --settle S    seconds after the map select (default 60)
#   --hdd P       HDD image to copy per run (default $WORK/checkprobe/hdd.qcow2)
#   --eeprom P    EEPROM with a profile (default $WORK/checkprobe/eeprom.bin)
#   --iso P       disc image (default $WORK/xemu/sg1.iso)
#   --out DIR     output directory (default $WORK/checkprobe)
#
# The disc image is not built here: `tools/xemu/build-xiso.sh DUMP $WORK/xemu/sg1.iso` makes one, and a
# missing `--iso` is refused by name rather than booted as xemu's own "Failed to open DVD image file" dialog.
set -eu
W=${WORK:-$HOME/sg1/work}
repo=$(cd "$(dirname "$0")/../.." && pwd)
index=10
port=12421
secs=120
settle=60
hdd="$W/checkprobe/hdd.qcow2"
eeprom="$W/checkprobe/eeprom.bin"
iso="$W/xemu/sg1.iso"
out="$W/checkprobe"
bios=${SG1_BIOS:-$HOME/nfs/sg1-dump/bios}

while [ $# -gt 0 ]; do
  case "$1" in
    --index=*) index=${1#*=}; shift ;;
    --port=*) port=${1#*=}; shift ;;
    --seconds=*) secs=${1#*=}; shift ;;
    --settle=*) settle=${1#*=}; shift ;;
    --hdd=*) hdd=${1#*=}; shift ;;
    --eeprom=*) eeprom=${1#*=}; shift ;;
    --iso=*) iso=${1#*=}; shift ;;
    --out=*) out=${1#*=}; shift ;;
    --index) index=$2; shift 2 ;;
    --port) port=$2; shift 2 ;;
    --seconds) secs=$2; shift 2 ;;
    --settle) settle=$2; shift 2 ;;
    --hdd) hdd=$2; shift 2 ;;
    --eeprom) eeprom=$2; shift 2 ;;
    --iso) iso=$2; shift 2 ;;
    --out) out=$2; shift 2 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done

for f in "$hdd" "$eeprom" "$iso"; do
  [ -e "$f" ] || { echo "missing $f (see the usage block)" >&2; exit 2; }
done

# A cron shell sets neither, and drive.py needs both to reach the window.
DISPLAY=${DISPLAY:-:0}
XAUTHORITY=${XAUTHORITY:-$(ls /run/user/1000/xauth_* 2>/dev/null | head -1)}
APPIMAGE_EXTRACT_AND_RUN=1
export DISPLAY XAUTHORITY APPIMAGE_EXTRACT_AND_RUN

dir="$out/run-$index"
rm -rf "$dir"; mkdir -p "$dir"
cp --reflink=auto "$hdd" "$dir/hdd.qcow2"
echo "index $index  port $port  hdd $(sha256sum "$dir/hdd.qcow2" | cut -c1-16)  eeprom $(sha256sum "$eeprom" | cut -c1-16)"

set -- --step=shot=00-title --step=noise=3 --step=pad=a --step=wait=4
n=0
while [ $n -lt "$index" ]; do set -- "$@" --step=pad=down --step=wait=0.5; n=$((n+1)); done
# Two `a` taps: a press swallowed by a fading menu is the failure this project keeps hitting, and the second
# one costs 25 s while a retry costs a whole boot.
set -- "$@" --step=shot=01-highlight --step=pad=a --step=wait=25 --step=pad=a --step=wait="$settle" \
       --step=shot=02-mission --step=cpu=2

python3 -u "$repo/tools/xemu/drive.py" --iso="$iso" --bios="$bios" --hdd="$dir/hdd.qcow2" \
  --out="$dir" --eeprom="$eeprom" --gdb="$port" --seconds="$secs" --fuzz=15 --pad-hold=0.35 "$@" \
  > "$out/run-$index.log" 2>&1 &
drv=$!

# The guest first: drive.py launches xemu, and the stub exists only once it is listening.
i=0
while [ $i -lt 120 ]; do
  if (exec 3<>/dev/tcp/127.0.0.1/"$port") 2>/dev/null; then exec 3>&-; break; fi
  i=$((i+1)); sleep 1
done
echo "stub port $port open after ${i}s"

python3 -u "$repo/tools/xemu/bp-args.py" --port "$port" --wait-user 180 --control-self \
  --break 0x121490 --break 0x121730 --break 0x125a90 --break 0x126860 --break 0x187350 \
  --stops 5 --timeout 60 --window 300 --json "$out/bp-$index.json" > "$out/bp-$index.log" 2>&1 \
  || echo "bp-args exit $?"
wait "$drv" || echo "drive exit $?"
pkill -x AppRun 2>/dev/null || true
rm -f "$dir/hdd.qcow2"
echo "record: $out/bp-$index.json  frames: $dir"
