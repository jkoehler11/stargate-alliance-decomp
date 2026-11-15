#!/usr/bin/env python3
"""Regenerate the map-side census artifacts: property-block coverage and squad-script references.

The three artifacts `docs/ARTIFACTS.md` still lists as orphaned. All three come from the same pass over the
`.unr` files, so they belong in one tool:

    python3 tools/map-census.py DUMP/Maps -o inventory
    python3 tools/map-census.py DUMP/Maps --check

* `map_property_coverage.csv` — per map: export count, and how many exports carry a property block.
* `actor_property_coverage.csv` — the same counts aggregated by class across every map, with the percentage.
* `actor_prefix_census.csv` — per class: the distribution of the offset the property block starts at (the native
  instance data in front of it), so "the prefix is 12 bytes" can be checked against every instance of the class
  rather than one, and the `flags_split` column says whether the export flags separate the offsets.
* `squad_scripts.csv` — per map, every `SuggestedSquadScript` value and how many spawners name it.

"Carries a property block" means the locator in `tools/class-defaults.py` finds one: a start offset in the first
64 bytes whose stream terminates exactly at the export's end and whose names all resolve. That is deliberately the
strict reading — it counts what can be read, not what might be there.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys as _sys

_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import checkcsv  # noqa: E402
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PREFIX_COLUMNS = ["class", "exports", "blocks", "prefixes", "dominant_prefix", "dominant_count", "flags_values",
                  "verdict", "flags_split"]


def prefix_problems(outdir):
    """The claims `actor_prefix_census.csv` makes that can be re-derived without the dump.

    `verdict` is a claim about the same row's histogram, the dominant count is a claim about `blocks`, and the block
    total is a claim about the *other* artifact this one walk writes -- so a hand edit, a truncated run or a stale
    copy fails here instead of in the prose that quotes it.
    """
    path = os.path.join(outdir, "actor_prefix_census.csv")
    problems = checkcsv.shape(path, PREFIX_COLUMNS, key="class")
    if problems:
        return problems
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    total = 0
    for r in rows:
        try:
            blocks, prefixes = int(r["blocks"]), int(r["prefixes"])
            dom_n, flags = int(r["dominant_count"]), int(r["flags_values"])
        except ValueError:
            problems.append("%s: a count is not a number" % r["class"])
            continue
        total += blocks
        if prefixes == 0:
            if r["verdict"] != "no-block" or blocks:
                problems.append("%s: no prefix, verdict %s, %d block(s)" % (r["class"], r["verdict"], blocks))
            continue
        if r["verdict"] == "no-block":
            problems.append("%s: verdict no-block with %d prefix(es)" % (r["class"], prefixes))
        if (r["verdict"] == "constant") != (prefixes == 1):
            problems.append("%s: verdict %s with %d prefix(es)" % (r["class"], r["verdict"], prefixes))
        if r["verdict"] == "varies" and dom_n >= blocks:
            problems.append("%s: varies but one prefix covers every block" % r["class"])
        if (r["flags_split"] == "single-flag") != (flags == 1):
            problems.append("%s: flags_split %s with %d flags value(s)" % (r["class"], r["flags_split"], flags))
        if dom_n > blocks or (prefixes == 1 and dom_n != blocks):
            problems.append("%s: dominant %d of %d block(s)" % (r["class"], dom_n, blocks))
    cover = os.path.join(outdir, "actor_property_coverage.csv")
    if os.path.isfile(cover):
        with open(cover, newline="", encoding="utf-8") as fh:
            there = sum(int(r["blocks_with_properties"]) for r in csv.DictReader(fh))
        if there != total:
            problems.append("blocks total %d against actor_property_coverage's %d" % (total, there))
    return problems



def load(name, filename):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def blocks_in(pkg, mod, exp):
    """The export's property block, or None: located by extent, not by an assumed offset.

    Returns `(prefix, props)`: `prefix` is the byte offset the block starts at, which is the export's native
    instance data in front of the properties -- the quantity `actor_prefix_census.csv` counts per class.
    """
    data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    for start in range(0, min(len(data), 64)):
        try:
            props, end, reason = mod.read_block(data[start:], pkg, True)
        except Exception:
            continue
        if reason != "None" or len(props) < 2:
            continue
        if any(p["name"] == "?" for p in props):
            continue
        if start + end != len(data):
            continue
        return start, props
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("maps_dir", nargs="?", help="the dump's Maps directory (not needed for --check)")
    ap.add_argument("-o", "--out", help="directory holding the CSVs (default: inventory/)")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    outdir = args.out or os.path.join(ROOT, "inventory")
    if args.check and not args.maps_dir:
        # Dump-free: the numbers cannot be recomputed here, so check that the committed artifacts are still the shape
        # this tool writes -- a truncated run or a stub would pass a rebuild that never ran.
        problems = []
        for name, header, *_rest in (("map_property_coverage.csv", ["map", "exports", "blocks_with_properties"],),
                                     ("actor_property_coverage.csv",
                                      ["class", "exports", "blocks_with_properties", "coverage"],)):
            problems += checkcsv.shape(os.path.join(outdir, name), header, key=header[0])
        problems += prefix_problems(outdir)
        return checkcsv.report("map-census", problems,
                               "map_property_coverage.csv, actor_property_coverage.csv and actor_prefix_census.csv "
                               "are the shapes this tool \n"
                               "     writes, and the prefix census agrees with its own histograms (shape only: needs "
                               "the dump)")

    cd = load("class_defaults", "class-defaults.py")
    ps = load("prop_stream", "prop-stream.py")
    outdir = args.out or os.path.join(ROOT, "inventory")

    map_rows, class_totals, class_blocks, squad_rows = [], collections.Counter(), collections.Counter(), []
    class_prefix = collections.defaultdict(collections.Counter)      # class -> {prefix: exports}
    class_flags = collections.defaultdict(lambda: collections.defaultdict(set))  # class -> {prefix: {flags}}
    first_seen = []  # ties keep first-seen order, which is how the committed artifact reads
    for fname in sorted(f for f in os.listdir(args.maps_dir) if f.lower().endswith(".unr")):
        pkg = Package(os.path.join(args.maps_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        map_name = fname[:-4]
        with_block = 0
        squads = collections.Counter()
        for exp in pkg.exports:
            cls = pkg.class_name_of(exp)
            if cls not in class_totals:
                first_seen.append(cls)
            class_totals[cls] += 1
            found = blocks_in(pkg, ps, exp)
            if found is None:
                continue
            start, props = found
            with_block += 1
            class_blocks[cls] += 1
            class_prefix[cls][start] += 1
            class_flags[cls][start].add(exp["flags"])
            squad = next((p for p in props if p["name"] == "SuggestedSquadScript"), None)
            if squad is not None:
                squads[str(squad.get("value") or "").strip()] += 1
        map_rows.append([map_name, len(pkg.exports), with_block])
        for script, n in sorted(squads.items()):
            if script and script != "None":
                squad_rows.append([map_name, script, n])
        print("%-22s %5d exports %5d blocks %4d squad names" % (map_name, len(pkg.exports), with_block, len(squads)))

    actor_rows = []
    order = {cls: i for i, cls in enumerate(first_seen)}
    for cls, total in sorted(class_totals.items(), key=lambda kv: (-kv[1], order[kv[0]])):
        got = class_blocks.get(cls, 0)
        actor_rows.append([cls, total, got, "%.1f%%" % (100.0 * got / total if total else 0.0)])

    # `actor_prefix_census.csv`: is the prefix in front of a class's property block the same on every instance?
    # The question is PLAN-independent -- a T3D writer needs to know whether the offset is a per-class constant
    # or per-object -- and `docs/ACTOR_PROPERTIES.md` listed it as unmeasured. The flags split is the falsifiable
    # half: if every export-flags value occurs at exactly one prefix, the prefix tracks the flags; if a flag value
    # occurs at two prefixes, it does not.
    prefix_rows = []
    for cls, total in sorted(class_totals.items(), key=lambda kv: (-kv[1], order[kv[0]])):
        blocks = class_blocks.get(cls, 0)
        hist = class_prefix.get(cls, {})
        if not hist:
            prefix_rows.append([cls, total, 0, 0, "", 0, 0, "no-block", ""])
            continue
        dom, dom_n = sorted(hist.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        flag_to_prefix = collections.defaultdict(set)
        for start, flags in class_flags[cls].items():
            for f in flags:
                flag_to_prefix[f].add(start)
        if len(flag_to_prefix) <= 1:
            split = "single-flag"
        elif all(len(v) == 1 for v in flag_to_prefix.values()):
            split = "separate"
        else:
            split = "shared"
        prefix_rows.append([cls, total, blocks, len(hist), dom, dom_n, len(flag_to_prefix),
                            "constant" if len(hist) == 1 else "varies", split])

    # `squad_scripts.csv` is deliberately *not* written: this pass finds 581 (map, script) pairs where the
    # committed artifact has 614, so the enumeration that produced it was broader than "a located property block
    # in a map export". Overwriting would silently drop 35 references that other docs count, so the delta is
    # reported instead and reconciling it is its own task.
    print("squad_scripts.csv: %d pairs found, committed has %d -- not written (see docs/T3D.md)"
          % (len(squad_rows), len(open(os.path.join(outdir, "squad_scripts.csv"), newline="").readlines()) - 1))

    files = [
        ("map_property_coverage.csv", ["map", "exports", "blocks_with_properties"], map_rows),
        ("actor_property_coverage.csv", ["class", "exports", "blocks_with_properties", "coverage"], actor_rows),
        ("actor_prefix_census.csv", PREFIX_COLUMNS, prefix_rows),
    ]
    stale = []
    for name, header, rows in files:
        path = os.path.join(outdir, name)
        if args.check:
            buf = []
            buf.append(",".join(header))
            for r in rows:
                buf.append(",".join(str(c) for c in r))
            want = "\r\n".join(buf) + "\r\n"
            try:
                have = open(path, encoding="utf-8", newline="").read()
            except FileNotFoundError:
                have = None
            same = have == want
            if not same:
                stale.append(name)
            print("%-4s %s (%d rows)" % ("ok" if same else "DIFF", name, len(rows)))
        else:
            with open(path, "w", newline="") as fh:
                w = csv.writer(fh, lineterminator="\r\n")
                w.writerow(header)
                w.writerows(rows)
            print("wrote %s (%d rows)" % (path, len(rows)))
    if args.check and stale:
        print("stale: %s" % ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
