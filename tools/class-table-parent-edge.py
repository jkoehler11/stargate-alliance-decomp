#!/usr/bin/env python3
"""Read each class table's parent from the base-constructor call its own installer makes.

`inventory/class_table_parents.csv` names a table from the classes that call its installer, and 17
rows come back `constrained`: their callers have no common ancestor *in* `inventory/classes.csv`.
That tree is the packages' script declarations (2,319 classes); the engine's native-only classes --
`Object`, `Property`, `Channel`, `Level`, `VertexBuffer`, `AudioSubsystem`, ... -- have no row there,
which is why the ancestor intersection is empty rather than ambiguous.

The image carries the relation anyway, one level down. A constructor calls its parent's constructor
*before* it stores its own table at `[this]`, and `inventory/class_constructors.csv` already records
that store. This tool reads the other half of that edge from the code:

    installer body -> first `E8` call -> (null-check thunk) -> parent constructor -> `C7 /0` table store

so a parent table comes out of one body read per table, with no boot and no script tree. The result
is checked against the tree it does not use: on every table where both the class and the parent it
finds have a script declaration, `classes.csv`'s `super` must be that parent -- 324 of 324 agree and
none disagrees. A parent the registration did not name is reported as an address, not guessed.

    python3 tools/class-table-parent-edge.py DUMP/default.xbe -o inventory
    python3 tools/class-table-parent-edge.py DUMP/default.xbe --report
    python3 tools/class-table-parent-edge.py --check        # synthetic bodies, no dump, no write

Addresses, class names and counts only: no package or image content is written.
"""
import argparse
import collections
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS = ["table_va", "class", "slots", "parent_va", "parent_class", "installer_va", "installer_form",
          "call_site", "thunk_va", "store_va", "script_super", "verdict"]
CENSUS = ("census", "tables", "edges", "agreed", "mismatched", "parent_unnamed",
          "class_outside_script", "no_edge", "constrained", "constrained_with_parent",
          "constrained_callers_in_script")


def load(root):
    """The committed artifacts this joins: tables, names, installers and the script tree."""
    tables = {}
    with open(os.path.join(root, "inventory/xbe_class_tables.csv"), newline="") as f:
        for row in csv.DictReader(f):
            tables[int(row["table_va"], 16)] = int(row["slots"])
    names = {}
    with open(os.path.join(root, "inventory/xbe_class_table_names.csv"), newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("class") or "").strip():
                names[int(row["table_va"], 16)] = row["class"].strip()
    supers = {}
    with open(os.path.join(root, "inventory/classes.csv"), newline="") as f:
        for row in csv.DictReader(f):
            supers[row["class"]] = (row.get("super") or "").strip()
    installers = collections.defaultdict(list)
    with open(os.path.join(root, "inventory/class_constructors.csv"), newline="") as f:
        for row in csv.DictReader(f):
            installers[int(row["table_va"], 16)].append(
                (int(row["function_va"], 16), row.get("form") or ""))
    constrained = {}
    path = os.path.join(root, "inventory/class_table_parents.csv")
    if os.path.exists(path):
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                if row.get("verdict") == "constrained":
                    constrained[int(row["table_va"], 16)] = [
                        c for c in (row.get("named_callers") or "").split("|") if c]
    functions = {}
    with open(os.path.join(root, "symbols/xbox_functions.csv"), newline="") as f:
        for row in csv.DictReader(f):
            try:
                functions[int(row["address"], 16)] = int(row.get("size") or 0)
            except (TypeError, ValueError):
                continue
    return tables, names, supers, installers, constrained, functions


def stores(text, va, tables):
    """Every `mov dword ptr [reg(+disp)], <class table>`; an absolute store is a global initialiser."""
    out, i = [], 0
    while i < len(text) - 9:
        if text[i] != 0xC7:                                   # C7 /0 = mov r/m32, imm32
            i += 1
            continue
        modrm = text[i + 1]
        mod, reg, rm = modrm >> 6, (modrm >> 3) & 7, modrm & 7
        if reg != 0 or rm == 4:                               # /0 only, no SIB byte
            i += 1
            continue
        if mod in (0, 1, 2) and not (mod == 0 and rm == 5):
            ln = 6 + (0 if mod == 0 else 1 if mod == 1 else 3)
            imm = struct.unpack_from("<I", text, i + ln - 4)[0]
            if imm in tables:
                out.append((va + i, imm))
            i += ln
            continue
        i += 1
    return out


