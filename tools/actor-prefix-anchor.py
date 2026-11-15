#!/usr/bin/env python3
"""The type a leading tag decodes as, not the offset, is what decides where a property block starts.

`actor_prefix_census.csv` (PLAN 5.2, `tools/map-census.py`) measures the offset in front of a class's property block
and finds that 119 of 295 classes locate at more than one offset. Its own note says why an extent test cannot settle
that: every property boundary is a valid start, so a suffix of the property list parses to the export's end just as
well as the front does, and "earliest start, most properties" is a tie-break rather than a reading.

`docs/ACTOR_PROPERTIES.md` named the next read as an identity anchor -- require the start whose first tag is the
class's own first property. Run over all 73 maps it does not survive: **35,870 of 121,394 blocks (29.5%) start with a
property other than the one their class usually starts with**, and the class's usual first property is not a constant
of the stream, so the anchor moves the start on instances that were never misparsed.

What does settle it is the tag's *type*. A property's info byte carries its type in the low nibble, and the leading
tag a misparse invents decodes as type 0, `Invalid`, which is not a property type at all: on `M02A` every
`StaticMeshActor` whose extent front is 12 begins with `PrePivot` typed `Invalid` and one byte of value, and rejecting
that candidate leaves the front at 15 on every one of them. So this tool locates each block twice -- the extent front
the census records, and the front with `Invalid` candidates rejected -- and counts how much the rule moves.

    python3 tools/actor-prefix-anchor.py DUMP/Maps -o inventory     # needs the dump
    python3 tools/actor-prefix-anchor.py --check                     # dump-free: shape and cross-artifact claims

`front`/`front_count` are the extent locator's choice (the census's `dominant_prefix`), `strict`/`strict_count` the
choice once type-0 candidates are rejected, `strict_variants` how many starts survive across the class, `moved` how
many of its blocks the rule moved, and `first`/`first_count`/`first_variants` the property the extent front starts
with -- which is how the anchor reading fails, measured rather than argued.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import checkcsv  # noqa: E402
from ue2pkg import Package  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

COLUMNS = ["class", "exports", "blocks", "front", "front_count", "first", "first_count", "first_variants",
           "strict", "strict_count", "strict_total", "strict_variants", "moved", "no_strict", "verdict"]

WINDOW = 64  # the same 64-byte window the locator reads
INVALID = "Invalid"  # type 0: not a property type, so a tag that decodes as it is a misparse


def loader():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prop-stream.py")
    spec = importlib.util.spec_from_file_location("prop_stream", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def candidates(pkg, mod, data):
    """Every start in the window whose property list parses cleanly and ends at the export's end.

    Ascending, which is the order the locator's tie-break reads. Each entry is `(start, props, invalid)`, `invalid`
    being how many of the block's properties decode as type 0.
    """
    out = []
    for start in range(0, min(len(data), WINDOW)):
        try:
            props, end, reason = mod.read_block(data[start:], pkg, True)
        except Exception:
            continue
        if reason != "None" or len(props) < 2:
            continue
        if any(p["name"] == "?" for p in props):
            continue
        if start + end != len(data):
            continue
        out.append((start, props, sum(1 for p in props if p.get("type") == INVALID)))
    return out


def maps_in(path):
    return sorted(f for f in os.listdir(path) if f.lower().endswith(".unr"))


def walk(maps_dir, mod, per_instance):
    """Call `per_instance(cls, candidates)` for every export with at least one candidate start."""
    for fname in maps_in(maps_dir):
        pkg = Package(os.path.join(maps_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        for exp in pkg.exports:
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            cands = candidates(pkg, mod, data)
            if cands:
                per_instance(pkg.class_name_of(exp), cands)


def compute(maps_dir):
    mod = loader()
    exports = collections.Counter()
    fronts = collections.defaultdict(collections.Counter)     # class -> {extent start: instances}
    firsts = collections.defaultdict(collections.Counter)     # class -> {first property name: instances}
    strict = collections.defaultdict(collections.Counter)     # class -> {start with no Invalid: instances}
    stats = collections.defaultdict(collections.Counter)      # class -> moved / no_strict

    def per_instance(cls, cands):
        exports[cls] += 1
        start, props, invalid = cands[0]
        fronts[cls][start] += 1
        firsts[cls][props[0]["name"]] += 1
        if invalid:
            stats[cls]["moved"] += 1
        clean = [c for c in cands if not c[2]]
        if not clean:
            stats[cls]["no_strict"] += 1
            return
        strict[cls][clean[0][0]] += 1

    walk(maps_dir, mod, per_instance)

    rows = []
    for cls in sorted(exports, key=lambda c: (-exports[c], c)):
        blocks = sum(fronts[cls].values())
        if not blocks:
            rows.append([cls, exports[cls], 0, "", 0, "", 0, 0, "", 0, 0, 0, 0, 0, "no-block"])
            continue
        front, front_n = sorted(fronts[cls].items(), key=lambda kv: (-kv[1], kv[0]))[0]
        name, first_n = sorted(firsts[cls].items(), key=lambda kv: (-kv[1], kv[0]))[0]
        hist = strict[cls]
        moved, no_strict = stats[cls]["moved"], stats[cls]["no_strict"]
        if hist:
            got, got_n = sorted(hist.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            verdict = "constant" if got_n == blocks else "varies"
        else:
            got, got_n, verdict = "", 0, "no-strict"
        rows.append([cls, exports[cls], blocks, front, front_n, name, first_n, len(firsts[cls]),
                     got, got_n, blocks - no_strict, len(hist), moved, no_strict, verdict])
    return rows


def check(outdir):
    """The claims the artifact makes that can be re-derived without the dump.

    Every count against its own denominator, the extent front against the census's `dominant_prefix` and
    `dominant_count` (the same walk in another tool), the block total against the census's, and the strict
    histogram against the moved count -- so a truncated run or a hand edit fails here, not in the prose.
    """
    path = os.path.join(outdir, "actor_prefix_anchor.csv")
    problems = checkcsv.shape(path, COLUMNS, key="class")
    if problems:
        return problems
    census = {}
    cpath = os.path.join(outdir, "actor_prefix_census.csv")
    if os.path.isfile(cpath):
        with open(cpath, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                census[r["class"]] = r
    else:
        problems.append("actor_prefix_census.csv is missing")
    total = 0
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        if not r["blocks"].strip() or int(r["blocks"]) == 0:
            if r["verdict"] != "no-block":
                problems.append("%s: verdict %s without a block" % (r["class"], r["verdict"]))
            continue
        try:
            blocks = int(r["blocks"])
            front, front_n = int(r["front"]), int(r["front_count"])
            first_n, first_v = int(r["first_count"]), int(r["first_variants"])
            got_n, got_v = int(r["strict_count"]), int(r["strict_variants"])
            got_all = int(r["strict_total"])
            moved, no_strict = int(r["moved"]), int(r["no_strict"])
        except ValueError:
            problems.append("%s: a count is not a number" % r["class"])
            continue
        total += blocks
        if max(front_n, first_n, got_n) > blocks or min(front_n, first_n, got_n) < 0:
            problems.append("%s: a count runs past its %d block(s)" % (r["class"], blocks))
        if first_v < 1 or got_v > blocks:
            problems.append("%s: %d first-property and %d strict variant(s)" % (r["class"], first_v, got_v))
        if got_all + no_strict != blocks:
            problems.append("%s: %d strict + %d unstrict != %d block(s)" % (r["class"], got_all, no_strict, blocks))
        if got_n > got_all:
            problems.append("%s: %d at the modal strict start of %d" % (r["class"], got_n, got_all))
        if moved > blocks:
            problems.append("%s: %d moved of %d block(s)" % (r["class"], moved, blocks))
        if not moved and (str(front) != r["strict"] or front_n != got_n):
            # Nothing moved, so the rule had nothing to reject: the strict front has to be the extent front. A stale
            # `strict` column beside an unchanged `front` is the one inconsistency the counts alone cannot show.
            problems.append("%s: %d moved but strict %s (%d) is not front %d (%d)"
                            % (r["class"], moved, r["strict"], got_n, front, front_n))
        if (r["verdict"] == "constant") != (got_n == blocks):
            problems.append("%s: verdict %s with %d of %d strict" % (r["class"], r["verdict"], got_n, blocks))
        if (r["verdict"] == "no-strict") != (got_n == 0):
            problems.append("%s: verdict %s with %d strict" % (r["class"], r["verdict"], got_n))
        have = census.get(r["class"])
        if have is None:
            problems.append("%s: not in actor_prefix_census.csv" % r["class"])
            continue
        if have["dominant_prefix"] and int(have["dominant_prefix"]) != front:
            problems.append("%s: front %d against the census's %s" % (r["class"], front, have["dominant_prefix"]))
        if have["dominant_count"] and int(have["dominant_count"]) != front_n:
            problems.append("%s: front count %d against the census's %s" % (r["class"], front_n,
                                                                            have["dominant_count"]))
        if have["blocks"] and int(have["blocks"]) != blocks:
            problems.append("%s: %d block(s) against the census's %s" % (r["class"], blocks, have["blocks"]))
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("maps_dir", nargs="?", help="the dump's Maps directory (not needed for --check)")
    ap.add_argument("-o", "--out", help="directory for the CSV (default: inventory/)")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    outdir = args.out or os.path.join(ROOT, "inventory")

    if args.check and not args.maps_dir:
        problems = check(outdir)
        return checkcsv.report("actor-prefix-anchor", problems,
                               "actor_prefix_anchor.csv is the shape this tool writes and agrees with "
                               "actor_prefix_census.csv on every extent front, front count and block total "
                               "(shape only: needs the dump)")
    if not args.maps_dir:
        ap.error("the Maps directory is required to rebuild the artifact")

    rows = compute(args.maps_dir)
    path = os.path.join(outdir, "actor_prefix_anchor.csv")
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        w.writerow(COLUMNS)
        w.writerows(rows)
    blocks = sum(int(r[2]) for r in rows)
    moved = sum(int(r[12]) for r in rows)
    with_block = sum(1 for r in rows if int(r[2]))
    strict_const = sum(1 for r in rows if r[14] == "constant")
    multi = sum(1 for r in rows if int(r[11]) > 1)
    other_first = sum(int(r[2]) - int(r[6]) for r in rows)
    print("wrote %s (%d rows)" % (path, len(rows)))
    print("%d block(s) over %d class(es): the type rule moves %d of them (%.1f%%)"
          % (blocks, len(rows), moved, 100.0 * moved / blocks if blocks else 0.0))
    print("%d of %d class(es) with a block land on one strict start, %d still on more than one"
          % (strict_const, with_block, multi))
    print("%d block(s) start with a property other than their class's usual one" % other_first)
    return 0


if __name__ == "__main__":
    sys.exit(main())
