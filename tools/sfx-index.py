#!/usr/bin/env python3
"""Index the sound effects inside `.uax` packages (PLAN 5.3's missing half).

Speech is loose WAVs and already indexed; music likewise. The effects are inside `.uax` packages, which is why
`docs/MUSIC.md` recorded them as unindexed. With the package reader they are countable: a `.uax` holds one
`Sound` export per effect, so the index is a census, not an extraction.

    python3 tools/sfx-index.py DUMP/Sounds            # every package
    python3 tools/sfx-index.py DUMP/Sounds -o inventory   # + inventory/sfx_index.csv
"""
import argparse
import collections
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="a .uax file or a directory of them")
    ap.add_argument("-o", "--out")
    ap.add_argument("--names", action="store_true", help="print the effect names (they are game identifiers)")
    args = ap.parse_args()

    paths = ([os.path.join(args.path, f) for f in sorted(os.listdir(args.path)) if f.lower().endswith(".uax")]
             if os.path.isdir(args.path) else [args.path])
    rows, total, total_bytes, failed = [], 0, 0, []
    for path in paths:
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            pkg = Package(path)
            pkg.read_names()
            pkg.read_imports()
            pkg.read_exports()
        except Exception as exc:
            failed.append((name, str(exc)))
            continue
        sounds = [e for e in pkg.exports if pkg.class_name_of(e) == "Sound"]
        size = os.path.getsize(path)
        total += len(sounds)
        total_bytes += size
        rows.append({"package": name, "sounds": len(sounds), "bytes": size,
                     "exports": pkg.export_count})
        print("%-22s sounds=%-4d bytes=%-8d exports=%d" % (name, len(sounds), size, pkg.export_count))
        if args.names:
            for e in sounds[:20]:
                print("    %s" % pkg.name(e["name"]))

    print("\npackages: %d, sounds: %d, bytes: %d" % (len(rows), total, total_bytes))
    if failed:
        print("failed: %s" % failed[:4])
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "sfx_index.csv")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["package", "sounds", "bytes", "exports"])
            w.writeheader()
            w.writerows(rows)
        print("wrote %s (%d rows)" % (path, len(rows)))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
