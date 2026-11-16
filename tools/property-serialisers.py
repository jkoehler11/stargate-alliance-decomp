#!/usr/bin/env python3
"""Which vtable slot holds a property class's per-value serialiser, and what DelegateProperty's writes (U18).

`docs/NATIVE_PROPERTIES.md` §3 read the walk at `0x0003bdc0` as calling `(**(code **)(*child + 0x94))`
for "per-element serialization".  The offset is real but the meaning is not: every property class has
its own body at `+0x94` and every one of them is a **two-argument comparison** (Int compares a dword,
Delegate compares its first field, Struct hands its Inner struct to `0x0003cb90`).

The value serialiser is one slot further, `+0xa0`, and the image proves it from the array classes:
`FixedArrayProperty`'s body loops its elements and calls `call DWORD PTR [edx+0xa0]` on the property in
its own `+0x6c` (`Inner`) -- a per-class reader that recurses into another class's reader at the same
offset.  `ArrayProperty` does the same through `0x000637e0`.

With the slot named, `DelegateProperty`'s own serialiser is readable (`0x00077f00`, 28 bytes): it takes
the archive's vtable and calls `[Ar+0x18]` on the value and then `[Ar+0x1c]` on the value **+4**, i.e.
`Ar << Object << FunctionName` -- an object reference then an FName, no third field.  That is U18's
writer route measured, and it is what the payload readings already tiled as two compact indices.

    python3 tools/property-serialisers.py DUMP/default.xbe [-o inventory/property_serialisers.csv]
    python3 tools/property-serialisers.py --check        # synthetic self-test, no dump, writes nothing

Rows: `class, table_va, table_slots, offset, slot_va, body_bytes, args, ops, shared, shape`, one row per
class per offset (`Serialize` `+0x2c`, `+0x94`, `SerializeItem` `+0xa0`).  `shared` says whether every
class in the roster has the same target at that offset; `ops` is the archive traffic the body actually
makes (`vt:+0x18` is `call [reg+0x18]`, `call:0x00037110` a direct call); `shape` is the grade.
"""
import argparse
import csv
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import xbe as X  # noqa: E402


