#!/usr/bin/env python3
"""Read a class's default properties, and follow them to what the class spawns (PLAN 5.2 remainder, 4.7).

The T3D work stopped at "a spawner names its squad script, and the script's roster is a class default"
(`docs/T3D.md`). This reads that default: the property block lives inside the *class* export, after the class's
own fields, and it is located the same way an actor's block is — by requiring it to end where the export ends
and to consist of resolving names.

    python3 tools/pkg-defaults.py DUMP/System --class SGOrder
    python3 tools/pkg-defaults.py DUMP/System --squads inventory/squad_scripts.csv -o inventory

`--squads` follows the chain the maps started: for every squad script named by a spawner, read its defaults and
report the object references that point at a class. Those references are the roster, and the classes they name
are the bots the mission will meet.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402


def load_prop_stream():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prop-stream.py")
    spec = importlib.util.spec_from_file_location("prop_stream", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def iter_packages(path):
    if os.path.isdir(path):
        for f in sorted(os.listdir(path)):
            if f.lower().endswith(".u"):
                pkg = Package(os.path.join(path, f))
                pkg.read_names()
                pkg.read_imports()
                pkg.read_exports()
                yield os.path.splitext(f)[0], pkg
    else:
        pkg = Package(path)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        yield os.path.splitext(os.path.basename(path))[0], pkg


def find_class(pkg, name):
    for exp in pkg.exports:
        if pkg.class_name_of(exp) == "Class" and pkg.name(exp["name"]) == name:
            return exp
    return None


def class_defaults(pkg, mod, exp, want_values=True):
    """Locate the default-property block inside a class export and return its properties."""
    data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    best = None
    for start in range(0, min(len(data), 512)):
        try:
            props, end, reason = mod.read_block(data[start:], pkg, False)
        except Exception:
            continue
        if reason != "None" or len(props) < 2:
            continue
        if any(p["name"] == "?" for p in props):
            continue
        if start + end != len(data):
            continue
        if best is None or len(props) > best[0]:
            best = (len(props), start)
    if not best:
        return None, None
    props, _end, _reason = mod.read_block(data[best[1]:], pkg, want_values)
    return best[1], props


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="a System/ directory or one package")
    ap.add_argument("--class", dest="cls")
    ap.add_argument("--squads", help="CSV of (map, squad_script, spawners) to follow")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()

    mod = load_prop_stream()
    if args.squads:
        scripts = sorted({r["squad_script"] for r in csv.DictReader(open(args.squads))
                          if r["squad_script"] not in ("", "?")})
        print("squad scripts to resolve: %d" % len(scripts))
        found = {}
        for pkg_name, pkg in iter_packages(args.path):
            for name in scripts:
                exp = find_class(pkg, name)
                if not exp:
                    continue
                start, props = class_defaults(pkg, mod, exp)
                if not props:
                    continue
                refs = [(p["name"], p["value"]) for p in props
                        if p.get("type") == "Object" and isinstance(p.get("value"), str) and "'" in p["value"]]
                found[name] = {"package": pkg_name, "props": len(props), "refs": refs}
        print("resolved: %d of %d" % (len(found), len(scripts)))
        rows = []
        for name, info in sorted(found.items()):
            targets = sorted({v.split("'")[0] for _p, v in info["refs"]})
            print("%-32s %2d props  %2d object refs  targets=%s"
                  % (name, info["props"], len(info["refs"]), targets[:4]))
            for prop, value in info["refs"]:
                rows.append({"squad_script": name, "package": info["package"],
                             "property": prop, "target": value})
        if args.out:
            os.makedirs(args.out, exist_ok=True)
            path = os.path.join(args.out, "squad_script_defaults.csv")
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["squad_script", "package", "property", "target"])
                w.writeheader()
                w.writerows(rows)
            print("wrote %s (%d rows)" % (path, len(rows)))
        return 0

    for pkg_name, pkg in iter_packages(args.path):
        exp = find_class(pkg, args.cls) if args.cls else None
        if not exp:
            continue
        start, props = class_defaults(pkg, mod, exp, want_values=True)
        if not props:
            print("%s: no defaults block found" % args.cls)
            return 1
        print("%s (%s): %d default properties at +0x%x" % (args.cls, pkg_name, len(props), start))
        for p in props:
            if "value" in p and p["value"] is not None:
                print("   %-24s %-8s %s" % (p["name"], p["type"], p["value"]))
        return 0
    print("%s: not found" % args.cls)
    return 1


if __name__ == "__main__":
    sys.exit(main())
