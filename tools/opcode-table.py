#!/usr/bin/env python3
"""Read the script walker's opcode tables out of the image (PLAN 3.7).

`UStruct::Serialize` walks a function's stored script token by token through a virtual at vtable `+0xa0`
(`docs/NATIVE_VM.md`). For `Struct`/`Function`/`Class` that virtual is `0x000398e0`, and its body reads one opcode
byte and then dispatches through **two** tables, not one:

    movzx  ebp,BYTE PTR [ebp+0x39ec0]      ; opcode -> group   (72 bytes, opcodes 0x00..0x47)
    jmp    DWORD PTR [ebp*4+0x39e44]       ; group  -> handler (31 entries)

So the entry count is the number of *groups*, not the number of opcodes: 72 opcodes share 31 handler bodies, and
three of them (`0x03`, `0x15`, `0x2b`, `0x35`, `0x3a..0x3f`, `0x46`) land on the group that logs
`"Bad expr token %02x"`. Above the table there are two range rules:

    0x48..0x5f  -> the bad-token group (the `cmp 0x47 / ja` before the table)
    0x60..0x6f  -> one operand byte, then a nested walk until `0x16` (EX_EndFunctionParms)
    0x70..0xff  -> no operand byte, then the same nested walk

Each group's operand pattern is transcribed from that group's handler body in the walker (the addresses are in the
CSV, so a reviewer can read the same instructions); this tool does not infer a width from UE2 documentation. The
`name` column is UELib's UE2 token map (`src/Branch/DefaultEngineBranch.cs::BuildTokenMap`, pinned checkout), which
is a *different* implementation and therefore a usable cross-check: where this build's walker and UELib disagree the
row says so in `note`.

    python3 tools/opcode-table.py --xbe DUMP/.../default.xbe -o inventory   # needs the image
    python3 tools/opcode-table.py --check                                   # dump-free, what `make ci` runs
    python3 tools/opcode-table.py --show                                    # print the group table

The artifact is `inventory/opcode_table.csv`: 256 rows (one per opcode), deterministic because the walker address
comes from the committed slot table.
"""
import argparse
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# group -> (pattern, uelib name(s) for the group, evidence note). Patterns are the operand stream in order:
#   expr        one nested token (recursive walk)
#   bytes:N     N fixed operand bytes
#   parms       nested tokens until one of them is 0x16 (EX_EndFunctionParms)
#   str         NUL-terminated ANSI string
#   utf16       NUL-terminated UTF-16 string (2-byte units, ends on a zero unit)
#   labeltable  8-byte entries until a zero dword
#   case        2-byte value; 0xffff ends the case list, otherwise one nested token
GROUPS = {
    0:  ("bytes:4",          "LocalVariable,InstanceVariable,DefaultVariable,ObjectConst,NativeParm",
         "object/name reference, 4 bytes"),
    1:  ("expr",             "Return", "one nested token"),
    2:  ("bytes:1,expr",     "Switch", "case-count byte then the expression"),
    3:  ("bytes:2",          "Jump", "code offset"),
    4:  ("bytes:2,expr",     "JumpIfNot", "code offset then the condition"),
    5:  ("",                 "Stop,Nothing,EndFunctionParms,Self,IntZero,IntOne,True,False,NoObject,"
                             "BoolVariable,IteratorPop,IteratorNext,EndOfScript",
         "no operand bytes, no children: the handler returns the opcode"),
    6:  ("bytes:2,expr",     "Assert,Skip", "code offset then the expression"),
    7:  ("bytes:2,case",     "Case", "value, then a nested token unless the value is 0xffff"),
    8:  ("labeltable",       "LabelTable", "8-byte entries until a zero dword"),
    9:  ("expr",             "GotoLabel,EatString,DynamicArrayLength", "one nested token"),
    10: ("expr,expr",        "Let,DynamicArrayElement,LetBool,ArrayElement,LetDelegate",
         "two nested tokens (lvalue, value)"),
    11: ("expr,expr,expr,expr", "New", "four nested tokens; agrees with UELib's NewToken"),
    12: ("expr,bytes:3,expr", "ClassContext,Context", "expression, 2-byte offset, byte, expression"),
    13: ("bytes:4,expr",     "MetaClassCast,DynamicCast,StructMember", "reference then the expression"),
    14: ("bytes:4,parms",    "VirtualFunction,GlobalFunction", "name reference then the parameter list"),
    15: ("bytes:4,parms",    "FinalFunction", "function reference then the parameter list"),
    16: ("bytes:4",          "IntConst", "int32"),
    17: ("bytes:4",          "FloatConst", "float32"),
    18: ("str",              "StringConst", "NUL-terminated ANSI string"),
    19: ("bytes:4",          "NameConst,DelegateProperty", "name reference"),
    20: ("bytes:12",         "RotationConst", "three int32"),
    21: ("bytes:12",         "VectorConst", "three float32"),
    22: ("bytes:1",          "ByteConst,IntConstByte", "one byte"),
    23: ("expr,bytes:2",     "Iterator", "expression then the code offset"),
    24: ("bytes:4,expr,expr", "StructCmpEq,StructCmpNe", "reference then two nested tokens"),
    25: ("utf16",            "UnicodeStringConst", "NUL-terminated UTF-16 string"),
    26: ("bytes:1,expr",     "PrimitiveCast", "cast type byte then the expression"),
    27: ("expr,expr,expr",   "DynamicArrayInsert,DynamicArrayRemove", "three nested tokens"),
    28: ("bytes:13",         "DebugInfo", "three int32 then one byte"),
    29: ("bytes:4,expr",     "DelegateFunction", "reference then the expression"),
    30: ("bad-token",        None, "the handler logs \"Bad expr token %02x\" and returns"),
}

