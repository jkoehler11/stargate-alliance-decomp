#!/usr/bin/env python3
"""Find the per-class tables of code pointers in the XBE and the slot that dispatches a target.

The build dispatches `UObject::ProcessEvent` instead of calling it (PLAN 4.3 / `docs/UNKNOWNS.md`
U8): no `call` to its address exists, but hundreds of data pointers to it do. This tool turns that
into a structure. Given a target address it scans the image for dwords equal to it, treats each hit
as living at a fixed slot of a table starting `slot*4` bytes earlier, and keeps the hit only when
**every** slot before it holds a function entry from the symbol map. A table row is therefore
evidence, not a scan result: twenty-one consecutive known function entries followed by the target.

    python3 tools/class-tables.py DUMP/default.xbe -o inventory
    python3 tools/class-tables.py DUMP/default.xbe --target 0x00048bf0   # ProcessInternal

Table extent is measured against the next table start (the tables are packed) or, for the last
one, against the end of the run of function entries that holds it.
"""
import argparse
import csv
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xbe import Xbe  # noqa: E402

DEFAULT_SLOT = 21  # measured: ProcessEvent is at +0x54 in every table (docs/NATIVE_VM.md)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xbe")
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--symbols", default="symbols/xbox_functions.csv", help="for naming only")
    ap.add_argument("--target", default="0x0004bf20", help="dispatched function (default ProcessEvent)")
    ap.add_argument("--slot", type=int, default=DEFAULT_SLOT,
                    help="slot the target must occupy; hits whose prefix is not all function "
                         "entries are dropped")
    args = ap.parse_args()

    xbe = Xbe(args.xbe)
    target = int(args.target, 16)
    names = {}
    with open(args.symbols) as f:
        for row in csv.DictReader(f):
            names[int(row["address"], 16)] = row["name"]

    def is_code(v):
        """A table slot holds a code pointer: aligned and inside a section flagged executable.
        The XBE flags mark most sections executable (including `.rdata`), so this is used only to
        validate the prefix, never to choose which sections to scan."""
        return v % 4 == 0 and any(x.executable and x.va <= v < x.va + x.vsize for x in xbe.sections)

    hits = []
    for sec in xbe.sections:
        n = min(sec.fsize, sec.vsize) // 4
        for i in range(args.slot, n):
            va = sec.va + 4 * i
            off = sec.foff + 4 * i
            if struct.unpack_from("<I", xbe.data, off)[0] != target:
                continue
            prefix = [struct.unpack_from("<I", xbe.data, sec.foff + 4 * (i - k))[0]
                      for k in range(1, args.slot + 1)]
            if all(is_code(p) for p in prefix):
                hits.append((sec, i, va - 4 * args.slot))
    if not hits:
        sys.exit("no tables found for %s at slot %d" % (args.target, args.slot))

    starts = sorted({h[2] for h in hits})
    by_start = {h[2]: h for h in hits}

    cache = {}

    def entries(start):
        if start in cache:
            return cache[start]
        sec, i, _ = by_start[start]
        n = min(sec.fsize, sec.vsize) // 4
        cache[start] = [struct.unpack_from("<I", xbe.data, sec.foff + 4 * (i - args.slot + k))[0]
                        for k in range(0, min(n - (i - args.slot), 4096))]
        return cache[start]

    lengths = {}
    for k, s in enumerate(starts):
        ent = entries(s)
        n = 0
        while n < len(ent) and is_code(ent[n]):
            n += 1
        # The tables are packed, so a neighbour start bounds the run of code pointers that spills
        # over from the next table.
        if k + 1 < len(starts):
            n = min(n, (starts[k + 1] - s) // 4)
        lengths[s] = n

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "xbe_class_tables.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["table_va", "section", "slots", "target_slot", "target_offset",
                    "slot0", "slot3", "evidence"])
        for s in starts:
            sec = by_start[s][0]
            ent = entries(s)
            w.writerow(["0x%08x" % s, sec.name, lengths[s], args.slot, "0x%02x" % (4 * args.slot),
                        "0x%08x" % ent[0], "0x%08x" % ent[3],
                        "0x%08x at slot %d, %d function entries before it"
                        % (target, args.slot, args.slot)])

    slots_path = os.path.join(args.out, "xbe_class_table_slots.csv")
    maxslots = max(lengths.values())
    with open(slots_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["slot", "byte_offset", "tables", "distinct", "most_common", "count"])
        for slot in range(maxslots):
            values = [entries(s)[slot] for s in starts
                      if slot < min(lengths[s], len(entries(s)))]
            if not values:
                continue
            counts = {}
            for v in values:
                counts[v] = counts.get(v, 0) + 1
            best = max(counts.items(), key=lambda kv: kv[1])
            w.writerow([slot, "0x%02x" % (4 * slot), len(values), len(counts),
                        "0x%08x" % best[0], best[1]])

    print("tables: %d (%.1f KB of code pointers), %s at slot %d (byte 0x%02x)"
          % (len(starts), (starts[-1] - starts[0]) / 1024.0, names.get(target, args.target),
             args.slot, 4 * args.slot))
    print("spread: 0x%08x - 0x%08x; slots per table %d..%d; slot 0 %s in %d of %d tables"
          % (starts[0], starts[-1], min(lengths.values()), maxslots,
             names.get(entries(starts[0])[0], "?"),
             sum(1 for s in starts if entries(s)[0] == entries(starts[0])[0]), len(starts)))
    print("wrote %s and %s" % (path, slots_path))


if __name__ == "__main__":
    main()
