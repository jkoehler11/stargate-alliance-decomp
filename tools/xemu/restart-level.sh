#!/bin/bash
# Restart the loaded level from the Unreal console and photograph the unload and the reload (PLAN 7.2/7.3, U8).
#
# A level *unload* that leaves the guest alive is the one window a boot could still reach for the slot-19/20 pair:
# a checkpoint of another level takes the guest down to the BIOS prompt (`docs/XEMU.md`, U5), and the mid-game
# menu's own exit was never found (three d-pad presses reach the bottom of the list). `RestartLevel` is an
# `exec function` on `PlayerController` (`inventory/console_commands.csv`), and its body travels with an empty map
# name -- `ClientTravel("?restart", NM_ListenServer, false)` in standalone play -- so it destroys the current level
# and loads it again without a reset.
#
# Every step below is a measured fact (`references/xemu-input.md`): the keyboard attaches at 75 s because one
# attached after the level loads is never read; nothing is pressed before 135 s because the title panel is still
# fading; Continue -> N d-pad presses -> A reaches the map-list entry, then A dismisses the intro card and A the
# tutorial modal that holds input until answered; Tab opens the console once, `spc` defeats `bIgnoreKeys`, the
# command goes one key per sendkey, the typed line is read off a frame *before* the submit because a submit that
# never landed and a command that printed nothing are the same picture, and `ret` is the only key that submits.
#
# The frames are the evidence; the exit status is the verdict, and it says `UNREAD` rather than `absent` whenever
# the run never reached the step that would have drawn the line.
#
# Usage: tools/xemu/restart-level.sh [options] OUTDIR
#   --iso PATH        disc image                 (default $WORK/sg1.iso)
#   --bios DIR        directory with mcpx*.bin   (default $SG1_DUMP/bios)
#   --hdd PATH        HDD image to copy          (default $WORK/hdd-val.qcow2)
#   --eeprom PATH     EEPROM to boot from        (default $WORK/xemu/smoke1/eeprom.bin)
#   --index N         map-list index to load     (default 10 = M00)
#   --command TEXT    console command to type    (default RestartLevel)
#   --boot SECS       title wait before the list (default 135)
#   --settle SECS     wait after the select      (default 55)
#   --post LIST       secs after `ret`, comma separated (default 5,20,45,85)
#   --arm A,B         also arm these two bodies via bp-args.py and keep its stops
#   --window SECS     bp-args stop window        (default 380)
#   --gdb PORT        stub port                  (default 12351)
#   --linger SECS     stub lifetime              (default 1200)
#   --keep            keep the boot's HDD copy (default: delete it, it is ~1 GB)
#
# Exit: 0 = the command was typed, submitted and the guest is still running; 2 = the run could not measure that
# (a missing frame, a prompt that never drew, or a guest that fell back to the BIOS screen); 3 = bad usage.
set -u
iso="${WORK:-$HOME/.local/share/xemu/sg1}/sg1.iso"
bios="${SG1_BIOS:-$HOME/nfs/sg1-dump/bios}"
hdd="${WORK:-$HOME/.local/share/xemu/sg1}/hdd-val.qcow2"
eeprom="${WORK:-$HOME/.local/share/xemu/sg1}/xemu/smoke1/eeprom.bin"
index=10
command=RestartLevel
boot=135
settle=55
post="5,20,45,85"
arm=""
window=380
port=12351
linger=1200
keep=0
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --iso=*) iso=${1#*=}; shift ;;
    --bios=*) bios=${1#*=}; shift ;;
    --hdd=*) hdd=${1#*=}; shift ;;
    --eeprom=*) eeprom=${1#*=}; shift ;;
    --index=*) index=${1#*=}; shift ;;
    --command=*) command=${1#*=}; shift ;;
    --boot=*) boot=${1#*=}; shift ;;
    --settle=*) settle=${1#*=}; shift ;;
    --post=*) post=${1#*=}; shift ;;
    --arm=*) arm=${1#*=}; shift ;;
    --window=*) window=${1#*=}; shift ;;
    --gdb=*) port=${1#*=}; shift ;;
    --linger=*) linger=${1#*=}; shift ;;
    --keep) keep=1; shift ;;
    --iso) iso="$2"; shift 2 ;;
    --bios) bios="$2"; shift 2 ;;
    --hdd) hdd="$2"; shift 2 ;;
    --eeprom) eeprom="$2"; shift 2 ;;
    --index) index="$2"; shift 2 ;;
    --command) command="$2"; shift 2 ;;
    --boot) boot="$2"; shift 2 ;;
    --settle) settle="$2"; shift 2 ;;
    --post) post="$2"; shift 2 ;;
    --arm) arm="$2"; shift 2 ;;
    --window) window="$2"; shift 2 ;;
    --gdb) port="$2"; shift 2 ;;
    --linger) linger="$2"; shift 2 ;;
    -*) echo "unknown option $1" >&2; exit 3 ;;
    *) if [ -z "$out" ]; then out="$1"; else echo "unknown argument $1" >&2; exit 3; fi; shift ;;
  esac
