#!/usr/bin/env python3
"""Read a UE2 profile (`.sgp`) and reconcile its `[Saved Games]` index with the checkpoints on disc.

The profile is plain text, not a package: sections in `[brackets]`, `key=value` rows. Two sections
matter here — `Engine.PlayerProfile` (settings) and `[Saved Games]`, which is the checkpoint *index*:
one row per save, the key is the checkpoint file name and the value is the description the menu
shows. So the write path is two artefacts, not one — the `.sgs` package and this row — and a rebuilt
checkpoint that is not in this section cannot be resumed.

What this tool answers without a boot: how many rows the shipped index carries, what shape the keys
have, which rows have a file, and which files have no row. Those numbers are what the docs quote;
the descriptions themselves are game text and are reduced to a kind (`autosave` / `label`) rather
than printed.

Usage:
    sgs-profile.py PROFILE.sgp [--sgs-dir DIR]
    sgs-profile.py --check                     # synthetic self-test, no dump, no writes
"""
import argparse
import os
import re
import sys

SECTION = re.compile(r"^\[(.+?)\]\s*$")
DIGITS = re.compile(r"\d{6,}")


def parse(text):
    """Return [(section, key, value)] in file order, plus the section order."""
    order, rows, cur = [], [], None
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line or line[0] in ";#":
            continue
        m = SECTION.match(line)
        if m:
            cur = m.group(1)
            if cur not in order:
                order.append(cur)
            continue
        if cur is None or "=" not in line:
            continue
        key, value = line.split("=", 1)
        rows.append((cur, key.strip(), value.strip()))
    return order, rows


def shape(key):
    """The key with its timestamp masked: `C_M07b_20050413_170733.sgs` -> `C_M07b_#_#.sgs`."""
    return DIGITS.sub("#", key)


def kind(value):
    return "autosave" if value.lower().startswith("autosave") else "label"


def report(profile, sgs_dir):
    with open(profile, encoding="latin-1") as fh:
        order, rows = parse(fh.read())
    by_section = {s: [] for s in order}
    for section, key, value in rows:
        by_section[section].append((key, value))

    print(f"{profile}: {len(order)} sections, {len(rows)} rows")
    for section in order:
        print(f"  [{section}] {len(by_section[section])} keys")

    saves = by_section.get("Saved Games", [])
    if not saves:
        print("  no [Saved Games] section: nothing is resumable from this profile")
        return 0

    shapes = {}
    kinds = {}
    for key, value in saves:
        sh = shapes.setdefault(shape(key), {"n": 0, "example": key})
        sh["n"] += 1
        kinds[kind(value)] = kinds.get(kind(value), 0) + 1
    print(f"  [Saved Games] {len(saves)} rows, {len(shapes)} key shape(s)")
    for sh in sorted(shapes, key=lambda s: -shapes[s]["n"]):
        print(f"    {sh:44s} {shapes[sh]['n']:4d}  e.g. {shapes[sh]['example']}")
    print("    values: " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))

    if not sgs_dir:
        return 0
    files = sorted(f for f in os.listdir(sgs_dir) if f.lower().endswith(".sgs"))
    indexed = {key for key, _ in saves}
    present = [f for f in files if f in indexed]
    print(f"  {len(files)} .sgs file(s) in {sgs_dir}, {len(present)} of them indexed, "
          f"{len(indexed) - len(present)} index row(s) with no file")
    for f in files:
        if f not in indexed:
            print(f"    unreferenced file: {f}")
    return 0


def self_check():
    sample = "\r\n".join([
        "[Engine.PlayerProfile]",
        "CharacterDisplayName=O'neill",
        "",
        "[Saved Games]",
        "C_M07b_20050413_170733.sgs=AutosaveCheckpoint4",
        "C_M07b_20050418_150452.sgs=Mission 7 - Tombs - Upper Cavern",
        "",
    ])
    order, rows = parse(sample)
    assert order == ["Engine.PlayerProfile", "Saved Games"], order
    assert len(rows) == 3, rows
    assert rows[1][1] == "C_M07b_20050413_170733.sgs", rows[1]
    assert shape(rows[1][1]) == "C_M07b_#_#.sgs", shape(rows[1][1])
    assert shape(rows[2][1]) == shape(rows[1][1])
    assert kind(rows[1][2]) == "autosave" and kind(rows[2][2]) == "label"
    assert parse("[A]\n; comment\nB=1\n")[1] == [("A", "B", "1")]
    print("sgs-profile --check: parser, masking and value kinds agree with the sample")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("profile", nargs="?", help="the .sgp file")
    ap.add_argument("--sgs-dir", help="directory holding the checkpoints (default: the profile's own)")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; no dump, no writes")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if not args.profile:
        ap.error("a .sgp path is required (or --check)")
    sgs_dir = args.sgs_dir or os.path.dirname(os.path.abspath(args.profile))
    return report(args.profile, sgs_dir)


if __name__ == "__main__":
    sys.exit(main())
