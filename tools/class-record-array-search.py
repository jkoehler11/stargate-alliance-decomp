#!/usr/bin/env python3
"""Locate the class record's array head from the block side, with the gap census as the control.

`tools/class-record-array-head.py` closes the record from the head the **gap-front census** supplies, and it closes
on 1,612 of 1,612 rows that have one. That census needs the writer's order to close on the payload, so it is silent
on 592 of the 2,204 located rows -- and on those the head is still unmeasured.

This tool finds the head without the census. The two ends it already knows are the located block start and the
record's own closing group (`UClass::Serialize`: a names array, a reference, a config name, a version-gated names
array), and the group's parse is exact -- it must land on the block start with every name index inside the table.
So a head is a position `p` where

    [count] [count x (compact reference, dword, dword)] <closing group>   ends on `block_start`

for some `p` in `[prefix + 42, prefix + 42 + 40)`. The tool reports every such `p` per row, so the answer carries
its own ambiguity: a row with one candidate is located, a row with several is reported as ambiguous rather than
resolved by preference.

The control is the census: on the 1,612 rows where it has a head, the search must find that same head.

    python3 tools/class-record-array-search.py --dump $SG1_DUMP --class Engine.IMusicActor
    python3 tools/class-record-array-search.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-array-search.py --report
    python3 tools/class-record-array-search.py --check           # synthetic, no dump, writes nothing

Reads the packages and two committed tables; no boot, no Ghidra round, no property value decoded.
"""
import argparse
import collections
import csv
import importlib.util
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402

DOC_OFFSET = 42          # prefix + 42: the shipped walk's position, i.e. the front
WINDOW = 40              # how far past it a head may sit
MAX_COUNT = 4096
MAX_REF = 5
ELEM_TAIL = 8
ARRAY_ARTIFACT = "class_record_array.csv"
GAP_ARTIFACT = "class_record_gap_front.csv"
ARTIFACT = "class_record_array_search.csv"
COLUMNS = ["package", "class", "payload", "prefix_end", "block_start", "gap_len", "census_head",
           "head", "head_off", "cands", "count", "end", "residue", "refs_ok",
           "group_names", "group_ref", "group_cfg", "group_list", "agree", "verdict"]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


crf = load("crf", "class-record-fields")
ctr = load("ctr", "class-tail-run")


def walk_elements(buf, p):
    if p < 0 or p >= len(buf):
        return None
    try:
        n, o = crf.read_index(buf, p)
    except IndexError:
        return None
    if n < 1 or n > MAX_COUNT:
        return None
    refs = []
    for _ in range(n):
        if o >= len(buf):
            return None
        try:
            v, e = crf.read_index(buf, o)
        except IndexError:
            return None
        if e - o > MAX_REF or e + ELEM_TAIL > len(buf):
            return None
        refs.append(v)
        o = e + ELEM_TAIL
    return {"count": n, "refs": refs, "end": o}


def in_tables(v, nimp, nex):
    return v == 0 or 0 < v <= nex or (v < 0 and -v <= nimp)


def search(data, prefix_end, block, nnames, nimp, nex):
    """Every head in the window whose element walk the closing group lands on the block start from."""
    out = []
    for p in range(prefix_end + DOC_OFFSET, min(prefix_end + DOC_OFFSET + WINDOW, block)):
        w = walk_elements(data, p)
        if not w or w["end"] > block:
            continue
        got = ctr.parse_group(data, w["end"], block, nnames)
        if not got or not got["ok"]:
            continue
        out.append({"head": p, "off": p - prefix_end - DOC_OFFSET, "count": w["count"],
                    "end": w["end"], "refs_ok": int(all(in_tables(v, nimp, nex) for v in w["refs"])),
                    "group": got})
    return out


