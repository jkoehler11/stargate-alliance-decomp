#!/usr/bin/env python3
"""Read the class record's array at the **writer's** head, and close the record from there.

`inventory/class_record_array.csv` starts its walk at `prefix_end + 42` -- `UStruct`'s prefix, `UState`'s 22 bytes
and five dwords. That position is not where the array begins: the record carries a 19-21 byte field in front of it
(a 3-5 byte compact front and the sixteen zero dwords), measured by `inventory/class_record_gap_front.csv`, and on
`prefix + 42` the walk reads the *front's* first value as a count. Two consequences were on the books:

* the walk's end is a median **27** bytes short of the block start, which is the front (19-21) plus the record's
  closing group (6-9) -- not a residue behind the array;
* on **95** rows it ends **past** the block start ("overshoot"), which no field of a 12-byte element explains.

This tool re-reads both ends at the head the writer uses, `prefix + 42 + gap_len`:

    <front: 3-5 compact> <16 zero bytes> [compact count c] [c x (compact ref, dword, dword)]
    <names array> <reference> <config name> <names array>        then the defaults block

and reports, per row, where each walk lands, which field the block start falls inside when it falls inside one, and
whether the array's end plus `UClass::Serialize`'s four closing fields lands exactly on the block start
(`class-tail-run.parse_group`'s grammar, imported, not re-guessed). The count it reads is joined against the gap
census's `arr_count`, which is a value measured from the payload's other side.

    python3 tools/class-record-array-head.py --dump $SG1_DUMP --class Engine.IMusicActor
    python3 tools/class-record-array-head.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-array-head.py --report
    python3 tools/class-record-array-head.py --check          # synthetic payloads, no dump, writes nothing

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

DOC_OFFSET = 42          # UState's 22 bytes and five dwords: where the shipped walk starts
MAX_COUNT = 4096         # a record array larger than this is not a class tail
MAX_REF = 5              # the writer's own bound for a compact object reference
ELEM_TAIL = 8            # the two raw dwords after each reference
ARRAY_ARTIFACT = "class_record_array.csv"
GAP_ARTIFACT = "class_record_gap_front.csv"
ARTIFACT = "class_record_array_head.csv"
COLUMNS = ["package", "class", "payload", "prefix_end", "block_start", "gap_len", "arr_count",
           "head", "count", "count_ok", "end", "residue", "landing", "field", "field_off",
           "group_closes", "group_ok", "group_names", "group_ref", "group_cfg", "group_list",
           "shipped_count", "shipped_end", "shipped_over", "shipped_landing", "shipped_field", "shipped_off",
           "wide_refs", "elem_min", "elem_max"]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


crf = load("crf", "class-record-fields")
ctr = load("ctr", "class-tail-run")


def read_gap():
    """(gap_len, arr_count) per class from the gap-front census, i.e. where the array's count really sits."""
    out = {}
    path = os.path.join(ROOT, "inventory", GAP_ARTIFACT)
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("gap_len") and r.get("arr_count"):
                out[(r["package"], r["class"])] = (int(r["gap_len"]), int(r["arr_count"]))
    return out


def walk_elements(buf, p):
    """count, then count x (compact reference, dword, dword); element offsets and widths for the landing test."""
    if p < 0 or p >= len(buf):
        return None
    try:
        n, o = crf.read_index(buf, p)
    except IndexError:
        return None
    if n < 1 or n > MAX_COUNT:
        return None
    elems = []
    for i in range(n):
        s = o
        if o >= len(buf):
            return None
        try:
            v, e = crf.read_index(buf, o)
        except IndexError:
            return None
        if e - o > MAX_REF or e + ELEM_TAIL > len(buf):
            return None
        elems.append({"i": i, "start": s, "refw": e - o, "ref": v, "end": e + ELEM_TAIL})
        o = e + ELEM_TAIL
    return {"count": n, "elems": elems, "end": o}


def landing(w, block):
    """Where `block` falls in the element grid: a boundary, one of the element's three fields, or past/before.

    The field matters: if the block start were a misplaced boundary the walk over-consumes a whole field, and the
    answer would repeat per row; if it falls mid-dword the walk's own alignment is wrong.
    """
    if not w:
        return ("no-walk", "", "")
    for e in w["elems"]:
        if e["start"] == block:
            return ("boundary", "", e["i"])
        if e["start"] < block < e["end"]:
            off = block - e["start"]
            if off < e["refw"]:
                return ("ref", "ref", off)
            off -= e["refw"]
            if off < 4:
                return ("d1", "dword1", off)
            return ("d2", "dword2", off - 4)
    if block >= w["elems"][-1]["end"]:
        return ("past", "", block - w["elems"][-1]["end"])
    return ("before", "", w["elems"][0]["start"] - block)


