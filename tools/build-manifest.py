#!/usr/bin/env python3
"""What a build target actually contains (PLAN 6.4, `docs/REBUILD.md`).

    python3 tools/build-manifest.py WORKDIR/System SOURCE/System -o WORKDIR

Two artifacts, both written into the build's own directory (never the repository):

* `manifest.csv` — one row per package: its size and SHA-256 next to the original's, and whether they are the same
  file. Today the writer assembles every package from its own model, so "identical" is the expected answer and a
  `false` is a real finding about the writer, not a detail of the build.
* `missions.csv` — the campaign missions the build carries, joined from `inventory/campaign_matrix.csv`, so the
  eventual boot matrix (which mission boots in which target) has a column to attach to instead of a list retyped
  from a doc.

The tool exits non-zero when a placed package differs from its source, so a build target cannot report PASS on a
tree it assembled wrongly.
"""
import argparse
import csv
import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(workdir, source, outdir):
    files = sorted(f for f in os.listdir(workdir) if f.lower().endswith(".u"))
    rows, same = [], 0
    for fname in files:
        placed = os.path.join(workdir, fname)
        original = os.path.join(source, fname)
        if not os.path.isfile(original):
            rows.append([fname, os.path.getsize(placed), digest(placed), "", "", 0])
            continue
        a, b = digest(placed), digest(original)
        rows.append([fname, os.path.getsize(placed), a, os.path.getsize(original), b, 1 if a == b else 0])
        same += 1 if a == b else 0
    path = os.path.join(outdir, "manifest.csv")
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["package", "bytes", "sha256", "source_bytes", "source_sha256", "identical"])
        w.writerows(rows)
    print("%d package(s) in the build; %d identical to the source tree" % (len(rows), same))
    print("wrote %s" % path)
    return 0 if same == len(rows) and rows else 1


def missions(outdir, source=os.path.join(ROOT, "inventory", "campaign_matrix.csv")):
    rows = []
    with open(source, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append([r["mission"], r["maps_on_disk"], r["chapters"], r["verdict"]])
    path = os.path.join(outdir, "missions.csv")
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["mission", "maps_on_disk", "chapters", "verdict"])
        w.writerows(rows)
    print("%d campaign mission row(s) carried" % len(rows))
    print("wrote %s" % path)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir", help="the build's assembled packages (System directory)")
    ap.add_argument("source", help="the original System directory, for the identity check")
    ap.add_argument("-o", "--out", default=".", help="where manifest.csv and missions.csv go (the build dir)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    rc = manifest(args.workdir, args.source, args.out)
    missions(args.out)
    return rc


if __name__ == "__main__":
    sys.exit(main())
