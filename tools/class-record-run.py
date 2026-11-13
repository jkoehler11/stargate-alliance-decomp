#!/usr/bin/env python3
"""Read what sits in front of the class record's stride-9 run, and whether its count is there.

`tools/class-record-middle.py` found the 12-byte record array's element by its *period* -- three or more
consecutive 9-byte units whose one dword repeats -- in the middle of a class record, on 536 of 2,204 located
classes, and recorded where the run starts (a median 128 bytes past the prefix's end, modes at +78 and +84).
What it did not answer is the run's own head: an array is a count and then its elements, so the count should end
exactly where the run starts -- and the previous two readings looked for that count in front of the *block*, not
in front of the *run*.

This tool asks one question per class that has a run, and pairs every answer with a null:

* **is a compact count in front of it?** `FUN_000637e0` writes every array count in a cooked package as an
  `FCompactIndex`, so the nearest index that **ends exactly** at the run's first unit is the candidate. It is
  recorded with its gap (0 = adjacent), its length and its value, and `count_match` says whether the value is the
  number of units the run holds. The null is the same test on a run found in a window of the middle's length
  taken from the block start: a rule that matches there as often as here has no discriminating power.
* **or a raw 4-byte count?** `FUN_000637e0` writes a dword instead when neither archive flag is set, which a
  cooked package is not, so this is the negative that keeps the compact claim honest (`dword_match`).
* **what is left in front of it, and behind it?** `front` is the window from the prefix's end to the count
  (or to the run when there is no count), labelled with `class-record-middle.py`'s own shapes; `back` is the
  window from the run's end to the block start. `front_minus_walk` places the count against the end of the field
  list `UClass::Serialize` is documented to write, which is the one anchor already measured.

    python3 tools/class-record-run.py --dump $SG1_DUMP --class AmbientCreatures.HerdAnimal
    python3 tools/class-record-run.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-run.py --report
    python3 tools/class-record-run.py --check

Reads the packages and two committed artifacts (the prefix table and, for the walk's end, the field walk); no
boot, no value decoded.
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
mrm = _load("class-record-middle")

ROOT = os.path.dirname(HERE)
UNIT = mrm.UNIT            # one 12-byte record cooked: 1-byte reference + two dwords
MIN_RUN = mrm.MIN_RUN
BACK = 6                   # how far in front of the run a count may end and still be called its head
EXT_UNITS = 41             # 9-byte steps the array may extend backwards past the run before its count
ARTIFACT = "class_record_run.csv"
COLUMNS = ["package", "class", "payload", "prefix_end", "block_start", "middle", "run_start", "run_units",
           "run_refs", "run_const", "const_shape", "count_gap", "count_at", "count_len", "count_val",
           "count_match", "dword_match",
           "ext_units", "ext_gap", "ext_count_val", "null_hits", "null_positions",
           "front", "front_shape", "back", "back_shape", "walk_end", "front_minus_walk",
           "ctl_units", "ctl_match", "ctl_gap", "exact"]


def const_shape(const):
    """What the one dword that repeats across the run's units looks like as an index or a value."""
    bs = [b for b in const if b]
    if not bs:
        return "zero"
    if len(bs) > 1:
        return "multi"
    b = bs[0]
    if 0x81 <= b <= 0xBF:
        return "neg-index"        # a one-byte FCompactIndex with the sign bit set: an import reference
    if 0x01 <= b <= 0x3F:
        return "pos-index"        # the same with no sign bit
    return "other-byte"


def find_count(buf, end, units):
    """Nearest compact index ending within BACK bytes before `end` whose value is the run's unit count."""
    for gap in range(0, BACK):
        e = end - gap
        if e <= 0:
            break
        for c in range(e - 1, max(e - 6, -1), -1):
            if c < 0:
                continue
            try:
                v, e2 = crf.read_index(buf, c)
            except IndexError:
                continue
            if e2 == e and v == units:
                return {"count_gap": gap, "count_at": c, "count_len": e - c, "count_val": v}
    return None


def dword_count(buf, end):
    """A raw 4-byte little-endian count ending exactly at `end`, which a saving archive does not write."""
    if end < 4:
        return None
    return int.from_bytes(buf[end - 4:end], "little")


def extend_back(buf, start, units):
    """Walk the array's lattice backwards in 9-byte steps for a count that equals the total element count.

    `longest_run` finds the longest stretch of *identical* records; the array's earlier records may each hold a
    different value, so the run can be the array's tail and its count is then in front of an earlier lattice point.
    """
    for k in range(0, EXT_UNITS):
        s = start - UNIT * k
        if s < 0:
            break
        head = find_count(buf, s, units + k)
        if head:
            return k, head
    return None, None


