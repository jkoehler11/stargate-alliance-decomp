#!/usr/bin/env python3
"""The gap shape at `prefix + 42` on the rows whose walk does not close.

`tools/class-record-front.py` identified the field by *subtraction*: it read the 3-5 bytes in front of the sixteen
zero dwords only on the 1,490 rows the gap census reached, and every reading it tested there was a small integer's
base rate. The question it left is whether those bytes are a field of the record at all, or an artefact of the offset
the walk chose -- and the population that answers it is the **578 located rows the walk does not close on**
(`inventory/class_record_array_head.csv` `arr_count` empty), which have no gap-census row by construction.

So this tool reads the *shape* at `prefix + 42` on every located row, without the walk:

    [3-5 compact values] [sixteen zero bytes] [the array's count]

The sixteen-zero run is anchored by its **end** (the count's position), not its start: a front whose last byte is
`00` merges into the run, so a start-anchored search finds the run one to three bytes early and calls a four-byte
front one byte long. Candidates are the modal lengths 19, 20 and 21 only, and when more than one passes the widest
is taken -- the shorter pass is the front's own trailing zero.

Three controls travel with the measurement: the closing population, where the shape must reproduce the gap census's
own length; the same test at 24 offsets just past the head in the *same* payload, which is the local base rate for
"sixteen zero bytes end here"; and the element walk from the head the shape implies, whose end says whether the
count really is where the shape puts it.

    python3 tools/class-record-open-front.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-open-front.py --report
    python3 tools/class-record-open-front.py --dump $SG1_DUMP --class AmbientCreatures.HerdAnimal
    python3 tools/class-record-open-front.py --check

Reads the packages and the committed array-head table; no boot, no Ghidra round, no property value decoded.
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

DOC_OFFSET = 42          # UState's 22 bytes and the five dwords: where the shape sits
HEAD_ARTIFACT = "class_record_array_head.csv"
ARTIFACT = "class_record_open_front.csv"
MODAL = (19, 20, 21)     # a 3-5 byte front carries a 19-21 byte gap
OFF_MODAL = (18, 22)     # reported separately: a two- or six-byte front would be a different width
CONTROL_OFFSETS = 24     # how many offsets just past the head are sampled for the local base rate
ZERO_RUN = b"\x00" * 16
COLUMNS = ["package", "class", "closed", "payload", "prefix_end", "block_start", "census_gap", "gap",
           "front_len", "front_hex", "values", "value_count", "count", "elem_count", "elem_end", "residue",
           "landing", "base_rate", "base_hits"]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


cfront = load("cfront", "class-record-front")
crah = load("crah", "class-record-array-head")


def zero_run_end(data, start, gap):
    """Do the sixteen bytes ending at `start + gap` all read zero? (the gap's own defining property)"""
    s, e = start + gap - 16, start + gap
    if s < 0 or e > len(data):
        return False
    return data[s:e] == ZERO_RUN


def front(data, start, gap):
    """The bytes in front of the zero run as compacts: `(ok, hex, values)`, or `(False, "", [])` on an overrun."""
    o, end, vals = start, start + gap - 16, []
    while o < end:
        try:
            v, e = cfront.read_index(data, o)
        except IndexError:
            return False, "", []
        if e <= o or e > end:
            return False, "", []
        vals.append(v)
        o = e
    return True, data[start:end].hex(" "), vals


def shape(data, start, widths=MODAL):
    """Every gap length whose sixteen-zero run and front both hold at `start`."""
    return [g for g in widths if zero_run_end(data, start, g) and front(data, start, g)[0]]


def pick(data, start):
    """The gap the shape implies: the widest modal pass, else the widest off-modal one."""
    for widths in (MODAL, OFF_MODAL):
        good = shape(data, start, widths)
        if good:
            return max(good), widths
    return None, None


def base_rate(data, start, gap):
    """The same test at 24 offsets just past the head, in the same payload."""
    hits = total = 0
    for q in range(start + gap + 8, start + gap + 8 + CONTROL_OFFSETS):
        if q + 16 > len(data):
            break
        total += 1
        if any(zero_run_end(data, q - g, g) and front(data, q - g, g)[0] for g in MODAL):
            hits += 1
    return hits, total


def measure(data, prefix_end, block):
    """One class: the shape, the count it puts the array at, and where the element walk from there lands."""
    row = {"prefix_end": prefix_end, "block_start": block, "payload": len(data), "gap": "", "front_len": "",
           "front_hex": "", "values": "", "value_count": "", "count": "", "elem_count": "", "elem_end": "",
           "residue": "", "landing": "", "base_rate": "", "base_hits": ""}
    start = prefix_end + DOC_OFFSET
    if start + 18 > len(data):
        return row
    gap, _ = pick(data, start)
    if gap is None:
        return row
    _ok, hexs, vals = front(data, start, gap)
    row.update({"gap": gap, "front_len": gap - 16, "front_hex": hexs,
                "values": " ".join(str(v) for v in vals), "value_count": len(vals)})
    hits, total = base_rate(data, start, gap)
    row.update({"base_hits": "%d/%d" % (hits, total), "base_rate": "%.3f" % (hits / float(total)) if total else ""})
    try:
        count, _ = cfront.read_index(data, start + gap)
    except IndexError:
        return row
    row["count"] = count
    if block <= start + gap:
        row["landing"] = "no-block"     # the locator's own false positive: a block start at or inside the head
        return row
    w = crah.walk_elements(data, start + gap)
    if not w:
        return row
    kind, _field, off = crah.landing(w, block)
    row.update({"elem_count": w["count"], "elem_end": w["end"], "landing": kind,
                "residue": "" if not isinstance(off, int) else off})
    return row


def head_rows():
    path = os.path.join(ROOT, "inventory", HEAD_ARTIFACT)
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def sweep(args):
    rows = head_rows()
    by_pkg = collections.defaultdict(list)
    for r in rows:
        by_pkg[r["package"]].append(r)
    out = []
    for pkgname in sorted(by_pkg):
        pkg = Package(os.path.join(args.dump, "System", pkgname + ".u"))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        index = {}
        for k, exp in enumerate(pkg.exports, start=1):
            index.setdefault(pkg.name(exp["name"]), exp)
        for r in by_pkg[pkgname]:
            rec = {"package": pkgname, "class": r["class"], "closed": int(r["arr_count"] != ""),
                   "census_gap": r["gap_len"]}
            out.append(rec)
            exp = index.get(r["class"])
            if exp is None or not r["prefix_end"] or not r["block_start"]:
                continue
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            rec.update(measure(data, int(r["prefix_end"]), int(r["block_start"])))
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


def one(args):
    if not args.dump or not args.cls:
        print("--class needs --dump", file=sys.stderr)
        return 2
    pkgname, cls = args.cls.split(".", 1) if args.package == "" else (args.package, args.cls)
    rows = [r for r in head_rows() if r["package"] == pkgname and r["class"] == cls]
    if not rows:
        print("not a located class: %s" % args.cls, file=sys.stderr)
        return 2
    r = rows[0]
    pkg = Package(os.path.join(args.dump, "System", pkgname + ".u"))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    exp = None
    for k, e in enumerate(pkg.exports, start=1):
        if pkg.name(e["name"]) == cls:
            exp = e
            break
    data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    start = int(r["prefix_end"]) + DOC_OFFSET
    print("%s.%s: payload %d, prefix end %s, block %s, census gap %s"
          % (pkgname, cls, len(data), r["prefix_end"], r["block_start"], r["gap_len"] or "-"))
    print("  bytes at prefix+42 .. +42+24: %s" % data[start:start + 24].hex(" "))
    print("  candidates: %s" % (shape(data, start) or "-"))
    m = measure(data, int(r["prefix_end"]), int(r["block_start"]))
    for k in ("gap", "front_hex", "values", "count", "elem_count", "elem_end", "residue", "landing", "base_hits"):
        print("  %-10s %s" % (k, m[k]))
    return 0


def _share(n, d):
    return "%.1f%%" % (100.0 * n / d) if d else "-"


def report(rows):
    rows = [r for r in rows if r.get("payload") not in ("", None)]
    shapeful = [r for r in rows if str(r.get("gap") or "") != ""]
    print("located rows: %d; the gap shape is at prefix+42 on %d (%s)"
          % (len(rows), len(shapeful), _share(len(shapeful), len(rows))))
    for closed, label in ((1, "closing (the walk does not need it)"), (0, "OPEN (the walk does not close)")):
        sub = [r for r in rows if int(r["closed"]) == closed]
        got = [r for r in sub if str(r.get("gap") or "") != ""]
        print("\n%s: %d rows, shape on %d (%s)" % (label, len(sub), len(got), _share(len(got), len(sub))))
        if not got:
            continue
        print("  gap lengths: %s" % collections.Counter(int(r["gap"]) for r in got).most_common(6))
        if closed:
            both = [r for r in got if str(r.get("census_gap") or "") != ""]
            print("  gap equals the census gap on %d of %d (%s)"
                  % (sum(1 for r in both if int(r["gap"]) == int(r["census_gap"])), len(both),
                     _share(sum(1 for r in both if int(r["gap"]) == int(r["census_gap"])), len(both))))
        rates = [float(r["base_rate"]) for r in got if str(r.get("base_rate") or "") != ""]
        if rates:
            print("  local base rate of the same test (24 offsets past the head): mean %.3f, max %.3f"
                  % (sum(rates) / len(rates), max(rates)))
        land = collections.Counter(r["landing"] for r in got)
        print("  element walk from that head: %s" % land.most_common(6))
        res = [int(r["residue"]) for r in got if r["landing"] == "past" and str(r.get("residue") or "") != ""]
        if res:
            print("  residue (block - array end) where it lands past: median %s, most common %s"
                  % (statistics.median(res), collections.Counter(res).most_common(8)))
            if not closed:
                print("  the elements end within 3 bytes of the block start on %d of %d (%s)"
                      % (sum(1 for v in res if v <= 3), len(got), _share(sum(1 for v in res if v <= 3), len(got))))
                print("  within 9 bytes: %d (%s)"
                      % (sum(1 for v in res if v <= 9), _share(sum(1 for v in res if v <= 9), len(got))))
        print("  counts: %s"
              % collections.Counter(int(r["count"]) for r in got if str(r.get("count") or "") != "").most_common(5))
    close = {r["front_hex"] for r in rows if int(r["closed"]) == 1 and r.get("front_hex")}
    opn = {r["front_hex"] for r in rows if int(r["closed"]) == 0 and r.get("front_hex")}
    print("\nfront vocabulary: closing %d distinct, open %d distinct, shared %d"
          % (len(close), len(opn), len(close & opn)))
    print("commonest open fronts: %s"
          % collections.Counter(r["front_hex"] for r in rows if int(r["closed"]) == 0 and r.get("front_hex")).most_common(6))


def self_check():
    """Synthetic bodies only: the shape, its trailing-zero rule, and the walk it puts the count at."""
    def body(front_bytes, zeros=16, count=1, elems=1):
        start = DOC_OFFSET
        out = bytearray(start)
        out += front_bytes + b"\x00" * zeros
        end = len(out)
        out += bytes([count])
        out += b"\x01" + b"\x00" * 8 * elems
        return bytes(out), start, len(out)

    data, start, end = body(b"\x02\x00\x00")
    assert pick(data, start)[0] == 19, pick(data, start)
    m = measure(data, 0, end)
    assert (m["gap"], m["front_hex"], m["values"], m["count"]) == (19, "02 00 00", "2 0 0", 1), m
    assert m["landing"] == "past" and m["residue"] == 0, m

    # a front whose last byte is zero merges into the run: both passes hold and the widest one is the true width
    data, start, _ = body(b"\x00\x12\x21\x00")
    assert shape(data, start) == [19, 20], shape(data, start)
    assert pick(data, start)[0] == 20

    # no sixteen-zero run: no shape, and no count is invented
    data, start, _ = body(b"\x02", zeros=0)
    assert shape(data, start) == [], shape(data, start)
    assert measure(data, 0, len(data))["gap"] == ""

    # an off-modal (two-byte) front is not quiet: it is reported as off-modal, not as a modal shape
    data, start, _ = body(b"\x02\x03")
    assert pick(data, start) == (18, OFF_MODAL), pick(data, start)

    # a zero count merges too: the widest pass then over-reads by one byte, which is the rule's one failure mode
    data, start, _ = body(b"\x02\x00\x00", count=0)
    assert pick(data, start)[0] == 20
    print("self-check ok: the shape is the run's end plus the front, a trailing zero merges with it, the widest "
          "pass wins, and the walk from the head it implies lands on the block start")
    return 0


def main():
    ap = argparse.ArgumentParser()
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
    if args.cls:
        return one(args)
    if not args.sweep or not args.dump:
        ap.error("--sweep needs --dump")
    return sweep(args)


if __name__ == "__main__":
    sys.exit(main())
