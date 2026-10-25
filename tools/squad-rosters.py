#!/usr/bin/env python3
"""Read the squad rosters: the `SQUAD_*` classes maps and spawners name.

`docs/systems/AI.md` ends its chain at "the roster is a class default". The defaults reader
(`tools/class-defaults.py`) can now resolve those blocks, so this is the roster itself: for each
`SquadRoster` subclass, the authoring data that decides what a squad contains and how it behaves.

    python3 tools/squad-rosters.py DUMP/System -o inventory
    python3 tools/squad-rosters.py DUMP/System --class SQUAD_Anubis --walk

Values are counts and names only -- the roster's member array is serialised inline and is not decoded, which
the doc states rather than hides.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name, filename):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def text(value):
    """A UStrProperty value: one byte of length, then the characters, then a NUL."""
    if not isinstance(value, str):
        return value
    return value.split("\x00")[0][1:] if value[:1] < " " and value[:1] != "" else value.split("\x00")[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("system_dir", help="the dump's System directory")
    ap.add_argument("--package", default="StargateContent.u")
    ap.add_argument("--root", default="SquadRoster", help="root class whose subclasses are rosters")
    ap.add_argument("--class", dest="one", help="one class, with its byte walk")
    ap.add_argument("--walk", action="store_true")
    ap.add_argument("-o", "--out", help="write squad_rosters.csv here")
    args = ap.parse_args()

    cd = load("class_defaults", "class-defaults.py")
    ps = load("prop_stream", "prop-stream.py")

    supers = cd.super_map()
    props = cd.declared_properties()
    pkg = Package(os.path.join(args.system_dir, args.package))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()

    def inherited(cls):
        """Declared properties of the class and every ancestor, which is what a defaults block may set."""
        out, seen = set(), set()
        while cls and cls not in seen:
            seen.add(cls)
            out |= props.get(cls, set())
            cls = supers.get(cls)
        return out

    def roster_classes():
        return [c for c, s in supers.items() if s == args.root]

    def read(cls):
        exp = next((e for e in pkg.exports
                    if pkg.name(e["name"]) == cls and pkg.class_name_of(e) == "Class"), None)
        if exp is None:
            return None, "no class export"
        data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
        cands, data = cd.candidates(pkg, ps, exp)
        best = cd.choose(cands, inherited(cls))
        if best is None:
            return None, "no default block"
        _score, count, start, _names = best
        block, _end, _reason = ps.read_block(data[start:], pkg, True)
        # The members array is serialised as one count byte and then two bytes per member, so the count is
        # the payload's first byte. Measured from seven distinct payload sizes (3 bytes -> 1 member,
        # 33 bytes -> 16), not assumed.
        members = next((x for x in block if x["name"] == "Members"), None)
        if members is not None:
            off = start
            while off < len(data):
                nm, after = ps.read_index(data, off)
                if nm == 0:
                    break
                _e, after_value = ps.read_value(data, after, pkg, False)
                if pkg.name(nm) == "Members":
                    size = data[after + 1]          # the size byte that follows the type byte
                    members["members"] = data[after + 2] if size else 0
                    break
                off = after_value
        return {"start": start, "count": count, "props": block, "data": data}, ""

    def field(block, name):
        return next((p for p in block if p["name"] == name), None)

    classes = [args.one] if args.one else sorted(roster_classes())
    if args.one and args.walk:
        got, why = read(args.one)
        if got is None:
            print("%s: %s" % (args.one, why))
            return 1
        print("%s: %d defaults at +%#x" % (args.one, got["count"], got["start"]))
        data, off = got["data"], got["start"]
        while off < len(data):
            name, after = ps.read_index(data, off)
            if name == 0:
                break
            entry, after_value = ps.read_value(data, after, pkg, True)
            print("   %-22s %-10s size=%-4s %s"
                  % (pkg.name(name), entry["type"], entry.get("size"), data[after:after_value].hex(" ")))
            off = after_value

    rows, stats = [], collections.Counter()
    for cls in classes:
        got, why = read(cls)
        if got is None:
            rows.append([cls, "", "", "", "", 0, 0, why])
            stats[why] += 1
            continue
        block = got["props"]
        rs, pt, mem = field(block, "RosterString"), field(block, "PawnTag"), field(block, "Members")
        rows.append([cls,
                     text(rs["value"]) if rs else "",
                     pt["value"] if pt else "",
                     mem.get("size", "") if mem else "",
                     mem.get("members", "") if mem else "",
                     len(block), got["start"], ""])
        stats["read"] += 1
        if pt:
            stats["pawnTag"] += 1
    rows.sort()

    print("%-30s %-4s %-7s %-22s %-22s %s"
          % ("class", "defs", "members", "roster string", "pawn tag", "members bytes"))
    for r in rows[:12]:
        print("%-30s %-4s %-7s %-22s %-22s %s" % (r[0], r[5], r[4], r[1][:22], r[2][:22], r[3]))
    print("...")
    print("classes: %d   read: %d   with PawnTag: %d   %s"
          % (len(rows), stats["read"], stats["pawnTag"],
             ", ".join("%s %d" % (k, v) for k, v in stats.items() if k not in ("read", "pawnTag")) or "no failures"))

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "squad_rosters.csv")
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["class", "roster_string", "pawn_tag", "members_bytes", "members",
                        "defaults", "block_start", "note"])
            w.writerows(rows)
        print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
