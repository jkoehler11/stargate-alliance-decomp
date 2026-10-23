#!/usr/bin/env python3
"""Inventory the functions attributed to the two game-specific modules.

The XBE is mostly engine code; `stargategame` and `stargatevehicles` are the 10 source files that
carry the game's own native code (vehicles, camera effects, game info, bot helpers). This lists them
one per row with the source file the assert came from, plus -- when a Ghidra C export is available --
the decompiled size, callee count and referenced-string count that triage needs.

Usage: game-modules.py [--symbols symbols/xbox_functions.csv] [--index WORK/native/functions.csv]
                       [-o inventory/xbe_game_modules.csv]
"""
import argparse
import csv
import os
import sys

MODULES = ("stargategame", "stargatevehicles")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="symbols/xbox_functions.csv")
    ap.add_argument("--index", help="ExportC index CSV (work/native/functions.csv)")
    ap.add_argument("--overrides", default="symbols/xbe_overrides.csv",
                    help="evidence-based names applied on top of the assert names")
    ap.add_argument("-o", "--out", default="inventory/xbe_game_modules.csv")
    args = ap.parse_args()

    extra = {}
    if args.index:
        if not os.path.isfile(args.index):
            sys.exit(f"index not found: {args.index}")
        with open(args.index, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                extra[row["address"].lower()] = row

    # Overrides replace the assert-derived file name, so they also carry the module/source
    # attribution for the functions they rename; merge them in first so nothing is listed twice.
    overrides = {}
    if args.overrides and os.path.isfile(args.overrides):
        with open(args.overrides, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                overrides[row["address"].lower()] = row

    rows = []
    with open(args.symbols, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            module, _, source = row["name"].partition("_")
            override = overrides.get(row["address"].lower())
            if override:
                module, source = override["module"], override["source_file"].replace(".cpp", "")
            if module not in MODULES:
                continue
            detail = extra.get(row["address"].lower(), {})
            rows.append({
                "address": row["address"],
                "module": module,
                "source_file": source or "?",
                "size": detail.get("size", row["size"]),
                "callees": detail.get("callees", ""),
                "strings": detail.get("strings", ""),
                "decompiled": detail.get("status", ""),
            })
    rows.sort(key=lambda r: (r["module"], r["source_file"], r["address"]))

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["address", "module", "source_file", "size", "callees",
                                           "strings", "decompiled"])
        w.writeheader()
        w.writerows(rows)

    by_source = {}
    for r in rows:
        by_source.setdefault((r["module"], r["source_file"]), []).append(r)
    print(f"{len(rows)} functions in {len(by_source)} source files -> {args.out}")
    for (module, source), items in sorted(by_source.items()):
        print(f"  {module}/{source:<24} {len(items):>2} functions")
    if args.index:
        ok = sum(1 for r in rows if r["decompiled"] == "ok")
        print(f"  decompiled: {ok}/{len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
