#!/bin/sh
# Type a command into the Unreal console and try submit keys, one frame per candidate (PLAN 7.3).
#
# The console opens, takes characters and draws its input line, but a Return does not submit what was typed
# (`docs/XEMU.md`, measured 2026-11-06). A frame cannot settle it: "the command ran and printed nothing" and "the
# key never arrived" are the same picture on a disc whose configured console class has no `PostRender`. This
# script therefore does two things -- it produces the sequence (so the boot is not hand-built each time) and it
# leaves a GDB stub up after the last frame, because the *answer* is the console object's own state:
#
#     objects.py --port 12351 --xbe $SG1_DUMP/default.xbe --walk 0x6c21b0 --class Console --fstrings
#
# `TypedStr` still holding the text means no submit; `History` holding it means the command was submitted and
# `ConsoleCommand` ran; `bTyping` says whether the console is still in its typing state. Read the guest, then the
# frame -- the frames are for the drawing question, not the delivery one.
#
# Usage: console-submit.sh [options] OUTDIR
#   --iso PATH       disc image                (default $WORK/sg1.iso)
#   --bios DIR       directory with mcpx*.bin (default $SG1_DUMP/bios)
#   --hdd PATH       HDD image to copy        (default $WORK/probe/hdd.qcow2)
#   --eeprom PATH    EEPROM to boot from      (default $WORK/smoke1/eeprom.bin)
#   --index N        map-list index to load   (default 10 = M00, measured by census; see planets below)
#   --type TEXT      text to type after `Tab` (default "stat fps")
#   --submit SPEC    a candidate, `gkey=NAME` or `pad=BUTTON`, repeatable and tried in the order given
#                    (default `gkey=kp_enter`, `gkey=ret`, `pad=start`)
#   --boot SECS      title wait before the first press (default 135)
#   --settle SECS    wait after the map select  (default 55)
#   --gdb PORT       leave a GDB stub on PORT   (default 12351)
#   --linger SECS    how long the stub stays up (default 1500)
#   --no-linger      exit after the last frame instead of keeping the guest (a submit test needs the read)
#
# Facts this encodes, each measured (docs/XEMU.md): the keyboard must be attached at 75 s, before the guest
# enumerates `XID` -- one attached after the level loads is never read; `Tab` toggles the console, so after a
# dismissed modal it is one press; the first keypress after a load is swallowed, so the type comes after the
# `Tab` frame; a level load needs an `A` for the intro card and an `A` for the tutorial modal; and `pad=start`
# goes last, because Start is the pause key and everything after it lands in a paused game.
set -eu
iso="${WORK:-$HOME/.local/share/xemu/sg1}/sg1.iso"
bios="${SG1_BIOS:-$HOME/nfs/sg1-dump/bios}"
hdd=""
eeprom=""
index=10
text="stat fps"
submit=""
boot=135
settle=55
port=12351
linger=1500
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --iso=*) iso=${1#*=}; shift ;;
    --bios=*) bios=${1#*=}; shift ;;
    --hdd=*) hdd=${1#*=}; shift ;;
    --eeprom=*) eeprom=${1#*=}; shift ;;
    --index=*) index=${1#*=}; shift ;;
    --type=*) text=${1#*=}; shift ;;
    --submit=*) submit="$submit ${1#*=}"; shift ;;
    --boot=*) boot=${1#*=}; shift ;;
    --settle=*) settle=${1#*=}; shift ;;
    --gdb=*) port=${1#*=}; shift ;;
    --linger=*) linger=${1#*=}; shift ;;
    --no-linger) linger=0; shift ;;
    --iso) iso="$2"; shift 2 ;;
    --bios) bios="$2"; shift 2 ;;
    --hdd) hdd="$2"; shift 2 ;;
    --eeprom) eeprom="$2"; shift 2 ;;
    --index) index="$2"; shift 2 ;;
    --type) text="$2"; shift 2 ;;
    --submit) submit="$submit $2"; shift 2 ;;
    --boot) boot="$2"; shift 2 ;;
    --settle) settle="$2"; shift 2 ;;
    --gdb) port="$2"; shift 2 ;;
    --linger) linger="$2"; shift 2 ;;
    --*) echo "unknown option $1" >&2; exit 2 ;;
    *) if [ -z "$out" ]; then out="$1"; else echo "unknown argument $1" >&2; exit 2; fi; shift ;;
  esac