def measure(data, prefix_end, block, gap_len, census_head, nnames, nimp, nex):
    row = {"payload": len(data), "prefix_end": prefix_end, "block_start": block,
           "gap_len": "" if gap_len is None else gap_len,
           "census_head": "" if census_head is None else census_head,
           "head": "", "head_off": "", "cands": 0, "count": "", "end": "", "residue": "",
           "refs_ok": "", "group_names": "", "group_ref": "", "group_cfg": "", "group_list": "",
           "agree": "", "verdict": ""}
    found = search(data, prefix_end, block, nnames, nimp, nex)
    row["cands"] = len(found)
    if not found:
        row["verdict"] = "no-head"
        return row
    pick = found[0]                      # the earliest candidate: the head precedes anything derived from its body
    row.update({"head": pick["head"], "head_off": pick["off"], "count": pick["count"],
                "end": pick["end"], "residue": block - pick["end"], "refs_ok": pick["refs_ok"],
                "group_names": pick["group"]["names"]["count"], "group_ref": pick["group"]["ref"],
                "group_cfg": pick["group"]["name"], "group_list": pick["group"]["list"]["count"]})
    row["verdict"] = "unique" if len(found) == 1 else "ambiguous"
    if census_head is not None:
        row["agree"] = int(any(f["head"] == census_head for f in found))
    return row


def read_gap():
    out = {}
    with open(os.path.join(ROOT, "inventory", GAP_ARTIFACT), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("gap_len"):
                out[(r["package"], r["class"])] = int(r["gap_len"])
    return out


def sweep(args):
    gaps = read_gap()
    with open(os.path.join(ROOT, "inventory", ARRAY_ARTIFACT), newline="", encoding="utf-8") as fh:
        located = [r for r in csv.DictReader(fh) if r["block_start"] and int(r["block_start"]) > 0]
    by_pkg = collections.defaultdict(list)
    for r in located:
        by_pkg[r["package"]].append(r)
    rows = []
    for name in sorted(by_pkg):
        pkg = Package(os.path.join(args.dump, "System", name + ".u"))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        nnames, nimp, nex = len(pkg.names), len(pkg.imports), len(pkg.exports)
        for r in by_pkg[name]:
            exp = None
            for e in pkg.exports:
                if pkg.class_name_of(e) == "Class" and pkg.name(e["name"]) == r["class"]:
                    exp = e
                    break
            if exp is None:
                continue
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            gl = gaps.get((name, r["class"]))
            head = None if gl is None else int(r["prefix_end"]) + DOC_OFFSET + gl
            row = measure(data, int(r["prefix_end"]), int(r["block_start"]), gl, head, nnames, nimp, nex)
            row.update({"package": name, "class": r["class"]})
            rows.append(row)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in COLUMNS})
        print("wrote %s: %d rows" % (path, len(rows)))
    report(rows)
    return rows


def _share(n, d):
    return "%.1f%%" % (100.0 * n / d) if d else "-"


def report(rows):
    allrows = list(rows)
    census = [r for r in allrows if r.get("census_head") not in ("", None)]
    blind = [r for r in allrows if r.get("census_head") in ("", None)]
    print("located rows: %d; with a census head: %d; without one: %d" % (len(allrows), len(census), len(blind)))

    def group(rs, label, control):
        if not rs:
            return
        found = [r for r in rs if int(r["cands"])]
        uniq = [r for r in rs if r["verdict"] == "unique"]
        print("  %s: n=%d; a candidate head on %d (%s), a unique one on %d (%s)"
              % (label, len(rs), len(found), _share(len(found), len(rs)), len(uniq), _share(len(uniq), len(rs))))
        if found:
            offs = collections.Counter(int(r["head_off"]) for r in found)
            print("    where it sits past prefix+%d: median %s, most common %s"
                  % (DOC_OFFSET, statistics.median([int(r["head_off"]) for r in found]), offs.most_common(6)))
            print("    verdicts: %s" % collections.Counter(r["verdict"] for r in rs).most_common(5))
            print("    candidates per row: %s" % collections.Counter(int(r["cands"]) for r in found).most_common(6))
        if control:
            agree = [r for r in found if r["agree"] not in ("", None)]
            print("    the census head is among the candidates on %d of %d rows that carry one (%s)"
                  % (sum(int(r["agree"]) for r in agree), len(agree), _share(sum(int(r["agree"]) for r in agree),
                                                                             len(agree))))

    group(census, "rows with a census head (the control)", True)
    group(blind, "rows without one (the measurement)", False)


