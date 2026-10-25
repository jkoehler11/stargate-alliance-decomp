#!/usr/bin/env python3
"""Read the class tables of the six ucpp-native classes and resolve every vtable slot.

PLAN 4.7/3.9 concern the classes whose script cannot run without C++ behind it. Those classes declare
native functions, and a native function is a real code address in the class table rather than a shared
bytecode stub -- so the vtable says which slots are native, and where the implementation lives.

    python3 tools/ucpp-vtables.py DUMP/default.xbe            # tables + per-slot lines for the six
    python3 tools/ucpp-vtables.py DUMP/default.xbe -o inventory   # + inventory/ucpp_vtables.csv

"Shared" below is measured across all 492 tables: a target that appears in many tables is an engine
mechanism (dispatch, constructor, destructor), a target that appears once is that class's own code.
"""
import argparse
import collections
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xbe import Xbe  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UCPP = ["SGOrder", "StargateHUD", "Electrocutor", "FarClipChange", "SGFontInfo", "ShadowManager"]


def load_names(path):
    out = {}
    if os.path.isfile(path):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                try:
                    out[int(r["table_va"], 16)] = r
                except (KeyError, ValueError):
                    continue
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xbe")
    ap.add_argument("--tables", default=os.path.join(ROOT, "inventory", "xbe_class_tables.csv"))
    ap.add_argument("--names", default=os.path.join(ROOT, "inventory", "xbe_class_table_names.csv"))
    ap.add_argument("--symbols", default=os.path.join(ROOT, "symbols", "xbox_functions.csv"))
    ap.add_argument("-o", "--out")
    args = ap.parse_args()

    image = Xbe(args.xbe)
    names = load_names(args.names)
    with open(args.tables, newline="") as f:
        tables = {int(r["table_va"], 16): int(r["slots"]) for r in csv.DictReader(f)}
    symbols = {}
    with open(args.symbols, newline="") as f:
        for r in csv.DictReader(f):
            symbols[int(r["address"], 16)] = r["name"]

    by_class = {r["class"]: int(r["table_va"], 16) for r in names.values() if r.get("class")}

    # Frequency of every slot target across every table: low counts mean class-specific code.
    freq = collections.Counter()
    for va, slots in tables.items():
        raw = image.read_va(va, slots * 4)
        for i in range(slots):
            freq[int.from_bytes(raw[i * 4:i * 4 + 4], "little")] += 1

    def kind(addr):
        if addr == 0:
            return "zero"
        sec = image.section_for(addr)
        if sec is None:
            return "unmapped"
        return "code" if sec.executable else "data"

    rows = []
    for cls in UCPP:
        table = by_class.get(cls)
        if table is None:
            print("%s: no named class table" % cls)
            continue
        slots = tables.get(table, 0)
        raw = image.read_va(table, slots * 4)
        targets = [int.from_bytes(raw[i * 4:i * 4 + 4], "little") for i in range(slots)]
        unique = [t for t in targets if freq[t] == 1]
        code = [t for t in targets if kind(t) == "code"]
        print("\n%s  table=0x%08x  slots=%d  code targets=%d  class-specific=%d"
              % (cls, table, slots, len(code), len(unique)))
        for i, t in enumerate(targets):
            if t == 0:
                continue
            if freq[t] > 8:
                continue  # engine mechanism, printed as a summary line below
            head = ""
            if kind(t) == "code":
                try:
                    head = image.read_va(t, 8).hex()
                except Exception:
                    head = ""
            print("  slot %2d  ->  0x%08x  %-6s shared=%-3d %-28s %s"
                  % (i, t, kind(t), freq[t], symbols.get(t, "")[:28], head))
            rows.append({"class": cls, "slot": i, "target": "0x%08x" % t, "kind": kind(t),
                         "shared_tables": freq[t], "symbol": symbols.get(t, ""),
                         "head_bytes": head})
        common = [(t, n) for t, n in collections.Counter(targets).most_common(4) if n > 8]
        for t, n in common:
            print("  (engine) 0x%08x %-6s shared=%-4d %s" % (t, kind(t), freq[t], symbols.get(t, "")))

    print("\nwrote nothing yet" if not args.out else "")
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "ucpp_vtables.csv")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["class", "slot", "target", "kind", "shared_tables",
                                              "symbol", "head_bytes"])
            w.writeheader()
            w.writerows(rows)
        print("wrote %s (%d rows)" % (path, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
