#!/usr/bin/env python3
"""Which functions belong to which module, per function -- the prerequisite for per-module work (PLAN 4.5).

`inventory/xbe_functions_by_module.csv` gives counts; PLAN 4.5 needs addresses, because the point of splitting the image
into per-module projects is to hand a module's functions to an exporter instead of re-analysing all 14,957.

    python3 tools/module-split.py                      # writes inventory/function_modules.csv
    python3 tools/module-split.py --plan inventory/module_export_plan.csv   # + the per-module export plan
    python3 tools/module-split.py --lists $WORK/export  # also writes <module>.txt for `ExportAt.java @<file>`
    python3 tools/module-split.py --check              # reconciles both artifacts (in `make ci`)

Attribution is the same rule `tools/native-summary.py` uses for its counts -- a name's prefix when it is one of the twelve
modules that assert in this binary, else the module an evidence override declares -- so the two artifacts cannot drift:
`--check` recomputes the per-module counts and fails if they disagree with `inventory/xbe_functions_by_module.csv`.

**The lists are the union of the two attribution artifacts, and that is the point of the plan.** The name/override rule
reaches 1,348 functions; `inventory/function_module_attribution.csv` (tools/module-attribution.py) adds 4,715 whose
module came from address locality, measured at 87.9% on held-out asserts. A list built from the assert rule alone is a
sample of a module; built from the union it is the file. `inventory/module_export_plan.csv` is what that union amounts
to per module -- functions, asserted vs propagated, bytes, contiguous runs, first and last address -- so a per-module
`ExportAt.java` pass can be sized before it is paid for.

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
PLAN_COLUMNS = ["module", "functions", "asserted", "propagated", "bytes", "runs", "first", "last"]


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


def is_module(label):
    """A real module label: the three name-only buckets and `(unnamed)` are not modules."""
    return bool(label) and not label.startswith("(")


def load_attribution(path):
    """{address: (module, method)} for the rows the locality rule propagated, from function_module_attribution.csv."""
    out = {}
    if not path or not os.path.isfile(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if is_module(r["module"]) and r["method"] == "propagated":
                out[r["address"].lower()] = (r["module"], r["evidence"])
    return out


def union(rows, attribution):
    """Every function with a module, from either artifact. `source` says which rule put it there."""
    out = []
    for r in rows:
        addr = r["address"].lower()
        if is_module(r["module"]):
            source = "override" if r["attributed_by"] == "override" else "assert"
            out.append({"address": r["address"], "name": r["name"], "size": r["size"],
                        "module": r["module"], "source": source, "evidence": ""})
        elif addr in attribution:
            module, evidence = attribution[addr]
            out.append({"address": r["address"], "name": r["name"], "size": r["size"],
                        "module": module, "source": "propagated", "evidence": evidence})
    out.sort(key=lambda r: int(r["address"], 16))
    return out


def plan_rows(items):
    """One row per module: what an `ExportAt.java` pass over that module's list would cover."""
    per = {}
    last_end = {}
    for r in items:
        va = int(r["address"], 16)
        rec = per.setdefault(r["module"], {"module": r["module"], "functions": 0, "asserted": 0, "propagated": 0,
                                           "bytes": 0, "runs": 0, "first": r["address"], "last": r["address"],
                                           "_end": va})
        rec["functions"] += 1
        rec["asserted" if r["source"] in ("assert", "override") else "propagated"] += 1
        rec["bytes"] += int(r["size"])
        if last_end.get(r["module"]) != va:
            rec["runs"] += 1
        last_end[r["module"]] = va + int(r["size"])
        rec["last"] = r["address"]
        rec["_end"] = va + int(r["size"])
    rows = sorted(per.values(), key=lambda r: (-r["functions"], r["module"]))
    for r in rows:
        r.pop("_end")
    return rows


def reconcile(rows):
    """{module: [functions, bytes]} recomputed, for comparison with the committed rollup."""
    out = {}
    for r in rows:
        key = r["module"]
        functs, byts = out.get(key, (0, 0))
        out[key] = (functs + 1, byts + r["size"])
    return out


