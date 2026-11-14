#!/usr/bin/env python3
"""What follows the closing group: on the rows the guard refuses, nothing -- and that is the answer for 45 of them.

`tools/class-blockstart-guard.py` refuses a start a committed closing group covers and offers the group's end as a
candidate. On 45 rows that leaves no candidate at all, and this tool measures why: it reads the class's property tag
stream from the group's end and reports whether a block opens there at all.

The population and the control are read from the two committed geometries, so neither is re-derived here:

* **refused** -- the 297 rows `class_blockstart_guard.csv` marks `inside-group`, with the group the guard found
  (`group_from`/`group_len`/`group_end`);
* **control** -- the 1,596 rows `class_record_open_tail.csv` measures with `overlap == 0`, where the element walk and
  the group parse independently put the group's end on the located block start. There the group end is the block
  start by two witnesses, so a tag stream that opens there is the honest expectation; it is what separates "no block
  after the group" from "this instrument cannot read one".

Per row: the anchored end (a group of this start has one end -- `class-tail-run.py`'s `parse_group` closes the list at
an explicit stop, so the free parse's end is not re-derived by a second parse), the tag stream from it
(`props`, `to_end`, `first`, `decl`), what is left of the payload after it (`after`), and whether the last property
the stream names ends on the payload's last byte.

    python3 tools/class-group-stop.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-group-stop.py --report
    python3 tools/class-group-stop.py --dump $SG1_DUMP --class Engine.SavedMove
    python3 tools/class-group-stop.py --check        # synthetic bodies, no dump, writes nothing

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

GUARD = "class_blockstart_guard.csv"       # refused rows: guard, group_from, group_len, group_end, shipped, delta
TAIL = "class_record_open_tail.csv"        # control rows: overlap == 0, group_end
ARTIFACT = "class_group_stop.csv"
COLUMNS = ["package", "class", "population", "guard_verdict", "payload", "shipped", "group_from", "group_len",
           "group_end", "after", "props", "to_end", "first", "decl", "last", "verdict"]


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
    """`(package, class)` -> row, from the guard's refusals and the element-side control."""
    out = {}
    for r in read(GUARD):
        if r["guard"] == "inside-group" and r.get("group_from"):
            out[(r["package"], r["class"])] = {
                "population": "refused", "shipped": int(r["shipped"]), "from": int(r["group_from"]),
                "len": int(r["group_len"]), "end": int(r["group_end"]), "verdict": r["verdict"],
                "guard_verdict": r["verdict"], "delta": r["delta"]}
    for r in read(TAIL):
        key = (r["package"], r["class"])
        if key in out or r.get("overlap") != "0" or not r.get("group_end"):
            continue
        out[key] = {"population": "control", "shipped": int(r["block_start"]), "from": int(r["group_end"])
                    - int(r["group_len"]), "len": int(r["group_len"]), "end": int(r["group_end"]), "verdict": "",
                    "guard_verdict": "", "delta": ""}
    return out


def measure(data, pkg, row, decl):
    """The tag stream that opens where the group ends."""
    end = row["end"]
    out = {c: "" for c in COLUMNS}
    out.update({"payload": len(data), "shipped": row["shipped"], "group_from": row["from"], "group_len": row["len"],
                "group_end": end, "after": len(data) - end, "population": row["population"],
                "guard_verdict": row.get("guard_verdict", "")})
    if end == len(data):
        # the group's last byte is the payload's last byte: nothing follows it at all
        out.update({"props": 0, "verdict": "group-ends-payload"})
        return out
    if not (0 <= end < len(data)):
        out["verdict"] = "group-past-payload"
        return out
    props, length, reason = pstream.read_block(data[end:], pkg, False)
    names = [p["name"] for p in props]
    known = sum(1 for n in names if n in decl)
    out.update({"props": len(props), "to_end": 1 if reason == "None" and end + length == len(data) else 0,
                "first": names[0] if names else "",
                "decl": "%.3f" % (known / float(len(names))) if names else "",
                "last": names[-1] if names else ""})
    if reason != "None":
        out["verdict"] = "no-block-after-group"
    elif not props:
        out["verdict"] = "empty-block-after-group"
    elif end + length == len(data):
        out["verdict"] = "block-after-group"
    else:
        out["verdict"] = "block-short-of-end"
    return out


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
        for cls, row in by_pkg[pkgname]:
            rec = {"package": pkgname, "class": cls}
            exp = index.get(cls)
            if exp is None:
                rec["verdict"] = "no-export"
                out.append(rec)
                continue
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            rec.update(measure(data, pkg, row, declared_for(cls, decl_all, supers)))
            rec["package"], rec["class"] = pkgname, cls
            out.append(rec)
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


def _share(n, d):
    return "%.1f%%" % (100.0 * n / d) if d else "-"


