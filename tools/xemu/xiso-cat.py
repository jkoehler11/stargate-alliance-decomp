#!/usr/bin/env python3
"""Read one file out of an Xbox XDVDFS image -- the disc-identity check, without extract-xiso (PLAN 7.3).

`System/Default.ini`'s `Console=` line decides which console class the engine instantiates, and the harness boots
images that are *not* the dump's own tree: `$WORK/xemu/sg1.iso` (stock) and `$WORK/console-iso/game.iso` (the
`Console=GUI.ExtendedConsole` swap) are both 2,258,370,560 bytes, so size cannot tell them apart either.

**Do not answer the question with `grep` on the image.** `grep -ac "Console=GUI.ExtendedConsole" sg1.iso` answers
1 on the *stock* image, because a stray copy of that line sits at offset 1,881,354,812 -- a byte-string hit says
nothing about which file the engine reads. Read the file instead; `extract-xiso` is no longer installed on the
desktop and a `-c` extract of the whole disc is 2.2 GB.

    python3 tools/xemu/xiso-cat.py --iso $WORK/xemu/sg1.iso System/Default.ini
    python3 tools/xemu/xiso-cat.py --iso $WORK/xemu/sg1.iso --list System

The walk is the XDVDFS left-child / node / right-child directory tree, the same one `tools/xemu/map-index.py`
does: a directory entry's `left`/`right` are offsets into the directory's table in **4-byte words**, and the table
size of a subdirectory is its own entry's file size.
"""
import argparse
import struct
import sys

SEC = 2048
MAGIC = b"MICROSOFT*XBOX*MEDIA"


class Disc:
    def __init__(self, path):
        self.f = open(path, "rb")
        self.f.seek(32 * SEC)
        vd = self.f.read(SEC)
        if vd[:20] != MAGIC:
            sys.exit("%s: not an Xbox disc image (volume descriptor %r)" % (path, vd[:20]))
        self.root_sector, self.root_size = struct.unpack("<II", vd[0x14:0x1C])

    def _blob(self, sector, size):
        self.f.seek(sector * SEC)
        return self.f.read(size)

    def entries(self, sector, size):
        """{name: (start_sector, file_size, attrs)} for one directory table."""
        blob = self._blob(sector, size)
        out, stack = {}, [0]
        while stack:
            off = stack.pop()
            if off + 14 > len(blob):
                continue
            left, right, start, fsize = struct.unpack("<HHII", blob[off:off + 12])
            attrs, nlen = blob[off + 12], blob[off + 13]
            if nlen:
                out[blob[off + 14:off + 14 + nlen].decode("latin-1").lower()] = (start, fsize, attrs)
            if left:
                stack.append(left * 4)
            if right:
                stack.append(right * 4)
        return out

    def lookup(self, parts):
        """(start_sector, size, attrs) for a path, or None. Directories carry the `0x10` attribute."""
        sector, size = self.root_sector, self.root_size
        for i, name in enumerate(parts):
            hit = self.entries(sector, size).get(name.lower())
            if hit is None:
                return None
            start, fsize, attrs = hit
            if i == len(parts) - 1:
                return hit
            if not attrs & 0x10:
                return None
            sector, size = start, fsize
        return None

    def read(self, hit):
        start, fsize, _attrs = hit
        self.f.seek(start * SEC)
        return self.f.read(fsize)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--iso", required=True, help="the XDVDFS image")
    ap.add_argument("path", help="a file inside it, e.g. System/Default.ini")
    ap.add_argument("--list", action="store_true", help="the path is a directory: print its entries instead")
    args = ap.parse_args()
    parts = [p for p in args.path.replace("\\", "/").split("/") if p]
    disc = Disc(args.iso)
    hit = disc.lookup(parts)
    if hit is None:
        sys.exit("%s: not found in %s" % (args.path, args.iso))
    if args.list:
        for name in sorted(disc.entries(hit[0], hit[1])):
            print(name)
        return 0
    sys.stdout.write(disc.read(hit).decode("latin-1"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
