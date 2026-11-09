#!/bin/sh
# Submit console messages in one boot while a live read watches the HUD's text boxes (PLAN 7.3 / U15).
#
# `say hello` is delivered -- `Console.History[0]` holds it and `bTyping` is 0 -- and draws nothing, and the
# reason is the route: `StargateHUD.Message` hands a `Say`/`TeamSay` to `SetSubtitle(Msg, 2.0s)`, which ends in
# `GameHud.DisplayTextBox`, and every other type to `AddNotification` -> `GameHud.SetNotifications`. Both end at
# a `GameHud`, and `UITextBox.DisplayTextBox` writes its argument to `UIWidget.DisplayText` and clears it once
# `FadeOffsetTime + Duration` has passed -- about two seconds. So the assertion is a read inside that window,
# which a frame cannot make (a text line is ~300 px in a noisy 3D view) and a dump taken after the run cannot
# make either (measured: 128 MiB, ten seconds late, no copy of the string).
#
# This script pairs `console-commands.sh`'s measured boot sequence with `hud-text.py`: the watcher starts before
# the boot, waits for the name table to reach a level's size, resolves `StargatePlayer+0x1008 -> GameHud` and
# the HUD's three `UITextBox`es, prints their `DisplayText` once as a baseline, and then prints one line per
# change for `--watch` seconds. The commands are submitted in the middle of that window, so a subtitle shows up
# as a `DialogueWindow` transition with its text, and its disappearance is the measured fade.
#
# **The boot has to be the one the emulator is actually showing.** A boot launched while another application
# owned the screen captured blank frames (every PNG 2,688 bytes, 0 differing pixels), the harness's repaint gate
# failed every shot, and the map never loaded -- and the guest read agreed with the frames rather than with the
# harness, which is why the `--wait` gate exists. `--step=activate` and `--step=raise` put the emulator's window
# in front before the first press; a boot that still fails the level gate is retried fresh (`--retries`), because
# a swallowed select press looks exactly like a map that did not load.
#
# **A free screen is not enough on this desktop** (measured 2026-11-08): with nothing fullscreen -- the Steam
# client was the only window and the desktop held `_NET_ACTIVE_WINDOW` -- every frame was still blank (a
# 7,556-byte PNG, black with one green pixel, five shot steps at 0 differing pixels) while the guest and the stub
# answered. So a blank capture here is a property of the capture path, not of what is in front of the window, and
# a shot step's `0 differing pixels` is not evidence that input was lost. What the read is gated on is the name
# table, never a frame.
#
# Usage: hud-say.sh [options] OUTDIR
#   --iso PATH       disc image                (default $WORK/sg1.iso)
#   --bios DIR       directory with mcpx*.bin (default $SG1_DUMP/bios)
#   --hdd PATH       HDD image to copy         (default $WORK/hdd-val.qcow2)
#   --eeprom PATH    EEPROM to boot from       (default $WORK/xemu/smoke1/eeprom.bin)
#   --index N        map-list index to load    (default 10 = M00)
#   --port N         GDB stub port             (default 12620; one port per attempt)
#   --boot SECS      title wait before the first press (default 240: at 180 s the first `A` lands in the menu's
#                    fade and is lost -- the `sink14` boots failed twice at 180 and passed first time at 240)
#   --pad-hold SECS  seconds a pad button is held (default 0.35)
#   --settle SECS    wait after the map select (default 60)
#   --watch SECS     how long the reader keeps polling after resolving (default 150)
#   --retries N      fresh boots before giving up (default 2)
#   --say TEXT       submit `say TEXT` (repeatable; default `say sg1txt1`)
#   --note TEXT      submit `addnotification TEXT` (repeatable; default `addnotification sg1txt2`)
#
# The markers are deliberately short alphanumerics: the console types one QEMU key name per character and
# refuses anything else, and a marker that is not in the guest's own history cannot be a subtitle either.
set -eu
iso="${WORK:-$HOME/sg1/work}/xemu/sg1.iso"
bios="${SG1_BIOS:-$HOME/nfs/sg1-dump/bios}"
hdd=""
eeprom=""
index=10
port=12620
boot=240
hold=0.35
settle=60
watch=150
retries=2
cmds=""
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --iso=*) iso=${1#*=}; shift ;;
    --bios=*) bios=${1#*=}; shift ;;
    --hdd=*) hdd=${1#*=}; shift ;;
    --eeprom=*) eeprom=${1#*=}; shift ;;
    --index=*) index=${1#*=}; shift ;;
    --port=*) port=${1#*=}; shift ;;
    --boot=*) boot=${1#*=}; shift ;;
    --pad-hold=*) hold=${1#*=}; shift ;;
    --settle=*) settle=${1#*=}; shift ;;
    --watch=*) watch=${1#*=}; shift ;;
    --retries=*) retries=${1#*=}; shift ;;
    --say=*) cmds="$cmds|say ${1#*=}"; shift ;;
    --note=*) cmds="$cmds|addnotification ${1#*=}"; shift ;;
    --say) cmds="$cmds|say $2"; shift 2 ;;
    --note) cmds="$cmds|addnotification $2"; shift 2 ;;
    --*) echo "unknown option $1" >&2; exit 2 ;;
    *) if [ -z "$out" ]; then out="$1"; else echo "unknown argument $1" >&2; exit 2; fi; shift ;;
  esac
