#!/usr/bin/env python3
"""Bound every located defaults block start by the class record's own array head.

`docs/CLASS_DEFAULTS.md` and U18's tenth entry were left with one test they do not have: the tiling test that read
`GUI.MenuItemOverlay`'s 110 bytes as twelve streamed record units also tiles at each of four shifted starts (159 of
181 blocks tile at their own start, 151-153 of 181 at a start one to four bytes in), so tiling cannot say whether a
located block start sits inside the record array. The array's own head can, because it is a *bound* rather than a
pattern: `UClass::Serialize` (`0x0003a800`) writes `UStruct::Serialize` (the record's measured 3-6 byte prefix) and
then, unconditionally, `UState::Serialize`'s 22 bytes and five dwords, so its `+0x4d0` array is read at
`prefix_end + 42` on every class; `FUN_0003d900` streams that array as a compact count and then one
`[object reference][dword][dword]` per element, and `FUN_0003d160` streams the version-gated `TArray<FName>`
siblings as a compact count and two compact indices per element. Nothing else is written between the last of those
fields and the defaults block, so the end of the last name array is the **earliest byte the block can start at**.

A count byte is cheap to satisfy, so the head is only credited when it validates:

* every element's reference is an object reference inside the package's own tables (`0`, `-nimp`..`-1`,
  `1`..`nex`), which is the archive `+0x18` method's own contract;
* every index of a name array is inside the package's name table, which is the `+0x1c` method's;
* both counts are within the array bound the writer's callers imply.

The bound is reported for one and for two name arrays, because the version gate (`archive version > 0x62`, and these
packages are 130/4) says two: a start that is inside the array's *elements* is a contradiction under either reading,
while a start inside the second name array alone is only a contradiction for this build's version.

    python3 tools/class-record-bound.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-bound.py --report
    python3 tools/class-record-bound.py --check          # synthetic bodies, no dump, writes nothing

Reads the packages and the committed prefix table; no boot, no Ghidra round, no defaults value decoded.
"""
import argparse
import collections
import csv
import importlib.util
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402