done
[ -n "$out" ] || { echo "usage: restart-level.sh [options] OUTDIR" >&2; exit 3; }
[ -f "$iso" ] || { echo "no disc image at $iso (pass --iso)" >&2; exit 3; }
[ -f "$hdd" ] || { echo "no HDD image at $hdd (pass --hdd): a boot needs a copy it may write" >&2; exit 3; }
[ -f "$eeprom" ] || { echo "no EEPROM at $eeprom (pass --eeprom): without a profile Continue opens SELECT PROFILE" >&2; exit 3; }

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)

# A QEMU key name per character. The console takes characters; `_` is shift-minus and `.` is dot.
keys=()
rest=$command
while [ -n "$rest" ]; do
  c=$(printf '%s' "$rest" | cut -c1)
  rest=$(printf '%s' "$rest" | cut -c2-)
  case "$c" in
    [a-zA-Z]) keys+=("$(printf '%s' "$c" | tr 'A-Z' 'a-z')") ;;
    [0-9]) keys+=("$c") ;;
    ' ') keys+=("spc") ;;
    .) keys+=("dot") ;;
    -) keys+=("minus") ;;
    _) keys+=("shift-minus") ;;
    /) keys+=("slash") ;;
    *) echo "cannot type '$c' as a guest key (letters, digits, space, . - _ / only)" >&2; exit 3 ;;
  esac
done

rm -rf "$out"; mkdir -p "$out"
cp --reflink=auto "$hdd" "$out/hdd.qcow2" || exit 2
cp "$eeprom" "$out/eeprom.bin" || exit 2
{ echo "before: apprun=$(pgrep -xc AppRun) pads=$(grep -c 'SG1 virtual pad' /proc/bus/input/devices || true)"
  df -h "$HOME" | tail -1; } > "$out/clean.log" 2>&1
pkill -x AppRun 2>/dev/null
for p in $(pgrep -f 'drive[.]py'); do kill -9 "$p" 2>/dev/null; done
sleep 2
echo "iso md5 $(md5sum "$iso" | cut -d' ' -f1)" >> "$out/clean.log"
echo "hdd copy md5 $(md5sum "$out/hdd.qcow2" | cut -d' ' -f1)" >> "$out/clean.log"

export DISPLAY="${DISPLAY:-:0}"
if [ -z "${XAUTHORITY:-}" ]; then
  XA=$(ls /run/user/1000/xauth_* 2>/dev/null | head -1) || true
  [ -n "$XA" ] && export XAUTHORITY="$XA"
fi
export APPIMAGE_EXTRACT_AND_RUN=1

# Steps are an ARRAY: a step whose value holds a space (`monitor=device_add usb-kbd,...`) splits on expansion
# otherwise and argparse answers `unrecognized arguments` before any emulator starts.
args="--iso=$iso --bios=$bios --hdd=$out/hdd.qcow2 --out=$out --eeprom=$out/eeprom.bin"
args="$args --fuzz=10 --seconds=75 --crop=480x520+760+210 --gdb=$port --linger=$linger --pad-hold=0.35"
steps=()
steps+=( "--step=monitor=device_add usb-kbd,port=1.3.2,id=kbd" )
steps+=( "--step=monitor=info usb" )
steps+=( "--step=wait=$((boot - 75))" "--step=shot=00-title" "--step=noise=3" "--step=wait=5" )
steps+=( "--step=pad=a" "--step=wait=4" )
i=0
while [ "$i" -lt "$index" ]; do steps+=( "--step=pad=down" "--step=wait=0.5" ); i=$((i+1)); done
steps+=( "--step=shot=01-highlight" "--step=pad=a" "--step=wait=70" "--step=shot=02-mission" )
steps+=( "--step=wait=6" "--step=pad=a" "--step=wait=10" "--step=pad=a" "--step=wait=$settle" )
steps+=( "--step=shot=03-gameplay" )
steps+=( "--step=gkey=tab" "--step=wait=2" "--step=gkey=spc" "--step=wait=0.5" )
for k in "${keys[@]}"; do steps+=( "--step=gkey=$k" "--step=wait=0.35" ); done
steps+=( "--step=wait=1" "--step=shot=04-typed" )
steps+=( "--step=gkey=ret" )
n=4
lastshot=""
for s in $(printf '%s' "$post" | tr ',' ' '); do
  n=$((n+1))
  lastshot=$(printf '%02d' "$n")-after-$s
  steps+=( "--step=wait=$s" "--step=shot=$lastshot" )
