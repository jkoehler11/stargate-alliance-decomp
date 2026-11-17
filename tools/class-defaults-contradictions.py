#!/usr/bin/env python3
"""Which property-kind / tag-type pairs the locator refuses, on every start it publishes and withholds.

`inventory/class_defaults_per_class.csv` publishes a `block_start` for 2,181 classes and withholds the verdict on
319 of them (`fully_declared = 0`): 313 of those parse a block in which **every name is a property the class
declares**, so the name score is satisfied and the only thing refusing them is `contradictions()` -- a tag whose
type `TYPE_AGREEMENT` says the declared property kind cannot carry. That refusal is worth a census rather than a
count: if the contradictions cluster on one (kind, type) pair, the map is wrong about that pair the way it was
wrong about `UClassProperty` (734 tags carry `Object`, and assuming `Class` alone demoted 419 clean blocks); if
they scatter, the blocks are reads of something that is not a tag stream.

    python3 tools/class-defaults-contradictions.py DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15 --sweep -o inventory
    python3 tools/class-defaults-contradictions.py --report
    python3 tools/class-defaults-contradictions.py DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15 --class SGOrder

For every published start the tool reads the tag stream once, joins each tag's name to the class's declared kind
(`inventory/class_properties.csv`, closed over the ancestors) and counts the tags `TYPE_AGREEMENT` refuses, by
`(kind, type)` and by the value's size class. The census row carries the population, and `recovered` carries the
classes that a single pair is all that stands between and a clean block -- the number a corrected map would move.

`--check` is dump-free: a synthetic body drives the join and the aggregation, then the committed artifact is
reconciled with its own census row (the pair rows must sum to the census's tag total).
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
ARTIFACT = os.path.join(ROOT, "inventory", "class_defaults_contradictions.csv")
COLUMNS = ["kind", "class_kind", "tag_type", "tags", "classes", "sizes", "single_pair", "example",
           "classes_withheld", "classes_with_start", "tags_read", "declared_tags", "contradictions", "recovered",
           "blocks_one_tag", "blocks_two_plus"]


def load(name):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    spec = importlib.util.spec_from_file_location(name.replace("-", "_").replace(".py", ""), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def withheld_starts(path=os.path.join(ROOT, "inventory", "class_defaults_per_class.csv")):
    """(package, class, start) for every class the locator publishes a start for and withholds the verdict on."""
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["fully_declared"] == "0" and r["defaults"] != "0" and r["block_start"]:
                out.append((r["package"], r["class"], int(r["block_start"])))
    return out


def contradictions_of(props, kinds, agreement):
    """([(name, kind, type, size)], [declared tags]) for one parsed block."""
    bad, declared = [], 0
    for p in props:
        kind = kinds.get(p["name"])
        if kind is None:
            continue
        declared += 1
        allowed = agreement.get(kind)
        if allowed is not None and p["type"] not in allowed:
            bad.append((p["name"], kind, p["type"], p.get("size", "")))
    return bad, declared


def sweep(dump, outdir, show=False, only=None):
    if not os.path.isdir(os.path.join(dump, "System")):
        print("FAIL class-defaults-contradictions: %s holds no System directory (pass the game directory, not its "
              "System/ child)" % dump)
        return 1
    cdef = load("class-defaults.py")
    mod = cdef.load_prop_stream()
    supers, declared_all, kinds_all = cdef.super_map(), cdef.declared_properties(), cdef.declared_kinds()
    starts = withheld_starts()
    by_pkg = collections.defaultdict(list)
    for pkgname, cls, start in starts:
        if only and cls != only:
            continue
        by_pkg[pkgname].append((cls, start))
    pairs = collections.defaultdict(lambda: {"tags": 0, "classes": set(), "sizes": collections.Counter(),
                                             "example": "", "single_pair": 0})
    tally = collections.Counter()
    for pkgname in sorted(by_pkg):
        pkg = Package(os.path.join(dump, "System", pkgname + ".u"))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        for cls, start in sorted(by_pkg[pkgname]):
            exp = next((e for e in pkg.exports
                        if pkg.class_name_of(e) == "Class" and pkg.name(e["name"]) == cls), None)
            if exp is None:
                tally["no-class-export"] += 1
                continue
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            tally["classes_with_start"] += 1
            kinds = cdef.kinds_for(cls, kinds_all, supers)
            try:
                props, _end, reason = mod.read_block(data[start:], pkg, False)
            except Exception as exc:
                tally["unreadable"] += 1
                if show:
                    print("error %s.%s: %s" % (pkgname, cls, exc))
                continue
            if reason != "None":
                tally["block-" + reason] += 1
                continue
            bad, declared = contradictions_of(props, kinds, cdef.TYPE_AGREEMENT)
            tally["tags_read"] += len(props)
            tally["declared_tags"] += declared
            tally["contradictions"] += len(bad)
            tally["blocks_one_tag" if len(props) == 1 else "blocks_two_plus"] += 1
            if not bad:
                tally["no-contradiction"] += 1
                continue
            seen = {(k, t) for _n, k, t, _s in bad}
            for name, kind, typ, size in bad:
                rec = pairs[(kind, typ)]
                rec["tags"] += 1
                rec["classes"].add((pkgname, cls))
                rec["sizes"][size] += 1
                if not rec["example"]:
                    rec["example"] = "%s.%s" % (cls, name)
            if len(seen) == 1:
                # one pair is all that stands between this class and a clean block -- but only if the block is
                # long enough for the floor, or allowing the pair would leave a one-tag parse withheld anyway
                kind, typ = next(iter(seen))
                pairs[(kind, typ)]["single_pair"] += 1
                if len(props) >= cdef.MIN_PROPERTIES:
                    tally["recovered"] += 1
            if show:
                print("%s.%s start %d: %d tags, %d declared, %d contradicting %s"
                      % (pkgname, cls, start, len(props), declared, len(bad), sorted(seen)))
    rows = []
    for (kind, typ), rec in sorted(pairs.items(), key=lambda kv: (-len(kv[1]["classes"]), kv[0])):
        rows.append({"kind": "pair", "class_kind": kind, "tag_type": typ, "tags": rec["tags"],
                     "classes": len(rec["classes"]),
                     "sizes": ";".join("%s %d" % kv for kv in sorted(rec["sizes"].items())),
                     "single_pair": rec["single_pair"], "example": rec["example"]})
    census = {"kind": "census", "classes_withheld": len(starts), "classes_with_start": tally["classes_with_start"],
              "tags_read": tally["tags_read"], "declared_tags": tally["declared_tags"],
              "contradictions": tally["contradictions"], "recovered": tally["recovered"],
              "blocks_one_tag": tally["blocks_one_tag"], "blocks_two_plus": tally["blocks_two_plus"]}
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "class_defaults_contradictions.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerow(census)
        for r in rows:
            w.writerow(r)
    print("%d withheld starts read, %d tags (%d declared; %d start(s) hold one tag, %d hold two or more); "
          "%d contradictions over %d classes; %d classes are one map pair away from clean"
          % (tally["classes_with_start"], tally["tags_read"], tally["declared_tags"], tally["blocks_one_tag"],
             tally["blocks_two_plus"],
             tally["contradictions"], len({c for rec in pairs.values() for c in rec["classes"]}),
             tally["recovered"]))
    for r in rows[:8]:
        print("  %-16s %-8s %4d tags on %4d classes (%d alone) e.g. %s"
              % (r["class_kind"], r["tag_type"], r["tags"], r["classes"], r["single_pair"], r["example"]))
    print("wrote %s" % path)
    return 0


def read_rows(path=ARTIFACT):
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    census = [r for r in rows if r["kind"] == "census"]
    return [r for r in rows if r["kind"] != "census"], (census[0] if census else None)


def report(path=ARTIFACT):
    rows, census = read_rows(path)
    if census is None:
        print("FAIL class-defaults-contradictions: %s has no census row" % path)
        return 1
    tags = sum(int(r["tags"]) for r in rows)
    print("%s withheld starts, %s read; %s tags, %s declared; %s one-tag / %s longer blocks; %s contradictions "
          "over %s classes; %s classes one map pair from clean"
          % (census["classes_withheld"], census["classes_with_start"], census["tags_read"],
             census["declared_tags"], census["blocks_one_tag"], census["blocks_two_plus"],
             census["contradictions"], len(rows), census["recovered"]))
    for r in rows:
        print("  %-16s %-8s %4s tags on %4s classes (%s alone) sizes %s  e.g. %s"
              % (r["class_kind"], r["tag_type"], r["tags"], r["classes"], r["single_pair"], r["sizes"],
                 r["example"]))
    return 0


def self_check():
    """A synthetic block whose kinds and types the agreement map does and does not allow."""
    cdef = load("class-defaults.py")
    kinds = {"Speed": "UFloatProperty", "bHidden": "UBoolProperty", "Tag": "UNameProperty",
             "Owner": "UObjectProperty"}
    props = [{"name": "Speed", "type": "Float", "size": 4},
             {"name": "bHidden", "type": "Byte", "size": 1},     # a bool stored as a byte: refused
             {"name": "Tag", "type": "Name", "size": 8},
             {"name": "Owner", "type": "Object", "size": 4},
             {"name": "Elsewhere", "type": "Int", "size": 4}]     # not declared: not counted at all
    bad, declared = contradictions_of(props, kinds, cdef.TYPE_AGREEMENT)
    assert [b[0] for b in bad] == ["bHidden"], bad
    assert bad[0][1:] == ("UBoolProperty", "Byte", 1), bad
    assert declared == 4, declared
    # a delegate and a pointer are unconstrained, so their tags never contradict
    assert cdef.TYPE_AGREEMENT.get("UDelegateProperty") is None
    assert cdef.TYPE_AGREEMENT.get("UPointerProperty") is None
    # a class property may carry Object, and a struct may carry Vector -- the two measured exceptions
    assert "Object" in cdef.TYPE_AGREEMENT["UClassProperty"]
    assert {"Struct", "Vector", "Rotator"} <= cdef.TYPE_AGREEMENT["UStructProperty"]
    print("ok   class-defaults-contradictions: synthetic join, the refused pair, and the map's two exceptions")
    return 0


def check(path=ARTIFACT):
    rc = self_check()
    rows, census = read_rows(path)
    if census is None:
        print("FAIL class-defaults-contradictions: no census row in %s" % path)
        return 1
    problems = []
    tags = sum(int(r["tags"]) for r in rows)
    if tags != int(census["contradictions"]):
        problems.append("%d tag rows against a census of %s contradictions" % (tags, census["contradictions"]))
    if sum(int(r["single_pair"]) for r in rows) != int(census["recovered"]):
        problems.append("single-pair rows sum to %d against a census of %s recovered"
                        % (sum(int(r["single_pair"]) for r in rows), census["recovered"]))
    cdef = load("class-defaults.py")
    for r in rows:
        allowed = cdef.TYPE_AGREEMENT.get(r["class_kind"])
        if allowed is None or r["tag_type"] in allowed:
            problems.append("%s/%s is not a pair the map refuses" % (r["class_kind"], r["tag_type"]))
        if int(r["classes"]) > int(census["classes_with_start"]):
            problems.append("%s/%s names more classes than the population" % (r["class_kind"], r["tag_type"]))
    if problems:
        print("FAIL class-defaults-contradictions: " + "; ".join(problems[:6]))
        return 1
    print("ok   class-defaults-contradictions: %d refused pairs over %d tags, %s contradictions, %s recovered "
          "by one pair" % (len(rows), tags, census["contradictions"], census["recovered"]))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("system", nargs="?", help="the game directory holding System/*.u")
    ap.add_argument("--class", dest="cls", help="one class name")
    ap.add_argument("--sweep", action="store_true", help="read every published start on a withheld class")
    ap.add_argument("-o", "--out", help="artifact directory")
    ap.add_argument("--report", action="store_true", help="summarise the committed artifact")
    ap.add_argument("--check", action="store_true", help="dump-free self-test and artifact reconciliation")
    ap.add_argument("--show", action="store_true", help="print one line per class while sweeping")
    args = ap.parse_args()
    if args.check:
        return check()
    if args.report:
        return report()
    if not args.system:
        ap.error("a System directory, or --report/--check")
    if not args.sweep or not args.out:
        ap.error("--sweep needs -o")
    return sweep(args.system, args.out, show=args.show, only=args.cls)


if __name__ == "__main__":
    sys.exit(main())
