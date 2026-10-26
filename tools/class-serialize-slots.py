#!/usr/bin/env python3
"""Read every class's `Serialize` override out of the image's class tables (PLAN 3.7 groundwork).

The per-class tables in `.rdata` (`inventory/xbe_class_table_names.csv`, 492 tables) are vtables: the slots that
differ between a class and its parent are the virtuals it overrides. Slot 11 is `Serialize` — established by
decompiling `UStruct`'s, which contains the string **"Script serialization mismatch: Got %i, expected %i"** and walks
the script's token stream to check it consumed exactly the stored size (`0x000392b0`, exported under `$WORK`).

That makes the bytecode's location in a `UFunction` payload a decidable question with the reader in hand, and it
gives this project something cheaper first: a table of which classes override their serializer and where the code is.

    python3 tools/class-serialize-slots.py --xbe DUMP/.../default.xbe -o inventory
    python3 tools/class-serialize-slots.py --xbe ... --class Struct --walk

Reads only the committed class tables plus the image, so it runs anywhere the dump is.
"""
import argparse
import bisect
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERIALIZE_SLOT = 11


def sections(path):
    out = []
    for line in open(path):
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        try:
            out.append((parts[0], int(parts[1], 16), int(parts[3], 16), int(parts[4], 16)))
        except (ValueError, IndexError):
            continue
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe", required=True)
    ap.add_argument("--sections", default=os.path.join(ROOT, "build", "ghidra", "sections.tsv"))
    ap.add_argument("--tables", default=os.path.join(ROOT, "inventory", "xbe_class_table_names.csv"))
    ap.add_argument("--symbols", default=os.path.join(ROOT, "symbols", "xbox_functions.csv"))
    ap.add_argument("-o", "--out")
    ap.add_argument("--class", dest="cls", help="print one class's slot table")
    ap.add_argument("--walk", action="store_true", help="with --class, print every slot")
    args = ap.parse_args()

    image = open(args.xbe, "rb").read()
    secs = sections(args.sections)
    names = {}
    for r in csv.DictReader(open(args.tables)):
        names[int(r["table_va"], 16)] = r["class"]
    starts, syms = [], {}
    for r in csv.DictReader(open(args.symbols)):
        va = int(r["address"], 16)
        starts.append(va)
        syms[va] = (r["name"], int(r["size"]))
    starts.sort()

    def file_offset(va):
        for _name, sva, foff, size in secs:
            if sva <= va < sva + size:
                return foff + (va - sva)
        return None

    def owner(va):
        i = bisect.bisect_right(starts, va) - 1
        if i >= 0 and starts[i] <= va < starts[i] + syms[starts[i]][1]:
            return syms[starts[i]][0]
        return ""

    rows = []
    for va, cls in sorted(names.items(), key=lambda kv: kv[1]):
        off = file_offset(va)
        if off is None:
            continue
        slots = struct.unpack_from("<32I", image, off)
        rows.append({"class": cls, "table_va": "0x%08x" % va,
                     "serialize": "0x%08x" % slots[SERIALIZE_SLOT],
                     "symbol": owner(slots[SERIALIZE_SLOT])})
        if args.cls and cls == args.cls:
            if args.walk:
                for i, s in enumerate(slots):
                    print("  slot %2d: %#010x %s" % (i, s, owner(s)))
            print("%s: Serialize at %#010x (%s)" % (cls, slots[SERIALIZE_SLOT], owner(slots[SERIALIZE_SLOT])))

    if args.out and not args.cls:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "class_serialize_slots.csv")
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["class", "table_va", "serialize", "symbol"], lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print("wrote %s" % path)
    distinct = collections_sorted = sorted({r["serialize"] for r in rows})
    print("%d classes, %d distinct Serialize entry points" % (len(rows), len(distinct)))
    for addr in distinct[:12]:
        who = [r["class"] for r in rows if r["serialize"] == addr][:4]
        print("  %s  %s%s" % (addr, ", ".join(who), " …" if len(who) == 4 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
