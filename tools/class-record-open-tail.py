#!/usr/bin/env python3
"""What the 1-9 bytes between the element grid and the located block start are: the closing group's front.

`tools/class-record-open-front.py` left one population named: on the rows the walk does not close, the element walk
stops **1 to 9 bytes short** of the located defaults-block start on 245 of the 515 rows that carry the gap shape. On
the closing rows those bytes are the record's closing group -- `UClass::Serialize`'s names array, one reference, the
config name and a second names array (`tools/class-tail-run.py`) -- so the open rows should carry the same group.
Anchored to the block start the group closes on **0** of them, which is exactly what a start placed *inside* the
group would produce.

So this tool stops anchoring the group to the block. It parses the writer's four fields from the element end with no
required end (bounded, every index inside the name table) and reports two things per row:

* **where the group ends** -- `overlap` is that end minus the located block start. `overlap > 0` means the located
  start falls inside the group; the group cannot have been truncated by it.
* **which candidate start opens the defaults block** -- the property tag stream is the oracle the block start was
  chosen by (`tools/class-defaults.py` picks a start whose tags run to the export end and name declared
  properties). Both the located start and the group's end are read as a tag stream, and each is scored by how many
  of the properties it names the class declares. A start inside the group names array reads bytes as tags that are
  not declared properties; the block start does not.

Controls travel with it: on the closing rows the same free parse must end *exactly* on the located block start (it
reproduces the anchored parse there), and the local base rate is the same parse at +-1..6 bytes around the element
end -- a permissive grammar that fires everywhere cannot be evidence at one position.

    python3 tools/class-record-open-tail.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-open-tail.py --report
    python3 tools/class-record-open-tail.py --dump $SG1_DUMP --class Engine.Interaction
    python3 tools/class-record-open-tail.py --check         # synthetic bodies, no dump, writes nothing

Reads the packages, the committed array-head table and the class property/declaration artifacts; no boot, no Ghidra
round, no property value decoded.
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

DOC_OFFSET = 42          # the field table's position for the array head: UState's 22 bytes and five dwords
HEAD_ARTIFACT = "class_record_array_head.csv"
ARTIFACT = "class_record_open_tail.csv"
MAX_GROUP = 48           # the closing group is a handful of compacts; nothing near a payload's length
MAX_NAMES = 256
WIDTH_MAX = 5            # one compact field is 1 to 5 bytes (inventory/archive_slots.csv)
BASE_OFFSETS = (-6, -5, -4, -3, -2, -1, 1, 2, 3, 4, 5, 6)
COLUMNS = ["package", "class", "closed", "payload", "prefix_end", "block_start", "gap", "count", "elem_end",
           "residue", "group_len", "group_end", "overlap", "group_hex", "loc_props", "loc_to_end", "loc_first",
           "loc_decl", "grp_props", "grp_to_end", "grp_first", "grp_decl", "base", "verdict"]


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


crah = _load("class-record-array-head")
cdef = _load("class-defaults")
pstream = _load("prop-stream")


# ---------------------------------------------------------------- the group, unanchored

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


def walk_names(buf, p, nnames, lo, hi):
    """A compact count then that many one-compact elements, every index inside the name table."""
    if p < lo or p >= hi:
        return None
    try:
        count, o = read_index(buf, p)
    except IndexError:
        return None
    if count < 0 or count > MAX_NAMES:
        return None
    vals = []
    for _ in range(count):
        if o >= hi:
            return None
        try:
            v, e = read_index(buf, o)
        except IndexError:
            return None
        if e - o > WIDTH_MAX or e > p + MAX_GROUP:
            return None
        vals.append(v)
        o = e
    if any(not (0 <= v < nnames) for v in vals):
        return None
    return {"count": count, "vals": vals, "end": o}


def free_group(buf, p, nnames, hi=None):
    """Names array, reference, name, list array -- the writer's order, with no required end.

    The anchored parse (`tools/class-tail-run.py`) demands the list end be the block start; that is what a start
    placed inside the group breaks. Here the end is an output, not a condition.
    """
    hi = len(buf) if hi is None else hi
    names = walk_names(buf, p, nnames, 0, hi)
    if names is None:
        return None
    q = names["end"]
    try:
        ref, q = read_index(buf, q)
        name, q = read_index(buf, q)
    except IndexError:
        return None
    if not (0 <= name < nnames) or q - p > MAX_GROUP:
        return None
    lst = walk_names(buf, q, nnames, 0, hi)
    if lst is None or lst["end"] - p > MAX_GROUP:
        return None
    return {"len": lst["end"] - p, "end": lst["end"], "names": names["count"], "ref": ref, "cfg": name,
            "list": lst["count"], "hex": buf[p:lst["end"]].hex(" ").upper()}


def find_group(buf, p, nnames):
    """The group at `p`, or the first start within one byte of it: the element walk can end mid-compact."""
    for q in (p, p + 1, p - 1):
        if 0 <= q < len(buf):
            g = free_group(buf, q, nnames)
            if g is not None:
                return q, g
    return None, None


# ---------------------------------------------------------------- the block start, by its property stream

def tag_stream(data, off, pkg):
    """`(props, to_end, first_name, declared_fraction)` for a candidate block start."""
    if pkg is None or not (0 <= off < len(data)):
        return 0, 0, "", ""
    props, end, reason = pstream.read_block(data[off:], pkg, False)
    to_end = 1 if reason == "None" and off + end == len(data) else 0
    first = props[0]["name"] if props else ""
    return len(props), to_end, first, " ".join(p["name"] for p in props)


def declared_frac(names, known):
    vals = [n for n in names.split() if n]
    if not vals:
        return ""
    return "%.3f" % (sum(1 for n in vals if n in known) / float(len(vals)))


def measure(data, prefix_end, block, nnames, pkg, known):
    row = {c: "" for c in COLUMNS}
    row.update({"payload": len(data), "prefix_end": prefix_end, "block_start": block})
    start = prefix_end + DOC_OFFSET
    gap = None
    if start + 18 <= len(data):
        gap = crah_gap(data, start)
    row["gap"] = "" if gap is None else gap
    if gap is None or block <= start + gap:
        return row
    w = crah.walk_elements(data, start + gap)
    if not w:
        return row
    end = w["end"]
    row.update({"count": w["count"], "elem_end": end, "residue": block - end})
    goff, g = find_group(data, end, nnames)
    if g is not None:
        row.update({"group_len": g["len"], "group_end": goff + g["len"], "overlap": goff + g["len"] - block,
                    "group_hex": g["hex"]})
    hits = 0
    for k in BASE_OFFSETS:
        q = end + k
        if 0 <= q < len(data) and find_group(data, q, nnames)[1] is not None:
            hits += 1
    row["base"] = hits
    for tag, off in (("loc", block), ("grp", row["group_end"] if row["group_end"] != "" else None)):
        if off is None:
            continue
        n, to_end, first, names = tag_stream(data, off, pkg)
        row["%s_props" % tag] = n
        row["%s_to_end" % tag] = to_end
        row["%s_first" % tag] = first
        row["%s_decl" % tag] = declared_frac(names, known)
    loc_ok = row["loc_to_end"] == 1 and row["loc_decl"] != "" and float(row["loc_decl"]) >= 0.999
    grp_ok = row["grp_to_end"] == 1 and row["grp_decl"] != "" and float(row["grp_decl"]) >= 0.999
    row["verdict"] = ("both" if loc_ok and grp_ok else "block-stands" if loc_ok
                      else "group-end-stands" if grp_ok else "neither")
    return row


def crah_gap(data, start):
    """The gap length the `class-record-open-front.py` shape implies at `start` (widest modal pass)."""
    for widths in ((19, 20, 21), (18, 22)):
        good = [g for g in widths
                if zero_run_end(data, start, g) and front_ok(data, start, g)]
        if good:
            return max(good)
    return None


def zero_run_end(data, start, gap):
    s, e = start + gap - 16, start + gap
    if s < 0 or e > len(data):
        return False
    return data[s:e] == b"\x00" * 16


def front_ok(data, start, gap):
    o, end = start, start + gap - 16
    while o < end:
        try:
            _v, e = read_index(data, o)
        except IndexError:
            return False
        if e <= o or e > end:
            return False
        o = e
    return True


# ---------------------------------------------------------------- the sweep

def head_rows():
    with open(os.path.join(ROOT, "inventory", HEAD_ARTIFACT), newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def known_sets():
    """class -> names its ancestors and it declare (the set `class-defaults.py` scores with)."""
    decl = cdef.declared_properties()
    supers = cdef.super_map()
    out = {}
    for cls, own in decl.items():
        names, cur, seen = set(), cls, set()
        while cur and cur not in seen:
            seen.add(cur)
            names |= decl.get(cur, set())
            cur = supers.get(cur)
        out[cls] = names
    return out


def measure_one(pkg, row, data, known):
    return measure(data, int(row["prefix_end"]), int(row["block_start"]), len(pkg.names or []), pkg, known)


def sweep(args):
    rows = head_rows()
    by_pkg = collections.defaultdict(list)
    for r in rows:
        by_pkg[r["package"]].append(r)
    known = known_sets()
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
            rec = {"package": pkgname, "class": r["class"], "closed": int(r["arr_count"] != "")}
            out.append(rec)
            if not r.get("prefix_end") or not r.get("block_start"):
                continue
            exp = index.get(r["class"])
            if exp is None:
                continue
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            rec.update(measure_one(pkg, r, data, known.get(r["class"], set())))
            rec["package"], rec["class"], rec["closed"] = pkgname, r["class"], int(r["arr_count"] != "")
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
    rows = [r for r in head_rows() if r["package"] == pkgname and r["class"] == cls]
    if not rows:
        print("not a located class: %s" % args.cls, file=sys.stderr)
        return 2
    pkg = Package(os.path.join(args.dump, "System", pkgname + ".u"))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    exp = next((e for e in pkg.exports if pkg.name(e["name"]) == cls), None)
    data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    known = known_sets().get(cls, set())
    m = measure_one(pkg, rows[0], data, known)
    print("%s: payload %d, block %s, gap %s, count %s, element end %s (residue %s)"
          % (args.cls, len(data), m["block_start"], m["gap"], m["count"], m["elem_end"], m["residue"]))
    print("  group at the element end: %s bytes, ends %s (overlap %s past the block start)"
          % (m["group_len"], m["group_end"], m["overlap"]))
    if m["group_hex"]:
        print("    %s" % m["group_hex"])
    print("  tags from the located block start: %s props, to the export end %s, first %r, declared %s"
          % (m["loc_props"], m["loc_to_end"], m["loc_first"], m["loc_decl"]))
    print("  tags from the group end        : %s props, to the export end %s, first %r, declared %s"
          % (m["grp_props"], m["grp_to_end"], m["grp_first"], m["grp_decl"]))
    print("  verdict: %s" % m["verdict"])
    return 0


def _share(n, d):
    return "%.1f%%" % (100.0 * n / d) if d else "-"


def report(rows=None):
    if rows is None:
        with open(os.path.join(ROOT, "inventory", ARTIFACT), newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    read = [r for r in rows if str(r.get("count") or "") != ""]
    print("located rows read: %d (of %d in the head table)" % (len(read), len(rows)))
    for closed, label in ((1, "CLOSING"), (0, "OPEN")):
        sub = [r for r in read if int(r["closed"]) == closed]
        if not sub:
            continue
        print("\n%s rows walked: %d" % (label, len(sub)))
        print("  the group parses from the element end: %d (%s)"
              % (sum(1 for r in sub if r["group_len"] != ""), _share(sum(1 for r in sub if r["group_len"] != ""),
                                                                     len(sub))))
        ov = collections.Counter(int(r["overlap"]) for r in sub if r["overlap"] != "")
        print("  overlap (group end - block start): %s" % ov.most_common(10))
        print("  overlap == 0 (the group ends on the block start): %d (%s)"
              % (sum(1 for r in sub if r["overlap"] == "0"), _share(sum(1 for r in sub if r["overlap"] == "0"),
                                                                   len(sub))))
        print("  verdict: %s" % collections.Counter(r["verdict"] for r in sub).most_common())
        print("  base rate (same parse at 12 offsets): mean %.2f, zero on %d"
              % (sum(int(r["base"]) for r in sub if r["base"] != "") / max(1, len(sub)),
                 sum(1 for r in sub if r["base"] == "0")))
        for tag in ("loc", "grp"):
            vals = [float(r["%s_decl" % tag]) for r in sub if r["%s_decl" % tag] != ""]
            if vals:
                print("  %s declared fraction: mean %.3f, fully declared on %d of %d"
                      % (tag, sum(vals) / len(vals), sum(1 for v in vals if v >= 0.999), len(vals)))
    short = [r for r in read if int(r["closed"]) == 0 and r["residue"] != "" and 1 <= int(r["residue"]) <= 9]
    print("\nthe 1-9-byte-short population: %d rows" % len(short))
    if short:
        print("  the group parses there: %d (%s)"
              % (sum(1 for r in short if r["group_len"] != ""), _share(sum(1 for r in short if r["group_len"] != ""),
                                                                       len(short))))
        print("  overlap: %s" % collections.Counter(int(r["overlap"]) for r in short
                                                    if r["overlap"] != "").most_common(10))
        print("  overlap <= 0: %d" % sum(1 for r in short if r["overlap"] != "" and int(r["overlap"]) <= 0))
        print("  verdict: %s" % collections.Counter(r["verdict"] for r in short).most_common())
        for tag in ("loc", "grp"):
            vals = [float(r["%s_decl" % tag]) for r in short if r["%s_decl" % tag] != ""]
            if vals:
                print("  %s declared fraction: mean %.3f, fully declared on %d of %d, zero on %d"
                      % (tag, sum(vals) / len(vals), sum(1 for v in vals if v >= 0.999), len(vals),
                         sum(1 for v in vals if v == 0.0)))
    return 0


# ---------------------------------------------------------------- the gate

def self_check():
    """Synthetic bodies: the free parse's four fields, its bound, and the overlap arithmetic."""
    def body(front=b"\x02\x00\x00", count=2, group=b"\x03\x01\x02\x03\x87\x04\x00", extra=b"\x00\x00\x00\x00"):
        out = bytearray(DOC_OFFSET) + front + b"\x00" * 16 + bytes([count])
        for i in range(count):
            out += bytes([i + 1]) + b"\x00" * 8
        gstart = len(out)
        out += group + extra
        return bytes(out), gstart, len(out) - len(extra)

    data, gstart, block = body()
    nnames = 40
    g = free_group(data, gstart, nnames)
    assert g is not None and g["len"] == 7, g
    assert (g["names"], g["ref"], g["cfg"], g["list"]) == (3, -7, 4, 0), g
    # the located block start inside the group: the group still parses, and the overlap is positive
    m = measure(data, 0, gstart + 3, nnames, None, set())
    assert m["group_len"] == 7 and m["overlap"] == 4, m
    # anchored at the group's end the free parse is the same group (the control the closing rows give)
    m = measure(data, 0, gstart + 7, nnames, None, set())
    assert m["overlap"] == 0, m
    # a name index outside the table is not a group
    bad = b"\x03\x01\x02\x09\x87\x04\x00"
    d2, g2, _ = body(group=bad)
    assert free_group(d2, g2, 8) is None
    # a count that runs past the bound is refused rather than read as zeros
    d3, g3, _ = body(group=b"\x09" + b"\x01" * 8)
    assert free_group(d3, g3, nnames, hi=g3 + 5) is None
    # one byte later is a different parse, which is why the base rate is measured
    d4, g4, _ = body(group=b"\x01\x01\x01\x00\x01\x00")
    assert free_group(d4, g4, nnames)["len"] == 6
    assert free_group(d4, g4 + 1, nnames)["len"] == 5
    print("self-check ok: the group parses unanchored, its end is an output, a start inside it still parses, "
          "an out-of-table index and an over-long count are refused")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", help="dump root (the directory holding System/)")
    ap.add_argument("--class", dest="cls", help="one class, package.Class")
    ap.add_argument("--sweep", action="store_true", help="read every located row")
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
