#!/usr/bin/env python3
"""Roll `inventory/sgs_schema.csv` up into a per-class picture of what a checkpoint persists.

The schema file is one row per (class, property) across the nine shipped `.sgs` checkpoints, with the
number of files that contain the property and the number of instances across all files. This turns that
into the questions PLAN 8.3 asks of the checkpoint system: which classes are persisted at all, how much
of each, and which properties every persisted object carries regardless of class.

    python3 tools/checkpoint-report.py                 # tables to stdout
    python3 tools/checkpoint-report.py -o inventory     # + inventory/checkpoint_summary.csv

Reads only the committed CSV, so it runs without the dump and can gate a PR.
"""
import argparse
import collections
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", default=os.path.join(ROOT, "inventory", "sgs_schema.csv"))
    ap.add_argument("-o", "--out")
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()

    with open(args.schema, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("empty schema: %s" % args.schema)
    for r in rows:
        for k in ("size", "array_index", "files", "instances"):
            r[k] = int(r[k])

    files_max = max(r["files"] for r in rows)
    classes = collections.defaultdict(lambda: {"properties": 0, "instances": 0, "types": set(),
                                               "universal": 0, "arrays": 0})
    for r in rows:
        c = classes[r["class"]]
        c["properties"] += 1
        c["instances"] += r["instances"]
        c["types"].add(r["type"])
        c["universal"] += 1 if r["files"] == files_max else 0
        c["arrays"] += 1 if r["array_index"] >= 0 else 0

    print("checkpoint schema: %d property rows over %d classes, %d checkpoint files"
          % (len(rows), len(classes), files_max))
    print("\ntypes")
    for t, n in collections.Counter(r["type"] for r in rows).most_common():
        print("  %-18s %5d  %5.1f%%" % (t, n, 100.0 * n / len(rows)))
    print("\nhow many of the %d files carry a property" % files_max)
    for n, c in sorted(collections.Counter(r["files"] for r in rows).items(), reverse=True):
        print("  %d files  %5d rows" % (n, c))
    print("\nclasses by instances persisted")
    for name, c in sorted(classes.items(), key=lambda kv: -kv[1]["instances"])[: args.top]:
        print("  %-26s %6d instances  %3d props  %d universal" %
              (name, c["instances"], c["properties"], c["universal"]))
    print("\nclasses by property count")
    for name, c in sorted(classes.items(), key=lambda kv: -kv[1]["properties"])[: args.top]:
        print("  %-26s %3d props  %6d instances" % (name, c["properties"], c["instances"]))
    universal = [r for r in rows if r["files"] == files_max]
    print("\nproperties in every file: %d rows, %d distinct names"
          % (len(universal), len({r["property"] for r in universal})))
    for name, n in collections.Counter(r["property"] for r in universal).most_common(10):
        print("  %-18s on %d classes" % (name, n))
    arrays = [r for r in rows if r["array_index"] >= 0]
    print("\narray elements: %d rows (%d distinct properties)"
          % (len(arrays), len({r["property"] for r in arrays})))

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "checkpoint_summary.csv")
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["class", "properties", "instances", "universal_props", "array_props",
                        "types", "files_max"])
            for name in sorted(classes):
                c = classes[name]
                w.writerow([name, c["properties"], c["instances"], c["universal"], c["arrays"],
                            "|".join(sorted(c["types"])), files_max])
        print("\nwrote %s (%d rows)" % (path, len(classes)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
