#!/bin/sh
# Boot the dump in xemu and capture evidence (PLAN 7.1/7.2 running on the desktop).
#
# xemu needs three things this project cannot supply:
#   - MCPX boot ROM  (mcpx_1.0.bin, 512 bytes)      } firmware dumped from the user's own console
#   - flash ROM      (complex_4627.bin or similar)  }
#   - a hard disk image (xbox_hdd.qcow2; xemu can create a blank one)
# Point BIOS_DIR at a directory holding those (or pass the files explicitly) and keep it out of git.
#
# Usage: boot-test.sh GAME_DIR BIOS_DIR [SECONDS] [OUT_DIR]
#   GAME_DIR  directory containing default.xbe and System/ (a copy of the dump, not the repo)
#   BIOS_DIR  directory with mcpx_1.0.bin and a flash ROM (complex_*.bin / *.bin 256K/512K)
#   SECONDS   how long to let it run before the screenshot (default 90)
set -eu
game="${1:?usage: boot-test.sh GAME_DIR BIOS_DIR [SECONDS] [OUT_DIR]}"
bios="${2:?usage: boot-test.sh GAME_DIR BIOS_DIR [SECONDS] [OUT_DIR]}"
secs="${3:-90}"
out="${4:-$PWD/build/xemu}"
xemu="${XEMU:-$HOME/tools/xemu.AppImage}"

[ -x "$xemu" ] || { echo "xemu not found at $xemu (set XEMU)" >&2; exit 2; }
[ -f "$game/default.xbe" ] || { echo "no default.xbe in $game" >&2; exit 2; }
[ -d "$bios" ] || { echo "no BIOS directory at $bios" >&2; exit 2; }

mcpx=$(find "$bios" -maxdepth 1 -iname "mcpx*.bin" | head -1)
flash=$(find "$bios" -maxdepth 1 -iname "*.bin" ! -iname "mcpx*" -size +100k | head -1)
[ -n "$mcpx" ] && [ -n "$flash" ] || { echo "need mcpx*.bin and a 256K/512K flash ROM in $bios" >&2; exit 2; }

mkdir -p "$out"
hdd="$bios/xbox_hdd.qcow2"     # created by xemu on first boot if absent
echo "xemu:   $xemu"
echo "mcpx:   $mcpx"
echo "flash:  $flash"
echo "hdd:    $hdd"
echo "game:   $game"
echo "output: $out"

# 128 MB (debug-kit memory) and the debug-friendly settings the plan asks for (7.1).
APPIMAGE_EXTRACT_AND_RUN=1 "$xemu" \
    -config_path "$out/xemu.toml" \
    -dvd_path "$game" \
    -mcpx_path "$mcpx" \
    -flashrom_path "$flash" \
    -hdd_path "$hdd" \
    -memory 128 \
    -serial "$out/serial.log" \
    > "$out/xemu.log" 2>&1 &
pid=$!
sleep "$secs"
if kill -0 "$pid" 2>/dev/null; then
    # A screenshot needs the guest's own framebuffer capture; xemu writes one on request via the
    # menu, which headless runs cannot do. The serial log and the process staying alive are the
    # evidence a headless run can produce.
    echo "still running after ${secs}s (pid $pid) -- see $out/xemu.log"
    kill "$pid" 2>/dev/null || true
    status=running
else
    wait "$pid" && status=exited-clean || status=failed
fi
echo "status: $status"
tail -20 "$out/xemu.log" 2>/dev/null || true
[ "$status" = "running" ] || [ "$status" = "exited-clean" ]