done
[ -n "$out" ] || { echo "usage: console-submit.sh [options] OUTDIR" >&2; exit 2; }
[ -n "$submit" ] || submit=" gkey=kp_enter gkey=ret pad=start"
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
[ -n "$hdd" ] || hdd="${WORK:-$HOME/.local/share/xemu/sg1}/probe/hdd.qcow2"
[ -n "$eeprom" ] || eeprom="${WORK:-$HOME/.local/share/xemu/sg1}/smoke1/eeprom.bin"
[ -f "$hdd" ] || { echo "no HDD image at $hdd (pass --hdd): a run needs a copy it may write" >&2; exit 2; }
[ -f "$iso" ] || { echo "no disc image at $iso (pass --iso)" >&2; exit 2; }

# A QEMU key name per character. The guest keyboard delivers characters into `TypedStr`; it is the *key codes*
# a submit needs, which is exactly what this table can and cannot produce.
keys=""
rest=$text
while [ -n "$rest" ]; do
  c=$(printf '%s' "$rest" | cut -c1)
  rest=$(printf '%s' "$rest" | cut -c2-)
  case "$c" in
    [a-z]) k=$c ;;
    [0-9]) k=$c ;;
    ' ') k=spc ;;
    .) k=dot ;;
    -) k=minus ;;
    _) k=shift-minus ;;
    /) k=slash ;;
    *) echo "cannot type '$c' as a guest key (letters, digits, space, . - _ / only): simplify --type" >&2; exit 2 ;;
  esac
  keys="$keys $k"
done

rm -rf "$out"; mkdir -p "$out"
cp --reflink=auto "$hdd" "$out/hdd.qcow2"
export DISPLAY="${DISPLAY:-:0}"
if [ -z "${XAUTHORITY:-}" ]; then
  XA=$(ls /run/user/1000/xauth_* 2>/dev/null | head -1) || true
  [ -n "$XA" ] && export XAUTHORITY="$XA"
fi
export APPIMAGE_EXTRACT_AND_RUN=1

args="--iso=$iso --bios=$bios --hdd=$out/hdd.qcow2 --out=$out --eeprom=$eeprom"
args="$args --fuzz=10 --seconds=75 --gdb=$port --linger=$linger"
set -- "--step=monitor=device_add usb-kbd,port=1.3.2,id=kbd"
# 75 s for the window, 62 s more for the title: the keyboard has to be in the guest before it enumerates `XID`,
# and the first press cannot land until the menu has finished fading in.
set -- "$@" --step=wait=$((boot - 75)) --step=shot=00-title --step=noise=3
set -- "$@" --step=pad=a --step=wait=4 --step=shot=01-list
i=0
while [ "$i" -lt "$index" ]; do
  set -- "$@" --step=pad=down --step=wait=0.9
  i=$((i+1))
done
# `A` selects, `A` dismisses the intro card (it appears a few seconds after the load), the settle reaches
# gameplay, and `A` dismisses the tutorial modal that holds input until it is answered.
set -- "$@" --step=pad=a --step=wait=6 --step=pad=a --step=wait=$settle --step=shot=02-mission
set -- "$@" --step=pad=a --step=wait=4 --step=shot=03-dismissed
set -- "$@" --step=gkey=tab --step=wait=2 --step=shot=04-tab
for k in $keys; do
  set -- "$@" "--step=gkey=$k"
done
set -- "$@" --step=wait=1 --step=shot=05-typed
n=0
for s in $submit; do
  case "$s" in
    gkey=*|pad=*) ;;
    *) echo "--submit $s: expected gkey=NAME or pad=BUTTON" >&2; exit 2 ;;
  esac
  n=$((n + 1))
  set -- "$@" "--step=$s" --step=wait=3 "--step=shot=0$((5 + n))-$(printf '%s' "${s#*=}" | tr -c 'a-zA-Z0-9' '-')"
done
set -- "$@" --step=cpu=2

echo "console run into $out: index $index, typing '$text', submit candidates:$submit"
( cd "$repo" && python3 tools/xemu/drive.py $args "$@" ) > "$out/run.log" 2>&1 || rc=$?
rc=${rc:-0}
tail -20 "$out/run.log"
if [ "$linger" != 0 ] && [ "$rc" = 0 ]; then
  cat <<EOF

The frames are drawing evidence only. The delivery question is the guest's:
  python3 tools/xemu/objects.py --port $port --xbe \$SG1_DUMP/default.xbe --walk 0x6c21b0 \\
      --class Console --fstrings --object-size 0x800
  python3 tools/xemu/console-line.py $out/05-typed.png   # exit 0 = prompt text drawn
EOF
fi
rm -f "$out/hdd.qcow2"   # a boot copy is ~1 GB and is not an artifact
exit "$rc"
