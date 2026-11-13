#!/usr/bin/env python3
"""Class the bytes between a class record's field list and its defaults block.

`tools/class-record-fields.py` walks the field list `UClass::Serialize` writes with the widths its own writers
use, and on all 2,209 located classes it lands on the defaults block **zero** times, explaining a median 47 bytes
of a 91-byte middle and leaving a median 42 in front of the block. This tool asks what those bytes *are*,
without adding another field to the list:

* **a stride-9 run** -- the 12-byte record array's element is one object reference plus two dwords (`FUN_0003d900`),
  so in a cooked package it is 9 bytes with a one-byte reference, and consecutive identical records leave a run of
  the same 4-byte field every 9 bytes. The tool finds the longest such run in the middle for every class, records
  where it starts (relative to the prefix's end), how many units it holds, which dword repeats, and how many
  distinct reference bytes it carries -- the layout `[reference][dword][dword]` predicts one varying byte and one
  repeating dword;
* **the leftover's shape** -- the window from the field walk's end to the block start is labelled `exact`,
  `overshoot`, `zero` (every byte 0), `records` (a whole number of 9-byte units), `names` (a compact count plus
  two-index elements), `compact` (a chain of in-range compact indices), `dwords` (a whole number of dwords with a
  repeat) or `mixed`;
* **the fit sweep, with a control** -- if the middle is the record's own tail, some start offset for the *same*
  field list should land on the block start. The sweep runs the walk at offsets 0..40 from the prefix's end, with
  the five-dword group before and after the arrays, and records the offset (if any) that ends exactly on the block
  start. The control re-aims the same sweep at `block_start + 8`: a rule that fits the real target and the shifted
  one about equally has no discriminating power, which is how the last three stream-side readings were refused.

    python3 tools/class-record-middle.py --dump $SG1_DUMP --class AmbientCreatures.Birds
    python3 tools/class-record-middle.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-middle.py --report
    python3 tools/class-record-middle.py --check

Reads the packages and two committed artifacts (the prefix table and the field walk); no boot, no value decoded.
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

_spec = importlib.util.spec_from_file_location("class_record_fields", os.path.join(HERE, "class-record-fields.py"))
crf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(crf)

ROOT = os.path.dirname(HERE)
UNIT = 9              # one 12-byte record cooked: 1-byte object reference + two dwords
MIN_RUN = 3           # identical units in a row before it is called a run
SWEEP = 41            # start offsets tried, 0..40
CTL_SHIFT = 8         # the control's target is this far past the real block start
ARTIFACT = "class_record_middle.csv"
COLUMNS = ["package", "class", "payload", "prefix_end", "block_start", "middle", "walk_end", "residual",
           "shape", "run_units", "run_start", "run_const", "run_refs", "run_ctl_units",
           "fit_before", "fit_after", "ctl_before", "ctl_after", "exact"]


def name_count(pkg):
    return len(pkg.names)


def decode_chain(buf, off, nmax):
    """Greedy FCompactIndex chain to the window's end, every index inside the name table."""
    o, k = off, 0
    try:
        while o < len(buf):
            v, o2 = crf.read_index(buf, o)
            if o2 == o or v < 0 or v >= nmax:
                return None
            o, k = o2, k + 1
    except IndexError:
        return None
    return k


def parse_names(buf, nmax):
    """A count then that many two-index `FName` elements, ending exactly on the window's end."""
    try:
        n, o = crf.read_index(buf, 0)
        if n < 0 or n > 64:
            return None
        for _ in range(2 * n):
            v, o = crf.read_index(buf, o)
            if v < 0 or v >= nmax:
                return None
        return n if o == len(buf) else None
    except IndexError:
        return None


