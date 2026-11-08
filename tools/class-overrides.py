#!/usr/bin/env python3
"""Diff every class table against its parent's and record which virtuals a class overrides.

The 492 per-class tables in `.rdata` (`inventory/xbe_class_tables.csv`,
`inventory/xbe_class_table_names.csv`) are vtables, and `docs/NATIVE_VM.md` has measured what the
fixed slots are: 21 is `ProcessEvent`, 22 the by-name entry point, 11 `Serialize`, and slot 3 holds
a **different function in every one of the 492** tables (`inventory/xbe_class_table_slots.csv`,
`distinct = 492`), so slot 3 is per-class bookkeeping and never an override.

That leaves the rest of the table as a decidable question per class: read the child's slots, read the
parent's, and every index that differs is a virtual the child replaces. `tools/class-serialize-slots.py`
does this for slot 11 alone; this tool does it for the whole table, over every resolvable pair, and
commits the result so a later question ("does this class override the renderer?") is a grep.

    python3 tools/class-overrides.py --xbe DUMP/.../default.xbe -o inventory
    python3 tools/class-overrides.py --xbe ... --class Console         # one class, slot by slot
    python3 tools/class-overrides.py --xbe ... --summary               # counts only

Rows are `override` (child and parent both hold a slot and the values differ), `added` (the child's
table is longer than the parent's, so these are the virtuals it introduces) and `class-slot` (slot 3,
reported once per pair so the exclusion is visible rather than implied). Slot 3 is excluded from the
`override` count; everything else counts.

Reads the image and the committed inventories only, so it runs anywhere the dump is. The parent link
comes from `inventory/classes.csv` (script-side `extends`), not from the table order.
"""
import argparse
import collections
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from xbe import Xbe  # noqa: E402

CLASS_SLOT = 3  # unique per table in 492/492; not an override (docs/NATIVE_VM.md)
FIELDS = ["relationship", "class", "package", "table_va", "slots", "parent", "parent_table_va",
          "parent_slots", "slot", "child_fn", "parent_fn", "child_symbol", "parent_symbol", "child_size"]


def load_tables(path):
    """table_va -> slot count, from the committed census (the extent is the measured one, not a guess)."""
    out = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            out[int(row["table_va"], 16)] = int(row["slots"])
    return out


