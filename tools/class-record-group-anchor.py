#!/usr/bin/env python3
"""Anchor the class record's two-record array on the closing group's start, and read the front as a negative.

`tools/class-record-gap.py` found a second array of `FUN_0003d900`'s grammar at the located first array's
*tail* on 361 of the 549 classes whose first array it could find, and named its residue ("what is in front of
it") as the next question. That population is small because it starts from an array located from the payload's
other side. The record's **closing group** is measured independently -- `tools/class-tail-run.py` parses
`count, names, reference, name, list` at `last_unit + 8` and it closes on the defaults start on 332 of 373 --
so the group's start is a far better anchor for the bytes in front of it.

This tool asks the one question that anchor answers, with a null beside it:

* **is the array's count right in front of the group?** The writer's element is one compact reference then two
  dwords, so a candidate head is a compact count followed by that many elements, each reference inside the
  package's own object tables, whose walk ends **exactly** on the group's start. The null aims the same walk two
  bytes past the group's start: a rule that fits a known boundary and a shifted one about equally has no
  discriminating power.
* **what is in front of the count?** The length of the zero run immediately before it, the head's offset from
  the prefix's end, and the count itself.
* **is the front a property block?** `tools/prop-stream.py`'s tag walk is run from the prefix's end over the
  bytes in front of the head; `tag_exact` says whether it terminates on `None` exactly at the head. This is the
  negative that refuses "the middle's front is the defaults stream written twice".
* **where does the stride-9 run sit?** `inventory/class_record_middle.csv` found a period-9 run on 536 classes;
  `run_inside` says whether it lies inside the anchored array and `run_units` against `head_count` says whether
  it can be part of it.

    python3 tools/class-record-group-anchor.py --dump $SG1_DUMP --class AmbientCreatures.HerdAnimal
    python3 tools/class-record-group-anchor.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-group-anchor.py --report
    python3 tools/class-record-group-anchor.py --check

Reads the packages and three committed artifacts (the prefix table, the tail group and the period search); no
boot, no value decoded, no name published.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402

ROOT = os.path.dirname(HERE)
MAX_COUNT = 8192          # a names array may be long; the array is bounded by the group anyway
MAX_FIELD = 5             # one compact field is 1 to 5 bytes
ELEMENT_BODY = 8          # the two dwords `FUN_0003d900` streams after each reference
CTL_SHIFT = 2             # the null's aim, in bytes past the group's start
ZERO_CAP = 24             # how far back the zero run in front of a head is counted
ARTIFACT = "class_record_group_anchor.csv"
COLUMNS = ["package", "class", "payload", "prefix_end", "block_start", "group_start", "heads", "head",
           "head_count", "ref_widths", "zeros_before", "front_len", "run_start", "run_units", "run_inside",
           "tag_props", "tag_end", "tag_left", "tag_reason", "tag_exact", "ctl_heads", "group_verdict"]

_PS = None


def prop_stream():
    """`tools/prop-stream.py` (hyphenated), loaded on first use: its tag walk is the front's reader."""
    global _PS
    if _PS is None:
        path = os.path.join(HERE, "prop-stream.py")
        spec = importlib.util.spec_from_file_location("prop_stream", path)
        _PS = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_PS)
    return _PS


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


def array_heads(data, start, stop, nexp, nimp, aim=0):
    """Every head in [start, stop) whose element walk ends exactly on `stop + aim`.

    Returns `[(head, count, [reference widths])]`. An element is taken only when its reference decodes to a
    width of 1-5 bytes and names an object the package holds (a positive index inside the export table, a
    negative one inside the import table), which is what keeps arbitrary bytes from walking.
    """
    out, end_target = [], stop + aim
    for p in range(start, max(start, end_target - 1)):
        try:
            count, o = read_index(data, p)
        except IndexError:
            continue
        if count <= 0 or count > MAX_COUNT:
            continue
        widths, ok = [], True
        for _ in range(count):
            if o >= end_target:
                ok = False
                break
            try:
                ref, e = read_index(data, o)
            except IndexError:
                ok = False
                break
            if e - o > MAX_FIELD or not (0 < ref <= nexp or -nimp <= ref < 0):
                ok = False
                break
            widths.append(e - o)
            o = e + ELEMENT_BODY
            if o > end_target:
                ok = False
                break
        if ok and o == end_target:
            out.append((p, count, widths))
    return out