def longest_run(buf):
    """Longest run of `MIN_RUN` identical 9-byte units, for every rotation of the repeating 4-byte field."""
    best = (0, 0, 0, b"", 0)
    n = len(buf)
    for shift in (0, 4, 8):
        i = 0
        while i + UNIT * MIN_RUN <= n:
            field = i + shift
            a = buf[field:field + 4]
            if len(a) < 4 or a == b"\0\0\0\0":
                i += 1
                continue
            k = 1
            while (i + UNIT * (k + 1) <= n
                   and buf[field + UNIT * k:field + UNIT * k + 4] == a):
                k += 1
            if k >= MIN_RUN:
                # among equal runs prefer the rotation in which the byte outside the repeating
                # dword varies: that is the record's reference, and the layout it names is
                # [reference][dword][dword] rather than a dword straddling the boundary.
                byte_pos = 8 if shift == 0 else (0 if shift == 4 else 4)
                refs = len({buf[i + UNIT * u + byte_pos] for u in range(k)})
                if (k, refs) > (best[0], best[4]):
                    best = (k, i, shift, a, refs)
            i += 1
    return best[:4]


def run_refs(buf, start, units, shift):
    """Distinct values of the byte that is not inside the repeating dword -- the record's reference."""
    pos = 8 if shift == 0 else (0 if shift == 4 else 4)
    return len({buf[start + UNIT * k + pos:start + UNIT * k + pos + 1] for k in range(units)})


def shape_of(resid, nmax):
    if not resid:
        return "exact"
    if all(b == 0 for b in resid):
        return "zero"
    if parse_names(resid, nmax) is not None:
        return "names"
    units = longest_run(resid)
    if units[0] * UNIT * 2 >= len(resid):
        return "records"
    if decode_chain(resid, 0, nmax) is not None:
        return "compact"
    if len(resid) % 4 == 0:
        dw = [int.from_bytes(resid[i:i + 4], "little") for i in range(0, len(resid), 4)]
        if len(dw) > 1 and len(set(dw)) <= len(dw) // 2:
            return "dwords"
    return "mixed"


def walk_at(data, start, delta, dwords_last):
    """The measured field list, walked from `start + delta`, optionally with the five-dword group after the arrays."""
    try:
        o = start + delta + crf.STATE_BYTES + (0 if dwords_last else 4 * crf.CLASS_DWORDS)
        if o > len(data):
            return None
        count, o = crf.read_index(data, o)
        if count < 0 or count > crf.MAX_COUNT:
            return None
        for _ in range(count):
            _, o = crf.read_index(data, o)
            o += 8
            if o > len(data):
                return None
        _, o = crf.read_index(data, o)
        _, o = crf.read_index(data, o)
        for _ in range(2):
            n, o = crf.read_index(data, o)
            if n < 0 or n > crf.MAX_COUNT:
                return None
            for _ in range(n):
                _, o = crf.read_index(data, o)
                _, o = crf.read_index(data, o)
                if o > len(data):
                    return None
        if dwords_last:
            o += 4 * crf.CLASS_DWORDS
        return o
    except IndexError:
        return None


def fit(data, start, target, dwords_last):
    """Smallest start offset at which the walk ends exactly on `target` under this ordering, or blank."""
    for delta in range(SWEEP):
        if walk_at(data, start, delta, dwords_last) == target:
            return delta
    return ""


