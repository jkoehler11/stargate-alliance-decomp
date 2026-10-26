#!/usr/bin/env python3
"""How much of the script tree the shipped content actually reaches (PLAN 5.6, cut-content evidence).

`docs/CUT_CONTENT.md` argues from missions, audio and maps. This answers the same question from the other
end: for every one of the 2,319 classes, is anything in the dump built out of it? Four evidence sources,
all committed:

  * `inventory/map_actors.csv`      — the class is placed in a `.unr` (and how often, per map kind)
  * `inventory/squad_scripts.csv`   — a map names it as a squad/encounter script (spawner convention)
  * `inventory/checkpoint_summary.csv` — the class appears in a `.sgs` snapshot, so a running game held one
  * `inventory/classes.csv`         — the class has a subclass that is reached by one of the above

A class reached by nothing is *not* automatically cut content: it can be an abstract base the content never
instantiates, a native shell, or a class only reachable through a default property this repository does not
publish (defaults values are game data). That is why the report counts the classes and names the mechanism
rather than calling each one cut.

    python3 tools/class-reach.py -o inventory     # writes inventory/class_reach.csv
    python3 tools/class-reach.py --check          # what `make ci` runs
"""
import argparse
import collections
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MP_PREFIXES = ("DM-", "CTF-", "SGCTF-")


def rows(name):
    path = os.path.join(ROOT, "inventory", name)
    if not os.path.isfile(path):
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def multiplayer(map_name):
    return any(map_name.startswith(p) for p in MP_PREFIXES)


def build():
    classes = rows("classes.csv")
    children = collections.defaultdict(set)
    for r in classes:
        if r["super"]:
            children[r["super"]].add(r["class"])

    placed = collections.Counter()
    mission = collections.Counter()
    mp = collections.Counter()
    maps_by_class = collections.defaultdict(set)
    for r in rows("map_actors.csv"):
        name, cls, count = r["map"], r["class"], int(r["count"])
        placed[cls] += count
        maps_by_class[cls].add(name)
        (mp if multiplayer(name) else mission)[cls] += count

    squads = collections.Counter()
    for r in rows("squad_scripts.csv"):
        squads[r["squad_script"]] += int(r.get("spawners") or 1)

    checkpoints = collections.Counter()
    for r in rows("checkpoint_summary.csv"):
        checkpoints[r["class"]] += int(r.get("instances") or 0)

    out = []
    for r in classes:
        cls = r["class"]
        direct = "placed" if placed[cls] else ("squad-script" if squads[cls] else
                                               ("checkpoint" if checkpoints[cls] else ""))
        reach = direct
        if not reach:
            # A base class is reached through its subclasses: the content instantiates one of them and the
            # base's script is what runs.
            stack, seen, base = [cls], {cls}, False
            while stack and not base:
                cur = stack.pop()
                for kid in children.get(cur, ()):
                    if kid in seen:
                        continue
                    seen.add(kid)
                    if placed[kid] or squads[kid] or checkpoints[kid]:
                        base = True
                        break
                    stack.append(kid)
            reach = "base-of-content" if base else "unreferenced"
        kind = "mp" if placed[cls] and not mission[cls] else ("mission" if mission[cls] else "")
        out.append({"package": r["package"], "class": cls, "super": r["super"],
                    "native_only": r["native_only"], "subclasses": len(children.get(cls, ())),
                    "placed_actors": placed[cls], "mission_actors": mission[cls], "mp_actors": mp[cls],
                    "maps": len(maps_by_class.get(cls, ())), "squad_refs": squads[cls],
                    "checkpoint_instances": checkpoints[cls], "placed_kind": kind, "reach": reach})
    return out


COLUMNS = ["package", "class", "super", "native_only", "subclasses", "placed_actors", "mission_actors",
           "mp_actors", "maps", "squad_refs", "checkpoint_instances", "placed_kind", "reach"]


def write(rows_out, outdir):
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "class_reach.csv")
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows_out:
            w.writerow(r)
    print("wrote %s" % path)
    return path


def summarise(rows_out):
    tally = collections.Counter(r["reach"] for r in rows_out)
    total = len(rows_out)
    for key in ("placed", "squad-script", "checkpoint", "base-of-content", "unreferenced"):
        print("%-16s %5d  %5.1f%%" % (key, tally[key], 100.0 * tally[key] / total if total else 0.0))
    reached = total - tally["unreferenced"]
    print("reached by content: %d of %d (%.1f%%)" % (reached, total, 100.0 * reached / total if total else 0))
    unref = [r for r in rows_out if r["reach"] == "unreferenced"]
    by_pkg = collections.Counter(r["package"] for r in unref)
    print("unreferenced by package: %s" % by_pkg.most_common(8))
    return tally


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--check", action="store_true", help="diff a fresh run against the committed CSV")
    args = ap.parse_args()
    rows_out = build()
    if args.check:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            new = open(write(rows_out, tmp), "rb").read()
        old = open(os.path.join(ROOT, "inventory", "class_reach.csv"), "rb").read()
        if new != old:
            for n, (a, b) in enumerate(zip(new.splitlines(), old.splitlines()), start=1):
                if a != b:
                    sys.exit("inventory/class_reach.csv drifted at line %d: %r != %r" % (n, a, b))
            sys.exit("inventory/class_reach.csv drifted (length %d vs %d)" % (len(new), len(old)))
        print("ok   class-reach: inventory/class_reach.csv reproduces")
        return 0
    write(rows_out, args.out)
    summarise(rows_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
