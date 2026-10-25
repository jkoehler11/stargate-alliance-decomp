#!/usr/bin/env python3
"""Resolve a class's default-property block by what the class declares, not just by where the block ends.

The previous attempt (`docs/CLASS_DEFAULTS.md`) located the block by the boundary rule alone and hit an
ambiguity: two candidate starts two bytes apart both end at the export end, so `Description` decoded as
`Str BeginState`. The class table already knows which properties a class declares
(`inventory/class_properties.csv`), so the ambiguity can be settled semantically: a candidate whose property
names are all declared by the class (or inherited from its supers) is the block, and one that invents a name
like `BeginState` is not.

    python3 tools/class-defaults.py DUMP/System --class SGOrder
    python3 tools/class-defaults.py DUMP/System --sweep -o inventory
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


def load_prop_stream():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prop-stream.py")
    spec = importlib.util.spec_from_file_location("prop_stream", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def super_map(path=os.path.join(ROOT, "inventory", "classes.csv")):
    """class -> superclass, taken from the whole tree rather than one package.

    Following supers within the package being read stops at the first class that extends a class from
    another package (StargateGame classes extend Engine ones), which truncated the inherited property set and
    made correct blocks look undeclared.
    """
    out = {}
    if os.path.isfile(path):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                out[r["class"]] = r["super"]
    return out


def declared_properties(path=os.path.join(ROOT, "inventory", "class_properties.csv")):
    """class -> set of property names it declares, plus the same set for all its ancestors."""
    by_class = collections.defaultdict(set)
    if not os.path.isfile(path):
        return by_class
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            by_class[r["class"]].add(r["property"])
    return by_class


def candidates(pkg, mod, exp, limit=512):
    data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    out = []
    for start in range(0, min(len(data), limit)):
        try:
            props, end, reason = mod.read_block(data[start:], pkg, False)
        except Exception:
            continue
        if reason != "None" or len(props) < 2:
            continue
        if any(p["name"] == "?" for p in props):
            continue
        if start + end > len(data):
            continue
        trailer = len(data) - (start + end)
        if trailer > 64:
            # A class export can carry data after its defaults (a component table, for one). Requiring an
            # exact ending threw away every such class; a bounded trailer keeps them.
            continue
        out.append((len(props), start, [p["name"] for p in props], trailer))
    return out, data


def choose(cands, declared):
    """Prefer the candidate whose names the class declares, then the longest, then the smallest trailer."""
    best = None
    for count, start, names, trailer in cands:
        known = sum(1 for n in names if n in declared)
        score = (known / float(count), count, -trailer)
        if best is None or score > best[0]:
            best = (score, count, start, names)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--class", dest="cls")
    ap.add_argument("--sweep", action="store_true", help="every class in the package")
    ap.add_argument("--walk", action="store_true",
                    help="print the raw byte walk of the chosen block (type byte, size, consumed bytes)")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()

    mod = load_prop_stream()
    declared = declared_properties()
    pkg = Package(args.path)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    # ancestor closure from the committed tree, so cross-package inheritance is followed
    supers = super_map()

    def known_for(cls):
        out, seen, cur = set(), set(), cls
        while cur and cur not in seen:
            seen.add(cur)
            out |= declared.get(cur, set())
            cur = supers.get(cur)
        return out

    targets = [e for e in pkg.exports
               if pkg.class_name_of(e) == "Class" and (args.sweep or pkg.name(e["name"]) == args.cls)]
    if not targets:
        sys.exit("no matching class")

    rows, clean, ambiguous, none_found = [], 0, 0, 0
    for exp in targets:
        cls = pkg.name(exp["name"])
        known = known_for(cls)
        cands, data = candidates(pkg, mod, exp)
        if not cands:
            none_found += 1
            continue
        score, count, start, names = choose(cands, known)
        if score[0] == 1.0:
            clean += 1
        elif len(cands) > 1:
            ambiguous += 1
        rows.append({"class": cls, "properties": count, "start": "0x%x" % start,
                     "declared_fraction": "%.2f" % score[0]})
        if args.walk:
            # Byte-level walk: every property with its info byte and how many bytes its value consumed.
            # This is what showed that a Bool writes no size field: the byte after its info byte is the next
            # property's name index. Kept as a mode because it is the only way to settle an alignment question.
            off = start
            while off < len(data):
                name, after_name = mod.read_index(data, off)
                if name == 0:
                    break
                info = data[after_name]
                _entry, after_value = mod.read_value(data, after_name, pkg, False)
                print("   %-24s nameIdx=%-5d info=%#04x type=%-8s consumed=%d"
                      % (pkg.name(name), name, info, mod.TYPES.get(info & 0x0F),
                         after_value - after_name))
                off = after_value
        if args.cls:
            props, _end, _reason = mod.read_block(data[start:], pkg, True)
            print("%s: %d defaults at +0x%x, %.0f%% declared by the class"
                  % (cls, len(props), start, 100 * score[0]))
            for p in props:
                if p.get("value") is not None:
                    print("   %-24s %-8s %s" % (p["name"], p["type"], p["value"]))
            print("   candidates considered: %d" % len(cands))

    if args.sweep:
        print("classes: %d, blocks found: %d, none: %d" % (len(targets), len(rows), none_found))
        print("  every property declared by the class: %d" % clean)
        print("  partial match, more than one candidate: %d" % ambiguous)
    if args.out and rows:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "class_defaults.csv")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["class", "properties", "start", "declared_fraction"])
            w.writeheader()
            w.writerows(rows)
        print("wrote %s (%d rows)" % (path, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
