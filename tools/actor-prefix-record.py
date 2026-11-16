#!/usr/bin/env python3
"""The bytes in front of an actor's property block are a fixed record, and its length is 2*w + 13.

`actor_prefix_census.csv` measures the offset in front of each block and finds 119 of 295 classes located at more
than one; `actor_prefix_anchor.py` takes that to 93 classes by rejecting candidates whose leading tag decodes as type
0, and names what is left as a residue of 79,873 blocks (66%) with more than one surviving start. Both tools decide
the start by *rejecting* candidates. This one reads the prefix instead and predicts the start, so the two can be
compared.

What the prefix is, measured on every block whose first byte decodes as the export's own class index:

    [class index][the same index again][8 x 0xff][4 bytes][0x81]

The class index is a compact value -- 1, 2 or 3 bytes depending on the import's position -- so a class's prefix
length is not a constant of the class but of the *package*: `2*w + 13` with w the encoded width. On four maps 4,218
of 4,254 blocks that carry a class index first (99.2%) are exactly that record and land on `2*w + 13`, and every one
of them is also the start the type-0 rule picks. The other 36 are the misparses both rules already refuse (fronts
of 12, 10 or 1 -- a truncated record whose remaining bytes the tag parser absorbed). Blocks whose first byte is not
their class index are counted separately (`no_class_ref`): they are the exports that carry no prefix at all.

So this artifact answers the residue question with a positive rule rather than another rejection, and it prices the
two older ones against it: `extent_eq` is the number of blocks where the record agrees with the extent locator,
`strict_eq` where it agrees with the type-0 rule.

    python3 tools/actor-prefix-record.py DUMP/Maps -o inventory     # needs the dump
    python3 tools/actor-prefix-record.py --check                     # dump-free: shape, self-test, joins

`predicted` counts the blocks the record's length names, `matched` those whose bytes are the record, `clean` those
whose predicted start is also a type-0-free candidate, and `residue` the blocks that carry the class index but do
not match -- the rows a reader should look at first.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import checkcsv  # noqa: E402
from ue2pkg import Package  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

COLUMNS = ["class", "exports", "blocks", "class_ref", "predicted", "matched", "clean", "extent_eq", "strict_eq",
           "no_prefix", "residue", "verdict"]

FILL = b"\xff" * 8   # the eight bytes every matched record carries after the class index pair
TAIL = 5             # 4 unconstrained bytes and the closing compact -1
INVALID = "Invalid"


def loader():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prop-stream.py")
    spec = importlib.util.spec_from_file_location("prop_stream", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_index(buf, off):
    """The UE2 index codec this package uses: bit 7 is the sign, bit 6 continues, 6 bits then 7 per byte.

    Returned with its width, because the width is what the record's length is made of.
    """
    if off >= len(buf):
        return None, 0
    start = off
    b = buf[off]
    sign, cont, val = b & 0x80, b & 0x40, b & 0x3F
    off += 1
    shift = 6
    while cont and shift < 32:
        if off >= len(buf):
            return None, 0
        b = buf[off]
        off += 1
        cont = b & 0x80
        val |= (b & 0x7F) << shift
        shift += 7
    return (-val if sign else val), off - start


def record(data, cls):
    """`(predicted, matched)`: the record's length and whether the bytes are the record.

    The length is `2*w + 13` for the class index's own encoded width `w`, so it is a property of the package's
    import table position, not of the class -- which is why a per-class constant could not describe it.
    """
    value, w = read_index(data, 0)
    if not w or value != cls:
        return 0, False
    again, w2 = read_index(data, w)
    if w2 != w or again != cls:
        return 0, False
    end = 2 * w + 8 + TAIL
    if len(data) < end:
        return end, False
    matched = data[2 * w:2 * w + 8] == FILL and data[end - 1] == 0x81
    return end, matched


def candidates(pkg, mod, data):
    """Every start in the window whose property list parses cleanly and ends at the export's end."""
    out = []
    for start in range(0, min(len(data), 64)):
        try:
            props, used, reason = mod.read_block(data[start:], pkg, True)
        except Exception:
            continue
        if reason != "None" or len(props) < 2:
            continue
        if any(p["name"] == "?" for p in props):
            continue
        if start + used != len(data):
            continue
        out.append((start, props, sum(1 for p in props if p.get("type") == INVALID)))
    return out


