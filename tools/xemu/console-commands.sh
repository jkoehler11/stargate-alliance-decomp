#!/bin/sh
# Type serveral console commands in one boot and photograph each one's effect (PLAN 7.3 / U15).
#
# The console on the stock disc draws nothing (`Engine.Console` has no `PostRender`), so a command's *effect* is the
# only picture there is: `stat fps` puts a counter up and is the measured positive control, and every other probe in
# `docs/XEMU.md` read 0 px. `tools/console-commands.py` now says which of those names the build declares as `exec`
# at all, and that splits a zero into "no such command" and "ran, drew nothing" -- which is what this script
# measures, one submitted command at a time.
#
# Each command gets the measured sequence: `Tab` opens the console (one press -- it toggles), the first keypress is
# swallowed by `bIgnoreKeys` (`Console.uc`), the characters follow, `ret` submits and closes, and then two frames
# three seconds apart say whether the world still animates (`noise=`, the frame's own animation in pixels).
#
# Usage: console-commands.sh [options] OUTDIR
#   --iso PATH       disc image                (default $WORK/sg1.iso)
#   --bios DIR       directory with mcpx*.bin (default $SG1_DUMP/bios)
#   --hdd PATH       HDD image to copy         (default $WORK/hdd-val.qcow2)
#   --eeprom PATH    EEPROM to boot from       (default $WORK/xemu/smoke1/eeprom.bin)
#   --index N        map-list index to load    (default 10 = M00)
#   --boot SECS      title wait before the first press (default 165: under a loaded GPU the title fade ran past 135 s
#                    and the first `A` only moved the highlight)
#   --settle SECS    wait after the map select (default 60)
#   --cmd TEXT       a console command, repeatable (default `stat fps`, `behindview 1`, `showmenu`)
#   --no-dump        skip the whole-RAM dump at the end
#
# Characters are the ones a QEMU key name exists for (letters, digits, space, `. - _ /`); anything else is refused
# rather than silently skipped, because a dropped character reads as "the command did not exist".
set -eu
iso="${WORK:-$HOME/sg1/work}/xemu/sg1.iso"
bios="${SG1_BIOS:-$HOME/nfs/sg1-dump/bios}"
hdd=""
eeprom=""
index=10
boot=165
settle=60
dump=1
cmds=""
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --iso=*) iso=${1#*=}; shift ;;
    --bios=*) bios=${1#*=}; shift ;;
    --hdd=*) hdd=${1#*=}; shift ;;
    --eeprom=*) eeprom=${1#*=}; shift ;;
    --index=*) index=${1#*=}; shift ;;
    --boot=*) boot=${1#*=}; shift ;;
    --settle=*) settle=${1#*=}; shift ;;
    --cmd=*) cmds="$cmds|${1#*=}"; shift ;;
    --no-dump) dump=0; shift ;;
    --iso) iso="$2"; shift 2 ;;
    --bios) bios="$2"; shift 2 ;;
    --hdd) hdd="$2"; shift 2 ;;
    --eeprom) eeprom="$2"; shift 2 ;;
    --index) index="$2"; shift 2 ;;
    --boot) boot="$2"; shift 2 ;;
    --settle) settle="$2"; shift 2 ;;
    --cmd) cmds="$cmds|$2"; shift 2 ;;
    --*) echo "unknown option $1" >&2; exit 2 ;;
    *) if [ -z "$out" ]; then out="$1"; else echo "unknown argument $1" >&2; exit 2; fi; shift ;;
  esac
done
[ -n "$out" ] || { echo "usage: console-commands.sh [options] OUTDIR" >&2; exit 2; }
[ -n "$cmds" ] || cmds="|stat fps|behindview 1|showmenu"
[ -n "$hdd" ] || hdd="${WORK:-$HOME/sg1/work}/hdd-val.qcow2"
[ -n "$eeprom" ] || eeprom="${WORK:-$HOME/sg1/work}/xemu/smoke1/eeprom.bin"
[ -f "$hdd" ] || { echo "no HDD image at $hdd (pass --hdd): a run needs a copy it may write" >&2; exit 2; }
[ -f "$iso" ] || { echo "no disc image at $iso (pass --iso)" >&2; exit 2; }
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)

