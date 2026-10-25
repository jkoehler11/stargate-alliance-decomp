#!/usr/bin/env python3
"""List the exports that belong to a class, and how much body each one has.

The export table gives every object a class, an outer and a byte range. That is enough to answer a question
the class graph could not: **which functions does this class declare, and which of them carry bytecode?** A
script function's body is bytecode in the package; a native function has an index and (in these packages) no
script body, so its serialised size is small. Size is therefore a proxy for "native vs script" -- a proxy,
not a proof, and this tool says so rather than labelling them.

    python3 tools/pkg-exports.py PKG.u --class SGOrder
    python3 tools/pkg-exports.py PKG.u --class SGOrder -o inventory     # + inventory/class_exports.csv

Reads only the package; needs no dump beyond the file itself.
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("package")
    ap.add_argument("--class", dest="cls", required=True, help="class whose children to list")
    ap.add_argument("--all", action="store_true", help="list every export whose outer is that class")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()

    pkg = Package(args.package)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()

    target = None
    for exp in pkg.exports:
        if pkg.name(exp["name"]) == args.cls and pkg.class_name_of(exp) == "Class":
            target = exp
            break
    if target is None:
        sys.exit("no class export named %s in %s" % (args.cls, args.package))

    ti = target["index"] + 1  # exports refer to each other by 1-based index
    kids = [e for e in pkg.exports if e["outer"] == ti]
    print("%s: export #%d, %d children" % (args.cls, target["index"] + 1, len(kids)))
    if not kids:
        return 1

    rows = []
    for e in sorted(kids, key=lambda e: -e["size"]):
        cls = pkg.class_name_of(e)
        rows.append({"class": args.cls, "export": e["index"] + 1, "kind": cls,
                     "name": pkg.name(e["name"]), "size": e["size"], "offset": "0x%x" % e["offset"]})
        print("  %-22s %-14s size=%-6d offset=%#010x" % (pkg.name(e["name"]), cls, e["size"], e["offset"]))
    by_kind = {}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    print("  kinds: %s" % dict(sorted(by_kind.items(), key=lambda kv: -kv[1])))
    tiny = [r for r in rows if r["kind"] == "Function" and r["size"] <= 8]
    print("  functions with <= 8 bytes of body: %d of %d"
          % (len(tiny), len([r for r in rows if r["kind"] == "Function"])))
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "class_exports.csv")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["class", "export", "kind", "name", "size", "offset"])
            w.writeheader()
            w.writerows(rows)
        print("wrote %s (%d rows)" % (path, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