def null_rate(buf, start, end, units):
    """How often the same count test succeeds at a byte position that is not the run's head."""
    hits = total = 0
    for p in range(BACK, len(buf) + 1):
        if start - BACK <= p <= end + BACK:
            continue
        total += 1
        if find_count(buf, p, units) is not None:
            hits += 1
    return hits, total


def measure(data, prefix_end, block_start, nmax, walk_end=None):
    """One class: the run, its head, the windows either side of it, and the control."""
    row = {"payload": len(data), "prefix_end": prefix_end, "block_start": block_start,
           "middle": block_start - prefix_end, "run_start": "", "run_units": "", "run_refs": "",
           "run_const": "", "const_shape": "",
           "count_gap": "", "count_at": "", "count_len": "", "count_val": "", "count_match": 0,
           "dword_match": 0, "ext_units": "", "ext_gap": "", "ext_count_val": "",
           "null_hits": "", "null_positions": "",
           "front": "", "front_shape": "", "back": "", "back_shape": "",
           "walk_end": "" if walk_end is None else walk_end, "front_minus_walk": "",
           "ctl_units": "", "ctl_match": 0, "ctl_gap": "", "exact": 0}
    middle = data[prefix_end:block_start] if block_start >= prefix_end else b""
    units, start, shift, const = mrm.longest_run(middle)
    if units >= MIN_RUN:
        end = start + units * UNIT
        row.update({"run_start": start, "run_units": units, "run_const": const.hex(),
                    "const_shape": const_shape(const),
                    "run_refs": mrm.run_refs(middle, start, units, shift),
                    "exact": int(end == len(middle))})
        head = find_count(middle, start, units)
        if head:
            row.update(head)
            row["count_match"] = 1
            front = middle[:head["count_at"]]
        else:
            front = middle[:start]
        dw = dword_count(middle, start)
        row["dword_match"] = int(dw == units)
        k, ext = extend_back(middle, start, units)
        if k is not None:
            row.update({"ext_units": k, "ext_gap": ext["count_gap"], "ext_count_val": ext["count_val"]})
        hits, total = null_rate(middle, start, end, units)
        row.update({"null_hits": hits, "null_positions": total})
        row["front"] = len(front)
        row["front_shape"] = mrm.shape_of(front, nmax) if front else "empty"
        back = middle[end:]
        row["back"] = len(back)
        row["back_shape"] = mrm.shape_of(back, nmax) if back else "empty"
        if head and walk_end is not None:
            row["front_minus_walk"] = head["count_at"] - walk_end
    ctl_window = data[block_start:block_start + max(len(middle), 0)]
    cu, cs, csh, _ = mrm.longest_run(ctl_window)
    if cu >= MIN_RUN:
        row["ctl_units"] = cu
        ch = find_count(ctl_window, cs, cu)
        if ch:
            row["ctl_match"] = 1
            row["ctl_gap"] = ch["count_gap"]
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
            got = crf.walk_row(data, start)
            row = measure(data, start, block, len(pkg.names), got[0] if got else None)
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