def zeros_before(data, p, floor, cap=ZERO_CAP):
    """How many zero bytes run directly in front of `p`, bounded by `floor` and by `cap`."""
    z, q = 0, p - 1
    while q >= floor and data[q] == 0 and z < cap:
        z += 1
        q -= 1
    return z


class NameTable(object):
    """The one thing `prop-stream.read_block` asks of a package: `name(i)`."""

    def __init__(self, names):
        self.names = names

    def name(self, i):
        return self.names[i] if 0 <= i < len(self.names) else "?"


def front_tag(data, pe, head, pkg):
    """The tag walk over the front; returns `(props, end, reason)` in the front's own coordinates."""
    buf = data[pe:head]
    try:
        props, end, reason = prop_stream().read_block(buf, pkg, False)
    except Exception as exc:                                  # a walker refusal is an answer, not a crash
        return [], len(buf), "exception:%s" % type(exc).__name__
    return props, end, reason


def measure(data, row, nexp, nimp, pkg, mid_row=None):
    """One class: the anchored heads, the front and the run's place."""
    pe, bs, g = int(row["prefix_end"]), int(row["block_start"]), row["group_start"]
    out = {"heads": 0, "head": "", "head_count": "", "ref_widths": "", "zeros_before": "", "front_len": "",
           "run_start": "", "run_units": "", "run_inside": "", "tag_props": "", "tag_end": "",
           "tag_left": "", "tag_reason": "", "tag_exact": "", "ctl_heads": 0, "group_verdict": ""}
    if g == "":
        return out
    g = int(g)
    heads = array_heads(data, pe, g, nexp, nimp)
    out["heads"] = len(heads)
    out["ctl_heads"] = len(array_heads(data, pe, g, nexp, nimp, aim=CTL_SHIFT))
    if not heads:
        return out
    p, count, widths = heads[0]
    out["head"] = p - pe
    out["head_count"] = count
    out["ref_widths"] = "+".join(str(w) for w in widths)
    out["zeros_before"] = zeros_before(data, p, pe)
    out["front_len"] = p - pe
    props, end, reason = front_tag(data, pe, p, pkg)
    out["tag_props"] = len(props)
    out["tag_end"] = end
    out["tag_left"] = (p - pe) - end
    out["tag_reason"] = reason
    out["tag_exact"] = 1 if (reason == "None" and end == p - pe) else 0
    if mid_row and mid_row.get("run_start") not in (None, "") and mid_row.get("run_units") not in (None, ""):
        out["run_start"] = int(mid_row["run_start"]) - pe
        out["run_units"] = int(mid_row["run_units"])
        out["run_inside"] = 1 if p <= int(mid_row["run_start"]) < g else 0
    return out