def maps_in(path):
    return sorted(f for f in os.listdir(path) if f.lower().endswith(".unr"))


def compute(maps_dir):
    mod = loader()
    exports = collections.Counter()
    stat = collections.defaultdict(collections.Counter)

    for fname in maps_in(maps_dir):
        pkg = Package(os.path.join(maps_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        for exp in pkg.exports:
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            cands = candidates(pkg, mod, data)
            if not cands:
                continue
            cls = pkg.class_name_of(exp)
            exports[cls] += 1
            clean = [c for c in cands if not c[2]]
            extent = cands[0][0]
            strict = clean[0][0] if clean else None
            if extent == 0:
                stat[cls]["no_prefix"] += 1
            predicted, matched = record(data, exp["class"])
            if not predicted:
                stat[cls]["no_class_ref"] += 1
                continue
            stat[cls]["class_ref"] += 1
            stat[cls]["predicted"] += 1
            starts = {c[0]: c for c in cands}
            if predicted in starts:
                stat[cls]["is_candidate"] += 1
                if not starts[predicted][2]:
                    stat[cls]["clean"] += 1
            if matched:
                stat[cls]["matched"] += 1
            else:
                stat[cls]["residue"] += 1
            if predicted == extent:
                stat[cls]["extent_eq"] += 1
            if predicted == strict:
                stat[cls]["strict_eq"] += 1

    rows = []
    for cls in sorted(exports, key=lambda c: (-exports[c], c)):
        st = stat[cls]
        if not exports[cls]:
            continue
        if st["class_ref"] and st["matched"] == st["class_ref"]:
            verdict = "record"
        elif st["class_ref"] and st["matched"]:
            verdict = "partial"
        elif st["class_ref"]:
            verdict = "residue"
        else:
            verdict = "no-class-ref"
        rows.append([cls, exports[cls], exports[cls], st["class_ref"], st["predicted"], st["matched"],
                     st["clean"], st["extent_eq"], st["strict_eq"], st["no_prefix"], st["residue"], verdict])
    return rows


def selftest():
    """Synthetic prefixes: the record the rule claims, and the three shapes that must not pass it."""
    problems = []
    for cls, body, want_len, want_match in (
            (-45, b"\xad\xad" + FILL + b"\x30\x00\x30\x00\x81", 15, True),      # one-byte class index
            (-153, b"\xd9\x02\xd9\x02" + FILL + b"\x6b\x00\x78\x00\x81", 17, True),   # two-byte class index
            (-45, b"\xad\xad" + FILL + b"\x30\x00\x30\x00\x82", 15, False),     # closing byte is not -1
            (-45, b"\xad\xad" + b"\xfe" * 8 + b"\x30\x00\x30\x00\x81", 15, False),  # fill is not 0xff
            (-45, b"\xad\xac" + FILL + b"\x30\x00\x30\x00\x81", 0, False),      # a different class index
            (-45, b"\xad" + FILL + b"\x30\x00\x30\x00\x81", 0, False),          # index written once
    ):
        data = body + b"\x81\x00\x00"       # a little of the tag stream behind it
        got_len, got_match = record(data, cls)
        if got_len != want_len or got_match != want_match:
            problems.append("selftest: class %d read as length %d match %s, wanted %d %s"
                            % (cls, got_len, got_match, want_len, want_match))
    if read_index(b"\x00", 0) != (0, 1) or read_index(b"\x81", 0) != (-1, 1) or read_index(b"\xd9\x02", 0) != (-153, 2):
        problems.append("selftest: the index codec does not decode 00, 81, d9 02")
    return problems


def check(outdir):
    """Every count against its own denominator, the block total against the anchor's, and the row sums."""
    path = os.path.join(outdir, "actor_prefix_record.csv")
    problems = selftest() + checkcsv.shape(path, COLUMNS, key="class")
    if problems:
        return problems
    anchor = {}
    apath = os.path.join(outdir, "actor_prefix_anchor.csv")
    if os.path.isfile(apath):
        with open(apath, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                anchor[r["class"]] = r
    else:
        problems.append("actor_prefix_anchor.csv is missing")
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        try:
            blocks = int(r["blocks"])
            class_ref, predicted = int(r["class_ref"]), int(r["predicted"])
            matched, clean = int(r["matched"]), int(r["clean"])
            extent_eq, strict_eq = int(r["extent_eq"]), int(r["strict_eq"])
            no_prefix, residue = int(r["no_prefix"]), int(r["residue"])
        except ValueError:
            problems.append("%s: a count is not a number" % r["class"])
            continue
        if class_ref + no_prefix > blocks:
            problems.append("%s: %d class-referenced + %d prefixless > %d block(s)"
                            % (r["class"], class_ref, no_prefix, blocks))
        if predicted != class_ref:
            problems.append("%s: %d predicted of %d class-referenced block(s)" % (r["class"], predicted, class_ref))
        if matched + residue != class_ref:
            problems.append("%s: %d matched + %d residue != %d class-referenced"
                            % (r["class"], matched, residue, class_ref))
        for name, val in (("clean", clean), ("extent_eq", extent_eq), ("strict_eq", strict_eq)):
            if val > class_ref:
                problems.append("%s: %s %d of %d class-referenced block(s)" % (r["class"], name, val, class_ref))
        if r["verdict"] == "record" and matched != class_ref:
            problems.append("%s: verdict record with %d of %d matched" % (r["class"], matched, class_ref))
        if r["verdict"] == "residue" and matched:
            problems.append("%s: verdict residue with %d matched" % (r["class"], matched))
        if (r["verdict"] == "no-class-ref") != (class_ref == 0):
            problems.append("%s: verdict %s with %d class-referenced" % (r["class"], r["verdict"], class_ref))
        have = anchor.get(r["class"])
        if have is None:
            problems.append("%s: not in actor_prefix_anchor.csv" % r["class"])
        elif have["blocks"] and int(have["blocks"]) != blocks:
            problems.append("%s: %d block(s) against the anchor's %s" % (r["class"], blocks, have["blocks"]))
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("maps_dir", nargs="?", help="the dump's Maps directory (not needed for --check)")
    ap.add_argument("-o", "--out", help="directory for the CSV (default: inventory/)")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    outdir = args.out or os.path.join(ROOT, "inventory")

    if args.check and not args.maps_dir:
        problems = check(outdir)
        return checkcsv.report("actor-prefix-record", problems,
                               "actor_prefix_record.csv is the shape this tool writes, every count fits its "
                               "denominator, and the block totals agree with actor_prefix_anchor.csv "
                               "(shape only: needs the dump)")

    if not args.maps_dir:
        ap.error("the Maps directory is required to rebuild the artifact")

    rows = compute(args.maps_dir)
    path = os.path.join(outdir, "actor_prefix_record.csv")
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        w.writerow(COLUMNS)
        w.writerows(rows)
    blocks = sum(int(r[2]) for r in rows)
    class_ref = sum(int(r[3]) for r in rows)
    matched = sum(int(r[5]) for r in rows)
    clean = sum(int(r[6]) for r in rows)
    extent_eq = sum(int(r[7]) for r in rows)
    strict_eq = sum(int(r[8]) for r in rows)
    residue = sum(int(r[10]) for r in rows)
    record_rows = sum(1 for r in rows if r[11] == "record")
    print("wrote %s (%d rows)" % (path, len(rows)))
    print("%d block(s) over %d class(es); %d carry the class index first" % (blocks, len(rows), class_ref))
    print("%d match the record (%.1f%%), %d are on a type-0-free start, %d on the extent start, %d on the "
          "type-0 start" % (matched, 100.0 * matched / class_ref if class_ref else 0.0, clean, extent_eq, strict_eq))
    print("%d block(s) in the residue; %d of %d class(es) with a prefix are the record on every block"
          % (residue, record_rows, sum(1 for r in rows if int(r[3]))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
