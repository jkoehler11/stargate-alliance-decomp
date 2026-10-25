#!/usr/bin/env python3
"""Walk the subclass tree under one or more script classes, with the per-class data that matters.

`fork-surface.py` answers "where does the fork attach to the engine". This answers the other direction:
"what is under this root" -- the shape of one gameplay system. For each root it walks `super` links across
packages, prints the tree, and writes one CSV row per class with functions, natives, properties and
UnrealScript **states**, since a state count is the closest thing to a measure of behaviour.

    python3 tools/class-graph.py --root BotBase --root AIMessage --root Order
    python3 tools/class-graph.py --root Vehicle -o inventory      # + inventory/class_graph.csv

Actor usage is joined in from `inventory/map_actors.csv` (placed instances per map) and
`inventory/sgs_schema.csv` (instances inside shipped checkpoints), so a class with no map presence is
visible as such. Reads committed CSVs only; no dump needed.
"""
import argparse
import collections
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(path):
    full = os.path.join(ROOT, path)
    if not os.path.isfile(full):
        return []
    with open(full, newline="") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append", required=True, help="class name to walk under (repeatable)")
    ap.add_argument("--classes", default=os.path.join(ROOT, "inventory", "classes.csv"))
    ap.add_argument("-o", "--out")
    ap.add_argument("--max-depth", type=int, default=6)
    args = ap.parse_args()

    with open(args.classes, newline="") as f:
        classes = list(csv.DictReader(f))
    kids = collections.defaultdict(list)
    for r in classes:
        kids[r["super"]].append(r)

    actors = collections.defaultdict(int)
    for r in load(os.path.join("inventory", "map_actors.csv")):
        key = r.get("class") or r.get("actor_class") or ""
        for col in ("count", "instances", "actors"):
            if col in r and r[col].isdigit():
                actors[key] += int(r[col])
                break
        else:
            actors[key] += 1
    saved = collections.defaultdict(int)
    for r in load(os.path.join("inventory", "sgs_schema.csv")):
        saved[r["class"]] += int(r["instances"])

    rows = []
    for root in args.root:
        print("\n=== %s" % root)
        stack = [(root, 0)]
        seen = set()
        total = {"classes": 0, "functions": 0, "natives": 0, "states": 0}
        while stack:
            name, depth = stack.pop()
            for r in sorted(kids.get(name, []), key=lambda r: (-int(r["states"]), r["class"])):
                if (r["class"], r["package"]) in seen:
                    continue
                seen.add((r["class"], r["package"]))
                states, funcs = int(r["states"]), int(r["functions"])
                total["classes"] += 1
                total["functions"] += funcs
                total["natives"] += int(r["native_functions"])
                total["states"] += states
                print("%s%-28s %-16s %3d states %4d funcs %3d props  super=%-18s placed=%-5d saved=%d"
                      % ("  " * depth, r["class"], r["package"], states, funcs,
                         int(r["properties"]), r["super"], actors.get(r["class"], 0),
                         saved.get(r["class"], 0)))
                rows.append({"root": root, "depth": depth, "class": r["class"], "package": r["package"],
                             "super": r["super"], "states": states, "functions": funcs,
                             "native_functions": r["native_functions"], "properties": r["properties"],
                             "placed_in_maps": actors.get(r["class"], 0),
                             "in_checkpoints": saved.get(r["class"], 0)})
                if depth + 1 <= args.max_depth:
                    stack.append((r["class"], depth + 1))
        print("  -- %s: %d subclasses, %d states, %d functions (%d native)"
              % (root, total["classes"], total["states"], total["functions"], total["natives"]))

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "class_graph.csv")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["root", "depth", "class", "package", "super", "states",
                                              "functions", "native_functions", "properties",
                                              "placed_in_maps", "in_checkpoints"])
            w.writeheader()
            w.writerows(rows)
        print("\nwrote %s (%d rows)" % (path, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
