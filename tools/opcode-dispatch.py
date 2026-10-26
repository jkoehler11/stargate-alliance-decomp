#!/usr/bin/env python3
"""Extract the one-opcode-at-a-time dispatch table from the image's own script walker (PLAN 3.7).

`UStruct::Serialize` (see `docs/NATIVE_VM.md`) rebuilds the script buffer and then walks it with a virtual at vtable
`+0xa0`. For `Struct`/`Function`/`Class` that virtual is `0x000398e0`, and its body starts by reading **one opcode byte**
and jumping through a table at `0x00039e44`:

    puVar7 = *(byte *)(buf + pos);  pos++;
    if (0x6f < puVar7) { do { ... } while (nested_walk(...) != 0x16); }   // 0x16 = EX_EndFunctionParms
    if (0x5f < puVar7) { ... }
    switch (puVar7) { ... }

The table covers opcodes `0x00..0x1e`, **one handler per opcode** (31 entries, all inside the walker); entries past
`0x1e` are not code at all — the table simply ends and `.rdata` continues (`0xcccccccc`, then ASCII). Opcodes at or
above `0x1f` are therefore skipped by the range checks, not by the table.

This tool finds that table by looking for the `jmp [reg*4 + disp32]` that indexes it, stops at the first entry outside
the walker, and writes `opcode, handler_va, handler_size` — the scaffolding a disassembler needs, without pretending to
know each handler's operand width (that has to be read out of the handler, and is not guessed here).

    python3 tools/opcode-dispatch.py --xbe DUMP/.../default.xbe -o inventory

Deterministic: the walker address comes from the committed slot table, so a rerun reproduces the CSV byte for byte.
"""
import argparse
import bisect
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe", required=True)
    ap.add_argument("--slots", default=os.path.join(ROOT, "inventory", "class_serialize_slots.csv"))
    ap.add_argument("--symbols", default=os.path.join(ROOT, "symbols", "xbox_functions.csv"))
    ap.add_argument("-o", "--out")
    args = ap.parse_args()

    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from xbe import Xbe

    xbe = Xbe(args.xbe)

    def read_va(va, size):
        for s in xbe.sections:
            if s.va <= va < s.va + s.fsize:
                off = s.foff + (va - s.va)
                return xbe.data[off:off + size]
        return b""

    walkers = {}
    for r in csv.DictReader(open(args.slots)):
        if r.get("script_walk"):
            walkers.setdefault(int(r["script_walk"], 16), []).append(r["class"])
    # the struct hierarchy's walker is the one to read; fall back to the most-shared entry point
    target = 0x000398E0 if 0x000398E0 in walkers else max(walkers, key=lambda k: len(walkers[k]))
    print("walker %#010x shared by %d classes" % (target, len(walkers.get(target, []))))

    sizes = {}
    for r in csv.DictReader(open(args.symbols)):
        sizes[int(r["address"], 16)] = int(r["size"])
    starts = sorted(sizes)
    i = bisect.bisect_right(starts, target) - 1
    end = starts[i] + sizes[starts[i]]

    code = read_va(target, min(end - target, 0x2000))
    sites = []
    for i in range(len(code) - 6):
        if code[i] == 0xFF and code[i + 1] == 0x24 and (code[i + 2] & 0xC7) == 0x85:
            sites.append((target + i, struct.unpack_from("<i", code, i + 3)[0]))
    if not sites:
        print("no jump table found in the walker")
        return 1

    rows = []
    for site, table in sites:
        raw = read_va(table, 4 * 0x100)
        entries = struct.unpack_from("<%dI" % (len(raw) // 4), raw, 0)
        n = 0
        for e in entries:
            if not (target <= e < end):
                break
            n += 1
        print("switch at %#010x, table at %#010x: %d entries, all inside the walker" % (site, table, n))
        for op in range(n):
            handler = entries[op]
            j = bisect.bisect_right(starts, handler) - 1
            rows.append({"opcode": "0x%02x" % op, "handler": "0x%08x" % handler,
                         "handler_size": sizes.get(starts[j], 0) if j >= 0 else 0,
                         "symbol": ""})

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "opcode_dispatch.csv")
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["opcode", "handler", "handler_size", "symbol"], lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print("wrote %s (%d opcodes)" % (path, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