rm -rf "$out"; mkdir -p "$out"
out=$(cd "$out" && pwd)
cp --reflink=auto "$hdd" "$out/hdd.qcow2"
export DISPLAY="${DISPLAY:-:0}"
if [ -z "${XAUTHORITY:-}" ]; then
  XA=$(ls /run/user/1000/xauth_* 2>/dev/null | head -1) || true
  [ -n "$XA" ] && export XAUTHORITY="$XA"
fi
export APPIMAGE_EXTRACT_AND_RUN=1

args="--iso=$iso --bios=$bios --hdd=$out/hdd.qcow2 --out=$out --eeprom=$eeprom"
args="$args --fuzz=10 --seconds=75"
set -- "--step=monitor=device_add usb-kbd,port=1.3.2,id=kbd"
# 75 s for the window, then the rest of the title: the keyboard has to be in the guest before it enumerates `XID`,
# and no press lands until the menu has finished fading in.
set -- "$@" --step=wait=$((boot - 75)) --step=shot=00-title --step=noise=3
set -- "$@" --step=pad=a --step=wait=4 --step=shot=01-list
i=0
while [ "$i" -lt "$index" ]; do
  set -- "$@" --step=pad=down --step=wait=0.9
  i=$((i+1))
done
# `A` selects, `A` dismisses the intro card, the settle reaches gameplay, `A` dismisses the tutorial modal.
set -- "$@" --step=pad=a --step=wait=6 --step=pad=a --step=wait="$settle" --step=shot=02-mission
set -- "$@" --step=pad=a --step=wait=4 --step=shot=03-dismissed --step=noise=3

n=0
oldIFS=$IFS
IFS='|'
for cmd in $cmds; do
  IFS=$oldIFS
  [ -n "$cmd" ] || continue
  n=$((n+1))
  slug=$(printf '%s' "$cmd" | tr -c 'a-zA-Z0-9' '-')
  # A QEMU key name per character, and a refusal for anything the table cannot produce.
  keys=""
  rest=$cmd
  while [ -n "$rest" ]; do
    c=$(printf '%s' "$rest" | cut -c1)
    rest=$(printf '%s' "$rest" | cut -c2-)
    case "$c" in
      [a-zA-Z0-9]) k=$c ;;
      ' ') k=spc ;;
      .) k=dot ;;
      -) k=minus ;;
      _) k=shift-minus ;;
      /) k=slash ;;
      *) echo "cannot type '$c' as a guest key (letters, digits, space, . - _ / only): simplify --cmd" >&2; exit 2 ;;
    esac
    keys="$keys $k"
  done
  set -- "$@" --step=gkey=tab --step=wait=2 --step=shot=04$n-tab
  set -- "$@" --step=gkey=spc --step=wait=0.6
  for k in $keys; do
    set -- "$@" "--step=gkey=$k"
  done
  set -- "$@" --step=wait=1 --step=shot=05$n-typed
  set -- "$@" --step=gkey=ret --step=wait=3 --step=shot=06$n-$slug --step=noise=3
  IFS='|'
done
IFS=$oldIFS
set -- "$@" --step=cpu=2
if [ "$dump" = 1 ]; then
  set -- "$@" "--step=monitor=pmemsave 0 0x8000000 \"$out/ram.bin\""
fi

echo "console command run into $out: index $index, commands:$cmds"
( cd "$repo" && python3 tools/xemu/drive.py $args "$@" ) > "$out/run.log" 2>&1 || echo "EXIT $?" >> "$out/run.log"
tail -25 "$out/run.log"
rm -f "$out/hdd.qcow2"   # a boot copy is ~1 GB and is not an artifact