def measure(data, prefix_end, block, gap_len, arr_count, nnames):
    row = {"payload": len(data), "prefix_end": prefix_end, "block_start": block,
           "gap_len": "" if gap_len is None else gap_len,
           "arr_count": "" if arr_count is None else arr_count,
           "head": "", "count": "", "count_ok": "", "end": "", "residue": "", "landing": "",
           "field": "", "field_off": "", "group_closes": 0, "group_ok": 0,
           "group_names": "", "group_ref": "", "group_cfg": "", "group_list": "",
           "shipped_count": "", "shipped_end": "", "shipped_over": "", "shipped_landing": "",
           "shipped_field": "", "shipped_off": "", "wide_refs": "", "elem_min": "", "elem_max": ""}
    # the shipped walk, for the comparison the previous notes quote
    ship = walk_elements(data, prefix_end + DOC_OFFSET)
    if ship:
        kind, field, off = landing(ship, block)
        row.update({"shipped_count": ship["count"], "shipped_end": ship["end"],
                    "shipped_over": ship["end"] - block, "shipped_landing": kind,
                    "shipped_field": field, "shipped_off": off})
    if gap_len is None:
        return row
    head = prefix_end + DOC_OFFSET + gap_len
    row["head"] = head
    w = walk_elements(data, head)
    if not w:
        return row
    kind, field, off = landing(w, block)
    row.update({"count": w["count"], "count_ok": int(arr_count is not None and w["count"] == arr_count),
                "end": w["end"], "residue": block - w["end"], "landing": kind,
                "field": field, "field_off": off,
                "wide_refs": sum(1 for e in w["elems"] if e["refw"] > 1),
                "elem_min": min(e["end"] - e["start"] for e in w["elems"]),
                "elem_max": max(e["end"] - e["start"] for e in w["elems"])})
    if 0 <= w["end"] <= block <= len(data):
        got = ctr.parse_group(data, w["end"], block, nnames)
        if got:
            row.update({"group_closes": 1, "group_ok": int(got["ok"]),
                        "group_names": got["names"]["count"], "group_ref": got["ref"],
                        "group_cfg": got["name"], "group_list": got["list"]["count"]})
    return row


def sweep(args):
    gaps = read_gap()
    rows = []
    with open(os.path.join(ROOT, "inventory", ARRAY_ARTIFACT), newline="", encoding="utf-8") as fh:
        located = [r for r in csv.DictReader(fh) if r["block_start"] and int(r["block_start"]) > 0]
    by_pkg = collections.defaultdict(list)
    for r in located:
        by_pkg[r["package"]].append(r)
    for name in sorted(by_pkg):
        pkg = Package(os.path.join(args.dump, "System", name + ".u"))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        nnames = len(pkg.names)
        for r in by_pkg[name]:
            exp = None
            for e in pkg.exports:
                if pkg.class_name_of(e) == "Class" and pkg.name(e["name"]) == r["class"]:
                    exp = e
                    break
            if exp is None:
                continue
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            gl, ac = gaps.get((name, r["class"]), (None, None))
            row = measure(data, int(r["prefix_end"]), int(r["block_start"]), gl, ac, nnames)
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
    allrows = [r for r in rows]
    withship = [r for r in allrows if str(r.get("shipped_end") or "") != ""]
    head = [r for r in allrows if str(r.get("count") or "") != ""]
    nohead = len(allrows) - len(head)

    def group(rs, label):
        if not rs:
            return
        res = [int(r["residue"]) for r in rs if str(r["residue"]) != ""]
        past = sum(1 for r in rs if r["landing"] == "past")
        inside = collections.Counter(r["landing"] for r in rs if r["landing"] in ("ref", "d1", "d2", "boundary"))
        closes = sum(int(r["group_closes"]) for r in rs)
        cnt = sum(int(r["count_ok"]) for r in rs)
        print("  %s: n=%d" % (label, len(rs)))
        print("    the block start past the array end on %d (%s); inside or on an element on %d%s"
              % (past, _share(past, len(rs)), sum(inside.values()),
                 (" " + str(inside.most_common(5))) if inside else ""))
        if res:
            print("    residue (block - array end): median %d, most common %s"
                  % (statistics.median(res), collections.Counter(res).most_common(6)))
        print("    count equals the gap census arr_count on %d (%s)" % (cnt, _share(cnt, len(rs))))
        print("    the array's end plus the closing group lands on the block start on %d (%s)"
              % (closes, _share(closes, len(rs))))

    print("located rows: %d; the shipped walk (prefix+42) answers on %d, the writer's head on %d (%d have no "
          "gap-front row)" % (len(allrows), len(withship), len(head), nohead))
    group(head, "at the writer's head (prefix+42+gap_len)")
    if withship:
        over = [r for r in withship if int(r["shipped_over"]) > 0]
        ok = [r for r in withship if int(r["shipped_over"]) <= 0]
        print("the shipped walk: past the block start on %d of %d, at or before it on %d"
              % (len(over), len(withship), len(ok)))
        group([r for r in head if int(r.get("shipped_over") or 0) > 0], "  those overshoot rows, at the true head")
        group([r for r in head if r.get("shipped_over") not in ("", None) and int(r["shipped_over"]) <= 0],
              "  the at-or-before rows, at the true head")
        if over:
            print("  the shipped walk's landing on the overshoot rows: %s"
                  % collections.Counter(r["shipped_landing"] for r in over).most_common(6))
            print("  which field of an element it lands in: %s"
                  % collections.Counter((r["shipped_field"], r["shipped_off"]) for r in over
                                        if r["shipped_field"]).most_common(6))
    fronts = [int(r["gap_len"]) for r in head if str(r["gap_len"]) != ""]
    if fronts:
        print("the front in front of the array: median %d, most common %s"
              % (statistics.median(fronts), collections.Counter(fronts).most_common(5)))
    wide = [int(r["wide_refs"]) for r in head if str(r["wide_refs"]) != ""]
    if wide:
        print("elements whose reference is wider than one byte: %d rows, %d references; element bytes %s..%s"
              % (sum(1 for v in wide if v), sum(wide),
                 collections.Counter(r["elem_min"] for r in head).most_common(3),
                 collections.Counter(r["elem_max"] for r in head).most_common(3)))


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
            gl, ac = gaps.get((args.package, args.cls), (None, None))
            row = measure(data, int(ref["prefix_end"]), int(ref["block_start"]), gl, ac, len(pkg.names))
            for k in COLUMNS:
                print("%-16s %s" % (k, row.get(k, "")))
            return 0
    print("class not found")
    return 1