done
[ -n "$out" ] || { echo "usage: hud-say.sh [options] OUTDIR" >&2; exit 2; }
[ -n "$cmds" ] || cmds="|say sg1txt1|addnotification sg1txt2"
[ -n "$hdd" ] || hdd="${WORK:-$HOME/sg1/work}/hdd-val.qcow2"
[ -n "$eeprom" ] || eeprom="${WORK:-$HOME/sg1/work}/xemu/smoke1/eeprom.bin"
[ -f "$hdd" ] || { echo "no HDD image at $hdd (pass --hdd): a run needs a copy it may write" >&2; exit 2; }
[ -f "$iso" ] || { echo "no disc image at $iso (pass --iso)" >&2; exit 2; }
# One boot at a time. The HDD copy is per attempt but the GDB port is not: two emulators on one desktop answer
# `-gdb tcp::N: Address already in use` and the second exits, which drive.py reports as `xemu exited immediately`
# and exit 3 -- a broken-looking emulator that is really a collision (measured 2026-11-08, when a hand-launched
# boot met this script's own retry). A leftover uinput pad is the quieter half: every `pad=` step does nothing.
if pgrep -x AppRun >/dev/null 2>&1; then
  echo "another emulator is running: one boot per desktop (xemu holds the GDB port and the HDD copy)" >&2
  exit 2
fi
if grep -q "SG1 virtual pad" /proc/bus/input/devices 2>/dev/null; then
  echo "a leftover 'SG1 virtual pad' is bound: every pad= step would do nothing -- clear /proc/bus/input/devices first" >&2
  exit 2
fi
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

write_steps() {  # $1 = attempt directory
  sf="$1/steps.txt"
  : > "$sf"
  step() { printf '%s\n' "$1" >> "$sf"; }
  step "--step=monitor=device_add usb-kbd,port=1.3.2,id=kbd"
  # A boot whose window is behind another application captures nothing (every frame a 2,688-byte blank), so the
  # harness has no picture to assert on and the presses are unverified: raise and focus it before the first press.
  step "--step=raise=1"
  step "--step=activate"
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
  step "--step=pad=a"
  step "--step=wait=6"
  step "--step=pad=a"
  step "--step=wait=$settle"
  step "--step=shot=02-mission"
  step "--step=pad=a"
  step "--step=wait=4"
  step "--step=shot=03-dismissed"
  step "--step=noise=3"
  # The reader needs a resolved row before the first submit; twenty seconds is what the sink walk costs.
  step "--step=wait=20"
  n=0
  oldIFS=$IFS
  IFS='|'
  for cmd in $cmds; do
    IFS=$oldIFS
    [ -n "$cmd" ] || continue
    n=$((n+1))
    slug=$(printf '%s' "$cmd" | tr -c 'a-zA-Z0-9' '-')
    keys=""
    rest=$cmd
    while [ -n "$rest" ]; do
      c=$(printf '%s' "$rest" | cut -c1)
      rest=$(printf '%s' "$rest" | cut -c2-)
      case "$c" in
        [a-z0-9]) k=$c ;;
        [A-Z]) echo "cannot type '$c': QEMU has no bare key name for an upper-case letter (a dropped character reads as a command that never ran); use lower case and digits" >&2; exit 2 ;;
        ' ') k=spc ;;
        .) k=dot ;;
        -) k=minus ;;
        _) k=shift-minus ;;
        /) k=slash ;;
        *) echo "cannot type '$c' as a guest key: simplify --say/--note" >&2; exit 2 ;;
      esac
      keys="$keys $k"
    done
    step "--step=gkey=tab"
    step "--step=wait=2"
    step "--step=gkey=spc"
    step "--step=wait=0.6"
    for k in $keys; do
      step "--step=gkey=$k"
    done
    step "--step=wait=1"
    step "--step=gkey=ret"
    step "--step=wait=0.7"
    step "--step=shot=04$n-$slug"
    step "--step=wait=6"
    IFS='|'
  done
  IFS=$oldIFS
  step "--step=cpu=2"
}

