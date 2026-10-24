#!/usr/bin/env python3
"""Build the campaign matrix: what exists for each mission id, and what the campaign expects (5.6).

One row per mission id, with the four independent artefacts the repository measures:

    map      inventory/maps.csv           a map on disk whose id is this mission
    speech   inventory/speech_index.csv   recorded dialogue lines for it
    music    inventory/music_index.csv    music cues whose group covers it
    chapter  inventory/chapters.csv       a chapter in ChapterList.ini naming a map of that id

The point is the disagreements: a mission with music and no map, or a chapter with no map and no
audio, is a cut-content claim with four file-level citations behind it.

    python3 tools/campaign-matrix.py -o inventory

Reads committed CSVs only -- no dump needed, so it runs anywhere.
"""
import argparse
import collections
import csv
import os
import re
import sys

MISSION_IN_MAP = re.compile(r"^[Mm](\d+)([A-Za-z])?")
MARKER = re.compile(r"[xX]$")


def mission_of_map(name):
    m = MISSION_IN_MAP.match(name)
    if not m:
        return None
    return "M%02d" % int(m.group(1))


def chapter_missions(mapfile):
    stem = os.path.splitext(mapfile)[0]
    stem = MARKER.sub("", stem)                 # the 'x' build marker: m02ax -> m02a
    mid = mission_of_map(stem)
    return mid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--maps", default="inventory/maps.csv")
    ap.add_argument("--speech", default="inventory/speech_index.csv")
    ap.add_argument("--music", default="inventory/music_index.csv")
    ap.add_argument("--chapters", default="inventory/chapters.csv")
    args = ap.parse_args()

    for p in (args.maps, args.speech, args.music, args.chapters):
        if not os.path.isfile(p):
            sys.exit("missing %s" % p)

    maps = collections.defaultdict(list)
    for row in csv.DictReader(open(args.maps)):
        mid = mission_of_map(row["map"])
        if mid:
            maps[mid].append(row["map"])

    speech = collections.Counter()
    for row in csv.DictReader(open(args.speech)):
        if row.get("mission"):
            speech["M%02d" % int(row["mission"].lstrip("mM"))] += 1

    music = collections.defaultdict(set)
    music_raw = collections.defaultdict(set)
    for row in csv.DictReader(open(args.music)):
        for mid in row["missions_covered"].split():
            m = MISSION_IN_MAP.match(mid)
            if not m:
                continue                      # a group like `entry` names no mission
            music["M%02d" % int(m.group(1))].add(row["group"])
            music_raw["M%02d" % int(m.group(1))].add(mid)

    chapters = collections.defaultdict(list)
    other_chapters = []
    for row in csv.DictReader(open(args.chapters)):
        mid = chapter_missions(row["mapfile"])
        if mid:
            chapters[mid].append("%s -> %s" % (row["chapter"], row["mapfile"]))
        else:
            other_chapters.append("%s -> %s" % (row["chapter"], row["mapfile"]))

    ids = sorted(set(maps) | set(speech) | set(music) | set(chapters))
    rows = []
    for mid in ids:
        has_map = bool(maps[mid])
        has_speech = speech[mid] > 0
        has_music = bool(music[mid])
        has_chapter = bool(chapters[mid])
        if has_map and (has_speech or has_music) and has_chapter:
            verdict = "shipped, recorded, in campaign"
        elif has_map and (has_speech or has_music):
            verdict = "shipped, recorded, NOT in campaign"
        elif has_map:
            verdict = "shipped map only"
        elif has_speech or has_music:
            verdict = "audio only (no map)"
        else:
            verdict = "campaign entry only"
        rows.append([mid, len(maps[mid]), ";".join(sorted(maps[mid])[:6]),
                     speech[mid], len(music[mid]), ";".join(sorted(music[mid])),
                     len(chapters[mid]), " | ".join(chapters[mid][:2]), verdict])

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "campaign_matrix.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["mission", "maps_on_disk", "map_names", "speech_lines", "music_groups",
                    "music_group_names", "chapters", "chapter_entries", "verdict"])
        w.writerows(rows)

    print("%-5s %-6s %-7s %-7s %-8s %s" % ("id", "maps", "speech", "music", "chapters", "verdict"))
    for r in rows:
        print("%-5s %-6d %-7d %-7d %-8d %s" % (r[0], r[1], r[3], r[4], r[6], r[8]))
    print("\n%d mission ids; %d chapters name a non-mission map:" % (len(rows), len(other_chapters)))
    for c in other_chapters:
        print("   " + c)
    print("wrote %s" % path)


if __name__ == "__main__":
    main()
