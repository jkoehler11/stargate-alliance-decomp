#!/usr/bin/env python3
"""Which functions belong to which module, per function — the prerequisite for per-module work (PLAN 4.5).

`inventory/xbe_functions_by_module.csv` gives counts; PLAN 4.5 needs addresses, because the point of splitting the image
into per-module projects is to hand a module's functions to an exporter instead of re-analysing all 14,957.

    python3 tools/module-split.py                     # writes inventory/function_modules.csv
    python3 tools/module-split.py --lists $WORK/export # also writes <module>.txt for `ExportAt.java @<file>`
    python3 tools/module-split.py --check              # reconciles with the committed rollup (in `make ci`)

Attribution is the same rule `tools/native-summary.py` uses for its counts — a name's prefix when it is one of the twelve
modules that assert in this binary, else the module an evidence override declares — so the two artifacts cannot drift:
`--check` recomputes the per-module counts and fails if they disagree with `inventory/xbe_functions_by_module.csv`.

A module's functions are **not contiguous**: this build orders code per translation unit, so a module shows up as many
runs. The `run` column numbers those runs in address order, which is what makes "export this module in one pass" as fast
as it is: the exporter still takes one address list, but the list is honest about the fragmentation.
"""
import argparse
import csv
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FUN = re.compile(r"^(FUN|sub|thunk_FUN|LAB)_([0-9a-fA-F]+)$")
NATIVE_SLOT = re.compile(r"^Native_\d+$")
KNOWN_MODULES = {"Engine", "Core", "IpDrv", "Gui", "D3DDrv", "XboxDrv", "XboxAudio", "XboxLive",
                 "Gameplay", "Fire", "stargategame", "stargatevehicles"}
COLUMNS = ["address", "name", "size", "module", "attributed_by", "run"]


def load(symbols, overrides):
    over = {}
    if overrides and os.path.isfile(overrides):
        with open(overrides, newline="", encoding="utf-8") as fh:
            over = {r["address"].lower(): r for r in csv.DictReader(fh)}
    rows = []
    with open(symbols, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = row["name"]
            prefix = name.split("_", 1)[0]
            unknown = bool(FUN.match(name))
            module, how = (prefix, "name prefix") if (not unknown and prefix in KNOWN_MODULES) else ("", "")
            o = over.get(row["address"].lower())
            if o and o.get("module") in KNOWN_MODULES:
                module, how = o["module"], "override"
            if not module:
                # The rollup separates the three ways a function can carry a name without a module; mirror it so the
                # reconciliation is exact rather than approximate.
                if name.startswith("exec") and not unknown:
                    module, how = "(dispatch: declared)", "dispatch name"
                elif NATIVE_SLOT.match(name):
                    module, how = "(dispatch: slot only)", "GNatives slot"
                elif not unknown:
                    module, how = "(other named)", "name only"
            rows.append({"address": row["address"], "name": name, "size": int(row["size"]),
                         "module": module or "(unnamed)", "attributed_by": how or "unattributed"})
    rows.sort(key=lambda r: int(r["address"], 16))
    # number the contiguous runs per module (code is ordered per translation unit, so these matter)
    run = {}
    last_end = {}
    for r in rows:
        va = int(r["address"], 16)
        m = r["module"]
        if last_end.get(m) != va:
            run[m] = run.get(m, 0) + 1
        last_end[m] = va + r["size"]
        r["run"] = run[m]
    return rows


def reconcile(rows):
    """{module: [functions, bytes]} recomputed, for comparison with the committed rollup."""
    out = {}
    for r in rows:
        key = r["module"]
        functs, byts = out.get(key, (0, 0))
        out[key] = (functs + 1, byts + r["size"])
    return out


def check(path, rollup):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    problems = []
    if not rows:
        problems.append("no rows")
    for r in rows[:1]:
        if sorted(r) != sorted(COLUMNS):
            problems.append("columns")
    mine = {}
    for r in rows:
        key = r["module"]
        functs, byts = mine.get(key, (0, 0))
        mine[key] = (functs + 1, byts + int(r["size"]))
    committed = {}
    for r in csv.DictReader(open(rollup, encoding="utf-8")):
        committed[r["module"] or "(unnamed)"] = (int(r["functions"]), int(r["bytes"]))
    for module, (functs, byts) in sorted(mine.items()):
        want = committed.get(module)
        if want is None:
            problems.append("%s not in the rollup" % module)
        elif want != (functs, byts):
            problems.append("%s: %d/%d here vs %d/%d in the rollup" % (module, functs, byts, *want))
    for module in committed:
        if module not in mine:
            problems.append("%s in the rollup but not here" % module)
    if problems:
        print("FAIL module-split: " + "; ".join(problems[:5]))
        return 1
    runs = {}
    for r in rows:
        key = r["module"]
        runs[key] = max(runs.get(key, 0), int(r["run"]))
    top = sorted(mine.items(), key=lambda kv: -kv[1][0])[:3]
    print("ok   module-split: %d functions in %d modules (%s); most fragmented: %s"
          % (len(rows), len(mine), ", ".join("%s %d" % (m, f) for m, (f, _b) in top),
             ", ".join("%s in %d runs" % (m, runs[m]) for m, _f in top)))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--symbols", default=os.path.join(ROOT, "symbols", "xbox_functions.csv"))
    ap.add_argument("--overrides", default=os.path.join(ROOT, "symbols", "xbe_overrides.csv"))
    ap.add_argument("--rollup", default=os.path.join(ROOT, "inventory", "xbe_functions_by_module.csv"))
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--lists", help="directory for per-module address lists (use $WORK, never the repo)")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    path = os.path.join(args.out, "function_modules.csv")
    if args.check:
        return check(path, args.rollup)
    rows = load(args.symbols, args.overrides)
    os.makedirs(args.out, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    counts = reconcile(rows)
    for module, (functs, byts) in sorted(counts.items(), key=lambda kv: -kv[1][0]):
        print("%-18s %5d functions %9d bytes" % (module, functs, byts))
    if args.lists:
        os.makedirs(args.lists, exist_ok=True)
        per = {}
        for r in rows:
            # only real modules get a list; the three name-only buckets are not things to export as a module
            if r["module"].startswith("("):
                continue
            per.setdefault(r["module"], []).append(r["address"])
        for module, addrs in per.items():
            with open(os.path.join(args.lists, module + ".txt"), "w", encoding="utf-8") as fh:
                fh.write("\n".join(addrs) + "\n")
        print("wrote %d address lists to %s" % (len(per), args.lists))
    print("%d functions -> %s" % (len(rows), path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