# UELib's UE2 token map (BuildTokenMap, version 130 path). Only the disagreements carry a note.
NAMES = {
    0x00: "LocalVariable", 0x01: "InstanceVariable", 0x02: "DefaultVariable", 0x03: "BadToken",
    0x04: "Return", 0x05: "Switch", 0x06: "Jump", 0x07: "JumpIfNot", 0x08: "Stop", 0x09: "Assert",
    0x0a: "Case", 0x0b: "Nothing", 0x0c: "LabelTable", 0x0d: "GotoLabel", 0x0e: "EatString",
    0x0f: "Let", 0x10: "DynamicArrayElement", 0x11: "New", 0x12: "ClassContext", 0x13: "MetaClassCast",
    0x14: "LetBool", 0x15: "LineNumber", 0x16: "EndFunctionParms", 0x17: "Self", 0x18: "Skip",
    0x19: "Context", 0x1a: "ArrayElement", 0x1b: "VirtualFunction", 0x1c: "FinalFunction",
    0x1d: "IntConst", 0x1e: "FloatConst", 0x1f: "StringConst", 0x20: "ObjectConst", 0x21: "NameConst",
    0x22: "RotationConst", 0x23: "VectorConst", 0x24: "ByteConst", 0x25: "IntZero", 0x26: "IntOne",
    0x27: "True", 0x28: "False", 0x29: "NativeParm", 0x2a: "NoObject", 0x2b: "BadToken",
    0x2c: "IntConstByte", 0x2d: "BoolVariable", 0x2e: "DynamicCast", 0x2f: "Iterator",
    0x30: "IteratorPop", 0x31: "IteratorNext", 0x32: "StructCmpEq", 0x33: "StructCmpNe",
    0x34: "UnicodeStringConst", 0x35: "RangeConst", 0x36: "StructMember", 0x37: "DynamicArrayLength",
    0x38: "GlobalFunction", 0x39: "PrimitiveCast", 0x3a: "ReturnNothing", 0x3b: "DelegateCmpEq",
    0x3c: "DelegateCmpNe", 0x3d: "DelegateFunctionCmpEq", 0x3e: "DelegateFunctionCmpNe",
    0x3f: "EmptyDelegate", 0x40: "DynamicArrayInsert", 0x41: "DynamicArrayRemove", 0x42: "DebugInfo",
    0x43: "DelegateFunction", 0x44: "DelegateProperty", 0x45: "LetDelegate", 0x46: "BadToken",
    0x47: "EndOfScript",
}

NOTES = {
    0x03: "UELib: BadToken; this build rejects it too",
    0x15: "UELib maps LineNumber for UE2 builds; this build rejects it (no line-number token is emitted)",
    0x2b: "UELib: BadToken from CastStringSizeTokenDeprecated on; rejected here too",
    0x35: "UELib maps RangeConst only for BuildGeneration.UE2; rejected here, so this build is not that generation",
    0x3a: "UELib: ReturnNothing; this build rejects it, and its payloads end `04 0b 47` instead",
    0x3b: "UELib: DelegateCmpEq; delegates are not accepted by this build's walker",
    0x3c: "UELib: DelegateCmpNe; not accepted",
    0x3d: "UELib: DelegateFunctionCmpEq; not accepted",
    0x3e: "UELib: DelegateFunctionCmpNe; not accepted",
    0x3f: "UELib: EmptyDelegate; not accepted",
    0x46: "UELib: BadToken; rejected here too",
    0x47: "UELib: EndOfScript; no operand bytes, and it is the third byte of the payload epilogue",
}

