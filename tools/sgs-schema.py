#!/usr/bin/env python3
"""Aggregate per-file `Sg1Tool save` output into one checkpoint schema table.

`Sg1Tool save --path X.sgs --out X.csv` writes one row per property tag it finds in the package:
file,class,object,property,type,size,array_index. One file is one checkpoint, so the files differ in
which actors they contain; the schema is what they have in common. This reduces the 18k rows across
nine checkpoints to the distinct tags with a file count and an instance count, which is the form a
reader of the format wants: "class X serialises property P as type T, width S".

Usage: sgs-schema.py PER-FILE-CSV-DIR [-o inventory/sgs_schema.csv]
"""
import argparse
import collections
import csv
import glob
import os
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_dir")
    ap.add_argument("-o", "--out", default="inventory/sgs_schema.csv")
    args = ap.parse_args()
    paths = sorted(glob.glob(os.path.join(args.csv_dir, "*.csv")))
    if not paths:
        sys.exit(f"no per-file CSVs under {args.csv_dir}")

    tags = collections.defaultdict(lambda: {"files": set(), "instances": 0})
    per_file = []
    for path in paths:
        name = os.path.basename(path)
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        per_file.append((name, len({r["object"] for r in rows}), len(rows)))
        for r in rows:
            key = (r["class"], r["property"], r["type"], int(r["size"]), int(r["array_index"]))
            tags[key]["files"].add(name)
            tags[key]["instances"] += 1

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["class", "property", "type", "size", "array_index", "files", "instances"])
        for (cls, prop, typ, size, index), d in sorted(tags.items()):
            w.writerow([cls, prop, typ, size, index, len(d["files"]), d["instances"]])

    print(f"{len(paths)} checkpoints, {sum(p[2] for p in per_file):,} property tags, "
          f"{len(tags):,} distinct tags -> {args.out}")
    for name, objects, count in per_file:
        print(f"  {name:44s} objects={objects:4d} tags={count:5d}")
    shared = sum(1 for d in tags.values() if len(d["files"]) == len(paths))
    print(f"  {shared:,} tags appear in all {len(paths)} checkpoints")
    return 0


if __name__ == "__main__":
    sys.exit(main())