done

echo "restart-level: index $index, command '$command', posts $post, out $out"
python3 -u "$repo/tools/xemu/drive.py" $args "${steps[@]}" > "$out/run.log" 2>&1 &
dpid=$!
n=0
while [ "$n" -lt 120 ]; do [ -f "$out/00-title.png" ] && break; sleep 3; n=$((n+1)); done
echo "title shot after $((n*3))s; apprun=$(pgrep -xc AppRun)" > "$out/arm.log"
arm_rc=0
if [ -n "$arm" ]; then
  a=${arm%,*}; b=${arm#*,}
  sleep 4
  timeout $((window + 200)) python3 -u "$repo/tools/xemu/bp-args.py" --port "$port" --wait-user 90 \
    --sample-interval 0.5 --control-self --break "$a" --break "$b" --dump 'ecx+0x0:0x10' \
    --stops 8 --timeout 60 --window "$window" --json "$out/stops.json" >> "$out/arm.log" 2>&1
  echo "arm_exit=$?" >> "$out/arm.log"
  python3 -u "$repo/tools/vcall-stops.py" --json "$out/stops.json" --csv "$out/vcall_slot_stops.csv" \
    >> "$out/arm.log" 2>&1 || echo "vcall_exit=$?" >> "$out/arm.log"
fi
# The harness lingers so a memory read can follow it; the *frames* are this run's evidence, so wait for the last
# one instead of for the emulator to be told to stop. Waiting on drive.py with `--linger` burns the whole linger.
n=0
while [ "$n" -lt 200 ]; do
  [ -f "$out/$lastshot.png" ] && break
  kill -0 "$dpid" 2>/dev/null || break
  sleep 3; n=$((n+1))
done
sleep 3
drive_rc=0
kill -0 "$dpid" 2>/dev/null && { kill -9 "$dpid" 2>/dev/null; drive_rc=137; }
echo "last shot $lastshot after $((n*3))s; drive_exit=$drive_rc" >> "$out/run.log"
pkill -x AppRun 2>/dev/null
[ "$keep" = 1 ] || rm -f "$out/hdd.qcow2"
rm -rf "$out/xdg"

# The verdicts. A missing frame is `UNREAD`, never `absent`: the run failing to reach a step is not the guest
# answering.
verdict=0
say() { echo "$*" | tee -a "$out/verdict.log"; }
: > "$out/verdict.log"
if [ -f "$out/04-typed.png" ]; then
  if python3 "$repo/tools/xemu/console-line.py" "$out/04-typed.png" --require=typed >> "$out/verdict.log" 2>&1; then
    say "typed: the prompt drew text before the submit"
  else
    say "typed: NO TEXT DRAWN on 04-typed.png -- the keys may not have reached the console"; verdict=2
  fi
else
  say "typed: UNREAD (no 04-typed.png -- the run did not reach the console step)"; verdict=2
fi
frames=$(ls "$out"/0[0-9]-after-*.png 2>/dev/null | sort)
if [ -n "$frames" ]; then
  # shellcheck disable=SC2086
  if python3 "$repo/tools/xemu/guest-halt.py" $frames >> "$out/verdict.log" 2>&1; then
    say "guest: a post-submit frame IS the BIOS screen -- the command took the guest down"; verdict=2
  else
    say "guest: every post-submit frame is running (no BIOS screen)"
  fi
  last=$(printf '%s\n' "$frames" | tail -1)
  if [ -f "$out/03-gameplay.png" ]; then
    pct=$(python3 "$repo/tools/xemu/frame-diff.py" "$out/03-gameplay.png" "$last" --min-pct 0 2>/dev/null | tail -1)
    say "reload: $last vs gameplay before the command: $pct"
  fi
else
  say "guest: UNREAD (no post-submit frames)"; verdict=2
fi
say "exit $verdict"
exit "$verdict"
