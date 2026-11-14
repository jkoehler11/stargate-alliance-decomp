#!/usr/bin/env python3
"""What the three to five bytes in front of the class record's array can and cannot be.

The forward walk of the writer's order closes on 1,626 of 2,204 located class payloads, and on 1,482 of them it
steps over a gap of 19 to 21 bytes made of a 3-5 byte front and a sixteen-zero run
(`docs/NATIVE_VM.md`, *The gap's last sixteen bytes are zero*). The zone run is the four dwords `UStruct::Serialize`
writes (`0x370e0` at `+0x5c`, two `0x37110` at `+0x58` and `+0x54`, the script count at `+0x4c`); the front is
written by no body in the chain (`0x38dc0`, `0x392b0`, `0x394c0`, `0x3a800`).

So the front is identified by what it can be, against controls. Four readings are tested on every class, and each
one is also run on three same-length windows that a real field would not be found in:

* **a name** -- every value a valid index into the package's own name table;
* **a property tag** -- the first value resolving to a name the class itself declares as a `Property` export;
* **an object reference** -- every value either a positive export index or a negative import index of the package;
* **a compact index of any width** -- whether any value needed more than one byte, which a name or export index
  above 63 must.

    python3 tools/class-record-front.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-front.py --report
    python3 tools/class-record-front.py --dump $SG1_DUMP --class AmbientCreatures.Bird
    python3 tools/class-record-front.py --check

Reads the packages and the committed gap census; no boot, no value decoded, no game data written.
"""
import argparse
import collections
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402

ROOT = os.path.dirname(HERE)
GAP_ARTIFACT = "class_record_gap_front.csv"
ARTIFACT = "class_record_front.csv"
DOC_OFFSET = 42          # UState's 22 bytes and the five dwords: where the walk steps over its gap
TAIL_BYTES = 16          # the four dwords the struct body writes, at the gap's own end
COLUMNS = ["package", "class", "gap_len", "front_len", "front_hex", "values", "value_count",
           "all_single_byte", "names_resolved", "declared_first", "refs_in_tables",
           "refs_nonzero", "refs_nonzero_in_tables",
           "ctl_shift_names", "ctl_shift_declared", "ctl_shift_refs",
           "ctl_before_names", "ctl_before_declared", "ctl_before_refs",
           "ctl_block_names", "ctl_block_declared", "ctl_block_refs"]


def read_index(buf, off):
    """UE2 `FCompactIndex`: bit 7 is the sign, bit 6 means a byte follows, 6 bits then 7 per byte."""
    b = buf[off]
    sign, cont, val = b & 0x80, b & 0x40, b & 0x3F
    off += 1
    shift = 6
    while cont and shift < 32:
        b = buf[off]
        off += 1
        cont = b & 0x80
        val |= (b & 0x7F) << shift
        shift += 7
    return (-val if sign else val), off


def parse_front(buf):
    """The window as compact values: `(values, widths)`, or `(None, None)` if a width overruns it."""
    vals, widths, o = [], [], 0
    while o < len(buf):
        try:
            v, e = read_index(buf, o)
        except IndexError:
            return None, None
        if e <= o or e > len(buf):
            return None, None
        vals.append(v)
        widths.append(e - o)
        o = e
    return vals, widths


def score(window, names_n, nexports, nimps, declared, name_of):
    """The four readings on one window, plus the values themselves."""
    vals, widths = parse_front(window)
    out = {"values": "", "value_count": 0, "all_single_byte": 0, "names_resolved": 0,
           "declared_first": 0, "refs_in_tables": 0, "refs_nonzero": 0, "refs_nonzero_in_tables": 0}
    if not vals:
        return out
    out["values"] = " ".join(str(v) for v in vals)
    out["value_count"] = len(vals)
    out["all_single_byte"] = int(all(w == 1 for w in widths))
    out["names_resolved"] = int(all(0 <= v < names_n for v in vals))
    first = vals[0]
    nm = name_of(first) if 0 <= first < names_n else ""
    out["declared_first"] = int(bool(nm) and nm in declared)
    out["refs_in_tables"] = sum(1 for v in vals
                                if (v == 0) or (0 < v <= nexports) or (0 > v >= -nimps))
    out["refs_nonzero"] = sum(1 for v in vals if v != 0)
    out["refs_nonzero_in_tables"] = sum(1 for v in vals
                                        if (0 < v <= nexports) or (0 > v >= -nimps))
    return out