def load_artifacts():
    def rows(name):
        with open(os.path.join(ROOT, "inventory", name), newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    pref = {(r["package"], r["class"]): r for r in rows("class_prefixes.csv")}
    tail = {(r["package"], r["class"]): r for r in rows("class_tail_run.csv")}
    mid = {(r["package"], r["class"]): r for r in rows("class_record_middle.csv")}
    return pref, tail, mid


def class_payloads(pkg):
    out = {}
    for exp in pkg.exports:
        if pkg.class_name_of(exp) == "Class":
            out[pkg.name(exp["name"])] = (exp["offset"], exp["size"])
    return out


def sweep(args):
    pref, tail, mid = load_artifacts()
    by_pkg = collections.defaultdict(list)
    for key, row in pref.items():
        if row["prefix_end"] and row["block_start"] and key in tail and tail[key]["last_unit"]:
            by_pkg[key[0]].append(key)
    rows = []
    for name in sorted(by_pkg):
        pkg = Package(os.path.join(args.dump, "System", name + ".u"))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        payloads = class_payloads(pkg)
        for key in sorted(by_pkg[name]):
            if key[1] not in payloads:
                continue
            off, size = payloads[key[1]]
            data = pkg.data[off:off + size]
            row = {"package": name, "class": key[1], "payload": size,
                   "prefix_end": pref[key]["prefix_end"], "block_start": pref[key]["block_start"],
                   "group_start": int(tail[key]["last_unit"]) + 8}
            if not (int(row["prefix_end"]) < int(row["group_start"]) <= int(row["block_start"])):
                row["group_start"] = ""
            row.update(measure(data, row, len(pkg.exports), len(pkg.imports), pkg, mid.get(key)))
            row["group_verdict"] = tail[key]["verdict"]
            rows.append(row)
    out_dir = args.out or os.path.join(ROOT, "inventory")
    path = os.path.join(out_dir, ARTIFACT)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in COLUMNS})
    print("wrote %s (%d rows)" % (path, len(rows)))
    report(rows)
    return 0


def one(args):
    pkg = Package(os.path.join(args.dump, "System", args.package + ".u"))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    payloads = class_payloads(pkg)
    if args.cls not in payloads:
        print("%s.%s has no class payload" % (args.package, args.cls))
        return 1
    pref, tail, mid = load_artifacts()
    key = (args.package, args.cls)
    off, size = payloads[args.cls]
    data = pkg.data[off:off + size]
    row = {"package": args.package, "class": args.cls, "payload": size,
           "prefix_end": pref[key]["prefix_end"], "block_start": pref[key]["block_start"],
           "group_start": int(tail[key]["last_unit"]) + 8 if key in tail and tail[key]["last_unit"] else ""}
    got = measure(data, row, len(pkg.exports), len(pkg.imports), pkg, mid.get(key))
    for k in COLUMNS:
        print("%-14s %s" % (k, got.get(k, row.get(k, ""))))
    print("head window    %s" % data[int(row["prefix_end"]) + int(got["head"]):int(row["group_start"])].hex(" "))
    return 0


def share(rows, key, pred=lambda v: True):
    c = collections.Counter(r[key] for r in rows if r[key] != "" and pred(r[key]))
    return c.most_common(10)


def front_census(args):
    """Per-offset zero share and distinct-value count over the front of the head-found classes."""
    pref, tail, mid = load_artifacts()
    by_pkg = collections.defaultdict(list)
    for key, row in pref.items():
        if row["prefix_end"] and row["block_start"] and key in tail and tail[key]["last_unit"]:
            by_pkg[key[0]].append(key)
    per = collections.defaultdict(lambda: [0, 0, collections.Counter()])
    n = 0
    for name in sorted(by_pkg):
        pkg = Package(os.path.join(args.dump, "System", name + ".u"))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        payloads = class_payloads(pkg)
        for key in sorted(by_pkg[name]):
            if key[1] not in payloads:
                continue
            off, size = payloads[key[1]]
            data = pkg.data[off:off + size]
            row = {"prefix_end": pref[key]["prefix_end"], "block_start": pref[key]["block_start"],
                   "group_start": int(tail[key]["last_unit"]) + 8}
            if not (int(row["prefix_end"]) < row["group_start"] <= int(row["block_start"])):
                continue
            heads = array_heads(data, int(row["prefix_end"]), row["group_start"],
                                len(pkg.exports), len(pkg.imports))
            if not heads:
                continue
            p = heads[0][0]
            front = data[int(row["prefix_end"]):p]
            if not 55 <= len(front) <= 66:
                continue
            n += 1
            for i, b in enumerate(front):
                slot = per[i]
                slot[0] += 1
                slot[1] += 1 if b == 0 else 0
                slot[2][b] += 1
    print("fronts of 55-66 bytes: %d classes" % n)
    print("%4s %8s %8s %6s  %s" % ("off", "n", "zeros", "values", "modal byte"))
    for i in range(66):
        c, z, vals = per[i]
        if c:
            top = vals.most_common(1)[0] if vals else (0, 0)
            print("%4d %8d %5.1f%% %6d  0x%02x on %d" % (i, c, 100.0 * z / c, len(vals), top[0], top[1]))
    return 0