def report(rows=None):
    if rows is None:
        rows = read(ARTIFACT)
    print("rows: %d" % len(rows))
    read_rows = [r for r in rows if r.get("population") in ("refused", "control")]
    for pop in ("refused", "control"):
        sub = [r for r in read_rows if r["population"] == pop]
        if not sub:
            continue
        print("\n%s: %d rows" % (pop.upper(), len(sub)))
        print("  verdicts: %s" % collections.Counter(r["verdict"] for r in sub).most_common())
        print("  bytes left after the group end: %s"
              % collections.Counter(int(r["after"]) for r in sub if str(r["after"]) != "").most_common(8))
        got = [r for r in sub if str(r["props"]) not in ("", "0")]
        print("  a tag stream opens there on %d (%s); reaches the payload end on %d"
              % (len(got), _share(len(got), len(sub)), sum(1 for r in sub if str(r["to_end"]) == "1")))
        if got:
            print("  declared fraction of the stream: mean %.3f, fully declared on %d of %d"
                  % (sum(float(r["decl"]) for r in got if r["decl"] != "") / len(got),
                     sum(1 for r in got if r["decl"] != "" and float(r["decl"]) >= 0.999), len(got)))
        print("  first tag: %s" % collections.Counter(r["first"] for r in got).most_common(6))
    refused = [r for r in read_rows if r["population"] == "refused"]
    empty = [r for r in refused
             if r["verdict"] in ("no-block-after-group", "empty-block-after-group", "group-ends-payload")]
    print("\nrefused rows with no block after the group: %d" % len(empty))
    if empty:
        print("  bytes left after the group end: %s"
              % collections.Counter(int(r["after"]) for r in empty).most_common(8))
        print("  the group ends within a byte of the payload on %d of %d"
              % (sum(1 for r in empty if int(r["after"]) <= 1), len(empty)))
    moved = [r for r in refused if r["verdict"] == "block-after-group"]
    print("  of the refused rows, %d have a block that runs to the payload end" % len(moved))
    print("\ncross-tab (the guard's own verdict x what opens at the group end):")
    for gv in ("inside-group-moved", "no-survivor"):
        sub = [r for r in refused if r.get("guard_verdict") == gv]
        if not sub:
            continue
        got = sum(1 for r in sub if str(r["props"]) not in ("", "0"))
        print("  %-20s %3d rows, a block opens on %3d, an empty or absent one on %3d"
              % (gv, len(sub), got, len(sub) - got))
        print("    verdicts: %s" % collections.Counter(r["verdict"] for r in sub).most_common())
        vals = [int(r["after"]) for r in sub if str(r["after"]) != ""]
        if vals:
            print("    bytes left after the group: the group ends within a byte of the payload on %d of %d"
                  % (sum(1 for v in vals if v <= 1), len(vals)))
    return 0


def one(args):
    if not args.dump or not args.cls:
        print("--class needs --dump", file=sys.stderr)
        return 2
    pkgname, cls = args.cls.split(".", 1)
    row = populations().get((pkgname, cls))
    if row is None:
        print("not a refused or control row: %s" % args.cls, file=sys.stderr)
        return 2
    pkg = Package(os.path.join(args.dump, "System", pkgname + ".u"))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    exp = next((e for e in pkg.exports if pkg.name(e["name"]) == cls), None)
    data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    m = measure(data, pkg, row, declared_for(cls, cdef.declared_properties(), cdef.super_map()))
    print("%s (%s): payload %s, shipped start %s, group %s at %s (%s bytes), %s left after it"
          % (args.cls, row["population"], m["payload"], m["shipped"], m["group_len"], m["group_from"],
             m["group_end"], m["after"]))
    print("  tags from the group end: %s props, to the payload end %s, first %r, declared %s"
          % (m["props"], m["to_end"], m["first"], m["decl"]))
    print("  verdict: %s" % m["verdict"])
    return 0


def self_check():
    """Synthetic payloads: a group a block follows, and one that ends the payload."""

    class _Pkg:
        exports = []
        imports = []
        names = ["None"] + ["N%d" % i for i in range(1, 40)]

        def name(self, i):
            return self.names[i] if 0 <= i < len(self.names) else "?"

    pkg = _Pkg()
    group = b"\x03\x01\x02\x03\x87\x04\x00"
    tag = b"\x03\x01\x01\x00"                        # one property: name 3, byte type, size 1, value 0
    data = bytes(16) + group + tag
    row = {"population": "refused", "shipped": 19, "from": 16, "len": 7, "end": 23, "verdict": "", "delta": ""}
    m = measure(data, pkg, row, {"N3"})
    assert m["verdict"] == "block-after-group" and m["props"] == 1 and m["after"] == 4, m
    assert m["first"] == "N3" and m["decl"] == "1.000", m
    # a group whose end is the payload's end has nothing after it
    m2 = measure(bytes(16) + group, pkg, row, set())
    assert m2["after"] == 0 and m2["verdict"] == "group-ends-payload", m2
    # an end past the payload is refused rather than clamped
    row3 = dict(row, end=len(data) + 4)
    assert measure(data, pkg, row3, set())["verdict"] == "group-past-payload"
    # a tag that runs past the payload is a short block, not one that reaches the end
    m4 = measure(data[:len(data) - 1], pkg, row, set())
    assert m4["verdict"] != "block-after-group", m4
    print("self-check ok: the stream from a group end is read as an output, a group that ends the payload has "
          "nothing after it, a truncated tag is not a block to the end, and an end past the payload is refused")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", help="dump root (the directory holding System/)")
    ap.add_argument("--class", dest="cls", help="one class, package.Class")
    ap.add_argument("--sweep", action="store_true", help="read every refused row and the control")
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
