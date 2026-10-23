#!/bin/sh
# Launch the prototype interactively in xemu with the verified working configuration.
#
# Use this when input automation is not enough: the menu needs a real button press, and a human at
# the keyboard is faster than fighting SDL input injection (see docs/XEMU.md, "Input automation").
#
# Usage: play.sh GAME_DIR BIOS_DIR [XISO]
#   GAME_DIR  the extracted dump (for the size sanity check only)
#   BIOS_DIR  directory with mcpx_1.0.bin and the *debug* flash (bios_debug_*.bin, Complex_debug*)
#   XISO      disc image; default: build one with tools/xemu/build-xiso.sh
set -eu
game="${1:?usage: play.sh GAME_DIR BIOS_DIR [XISO]}"
bios="${2:?usage: play.sh GAME_DIR BIOS_DIR [XISO]}"
iso="${3:-}"
xemu="${XEMU:-$HOME/tools/xemu.AppImage}"
work="${WORK:-$HOME/.local/share/xemu/sg1}"

[ -x "$xemu" ] || { echo "xemu not found (set XEMU)" >&2; exit 2; }
[ -f "$game/default.xbe" ] || { echo "no default.xbe in $game" >&2; exit 2; }
[ -n "$iso" ] || { echo "pass the XISO path, or build it: tools/xemu/build-xiso.sh \"$game\" /tmp/sg1.iso" >&2; exit 2; }
[ -f "$iso" ] || { echo "no such ISO: $iso" >&2; exit 2; }

mcpx=$(ls "$bios"/mcpx*.bin 2>/dev/null | head -1)
flash=$(ls "$bios"/*[Dd]ebug*.bin 2>/dev/null | head -1)
[ -n "$mcpx" ] || { echo "no mcpx*.bin in $bios" >&2; exit 2; }
[ -n "$flash" ] || { echo "no debug flash (*Debug*.bin) in $bios -- a retail flash refuses this build" >&2; exit 2; }

mkdir -p "$work"
[ -f "$work/hdd.qcow2" ] || cp "${HDD_IMAGE:-$bios/xbox_hdd.qcow2}" "$work/hdd.qcow2" 2>/dev/null || true
cat > "$work/xemu.toml" <<TOML
[general]
show_welcome = false
screenshot_dir = '$work/shots'
[sys]
mem_limit = '128'
[sys.files]
bootrom_path = '$mcpx'
flashrom_path = '$flash'
eeprom_path = '$work/eeprom.bin'
hdd_path = '$work/hdd.qcow2'
dvd_path = '$iso'
TOML

echo "bios:  $flash"
echo "iso:   $iso"
echo "work:  $work"
echo "Press Continue on the main menu; xemu saves a screenshot with F12 into $work/shots."
exec "$xemu" -config_path "$work/xemu.toml"
