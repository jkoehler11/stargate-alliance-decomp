#!/usr/bin/env python3
"""Index the Music WAVs by group, and say which missions each group covers (PLAN 5.3).

Speech is indexed by `tools/dialogue-index.py` (docs/DIALOGUE.md). The music tree has a different
shape — one directory per mission group, four variants per cue — and it is the larger half of the
audio (1,923 of 3,287 WAVs), so it gets its own index.

    python3 tools/music-index.py DUMP_ROOT -o inventory

Group names are the campaign's own labels (`M02A`, `M10_17`, `entry`); the mission ids a group covers
are parsed from that label, and whether a map for the id ships is answered from `inventory/maps.csv`.
Byte counts come from the dump; only counts, names and sizes are emitted, never audio.
"""
import argparse
import collections
import csv
import os
import re
import sys

GAME_DIR = "Stargate_SG-1_-_The_Alliance-2005-12-15"
MISSION = re.compile(r"^[Mm](\d+)([A-Za-z])?$")


def mission_ids(group):
    """Mission ids a group label covers: M02A -> {M02A}, M10_17 -> {M10..M17}, entry -> {}."""
    g = group.strip()
    if "_" in g:
        head, _, tail = g.partition("_")
        a = MISSION.match(head)
        b = MISSION.match(tail) or re.match(r"^(\d+)$", tail)
        if a and b:
            lo, hi = int(a.group(1)), int(b.group(1))
            return ["M%02d" % n for n in range(lo, hi + 1)]
    m = MISSION.match(g)
    if m:
        return ["M%02d%s" % (int(m.group(1)), (m.group(2) or "").upper())]
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("--manifest", default="inventory/manifest.sha256")
    ap.add_argument("--maps", default="inventory/maps.csv")
    ap.add_argument("-o", "--out", default="inventory")
    args = ap.parse_args()

    root = os.path.join(args.dump, GAME_DIR)
    if not os.path.isdir(root):
        sys.exit("no %s under %s" % (GAME_DIR, args.dump))

    shipped = set()
    if os.path.isfile(args.maps):
        for row in csv.DictReader(open(args.maps)):
            if MISSION.match(row["map"]):
                shipped.add(row["map"].upper())

    groups = collections.OrderedDict()
    for dirpath, _dirs, files in os.walk(os.path.join(root, "Music")):
        rel = os.path.relpath(dirpath, os.path.join(root, "Music"))
        if rel == ".":
            continue
        wavs = [f for f in files if f.lower().endswith(".wav")]
        if not wavs:
            continue
        groups[rel] = sorted(wavs)

    rows = []
    for group in sorted(groups):
        wavs = groups[group]
        total = sum(os.path.getsize(os.path.join(root, "Music", group, f)) for f in wavs)
        variants = collections.Counter(os.path.splitext(f)[0].rsplit("_", 1)[-1] for f in wavs)
        ids = mission_ids(group)
        covers = " ".join(ids) if ids else "(none)"
        missing = [i for i in ids if i not in shipped
                   and not any(s.startswith(i) for s in shipped)]
        rows.append([group, len(wavs), total, covers,
                     "yes" if ids and not missing else ("no" if ids else "n/a"),
                     ",".join(sorted(variants)), ";".join(missing)])

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "music_index.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["group", "files", "bytes", "missions_covered", "map_ships",
                    "variants", "missions_without_a_map"])
        w.writerows(rows)

    total_files = sum(r[1] for r in rows)
    total_bytes = sum(r[2] for r in rows)
    print("%d groups, %d WAVs, %.1f MiB" % (len(rows), total_files, total_bytes / 1048576.0))
    for r in rows:
        print("  %-10s %4d files %6.1f MiB  covers %-28s maps %s%s"
              % (r[0], r[1], r[2] / 1048576.0, r[3], r[4],
                 ("  MISSING: " + r[6]) if r[6] else ""))
    print("wrote %s" % path)


if __name__ == "__main__":
    main()
