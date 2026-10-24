#!/bin/sh
# Build an XISO from a dump directory so xemu can mount it as a DVD.
#
# Two things bite every time:
#  1. the dump copy must be world-readable (a dump copied over ssh/scp often is not), or
#     extract-xiso fails with "open error: default.xbe Permission denied" and still writes an
#     empty ISO;
#  2. debug-kit builds carry a zeroed XBE certificate (media types/region), which the retail
#     kernel refuses to boot. This script prints those fields so the failure is visible before
#     anyone blames the emulator.
#
# Usage: build-xiso.sh GAME_DIR OUT.iso
set -eu
game="${1:?usage: build-xiso.sh GAME_DIR OUT.iso}"
out="${2:?usage: build-xiso.sh GAME_DIR OUT.iso}"
[ -f "$game/default.xbe" ] || { echo "no default.xbe in $game" >&2; exit 2; }

ex=""
for c in extract-xiso /tmp/extract-xiso/build/extract-xiso; do command -v "$c" >/dev/null 2>&1 && ex="$c" && break; [ -x "$c" ] && ex="$c" && break; done
if [ -z "$ex" ]; then
    echo "building extract-xiso" >&2
    tmp=$(mktemp -d)
    git clone --depth 1 -q https://github.com/XboxDev/extract-xiso "$tmp/src"
    cmake -S "$tmp/src" -B "$tmp/src/build" -DCMAKE_BUILD_TYPE=Release >/dev/null
    cmake --build "$tmp/src/build" -j "$(nproc)" >/dev/null
    ex="$tmp/src/build/extract-xiso"
fi

chmod -R a+rX "$game" 2>/dev/null || true
rm -f "$out"
# extract-xiso 2.7 takes the output name as a positional after the directory: `-c <dir> [name]`.
# The older `-o <path>` form is silently ignored by that version and leaves no image behind.
"$ex" -q -c "$game" "$out" 2>/dev/null || "$ex" -q -c "$game" -o "$out" 2>/dev/null || true
[ -s "$out" ] || { echo "extract-xiso produced no image (see the -q output by re-running without -q)" >&2; exit 3; }
echo "iso: $out ($(stat -c %s "$out") bytes)"

python3 - "$out" <<'PY'
import struct, sys
path = sys.argv[1]
with open(path, "rb") as fh:
    head = fh.read(32 * 1024 * 1024)
i = head.find(b"XBEH")
if i < 0:
    print("no XBEH in the image header window"); raise SystemExit(0)
base = struct.unpack_from("<I", head, i + 0x104)[0]
cert = struct.unpack_from("<I", head, i + 0x118)[0]
off = i + (cert - base)
media, region = struct.unpack_from("<2I", head, off + 0x134)
version = struct.unpack_from("<I", head, off + 0x144)[0]
titleid = struct.unpack_from("<I", head, off + 0x08)[0]
name = head[off + 0x0C:off + 0x34].decode("utf-16-le", "replace").rstrip("\x00")
print(f"xbe: titleid=0x{titleid:08x} name={name!r} media=0x{media:08x} region=0x{region:08x} version=0x{version:08x}")
if media == 0:
    print("media types are zero: this is a debug-kit build. A retail flash ROM refuses to boot it;")
    print("run it under a debug BIOS, because patching these fields invalidates the signature the")
    print("retail kernel checks (see docs/XEMU.md).")
PY
