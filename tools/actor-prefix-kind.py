#!/usr/bin/env python3
"""The front record marks an actor: the class's derivation decides whether an export carries one.

`actor_prefix_record.csv` splits the map exports by their front bytes and leaves two populations it could not
explain: 79,925 blocks that begin with `[class][class][8 x 0xff][4][0x81]` and 41,254 that begin directly with a
property tag. The parent that distinguishes them is not in the block -- it is in the class table. This tool joins
each map export's class to `inventory/classes.csv`'s `super` column, walks the chain to its root, and records both
what the front is and where the class sits:

    record  the 13-byte record is in front of the block (`2*w + 13`, the class index twice)
    bare    the block starts at the export's first byte, no front at all
    other   bytes in front that are not the record, or no clean block under the locator

The rule it measures: **a record exactly when the class's chain reaches `Actor`.** On all 73 maps that is 148
classes and 79,925 blocks for `record`, 147 classes and 41,254 blocks for `bare`, with no class on both sides --
`ReachSpec` and `ActionMoveCamera` extend `Object`, `ACTION_*` extend `ScriptedAction`, and `SpriteEmitter`'s parent
`ParticleEmitter` is itself an `Object` in this build. The three classes the table does not hold (`StaticMeshInstance`
211, `KMeshProps` 1, `TerrainSector` 1) are the `other` population: their exports are mesh and terrain data, and the
"block" the locator finds inside them is a tag-shaped false positive.

    python3 tools/actor-prefix-kind.py DUMP/Maps -o inventory     # needs the dump
    python3 tools/actor-prefix-kind.py --check                     # dump-free: shape, self-test, joins

`blocks` is the locator's block count for the class, `record`/`bare`/`other` the front it found on each, `super` and
`root` the class table's answer, `actor_derived` whether `Actor` appears in that chain, and `verdict` the class's
kind (`actor`, `object`, `other`, `mixed`, `unresolved`).
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

COLUMNS = ["class", "blocks", "record", "bare", "other", "super", "root", "actor_derived", "verdict"]


def loader():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prop-stream.py")
    spec = importlib.util.spec_from_file_location("prop_stream", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def record_loader():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "actor-prefix-record.py")
    spec = importlib.util.spec_from_file_location("actor_prefix_record", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def class_table(path):
    """`{class: super}` from `inventory/classes.csv`; the first row wins, as that table is per package."""
    table = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            table.setdefault(r["class"], r["super"])
    return table


def chain(cls, table):
    """`(names, root, unresolved)` -- the super chain, its last name, and whether it stopped on a missing class."""
    names, seen = [], set()
    while cls and cls not in seen:
        seen.add(cls)
        names.append(cls)
        if cls not in table:
            return names, names[-1], True
        cls = table[cls]
    return names, (names[-1] if names else ""), bool(cls)


def candidates(pkg, mod, data):
    """Every start in the window whose property list parses cleanly and ends at the export's end."""
    out = []
    for start in range(0, min(len(data), 64)):
        try:
            props, used, reason = mod.read_block(data[start:], pkg, True)
        except Exception:
            continue
        if reason != "None" or len(props) < 2:
            continue
        if any(p["name"] == "?" for p in props):
            continue
        if start + used != len(data):
            continue
        out.append((start, props))
    return out


def maps_in(path):
    return sorted(f for f in os.listdir(path) if f.lower().endswith(".unr"))


