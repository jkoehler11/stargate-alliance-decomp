#!/usr/bin/env python3
"""Regenerate the map-side census artifacts: property-block coverage and squad-script references.

The three artifacts `docs/ARTIFACTS.md` still lists as orphaned. All three come from the same pass over the
`.unr` files, so they belong in one tool:

    python3 tools/map-census.py DUMP/Maps -o inventory
    python3 tools/map-census.py DUMP/Maps --check

* `map_property_coverage.csv` — per map: export count, and how many exports carry a property block.
* `actor_property_coverage.csv` — the same counts aggregated by class across every map, with the percentage.
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


def load(name, filename):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def blocks_in(pkg, mod, exp):
    """The export's property block, or None: located by extent, not by an assumed offset."""
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
        return props
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
        return checkcsv.report("map-census", problems,
                               "map_property_coverage.csv and actor_property_coverage.csv are the shapes this tool \n"
                               "     writes (shape only: needs the dump)")

    cd = load("class_defaults", "class-defaults.py")
    ps = load("prop_stream", "prop-stream.py")
    outdir = args.out or os.path.join(ROOT, "inventory")

    map_rows, class_totals, class_blocks, squad_rows = [], collections.Counter(), collections.Counter(), []
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
            props = blocks_in(pkg, ps, exp)
            if props is None:
                continue
            with_block += 1
            class_blocks[cls] += 1
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

    # `squad_scripts.csv` is deliberately *not* written: this pass finds 581 (map, script) pairs where the
    # committed artifact has 614, so the enumeration that produced it was broader than "a located property block
    # in a map export". Overwriting would silently drop 35 references that other docs count, so the delta is
    # reported instead and reconciling it is its own task.
    print("squad_scripts.csv: %d pairs found, committed has %d -- not written (see docs/T3D.md)"
          % (len(squad_rows), len(open(os.path.join(outdir, "squad_scripts.csv"), newline="").readlines()) - 1))

    files = [
        ("map_property_coverage.csv", ["map", "exports", "blocks_with_properties"], map_rows),
        ("actor_property_coverage.csv", ["class", "exports", "blocks_with_properties", "coverage"], actor_rows),
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
