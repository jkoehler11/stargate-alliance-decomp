#!/usr/bin/env python3
"""List the libraries the XBE was linked against (PLAN 2.6).

An XBE keeps a table of the libraries it is linked with: eight bytes of name, then major, minor,
build and flags as 16-bit values. This build has **no non-kernel import table** (the header's import
address and count are 0), because those libraries are linked statically -- which is why their code
shows up as whole sections (`XNET`, `XONLINE`, `D3D`, `D3DX`, `XGRPH`, `DSOUND`, `XPP`, `DOLBY`) and
why no import thunk exists to name.

    python3 tools/xbe-libraries.py DUMP/default.xbe -o inventory

Only names, versions and flags are emitted: an ordinal-to-name table would be SDK-derived and is not
this repository's to carry (docs/UNKNOWNS.md U9).
"""
import argparse
import csv
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xbe import Xbe  # noqa: E402

NAME_LEN = 8
ENTRY = 16


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xbe")
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--table", default="0x164", help="header field holding the library table address")
    ap.add_argument("--count", default="0x160", help="header field holding the entry count")
    args = ap.parse_args()

    xbe = Xbe(args.xbe)
    d = xbe.data
    field = lambda va: struct.unpack_from("<I", d, int(va, 16) - xbe.base)[0] if int(va, 16) >= xbe.base else struct.unpack_from("<I", d, int(va, 16))[0]
    table = field(args.table)
    count = field(args.count)
    if not (xbe.base <= table < xbe.base + xbe.image_size) or count > 64:
        sys.exit("library table does not look like one: address %#x count %d" % (table, count))

    rows = []
    for i in range(count):
        off = table - xbe.base + i * ENTRY
        name = d[off:off + NAME_LEN].split(b"\0")[0].decode("latin1", "replace")
        major, minor, build, flags = struct.unpack_from("<HHHH", d, off + NAME_LEN)
        if not name.isprintable() or not name:
            sys.exit("entry %d has no name: the table layout assumption is wrong" % i)
        rows.append([name, major, minor, build, "0x%04x" % flags])

    # Which section each library's code occupies, where the name makes that certain. A library linked
    # into .text has no section of its own, and that is reported as such rather than guessed.
    sections = {s.name for s in xbe.sections}
    exact = {"XNET": "XNET", "XONLINE": "XONLINE", "D3DX8": "D3DX", "D3D8": "D3D",
             "XGRAPHC": "XGRPH", "DSOUND": "DSOUND"}
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "xbe_libraries.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["library", "major", "minor", "build", "flags", "section",
                    "section_named_from_library"])
        for name, major, minor, build, flags in rows:
            sec = exact.get(name, ".text" if name in ("XAPILIB", "LIBC", "LIBCP", "UIX", "XKBD") else "")
            w.writerow([name, major, minor, build, flags, sec, "yes" if name in exact else
                        ("in .text" if sec else "not identified")])
    print("%d libraries; table at %#x; sections: %s" % (len(rows), table, ", ".join(sorted(sections))))
    print("wrote %s" % path)


if __name__ == "__main__":
    main()