def classify(pkg, exp, prefix_end, block_start):
    data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    nmax = name_count(pkg)
    row = {"payload": len(data), "prefix_end": prefix_end, "block_start": block_start,
           "middle": block_start - prefix_end, "walk_end": "", "residual": "", "shape": "no-walk",
           "run_units": "", "run_start": "", "run_const": "", "run_refs": "", "run_ctl_units": "",
           "fit_before": "", "fit_after": "", "ctl_before": "", "ctl_after": "", "exact": 0}
    middle = data[prefix_end:block_start] if block_start >= prefix_end else b""
    units, start, shift, const = longest_run(middle)
    if units >= MIN_RUN:
        row.update({"run_units": units, "run_start": start, "run_const": const.hex(),
                    "run_refs": run_refs(middle, start, units, shift)})
    ctl = longest_run(data[block_start:block_start + max(len(middle), 0)])
    if ctl[0]:
        row["run_ctl_units"] = ctl[0]
    got = crf.walk_row(data, prefix_end)
    if got:
        end = got[0]
        if end > block_start:
            shape = "overshoot"
        elif end == block_start:
            shape = "exact"
        else:
            shape = shape_of(data[end:block_start], nmax)
        row.update({"walk_end": end, "residual": block_start - end, "shape": shape})
    row["fit_before"] = fit(data, prefix_end, block_start, False)
    row["fit_after"] = fit(data, prefix_end, block_start, True)
    row["ctl_before"] = fit(data, prefix_end, block_start + CTL_SHIFT, False)
    row["ctl_after"] = fit(data, prefix_end, block_start + CTL_SHIFT, True)
    return row, data


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
            row, _ = classify(pkg, exp, start, block)
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


def _int(rows, key):
    return [int(r[key]) for r in rows if r[key] not in ("", None)]


def report(rows):
    rows = [r for r in rows if r.get("block_start") not in ("", "0", 0)]
    for r in rows:
        for k in ("middle", "residual", "run_units", "run_start", "run_refs", "run_ctl_units", "exact"):
            if r[k] not in ("", None):
                r[k] = int(r[k])
    print("located classes: %d" % len(rows))
    mid = _int(rows, "middle")
    print("the middle: median %d bytes, p90 %d, max %d"
          % (statistics.median(mid), sorted(mid)[int(len(mid) * 0.9)], max(mid)))
    shapes = collections.Counter(r["shape"] for r in rows)
    print("the field walk: %s" % dict(sorted(shapes.items())))
    print("it ends exactly on the block start: %d" % sum(r["exact"] for r in rows))

    runs = [r for r in rows if r["run_units"] not in ("", None)]
    print("a stride-%d run of >=%d identical units in the middle: %d classes (%.1f%%)"
          % (UNIT, MIN_RUN, len(runs), 100.0 * len(runs) / len(rows)))
    if runs:
        units = [r["run_units"] for r in runs]
        print("  units per run: median %d, p90 %d, max %d (%.0f bytes)"
              % (statistics.median(units), sorted(units)[int(len(units) * 0.9)], max(units),
                 max(units) * UNIT))
        print("  the run's first unit, in bytes from the prefix's end: median %d, p10 %d, p90 %d"
              % (statistics.median([r["run_start"] for r in runs]),
                 sorted(r["run_start"] for r in runs)[int(len(runs) * 0.1)],
                 sorted(r["run_start"] for r in runs)[int(len(runs) * 0.9)]))
        print("  a run whose repeating dword is a constant and whose reference byte varies: %d of %d"
              % (sum(1 for r in runs if r["run_refs"] >= 2), len(runs)))
        starts = collections.Counter(r["run_start"] for r in runs)
        print("  most common start offsets: %s" % starts.most_common(6))
        consts = collections.Counter(r["run_const"] for r in runs)
        print("  most repeated dwords: %s" % consts.most_common(4))

    ctlruns = [r for r in rows if r["run_ctl_units"] not in ("", None)]
    print("the same search on the block itself (a window of the middle's length from the block start): %d classes"
          % len(ctlruns))
    if ctlruns:
        print("  units per run: median %d, max %d"
              % (statistics.median([r["run_ctl_units"] for r in ctlruns]), max(r["run_ctl_units"] for r in ctlruns)))
    resid = [r for r in rows if r["residual"] not in ("", None)]
    print("the leftover window (walk end to block start), %d classes:" % len(resid))
    by_shape = collections.defaultdict(list)
    for r in resid:
        by_shape[r["shape"]].append(r["residual"])
    for shape, lens in sorted(by_shape.items(), key=lambda kv: -len(kv[1])):
        print("  %-8s %4d classes, length median %d, most common %s"
              % (shape, len(lens), statistics.median(lens), collections.Counter(lens).most_common(3)))

    print("the fit sweep (start offset 0..%d, the five-dword group before and after the arrays):" % (SWEEP - 1))
    for order in ("before", "after"):
        fit = _int(rows, "fit_" + order)
        ctl = _int(rows, "ctl_" + order)
        print("  dwords %-6s lands on the block start: %4d classes (%.1f%%); on the control (+%d): %d (%.1f%%)"
              % (order, len(fit), 100.0 * len(fit) / len(rows), CTL_SHIFT, len(ctl),
                 100.0 * len(ctl) / len(rows)))
        if fit:
            print("    offsets that land: %s" % collections.Counter(fit).most_common(6))
    tails = collections.Counter()
    for r in rows:
        if r["residual"] not in ("", None) and r["residual"] > 0:
            tails[int(r["block_start"])] += 0
    print("the leftover's size, classes with one: %d" % sum(1 for r in rows if isinstance(r["residual"], int)
                                                             and r["residual"] > 0))


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
        row, data = classify(pkg, exp, start, block)
        print("%s.%s payload %d prefix_end %d block_start %d middle %d"
              % (args.package, args.cls, row["payload"], start, block, row["middle"]))
        print("field walk ends %s: leftover %s bytes, shape %s"
              % (row["walk_end"], row["residual"], row["shape"]))
        if row["run_units"] not in ("", None):
            print("stride-%d run: %d units from +%d, repeated dword %s, %d distinct reference bytes"
                  % (UNIT, row["run_units"], row["run_start"], row["run_const"], row["run_refs"]))
            i, n = row["run_start"], row["run_units"] * UNIT
            for u in range(min(4, row["run_units"])):
                print("   %s" % data[start + i + UNIT * u:start + i + UNIT * u + UNIT].hex())
        print("fit sweep: before %s/%s, after %s/%s (fit/control)"
              % (row["fit_before"], row["ctl_before"], row["fit_after"], row["ctl_after"]))
        print("middle %s" % data[start:block][:200].hex())
        return 0
    print("no such class")
    return 2


