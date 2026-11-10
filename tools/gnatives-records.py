#!/usr/bin/env python3
"""The `GNatives` slots with no store site, explained by the `{name, body}` records in `.data` (U7).

`tools/gnatives-writes.py` reads every store that names a slot absolutely (`C7 05` / `89 <modrm>`), which is
**233** of the 356 slots the guest has live at the main menu. The other 123 it cannot see *by construction*: a
fill through a register (`mov [ecx+eax*4], edx`) encodes no slot address. That is a limit of the instrument, not
an absence of evidence, and this tool closes most of the gap from the other side.

Every native body in this build also sits in a **`{pool name, body}` record** in the initialised part of
`.data` -- 1,104 records over 203 per-class blocks (`tools/native-table.py`, `inventory/native_targets.csv`).
Pair each live slot's value with those records by body and the question changes from "which instruction writes
this slot" to "which record carries this body", and 113 of the 123 blind slots answer.

    gnatives-records.py --xbe DUMP/default.xbe -o inventory    # the artifact
    gnatives-records.py --check                                 # dump-free, in `make ci`

The pair is exact: a record is `{name pointer, body}` and the body has to land in `.text`, so a slot whose value
is a record's body is a slot the loader filled from that record. A slot that is *both* stored and recorded is
reported as `store` (the store names the value outright) and the record is kept beside it, so the two instruments
can be counted against each other instead of one hiding the other.
"""
import argparse
import csv
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

SLOTS = ["index", "address", "name", "evidence", "declarers"]
SITES = ["address", "slot", "form", "value", "value_kind", "function"]
COLUMNS = ["slot", "live_address", "name", "store_site", "record_va", "record_name", "source"]


def native_table():
    """`tools/native-table.py`: the one definition of the `{pool name, body}` record in this repo."""
    path = os.path.join(ROOT, "tools", "native-table.py")
    spec = importlib.util.spec_from_file_location("native_table", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_csv(path, columns):
    with open(path, newline="", encoding="utf-8") as fh:
        r = csv.DictReader(fh)
        missing = [c for c in columns if c not in (r.fieldnames or [])]
        if missing:
            raise SystemExit("gnatives-records: %s has no %s column" % (path, ", ".join(missing)))
        return list(r)


def record_bodies(records):
    """{body address: (record_va, name)} from `native-table.table_records`."""
    out = {}
    for r in records:
        out.setdefault(r["body"], []).append((r["record_va"], r["name"]))
    return out


def classify(live, stored, bodies):
    """One row per live slot: `store` when a store names it, else `record` when a record carries its body."""
    rows = []
    for slot in live:
        index = int(slot["index"])
        addr = int(slot["address"], 16)
        hit = bodies.get(addr) or []
        row = {"slot": index, "live_address": "0x%08x" % addr, "name": slot["name"],
               "store_site": "yes" if index in stored else "no",
               "record_va": "0x%08x" % hit[0][0] if hit else "",
               "record_name": hit[0][1] if hit else "",
               "source": "store" if index in stored else ("record" if hit else "none")}
        rows.append(row)
    return rows


def tally(rows):
    out = {"live": len(rows),
           "store": sum(1 for r in rows if r["source"] == "store"),
           "record": sum(1 for r in rows if r["source"] == "record"),
           "none": sum(1 for r in rows if r["source"] == "none"),
           "recorded": sum(1 for r in rows if r["record_va"]),
           "record_and_store": sum(1 for r in rows if r["source"] == "store" and r["record_va"])}
    return out


def run(xbe_path, out_dir):
    if not os.path.isdir(out_dir):
        raise SystemExit("gnatives-records: no such directory %s" % out_dir)
    nt = native_table()
    x = nt.Xbe(xbe_path)
    pool = nt.binder_pool(x, x.data)
    bodies = record_bodies(nt.table_records(x, pool))

    live = read_csv(os.path.join(ROOT, "inventory", "gnatives_slots.csv"), SLOTS)
    stored = {int(r["slot"]) for r in read_csv(os.path.join(ROOT, "inventory", "gnatives_write_sites.csv"),
                                               SITES)}
    rows = classify(live, stored, bodies)
    n = tally(rows)

    path = os.path.join(out_dir, "gnatives_records.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    print("%d binder names, %d distinct record bodies in .data -> %s" % (len(pool), len(bodies), path))
    print("  %d live slots: %d named by a store, %d by a record, %d by neither"
          % (n["live"], n["store"], n["record"], n["none"]))
    print("  %d of the %d slots with no store site are a record's body (the other %d have neither), "
          "and %d stored slots are recorded too"
          % (n["record"], n["record"] + n["none"], n["none"], n["record_and_store"]))
    for r in rows:
        if r["source"] == "none":
            print("    NONE  slot %-4s %s %s" % (r["slot"], r["live_address"], r["name"]))
    return 0


def check():
    """Synthetic: the three sources, the tie-break, and the ordering rule, on rows that never touch a dump."""
    bodies = {0x2000: [(0x5000, "execFoo")], 0x3000: [(0x5008, "execBar")]}
    live = [{"index": "0", "address": "0x2000", "name": "execFoo", "evidence": "declared", "declarers": "X"},
            {"index": "1", "address": "0x3000", "name": "execBar", "evidence": "", "declarers": ""},
            {"index": "2", "address": "0x4000", "name": "", "evidence": "slot-only", "declarers": ""},
            {"index": "3", "address": "0x5000", "name": "execBoth", "evidence": "", "declarers": ""}]
    rows = classify(live, {0, 3}, bodies)
    assert [r["source"] for r in rows] == ["store", "record", "none", "store"], rows
    assert rows[0]["record_va"] == "0x00005000" and rows[0]["record_name"] == "execFoo", rows[0]
    assert rows[1]["record_va"] == "0x00005008", rows[1]
    assert rows[2]["record_va"] == "" and rows[2]["record_name"] == "", rows[2]
    n = tally(rows)
    assert (n["store"], n["record"], n["none"], n["record_and_store"]) == (2, 1, 1, 1), n
    empty = tally(classify([], set(), {}))
    assert empty["live"] == 0 and empty["record"] == 0, empty
    print("ok")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xbe", help="the dump's default.xbe")
    ap.add_argument("-o", "--out", default="inventory", help="directory to write gnatives_records.csv into")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump and no write")
    args = ap.parse_args()
    if args.check:
        return check()
    if not args.xbe:
        ap.error("--xbe is required unless --check")
    return run(args.xbe, args.out)


if __name__ == "__main__":
    sys.exit(main())
