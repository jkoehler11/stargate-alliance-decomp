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
# **A blank capture here was the reader, not the screen** (settled 2026-11-09): with nothing fullscreen -- the
# Steam client the only window and the desktop holding `_NET_ACTIVE_WINDOW` -- every frame was still blank (a
# 7,556-byte PNG, black with one green pixel, five shot steps at 0 differing pixels) while the guest and the stub
# answered. That was read as a property of the capture path, and it was the watcher: attaching to the stub
# interrupts the CPU and the reader never resumed it, so the guest was frozen at 4.5 s and the frames were one
# picture. `--live` (stop/read/resume) fixes that and the next boot reached `M00` and read all three boxes. What
# the read is gated on is the name table, never a frame.
#
# **Two things then made a boot unreadable for reasons that had nothing to do with the boxes** (measured
# 2026-11-08, both fixed here). The watcher's window was bounded by the *process* clock, so a run that spent
# 240 s on the title and 85 s resolving its `GameHud` printed a baseline and `watched 0s` while the commands it
# was started for were still being typed -- the read could not see the thing it was built to see. And the pad
# presses were sent after `raise`/`activate` without asking who owned the focus, so three boots pressed at a
# window another application was drawing and read as "the map never loaded". `focus=30` now asks X and refuses
# the run (exit 2) instead.
#
# **The markers were typed into a modal and the reader had already stopped** (measured 2026-11-09, `hudf2`).
# `M00`'s tutorial panel ("Press the X button to open the door / A Continue") is up when the intro ends and holds
# input until it is dismissed, so all four commands went nowhere: four shot steps read 0 differing pixels while
# the screen showed the panel. The dismissal press in the mission sequence is not enough -- it lands on the intro
# card ("10 days later / Press A"), minutes before the panel appears -- so the press belongs immediately before
# the first command, after `--post`. The same boot also showed a second, independent stop: the reader's page cache
# has a 300 s deadline of its own, so the watch died at 479 s, *before* the last two commands were typed, and the
# report still called the markers ABSENT. `--deadline` is now the run's own budget, and a report that finds the
# deadline line refuses the per-marker verdict instead of turning "not read" into "not there".
#
# **The four markers were ABSENT with the panel gone, and the boxes never said why** (measured 2026-11-09): the
# run's own frames proved the dismissal (40,532 px then 34,096 px) and the reader was alive (its deadline now
# derives from the sequence), so "the keys never arrived" and "the command ran and drew nothing" stayed
# indistinguishable. `--receipt` answers it from the guest's own state: after the watch the reader walks the
# `Console` class table (`0x0050cb80` in the census) and prints `RECEIPT <mark> IN HISTORY` or `NOT IN HISTORY`,
# which separates a sink that draws nothing from a keystroke that never landed. The receipt polls for
# `--receipt-wait` seconds because the commands are typed at the very end of the sequence, and `--linger` keeps
# the emulator (and its stub) up long enough for that read to finish.
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
#   --post SECS      extra seconds after the level gate before the first command (default 200: on `M00` the
#                    intro runs to ~437 s and tears its `GameHud` down as it hands over, so a command typed
#                    earlier is typed into a HUD that is about to be destroyed)
#   --gap SECS       seconds between commands (default 6)
#   --follow SECS    the reader's rebind cadence (default 2; 0 disables, which is only right for a menu boot)
#   --watch SECS     how long the reader keeps polling after resolving (default 0 = derived from --post)
#   --receipt-wait SECS the receipt's poll window, for a marker typed late (default 90)
#   --linger SECS    seconds drive.py keeps the emulator up after the last step (default 0 = derived, so the
#                    receipt's walk lands inside the run)
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
post=200
gap=6
follow=2
watch=0
receipt_wait=90
linger=0
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
    --post=*) post=${1#*=}; shift ;;
    --gap=*) gap=${1#*=}; shift ;;
    --follow=*) follow=${1#*=}; shift ;;
    --watch=*) watch=${1#*=}; shift ;;
    --receipt-wait=*) receipt_wait=${1#*=}; shift ;;
    --linger=*) linger=${1#*=}; shift ;;
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
ncmd=$(printf '%s' "$cmds" | tr '|' '\n' | grep -c . || true)
# The watch has to outlive the sequence, not the resolve: the commands are typed after the intro hands over,
# which is ~200 s past the level gate, so a 180 s window ends before the first keystroke (measured 2026-11-09:
# the log's last line was the intro's HUD dying, at 437.5 s, and no command had landed yet). The receipt is what
# polls past the last keystroke, so this window only has to cover the typing: `post` plus one command's own
# cost. The resolve lands 20-60 s after the gate, which is the slack a shorter window needs.
[ "$watch" != 0 ] || watch=$((post + (gap + 15) * ncmd))
[ "$watch" -ge 120 ] || watch=120
# The receipt locates the `Console` after the watch, and drive.py's linger is the only thing holding the stub up
# by then. **A whole-array walk does not fit in 180 s**: on `hudsay2` the walk was still reading when the guest
# went away (`the stub stopped answering at page 0x012e0000 ... the guest is gone`), so the receipt printed
# UNMEASURED for both markers and the run's submit question was lost. 300 s covers the 347-page walk plus the
# typing of one more command; the cheap routes (row `+0xc0` -> `InteractionMaster`, HUD `+0x398`) need seconds.
[ "$linger" != 0 ] || linger=$((300 + 30 * ncmd))
# The markers, one per command: the receipt asks the console's own `History` about each of them.
marks=$(printf '%s' "$cmds" | tr '|' '\n' | sed 's/.* //' | grep -v '^$' | paste -sd, - 2>/dev/null || true)
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
  # Raising a window and *owning* the focus are different things, and only the second makes a pad press
  # arrive: drive.py's `focus=` step asks X who owns the focus and refuses (exit 2) rather than pressing into
  # a screen another application is drawing. Three boots pressed at a blank, unfocused window and read as
  # "the map never loaded" (measured 2026-11-08).
  step "--step=focus=30"
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
  step "--step=shot=01-highlight"
  step "--step=pad=a"
  step "--step=wait=6"
  step "--step=pad=a"
  step "--step=wait=$settle"
  step "--step=shot=02-mission"
  step "--step=pad=a"
  step "--step=wait=4"
  step "--step=shot=03-dismissed"
  step "--step=noise=3"
  # The reader needs a resolved row before the first submit; twenty seconds is what the sink walk costs, and
  # `--post` is the wait for the level's own hand-over: `M00` runs an intro to ~437 s whose end destroys the
  # `GameHud` the read resolved, so a command typed before it is typed into a HUD that is about to be gone.
  step "--step=wait=$((20 + post))"
  # The intro ends on the tutorial panel, which freezes the picture and swallows keys until the pad's `A`
  # dismisses it (docs/XEMU.md). The `pad=a` in the mission sequence above lands on the intro card minutes
  # earlier, so the press has to be repeated once `--post` has passed the hand-over: two shots around it turn
  # "the commands went nowhere" into "the press did / did not move the frame".
  step "--step=shot=05-precommands"
  step "--step=pad=a"
  step "--step=wait=3"
  step "--step=shot=06-dismissed"
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
    step "--step=wait=$gap"
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
  deadline=0
  grep -q "passed its .* deadline" "$1/hud.log" && deadline=1
  grep -q "GameHudPtr\` holds the live" "$1/hud.log" && resolved=1
  grep -q "no level within" "$1/hud.log" && verdict=1
  # A refused focus is neither a bad boot nor a message that missed: nothing was pressed, and the run says so
  # instead of leaving the map's absence to be read as the pad's fault.
  if grep -q "never got the focus" "$1/run.log" 2>/dev/null; then
    echo "NO FOCUS  (the emulator never owned the X focus, so no press was sent: bring the desktop to a state"
    echo "          where xemu can be focused -- a fullscreen application on the same display is the usual cause)"
    return 0
  fi
  if grep -q "REFUSED" "$1/run.log" 2>/dev/null && [ "$resolved" = 0 ]; then
    echo "REFUSED  (drive.py stopped at a step it would not fake: $(grep -m1 'REFUSED' "$1/run.log"))"
    return 0
  fi
  # A `--wait` longer than the run leaves no verdict at all: the watcher is killed mid-wait, and "no live
  # GameHud" then reads as a level that failed to load when it may have loaded after the run stopped looking.
  if [ "$resolved" = 0 ] && [ "$verdict" = 0 ]; then
    echo "NO VERDICT  (the watcher was killed before its deadline: hud.log holds neither a resolved \`GameHud\`"
    echo "            nor 'no level within'. Nothing here says whether a level loaded -- see killed.log)"
  fi
  # The tutorial panel is the difference between "the message never painted" and "nothing typed after the
  # intro was ever delivered": its dismissal is a `pad=a` whose two surrounding shots say whether it landed.
  if grep -q "shot 06-dismissed" "$1/run.log" 2>/dev/null; then
    if grep -q "shot 06-dismissed .*PASS" "$1/run.log"; then
      echo "PANEL  the post-resolve \`pad=a\` repainted the frame: the tutorial panel was dismissed"
    else
      echo "PANEL  the post-resolve \`pad=a\` did NOT repaint the frame: the panel (or a frozen picture) still holds"
      echo "       input, so every marker below is about the modal and not about the message"
    fi
  fi
  oldIFS=$IFS
  IFS='|'
  for cmd in $cmds; do
    IFS=$oldIFS
    [ -n "$cmd" ] || continue
    mark=$(printf '%s' "$cmd" | sed 's/.* //')
    # The receipt lines carry the marker too, so a box verdict has to read only the box lines: a `FOUND` that
    # was really `RECEIPT sg1txt1 IN HISTORY` would report the message path as painting when it painted nothing.
    inbox=$(grep -v "RECEIPT \|HIST \|CONSOLE " "$1/hud.log" 2>/dev/null | grep "$mark" || true)
    if [ -n "$inbox" ]; then
      echo "FOUND  $mark  ($(printf '%s\n' "$inbox" | grep -c .) line(s), first: $(printf '%s\n' "$inbox" | grep -m1 .))"
    elif [ "$deadline" = 1 ]; then
      echo "UNREAD $mark  (the reader hit its page-cache deadline before the watch ended: the commands after"
      echo "          that point were typed with nothing reading, so this says nothing about the message)"
    elif [ "$resolved" = 1 ]; then
      echo "ABSENT $mark  ('$cmd' was submitted and no box ever held it, on a boot whose HUD was read)"
    else
      echo "UNREAD $mark  (no live \`GameHud\` was resolved: the boot's level never loaded, so a zero here is " \
           "about the boot and not about the message)"
    fi
    # The second question, from the console's own state: a delivered line with an empty box is the sink drawing
    # nothing, and an empty history is a keystroke that never arrived. Both read `ABSENT` above.
    if grep -q "RECEIPT $mark IN HISTORY" "$1/hud.log" 2>/dev/null; then
      echo "DELIVERED $mark  (the console's own \`History\` holds the line, so the submit ran: a box that never"
      echo "          held it is the message path drawing nothing, not a lost keystroke)"
    elif grep -q "RECEIPT $mark UNMEASURED" "$1/hud.log" 2>/dev/null; then
      echo "UNREAD $mark  (the receipt walked the object array and read no \`Console\` object, so \`History\` was"
      echo "          never seen: delivery is unmeasured here, not a keystroke that failed)"
    elif grep -q "RECEIPT $mark NOT IN HISTORY" "$1/hud.log" 2>/dev/null; then
      echo "NOT DELIVERED $mark  (no submit ran: the keys never reached the console -- the modal, the focus or a"
      echo "          dropped character. Nothing here is about the message path)"
    else
      echo "UNREAD $mark  (no \`Console.History\` read in this log: the receipt did not run, so delivery is unmeasured)"
    fi
    IFS='|'
  done
  IFS=$oldIFS
  # A rebind is the difference between "no box held the marker" and "the marker went to a HUD nobody read":
  # the intro tears its `GameHud` down mid-level, so print every slot change the watch saw.
  if grep -q " rebind  \| follow  " "$1/hud.log" 2>/dev/null; then
    echo "SLOT   the read followed the row's \`+0x1008\`:"
    grep " rebind  \| follow  \| rewalk  " "$1/hud.log" | sed 's/^/          /'
  fi
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
  # table to reach a level's size, so its clock is the boot's, not this script's. `--live` is not optional here:
  # attaching to the stub interrupts the CPU, and the reader used to leave it stopped -- the guest it was
  # watching was frozen from the first poll, which is the blank frame and the name table that never grew
  # (measured 2026-11-09, six boots). The live reader stops, reads and resumes, so the boot proceeds.
  # The watcher's deadline has to land *inside* the run. `--wait` used to be `boot + settle + 150` (450 s at the
  # defaults) against a sequence that finishes in about `boot - 75 + settle + 40` s, so the watcher was killed
  # while still waiting and a boot that never loaded a level left no verdict at all -- `UNREAD` with no cause
  # (measured 2026-11-08). Ten seconds per command is the typing cost, plus a little slack.
  ncmd=$(printf '%s' "$cmds" | tr '|' '\n' | grep -c . || true)
  # Measured 2026-11-08: `boot - 75 + settle + 45` (300 s at the defaults) expired while the boot was still
  # loading -- the watcher said `unreadable` and stopped looking six seconds before the level it was waiting
  # for. The clock is the sequence's whole length from the launch, plus its typing, so the deadline lands after
  # the last command and not at the settle -- and `--post` is part of the sequence.
  budget=$((boot + settle + 90 + post + (gap + 15) * ncmd))
  python3 -u "$repo/tools/xemu/hud-text.py" --port "$gport" --wait "$budget" --watch "$watch" \
    --deadline "$budget" --receipt "$marks" --receipt-wait "$receipt_wait" \
    --follow "$follow" --live --tag "try$attempt" --tries 12 > "$dir/hud.log" 2>&1 &
  wpid=$!
  sleep 1
  echo "attempt $attempt: index $index, gdb $gport, commands:$cmds -> $dir" | tee "$dir/attempt.log"
  rc=0
  ( cd "$repo" && python3 tools/xemu/drive.py --iso="$iso" --bios="$bios" --hdd="$dir/hdd.qcow2" --out="$dir" \
      --eeprom="$eeprom" --gdb="$gport" --linger="$linger" --fuzz=10 --seconds=75 --pad-hold="$hold" \
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