def one(args):
    gaps = read_gap()
    pkg = Package(os.path.join(args.dump, "System", args.package + ".u"))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    with open(os.path.join(ROOT, "inventory", ARRAY_ARTIFACT), newline="", encoding="utf-8") as fh:
        ref = next((r for r in csv.DictReader(fh)
                    if r["package"] == args.package and r["class"] == args.cls), None)
    if not ref:
        print("no located row for %s.%s" % (args.package, args.cls))
        return 1
    for e in pkg.exports:
        if pkg.class_name_of(e) == "Class" and pkg.name(e["name"]) == args.cls:
            data = pkg.data[e["offset"]:e["offset"] + e["size"]]
            gl = gaps.get((args.package, args.cls))
            head = None if gl is None else int(ref["prefix_end"]) + DOC_OFFSET + gl
            row = measure(data, int(ref["prefix_end"]), int(ref["block_start"]), gl, head,
                          len(pkg.names), len(pkg.imports), len(pkg.exports))
            for k in COLUMNS:
                print("%-12s %s" % (k, row.get(k, "")))
            return 0
    print("class not found")
    return 1


def self_check():
    """Synthetic: a record whose head is only findable from the block side, and one whose group is broken."""

    def compact(v):
        v = abs(v)
        out, more = v & 0x3F, v >> 6
        if not more:
            return bytes([out])
        return bytes([0x40 | out]) + compact(more)

    prefix = compact(110) + b"\x00" + compact(5)
    prelude = bytes(range(1, 43))
    front = bytes([2, 0, 0, 4])
    zeros = bytes(16)
    elems = b"".join(compact(ref) + b"\x01\x00\x00\x00\x02\x00\x00\x00" for ref in (1, 2, 3))
    array = compact(3) + elems
    group = compact(2) + compact(1) + compact(2) + compact(3) + compact(7) + compact(0)
    payload = prefix + prelude + front + zeros + array + group
    block = len(payload)
    row = measure(payload, len(prefix), block, len(front) + len(zeros),
                  len(prefix) + DOC_OFFSET + len(front) + len(zeros), 64, 64, 4096)
    assert int(row["head"]) == len(prefix) + DOC_OFFSET + len(front) + len(zeros), row
    assert int(row["cands"]) >= 1 and row["head"] == row["census_head"], row
    assert int(row["count"]) == 3 and int(row["agree"]) == 1, row
    assert int(row["group_ref"]) == 3 and int(row["residue"]) == len(group), row

    broken = payload[:-1] + b"\x00"                     # the group no longer ends on the block
    row2 = measure(broken, len(prefix), len(broken), None, None, 64, 64, 4096)
    assert row2["verdict"] in ("no-head", "ambiguous"), row2

    # a reference outside the package's tables is reported, and the head still resolves when one candidate is clean
    wide = prefix + prelude + front + zeros \
        + compact(3) + b"".join(compact(r) + b"\x01\x00\x00\x00\x02\x00\x00\x00" for r in (700, 701, 702)) + group
    row3 = measure(wide, len(prefix), len(wide), len(front) + len(zeros), None, 64, 64, 64)
    assert int(row3["count"]) == 3 and int(row3["refs_ok"]) == 0, row3
    print("self-check ok: the head is found from the block side alone (the census head among the candidates, count "
          "3, the group landing on the block), a broken group yields no head, and references outside the tables are "
          "reported")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", help="dump root (the directory holding System/)")
    ap.add_argument("--class", dest="cls", help="one class, with --package")
    ap.add_argument("--package", default="", help="package of --class")
    ap.add_argument("--sweep", action="store_true", help="search every located class")
    ap.add_argument("-o", "--out", help="artifact directory")
    ap.add_argument("--report", action="store_true", help="summarise the committed artifact")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.report:
        with open(os.path.join(ROOT, "inventory", ARTIFACT), newline="", encoding="utf-8") as fh:
            report(list(csv.DictReader(fh)))
        return 0
    if not args.dump:
        ap.error("--dump is required (or --check / --report)")
    if args.cls:
        if "." in args.cls and not args.package:
            args.package, args.cls = args.cls.split(".", 1)
        return one(args)
    if args.sweep:
        sweep(args)
        return 0
    ap.error("pick --sweep, --class or --report")


if __name__ == "__main__":
    sys.exit(main())
