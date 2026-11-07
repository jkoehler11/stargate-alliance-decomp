#!/usr/bin/env python3
"""Locate `GNames` in one guest boot: a TArray triple in .data whose element 1 decodes as `ByteProperty`.

`names.py --find` scores every TArray-shaped triple in the section (15,384 of them) and each score costs stub
round-trips, so it takes tens of minutes. This is the narrow version: only triples whose `Num` is a plausible name
count are kept (a few dozen), and each is checked by reading its element 1 -- `ByteProperty` is index 1 in UE2's
reserved names -- so a hit is a positive identification, not a shape match.
"""
import struct
import sys

sys.path.insert(0, "/home/james/sg1/agent/sg1clone/tools/xemu")
import names  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 12519
DATA_VA, DATA_SIZE = 0x00598E00, 0x226BD4

guest = names.Guest("127.0.0.1", PORT)
try:
    blob = names.read_section(guest, DATA_VA, DATA_SIZE)
    print("read %d byte(s) of .data" % len(blob))
    kept = []
    for off in range(0, len(blob) - 12, 4):
        data, num, cap = struct.unpack_from("<III", blob, off)
        if not (0x10000 <= data < 0x8000000) or data % 4:
            continue
        if not (4096 <= num <= 400000) or cap < num or cap > num * 8:
            continue
        kept.append((DATA_VA + off, data, num, cap))
    print("%d triple(s) with a plausible name count" % len(kept))
    for addr, data, num, cap in kept:
        try:
            p1 = struct.unpack("<I", guest.read(data + 4, 4))[0]
            got = names.entry(guest, p1)
        except Exception:
            continue
        if not got or got[3] != "ByteProperty":
            continue
        p16 = struct.unpack("<I", guest.read(data + 64, 4))[0]
        g16 = names.entry(guest, p16)
        print("GNAMES %#010x data=%#010x num=%d max=%d  1=%r 16=%r"
              % (addr, data, num, cap, got[3], g16[3] if g16 else None))
finally:
    guest.close()
