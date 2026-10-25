#!/usr/bin/env python3
"""Count what a map package actually contains, from its export table (PLAN 5.2).

A `.unr` is a package, and its level actors are exports. That means the question "which actor classes does
this mission place" does not need a T3D dump or a property reader: it is a histogram over the export table,
with each export's class resolved through the import/export indices.

    python3 tools/map-actors.py DUMP/Maps/CTF-Abydos.unr
    python3 tools/map-actors.py DUMP/Maps -o inventory        # every map: inventory/map_export_classes.csv

Cross-checks against `inventory/map_actors.csv`, which came from UELib: the two are independent readings of
the same maps, so a disagreement is a finding rather than noise.
"""
import argparse
import collections
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402


def histogram(path):
    pkg = Package(path)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    hist = collections.Counter(pkg.class_name_of(e) for e in pkg.exports)
    return pkg, hist


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="a .unr file, or a directory of them")
    ap.add_argument("-o", "--out")
    ap.add_argument("--top", type=int, default=8)
    args = ap.parse_args()

    paths = ([os.path.join(args.path, f) for f in sorted(os.listdir(args.path)) if f.lower().endswith(".unr")]
             if os.path.isdir(args.path) else [args.path])
    rows, totals = [], collections.Counter()
    failed = []
    for path in paths:
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            pkg, hist = histogram(path)
        except Exception as exc:  # a bad map must not abort the sweep
            failed.append((name, str(exc)))
            continue
        actors = sum(v for k, v in hist.items() if k not in ("Model", "Polys", "Level", "Package"))
        print("%-22s exports=%-5d actor-ish=%-5d top=%s"
              % (name, pkg.export_count, actors, hist.most_common(3)))
        for cls, n in hist.items():
            rows.append({"map": name, "class": cls, "count": n})
            totals[cls] += n
    print("\nmaps parsed: %d of %d" % (len(paths) - len(failed), len(paths)))
    if failed:
        print("failed: %s" % failed[:5])
    print("distinct export classes across all maps: %d" % len(totals))
    print("top classes overall: %s" % totals.most_common(args.top))
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "map_export_classes.csv")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["map", "class", "count"])
            w.writeheader()
            w.writerows(rows)
        print("wrote %s (%d rows)" % (path, len(rows)))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
