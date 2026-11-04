#!/bin/sh
# Load missions one at a time out of the in-game map list, and keep one frame per mission (PLAN 7.2).
#
# The list opens on `CTF-Abydos` (index 0) and advances exactly one entry per d-pad press, so a mission's index is
# the number of presses -- measured: 10 is `M00`, 11 `M02A`, 15 `M04B`, 31 `M12F`, 35 `M13D` (docs/XEMU.md). This
# script takes indices, not names, on purpose: the name of the mission an index lands on is read off the frame it
# saves, which is the only place it is recorded.
#
# Usage: missions.sh [options] OUTDIR [INDEX ...]
#   --iso PATH      disc image                (default $WORK/sg1.iso)
#   --bios DIR      directory with mcpx*.bin and a debug flash (default $SG1_DUMP/bios)
#   --hdd PATH      HDD image to copy per run (default $WORK/probe/hdd.qcow2)
#   --eeprom PATH   EEPROM to boot from; the profile-carrying one sends Continue straight to the list
#                   (default $WORK/smoke1/eeprom.bin)
#   --settle SECS   seconds to wait after the map is selected (default 60)
#   --boot SECS     seconds to wait for the title before the first press (default 135: at 110 the menu is still fading
#                   in and the press can be swallowed -- docs/XEMU.md measures 1,476 pixels at 110 s against 34,890 at
#                   135 s for the same key)
#   --all           the campaign range, 10-40 (the list has 41 entries and index 10 is the first campaign map; the
#                   0-9 range is the multiplayer maps and `Entry`). `--all --dry-run` prints the indices instead of
#                   booting, which is how the range is checked without a three-hour run.
#   --dry-run       print what would run, then exit
#   --retries N     attempts per index before it counts as failed (default 2). A run fails when a press is swallowed,
#                   and a swallowed press looks identical to a map that will not load -- measured, one index in six
#                   failed that way in a six-index run, with the highlight frame still showing the main menu. Each
#                   attempt is a fresh boot, and the TSV records how many it took.
# One run per index: a boot, `Continue`, N d-pad presses, `A`, a settle, one screenshot. Missions do not share an
# emulator session because a level that fails to load leaves the guest in a state the next index cannot trust.
set -eu
iso="${WORK:-$HOME/.local/share/xemu/sg1}/sg1.iso"
bios="${SG1_BIOS:-$HOME/nfs/sg1-dump/bios}"
hdd=""
eeprom=""
settle=60
boot=135
retries=2
all=0
dry_run=0
out=""
indices=""
while [ $# -gt 0 ]; do
  case "$1" in
    --iso=*) iso=${1#*=}; shift ;;
    --bios=*) bios=${1#*=}; shift ;;
    --hdd=*) hdd=${1#*=}; shift ;;
    --eeprom=*) eeprom=${1#*=}; shift ;;
    --settle=*) settle=${1#*=}; shift ;;
    --boot=*) boot=${1#*=}; shift ;;
    --retries=*) retries=${1#*=}; shift ;;
    --all) all=1; shift ;;
    --dry-run) dry_run=1; shift ;;
    --iso) iso="$2"; shift 2 ;;
    --bios) bios="$2"; shift 2 ;;
    --hdd) hdd="$2"; shift 2 ;;
    --eeprom) eeprom="$2"; shift 2 ;;
    --settle) settle="$2"; shift 2 ;;
    --boot) boot="$2"; shift 2 ;;
    --retries) retries="$2"; shift 2 ;;
    --*) echo "unknown option $1" >&2; exit 2 ;;
    *) if [ -z "$out" ]; then out="$1"; else indices="$indices $1"; fi; shift ;;
  esac
done
[ "$all" = 1 ] && indices="$indices $(seq 10 40 | tr '\n' ' ')"
[ -n "$out" ] && [ -n "$indices" ] || { echo "usage: missions.sh OUTDIR INDEX [INDEX ...] | --all" >&2; exit 2; }
if [ "$dry_run" = 1 ]; then
  echo "$(echo $indices | wc -w | tr -d ' ') indices, one boot each:"
  echo "$indices"
  exit 0
fi
mkdir -p "$out"
: > "$out/missions.tsv"  # fresh: one row per index for this invocation
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
[ -n "$hdd" ] || hdd="${WORK:-$HOME/.local/share/xemu/sg1}/probe/hdd.qcow2"
[ -n "$eeprom" ] || eeprom="${WORK:-$HOME/.local/share/xemu/sg1}/smoke1/eeprom.bin"

failed=0
for index in $indices; do
  dir="$out/$(printf 'index-%02d' "$index")"
  rm -rf "$dir"; mkdir -p "$dir"
  cp --reflink=auto "$hdd" "$dir/hdd.qcow2"
  args="--iso=$iso --bios=$bios --hdd=$dir/hdd.qcow2 --out=$dir --eeprom=$eeprom"
  args="$args --fuzz=10 --seconds=$boot --crop=480x520+760+210"
  set -- --step=shot=00-title --step=noise=3 --step=pad=a --step=wait=4
  i=0
  while [ "$i" -lt "$index" ]; do
    set -- "$@" --step=pad=down --step=wait=0.5
    i=$((i+1))
  done
  set -- "$@" --step=shot=01-highlight --step=pad=a --step=wait="$settle" --step=shot=02-mission --step=cpu=2
  attempt=1
  ok=0
  while [ "$attempt" -le "$retries" ]; do
    echo "index $index: attempt $attempt of $retries, booting, $index d-pad presses, then select"
    if ( cd "$repo" && DISPLAY="${DISPLAY:-:0}" XAUTHORITY="$XAUTHORITY" python3 tools/xemu/drive.py $args "$@" ) \
         > "$dir/run.log" 2>&1; then
      ok=1
      break
    fi
    attempt=$((attempt+1))
  done
  if [ "$ok" = 1 ]; then
    printf '%s\tPASS\t%s\t%s attempts\n' "$index" "$dir/02-mission.png" "$((attempt))" >> "$out/missions.tsv"
    echo "index $index: PASS after $attempt attempt(s) ($dir/02-mission.png)"
  else
    printf '%s\tFAIL\t%s\t%s attempts\n' "$index" "$dir/run.log" "$retries" >> "$out/missions.tsv"
    echo "index $index: FAIL after $retries attempt(s) ($dir/run.log)"
    failed=$((failed+1))
  fi
  pgrep -fc "[x]emu" >/dev/null 2>&1 && sleep 5 || true
done
echo "$(($# - 1)) mission(s) attempted" > /dev/null || true
[ "$failed" -eq 0 ]