def compute(maps_dir, table):
    mod, rec = loader(), record_loader()
    stat = collections.defaultdict(collections.Counter)
    order = []

    for fname in maps_in(maps_dir):
        pkg = Package(os.path.join(maps_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        for exp in pkg.exports:
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            found = candidates(pkg, mod, data)
            if not found:
                continue
            cls = pkg.class_name_of(exp)
            if cls not in stat:
                order.append(cls)
            stat[cls]["blocks"] += 1
            front = found[0][0]
            _, matched = rec.record(data, exp["class"])
            if matched:
                stat[cls]["record"] += 1
            elif front == 0:
                stat[cls]["bare"] += 1
            else:
                stat[cls]["other"] += 1

    rows = []
    for cls in sorted(order, key=lambda c: (-stat[c]["blocks"], c)):
        st = stat[cls]
        _, root, unresolved = chain(cls, table)
        derived = "Actor" in chain(cls, table)[0]
        if unresolved:
            verdict = "unresolved"
        elif st["record"] and not st["bare"] and not st["other"]:
            verdict = "actor"
        elif st["bare"] and not st["record"] and not st["other"]:
            verdict = "object"
        elif st["other"] and not st["record"] and not st["bare"]:
            verdict = "other"
        else:
            verdict = "mixed"
        rows.append([cls, st["blocks"], st["record"], st["bare"], st["other"],
                     table.get(cls, ""), root, "yes" if derived else "no", verdict])
    return rows


def selftest():
    """A synthetic class table: a chain that reaches `Actor`, one that stops on an object, and one that is absent."""
    problems = []
    table = {"Pawn": "Actor", "Actor": "Object", "Object": "", "Particle": "Object", "ReachSpec": "Object"}
    if chain("Pawn", table)[:2] != (["Pawn", "Actor", "Object"], "Object"):
        problems.append("selftest: the chain to Object does not read Pawn -> Actor -> Object")
    if "Actor" not in chain("Pawn", table)[0]:
        problems.append("selftest: an actor chain does not carry Actor")
    if "Actor" in chain("Particle", table)[0]:
        problems.append("selftest: a ParticleEmitter-shaped chain claims Actor")
    names, root, unresolved = chain("Ghost", table)
    if names != ["Ghost"] or not unresolved:
        problems.append("selftest: an absent class does not answer unresolved")
    return problems


def check(outdir):
    """Shape, the row sums, the join with the record census, and the rule's own fit."""
    path = os.path.join(outdir, "actor_prefix_kind.csv")
    problems = selftest() + checkcsv.shape(path, COLUMNS, key="class")
    if problems:
        return problems
    record = {}
    rpath = os.path.join(outdir, "actor_prefix_record.csv")
    if os.path.isfile(rpath):
        with open(rpath, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                record[r["class"]] = int(r["blocks"])
    else:
        problems.append("actor_prefix_record.csv is missing")
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        try:
            blocks, got = int(r["blocks"]), int(r["record"]) + int(r["bare"]) + int(r["other"])
        except ValueError:
            problems.append("%s: a count is not a number" % r["class"])
            continue
        if got != blocks:
            problems.append("%s: %d record + bare + other != %d block(s)" % (r["class"], got, blocks))
        if r["class"] in record and record[r["class"]] != blocks:
            problems.append("%s: %d block(s) against the record census's %d" % (r["class"], blocks, record[r["class"]]))
        if r["class"] not in record:
            problems.append("%s: not in actor_prefix_record.csv" % r["class"])
        counts = [int(r["record"]), int(r["bare"]), int(r["other"])]
        want = {"actor": [1, 0, 0], "object": [0, 1, 0], "other": [0, 0, 1]}.get(r["verdict"])
        if want is not None and [1 if c else 0 for c in counts] != want:
            problems.append("%s: verdict %s with %d/%d/%d record/bare/other"
                            % (r["class"], r["verdict"], counts[0], counts[1], counts[2]))
        if r["verdict"] == "mixed" and sum(1 for c in counts if c) < 2:
            problems.append("%s: verdict mixed with one front kind" % r["class"])
        if (r["verdict"] == "unresolved") != (r["root"] == r["class"]):
            problems.append("%s: verdict %s with root %s" % (r["class"], r["verdict"], r["root"]))
    # the rule itself: no class may carry a record without reaching Actor, or sit bare on one
    wrong = [r["class"] for r in rows if r["verdict"] in ("actor", "object")
             and (r["verdict"] == "actor") != (r["actor_derived"] == "yes")]
    if wrong:
        problems.append("%d class(es) break record <=> Actor: %s" % (len(wrong), ", ".join(wrong[:5])))
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
        return checkcsv.report("actor-prefix-kind", problems,
                               "actor_prefix_kind.csv is the shape this tool writes, every row sums to its block "
                               "count, the block totals agree with actor_prefix_record.csv and the record <=> Actor "
                               "split holds (shape only: needs the dump)")

    if not args.maps_dir:
        ap.error("the Maps directory is required to rebuild the artifact")

    table = class_table(os.path.join(outdir, "classes.csv"))
    rows = compute(args.maps_dir, table)
    path = os.path.join(outdir, "actor_prefix_kind.csv")
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        w.writerow(COLUMNS)
        w.writerows(rows)
    blocks = sum(int(r[1]) for r in rows)
    rec = sum(int(r[2]) for r in rows)
    bare = sum(int(r[3]) for r in rows)
    other = sum(int(r[4]) for r in rows)
    artist = [r[0] for r in rows if r[8] == "actor"]
    obj = [r[0] for r in rows if r[8] == "object"]
    oth = [r[0] for r in rows if r[8] == "other"]
    mixed = [r[0] for r in rows if r[8] == "mixed"]
    unres = [r[0] for r in rows if r[8] == "unresolved"]
    print("wrote %s (%d rows)" % (path, len(rows)))
    print("%d block(s) over %d class(es): %d record, %d bare, %d other" % (blocks, len(rows), rec, bare, other))
    print("%d class(es) actor (record), %d object (bare), %d other, %d mixed, %d unresolved"
          % (len(artist), len(obj), len(oth), len(mixed), len(unres)))
    print("unresolved: %s" % ", ".join("%s %d" % (r[0], int(r[1])) for r in rows if r[8] == "unresolved"))
    if mixed:
        print("mixed: %s" % ", ".join(mixed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