def load_names(path):
    va, name = {}, {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if not row.get("class"):
                continue
            va[int(row["table_va"], 16)] = row["class"]
            name.setdefault(row["class"], int(row["table_va"], 16))
    return va, name


def load_supers(path):
    """class name -> (package, super), only when every package agrees on the super.

    A name can exist in more than one package (the tables are named by class alone), and pairing a
    class with the wrong parent's table would report a page of overrides that are not its own.
    """
    seen = collections.defaultdict(set)
    meta = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            seen[row["class"]].add(row["super"])
            meta.setdefault(row["class"], (row["package"], row["super"]))
    return {k: meta[k] for k, v in seen.items() if len(v) == 1}


def load_symbols(path):
    names, sizes = {}, {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                va = int(row["address"], 16)
            except ValueError:
                continue
            names[va] = row["name"]
            try:
                sizes[va] = int(row["size"] or 0)
            except ValueError:
                sizes[va] = 0
    return names, sizes


def read_slots(xbe, va, count):
    try:
        return struct.unpack("<%dI" % count, xbe.read_va(va, 4 * count))
    except Exception:  # a table that runs off a mapped section is skipped, not guessed
        return None


def diff_pairs(xbe, tables, class_of, table_of, super_of):
    names, sizes = load_symbols(os.path.join(ROOT, "symbols", "xbox_functions.csv"))
    rows = []
    for cls in sorted(class_of.values()):
        va = table_of.get(cls)
        if va is None or va not in tables:
            continue
        meta = super_of.get(cls)
        if not meta:
            continue
        package, parent = meta
        pva = table_of.get(parent)
        if pva is None or pva == va or pva not in tables:
            continue
        child = read_slots(xbe, va, tables[va])
        par = read_slots(xbe, pva, tables[pva])
        if child is None or par is None:
            continue
        common = min(len(child), len(par))
        for slot in range(common):
            if child[slot] == par[slot]:
                continue
            rows.append({
                "relationship": "class-slot" if slot == CLASS_SLOT else "override",
                "class": cls, "package": package, "table_va": "0x%08x" % va, "slots": len(child),
                "parent": parent, "parent_table_va": "0x%08x" % pva, "parent_slots": len(par),
                "slot": slot, "child_fn": "0x%08x" % child[slot], "parent_fn": "0x%08x" % par[slot],
                "child_symbol": names.get(child[slot], ""), "parent_symbol": names.get(par[slot], ""),
                "child_size": sizes.get(child[slot], ""),
            })
        for slot in range(common, len(child)):
            rows.append({
                "relationship": "added",
                "class": cls, "package": package, "table_va": "0x%08x" % va, "slots": len(child),
                "parent": parent, "parent_table_va": "0x%08x" % pva, "parent_slots": len(par),
                "slot": slot, "child_fn": "0x%08x" % child[slot], "parent_fn": "",
                "child_symbol": names.get(child[slot], ""), "parent_symbol": "",
                "child_size": sizes.get(child[slot], ""),
            })
    return rows


def print_class(xbe, tables, class_of, table_of, super_of, cls):
    va = table_of.get(cls)
    if va is None:
        raise SystemExit("no class table named %s in inventory/xbe_class_table_names.csv" % cls)
    names, sizes = load_symbols(os.path.join(ROOT, "symbols", "xbox_functions.csv"))
    child = read_slots(xbe, va, tables[va])
    meta = super_of.get(cls)
    par, pva = None, None
    if meta and table_of.get(meta[1]) in tables:
        pva = table_of[meta[1]]
        par = read_slots(xbe, pva, tables[pva])
    print("%s  table 0x%08x  %d slots%s" % (cls, va, len(child),
          "" if not par else "  (parent %s 0x%08x, %d slots)" % (meta[1], pva, len(par))))
    for slot in range(len(child)):
        base = par[slot] if par and slot < len(par) else None
        mark = ""
        if base is not None and base != child[slot]:
            mark = "  <- %s" % ("class-slot" if slot == CLASS_SLOT else "DIFFERS from 0x%08x" % base)
        elif par is not None and slot >= len(par):
            mark = "  <- added"
        print("  slot %2d: 0x%08x %-24s %4d%s" % (slot, child[slot], names.get(child[slot], ""),
                                                  sizes.get(child[slot], 0), mark))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--xbe", required=True, help="<dump>/default.xbe")
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--class", dest="cls", help="print one class's table instead of writing the census")
    ap.add_argument("--summary", action="store_true", help="print counts only")
    args = ap.parse_args()

    tables = load_tables(os.path.join(ROOT, "inventory", "xbe_class_tables.csv"))
    class_of, table_of = load_names(os.path.join(ROOT, "inventory", "xbe_class_table_names.csv"))
    super_of = load_supers(os.path.join(ROOT, "inventory", "classes.csv"))
    xbe = Xbe(args.xbe)

    if args.cls:
        print_class(xbe, tables, class_of, table_of, super_of, args.cls)
        return 0

    rows = diff_pairs(xbe, tables, class_of, table_of, super_of)
    pairs = {(r["class"], r["parent"]) for r in rows if r["relationship"] != "added"}
    overrides = [r for r in rows if r["relationship"] == "override"]
    added = [r for r in rows if r["relationship"] == "added"]
    with_slot = {r["class"] for r in overrides}
    print("tables %d, named %d, pairs resolved %d" % (len(tables), len(table_of), len(pairs)))
    print("overrides %d over %d classes, added slots %d over %d classes"
          % (len(overrides), len(with_slot), len(added), len({r["class"] for r in added})))
    print("classes that override nothing: %d of %d"
          % (len({c for c, _ in pairs}) - len(with_slot), len({c for c, _ in pairs})))
    hist = collections.Counter(r["slot"] for r in overrides)
    print("  %d distinct slots carry an override; the busiest:" % len(hist))
    for slot, count in hist.most_common(10):
        print("    slot %2d overridden by %d classes" % (slot, count))
    if args.summary:
        return 0

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "class_virtual_overrides.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, FIELDS, lineterminator="\r\n")
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print("wrote %s (%d rows)" % (path, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
