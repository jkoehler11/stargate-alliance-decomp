#!/bin/bash
# Does a shipped checkpoint load back into the level that wrote it, and does play resume?
#
# The one attempt on record typed `checkpointload c_m02a_drillsite.sgs` from a loaded **M00** -- a checkpoint of a
# different level -- and the guest fell to the kernel's `Please insert an Xbox disc...` prompt (U5, 2026-11-12).
# That is a foreign-level load, and it also used the wrong route: `PlayerController.CheckpointLoad` issues
# `LoadCheckPoint`, while every path the shipped scripts use -- `RestartCheckpoint`, `SavedGameList` -- issues
# `LoadSaveGame` through `PlayerController.LoadGame`, and `GameInfo.PostLogin` puts that filename back through
# `LoadSaveGame` when `bIsSaveGame` is set. So this boots one map, waits for a gameplay frame, and types **both**
# spellings at the same file. It is the frame that decides: the HUD's top-left corner draws on every gameplay
# frame (0.140-0.986) and is dark on a cutscene, a menu and the BIOS screen (0.000-0.027), measured over 72 frames
# in docs/MAP_FRAMES.md.
#
#     tools/xemu/checkpoint-load.sh OUTDIR INDEX C_<level>_<name>.sgs [C_...sgs]
#
# OUTDIR gets the frames, run.log and one row in `runs.tsv`; the HDD copy and the shader cache are removed at the
# end because a boot is ~1.2 GB and the disk has been filled to 99% by a leaked sweep before. Run it on the desktop
# with DISPLAY=:0 and the xauth of session 1000, launched detached: a foreground ssh is cut at 180 s and the
# sequence is ~8 minutes.
set -u
cd "$HOME/sg1/agent/sg1clone" || exit 2

[ "$#" -ge 3 ] || { echo "usage: $0 OUTDIR INDEX SGS..." >&2; exit 2; }
D="$1"; INDEX="$2"; shift 2
SGS=("$@")
PORT=12931

rm -rf "$D"; mkdir -p "$D"
{
  echo "before: apprun=$(pgrep -xc AppRun) pads=$(grep -c 'SG1 virtual pad' /proc/bus/input/devices || true)"
  df -h "$HOME" | tail -1
} > "$D/clean.log" 2>&1
pkill -x AppRun 2>/dev/null
for p in $(pgrep -f 'drive[.]py'); do kill -9 "$p" 2>/dev/null; done
sleep 3
{
  echo "after: apprun=$(pgrep -xc AppRun) pads=$(grep -c 'SG1 virtual pad' /proc/bus/input/devices || true)"
  echo "iso md5 $(md5sum "$HOME/sg1/work/xemu/sg1.iso" | cut -d' ' -f1)"
  echo "eeprom md5 $(md5sum "$HOME/sg1/work/xemu/smoke1/eeprom.bin" | cut -d' ' -f1)"
} >> "$D/clean.log"
cp --reflink=auto "$HOME/sg1/work/hdd-val.qcow2" "$D/hdd.qcow2" || exit 2
cp "$HOME/sg1/work/xemu/smoke1/eeprom.bin" "$D/eeprom.bin" || exit 2
md5sum "$D/eeprom.bin" >> "$D/clean.log"

args="--iso=$HOME/sg1/work/xemu/sg1.iso --bios=$HOME/nfs/sg1-dump/bios --hdd=$D/hdd.qcow2 --out=$D"
args="$args --eeprom=$D/eeprom.bin --fuzz=10 --seconds=75 --crop=480x520+760+210 --pad-hold=0.35"

# Type a string at the console, one key per sendkey. The console keeps no scrollback, so the frame after the
# submit is the receipt: `ret` is the one key that submits (kp_enter and pad=start leave the object byte-identical).
steps=()
type_word() {
  local w="$1" i ch k
  for ((i = 0; i < ${#w}; i++)); do
    ch="${w:i:1}"
    case "$ch" in
      " ") k=spc ;;
      "_") k=shift-minus ;;
      ".") k=dot ;;
      "-") k=minus ;;
      *) k="$ch" ;;
    esac
    steps+=( "--step=gkey=$k" "--step=wait=0.35" )
  done
}

