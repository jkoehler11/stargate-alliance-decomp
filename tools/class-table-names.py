#!/usr/bin/env python3
"""Name the class tables from the build's own class registrations.

`inventory/xbe_class_tables.csv` (tools/class-tables.py) measures 492 tables of code pointers and the
slot that dispatches `ProcessEvent`, but not which class each table belongs to. The build names that
itself: every class is registered by a call to `FUN_0003af60` with

    FUN_0003af60(outer, <object size>, <class flags>, 0, 0, 0, 0, L"<Class>", ?, L"System",
                 <class flags 2>, &<constructor thunk>, &<parent>)

and the constructor thunk forwards to the constructor that installs the class's table with
`mov dword ptr [this], <table>`. So the chain is
registration -> class literal -> constructor thunk -> constructor -> `mov [this], table`.

    pip install capstone
    python3 tools/class-table-names.py DUMP/default.xbe -o inventory

Inputs are the image, `inventory/xbe_class_tables.csv` and `symbols/xbox_functions.csv` (function
bounds only). Output is derived measurement: names the build ships plus the addresses that link them.
"""
import argparse
import bisect
import csv
import os
import re
import struct
import sys

from capstone import Cs, CS_ARCH_X86, CS_MODE_32

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xbe import Xbe  # noqa: E402

THUNK_PREFIX = b"\x8b\x4c\x24\x04"          # mov ecx,[esp+4] -- a constructor thunk's first bytes
IMM = re.compile(r"0x([0-9a-f]+)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xbe")
    ap.add_argument("--tables", default="inventory/xbe_class_tables.csv")
    ap.add_argument("--symbols", default="symbols/xbox_functions.csv")
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--ctor", default="0x0003af60", help="the class-object constructor")
    args = ap.parse_args()

    xbe = Xbe(args.xbe)
    data = xbe.data
    text = next(s for s in xbe.sections if s.name == ".text")
    tb = data[text.foff:text.foff + text.fsize]
    ctor = int(args.ctor, 16)
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.skipdata = True

    funcs = []
    with open(args.symbols) as f:
        for row in csv.DictReader(f):
            funcs.append((int(row["address"], 16), int(row["size"]), row["name"]))
    funcs.sort()
    fstarts = [f[0] for f in funcs]

    tables = {}
    with open(args.tables) as f:
        for row in csv.DictReader(f):
            tables[int(row["table_va"], 16)] = row

    def sec_of(va):
        for s in xbe.sections:
            if s.va <= va < s.va + s.vsize:
                return s
        return None

    def read(va, n):
        s = sec_of(va)
        if not s or va + n > s.va + s.vsize:
            return None
        return data[s.foff + (va - s.va):s.foff + (va - s.va) + n]

    def widestr(va, maxchars=48):
        b = read(va, maxchars * 2)
        if not b:
            return None
        out = []
        for i in range(0, len(b) - 1, 2):
            ch = struct.unpack_from("<H", b, i)[0]
            if ch == 0:
                break
            if 32 <= ch < 127:
                out.append(chr(ch))
            else:
                return None
        name = "".join(out)
        return name if 3 <= len(name) <= maxchars else None

    def thunk_target(va):
        b = read(va, 16)
        if not b or b[:4] != THUNK_PREFIX:
            return None
        i = 4
        while i + 5 < len(b):
            if b[i:i + 2] == b"\x85\xc9" or b[i] == 0x74 or b[i] == 0xEB:
                i += 2
            elif b[i] == 0xE9:
                return va + i + 5 + struct.unpack_from("<i", b, i + 1)[0]
            else:
                return None
        return None

    def installed_table(ctor_va):
        b = read(ctor_va, 400)
        if not b:
            return None
        for i in range(len(b) - 6):
            if b[i] != 0xC7:                       # mov r/m32, imm32
                continue
            modrm = b[i + 1]
            if (modrm >> 6) or ((modrm >> 3) & 7):
                continue
            imm = struct.unpack_from("<I", b, i + 2)[0]
            if imm in tables:
                return imm, ctor_va + i
        return None

    sites = [text.va + i for i in range(len(tb) - 5)
             if tb[i] == 0xE8 and text.va + i + 5 + struct.unpack_from("<i", tb, i + 1)[0] == ctor]

    # Call sites grouped into their enclosing function, so each function is disassembled once.
    def owner(va):
        i = bisect.bisect_right(fstarts, va) - 1
        if i < 0:
            return None
        a, s, n = funcs[i]
        return (a, s, n) if va < a + s else None

    rows = {}
    unnamed = []
    for site in sites:
        own = owner(site)
        start, size = (own[0], own[1]) if own else (site - 200, 200)
        code = read(start, size)
        if not code:
            continue
        ins = list(md.disasm(code, start))
        idx = next((k for k, i in enumerate(ins)
                    if i.address <= site < i.address + i.size and i.mnemonic == "call"), None)
        if idx is None:
            continue
        pushes = [int(m.group(1), 16) for i in ins[max(0, idx - 40):idx]
                  for m in [IMM.search(i.op_str)] if i.mnemonic == "push" and m]
        name = None
        for value in reversed(pushes):
            w = widestr(value)
            if w and w != "System":
                name = w
                break
        # Argument order is (outer, size, class flags, ...), pushed right to left, so the size is
        # the last immediate push before the call unless the outer pointer was pushed as a literal 0.
        obj = None
        if pushes:
            obj = pushes[-1] if pushes[-1] else (pushes[-2] if len(pushes) > 1 else None)
        linked = None
        for value in reversed(pushes):
            if value in tables or not read(value, 4):
                continue
            for cand in (thunk_target(value), value):
                if not cand:
                    continue
                got = installed_table(cand)
                if got:
                    linked = (value, cand, got[0], got[1])
                    break
            if linked:
                break
        if linked:
            th, target, table, store = linked
            rows[table] = (name or "", obj, site, th, target, store)
        else:
            unnamed.append((site, name))

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "xbe_class_table_names.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["table_va", "class", "object_size", "registration_va", "constructor_thunk_va",
                    "constructor_va", "installed_at", "evidence"])
        for table in sorted(tables):
            r = rows.get(table)
            if r:
                w.writerow(["0x%08x" % table, r[0], "" if r[1] is None else r[1], "0x%08x" % r[2],
                            "0x%08x" % r[3], "0x%08x" % r[4], "0x%08x" % r[5],
                            "class literal at the registration; C7 /0 store of this table in the "
                            "constructor"])
            else:
                w.writerow(["0x%08x" % table, "", "", "", "", "", "",
                            "no registration names this table"])

    print("registrations: %d; tables named: %d of %d" % (len(sites), len(rows), len(tables)))
    print("wrote %s" % path)


if __name__ == "__main__":
    main()