def gap_rows():
    path = os.path.join(ROOT, "inventory", GAP_ARTIFACT)
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def property_names(pkg):
    """Per class export index, the names of the `Property` exports whose Outer is that class."""
    props = collections.defaultdict(set)
    for exp in pkg.exports:
        if pkg.class_name_of(exp).endswith("Property"):
            props[exp["outer"]].add(pkg.name(exp["name"]))
    return props


def measure_package(pkgname, rows, dump, out):
    pkg = Package(os.path.join(dump, "System", pkgname + ".u"))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    nnames, nexports, nimps = len(pkg.names), len(pkg.exports), len(pkg.imports)
    name_of = lambda i: pkg.name(i)  # noqa: E731
    props = property_names(pkg)
    index = {}
    for k, exp in enumerate(pkg.exports, start=1):
        index.setdefault(pkg.name(exp["name"]), (k, exp))
    for r in rows:
        row = {"package": pkgname, "class": r["class"], "gap_len": r["gap_len"],
               "front_len": "", "front_hex": "", "values": "", "value_count": 0,
               "all_single_byte": 0, "names_resolved": 0, "declared_first": 0, "refs_in_tables": 0,
               "refs_nonzero": 0, "refs_nonzero_in_tables": 0,
               "ctl_shift_names": 0, "ctl_shift_declared": 0, "ctl_shift_refs": 0,
               "ctl_before_names": 0, "ctl_before_declared": 0, "ctl_before_refs": 0,
               "ctl_block_names": 0, "ctl_block_declared": 0, "ctl_block_refs": 0}
        out.append(row)
        if r["class"] not in index or not r["gap_len"]:
            continue
        flen = int(r["gap_len"]) - TAIL_BYTES
        if flen <= 0:
            continue
        k, exp = index[r["class"]]
        declared = props.get(k, set())
        data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
        start = int(r["prefix_end"]) + DOC_OFFSET
        block = int(r["block_start"])
        if start + flen > len(data):
            continue
        row["front_len"] = flen
        row["front_hex"] = data[start:start + flen].hex(" ")
        got = score(data[start:start + flen], nnames, nexports, nimps, declared, name_of)
        row.update(got)
        # controls: the same-length window one byte later inside the same gap, one window earlier, and the
        # bytes immediately before the defaults block
        for prefix, pos in (("ctl_shift", start + 1), ("ctl_before", start - flen), ("ctl_block", block - flen)):
            if not (0 <= pos and pos + flen <= len(data)):
                continue
            c = score(data[pos:pos + flen], nnames, nexports, nimps, declared, name_of)
            row[prefix + "_names"] = c["names_resolved"]
            row[prefix + "_declared"] = c["declared_first"]
            row[prefix + "_refs"] = c["refs_in_tables"]
    return out


def sweep(args):
    rows = gap_rows()
    by_pkg = collections.defaultdict(list)
    for r in rows:
        by_pkg[r["package"]].append(r)
    out = []
    for pkgname in sorted(by_pkg):
        measure_package(pkgname, by_pkg[pkgname], args.dump, out)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(out)
        print("wrote %s: %d rows" % (path, len(out)))
    report(out)
    return 0


def _pct(n, d):
    return 100.0 * n / d if d else 0.0


def report(rows):
    rows = [r for r in rows if r.get("front_len") not in ("", None)]
    print("gap rows with a front: %d" % len(rows))
    if not rows:
        return
    n = len(rows)
    lens = collections.Counter(int(r["front_len"]) for r in rows)
    print("front lengths: %s" % lens.most_common(6))
    single = sum(int(r["all_single_byte"]) for r in rows)
    vmax = max((int(v) for r in rows for v in r["values"].split()), default=0)
    print("fronts whose every value is one byte: %d (%.1f%%); largest value %d"
          % (single, _pct(single, n), vmax))
    print("fronts whose values are all inside the name table: %d (%.1f%%)" % (sum(int(r["names_resolved"]) for r in rows),
                                                                             _pct(sum(int(r["names_resolved"]) for r in rows), n)))
    print("fronts whose first value names a property the class declares: %d (%.1f%%)"
          % (sum(int(r["declared_first"]) for r in rows), _pct(sum(int(r["declared_first"]) for r in rows), n)))
    refs = sum(int(r["refs_in_tables"]) for r in rows)
    vals = sum(int(r["value_count"]) for r in rows)
    print("front values that are valid object references: %d of %d (%.1f%%)" % (refs, vals, _pct(refs, vals)))
    nz = sum(int(r["refs_nonzero"]) for r in rows)
    nzi = sum(int(r["refs_nonzero_in_tables"]) for r in rows)
    print("of the non-zero values, inside the export/import tables: %d of %d (%.1f%%)" % (nzi, nz, _pct(nzi, nz)))
    for label, key in (("one byte later", "ctl_shift"), ("one window earlier", "ctl_before"),
                       ("before the block", "ctl_block")):
        nm = sum(int(r[key + "_names"]) for r in rows)
        dc = sum(int(r[key + "_declared"]) for r in rows)
        rf = sum(int(r[key + "_refs"]) for r in rows)
        print("  control, %-18s names %d (%.1f%%), declared %d (%.1f%%), references %d of %d (%.1f%%)"
              % (label, nm, _pct(nm, n), dc, _pct(dc, n), rf, vals, _pct(rf, vals)))
    print("most common fronts: %s" % collections.Counter(r["values"] for r in rows).most_common(6))