def body(va, functions, read, cap=600):
    size = functions.get(va) or cap
    try:
        return read(va, min(size, cap))
    except Exception:                                          # an unmapped call target is not a body
        return b""


def follow(va, functions, read, hops=3):
    """A `mov ecx,[esp+4]; test; je; jmp` null-check thunk stands between a call and the constructor."""
    seen = set()
    while hops and va not in seen:
        seen.add(va)
        try:                                    # a thunk's symbol size can stop short of its `jmp`
            text = read(va, 32)
        except Exception:
            return va
        if not text or any(b == 0xE8 for b in text):
            return va
        jump = None
        for i in range(min(len(text), 40) - 4):
            if text[i] == 0xE9:
                jump = va + i + 5 + struct.unpack_from("<i", text, i + 1)[0]
                break
        if jump is None:
            return va
        va, hops = jump, hops - 1
    return va


def parent_edge(table, installers, tables, functions, read):
    """(installer, call site, thunk, store site, parent table) for one table, or None."""
    for fn, form in sorted(installers.get(table, ()), key=lambda p: (p[1] != "dtor", p[0])):
        text = body(fn, functions, read)
        for i in range(len(text) - 5):
            if text[i] != 0xE8:
                continue
            target = fn + i + 5 + struct.unpack_from("<i", text, i + 1)[0]
            real = follow(target, functions, read)
            hits = stores(body(real, functions, read), real, tables)
            if hits:
                return fn, form, fn + i, real, hits[0][0], hits[0][1]
    return None


def verdict_for(cls, parent, names, supers):
    if parent is None:
        return "no-edge"
    parent_class = names.get(parent, "")
    super_name = supers.get(cls, "") if cls else ""
    if not parent_class:
        return "parent-unnamed"
    if parent_class == super_name:
        return "agrees"
    if super_name:
        return "mismatch"
    return "class-outside-script"


def run(dump, out_dir):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from xbe import Xbe

    tables, names, supers, installers, constrained, functions = load(ROOT)
    image = Xbe(dump)
    sections = list(image.sections)

    def read(va, n):
        if not any(sec.contains(va) for sec in sections):
            raise KeyError(hex(va))
        return image.read_va(va, n)

    rows, counts = [], collections.Counter()
    for table in sorted(tables):
        cls = names.get(table, "")
        edge = parent_edge(table, installers, tables, functions, read)
        parent = edge[5] if edge else None
        verdict = verdict_for(cls, parent, names, supers)
        counts[verdict] += 1
        rows.append({
            "table_va": hex(table), "class": cls, "slots": tables[table],
            "parent_va": hex(parent) if parent is not None else "",
            "parent_class": names.get(parent, "") if parent is not None else "",
            "installer_va": hex(edge[0]) if edge else "", "installer_form": edge[1] if edge else "",
            "call_site": hex(edge[2]) if edge else "", "thunk_va": hex(edge[3]) if edge else "",
            "store_va": hex(edge[4]) if edge else "", "script_super": supers.get(cls, "") if cls else "",
            "verdict": verdict,
        })

    with_parent = sum(1 for t in constrained if any(r["verdict"] != "no-edge" and
                                                    int(r["table_va"], 16) == t for r in rows))
    in_script = sum(1 for names_ in constrained.values() for c in names_ if c in supers)
    rows.append({
        "table_va": "census", "class": "", "slots": len(tables),
        "parent_va": "", "parent_class": "", "installer_va": "", "installer_form": "",
        "call_site": "", "thunk_va": "", "store_va": "",
        "script_super": "",
        "verdict": "tables=%d edges=%d agreed=%d mismatched=%d parent-unnamed=%d "
                   "class-outside-script=%d no-edge=%d constrained=%d constrained-with-parent=%d "
                   "constrained-callers-in-script=%d" % (
                       len(tables), len(tables) - counts["no-edge"], counts["agrees"],
                       counts["mismatch"], counts["parent-unnamed"], counts["class-outside-script"],
                       counts["no-edge"], len(constrained), with_parent, in_script),
    })
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "class_table_parent_edges.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return rows, counts, len(constrained), with_parent, in_script, path


