#!/usr/bin/env python3
"""Who calls a function: census one address or symbol name from the Ghidra evidence export.

An unnamed helper is usually the reason a whole module reads as `FUN_*` soup, and the way to name it
is to look at who calls it and with what. This prints the call sites and groups callers by the
attribution they carry (assert-derived `<Module>_<File>`, or unnamed).

Input is the export written by tools/ghidra/ghidra_scripts/ExportEvidence.java, so run `make ghidra`
first. `--target` takes an address (`0x00058a30`) or a final Ghidra name.

Usage:
    call-census.py --target FUN_00058a30 [--evidence build/ghidra/evidence] [--names]
"""
import argparse
import collections
import csv
import os
import re
import sys

MODULES = {"Core", "Engine", "Gui", "IpDrv", "D3DDrv", "XboxDrv", "XboxAudio", "XboxLive",
           "Gameplay", "Fire", "stargategame", "stargatevehicles"}
NAMED = re.compile(r"^([A-Za-z][A-Za-z0-9]*)_([A-Za-z0-9_]+)$")


def read(path, columns):
    rows = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            parts += [""] * (columns - len(parts))
            rows.append(parts[:columns])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, help="address or final function name")
    ap.add_argument("--evidence", default=os.path.join("build", "ghidra", "evidence"))
    ap.add_argument("--names", action="store_true", help="list every call site")
    args = ap.parse_args()

    for name in ("funcs.tsv", "calls.tsv"):
        if not os.path.isfile(os.path.join(args.evidence, name)):
            sys.exit("%s missing -- run `make ghidra` first" % os.path.join(args.evidence, name))

    funcs = {a: (n, int(s)) for a, n, s in ((r[0], r[1], r[2]) for r in
                                            read(os.path.join(args.evidence, "funcs.tsv"), 6))}
    target = args.target
    if not target.startswith("0x"):
        matches = [a for a, (n, _) in funcs.items() if n == target]
        if len(matches) != 1:
            sys.exit("target %r matched %d functions -- pass an address" % (target, len(matches)))
        target = matches[0]

    sites = read(os.path.join(args.evidence, "calls.tsv"), 4)
    hits = [(caller, site) for caller, site, callee, _n in sites if callee == target]
    callers = collections.Counter(caller for caller, _ in hits)
    by_attr = collections.Counter()
    for caller, count in callers.items():
        name = funcs.get(caller, ("", 0))[0]
        m = NAMED.match(name)
        by_attr[m.group(1) + "/" + m.group(2) if m and m.group(1) in MODULES
                else (name if not name.startswith(("FUN_", "Native_")) else "unnamed")] += count

    print("%s (%s): %d call sites from %d distinct callers"
          % (target, funcs.get(target, ("?", 0))[0], len(hits), len(callers)))
    for label, count in by_attr.most_common(10):
        print("  %-28s %d" % (label, count))
    if args.names:
        for caller, site in sorted(hits):
            print("  %s -> %s" % (site, funcs.get(caller, (caller, 0))[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