def report(rows):
    rows = [r for r in rows if int(r.get("block_start") or 0) > 0]
    print("classes with a located block start: %d" % len(rows))
    runs = [r for r in rows if r["run_units"] not in ("", None)]
    print("a stride-%d run of >=%d identical units in the middle: %d (%.1f%%)"
          % (UNIT, MIN_RUN, len(runs), _pct(len(runs), len(rows))))
    if not runs:
        return
    hit = [r for r in runs if int(r["count_match"])]
    gaps = collections.Counter(int(r["count_gap"]) for r in hit)
    lens = collections.Counter(int(r["count_len"]) for r in hit)
    print("a compact count equal to the units, ending within %d bytes in front of it: %d of %d (%.1f%%)"
          % (BACK, len(hit), len(runs), _pct(len(hit), len(runs))))
    print("  adjacent (gap 0): %d of %d (%.1f%%); gap distribution %s"
          % (gaps.get(0, 0), len(runs), _pct(gaps.get(0, 0), len(runs)), sorted(gaps.items())))
    print("  index length: %s; the count it spells: %s"
          % (sorted(lens.items()), collections.Counter(int(r["count_val"]) for r in hit).most_common(6)))
    dw = sum(int(r["dword_match"]) for r in runs)
    print("a raw 4-byte count in front of it (what a non-saving archive writes): %d of %d (%.1f%%)"
          % (dw, len(runs), _pct(dw, len(runs))))
    null_h = sum(int(r["null_hits"]) for r in runs if r["null_hits"] not in ("", None))
    null_n = sum(int(r["null_positions"]) for r in runs if r["null_positions"] not in ("", None))
    print("the same test at every other byte position of the same middles: %d of %d (%.1f%%)"
          % (null_h, null_n, _pct(null_h, null_n)))
    ext = [r for r in runs if r["ext_units"] not in ("", None)]
    ext_k0 = [r for r in ext if int(r["ext_units"]) == 0]
    print("a count equal to the total element count, in front of a lattice point 0..%d units back: %d of %d "
          "(%.1f%%); at the run's own head %d, further back %d"
          % (EXT_UNITS - 1, len(ext), len(runs), _pct(len(ext), len(runs)), len(ext_k0), len(ext) - len(ext_k0)))
    if ext:
        print("  units the array extends past the run: %s"
              % collections.Counter(int(r["ext_units"]) for r in ext).most_common(8))
    print("the dword that repeats across the run's units: %s"
          % collections.Counter(r["run_const"] for r in runs).most_common(6))
    print("  its shape, read as one index byte: %s"
          % collections.Counter(r["const_shape"] for r in runs).most_common(6))
    print("  a unit carries %s distinct bytes outside the repeating dword"
          % collections.Counter(int(r["run_refs"]) for r in runs).most_common(4))
    ctl = [r for r in rows if r["ctl_units"] not in ("", None)]
    chit = [r for r in ctl if int(r["ctl_match"])]
    print("control, a window of the middle's length from the block start: a run on %d classes, a matching "
          "count on %d (%.1f%% of the runs, %.1f%% of the classes); gaps %s"
          % (len(ctl), len(chit), _pct(len(chit), len(ctl)), _pct(len(chit), len(rows)),
             sorted(collections.Counter(int(r["ctl_gap"]) for r in chit).items())))

    if hit:
        print("where the count sits relative to the documented field walk's end (count_at - walk_end):")
        fw = _ints([r for r in hit if r["front_minus_walk"] not in ("", None)], "front_minus_walk")
        if fw:
            print("  median %d, most common %s, exact 0 on %d of %d"
                  % (statistics.median(fw), collections.Counter(fw).most_common(6), sum(1 for v in fw if v == 0),
                     len(fw)))
        print("the window in front of the count (prefix end to count_at): %s"
              % collections.Counter(int(r["front"]) for r in hit).most_common(6))
        print("  its shape: %s" % collections.Counter(r["front_shape"] for r in hit).most_common(8))

    miss = [r for r in runs if not int(r["count_match"])]
    print("the run with no count in front of it: %d" % len(miss))
    if miss:
        print("  its start, in bytes from the prefix's end: median %d, most common %s"
              % (statistics.median(_ints(miss, "run_start")), collections.Counter(_ints(miss, "run_start")).most_common(5)))
        print("  the window in front of the run: median %d, shapes %s"
              % (statistics.median(_ints(miss, "front")),
                 collections.Counter(r["front_shape"] for r in miss).most_common(6)))

    print("the window behind the run (its end to the block start): median %d, most common %s"
          % (statistics.median(_ints(runs, "back")), collections.Counter(_ints(runs, "back")).most_common(6)))
    print("  its shape: %s" % collections.Counter(r["back_shape"] for r in runs).most_common(8))
    print("the run ends exactly on the block start: %d" % sum(int(r["exact"]) for r in runs))


def one(args):
    prefixes = crf.read_prefixes()
    pkg = Package(os.path.join(args.dump, "System", args.package + ".u"))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    for exp in pkg.exports:
        if pkg.class_name_of(exp) != "Class" or pkg.name(exp["name"]) != args.cls:
            continue
        if (args.package, args.cls) not in prefixes:
            print("no located block start for this class")
            return 2
        start, block = prefixes[(args.package, args.cls)]
        data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
        got = crf.walk_row(data, start)
        row = measure(data, start, block, len(pkg.names), got[0] if got else None)
        print("%s.%s payload %d prefix %d block start %d middle %d"
              % (args.package, args.cls, len(data), start, block, row["middle"]))
        print("field walk ends %s; the run %s units from %s, %s distinct reference bytes"
              % (row["walk_end"], row["run_units"] or "-", row["run_start"] or "-", row["run_refs"] or "-"))
        if int(row["count_match"]):
            print("count %d at %d (%d bytes, gap %d), %d bytes in front of it (%s), %d behind the run (%s)"
                  % (row["count_val"], row["count_at"], row["count_len"], row["count_gap"], row["front"],
                     row["front_shape"], row["back"], row["back_shape"]))
        else:
            print("no compact count in front of the run; %d bytes in front of it (%s), %d behind (%s); "
                  "a 4-byte count in front: %s"
                  % (row["front"], row["front_shape"], row["back"], row["back_shape"],
                     "yes" if int(row["dword_match"]) else "no"))
        if row["run_units"] not in ("", None):
            for u in range(min(3, int(row["run_units"]))):
                o = start + int(row["run_start"]) + UNIT * u
                print("   %s" % data[o:o + UNIT].hex())
        print("control: run %s units, matching count %s"
              % (row["ctl_units"] or "none", "yes" if int(row["ctl_match"]) else "no"))
        print("middle %s" % data[start:block][:180].hex())
        return 0
    print("no such class")
    return 2


