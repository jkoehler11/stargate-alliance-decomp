#!/usr/bin/env python3
"""The 63 open rows the gap shape does not answer at `prefix + 42`: is the shape somewhere else in the payload?

`tools/class-record-open-front.py` puts the shape -- three to five compact values, sixteen zero bytes and the array's
count -- at `prefix + 42` on 515 of the 578 located rows the walk does not close on, and leaves 63 with nothing there.
Two explanations survive that census: the offset is wrong on those rows (their record's prefix is a different width),
or the shape is genuinely absent (a record that is not a class, or one whose array is empty). The two are separable
without a boot: scan a window of offsets around `prefix + 42` with the same test, and ask whether the rows that answer
do so at a neighbouring offset, and whether the element walk from that offset then reaches the block start.

The control is the scan's own density on rows the shape *does* answer: the same window on the 515, with offset 0
removed, says how often the test fires at a position that is not the record's. A crowded window there would make a
neighbour hit on the 63 meaningless, so both numbers ship.

    python3 tools/class-record-no-shape.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-no-shape.py --report
    python3 tools/class-record-no-shape.py --dump $SG1_DUMP --class Engine.Camera
    python3 tools/class-record-no-shape.py --check        # synthetic bodies, no dump, writes nothing

Reads the packages and two committed tables; no boot, no Ghidra round, no value decoded.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402

HEAD_ARTIFACT = "class_record_array_head.csv"
ARTIFACT = "class_record_no_shape.csv"
LO = -16                  # the scan window, relative to prefix + 42
HI = 48
COLUMNS = ["package", "class", "payload", "prefix_end", "block_start", "gap_at_0", "offsets", "scan_hits",
           "head", "count", "elem_end", "residue", "landing"]


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ot = _load("class-record-open-tail")
crah = _load("class-record-array-head")

DOC = ot.DOC_OFFSET


def scan(data, prefix_end, lo=LO, hi=HI):
    """Every offset in the window whose shape holds, as `(k, gap, count position)`.

    Two offsets in the same window can put the count at the *same* byte: a front whose leading bytes are zero merges
    into the sixteen-zero run, so the same shape is reported two or three bytes earlier (`class-record-open-front.py`
    reads the widest pass for that reason). Rows are therefore compared by **count position**, not by offset.
    """
    out = []
    for k in range(lo, hi + 1):
        start = prefix_end + DOC + k
        if start + 18 > len(data):
            break
        gap = ot.crah_gap(data, start)
        if gap is not None:
            out.append((k, gap, start + gap))
    return out


def measure(data, prefix_end, block):
    row = {c: "" for c in COLUMNS}
    row.update({"payload": len(data), "prefix_end": prefix_end, "block_start": block})
    hits = scan(data, prefix_end)
    row["gap_at_0"] = 1 if any(k == 0 for k, _g, _c in hits) else 0
    others = [(k, g, c) for k, g, c in hits if k != 0]
    row["offsets"] = " ".join(str(k) for k, _g, _c in others[:10])
    row["scan_hits"] = len({c for _k, _g, c in others})
    if not others:
        return row
    best = None
    for k, _gap, count_pos in others:
        w = crah.walk_elements(data, count_pos)
        if not w:
            continue
        kind, _field, off = crah.landing(w, block)
        score = {"boundary": 0, "past": 0}.get(kind, 1)
        key = (score, off if isinstance(off, int) else 0)
        if best is None or key < best[0]:
            best = (key, k, w, kind, off)
    if best is None:
        return row
    _key, k, w, kind, off = best
    row.update({"head": k, "count": w["count"], "elem_end": w["end"], "landing": kind,
                "residue": "" if not isinstance(off, int) else off})
    return row


def head_rows():
    with open(os.path.join(ROOT, "inventory", HEAD_ARTIFACT), newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def sweep(args):
    rows = [r for r in head_rows() if r["arr_count"] == ""]
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
        for e in pkg.exports:
            index.setdefault(pkg.name(e["name"]), e)
        for r in by_pkg[pkgname]:
            rec = {"package": pkgname, "class": r["class"]}
            out.append(rec)
            if not r.get("prefix_end") or not r.get("block_start"):
                continue
            exp = index.get(r["class"])
            if exp is None:
                continue
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            rec.update(measure(data, int(r["prefix_end"]), int(r["block_start"])))
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(out)
        print("wrote %s: %d rows" % (path, len(out)))
    report(out)
    return 0


def one(args):
    if not args.dump or not args.cls:
        print("--class needs --dump", file=sys.stderr)
        return 2
    pkgname, cls = args.cls.split(".", 1)
    row = next((r for r in head_rows() if r["package"] == pkgname and r["class"] == cls), None)
    if row is None:
        print("not an open row: %s" % args.cls, file=sys.stderr)
        return 2
    pkg = Package(os.path.join(args.dump, "System", pkgname + ".u"))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    exp = next((e for e in pkg.exports if pkg.name(e["name"]) == cls), None)
    data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    m = measure(data, int(row["prefix_end"]), int(row["block_start"]))
    print("%s: payload %d, prefix end %s, block %s" % (args.cls, len(data), row["prefix_end"], row["block_start"]))
    print("  shape at prefix+42: %s; offsets holding it: %s"
          % ("yes" if m["gap_at_0"] else "no", m["offsets"] or "-"))
    print("  nearest head k=%s, count %s, element end %s, residue %s, landing %s"
          % (m["head"], m["count"], m["elem_end"], m["residue"], m["landing"]))
    return 0


def _share(n, d):
    return "%.1f%%" % (100.0 * n / d) if d else "-"


def report(rows=None):
    if rows is None:
        with open(os.path.join(ROOT, "inventory", ARTIFACT), newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    read = [r for r in rows if str(r.get("scan_hits") or "") != ""]
    no0 = [r for r in read if int(r["gap_at_0"] or 0) == 0]
    yes0 = [r for r in read if int(r["gap_at_0"] or 0) == 1]
    print("open rows read: %d; the shape is at prefix+42 on %d (%s), absent there on %d"
          % (len(read), len(yes0), _share(len(yes0), len(read)), len(no0)))
    for label, sub in (("no shape at 0", no0), ("control (shape at 0)", yes0)):
        if not sub:
            continue
        hits = [int(r["scan_hits"]) for r in sub]
        print("\n%s: %d rows" % (label, len(sub)))
        print("  offsets in the window holding the shape (excluding 0): mean %.2f, zero on %d (%s), max %d"
              % (sum(hits) / len(hits), sum(1 for h in hits if h == 0), _share(sum(1 for h in hits if h == 0),
                                                                              len(hits)), max(hits)))
        got = [r for r in sub if r["head"] != ""]
        print("  a head whose element walk reaches the block: %d of %d (%s)"
              % (sum(1 for r in got if r["landing"] in ("past", "boundary")), len(sub),
                 _share(sum(1 for r in got if r["landing"] in ("past", "boundary")), len(sub))))
        print("  that head offset (k): %s" % collections.Counter(int(r["head"]) for r in got).most_common(10))
        print("  landing: %s" % collections.Counter(r["landing"] for r in got).most_common())
        res = [int(r["residue"]) for r in got if r["landing"] == "past" and r["residue"] != ""]
        if res:
            print("  residue where it lands past: median %d, within 9 on %d of %d, within 3 on %d"
                  % (sorted(res)[len(res) // 2], sum(1 for v in res if v <= 9), len(got),
                     sum(1 for v in res if v <= 3)))
    return 0


def self_check():
    """Synthetic bodies: a shape one offset late is found, an absent one is not, and the window is bounded."""
    body = bytearray(DOC + 8)
    body += b"\x02\x00\x00" + b"\x00" * 16 + b"\x01" + b"\x01" + b"\x00" * 8
    body += b"\x00" * 24
    data = bytes(body)
    assert ot.crah_gap(data, DOC + 8) is not None        # the shape sits eight bytes late here
    hits = scan(data, 0, lo=-8, hi=8)
    # a front of leading zeros merges backwards into the run: same count position, several offsets
    assert max(k for k, _g, _c in hits) == 8, hits
    assert all(k >= 3 for k, _g, _c in hits), hits
    # the count positions those offsets imply are the same one when the front is zero-padded
    assert min(c for _k, _g, c in hits) >= DOC + 8
    # no zero run at all: nothing in the window
    flat = bytes(DOC + 8) + b"\x02" * 60
    assert scan(flat, 0, lo=-8, hi=8) == []
    # a payload shorter than the window stops rather than reading past its end
    assert scan(data[:DOC + 20], 0, lo=0, hi=48) == []
    print("self-check ok: a shape one offset late is found, a body with no zero run answers nothing, "
          "and the scan stops at the payload end")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", help="dump root (the directory holding System/)")
    ap.add_argument("--class", dest="cls", help="one class, package.Class")
    ap.add_argument("--sweep", action="store_true", help="read every open row")
    ap.add_argument("-o", "--out", help="artifact directory")
    ap.add_argument("--report", action="store_true", help="summarise the committed artifact")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.report:
        return report()
    if args.cls:
        return one(args)
    if not args.sweep or not args.dump:
        ap.error("--sweep needs --dump")
    return sweep(args)


if __name__ == "__main__":
    sys.exit(main())
