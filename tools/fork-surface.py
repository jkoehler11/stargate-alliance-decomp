#!/usr/bin/env python3
"""Map the fork's script surface: every class a Stargate package inserts directly onto a stock UE2.5 base.

PLAN 4.8 asks what Perception changed against stock Unreal Engine 2.5. On the script side that question
has a precise answer: the classes whose `super` lives in a stock package (`Engine`, `Core`, `GamePlay`,
`GUI`, `UWindow`, ...) are exactly where the fork attaches to the engine. This measures that set.

    python3 tools/fork-surface.py                 # tables to stdout
    python3 tools/fork-surface.py -o inventory     # + inventory/fork_surface.csv

Reads only `inventory/classes.csv`, so it needs no dump and can gate a PR.
"""
import argparse
import collections
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOCK = {"Engine", "Core", "GamePlay", "GUI", "UWindow", "IpDrv", "Fire", "UnrealEd", "Editor",
         "UPreview", "AmbientCreatures"}
FORK = {"StargateGame", "StargateContent", "StargateVehicles", "StargateMenus",
        "Stargateeffects", "Sharpeffects", "UDebugMenu"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--classes", default=os.path.join(ROOT, "inventory", "classes.csv"))
    ap.add_argument("-o", "--out")
    ap.add_argument("--top", type=int, default=18)
    args = ap.parse_args()

    with open(args.classes, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("empty class table: %s" % args.classes)

    insert = [r for r in rows if r["package"] in FORK and r.get("package_of_super") in STOCK]
    if not insert:
        sys.exit("no fork insertion points found -- is %s the stock/fork class table?" % args.classes)

    print("classes in the script tree: %d" % len(rows))
    print("fork insertion points (fork class, stock super): %d (%.1f%% of the tree)"
          % (len(insert), 100.0 * len(insert) / len(rows)))
    print("\nby fork package")
    for p, n in collections.Counter(r["package"] for r in insert).most_common():
        funcs = sum(int(r["functions"]) for r in insert if r["package"] == p)
        print("  %-18s %3d classes  %4d functions" % (p, n, funcs))

    bases = collections.Counter((r["super"], r["package_of_super"]) for r in insert)
    print("\ndistinct stock bases touched: %d" % len(bases))
    print("most-subclassed bases")
    for (name, pkg), n in bases.most_common(args.top):
        print("  %-28s %-10s %3d" % (name, pkg, n))

    natives = [r for r in insert if int(r["native_functions"]) > 0]
    print("\ninsertion classes carrying native (ucpp) functions: %d" % len(natives))
    for r in natives[:10]:
        print("  %-26s %-16s %d native of %s functions"
              % (r["class"], r["package"], int(r["native_functions"]), r["functions"]))

    with_props = sum(1 for r in insert if int(r["properties"]) > 0)
    print("\ninsertion classes that add properties: %d of %d" % (with_props, len(insert)))
    print("insertion classes with no functions and no properties (data-only renames): %d"
          % sum(1 for r in insert if int(r["functions"]) == 0 and int(r["properties"]) == 0))

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "fork_surface.csv")
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["fork_package", "class", "super", "super_package", "functions",
                        "native_functions", "properties", "states"])
            for r in sorted(insert, key=lambda r: (r["package"], r["class"].lower())):
                w.writerow([r["package"], r["class"], r["super"], r["package_of_super"],
                            r["functions"], r["native_functions"], r["properties"], r["states"]])
        print("\nwrote %s (%d rows)" % (path, len(insert)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