def self_check():
    """Synthetic records: a count in front of a run, and the same run with a byte there instead."""
    def compact(v):
        out, v = v & 0x3F, v >> 6
        if not v:
            return bytes([out])
        return bytes([0x40 | out]) + compact(v)

    def unit(u):
        # one cooked 12-byte record: a one-byte object reference, a dword that repeats, a dword that does not
        return bytes([0x05 + u]) + b"\x11\x22\x33\x44" + bytes([u, 0, 0, 0])

    run = unit(0) + unit(1) + unit(2)
    unit = run[:UNIT]
    back = b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a"
    payload = b"\x07\x00" + b"\x7f" * 6 + compact(3) + run + back + bytes(12)
    row = measure(payload, 2, len(payload) - 12, 8)
    assert row["run_units"] == 3, row
    # the period-9 lattice can be anchored one byte into the unit (the repeating dword starts at unit+1),
    # so the run's start is 7 or 8 and the count is found with a gap of 0 or 1
    assert row["run_start"] in (7, 8) and row["front"] == 6, row
    assert row["count_match"] == 1 and row["count_gap"] in (0, 1) and row["count_val"] == 3, row
    assert row["count_at"] == 6 and row["count_len"] == 1, row
    assert row["run_refs"] == 3, row
    assert row["front_shape"] == "mixed", row
    assert row["back"] in (len(back) - 1, len(back)) and row["exact"] == 0, row
    assert row["dword_match"] == 0, row

    bare = b"\x07\x00" + b"\x7f" * 7 + run + back + bytes(12)
    row2 = measure(bare, 2, len(bare) - 12, 8)
    assert row2["run_units"] == 3 and row2["run_start"] in (7, 8), row2
    assert row2["count_match"] == 0 and row2["front"] == row2["run_start"], row2

    # a count that is not the unit count must not be accepted either
    wrong = b"\x07\x00" + b"\x7f" * 6 + compact(9) + run + back + bytes(12)
    row3 = measure(wrong, 2, len(wrong) - 12, 8)
    assert row3["run_units"] == 3 and row3["count_match"] == 0, row3

    # the array's own head can sit one lattice point back, with a count of the total element count
    lead = bytes([0x09]) + b"\x99\x88\x77\x66" + bytes([0, 0, 0, 0])
    ext_payload = b"\x07\x00" + b"\x7f" * 6 + compact(4) + lead + run + back + bytes(12)
    row4 = measure(ext_payload, 2, len(ext_payload) - 12, 8)
    assert row4["run_units"] == 3 and row4["count_match"] == 0, row4
    assert int(row4["ext_units"]) == 1 and int(row4["ext_count_val"]) == 4, row4
    print("self-check ok: a 3-unit run takes a count of 3 in front, refuses 9 or a bare byte, and finds the "
          "array's head one lattice point back with a count of the whole array")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", help="dump root (the directory holding System/, Maps/, ...)")
    ap.add_argument("--class", dest="cls", help="one class, with --package")
    ap.add_argument("--package", default="", help="package of --class")
    ap.add_argument("--sweep", action="store_true", help="measure every located class")
    ap.add_argument("-o", "--out", help="artifact directory")
    ap.add_argument("--report", action="store_true", help="print the committed artifact's tables")
    ap.add_argument("--check", action="store_true", help="self-test, no dump")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.report:
        path = os.path.join(ROOT, "inventory", ARTIFACT)
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        report(rows)
        return 0
    if not args.dump:
        ap.error("--dump is required (or --check)")
    if args.cls:
        if "." in args.cls and not args.package:
            args.package, args.cls = args.cls.split(".", 1)
        return one(args)
    if args.sweep:
        return sweep(args)
    ap.error("pick --sweep, --class or --report")


if __name__ == "__main__":
    sys.exit(main())
