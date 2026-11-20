#!/usr/bin/env python3
"""Karma ragdoll census: the `.ka` assets and what each one defines (PLAN 5.5)

The four Karma files under `Resources/` are **XML**, not binary: a `.ka` is `<KARMA ka_file_version="1.0">`
holding `<ASSET>` elements (one ragdoll per character, pointing at a `.PSK`), each with `<GEOMETRY>` per bone,
`<PRIMITIVE>` shapes, `<MODEL>/<DYNAMICS>` body properties and `<JOINT>` constraints; a `.kaw` is a
`<KAT_WORKSPACE>` listing the `.ka` files with their original authoring paths.

This tool counts what each file defines so the format page can quote numbers instead of adjectives. It reads
only the dump, and the artifact holds counts and identifiers — never file content, per `docs/LEGAL.md`.

    python3 tools/ragdoll-census.py --dump ~/sg1/work/dump-local -o inventory/ragdoll_assets.csv
"""

import argparse
import csv
import os
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLUMNS = ["file", "asset", "graphic", "scale", "geometries", "primitives", "joints", "bodies"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default=os.environ.get("SG1_DUMP") or os.path.expanduser("~/sg1/work/dump-local"))
    ap.add_argument("-o", "--out", default=os.path.join("inventory", "ragdoll_assets.csv"))
    ap.add_argument("--check", action="store_true", help="write nothing; fail if the artifact is stale")
    args = ap.parse_args()

    res = os.path.join(os.path.expanduser(args.dump), "Resources")
    files = sorted(f for f in os.listdir(res) if f.endswith(".ka")) if os.path.isdir(res) else []
    if not files:
        print("ragdoll-census: --dump required (no Resources/*.ka under %s)" % res)
        return 2

    rows = []
    for name in files:
        root = ET.parse(os.path.join(res, name)).getroot()
        for asset in root.iter("ASSET"):
            def count(tag):
                return sum(1 for _ in asset.iter(tag))
            rows.append({
                "file": name,
                "asset": asset.get("id", ""),
                "graphic": asset.get("graphic", ""),
                "scale": asset.get("scale", ""),
                "geometries": count("GEOMETRY"),
                "primitives": count("PRIMITIVE"),
                "joints": count("JOINT"),
                "bodies": count("DYNAMICS"),
            })

    wanted = [{k: str(r[k]) for k in COLUMNS} for r in rows]
    if args.check:
        have = []
        if os.path.exists(args.out):
            have = [{k: r[k] for k in COLUMNS} for r in csv.DictReader(open(args.out, encoding="utf-8"))]
        if have != wanted:
            raise SystemExit("ragdoll-census: artifact is stale (%d rows on disk, %d measured)" % (len(have), len(wanted)))
        print("ok   ragdoll-census: %d assets over %d files" % (len(rows), len(files)))
        return 0

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print("wrote %s: %d assets over %d files, %d geometries, %d joints"
          % (args.out, len(rows), len(files), sum(r["geometries"] for r in rows), sum(r["joints"] for r in rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