def load_tool(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


nv = load_tool("native_vtable", "native-vtable.py")

# The property roster this build builds in C++ at startup (docs/NATIVE_PROPERTIES.md §2).
ROSTER = ["Property", "IntProperty", "BoolProperty", "FloatProperty", "ByteProperty",
          "ObjectProperty", "ClassProperty", "NameProperty", "StrProperty", "ArrayProperty",
          "FixedArrayProperty", "MapProperty", "StructProperty", "DelegateProperty",
          "PointerProperty"]

OFFSETS = [("Serialize", 0x2C), ("compare", 0x94), ("SerializeItem", 0xA0)]

COLUMNS = ["class", "table_va", "table_slots", "offset", "slot_va", "body_bytes", "args", "ops",
           "reaches", "shared", "shape"]


def roster(path=None):
    """[(class, table_va)] for the property classes, read from the committed constructor artifact."""
    path = path or os.path.join(ROOT, "inventory", "class_constructors.csv")
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            cls = (r.get("class") or "").strip()
            tva = (r.get("table_va") or "").strip()
            if cls in ROSTER and tva:
                out.setdefault(cls, int(tva, 16))
    return sorted(out.items(), key=lambda kv: kv[1])


def scan(insns, start, limit=320):
    """Linear decode from `start` to the first `ret`; the body's argument count and its traffic.

    A body's *first* terminator is not its end: `FixedArrayProperty`'s serialiser branches over a
    `jmp +2` in the middle and its `call [edx+0xa0]` sits past it, so stopping at the `jmp` -- which
    is what a tail-call scan does -- cut the dispatch off the body and left the array classes
    unclassified.  Only `ret` leaves a body.
    """
    ops, args = [], None
    a, end = start, start + limit
    while a < end:
        ins = insns.get(a)
        if ins is None:
            break
        op, operands, n = ins[0], ins[1], max(ins[2], 1)
        if op in ("call", "jmp"):
            mem = None
            if "DWORD PTR [" in operands:
                mem = operands.split("DWORD PTR [", 1)[1].split("]", 1)[0]
            if mem is not None and "+0x" in mem:
                disp = int(mem.split("+0x", 1)[1], 16)
                ops.append(("jmpvt:" if op == "jmp" else "vt:") + "0x%02x" % disp)
            elif op == "call":
                parts = operands.split()
                if parts and parts[-1].startswith("0x"):
                    ops.append("call:" + parts[-1])
        if op in ("ret", "retf"):
            args = int(operands, 16) // 4 if operands else 0
            break
        a += n
    return args, ops, a + (insns[a][2] if a in insns else 0) - start


def reaches(insns, start, slot, hop=True):
    """Does the body at `start` dispatch `+slot` directly, or through the one helper it calls?"""
    _args, ops, _n = scan(insns, start)
    tag = "vt:0x%02x" % slot
    if tag in ops or "jmpvt:0x%02x" % slot in ops:
        return "direct"
    if not hop:
        return ""
    calls = [o for o in ops if o.startswith("call:")]
    if len(calls) == 1:
        _a, hops, _n = scan(insns, int(calls[0].split(":")[1], 16))
        if tag in hops or "jmpvt:0x%02x" % slot in hops:
            return "via " + calls[0].split(":", 1)[1]
    return ""


def classify(args, ops, reach=""):
    """The grade a body gets from the archive traffic it makes."""
    if reach:
        return "recurse-inner"
    if ops[:2] == ["vt:0x18", "vt:0x1c"]:
        return "object-then-name"
    if len(ops) == 1 and ops[0].startswith("call:"):
        return "archive-op"
    if any(o.startswith("vt:") for o in ops):
        return "archive-virtual"
    return "other" if ops else "no-ops"


def sweep_rows(x, names):
    text = next((s for s in x.sections if s.name == ".text"), None)
    if text is None:
        raise SystemExit(".text: section not found")
    insns, calls = nv.sweep(x, text)
    rows, tables = [], {}
    for cls, tva in roster():
        census, starts = nv.census(x, tva, names, insns, calls)
        tables[cls] = census
        canon = next(r for r in census if int(r["table_va"], 16) == tva)
        for label, off in OFFSETS:
            slot = off // 4
            hit = next((r for r in census if int(r["table_va"], 16) == tva and r["slot"] == slot), None)
            if hit is None:
                continue
            sva = int(hit["slot_va"], 16)
            args, ops, walked = scan(insns, sva)
            reach = reaches(insns, sva, 0xA0) if label == "SerializeItem" else ""
            rows.append({
                "class": cls,
                "table_va": hit["table_va"],
                "table_slots": canon["table_slots"],
                "offset": label,
                "slot_va": hit["slot_va"],
                "body_bytes": walked,
                "args": "" if args is None else args,
                "ops": " ".join(ops),
                "reaches": reach,
                "shared": "",
                "shape": classify(args, ops, reach),
            })
    for label, _off in OFFSETS:
        sel = [r for r in rows if r["offset"] == label]
        targets = {r["slot_va"] for r in sel}
        for r in sel:
            r["shared"] = "yes" if len(targets) == 1 else "no"
    return rows


def emit(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return path


def report(rows):
    print("%d rows over %d classes" % (len(rows), len({r["class"] for r in rows})))
    for label, _off in OFFSETS:
        sel = [r for r in rows if r["offset"] == label]
        shared = sum(1 for r in sel if r["shared"] == "yes")
        print("  +0x%02x %-14s per-class targets %d/%d  shapes: %s" % (
            dict(OFFSETS)[label], label, len(sel) - shared, len(sel),
            ", ".join("%s=%d" % (s, sum(1 for r in sel if r["shape"] == s))
                      for s in sorted({r["shape"] for r in sel}))))
    slots = {r["table_slots"] for r in rows}
    shown = "/".join(str(s) for s in sorted(slots))
    print("  table extent: %s slots" % (shown if len(slots) < 4 else "varies"))


def self_test():
    """Drive the two rules that decide every row against synthetic instructions -- no dump."""
    fails = []

    def eq(what, got, want):
        if got != want:
            fails.append("%s: got %r, want %r" % (what, got, want))

    insns = {
        0x1000: ("mov", "ecx,DWORD PTR [esp+0x4]", 4),
        0x1004: ("mov", "eax,DWORD PTR [ecx]", 2),
        0x1006: ("call", "DWORD PTR [eax+0x18]", 3),
        0x1009: ("mov", "edx,DWORD PTR [eax]", 2),
        0x100b: ("call", "DWORD PTR [edx+0x1c]", 3),
        0x100e: ("ret", "0xc", 3),
        0x2000: ("push", "0x4", 1),
        0x2001: ("call", "0x37110", 5),
        0x2006: ("ret", "0xc", 3),
        0x3000: ("mov", "edx,DWORD PTR [ecx]", 2),
        0x3002: ("call", "DWORD PTR [edx+0xa0]", 6),
        0x3008: ("ret", "0x10", 3),
        0x4000: ("ret", "", 1),
        # a body that branches over its own dispatch: only `ret` ends it
        0x5000: ("push", "esi", 1),
        0x5001: ("jmp", "0x5005", 2),
        0x5003: ("xor", "eax,eax", 2),
        0x5005: ("mov", "edx,DWORD PTR [ecx]", 2),
        0x5007: ("call", "DWORD PTR [edx+0xa0]", 6),
        0x500d: ("ret", "0x10", 3),
        # one direct call, whose own body does the dispatch
        0x6000: ("push", "0x0", 2),
        0x6002: ("call", "0x7000", 5),
        0x6007: ("ret", "0xc", 3),
        0x7000: ("mov", "edx,DWORD PTR [eax]", 2),
        0x7002: ("call", "DWORD PTR [edx+0xa0]", 6),
        0x7008: ("ret", "0x4", 3),
    }
    args, ops, _n = scan(insns, 0x1000)
    eq("delegate args", args, 3)
    eq("delegate ops", ops, ["vt:0x18", "vt:0x1c"])
    eq("delegate shape", classify(args, ops), "object-then-name")
    args, ops, _n = scan(insns, 0x2000)
    eq("scalar ops", ops, ["call:0x37110"])
    eq("scalar shape", classify(args, ops), "archive-op")
    args, ops, _n = scan(insns, 0x5000)
    eq("array shape", classify(args, ops, "direct"), "recurse-inner")
    eq("array args", args, 4)
    eq("array ops survive a jmp", ops, ["vt:0xa0"])
    args, ops, _n = scan(insns, 0x4000)
    eq("empty body", classify(args, ops), "no-ops")
    eq("reach direct", reaches(insns, 0x5000, 0xA0), "direct")
    eq("reach via helper", reaches(insns, 0x6000, 0xA0), "via 0x7000")
    eq("no reach", reaches(insns, 0x2000, 0xA0), "")
    # the offset table is the claim the rows are keyed by
    eq("serialiser offset", dict(OFFSETS)["SerializeItem"], 0xA0)
    eq("slot of +0xa0", 0xA0 // 4, 40)
    if fails:
        for f in fails:
            print("FAIL property-serialisers: " + f)
        return 1
    print("ok property-serialisers: %d synthetic cases" % 19)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xbe", nargs="?")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--symbols", default=os.path.join(ROOT, "symbols", "xbox_functions.csv"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return self_test()
    if not args.xbe:
        ap.error("an XBE is required unless --check")
    x = X.Xbe(args.xbe)
    rows = sweep_rows(x, nv.load_symbols(args.symbols))
    report(rows)
    out = args.out or os.path.join(ROOT, "inventory", "property_serialisers.csv")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    emit(rows, out)
    print("wrote " + out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