def self_check():
    """Synthetic: a front, the 16 zero bytes, the array, the closing group and the block -- and the shipped walk."""

    def compact(v):
        v = abs(v)
        out, more = v & 0x3F, v >> 6
        if not more:
            return bytes([out])
        return bytes([0x40 | out]) + compact(more)

    def element(ref, d1=b"\x01\x00\x00\x00", d2=b"\x02\x00\x00\x00"):
        return compact(ref) + d1 + d2

    prefix = compact(110) + b"\x00" + compact(5)          # the 3-6 byte struct prefix
    prelude = bytes(range(1, 43))                         # UState's 22 bytes and the five dwords
    front = bytes([2, 0, 0, 4])          # the front's first value is small: the shipped head reads it as a count
    zeros = bytes(16)
    elems = b"".join(element(ref) for ref in (1, 2, 3))
    array = compact(3) + elems
    group = compact(2) + compact(1) + compact(2) + compact(3) + compact(7) + compact(0)
    payload = prefix + prelude + front + zeros + array + group
    block = len(payload)
    row = measure(payload, len(prefix), block, len(front) + len(zeros), 3, 64)
    assert int(row["count"]) == 3 and int(row["count_ok"]) == 1, row
    assert int(row["end"]) == block - len(group) and int(row["residue"]) == len(group), row
    assert row["landing"] == "past", row                     # the writer's head never crosses the block
    assert int(row["group_closes"]) == 1 and int(row["group_ref"]) == 3, row
    assert int(row["shipped_count"]) == 2, row               # the shipped head reads the front as an array
    assert int(row["shipped_end"]) < int(row["head"]), row    # and lands short of the array it cannot see

    # a group that does not land on the block is refused, not hidden
    short = prefix + prelude + front + zeros + array + group + b"\x00"
    row2 = measure(short, len(prefix), len(short), len(front) + len(zeros), 3, 64)
    assert int(row2["group_closes"]) == 0 and int(row2["residue"]) == len(group) + 1, row2

    # a count that disagrees with the census is reported, and the element widths are measured, not assumed
    narrow = prefix + prelude + front + zeros + compact(3) \
        + b"".join(element(700 if i == 0 else 1, b"\x01\x00\x00\x00") for i in range(3)) + group
    row3 = measure(narrow, len(prefix), len(narrow), len(front) + len(zeros), 4, 64)
    assert int(row3["count_ok"]) == 0 and int(row3["count"]) == 3, row3
    assert int(row3["wide_refs"]) == 1 and int(row3["elem_max"]) > int(row3["elem_min"]), row3
    print("self-check ok: the array is read at prefix+42+gap_len (count 3, count_ok, end at the group), the closing "
          "group lands on the block start, a payload one byte long is refused, a census disagreement is reported, "
          "and wide references are counted")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", help="dump root (the directory holding System/)")
    ap.add_argument("--class", dest="cls", help="one class, with --package")
    ap.add_argument("--package", default="", help="package of --class")
    ap.add_argument("--sweep", action="store_true", help="read every located class")
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