steps+=( "--step=monitor=device_add usb-kbd,port=1.3.2,id=kbd" )
steps+=( "--step=monitor=info usb" )
steps+=( "--step=wait=65" "--step=shot=00-title" "--step=noise=3" "--step=wait=5" )
steps+=( "--step=pad=a" "--step=wait=4" )
i=0
while [ "$i" -lt "$INDEX" ]; do steps+=( "--step=pad=down" "--step=wait=0.5" ); i=$((i + 1)); done
steps+=( "--step=shot=01-highlight" "--step=pad=a" "--step=wait=70" "--step=shot=02-mission" )
# Two `a` presses after the load are the intro card and the tutorial modal: M00 holds input until the modal is
# dismissed, and a run that types into one looks exactly like a run whose keys never arrived.
steps+=( "--step=cpu=6" "--step=wait=6" "--step=pad=a" "--step=wait=10" "--step=pad=a" "--step=wait=45" "--step=shot=03-gameplay" )
steps+=( "--step=cpu=6" )

# The control comes first, because a load that stops the guest ends the boot: the same console, the same number of
# keystrokes and the same waits, with a command that cannot touch the level. If the frames after THIS submit stop
# moving, the freeze is the mission's own and the load did not cause it.
steps+=( "--step=gkey=tab" "--step=wait=2" "--step=gkey=spc" "--step=wait=0.5" )
type_word "stat fps"
steps+=( "--step=wait=1" "--step=shot=r0a-typed" "--step=gkey=ret" )
steps+=( "--step=wait=30" "--step=shot=r0b-30" )
steps+=( "--step=wait=40" "--step=shot=r0c-70" )
steps+=( "--step=cpu=6" )

n=0
for sgs in "${SGS[@]}"; do
  n=$((n + 1))
  # Round 1 is the route the shipped scripts use (`LoadGame` -> `LoadSaveGame`); round 2 is the exec function the
  # one recorded attempt used (`CheckpointLoad` -> `LoadCheckPoint`), at the same file in the same boot.
  steps+=( "--step=gkey=tab" "--step=wait=2" "--step=gkey=spc" "--step=wait=0.5" )
  type_word "loadgame $sgs"
  steps+=( "--step=wait=1" "--step=shot=r${n}a-typed" "--step=gkey=ret" )
  steps+=( "--step=wait=30" "--step=shot=r${n}b-30" )
  steps+=( "--step=cpu=6" )
  steps+=( "--step=wait=30" "--step=shot=r${n}c-60" )
  steps+=( "--step=wait=40" "--step=shot=r${n}d-100" )
  steps+=( "--step=gkey=tab" "--step=wait=2" "--step=gkey=spc" "--step=wait=0.5" )
  type_word "checkpointload $sgs"
  steps+=( "--step=wait=1" "--step=shot=r${n}e-cptyped" "--step=gkey=ret" )
  steps+=( "--step=wait=50" "--step=shot=r${n}f-cp50" )
  steps+=( "--step=wait=30" "--step=shot=r${n}g-cp80" )
  steps+=( "--step=cpu=6" )
done

# The picker belongs to the console run too: `Retry` is the pad's own way out of a frozen level, and a press that
# revives the picture after the load says the guest was waiting rather than stopped.
steps+=( "--step=pad=start" "--step=wait=5" "--step=shot=h0-pause" )
steps+=( "--step=pad=a" "--step=wait=20" "--step=shot=h1-after-a" "--step=cpu=6" )

export DISPLAY=:0 APPIMAGE_EXTRACT_AND_RUN=1
export XAUTHORITY="$(ls /run/user/1000/xauth_* | head -1)"
{ echo -e "index	$INDEX"; for s in "${SGS[@]}"; do echo -e "sgs	$s"; done; } > "$D/runs.tsv"
python3 -u tools/xemu/drive.py $args "${steps[@]}" > "$D/run.log" 2>&1 &
dpid=$!
echo "drive pid $dpid" > "$D/arm.log"
n=0
while [ "$n" -lt 200 ]; do
  [ -f "$D/00-title.png" ] && break
  sleep 3; n=$((n + 1))
done
echo "title shot after $((n * 3))s; apprun=$(pgrep -xc AppRun)" >> "$D/arm.log"
wait "$dpid"
echo "drive_exit=$?" >> "$D/arm.log"
pkill -x AppRun 2>/dev/null
sleep 2
rm -f "$D/hdd.qcow2"
rm -rf "$D/xdg"
ls "$D" >> "$D/arm.log"
echo done >> "$D/arm.log"