def gap_join(rows):
    """The gap census's own populations, read against the anchored array (committed artifacts only)."""
    path = os.path.join(ROOT, "inventory", "class_record_gap.csv")
    if not os.path.exists(path):
        return
    with open(path, newline="", encoding="utf-8") as fh:
        gap = list(csv.DictReader(fh))
    mine = {(r["package"], r["class"]): r for r in rows}
    shapes = collections.Counter()
    with_head = collections.Counter()
    same_end = 0
    same_start = 0
    rest = collections.Counter()
    rest_head = collections.Counter()
    for r in gap:
        key = (r["package"], r["class"])
        m = mine.get(key)
        heads = int(m["heads"]) if m and m["heads"] != "" else 0
        shapes[r["shape"]] += 1
        if heads:
            with_head[r["shape"]] += 1
        if m and m["head"] != "" and r["second_len"] and r["array_end"]:
            start = int(m["prefix_end"]) + int(m["head"])
            end = start + int(r["second_len"]) + 1     # the count byte, then the elements
            if end == int(m["block_start"]):
                same_end += 1
            if start == int(r["array_end"]):
                same_start += 1
        if r["rest"] != "":
            rest[r["rest"]] += 1
            rest_head[r["rest"]] += 1 if heads else 0
    print("the gap census's %d rows, by shape: %s" % (len(gap), shapes.most_common()))
    print("  a head found on them: %s" % with_head.most_common())
    print("  the anchored array's span against the census's: same start %d, ends on the block %d"
          % (same_start, same_end))
    print("  the census's residue (`rest`, bytes after its second array): %s" % rest.most_common(6))
    print("    of which a head is found: %s" % rest_head.most_common(6))


def report(rows):
    total = len(rows)
    with_group = [r for r in rows if r["group_start"] != ""]
    found = [r for r in with_group if int(r["heads"]) > 0]
    per = collections.Counter(int(r["heads"]) for r in with_group)
    print("located classes: %d; with a closing group: %d" % (total, len(with_group)))
    print("heads per class: %s" % per.most_common(6))
    print("classes with a head: %d of %d (%.1f%%)"
          % (len(found), len(with_group), 100.0 * len(found) / max(1, len(with_group))))
    print("the null (aimed %d bytes past the group's start) finds a head on %d of %d"
          % (CTL_SHIFT, sum(1 for r in with_group if int(r["ctl_heads"]) > 0), len(with_group)))
    print("the group's verdict where a head was found: %s" % share(found, "group_verdict"))
    print("head offset from the prefix's end: %s" % share(found, "head"))
    print("element count: %s" % share(found, "head_count"))
    print("reference widths: %s" % share(found, "ref_widths"))
    print("zero bytes in front of the count: %s" % share(found, "zeros_before"))
    print("the front read as a property block: %s" % share(found, "tag_reason"))
    print("  closes exactly on the head: %d of %d" % (sum(1 for r in found if r["tag_exact"] == "1"), len(found)))
    print("  properties parsed: %s" % share(found, "tag_props"))
    print("  bytes left after the None: %s"
          % collections.Counter(int(r["tag_left"]) for r in found
                                if r["tag_reason"] == "None").most_common(8))
    with_run = [r for r in found if r["run_inside"] != ""]
    print("classes with both a head and a period-9 run: %d" % len(with_run))
    print("  the run inside the anchored array: %d; units <= the array's count: %d"
          % (sum(1 for r in with_run if r["run_inside"] == "1"),
             sum(1 for r in with_run if r["run_inside"] == "1" and int(r["run_units"]) <= int(r["head_count"]))))
    gap_join(rows)
    no = [r for r in with_group if int(r["heads"]) == 0]
    print("no head: %d classes" % len(no))
    if no:
        print("  their middles (block_start - prefix_end): %s"
              % collections.Counter(int(r["block_start"]) - int(r["prefix_end"]) for r in no).most_common(6))


