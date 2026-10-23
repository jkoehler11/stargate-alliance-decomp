#!/usr/bin/env python3
"""Structural statistics over a dump: extension and directory tables.

Usage: file-stats.py DUMP_ROOT -o inventory

Emits inventory/extensions.csv and inventory/directories.csv. These are counts, sizes and
distributions only -- no filenames of game content beyond directories that already appear in
inventory/manifest.sha256 -- so the output is publishable and the totals double as a
cheap "did the dump change?" check independent of hashing.
"""
import argparse
import csv
import os
import sys
from collections import defaultdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump_root")
    ap.add_argument("-o", "--out", default="inventory")
    args = ap.parse_args()
    if not os.path.isdir(args.dump_root):
        sys.exit(f"not a directory: {args.dump_root}")

    ext = defaultdict(lambda: [0, 0])
    dirs = defaultdict(lambda: [0, 0])
    total = [0, 0]
    for dirpath, dirnames, filenames in os.walk(args.dump_root):
        dirnames.sort()
        rel = os.path.relpath(dirpath, args.dump_root).replace(os.sep, "/")
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            size = os.path.getsize(full)
            e = os.path.splitext(name)[1].lower().lstrip(".") or "(none)"
            ext[e][0] += 1
            ext[e][1] += size
            top = "/".join(rel.split("/")[:2]) if rel != "." else "(root)"
            dirs[top][0] += 1
            dirs[top][1] += size
            total[0] += 1
            total[1] += size

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "extensions.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["extension", "files", "bytes"])
        for e, (n, b) in sorted(ext.items(), key=lambda kv: (-kv[1][0], kv[0])):
            w.writerow([e, n, b])
    with open(os.path.join(args.out, "directories.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["path", "files", "bytes"])
        for d, (n, b) in sorted(dirs.items()):
            w.writerow([d, n, b])
    print(f"{total[0]} files, {total[1]} bytes ({total[1] / 2**30:.2f} GiB), "
          f"{len(ext)} extensions, {len(dirs)} directories")


if __name__ == "__main__":
    main()
