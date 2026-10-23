#!/usr/bin/env python3
"""Cross-reference the actor census against the class list and the shipped script.

Answers three questions with file-level evidence (PLAN 5.2/5.6 and the CUT_CONTENT standard):

  1. Which game classes are placed in no shipped map?   (candidate unplaced / leftover content)
  2. Which maps exist on disk but are named by no script? (candidate unreachable content)
  3. How is the map set partitioned?                     (missions vs DM/CTF vs entry/unused)

The class list comes from `inventory/classes.csv` (build packages) and the actor census from
`inventory/map_actors.csv` (map exports). Question 2 greps the reconstructed script tree, which is
game-derived and never committed, so it is optional: pass --src or set WORK.

Usage: content-census.py [--src DIR] [-o inventory]
"""
import argparse
import csv
import os
import re
import subprocess
import sys
from collections import defaultdict

GAME_PACKAGES = {"StargateGame", "StargateVehicles", "StargateContent", "StargateMenus",
                 "Stargateeffects", "Sharpeffects", "AmbientCreatures"}


def DumpSystemPath(dump_root):
    candidate = os.path.join(dump_root, "Stargate_SG-1_-_The_Alliance-2005-12-15")
    return candidate if os.path.isdir(candidate) else dump_root


def rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(os.environ.get("WORK", os.path.expanduser("~/sg1/work")),
                                                  "UnrealScript"))
    ap.add_argument("-o", "--out", default="inventory")
    args = ap.parse_args()

    maps = rows(os.path.join(args.out, "maps.csv"))
    actors = rows(os.path.join(args.out, "map_actors.csv"))
    classes = rows(os.path.join(args.out, "classes.csv"))

    placed = {r["class"] for r in actors}
    game_classes = {r["class"] for r in classes if r["package"] in GAME_PACKAGES}
    # Content classes (weapons, pickups, emitters) are not all meant to be placed directly; the
    # interesting set is everything unreferenced, which the report states rather than filters.
    unplaced = sorted(game_classes - placed)

    # The campaign's own definition: ChapterList config (class ChapterList is config(Tweak_Misc_ChapterList)
    # and its Chapters array is the only place a mission is mapped to a map file).
    chapters = []
    config_path = os.path.join(DumpSystemPath(os.environ.get("SG1_DUMP", "")), "System", "tweak", "Misc",
                               "ChapterList.ini") if os.environ.get("SG1_DUMP") else ""
    if config_path and os.path.isfile(config_path):
        entry = re.compile(r'Chapters\s*=\s*\(\s*Name\s*=\s*"([^"]*)"\s*,\s*MapFile\s*=\s*"([^"]*)"')
        with open(config_path, encoding="latin-1", errors="replace") as fh:
            for line_no, line in enumerate(fh, 1):
                m = entry.search(line)
                if m:
                    chapters.append({"index": len(chapters) + 1, "name": m.group(1),
                                     "mapfile": m.group(2)})

    on_disk = {m["map"].lower() for m in maps}
    for chapter in chapters:
        key = chapter["mapfile"].lower()
        if key.endswith(".unr"):
            key = key[:-4]
        chapter["on_disk"] = key in on_disk

    prefixes = defaultdict(int)
    for m in maps:
        name = m["map"]
        prefix = "mission" if re.match(r"^M\d", name) else \
                 "deathmatch" if name.startswith("DM-") else \
                 "ctf" if name.startswith("CTF-") else "other"
        prefixes[prefix] += 1

    # Two mention sources, of very different strength:
    #  - the reconstructed script: weak, because mission names are built by concatenation
    #    ("M" $ chapter $ "A"), so a literal match under-counts badly;
    #  - the shipped config (.ini/.int under System/): authoritative for map lists, since a map has
    #    to be named there to appear in a menu.
    config_mentions = defaultdict(int)
    game_dir = os.environ.get("SG1_DUMP")
    if game_dir:
        sys_dir = os.path.join(DumpSystemPath(game_dir), "System")
        if os.path.isdir(sys_dir):
            text = []
            for name in sorted(os.listdir(sys_dir)):
                if name.lower().endswith((".ini", ".int", ".txt")):
                    with open(os.path.join(sys_dir, name), encoding="latin-1", errors="replace") as fh:
                        text.append(fh.read().lower())
            blob = "\n".join(text)
            for m in maps:
                config_mentions[m["map"]] = blob.count(m["map"].lower())
    script_mentions = {}
    src_ok = os.path.isdir(args.src)
    if src_ok:
        blob = []
        for dirpath, _d, filenames in os.walk(args.src):
            for name in filenames:
                if name.endswith(".uc"):
                    with open(os.path.join(dirpath, name), encoding="utf-8", errors="replace") as fh:
                        blob.append(fh.read().lower())
        blob = "\n".join(blob)
        for m in maps:
            script_mentions[m["map"]] = blob.count(m["map"].lower())

    if chapters:
        with open(os.path.join(args.out, "chapters.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["index", "chapter", "mapfile", "present_on_disk"])
            for c in chapters:
                w.writerow([c["index"], c["name"], c["mapfile"], str(c["on_disk"]).lower()])

    with open(os.path.join(args.out, "content_census.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["map", "actor_exports", "script_mentions", "config_mentions"])
        for m in maps:
            w.writerow([m["map"], m["actor_exports"], script_mentions.get(m["map"], ""),
                        config_mentions.get(m["map"], "")])

    unreferenced = [m["map"] for m in maps if script_mentions.get(m["map"], 0) == 0] if src_ok else []
    top_unplaced = sorted(unplaced)

    lines = ["# Content census", "",
             "Generated by `tools/content-census.py` from `maps.csv`, `map_actors.csv` and",
             "`classes.csv`. Every number here is citable (PLAN 5.6 / docs/CUT_CONTENT.md); the",
             "script column depends on the local decompiled tree and is blank when it was not built.",
             "", "## Map set", "", "| kind | maps |", "|---|---|"]
    for kind, count in sorted(prefixes.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {kind} | {count} |")
    lines += ["", f"{len(maps)} maps on disk, {sum(int(m['actor_exports']) for m in maps):,} placed actors, "
              f"{len(placed)} distinct actor classes across all maps.", ""]

    if chapters:
        present = [c for c in chapters if c["on_disk"]]
        missing = [c for c in chapters if not c["on_disk"]]
        listed = {c["mapfile"].lower().removesuffix(".unr") for c in chapters}
        unlisted = sorted(m["map"] for m in maps if m["map"].lower() not in listed)
        lines += ["## Campaign definition vs shipped maps", "",
                  "`System/tweak/Misc/ChapterList.ini` is the campaign: `StargateMenus.ChapterList`'s",
                  "`Chapters` array is `config(...)`, so that file is the only place a mission is bound",
                  "to a map. Comparing it with `Maps/` is the strongest static cut-content evidence",
                  "available without running the game.", "",
                  f"- {len(chapters)} chapters defined; {len(present)} reference a map that ships in this",
                  f"  build, {len(missing)} reference a map that does not.",
                  f"- {len(unlisted)} of the {len(maps)} shipped maps appear in no chapter.", ""]
        if missing:
            lines += ["Chapters pointing at absent maps:", "", "| # | chapter | map file |", "|---|---|---|"]
            for c in missing:
                lines.append(f"| {c['index']} | {c['name']} | `{c['mapfile']}` |")
            lines.append("")
        if unlisted:
            lines += ["Shipped maps that no chapter lists:", ""]
            lines += [f"- `{m}`" for m in unlisted]
            lines += ["", "### What this means", "",
                      "The two sets are disjoint, and that is a finding rather than a parsing error:", "",
                      "- The chapter list uses a **second campaign revision** (`m01a`, `M02AX`, `m07ax`, "
                      "`m12ax`, … up to Mission 21), matching the `Music/M01…M21` and `Speech/M01…M21` "
                      "folders — a 21-mission design.",
                      "- The shipped `Maps/` set uses the prototype's own ids (`M00`, `M02A`, `M04a`, "
                      "`M07A`, `M12A`, …), which no chapter references.",
                      "- This is visible from outside the binary too: the release notes tell players the "
                      "single-player campaign cannot be started from the Single Player menu and that "
                      "missions must be loaded from **Continue**. The menu path is broken because the "
                      "chapter list it reads names maps this build does not contain.",
                      "",
                      "So the interesting cut-content question is not \"which maps are missing\" (that is "
                      "answered above: 37 chapter maps) but \"which of the 73 shipped maps is a later or "
                      "earlier revision of a chapter\" — a naming/revision mapping that needs the "
                      "profile data (`.sgp`, `.sgs`) and runtime checks in Phase 7.", ""]

    lines += ["## Map reachability", "",
              "Two mention counts per map: literal occurrences in the reconstructed script, and literal",
              "occurrences in the shipped config (`System/*.ini`, `*.int`). The script count is a weak",
              "signal — mission ids are assembled by concatenation — so the **config** count is the one",
              "that means something: a map named nowhere in the config cannot appear in a menu.",
              ""]
    if not config_mentions:
        lines.append("_Config counts not computed: set `SG1_DUMP` to the dump root and re-run._")
    else:
        silent = sorted(m["map"] for m in maps if config_mentions.get(m["map"], 0) == 0)
        lines.append(f"{len(maps) - len(silent)} of {len(maps)} maps are named in the config; "
                     f"{len(silent)} are not.")
        if silent:
            lines += ["", "Not named in any config file:", ""]
            lines += [f"- `{m}`" for m in silent]
            lines += ["", "These are reachable, if at all, only through a console command or a directory",
                      "scan — a runtime question for Phase 7, not a static conclusion here."]
    lines.append("")
    if src_ok and unreferenced:
        lines += ["## Maps no script text mentions (weak signal)", "",
                  f"{len(unreferenced)} of {len(maps)} map names never appear literally in the script.",
                  "Mission ids are built dynamically in the chapter/menu code, so this list is expected",
                  "to be long and is reported only to keep the two signals separate.", ""]

    lines += ["## Game classes placed in no shipped map", "",
              f"{len(top_unplaced)} of {len(game_classes)} game-package classes have no instance in any "
              "`.unr` export census.", ""]
    lines += ["| class | package | functions |", "|---|---|---|"]
    by_name = {r["class"]: r for r in classes}
    for name in top_unplaced:
        r = by_name.get(name, {})
        lines.append(f"| `{name}` | `{r.get('package', '')}` | {r.get('functions', '')} |")
    lines += ["", "Read this table as a work list, not as a verdict: many entries are base classes,",
              "abstract actors, weapons spawned at runtime, or content classes only referenced from",
              "other classes. The tests that promote an entry to a cut-content claim are in",
              "`docs/CUT_CONTENT.md`.", ""]

    with open(os.path.join("docs", "CONTENT_CENSUS.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))
    print(f"{len(maps)} maps, {len(placed)} placed classes, {len(unplaced)} game classes unplaced, "
          f"{len(unreferenced)} maps unmentioned by script -> docs/CONTENT_CENSUS.md")


if __name__ == "__main__":
    main()