def self_check():
    """Synthetic bodies: a head that must be found, the null that must not, and a front that must tag-parse."""
    names = ["None", "Alpha", "Beta", "Gamma"]
    pkg = NameTable(names)
    # body: [front of 4 bytes][count 2][2 x (ref, 8 bytes)][group]
    body = bytearray(b"\x00\x00\x00\x00") + bytearray(b"\x02")
    body += bytes([2]) + b"\x11\x11\x11\x11\x22\x22\x22\x22"
    body += bytes([3]) + b"\x33\x33\x33\x33\x44\x44\x44\x44"
    group_start = len(body)
    data = bytes(body) + b"\x02\x01\x02\x83\x03\x00"
    got = array_heads(data, 0, group_start, nexp=9, nimp=9)
    assert len(got) == 1 and got[0][0] == 4 and got[0][1] == 2 and got[0][2] == [1, 1], got
    assert array_heads(data, 0, group_start, nexp=9, nimp=9, aim=CTL_SHIFT) == [], "the null must not fit"
    # a reference outside the tables kills the walk
    bad = bytearray(data)
    bad[4] = 2
    bad[5] = 0x7F                                   # 127 > nexp: outside the export table
    assert array_heads(bytes(bad), 0, group_start, nexp=9, nimp=9) == [], "an out-of-table reference must refuse"
    # a negative reference inside the import table is a reference, not a bad index
    neg = bytearray(data)
    neg[5] = 0x83                                   # -3 -> import 2
    assert len(array_heads(bytes(neg), 0, group_start, nexp=9, nimp=9)) == 1, "a negative must walk"
    # the zero run in front of the count, and the front's own tag read
    assert zeros_before(data, 4, 0) == 4
    assert zeros_before(data, 4, 4) == 0, "the floor must bound the run"
    props, end, reason = front_tag(data, 0, 4, pkg)
    assert reason == "None" and end == 1 and props == [], (reason, end, len(props))
    assert (1 if (reason == "None" and end == 4) else 0) == 0, "the front is not a block here"
    # a front that *is* a block: an empty None at the last byte
    tag_front = b"\x00\x00\x01\x00"
    props, end, reason = front_tag(tag_front, 0, 4, pkg)
    assert reason == "None" and end == 1, (reason, end)
    # measure() drives the whole row from a synthetic prefix
    row = {"prefix_end": "0", "block_start": str(len(data)), "group_start": str(group_start)}
    out = measure(data, row, 9, 9, pkg, {"run_start": "4", "run_units": "2"})
    assert out["heads"] == 1 and out["head"] == 4 and int(out["head_count"]) == 2, out
    assert out["run_inside"] == 1 and out["ctl_heads"] == 0, out
    out2 = measure(data, dict(row, group_start=str(len(data))), 9, 9, pkg)
    assert out2["heads"] == 0, "a group start past the array must find nothing"
    print("ok class-record-group-anchor: 1 head found, 0 for the null, 0 past the array, an out-of-table "
          "reference refused, a negative walked, the zero run bounded and the front's tag read")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", help="dump root (the directory holding System/)")
    ap.add_argument("--class", dest="cls", help="one class, with --package")
    ap.add_argument("--package", default="", help="package of --class")
    ap.add_argument("--sweep", action="store_true", help="measure every located class with a closing group")
    ap.add_argument("-o", "--out", help="artifact directory")
    ap.add_argument("--report", action="store_true", help="print the committed artifact's tables")
    ap.add_argument("--front-census", action="store_true", help="per-offset zero share over the front")
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
    if args.front_census:
        return front_census(args)
    if args.cls:
        if "." in args.cls and not args.package:
            args.package, args.cls = args.cls.split(".", 1)
        return one(args)
    if args.sweep:
        return sweep(args)
    ap.error("pick --sweep, --class or --report")


if __name__ == "__main__":
    sys.exit(main())