COLUMNS = ["opcode", "group", "handler", "name", "pattern", "fixed_bytes", "source", "note"]


def fixed_bytes(pattern):
    total = 0
    for item in pattern.split(",") if pattern else []:
        if item.startswith("bytes:"):
            total += int(item.split(":", 1)[1])
    return total


def read_va(xbe, va, size):
    for s in xbe.sections:
        if s.va <= va < s.va + s.fsize:
            off = s.foff + (va - s.va)
            return xbe.data[off:off + size]
    return b""


def build_rows(xbe_path, slots, symbols):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from xbe import Xbe                                            # noqa: PLC0415 - only when a dump exists

    xbe = Xbe(xbe_path)

    walkers = {}
    for r in csv.DictReader(open(slots)):
        if r.get("script_walk"):
            walkers.setdefault(int(r["script_walk"], 16), []).append(r["class"])
    if not walkers:
        raise SystemExit("no script_walk column values in %s" % slots)
    target = 0x000398E0 if 0x000398E0 in walkers else max(walkers, key=lambda k: len(walkers[k]))
    print("walker %#010x shared by %d classes" % (target, len(walkers.get(target, []))))

    sizes = {}
    for r in csv.DictReader(open(symbols)):
        sizes[int(r["address"], 16)] = int(r["size"])
    starts = sorted(sizes)
    import bisect
    i = bisect.bisect_right(starts, target) - 1
    if i < 0:
        raise SystemExit("walker %#x is not in the symbol map" % target)
    end = starts[i] + sizes[starts[i]]
    code = read_va(xbe, target, min(end - target, 0x2000))

    # the switch: jmp dword ptr [reg*4 + disp32]
    jump_sites = []
    for i in range(len(code) - 6):
        if code[i] == 0xFF and code[i + 1] == 0x24 and (code[i + 2] & 0xC7) == 0x85:
            jump_sites.append((target + i, struct.unpack_from("<i", code, i + 3)[0]))
    if not jump_sites:
        raise SystemExit("no jump table in the walker")
    site, table_va = jump_sites[0]
    raw = read_va(xbe, table_va, 4 * 0x40)
    entries = struct.unpack_from("<%dI" % (len(raw) // 4), raw, 0)
    handlers = []
    for e in entries:
        if not (target <= e < end):
            break
        handlers.append(e)
    if len(handlers) != 31:
        print("warning: %d entries inside the walker, expected 31" % len(handlers))

    # the opcode -> group byte map: movzx reg, byte ptr [reg + disp32]
    map_va, map_len = None, 0
    for i in range(len(code) - 6):
        if code[i] == 0x0F and code[i + 1] == 0xB6 and (code[i + 2] & 0xC0) == 0x80:
            va = struct.unpack_from("<i", code, i + 3)[0]
            blob = read_va(xbe, va, 0x48)
            if len(blob) == 0x48 and max(blob) < len(handlers):
                map_va, map_len = va, 0x48
                print("opcode map at %#010x (%d bytes), jump table at %#010x (%d groups)"
                      % (va, 0x48, table_va, len(handlers)))
                break
    if map_va is None:
        raise SystemExit("no opcode -> group byte map in the walker")
    bmap = read_va(xbe, map_va, map_len)

    used = sorted(set(bmap))
    missing = [g for g in range(len(handlers)) if g not in used]
    if missing:
        raise SystemExit("groups with no opcode: %s" % missing)
    bad = [o for o in range(map_len) if bmap[o] not in GROUPS]
    if bad:
        raise SystemExit("opcode %#x maps to a group with no rule" % bad[0])
    print("opcodes %#04x..%#04x map onto %d groups, all %d with a rule"
          % (0, map_len - 1, len(used), len(handlers)))

    rows = []
    for op in range(0x100):
        if op < map_len:
            g = bmap[op]
            pattern = GROUPS[g][0]
            rows.append({"opcode": "0x%02x" % op, "group": g, "handler": "0x%08x" % handlers[g],
                         "name": NAMES.get(op, ""), "pattern": pattern or "",
                         "fixed_bytes": fixed_bytes(pattern) if pattern else 0,
                         "source": "map", "note": NOTES.get(op, GROUPS[g][2])})
        elif op <= 0x5f:
            rows.append({"opcode": "0x%02x" % op, "group": 30, "handler": "0x%08x" % handlers[30],
                         "name": "", "pattern": "bad-token", "fixed_bytes": 0, "source": "range",
                         "note": "above the table (cmp 0x47 / ja) the walker logs the bad-token message"})
        elif op <= 0x6f:
            rows.append({"opcode": "0x%02x" % op, "group": "", "handler": "",
                         "name": "", "pattern": "bytes:1,parms", "fixed_bytes": 1,
                         "source": "range", "note": "0x60..0x6f: one byte then a nested walk until 0x16"})
        else:
            rows.append({"opcode": "0x%02x" % op, "group": "", "handler": "",
                         "name": "", "pattern": "parms", "fixed_bytes": 0,
                         "source": "range", "note": "above 0x6f: no byte, a nested walk until 0x16"})
    return rows, bmap, handlers


def show(bmap, handlers):
    per = {}
    for op, g in enumerate(bmap):
        per.setdefault(g, []).append(op)
    for g in sorted(per):
        pattern, names, note = GROUPS[g]
        print("%2d %#08x %-22s %s" % (g, handlers[g],
                                      pattern if pattern else "no-operand",
                                      " ".join("%02x" % o for o in per[g])))
        print("   %s" % (names or "-"))


def check(path):
    """Dump-free: the CSV must be the table the tool would write, and internally consistent."""
    rows = list(csv.DictReader(open(path)))
    problems = []
    if len(rows) != 256:
        problems.append("%d rows, expected 256" % len(rows))
    seen = set()
    for i, r in enumerate(rows):
        op = int(r["opcode"], 16)
        if op != i:
            problems.append("row %d is opcode %#x" % (i, op))
        seen.add(op)
        if r["source"] not in ("map", "range"):
            problems.append("opcode %#x: source %r" % (op, r["source"]))
        if r["source"] == "map":
            g = int(r["group"])
            if g not in GROUPS:
                problems.append("opcode %#x: group %d has no rule" % (op, g))
                continue
            pattern = GROUPS[g][0] or ""
            if r["pattern"] != pattern:
                problems.append("opcode %#x: pattern %r, group %d says %r" % (op, r["pattern"], g, pattern))
            if int(r["fixed_bytes"]) != (fixed_bytes(pattern) if pattern else 0):
                problems.append("opcode %#x: fixed_bytes %s" % (op, r["fixed_bytes"]))
        if r["source"] == "range":
            want = "bad-token" if op <= 0x5f else ("bytes:1,parms" if op <= 0x6f else "parms")
            if r["pattern"] != want:
                problems.append("opcode %#x: range pattern %r, expected %r" % (op, r["pattern"], want))
    if seen != set(range(256)):
        problems.append("opcode set is not 0..255")
    groups = {int(r["group"]) for r in rows if r["source"] == "map"}
    expected = set(GROUPS)
    if groups != expected:
        problems.append("groups %s, expected %s" % (sorted(groups ^ expected), sorted(expected)))
    if problems:
        print("FAIL opcode-table: " + "; ".join(problems[:6]))
        return 1
    print("ok   opcode-table: 256 opcodes, %d groups, %d rejected, %d range rows"
          % (len(groups), sum(1 for r in rows if r["pattern"] == "bad-token"),
             sum(1 for r in rows if r["source"] == "range")))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("--slots", default=os.path.join(ROOT, "inventory", "class_serialize_slots.csv"))
    ap.add_argument("--symbols", default=os.path.join(ROOT, "symbols", "xbox_functions.csv"))
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--check", action="store_true", help="validate the committed CSV (no image needed)")
    ap.add_argument("--show", action="store_true", help="print the group table")
    args = ap.parse_args()

    path = os.path.join(args.out, "opcode_table.csv")
    if args.check:
        return check(path)
    if not args.xbe:
        ap.error("--xbe is required unless --check")
    rows, bmap, handlers = build_rows(args.xbe, args.slots, args.symbols)
    if args.show:
        show(bmap, handlers)
    os.makedirs(args.out, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("wrote %s (%d rows)" % (path, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