def self_check():
    """Synthetic record: prefix, 22 state bytes, five dwords, a count plus three identical 9-byte units,
    a config FName and two name arrays, then a 12-byte mixed leftover and the defaults block."""
    def compact(v):
        out, v = v & 0x3F, v >> 6
        if not v:
            return bytes([out])
        return bytes([0x40 | out]) + compact(v)

    body = b"\x07\x00"
    data = body + bytes(22) + bytes(20)
    unit = b"\x05" + b"\x11\x22\x33\x44" + b"\x11\x22\x33\x44"     # reference + two dwords = 9 bytes
    data += compact(3) + unit * 3
    data += compact(1) + compact(1)
    data += compact(0) + compact(1) + compact(2) + compact(0)
    block = len(data) + 12
    data += b"\xaa\xbb\xcc\xdd\x01\x00\x00\x00\x02\x03\x00\x00"
    data += bytes(range(1, 20))
    row = classify(_Pkg(data), {"offset": 0, "size": len(data)}, 2, block)[0]
    assert row["middle"] == len(data) - 2 - 19, row
    assert row["walk_end"] != "" and row["residual"] == 12, row
    assert row["run_units"] == 3 and 43 <= row["run_start"] <= 45, row
    assert row["run_const"] in ("05112233", "11223344"), row
    assert row["shape"] == "mixed", row
    assert row["exact"] == 0, row
    print("self-check ok: a 3-unit stride-9 run at +43, a 12-byte mixed leftover, no exact fit")
    return 0


class _Pkg(object):
    """The two attributes `classify` reads off a real package."""

    def __init__(self, data):
        self.data = data
        self.names = ["None"] * 8


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", help="dump root (the directory holding System/, Maps/, ...)")
    ap.add_argument("--class", dest="cls", help="one class, with --package")
    ap.add_argument("--package", default="", help="package of --class")
    ap.add_argument("--sweep", action="store_true", help="classify every located class")
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
