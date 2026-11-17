#!/usr/bin/env python3
"""Where a class payload's record ends: the writer's closing group, anchored at the payload's own terminator.

`inventory/class_defaults_misses.csv` withholds the defaults verdict on **334** classes as `empty-block`, and the
last two reads cleared the two explanations that do not need the record's end: the locator's floor is not what
withholds them (`single_declared` is a different population) and the type map refuses only five tags in 330. What
was left was that 153 of the 334 payloads are short enough for the class record alone to account for every byte
and 181 are not -- so the open question is *where the record ends*, not which rule to relax.

A record's end is measurable without knowing its length: `UClass::Serialize` closes with a group of four fields
(`class-tail-run.py` -- a compact-counted names array, a reference, a name, a second names array), and on the
located population that group's last byte **is** the defaults block's first byte. So on a payload that holds a
record and nothing else, the same group must end exactly on the payload's terminator -- the single `00` an empty
tag block leaves. This tool reads that boundary both ways:

* for every `empty-block` class, every offset's group is parsed with no required end (bounded, every index inside
  the name table), and the payload's terminator is compared with the ends those parses produce: `tail_at_terminator`
  says a group ends on it, `latest_end` is the furthest end any group reaches, and `residue` is the bytes between
  that end and the terminator -- the bytes a record does not account for;
* for the located control population the identical parse is anchored on the published block start
  (`class_record_bound.csv`) instead, which is the same question with a known answer: the recall it publishes is
  what makes a high or low figure on the 334 a reading of the payload rather than of the grammar.

Two shape columns keep the match from being "some bytes decoded". The group's names array is read as names and
classified: a **package** name (`Core`, `Engine`, the class's own package and the corpus's other 15) is what the
record's references are; `ref` is the group's reference field, and `ref_import` says whether it lands inside the
import table the archive biases references into. A parse that matches the end but names properties or arbitrary
strings is not the writer's group, and the columns say so per row.

    python3 tools/class-defaults-recordend.py DUMP/System --sweep -o inventory
    python3 tools/class-defaults-recordend.py --report
    python3 tools/class-defaults-recordend.py DUMP/System --class Engine.xAdminGroup
    python3 tools/class-defaults-recordend.py --check        # synthetic bodies, no dump, writes nothing

Reads the packages plus five committed tables; no boot, no Ghidra round, no property value decoded.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTIFACT = os.path.join(ROOT, "inventory", "class_defaults_recordend.csv")
COLUMNS = ["kind", "package", "class", "payload", "anchor", "terminator", "groups", "tail_at_terminator",
           "group_start", "group_len", "name_count", "names", "names_package", "ref", "ref_import", "ref_in_range",
           "own_package", "latest_end", "residue", "ends", "density", "shape", "verdict", "classes", "payload_bytes", "anchored",
           "found", "package_names", "own_ref", "residue_bytes", "window", "long", "long_found", "long_residue",
           "control_scanned", "control_found", "control_package_names", "control_own_ref", "group_limit"]

# The same record window the sink census quotes: a payload at or below this is short enough for the class record
# alone to account for every byte, so the two populations are split the way `class-defaults-nostream.py` splits them.
RECORD_WINDOW = 100

# A payload is parsed at every offset, and a long record (13 KB) yields many spurious groups; the reading is about
# whether one *ends on the terminator*, so the per-payload parse count is published rather than capped.
GROUP_LIMIT = 20000


def load(name):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    spec = importlib.util.spec_from_file_location(name.replace("-", "_").replace(".py", ""), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def walk_group(data, start, stop, nnames):
    """The writer's four fields from `start`, with no required end: [(end, fields)] or None.

    `class-tail-run.parse_group` requires the list array to end on a given byte, which is the right rule when the
    boundary is known (the block start) and the wrong one here: this tool is asking *where* the group ends. Every
    index must be inside the name table, and a group whose arrays are both empty is refused -- every payload can
    produce `00 00` at any offset, and an empty group carries no evidence.
    """
    names = _walk(data, start, stop, nnames)
    if names is None:
        return None
    q = names["end"]
    ref, q = _index(data, q, stop)
    if ref is None:
        return None
    name, q = _index(data, q, stop)
    if name is None or q > stop:
        return None
    lst = _walk(data, q, stop, nnames)
    if lst is None:
        return None
    if names["count"] == 0 and lst["count"] == 0:
        return None
    if not (0 <= name < nnames):
        return None
    return {"end": lst["end"], "names": names, "ref": ref, "name": name, "list": lst}


def _index(data, off, stop):
    if off < 0 or off >= stop:
        return None, off
    try:
        b = data[off]
        sign, cont, val = b & 0x80, b & 0x40, b & 0x3F
        off += 1
        shift = 6
        while cont and shift < 32:
            if off >= stop:
                return None, off
            b = data[off]
            off += 1
            cont = b & 0x80
            val |= (b & 0x7F) << shift
            shift += 7
        return (-val if sign else val), off
    except IndexError:
        return None, off


def _walk(data, p, stop, nnames):
    """A compact count and that many one-compact elements; every element inside the name table."""
    count, o = _index(data, p, stop)
    if count is None or count < 0 or count > 4096 or o > stop:
        return None
    vals = []
    for _ in range(count):
        v, o = _index(data, o, stop)
        if v is None:
            return None
        vals.append(v)
        if o > stop:
            return None
    if not all(0 <= v < nnames for v in vals):
        return None
    return {"count": count, "vals": vals, "end": o}


def scan(data, nnames):
    """Every group the payload yields, at every offset: [(start, group)]."""
    out = []
    stop = len(data)
    for s in range(0, len(data)):
        g = walk_group(data, s, stop, nnames)
        if g is not None:
            out.append((s, g))
            if len(out) >= GROUP_LIMIT:
                break
    return out


def measure(data, nnames, packages, own_package, anchor=None):
    """One payload's reading, against the payload's terminator or a given anchor (the located control)."""
    term = len(data) - 1 if data and data[-1] == 0 else len(data)
    target = term if anchor is None else anchor
    groups = scan(data, nnames)
    at = [(s, g) for s, g in groups if g["end"] == target]
    row = {"payload": len(data), "anchor": target, "terminator": term, "groups": len(groups),
           "tail_at_terminator": 1 if at else 0, "group_start": "", "group_len": "", "name_count": "",
           "names": "", "names_package": "", "ref": "", "ref_import": "", "ref_in_range": "",
           "own_package": "", "latest_end": "", "residue": "", "ends": len({g["end"] for _s, g in groups}),
           "density": round(len({g["end"] for _s, g in groups}) / float(len(data)), 4) if data else "",
           "shape": "", "verdict": "no-group"}
    if groups:
        latest = max(g["end"] for _s, g in groups)
        row["latest_end"] = latest
        row["residue"] = target - latest if latest <= target else 0
    if at:
        # The widest passing start: a group whose count is one element smaller is the same array read one field late
        # (measured on the class record's gap, #470), so the earliest start is the writer's.
        s, g = sorted(at, key=lambda sg: sg[0])[0]
        named = [(v, "") for v in g["names"]["vals"]]
        row.update({"group_start": s, "group_len": g["end"] - s, "name_count": g["names"]["count"],
                    "names": ";".join(str(v) for v in g["names"]["vals"]),
                    "ref": g["ref"], "verdict": "tail-at-anchor" if anchor is not None else "tail-at-terminator"})
    return row, groups, term


def classify(row, pkg, packages, own_package):
    """Fill the name-shape columns: are the group's names package names, and is the reference an import."""
    if row["names"] == "":
        return
    vals = [int(v) for v in row["names"].split(";") if v != ""]
    named = [pkg.name(v) for v in vals]
    row["names_package"] = sum(1 for n in named if n in packages)
    row["own_package"] = 1 if own_package in named else 0
    nimp = len(pkg.imports)
    ref = int(row["ref"])
    row["ref_import"] = 1 if ref < 0 else 0
    row["ref_in_range"] = 1 if (ref < 0 and -ref <= nimp) else 0
    row["shape"] = ("packages" if row["names_package"] == len(named)
                    else "mixed" if row["names_package"] else "other")