def check():
    """Synthetic bodies: the thunk, the store, the second-call fallback and an absolute store."""
    tables = {0x5000: 30, 0x5100: 31}
    codes = {}
    # a child ctor at 0x1000: call thunk(0x1200), then store the child's own table
    codes[0x1000] = b"\xE8" + struct.pack("<i", 0x1200 - 0x1005) + b"\x90" * 10 + \
        b"\xC7\x06" + struct.pack("<I", 0x5100) + b"\xC3"
    # the null-check thunk at 0x1200: mov ecx,[esp+4]; test ecx,ecx; je +2; jmp 0x1100
    codes[0x1200] = b"\x8B\x4C\x24\x04\x85\xC9\x74\x02\xE9" + struct.pack("<i", 0x1100 - 0x120d)
    # the parent ctor at 0x1100 stores the parent's table
    codes[0x1100] = b"\xC7\x06" + struct.pack("<I", 0x5000) + b"\xC3" + b"\x90" * 20
    # a body whose only store is absolute: a global initialiser, not a vtable install
    codes[0x1300] = b"\xC7\x05" + struct.pack("<I", 0x400000) + struct.pack("<I", 0x5000) + b"\xC3"
    # a ctor whose first call reaches no table and whose second one does
    codes[0x1400] = b"\xE8" + struct.pack("<i", 0x1300 - 0x1405) + b"\xE8" + \
        struct.pack("<i", 0x1100 - 0x140a) + b"\xC3"
    sizes = {0x1000: 24, 0x1200: 19, 0x1100: 12, 0x1300: 12, 0x1400: 11}

    def read(va, n):
        return codes[va][:n]

    fails = []

    def want(label, cond):
        if not cond:
            fails.append(label)

    edge = parent_edge(0x5100, {0x5100: [(0x1000, "ctor")]}, tables, sizes, read)
    want("thunk-followed parent store", edge is not None and edge[5] == 0x5000)
    want("the thunk is reported beside the constructor it jumps to", edge is not None and edge[3] == 0x1100)
    want("the call site is the installer's own instruction", edge is not None and edge[2] == 0x1000)
    want("an absolute store is not a vtable install", stores(codes[0x1300], 0x1300, tables) == [])
    want("an absolute store alone yields no edge",
         parent_edge(0x5100, {0x5100: [(0x1300, "ctor")]}, tables, sizes, read) is None)
    edge = parent_edge(0x5100, {0x5100: [(0x1400, "ctor")]}, tables, sizes, read)
    want("the first table-storing call wins", edge is not None and edge[5] == 0x5000)
    want("no parent is reported, not guessed", verdict_for("Foo", None, {}, {}) == "no-edge")
    want("an unnamed parent stays an address", verdict_for("Channel", 0x5000, {}, {}) == "parent-unnamed")
    want("script agreement", verdict_for("Foo", 0x5000, {0x5000: "Bar"}, {"Foo": "Bar"}) == "agrees")
    want("a conflict is reported", verdict_for("Foo", 0x5000, {0x5000: "Baz"}, {"Foo": "Bar"}) == "mismatch")
    want("a class absent from the script tree is named as such",
         verdict_for("Channel", 0x5000, {0x5000: "Bar"}, {}) == "class-outside-script")
    if fails:
        print("FAIL class-table-parent-edge: %s" % "; ".join(fails))
        return 1
    print("ok class-table-parent-edge: 11 assertions")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xbe", nargs="?")
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        return check()
    if not args.xbe:
        ap.error("an XBE path is required unless --check is given")
    rows, counts, nconstrained, with_parent, in_script, path = run(args.xbe, args.out)
    print("wrote %s: %d rows" % (path, len(rows)))
    print("edges %d  agreed %d  mismatched %d  parent-unnamed %d  class-outside-script %d  no-edge %d"
          % (len(rows) - 1 - counts["no-edge"], counts["agrees"], counts["mismatch"],
             counts["parent-unnamed"], counts["class-outside-script"], counts["no-edge"]))
    print("constrained %d, with a parent %d, callers declared in classes.csv %d"
          % (nconstrained, with_parent, in_script))
    if args.report:
        for row in rows[:-1]:
            if row["table_va"] in {hex(t) for t in (0x4c78c0, 0x4d1468, 0x4fa390)}:
                print(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
