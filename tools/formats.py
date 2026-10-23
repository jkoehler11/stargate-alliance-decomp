#!/usr/bin/env python3
"""Catalogue the dump's non-obvious file formats and identify each container.

The dump mixes four different universes under one directory tree: engine packages (`.u`, `.unr`,
`.utx`, `.ukx`, `.usx`, `.uax` and -- surprisingly -- the `.sgs` checkpoints), XML ragdoll
descriptions (`.ka`, `.kaw`), plain INI profiles (`.sgp`, `.PRO`) and an opaque binary (`.lad`).
Knowing which is which decides the reader: a package needs UELib, XML needs a parser, INI needs
nothing, and only `.lad` needs reverse engineering.

Counts come from the committed manifest (paths only, so no game bytes are read); container
detection reads the first bytes of a sample directory when `--samples` is given. Every row names
the tool that handles the format and the doc that describes it.

Usage: formats.py [--manifest inventory/manifest.sha256] [--samples DIR] [-o inventory]
"""
import argparse
import csv
import os
import struct
import sys

UNREAL_MAGIC = 0x9E2A83C1

FORMATS = [
    # extension, parser, doc
    ("u", "UELib (Sg1Tool)", "docs/INVENTORY.md"),
    ("unr", "UELib (Sg1Tool maps)", "docs/CONTENT_CENSUS.md"),
    ("utx", "umodel", "PLAN 5.1"),
    ("ukx", "umodel", "PLAN 5.1"),
    ("usx", "umodel", "PLAN 5.1"),
    ("uax", "umodel", "PLAN 5.1"),
    ("sgs", "UELib (Sg1Tool save)", "docs/formats/sgs-checkpoint.md"),
    ("sgp", "config/INI reader", "docs/formats/sgp-profile.md"),
    ("PRO", "config/INI reader", "docs/formats/sgp-profile.md"),
    ("ka", "XML (KARMA 1.0)", "docs/formats/ragdoll-ka.md"),
    ("kaw", "XML (KAT_WORKSPACE)", "docs/formats/ragdoll-ka.md"),
    ("lad", "unknown -- LIPSinc binary", "docs/formats/lipsinc-lad.md"),
]

EXTENSIONS = {row[0]: row for row in FORMATS}


def container_of(path):
    with open(path, "rb") as fh:
        head = fh.read(16)
    if len(head) >= 4 and struct.unpack_from("<I", head)[0] == UNREAL_MAGIC:
        return "unreal-package"
    if head.startswith(b"<?xml"):
        return "xml"
    if head.startswith(b"[") or head.startswith(b";"):
        return "ini"
    return "binary"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="inventory/manifest.sha256")
    ap.add_argument("--samples", help="directory holding one example of each format")
    ap.add_argument("-o", "--out", default="inventory")
    args = ap.parse_args()
    if not os.path.isfile(args.manifest):
        sys.exit(f"manifest not found: {args.manifest}")

    counts = {}
    if os.path.isdir(args.manifest):
        for root, _dirs, files in os.walk(args.manifest):
            for name in files:
                counts[os.path.splitext(name)[1].lstrip(".")] = \
                    counts.get(os.path.splitext(name)[1].lstrip("."), 0) + 1
    else:
        with open(args.manifest, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("#") or not line.strip():
                    continue
                path = line.split("  ", 1)[-1].rstrip("\n")
                ext = os.path.splitext(path)[1].lstrip(".")
                counts[ext] = counts.get(ext, 0) + 1

    # Container detection from samples: the extension tells you the family, the bytes tell you the
    # reader. An extension with two containers (a package masquerading as a save) is worth knowing.
    found = {}
    if args.samples:
        for root, _dirs, files in os.walk(args.samples):
            for name in files:
                ext = os.path.splitext(name)[1].lstrip(".")
                if ext not in EXTENSIONS:
                    continue
                found.setdefault(ext, set()).add(container_of(os.path.join(root, name)))

    os.makedirs(args.out, exist_ok=True)
    out = os.path.join(args.out, "formats.csv")
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["extension", "files", "container", "parser", "doc"])
        for ext, _parser, doc in FORMATS:
            container = "+".join(sorted(found.get(ext, {"not sampled"})))
            w.writerow([ext, counts.get(ext, 0), container, EXTENSIONS[ext][1], doc])
    print(f"{len(FORMATS)} formats catalogued -> {out}")
    for ext, _p, _d in FORMATS:
        container = "+".join(sorted(found.get(ext, {"not sampled"})))
        print(f"  .{ext:<4} {counts.get(ext, 0):>5} files  {container}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