def one(args):
    if "." in args.cls and not args.package:
        args.package, args.cls = args.cls.split(".", 1)
    if not args.package:
        args.package = "Engine"
    rows = [r for r in gap_rows() if r["package"] == args.package and r["class"] == args.cls]
    if not rows:
        print("no gap row for %s.%s" % (args.package, args.cls))
        return 2
    out = measure_package(args.package, rows, args.dump, [])
    for r in out:
        print("%s.%s gap %s front %s = %s (one byte each %d, names %d, declared %d, refs %d)"
              % (r["package"], r["class"], r["gap_len"], r["front_hex"] or "-", r["values"] or "-",
                 r["all_single_byte"], r["names_resolved"], r["declared_first"], r["refs_in_tables"]))
    return 0


def self_check():
    """A synthetic payload: a 42-byte prelude, a three-value front, sixteen zeros, then a block."""
    def compact(v):
        assert 0 <= v < 0x40
        return bytes([v])

    prelude = bytes(DOC_OFFSET)
    front = compact(2) + compact(0) + compact(0)
    body = prelude + front + bytes(TAIL_BYTES) + b"\x01\x02\x03"
    names = 8
    got = score(body[DOC_OFFSET:DOC_OFFSET + 3], names, 4, 2, {"Foo"}, lambda i: ["None", "Foo"][i] if i < 2 else "Name%d" % i)
    assert got["values"] == "2 0 0", got
    assert got["all_single_byte"] == 1 and got["names_resolved"] == 1, got
    assert got["declared_first"] == 0, got          # index 2 does not resolve
    assert got["refs_in_tables"] == 3, got          # 2 is an export, 0 is null, 0 is null
    # a window whose first value is the declared property, and one whose value needs two bytes
    got2 = score(b"\x01\x00\x00", names, 4, 2, {"Foo"}, lambda i: ["None", "Foo"][i] if i < 2 else "Name%d" % i)
    assert got2["declared_first"] == 1, got2
    assert score(b"\x40\x01", names, 4, 2, set(), lambda i: "")["all_single_byte"] == 0
    assert score(b"", names, 4, 2, set(), lambda i: "")["values"] == ""
    # a window that runs off the end is refused rather than guessed
    assert score(b"\x40", names, 4, 2, set(), lambda i: "")["values"] == ""
    print("self-check ok: front values, the declared-name reading, a two-byte value and two refusals")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", help="dump root (the directory holding System/)")
    ap.add_argument("--sweep", action="store_true", help="census every gap row")
    ap.add_argument("-o", "--out", help="artifact directory")
    ap.add_argument("--class", dest="cls", help="one class, `Package.Class` or with --package")
    ap.add_argument("--package", default="", help="package of --class")
    ap.add_argument("--report", action="store_true", help="print the committed artifact's tables")
    ap.add_argument("--check", action="store_true", help="self-test, no dump")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.report:
        with open(os.path.join(ROOT, "inventory", ARTIFACT), newline="", encoding="utf-8") as fh:
            report(list(csv.DictReader(fh)))
        return 0
    if not args.dump:
        ap.error("--dump is required (or --check)")
    if args.cls:
        return one(args)
    if args.sweep:
        return sweep(args)
    ap.error("pick --sweep, --class or --report")


if __name__ == "__main__":
    sys.exit(main())