def check(path, rollup, plan_path="", attribution_path=""):
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
    # The export plan is derived from this file plus the attribution artifact: recompute it and compare exactly, so a
    # stale plan (or an attribution artifact that moved) fails here instead of being quoted in a doc.
    want_plan = []
    if plan_path and os.path.isfile(plan_path):
        want_plan = plan_rows(union(rows, load_attribution(attribution_path)))
        got_plan = list(csv.DictReader(open(plan_path, encoding="utf-8")))
        if got_plan and sorted(got_plan[0]) != sorted(PLAN_COLUMNS):
            problems.append("plan columns")
        for a, b in zip(want_plan, got_plan):
            diff = [k for k in PLAN_COLUMNS if str(a[k]) != b.get(k)]
            if diff:
                problems.append("plan row %s differs on %s" % (a["module"], ",".join(diff)))
                break
        if len(want_plan) != len(got_plan):
            problems.append("plan has %d rows, recomputed %d" % (len(got_plan), len(want_plan)))
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
    if want_plan:
        covered = sum(r["functions"] for r in want_plan)
        print("ok   module-export-plan: %d functions in %d modules (%d of %d, %.2f%%); largest %s %d, %d runs"
              % (covered, len(want_plan), covered, len(rows), 100.0 * covered / len(rows),
                 want_plan[0]["module"], want_plan[0]["functions"], want_plan[0]["runs"]))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--symbols", default=os.path.join(ROOT, "symbols", "xbox_functions.csv"))
    ap.add_argument("--overrides", default=os.path.join(ROOT, "symbols", "xbe_overrides.csv"))
    ap.add_argument("--rollup", default=os.path.join(ROOT, "inventory", "xbe_functions_by_module.csv"))
    ap.add_argument("--attribution", default=os.path.join(ROOT, "inventory", "function_module_attribution.csv"),
                    help="the locality attribution the lists add to the assert rule ('' to use the asserts only)")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--plan", help="write the per-module export plan here (the committed artifact)")
    ap.add_argument("--lists", help="directory for per-module address lists (use $WORK, never the repo)")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    path = os.path.join(args.out, "function_modules.csv")
    plan_path = args.plan or os.path.join(args.out, "module_export_plan.csv")
    if args.check:
        return check(path, args.rollup, plan_path, args.attribution)
    rows = load(args.symbols, args.overrides)
    os.makedirs(args.out, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    counts = reconcile(rows)
    for module, (functs, byts) in sorted(counts.items(), key=lambda kv: -kv[1][0]):
        print("%-18s %5d functions %9d bytes" % (module, functs, byts))
    print("%d functions -> %s" % (len(rows), path))
    if not args.plan:
        return 0
    items = union(rows, load_attribution(args.attribution))
    plan = plan_rows(items)
    with open(plan_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PLAN_COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(plan)
    covered = sum(r["functions"] for r in plan)
    print("export plan: %d functions in %d modules (%d of %d, %.2f%%), %d asserted, %d propagated"
          % (covered, len(plan), covered, len(rows), 100.0 * covered / len(rows),
             sum(r["asserted"] for r in plan), sum(r["propagated"] for r in plan)))
    for r in plan:
        print("  %-18s %5d functions %5d asserted %5d propagated %4d runs %9d bytes"
              % (r["module"], r["functions"], r["asserted"], r["propagated"], r["runs"], r["bytes"]))
    print("wrote %s" % plan_path)
    if args.lists:
        os.makedirs(args.lists, exist_ok=True)
        per = {}
        for r in items:
            per.setdefault(r["module"], []).append(r["address"])
        for module, addrs in per.items():
            with open(os.path.join(args.lists, module + ".txt"), "w", encoding="utf-8") as fh:
                fh.write("\n".join(addrs) + "\n")
        print("wrote %d address lists to %s" % (len(per), args.lists))
    return 0


if __name__ == "__main__":
    sys.exit(main())
