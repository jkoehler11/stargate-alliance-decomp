#!/bin/sh
# Type serveral console commands in one boot and photograph each one's effect (PLAN 7.3 / U15).
#
# The console on the stock disc draws nothing (`Engine.Console` has no `PostRender`), so a command's *effect* is the
# only picture there is: `stat fps` puts a counter up and is the measured positive control. `Engine.HUD.Message`
# (`AddTextMessage`) is the other candidate the source names -- it fills `HUD.TextMessages[8]` -- and whether anything
# paints that buffer is a fact about `StargateHUD.DrawHUD`, not about the console. This script measures one submitted
# command at a time: a frame after each, and one whole-RAM dump at the end so a message can be looked for in the guest.
#
# Each command gets the measured sequence: `Tab` opens the console (one press -- it toggles), the first keypress is
# swallowed by `bIgnoreKeys` (`Console.uc`), the characters follow, `ret` submits and closes, and then a frame one
# second after the submit -- inside the lifetime of the game's own message channels (`StargateHUD.Message` gives a
# `Say` a 2.0 s subtitle, and a notification expires after `NotificationLifeTime`) -- followed by two frames three
# seconds apart that say whether the world still animates (`noise=`, the frame's own animation in pixels).
# **A frame taken three seconds after the submit cannot see a two-second subtitle**, and that is the whole of why the
# first measured run read zero on `say hello`.
#
# Usage: console-commands.sh [options] OUTDIR
#   --iso PATH       disc image                (default $WORK/sg1.iso)
#   --bios DIR       directory with mcpx*.bin (default $SG1_DUMP/bios)
#   --hdd PATH       HDD image to copy         (default $WORK/hdd-val.qcow2)
#   --eeprom PATH    EEPROM to boot from       (default $WORK/xemu/smoke1/eeprom.bin)
#   --index N        map-list index to load    (default 10 = M00)
#   --boot SECS      title wait before the first press (default 180: a boot that shared the desktop with a rendering
#                    game lost its first `A` at 165 s -- the press moved only the highlight and nothing ever loaded)
#   --pad-hold SECS  seconds a pad button is held, forwarded to drive.py (default 0.35: the 0.15 s press is the
#                    suspect for a menu that registers a highlight move and no select)
#   --settle SECS    wait after the map select (default 60)
#   --retries N      attempts to get a level loaded before giving up (default 2). A swallowed select press leaves
#                    the main menu up and the run then types into the menu, which is indistinguishable from a console
#                    that does not work -- so each attempt is a fresh boot and every attempt keeps its frames.
#   --cmd TEXT       a console command, repeatable (default `god`, `say hello`, `behindview 1`)
#   --watch TEXT     after the commands, dump all RAM and look for TEXT in it (`drive.py --step=watch=`) -- the
#                    receipt for a `ClientMessage` that the frame cannot show
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
boot=180
hold=0.35
settle=60
retries=2
dump=1
cmds=""
watch=""
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --iso=*) iso=${1#*=}; shift ;;
    --bios=*) bios=${1#*=}; shift ;;
    --hdd=*) hdd=${1#*=}; shift ;;
    --eeprom=*) eeprom=${1#*=}; shift ;;
    --index=*) index=${1#*=}; shift ;;
    --boot=*) boot=${1#*=}; shift ;;
    --pad-hold=*) hold=${1#*=}; shift ;;
    --settle=*) settle=${1#*=}; shift ;;
    --retries=*) retries=${1#*=}; shift ;;
    --cmd=*) cmds="$cmds|${1#*=}"; shift ;;
    --watch=*) watch=${1#*=}; shift ;;
    --no-dump) dump=0; shift ;;
    --iso) iso="$2"; shift 2 ;;
    --bios) bios="$2"; shift 2 ;;
    --hdd) hdd="$2"; shift 2 ;;
    --eeprom) eeprom="$2"; shift 2 ;;
    --index) index="$2"; shift 2 ;;
    --boot) boot="$2"; shift 2 ;;
    --pad-hold) hold="$2"; shift 2 ;;
    --settle) settle="$2"; shift 2 ;;
    --retries) retries="$2"; shift 2 ;;
    --cmd) cmds="$cmds|$2"; shift 2 ;;
    --watch) watch="$2"; shift 2 ;;
    --*) echo "unknown option $1" >&2; exit 2 ;;
    *) if [ -z "$out" ]; then out="$1"; else echo "unknown argument $1" >&2; exit 2; fi; shift ;;
  esac
done
[ -n "$out" ] || { echo "usage: console-commands.sh [options] OUTDIR" >&2; exit 2; }
[ -n "$cmds" ] || cmds="|god|say hello|behindview 1"
[ -n "$hdd" ] || hdd="${WORK:-$HOME/sg1/work}/hdd-val.qcow2"
[ -n "$eeprom" ] || eeprom="${WORK:-$HOME/sg1/work}/xemu/smoke1/eeprom.bin"
[ -f "$hdd" ] || { echo "no HDD image at $hdd (pass --hdd): a run needs a copy it may write" >&2; exit 2; }
[ -f "$iso" ] || { echo "no disc image at $iso (pass --iso)" >&2; exit 2; }
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)

