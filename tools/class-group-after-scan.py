#!/usr/bin/env python3
"""Which offset past the closing group opens the block: the 13 rows where the group's end opens nothing usable.

`tools/class-group-stop.py` reads the property tag stream from the closing group's end on the rows the start chooser
refuses, and on **13** of them that end opens nothing usable -- six parse no tag at all, seven open an empty block --
while 2-459 bytes of payload still follow it. So on those rows the record does continue past the group and the
boundary is some offset inside those bytes, not the group's end. This tool measures which offset: it reads the tag
stream from every offset in `[group_end, group_end + WINDOW)` and asks which one ends on the payload's last byte with
every tag naming a property the class or an ancestor declares.

Populations are read from the two committed geometries, so nothing is re-derived here:

* **scan** -- `class_group_stop.csv`'s refused rows whose verdict is `no-block-after-group` or
  `empty-block-after-group` with more than one byte left after the group (the 13 the last PLAN note named);
* **control** -- `class_record_open_tail.csv`'s rows with `overlap == 0`, where the element walk and the group parse
  independently put the group's end on the located block start (1,586 rows). There the answer must be offset 0, and
  every *other* offset that also answers is the scan's own permissiveness -- the base rate a hit on the 13 is
  measured against.

A scan row is one `(class, offset)` test; a control row is one class, carrying the smallest offset that answered and
how many offsets answered at all (`n_full`), so the control's base rate stays readable without 19,000 rows.

    python3 tools/class-group-after-scan.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-group-after-scan.py --report
    python3 tools/class-group-after-scan.py --dump $SG1_DUMP --class Engine.Weapon
    python3 tools/class-group-after-scan.py --check        # synthetic bodies, no dump, writes nothing

Reads the packages and two committed tables; no boot, no Ghidra round, no property value decoded.
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

STOP = "class_group_stop.csv"          # refused rows: verdict, after, group_from/len/end, shipped
TAIL = "class_record_open_tail.csv"    # control rows: overlap == 0, group_end, group_len, block_start
ARTIFACT = "class_group_after_scan.csv"
WINDOW = 12                            # offsets tested, measured from the group's end
COLUMNS = ["package", "class", "population", "payload", "shipped", "group_from", "group_len", "group_end", "after",
           "k", "offset", "props", "length", "to_end", "decl", "first", "last", "verdict", "n_full", "full_offsets"]


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


cdef = _load("class-defaults")
pstream = _load("prop-stream")


def declared_for(cls, decl, supers):
    names, cur, seen = set(), cls, set()
    while cur and cur not in seen:
        seen.add(cur)
        names |= decl.get(cur, set())
        cur = supers.get(cur)
    return names


def read(path):
    with open(os.path.join(ROOT, "inventory", path), newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def populations():
    """`(package, class)` -> row. Scan rows are the 13; control rows are the element-side clean population."""
    out, refused = {}, set()
    for r in read(STOP):
        if r.get("population") != "refused":
            continue
        # every refused row is excluded from the control, not only the 13 the scan reads: five of them also carry
        # `overlap == 0` in the element-side table, and letting those in would count a refused row as a witness
        refused.add((r["package"], r["class"]))
        if r.get("verdict") not in ("no-block-after-group", "empty-block-after-group"):
            continue
        if not r.get("after") or int(r["after"]) <= 1:
            continue
        out[(r["package"], r["class"])] = {
            "population": "scan", "shipped": int(r["shipped"]), "from": int(r["group_from"]),
            "len": int(r["group_len"]), "end": int(r["group_end"]), "verdict": r["verdict"],
            "after": int(r["after"])}
    for r in read(TAIL):
        key = (r["package"], r["class"])
        if key in refused or r.get("overlap") != "0" or not r.get("group_end"):
            continue
        out[key] = {"population": "control", "shipped": int(r["block_start"]),
                    "from": int(r["group_end"]) - int(r["group_len"]), "len": int(r["group_len"]),
                    "end": int(r["group_end"]), "verdict": "", "after": None}
    return out


def scan_row(data, pkg, row, decl, k):
    """One offset past the group end, read as a property block."""
    end, off = row["end"], row["end"] + k
    out = {c: "" for c in COLUMNS}
    out.update({"payload": len(data), "shipped": row["shipped"], "group_from": row["from"],
                "group_len": row["len"], "group_end": end, "k": k, "offset": off,
                "population": row["population"]})
    if row["after"] is not None:
        out["after"] = row["after"]
    else:
        out["after"] = len(data) - end
    if off > len(data):
        out["verdict"] = "past-payload"
        return out
    if off == len(data):
        out["props"] = 0
        out["to_end"] = 1
        out["verdict"] = "nothing-left"
        return out
    try:
        props, length, reason = pstream.read_block(data[off:], pkg, False)
    except Exception:
        # a malformed body can run the value reader off the end: that offset opens nothing readable
        out["verdict"] = "unreadable"
        return out
    names = [p["name"] for p in props]
    known = sum(1 for n in names if n in decl)
    out.update({"props": len(props),
                "length": length if reason == "None" else "",
                "to_end": 1 if reason == "None" and off + length == len(data) else 0,
                "first": names[0] if names else "",
                "last": names[-1] if names else "",
                "decl": "%.3f" % (known / float(len(names))) if names else ""})
    if reason != "None":
        out["verdict"] = "no-stream"
    elif not props:
        out["verdict"] = "empty"
    elif off + length != len(data):
        out["verdict"] = "short-of-end"
    elif known != len(names):
        out["verdict"] = "undeclared"
    else:
        out["verdict"] = "declared-to-end"
    return out


def scan_class(data, pkg, row, decl):
    """Every offset in the window, plus the class-level answer the rows carry.

    The pick is the answering offset that reads the **most** properties, ties to the smallest: a block read from
    inside another one is a suffix of it and therefore strictly shorter, so the widest reading is the boundary and the
    others are the scan's own permissiveness (measured on the control, not assumed away).
    """
    rows = []
    for k in range(WINDOW):
        if row["end"] + k > len(data):
            break
        rows.append(scan_row(data, pkg, row, decl, k))
    full = [r for r in rows if r["verdict"] == "declared-to-end"]
    best = min(full, key=lambda r: (-int(r["props"]), int(r["k"])))["k"] if full else ""
    if row["population"] == "control":
        # one row per class: the picked offset, carrying how many answered
        keep = [r for r in rows if r["k"] == best] or [r for r in rows if r["k"] == 0][:1]
        for r in keep:
            r["n_full"] = len(full)
            r["full_offsets"] = ",".join(str(r["k"]) for r in full)
        return keep
    for r in rows:
        r["n_full"] = len(full)
        r["full_offsets"] = ",".join(str(r["k"]) for r in full)
    return rows


def sweep(args):
    pops = populations()
    decl_all = cdef.declared_properties()
    supers = cdef.super_map()
    by_pkg = collections.defaultdict(list)
    for (pkgname, cls), row in pops.items():
        by_pkg[pkgname].append((cls, row))
    out = []
    for pkgname in sorted(by_pkg):
        pkg = Package(os.path.join(args.dump, "System", pkgname + ".u"))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        index = {}
        for e in pkg.exports:
            index.setdefault(pkg.name(e["name"]), e)
        for cls, row in sorted(by_pkg[pkgname]):
            exp = index.get(cls)
            rec = {"package": pkgname, "class": cls, "population": row["population"]}
            if exp is None:
                rec["verdict"] = "no-export"
                out.append(rec)
                continue
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            for r in scan_class(data, pkg, row, declared_for(cls, decl_all, supers)):
                r["package"], r["class"] = pkgname, cls
                out.append(r)
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


def _pct(n, d):
    return "%.1f%%" % (100.0 * n / d) if d else "-"


def _by_class(rows):
    per = collections.defaultdict(list)
    for r in rows:
        per[(r["package"], r["class"])].append(r)
    return per


def report(rows=None):
    if rows is None:
        rows = read(ARTIFACT)
    for r in rows:
        r["k"] = int(r["k"]) if str(r["k"]).isdigit() else 0
    print("rows: %d" % len(rows))
    scan = [r for r in rows if r.get("population") == "scan"]
    ctrl = [r for r in rows if r.get("population") == "control"]
    if scan:
        per = _by_class(scan)
        print("\nSCAN: %d classes, %d (class, offset) tests" % (len(per), len(scan)))
        print("  verdicts: %s" % collections.Counter(r["verdict"] for r in scan).most_common())
        answered, root = [], 0
        for key, rs in sorted(per.items()):
            full = [r for r in rs if r["verdict"] == "declared-to-end"]
            at0 = [r for r in full if r["k"] == 0]
            root += len(at0)
            if full:
                answered.append((key, min(int(r["k"]) for r in full), len(full), rs))
        print("  a declared block to the payload end opens at offset 0 on %d of %d, at some offset in the "
              "window on %d (%s)" % (root, len(per), len(answered), _pct(len(answered), len(per))))
        print("  offsets that answer, over the classes that have one: %s"
              % collections.Counter(n for _, _, n, _ in answered).most_common())
        print("  the smallest answering offset: %s"
              % collections.Counter(k for _, k, _, _ in answered).most_common())
        print("\n  per class (k = the smallest offset with a declared block to the payload end):")
        for key, k, n, rs in answered:
            first = [r for r in rs if r["k"] == 0][0]
            at = [r for r in rs if r["k"] == k][0]
            print("    %-42s k=%2d  offset 0: %-10s props %-3s decl %-6s | k: props %-3s decl %-6s first %-24s "
                  "also %s" % ("%s.%s" % key, k, first["verdict"], first["props"], first["decl"], at["props"],
                               at["decl"], at["first"], n))
        silent = [key for key in per if not any(r["verdict"] == "declared-to-end" for r in per[key])]
        print("  classes with no declared block to the end anywhere in the window: %d %s"
              % (len(silent), ["%s.%s" % k for k in silent]))
    if ctrl:
        at0 = sum(1 for r in ctrl if r["k"] == 0 and r["n_full"] and str(r["n_full"]).isdigit())
        print("\nCONTROL: %d classes" % len(ctrl))
        print("  offset 0 answers (declared block to the payload end) on %d (%s)"
              % (at0, _pct(at0, len(ctrl))))
        nf = collections.Counter(int(r["n_full"]) for r in ctrl if str(r["n_full"]).isdigit())
        print("  offsets that answer per class: %s" % nf.most_common(6))
        multi = sum(1 for r in ctrl if str(r["n_full"]).isdigit() and int(r["n_full"]) > 1)
        print("  more than one offset answers on %d (%s) -- the scan's own permissiveness, which is what a hit on "
              "the scan rows is measured against" % (multi, _pct(multi, len(ctrl))))
        extra = sum(int(r["n_full"]) - 1 for r in ctrl if str(r["n_full"]).isdigit() and int(r["n_full"]) > 1)
        print("  and the extra offsets over those: %d (%.2f per multi-answer class)"
              % (extra, extra / float(multi) if multi else 0.0))
        moved = sum(1 for r in ctrl if r["k"] != 0)
        print("  the smallest answering offset is not 0 on %d of %d" % (moved, len(ctrl)))
    return 0


def one(args):
    if not args.dump or not args.cls:
        print("--class needs --dump", file=sys.stderr)
        return 2
    pkgname, cls = args.cls.split(".", 1)
    row = populations().get((pkgname, cls))
    if row is None:
        print("not a scan or control class: %s" % args.cls, file=sys.stderr)
        return 2
    pkg = Package(os.path.join(args.dump, "System", pkgname + ".u"))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    exp = next((e for e in pkg.exports if pkg.name(e["name"]) == cls), None)
    data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    rows = scan_class(data, pkg, row, declared_for(cls, cdef.declared_properties(), cdef.super_map()))
    print("%s (%s): payload %s, shipped start %s, group %s at %s, %s left after it"
          % (args.cls, row["population"], len(data), row["shipped"], row["len"], row["from"],
             len(data) - row["end"]))
    for r in rows:
        print("  k=%2d offset %4s: %-15s props %-3s len %-4s to_end %s decl %-6s first %-24s last %s"
              % (r["k"], r["offset"], r["verdict"], r["props"], r["length"], r["to_end"], r["decl"], r["first"],
                 r["last"]))
    return 0


def self_check():
    """Synthetic bodies: a block four bytes past the group, a window with two answers, and a group ending the payload."""

    class _Pkg:
        exports = []
        imports = []
        names = ["None"] + ["N%d" % i for i in range(1, 40)]

        def name(self, i):
            return self.names[i] if 0 <= i < len(self.names) else "?"

    pkg = _Pkg()
    decl = {"N3", "N4"}
    group = b"\x03\x01\x02\x03\x87\x04\x00\x00\x00"
    tag = b"\x03\x01\x07"                          # property N3: byte, size 1, value 7 (3 bytes per tag)
    tail = b"\x04\x01\x09"                         # property N4: byte, size 1, value 9
    row = {"population": "scan", "shipped": 19, "from": 16, "len": len(group), "end": 16 + len(group),
           "verdict": "", "after": None}
    # a four-byte native field between the group and the block: only k=4 lands
    data = bytes(16) + group + b"\xde\xad\xbe\xef" + tag + tail + b"\x00"   # None terminates the block
    rows = scan_class(data, pkg, row, decl)
    good = [r for r in rows if r["verdict"] == "declared-to-end"]
    # the reader is permissive: a suffix of the last tag's bytes can also parse, which is why the pick is the
    # widest reading and why the control measures how often a second offset answers
    assert 4 in [r["k"] for r in good] and 0 not in [r["k"] for r in good], [(r["k"], r["verdict"]) for r in rows]
    at4 = [r for r in good if r["k"] == 4][0]
    assert at4["props"] == 2 and at4["first"] == "N3" and at4["last"] == "N4", at4
    # a stream read from inside the block is a suffix of it, so the widest reading is the boundary
    widest = min(good, key=lambda r: (-int(r["props"]), int(r["k"])))
    assert widest["k"] == 4, [(r["k"], r["props"]) for r in good]
    # the same body with a group that ends the payload answers nothing but k=0's "nothing-left"
    end_row = dict(row, end=16 + len(group) + 8)
    short = [r for r in scan_class(bytes(16) + group + bytes(8), pkg, end_row, decl) if r["k"] == 0]
    assert short and short[0]["verdict"] == "nothing-left", short
    # an undeclared stream is not a hit, even when it reaches the end
    rows2 = scan_class(bytes(16) + group + tag + tail + b"\x00", pkg, row, {"N98", "N99"})
    assert not [r for r in rows2 if r["verdict"] == "declared-to-end"], rows2
    print("self-check ok: a block four bytes past the group is found at k=4 and not at k=0, a group that ends the "
          "payload has nothing left at k=0, and a stream naming no declared property is not a hit")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", help="dump root (the directory holding System/)")
    ap.add_argument("--class", dest="cls", help="one class, package.Class")
    ap.add_argument("--sweep", action="store_true", help="test every offset on the 13 and the control")
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