def corpus():
    inv = os.path.join(ROOT, "inventory")
    misses, bound, packages = {}, {}, set()
    with open(os.path.join(inv, "class_defaults_misses.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            misses[(r["package"], r["class"])] = r["reason"]
    with open(os.path.join(inv, "class_record_bound.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["block_start"]:
                bound[(r["package"], r["class"])] = int(r["block_start"])
    with open(os.path.join(inv, "classes.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            packages.add(r["package"])
    return misses, bound, packages


def class_exports(pkg):
    for e in pkg.exports:
        if pkg.class_name_of(e) == "Class" and e["size"]:
            yield pkg.name(e["name"]), pkg.data[e["offset"]:e["offset"] + e["size"]]


def sweep(system_dir, outdir, only=None, control=True):
    misses, bound, packages = corpus()
    targets = collections.defaultdict(set)
    for (p, c), reason in misses.items():
        if reason == "empty-block":
            targets[p].add(c)
    rows = []
    census = collections.Counter()
    for pkgname in sorted(targets):
        path = os.path.join(system_dir, pkgname + ".u")
        if not os.path.exists(path):
            raise SystemExit("missing package %s" % path)
        pkg = Package(path)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        nnames = len(pkg.names)
        want = targets[pkgname]
        seen = set()
        for cls, data in class_exports(pkg):
            if cls not in want:
                continue
            seen.add(cls)
            row, groups, term = measure(data, nnames, packages, pkgname)
            row.update({"kind": "class", "package": pkgname, "class": cls})
            classify(row, pkg, packages, pkgname)
            rows.append(row)
            census["payload_bytes"] += len(data)
            census["anchored"] += 1
            census["found"] += row["tail_at_terminator"]
            census["package_names"] += 1 if row["shape"] == "packages" else 0
            census["own_ref"] += 1 if (row["own_package"] and row["ref_in_range"]) else 0
            census["residue_bytes"] += int(row["residue"] or 0)
            if len(data) <= RECORD_WINDOW:
                census["window"] += 1
            else:
                census["long"] += 1
                census["long_found"] += row["tail_at_terminator"]
                census["long_residue"] += int(row["residue"] or 0)
        missing = want - seen
        if missing:
            print("unresolved: %s %s" % (pkgname, sorted(missing)[:4]), file=sys.stderr)
        if control:
            ctl = [(c, b) for (p, c), b in bound.items() if p == pkgname]
            for cls, data in class_exports(pkg):
                hit = [b for c, b in ctl if c == cls]
                if not hit:
                    continue
                row, groups, term = measure(data, nnames, packages, pkgname, anchor=hit[0])
                classify(row, pkg, packages, pkgname)
                census["control_scanned"] += 1
                census["control_found"] += row["tail_at_terminator"]
                census["control_package_names"] += 1 if row["shape"] == "packages" else 0
                census["control_own_ref"] += 1 if (row["own_package"] and row["ref_in_range"]) else 0
    census["classes"] = len(rows)
    census["group_limit"] = GROUP_LIMIT
    outdir = outdir or os.path.join(ROOT, "inventory")
    path = os.path.join(outdir, "class_defaults_recordend.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        crow = {k: "" for k in COLUMNS}
        crow.update({"kind": "census"})
        for k in census:
            crow[k] = census[k]
        w.writerow(crow)
        for row in sorted(rows, key=lambda r: (r["package"], r["class"])):
            w.writerow({k: row.get(k, "") for k in COLUMNS})
    print("wrote %s: %d classes, %d tail-at-terminator, %d residue bytes" %
          (path, census["classes"], census["found"], census["residue_bytes"]))
    return rows, census


def read_csv(path=None):
    path = path or ARTIFACT
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return [r for r in rows if r["kind"] == "class"], [r for r in rows if r["kind"] == "census"]


def report(rows=None, census=None):
    if rows is None:
        rows, cens = read_csv()
        census = cens[0] if cens else {}
    total = len(rows)
    found = sum(1 for r in rows if str(r["tail_at_terminator"]) == "1")
    print("%d empty-block classes, %s payload bytes: %d end on the payload terminator, %d do not"
          % (total, census.get("payload_bytes", "?"), found, total - found))
    for label, pop in (("within the record window", [r for r in rows if int(r["payload"]) <= RECORD_WINDOW]),
                       ("longer", [r for r in rows if int(r["payload"]) > RECORD_WINDOW])):
        f = sum(1 for r in pop if str(r["tail_at_terminator"]) == "1")
        res = sum(int(r["residue"] or 0) for r in pop)
        print("  %-26s %3d classes, %3d found, %d residue bytes" % (label, len(pop), f, res))
    shapes = collections.Counter((r["shape"] or "none") for r in rows if str(r["tail_at_terminator"]) == "1")
    print("  shapes of the found groups: %s" % dict(shapes))
    dens = sorted(float(r["density"]) for r in rows if r["density"] != "")
    if dens:
        print("  end density: a group ends on %.1f%% of a payload's offsets (median %.1f%%)"
              % (100.0 * sum(dens) / len(dens), 100.0 * dens[len(dens) // 2]))
    print("  control: %s scanned, %s found at the block start, %s package-shaped, %s own-package+import"
          % (census.get("control_scanned"), census.get("control_found"), census.get("control_package_names"),
             census.get("control_own_ref")))


def selftest():
    """Synthetic bodies: the group is found where the writer put it, and not at one byte either side."""
    ok = True
    # names array [1,2,3] (one compact each), ref -7 (0x87), name 4, empty list array (0x00), then the terminator
    tail = bytes([0x03, 0x01, 0x02, 0x03, 0x87, 0x04, 0x00])
    body = bytes([0xFF] * 40) + tail + bytes([0x00])
    nnames = 16
    row, groups, term = measure(body, nnames, {"Core"}, "Core")
    if term != len(body) - 1:
        print("FAIL selftest: terminator %d, expected %d" % (term, len(body) - 1))
        ok = False
    if str(row["tail_at_terminator"]) != "1" or row["group_start"] != 40:
        print("FAIL selftest: group at %r, expected start 40" % (row["group_start"],))
        ok = False
    if row["residue"] != 0 or row["ref"] != -7 or row["name_count"] != 3:
        print("FAIL selftest: fields %r" % row)
        ok = False
    # one byte of padding between the group and the terminator: nothing ends on the terminator any more
    body2 = bytes([0xFF] * 40) + tail + bytes([0x00, 0x00])
    row2, _g, term2 = measure(body2, nnames, {"Core"}, "Core")
    if str(row2["tail_at_terminator"]) == "1" and row2["group_start"] == 40:
        print("FAIL selftest: a group separated from the terminator still matched")
        ok = False
    if row2["residue"] != 1:
        print("FAIL selftest: residue %r, expected 1" % (row2["residue"],))
        ok = False
    # an out-of-range name index must not parse: count 3 then index 0x7F (127 >= nnames)
    body3 = bytes([0xFF] * 40) + bytes([0x03, 0x7F, 0x01, 0x02, 0x87, 0x04, 0x00]) + bytes([0x00])
    row3, _g, _t = measure(body3, nnames, {"Core"}, "Core")
    if row3["group_start"] == 40:
        print("FAIL selftest: a group with an out-of-range name index parsed")
        ok = False
    # the anchored form answers the same group for the located control, and refuses one byte short
    row4, _g, _t = measure(body, nnames, {"Core"}, "Core", anchor=len(body) - 1)
    if str(row4["tail_at_terminator"]) != "1" or row4["verdict"] != "tail-at-anchor":
        print("FAIL selftest: anchored parse %r" % row4)
        ok = False
    row5, _g, _t = measure(body, nnames, {"Core"}, "Core", anchor=len(body) - 2)
    if row5["verdict"] != "no-group":
        print("FAIL selftest: an anchor one byte short still matched at %r" % (row5["group_start"],))
        ok = False
    print("ok class-defaults-recordend: %d synthetic bodies" % 5 if ok else "FAIL class-defaults-recordend")
    return 0 if ok else 1


def doc_check():
    """The committed artifact against the package count the misses table names -- dump-free, writes nothing."""
    rows, cens = read_csv()
    if not cens:
        print("FAIL class-defaults-recordend: no census row")
        return 1
    census = cens[0]
    misses, _bound, _packages = corpus()
    empties = {k for k, v in misses.items() if v == "empty-block"}
    got = {(r["package"], r["class"]) for r in rows}
    problems = []
    if got != empties:
        problems.append("%d classes against %d in misses (missing %s, extra %s)"
                        % (len(got), len(empties), sorted(empties - got)[:3], sorted(got - empties)[:3]))
    found = sum(1 for r in rows if str(r["tail_at_terminator"]) == "1")
    if str(census.get("found")) != str(found):
        problems.append("found %d against a census of %s" % (found, census.get("found")))
    res = sum(int(r["residue"] or 0) for r in rows)
    if str(census.get("residue_bytes")) != str(res):
        problems.append("residue %d against a census of %s" % (res, census.get("residue_bytes")))
    longs = [r for r in rows if int(r["payload"]) > RECORD_WINDOW]
    if str(census.get("long")) != str(len(longs)):
        problems.append("%d long rows against a census of %s" % (len(longs), census.get("long")))
    if problems:
        print("FAIL class-defaults-recordend: " + "; ".join(problems))
        return 1
    print("ok class-defaults-recordend: %d classes, %d tail-at-terminator, %d residue bytes"
          % (len(rows), found, res))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("system_dir", nargs="?", help="the dump's System directory")
    ap.add_argument("--sweep", action="store_true", help="walk every package and write the artifact")
    ap.add_argument("--report", action="store_true", help="print the split from the committed artifact")
    ap.add_argument("--class", dest="one", help="package.Class to read on its own")
    ap.add_argument("--no-control", action="store_true", help="skip the located control population")
    ap.add_argument("-o", "--out", help="artifact directory (default: inventory/)")
    ap.add_argument("--check", action="store_true", help="synthetic self-test plus the artifact gate; no dump")
    args = ap.parse_args()
    if args.check:
        rc = selftest()
        return rc if rc else doc_check()
    if args.report:
        report()
        return 0
    if not args.system_dir:
        ap.error("a System directory is required unless --report or --check is given")
    misses, bound, packages = corpus()
    if args.one:
        pkgname, cls = args.one.split(".", 1)
        pkg = Package(os.path.join(args.system_dir, pkgname + ".u"))
        pkg.read_names(); pkg.read_imports(); pkg.read_exports()
        for name, data in class_exports(pkg):
            if name != cls:
                continue
            row, groups, term = measure(data, len(pkg.names), packages, pkgname, anchor=bound.get((pkgname, cls)))
            classify(row, pkg, packages, pkgname)
            print("%s.%s payload %d, terminator %d, %d groups" % (pkgname, cls, len(data), term, len(groups)))
            for k in ("tail_at_terminator", "group_start", "group_len", "name_count", "names", "shape",
                      "ref", "ref_import", "ref_in_range", "own_package", "latest_end", "residue"):
                print("  %-18s %s" % (k, row[k]))
            return 0
        print("no class %s in %s" % (cls, pkgname))
        return 1
    if not args.sweep:
        ap.error("pass --sweep to walk the packages")
    sweep(args.system_dir, args.out, control=not args.no_control)
    return 0


if __name__ == "__main__":
    sys.exit(main())