rm -rf "$out"; mkdir -p "$out"
out=$(cd "$out" && pwd)
export DISPLAY="${DISPLAY:-:0}"
if [ -z "${XAUTHORITY:-}" ]; then
  XA=$(ls /run/user/1000/xauth_* 2>/dev/null | head -1) || true
  [ -n "$XA" ] && export XAUTHORITY="$XA"
fi
export APPIMAGE_EXTRACT_AND_RUN=1

# The step list goes into a file, one argv per line, because two of its values contain spaces (`monitor=device_add
# usb-kbd,...` and a `watch=` phrase); splitting a shell string would break them into stray arguments.
write_steps() {  # $1 = attempt directory
  sf="$1/steps.txt"
  : > "$sf"
  step() { printf '%s\n' "$1" >> "$sf"; }
  step "--step=monitor=device_add usb-kbd,port=1.3.2,id=kbd"
  # 75 s for the window, then the rest of the title: the keyboard has to be in the guest before it enumerates `XID`,
  # and no press lands until the menu has finished fading in.
  step "--step=wait=$((boot - 75))"
  step "--step=shot=00-title"
  step "--step=noise=3"
  step "--step=pad=a"
  step "--step=wait=4"
  step "--step=shot=01-list"
  i=0
  while [ "$i" -lt "$index" ]; do
    step "--step=pad=down"
    step "--step=wait=0.9"
    i=$((i+1))
  done
  # `A` selects, `A` dismisses the intro card, the settle reaches gameplay, `A` dismisses the tutorial modal.
  step "--step=pad=a"
  step "--step=wait=6"
  step "--step=pad=a"
  step "--step=wait=$settle"
  step "--step=shot=02-mission"
  step "--step=pad=a"
  step "--step=wait=4"
  step "--step=shot=03-dismissed"
  step "--step=noise=3"

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
    step "--step=gkey=tab"
    step "--step=wait=2"
    step "--step=shot=04$n-tab"
    step "--step=gkey=spc"
    step "--step=wait=0.6"
    for k in $keys; do
      step "--step=gkey=$k"
    done
    step "--step=wait=1"
    step "--step=shot=05$n-typed"
    step "--step=gkey=ret"
    step "--step=wait=1"
    step "--step=shot=06$n-$slug"
    step "--step=noise=3"
    IFS='|'
  done
  IFS=$oldIFS
  if [ -n "$watch" ]; then
    step "--step=watch=$watch"
  fi
  step "--step=cpu=2"
  if [ "$dump" = 1 ]; then
    step "--step=monitor=pmemsave 0 0x8000000 \"$1/ram.bin\""
  fi
}

attempt=1
loaded=0
while [ "$attempt" -le "$retries" ]; do
  dir="$out/try$attempt"
  mkdir -p "$dir"
  cp --reflink=auto "$hdd" "$dir/hdd.qcow2"
  write_steps "$dir"
  set --
  while IFS= read -r s; do set -- "$@" "$s"; done < "$dir/steps.txt"
  args="--iso=$iso --bios=$bios --hdd=$dir/hdd.qcow2 --out=$dir --eeprom=$eeprom"
  # The console has no `PostRender` on this disc, so a frame that shows it cannot differ from the one before it:
  # `*-tab` and `*-typed` are expected identical, and the gate has to be told rather than read as a lost key.
  args="$args --fuzz=10 --seconds=75 --pad-hold=$hold --static-shot=-tab --static-shot=-typed"
  echo "attempt $attempt: index $index, commands:$cmds -> $dir"
  rc=0
  ( cd "$repo" && python3 tools/xemu/drive.py $args "$@" ) > "$dir/run.log" 2>&1 || rc=$?
  echo "drive.py exit $rc" >> "$dir/run.log"
  rm -f "$dir/hdd.qcow2"   # a boot copy is ~1 GB and is not an artifact
  if [ -f "$dir/02-mission.png" ] && [ -f "$dir/00-title.png" ] \
     && ( cd "$repo" && python3 tools/xemu/frame-diff.py "$dir/00-title.png" "$dir/02-mission.png" --min-pct=2.5 ) >> "$dir/run.log" 2>&1; then
    loaded=1
    break
  fi
  echo "attempt $attempt: still the main menu at the settle (frame-diff against the title); retrying" >> "$dir/run.log"
  attempt=$((attempt+1))
done

tail -25 "$out/try$attempt/run.log"
if [ "$loaded" = 1 ]; then
  echo "console command run: attempt $attempt in $out/try$attempt"
  exit 0
fi
echo "console command run: no level loaded in $retries attempt(s); frames kept in $out" >&2
exit 3