def _load(mod):
    spec = importlib.util.spec_from_file_location(mod, os.path.join(HERE, mod + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


crf = _load("class-record-fields")

ROOT = os.path.dirname(HERE)
STATE_BYTES = crf.STATE_BYTES                  # UState::Serialize, 22 bytes at +0x80
CLASS_DWORDS = crf.CLASS_DWORDS                # five dwords at +0x498..+0x4ac
DOC_OFFSET = STATE_BYTES + 4 * CLASS_DWORDS    # +0x4d0: where UClass writes its own array
MAX_COUNT = 4096                               # a class-record array larger than this is not a class tail
MAX_NAMES = 4096                               # same bound for a name array
MAX_REF = 5                                    # the writer's own compact-reference bound
ARTIFACT = "class_record_bound.csv"
COLUMNS = ["package", "class", "payload", "prefix_end", "block_start", "middle",
           "count", "elems_end", "refs_ok", "names_a", "names_a_ok", "names_b", "names_b_ok",
           "end_k1", "end_k2", "margin_k1", "margin_k2", "inside_bare", "inside_k1", "inside_k2",
           "ctl_count", "ctl_inside"]


def read_count(buf, p):
    """A compact count at `p`, or None when it cannot be one."""
    if p < 0 or p >= len(buf):
        return None
    try:
        v, e = crf.read_index(buf, p)
    except IndexError:
        return None
    if v < 0 or v > MAX_COUNT:
        return None
    return v, e


def walk_elements(buf, p, nimp, nex):
    """The record array as `FUN_0003d900` streams it, credited only when every reference lands in a table."""
    got = read_count(buf, p)
    if not got:
        return None
    count, o = got
    if count < 1:
        return None
    for _ in range(count):
        if o >= len(buf):
            return None
        try:
            v, e = crf.read_index(buf, o)
        except IndexError:
            return None
        if e - o > MAX_REF or e + 8 > len(buf):
            return None
        if not (v == 0 or (v > 0 and v <= nex) or (v < 0 and -v <= nimp)):
            return {"count": count, "end": o, "refs_ok": 0}
        o = e + 8
    return {"count": count, "end": o, "refs_ok": 1}


def walk_names(buf, p, nnames):
    """One `TArray<FName>` as `FUN_0003d160` streams it: a compact count then two compact indices per element."""
    got = read_count(buf, p)
    if not got:
        return None
    n, o = got
    if n > MAX_NAMES:
        return None
    for _ in range(n):
        for _ in range(2):
            if o >= len(buf):
                return None
            try:
                v, e = crf.read_index(buf, o)
            except IndexError:
                return None
            if not (0 <= v < nnames):
                return {"count": n, "end": o, "ok": 0}
            o = e
    return {"count": n, "end": o, "ok": 1}


def bound(data, p, block_start, nimp, nex, nnames):
    """The record's own end at the writer's position, and whether the located start is beyond it."""
    row = {"count": "", "elems_end": "", "refs_ok": 0, "names_a": "", "names_a_ok": 0,
           "names_b": "", "names_b_ok": 0, "end_k1": "", "end_k2": "",
           "margin_k1": "", "margin_k2": "", "inside_bare": "", "inside_k1": "", "inside_k2": ""}
    arr = walk_elements(data, p, nimp, nex)
    if not arr:
        return row
    row.update({"count": arr["count"], "refs_ok": arr["refs_ok"]})
    if not arr["refs_ok"]:
        # a reference outside both tables is not an object reference, so the count is not credited and neither the
        # element span nor a bound exists: `elems_end` here would be the position of the first bad reference
        return row
    row["elems_end"] = arr["end"]
    row["inside_bare"] = int(block_start < arr["end"])
    a = walk_names(data, arr["end"], nnames)
    if not a or not a["ok"]:
        return row
    row.update({"names_a": a["count"], "names_a_ok": a["ok"], "end_k1": a["end"],
                "margin_k1": block_start - a["end"], "inside_k1": int(block_start < a["end"])})
    b = walk_names(data, a["end"], nnames)
    if not b or not b["ok"]:
        return row
    row.update({"names_b": b["count"], "names_b_ok": b["ok"], "end_k2": b["end"],
                "margin_k2": block_start - b["end"], "inside_k2": int(block_start < b["end"])})
    return row


def measure(data, prefix_end, block_start, nimp, nex, nnames):
    row = {"payload": len(data), "prefix_end": prefix_end, "block_start": block_start,
           "middle": block_start - prefix_end, "count": "", "elems_end": "", "refs_ok": 0,
           "names_a": "", "names_a_ok": 0, "names_b": "", "names_b_ok": 0,
           "end_k1": "", "end_k2": "", "margin_k1": "", "margin_k2": "",
           "inside_bare": "", "inside_k1": "", "inside_k2": "", "ctl_count": "", "ctl_inside": ""}
    if block_start <= prefix_end:
        return row
    p = prefix_end + DOC_OFFSET
    row.update(bound(data, p, block_start, nimp, nex, nnames))
    # control: the same grammar one byte later. A position that is the writer's must do worse than +42.
    ctl = bound(data, p + 1, block_start, nimp, nex, nnames)
    row["ctl_count"] = ctl["count"]
    row["ctl_inside"] = ctl["inside_k2"]
    return row


def sweep(args):
    prefixes = crf.read_prefixes()
    rows = []
    for fn in sorted(os.listdir(os.path.join(args.dump, "System"))):
        if not fn.endswith(".u"):
            continue
        pkg = Package(os.path.join(args.dump, "System", fn))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        name = fn[:-2]
        nimp, nex, nnames = len(pkg.imports), len(pkg.exports), len(pkg.names)
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            key = (name, pkg.name(exp["name"]))
            if key not in prefixes:
                continue
            start, block = prefixes[key]
            if block <= 0:
                continue
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            row = measure(data, start, block, nimp, nex, nnames)
            row.update({"package": name, "class": key[1]})
            rows.append(row)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
        print("wrote %s: %d rows" % (path, len(rows)))
    report(rows)
    return 0


def _ints(rows, key):
    return [int(r[key]) for r in rows if r[key] not in ("", None)]


def _pct(n, d):
    return 100.0 * n / d if d else 0.0


def report(rows=None):
    if rows is None:
        path = os.path.join(ROOT, "inventory", ARTIFACT)
        if not os.path.exists(path):
            print("no %s -- run --sweep first" % ARTIFACT)
            return 1
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    rows = [r for r in rows if int(r.get("block_start") or 0) > 0]
    n = len(rows)
    head = [r for r in rows if r.get("count") not in ("", None)]
    print("classes with a located block start: %d" % n)
    print("the array head decodes at the writer's position (prefix + %d): %d (%.1f%%)"
          % (DOC_OFFSET, len(head), _pct(len(head), n)))
    ok = [r for r in head if int(r.get("refs_ok") or 0)]
    print("  every element reference inside the package's tables: %d (%.1f%%)" % (len(ok), _pct(len(ok), n)))
    both = [r for r in ok if r.get("end_k2") not in ("", None)]
    print("  both name arrays decode with every index inside the name table: %d (%.1f%%)"
          % (len(both), _pct(len(both), n)))
    if both:
        print("  the count on those rows: median %d, most common %s"
              % (statistics.median(_ints(both, "count")), collections.Counter(_ints(both, "count")).most_common(5)))
        print("  name-array elements: first %s, second %s"
              % (collections.Counter(_ints(both, "names_a")).most_common(4),
                 collections.Counter(_ints(both, "names_b")).most_common(4)))
        m2 = _ints(both, "margin_k2")
        print("  the block start past the record's own end: median %d, most common %s"
              % (statistics.median(m2), collections.Counter(m2).most_common(6)))
        print("  the block start inside that end: %d (k=2), %d (k=1), %d inside the elements alone"
              % (sum(int(r["inside_k2"]) for r in both), sum(int(r["inside_k1"]) for r in both),
                 sum(int(r["inside_bare"]) for r in both)))
        neg = [r for r in both if int(r["inside_k2"])]
        for r in neg[:12]:
            print("    inside: %s.%s payload %s start %s count %s elems_end %s end_k2 %s margin %s"
                  % (r["package"], r["class"], r["payload"], r["block_start"], r["count"],
                     r["elems_end"], r["end_k2"], r["margin_k2"]))
    # the elements alone are the strongest claim: no name-array reading is needed to contradict a start there
    bare = [r for r in ok if int(r.get("inside_bare") or 0)]
    print("  block start inside the array's own elements, on the table-checked heads: %d" % len(bare))
    for r in bare[:12]:
        print("    inside: %s.%s payload %s start %s count %s elems_end %s"
              % (r["package"], r["class"], r["payload"], r["block_start"], r["count"], r["elems_end"]))
    ctl = [r for r in head if r.get("ctl_count") not in ("", None)]
    print("the same grammar one byte later (prefix + %d): a head on %d, a fully validated bound on %d"
          % (DOC_OFFSET + 1, len(ctl), sum(1 for r in ctl if r["ctl_inside"] not in ("", None))))
    for want in (("GUI", "MenuItemOverlay"), ("StargateMenus", "ExtrasPage"), ("StargateMenus", "CreditsPage")):
        for r in rows:
            if (r["package"], r["class"]) == want:
                print("  %s.%s: payload %s, middle %s, start %s, count %s, elems_end %s, end_k2 %s, margin_k2 %s"
                      % (r["package"], r["class"], r["payload"], r["middle"], r["block_start"],
                         r.get("count") or "-", r.get("elems_end") or "-", r.get("end_k2") or "-",
                         r.get("margin_k2") or "-"))
    return 0


def _enc(v):
    """The archive's `FCompactIndex` for `v`, the spelling `crf.read_index` decodes."""
    sign = 0x80 if v < 0 else 0
    v = abs(v)
    if v < 0x40:
        return bytes([sign | v])
    if v < 0x2000:
        return bytes([sign | 0x40 | (v & 0x3F), v >> 6])
    return bytes([sign | 0x40 | (v & 0x3F), 0x80 | ((v >> 6) & 0x7F), v >> 13])


def _record(count, refs, names_a, names_b, prefix=3, block_gap=8):
    """A synthetic class payload: prefix, the 42-byte middle, the array, two name arrays, the gap, the block."""
    elems = b"".join(_enc(ref) + b"\x11\x22\x33\x44" + b"\x55\x66\x77\x88" for ref in refs)
    prelude = bytes(range(1, STATE_BYTES + 1)) + b"\x01\x00\x00\x00" * CLASS_DWORDS
    body = _enc(count) + elems
    body += _enc(len(names_a)) + b"".join(_enc(n) + _enc(0) for n in names_a)
    body += _enc(len(names_b)) + b"".join(_enc(n) + _enc(0) for n in names_b)
    head = bytes(prefix) + prelude
    block = len(head) + len(body) + block_gap
    return head + body + bytes(max(0, block_gap)), prefix, block


def selftest():
    cases = 0
    # 1. a head whose references and name indices are all in range bounds the block exactly, and its margin is 0
    data, start, block = _record(3, (0, 2, -1), (1, 2), (3,), block_gap=0)
    row = measure(data, start, block, 4, 8, 9)
    assert int(row["count"]) == 3 and int(row["refs_ok"]) == 1, row
    assert int(row["end_k2"]) == block and int(row["margin_k2"]) == 0, row
    assert int(row["inside_k2"]) == 0 and int(row["inside_bare"]) == 0, row
    cases += 1
    # 2. a reference outside both tables refuses the head: no count is credited
    data, start, block = _record(2, (0, 99), (1,), (2,), block_gap=0)
    row = measure(data, start, block, 4, 8, 9)
    assert row["count"] not in ("", None) and int(row["refs_ok"]) == 0, row
    assert row["end_k2"] in ("", None) and row["margin_k2"] in ("", None), row
    cases += 1
    # 3. a name index past the name table refuses the name array, so the bound stays at k=1
    data, start, block = _record(2, (0, 1), (1, 99), (2,), block_gap=0)
    row = measure(data, start, block, 4, 8, 9)
    assert int(row["refs_ok"]) == 1 and row["names_a_ok"] in (0, "") , row
    assert row["end_k1"] in ("", None) and row["end_k2"] in ("", None), row
    cases += 1
    # 4. a block start inside the elements is flagged, and one inside only the second name array at k=2
    data, start, block = _record(4, (0, 1, 2, 3), (1,), (2,), block_gap=-40)
    row = measure(data, start, block, 4, 8, 9)
    assert int(row["inside_bare"]) == 1, row
    data, start, block = _record(2, (0, 1), (1,), (2,), block_gap=0)
    row = measure(data, start, block, 4, 8, 9)
    mid = int(row["end_k1"])
    row = measure(data, start, mid, 4, 8, 9)
    assert int(row["inside_k1"]) == 0 and int(row["inside_k2"]) == 1, row
    cases += 1
    # 5. the count is compact and the element stride is nine: a two-byte count shifts the bound by one
    assert len(_enc(4)) == 1 and len(_enc(64)) == 2, (_enc(4), _enc(64))
    data, start, block = _record(64, [0] * 64, (1,), (2,), block_gap=0)
    row = measure(data, start, block, 4, 8, 9)
    assert int(row["count"]) == 64 and int(row["margin_k2"]) == 0, row
    cases += 1
    print("class-record-bound: selftest passed (%d cases)" % cases)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default=os.environ.get("SG1_DUMP", ""))
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return selftest()
    if args.report:
        return report()
    if args.sweep:
        dump = args.dump
        game = "Stargate_SG-1_-_The_Alliance-2005-12-15"
        if dump and os.path.isdir(os.path.join(dump, game)):
            dump = os.path.join(dump, game)
        if not dump or not os.path.isdir(os.path.join(dump, "System")):
            print("no System/ under %r -- set SG1_DUMP" % dump)
            return 2
        args.dump = dump
        return sweep(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