report() {  # $1 = attempt directory -- what the run is for, and what it refuses to claim
  printf '%s\n' "--- hud.log ($1)"
  sed -n '1,200p' "$1/hud.log"
  resolved=0
  verdict=0
  grep -q "GameHudPtr\` holds the live" "$1/hud.log" && resolved=1
  grep -q "no level within" "$1/hud.log" && verdict=1
  # A `--wait` longer than the run leaves no verdict at all: the watcher is killed mid-wait, and "no live
  # GameHud" then reads as a level that failed to load when it may have loaded after the run stopped looking.
  if [ "$resolved" = 0 ] && [ "$verdict" = 0 ]; then
    echo "NO VERDICT  (the watcher was killed before its deadline: hud.log holds neither a resolved \`GameHud\`"
    echo "            nor 'no level within'. Nothing here says whether a level loaded -- see killed.log)"
  fi
  oldIFS=$IFS
  IFS='|'
  for cmd in $cmds; do
    IFS=$oldIFS
    [ -n "$cmd" ] || continue
    mark=$(printf '%s' "$cmd" | sed 's/.* //')
    if grep -q "$mark" "$1/hud.log"; then
      echo "FOUND  $mark  ($(grep -c "$mark" "$1/hud.log") line(s), first: $(grep -m1 "$mark" "$1/hud.log"))"
    elif [ "$resolved" = 1 ]; then
      echo "ABSENT $mark  ('$cmd' was submitted and no box ever held it, on a boot whose HUD was read)"
    else
      echo "UNREAD $mark  (no live \`GameHud\` was resolved: the boot's level never loaded, so a zero here is " \
           "about the boot and not about the message)"
    fi
    IFS='|'
  done
  IFS=$oldIFS
  return "$resolved"
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
  gport=$((port + attempt - 1))
  # The watcher has to be up before the guest is: it waits for the stub to accept a connection and for the name
  # table to reach a level's size, so its clock is the boot's, not this script's.
  # The watcher's deadline has to land *inside* the run. `--wait` used to be `boot + settle + 150` (450 s at the
  # defaults) against a sequence that finishes in about `boot - 75 + settle + 40` s, so the watcher was killed
  # while still waiting and a boot that never loaded a level left no verdict at all -- `UNREAD` with no cause
  # (measured 2026-11-08). Ten seconds per command is the typing cost, plus a little slack.
  ncmd=$(printf '%s' "$cmds" | tr '|' '\n' | grep -c . || true)
  budget=$((boot - 75 + settle + 30 + 12 * ncmd))
  python3 -u "$repo/tools/xemu/hud-text.py" --port "$gport" --wait "$budget" --watch "$watch" \
      --tag "try$attempt" --tries 12 > "$dir/hud.log" 2>&1 &
  wpid=$!
  sleep 1
  echo "attempt $attempt: index $index, gdb $gport, commands:$cmds -> $dir" | tee "$dir/attempt.log"
  rc=0
  ( cd "$repo" && python3 tools/xemu/drive.py --iso="$iso" --bios="$bios" --hdd="$dir/hdd.qcow2" --out="$dir" \
      --eeprom="$eeprom" --gdb="$gport" --linger=30 --fuzz=10 --seconds=75 --pad-hold="$hold" \
      --static-shot=-tab --static-shot=-typed "$@" ) > "$dir/run.log" 2>&1 || rc=$?
  echo "drive.py exit $rc" >> "$dir/run.log"
  sleep 2
  kill "$wpid" 2>/dev/null || true
  wait "$wpid" 2>/dev/null || true
  rm -f "$dir/hdd.qcow2"
  report "$dir" || true
  if grep -q "GameHudPtr\` holds the live" "$dir/hud.log"; then
    loaded=1
    break
  fi
  attempt=$((attempt + 1))
done
if [ "$loaded" = 1 ]; then
  echo "PASS: a level loaded and its \`GameHud\` was read"
else
  echo "FAIL: $retries boot(s) and no level-sized name table -- see each try's frames and hud.log"
fi
exit 0
