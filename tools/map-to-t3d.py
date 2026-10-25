#!/usr/bin/env python3
"""Write a map's actors out as T3D (PLAN 5.2).

The parsing is done (`docs/ACTOR_PROPERTIES.md`): a `.unr`'s actors are exports, and 62% of exports — every
actor — carry a readable property block. This is the formatting step: one `Begin Actor` … `End Actor` section
per actor, with the properties written the way the editor writes them.

    python3 tools/map-to-t3d.py DUMP/Maps/M02A.unr --out $WORK/extracted/maps
    python3 tools/map-to-t3d.py DUMP/Maps --out $WORK/extracted/maps

Output is game-derived content: `$WORK` only, never this repository (`.gitignore` keeps `/work` and `/out` out,
and the docs say the same). What is committed is the tool and the counts it prints.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402
from importlib import import_module

ps = import_module("prop_stream") if False else None  # keep linters quiet about the import name


def load_prop_stream():
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prop-stream.py")
    spec = importlib.util.spec_from_file_location("prop_stream", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fmt(value):
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        return "%.6g" % value
    if isinstance(value, tuple) and len(value) == 3:
        if all(isinstance(v, float) for v in value):
            return "(X=%.6g,Y=%.6g,Z=%.6g)" % value
        return "(Pitch=%d,Yaw=%d,Roll=%d)" % value
    if value is None:
        return '""'
    return '"%s"' % str(value).replace('"', "'")


def write_map(path, outdir, mod, verbose=False):
    pkg = Package(path)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    name = os.path.splitext(os.path.basename(path))[0]
    lines = ["// %s — written by tools/map-to-t3d.py" % name, ""]
    actors = 0
    for exp in pkg.exports:
        data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
        found = mod.find_block(data, pkg)
        if not found:
            continue
        count, start, props = found
        cls = pkg.class_name_of(exp)
        lines.append("Begin Actor Class=%s Name=%s_%d" % (cls, cls, exp["index"] + 1))
        for p in props:
            # decode simple values on demand (read_block already walked the bytes)
            lines.append("   %s=%s" % (p["name"], fmt(p.get("value"))))
        lines.append("End Actor")
        lines.append("")
        actors += 1
    text = "\n".join(lines)
    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, name + ".t3d")
    with open(out, "w") as f:
        f.write(text)
    if verbose:
        print("  %s" % out)
    return actors, len(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="a .unr file or a directory of them")
    ap.add_argument("--out", required=True, help="$WORK/extracted/maps (never the repository)")
    ap.add_argument("--limit", type=int, help="first N maps only")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if "/work/" not in args.out and not args.out.startswith(os.path.expanduser("~/sg1/work")):
        print("note: writing into %s — this is game content, keep it under $WORK" % args.out)

    mod = load_prop_stream()
    paths = ([os.path.join(args.path, f) for f in sorted(os.listdir(args.path)) if f.lower().endswith(".unr")]
             if os.path.isdir(args.path) else [args.path])
    if args.limit:
        paths = paths[: args.limit]

    total_actors = total_bytes = 0
    for path in paths:
        actors, size = write_map(path, args.out, mod, args.verbose)
        total_actors += actors
        total_bytes += size
        print("%-22s actors=%-6d t3d=%8d bytes" % (os.path.splitext(os.path.basename(path))[0], actors, size))
    print("\nmaps: %d, actors: %d, t3d bytes: %d, out: %s"
          % (len(paths), total_actors, total_bytes, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
