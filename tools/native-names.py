#!/usr/bin/env python3
"""Name the natives the shipped packages declare, by the index each declaration carries.

Bytecode calls natives by index, and `inventory/natives.csv` records every native function the
packages declare plus the index the package serialised for it. Two things follow, and only one of
them is a name:

- **declared** -- one distinct `Class.Function` claims the index. `exec<Function>` is the engine's
  spelling for a native implementation (`docs/CONVENTIONS.md`); a name two indices would share takes
  the class prefix instead, so the common case stays `execTrace`, not `execActor_Trace`.
- **ambiguous** -- two different functions claim one index. A conflict means the mapping is not
  understood yet, so it is recorded empty rather than guessed.

What this tool deliberately does *not* produce is an address. `GNatives` lives in the uninitialised
part of `.data` and is populated at startup (see `docs/NATIVE_VM.md`), so the file contains no
index -> address map; an earlier version of this tool paired these names with addresses taken from a
pointer array that turned out to be the CRT's static-initialiser list. An index is evidence of an
entry point, not of an address, and the two are kept apart here until the runtime table is resolved.

`Editor`, `UPreview` and `UnrealEd` are excluded: they are editor-side packages that the shipped
game does not load, and `UPreview.UPreviewMapListClient.Created` claims index 2, which the runtime
table reserve for nothing -- their small indices are numbered inside the package, not in the
engine's global space.

Usage: native-names.py [--natives inventory/natives.csv] [-o inventory]
"""
import argparse
import csv
import os
import re
import sys

EXCLUDED_PACKAGES = {"Editor", "UPreview", "UnrealEd"}
IDENT = re.compile(r"[^A-Za-z0-9_]")


def load_declarations(path):
    declared = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            token = int(row["native_token"])
            if token == 0:
                continue
            declared.setdefault(token, []).append(
                (row["package"], row["class"], row["function"]))
    return declared


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--natives", default="inventory/natives.csv")
    ap.add_argument("-o", "--out", default="inventory")
    args = ap.parse_args()

    declared = load_declarations(args.natives)
    claims, ambiguous = {}, {}
    for index in sorted(declared):
        live = [(c, f) for p, c, f in declared[index] if p not in EXCLUDED_PACKAGES]
        distinct = sorted(set(live))
        if len(distinct) == 1:
            claims[index] = distinct[0]
        elif len(distinct) > 1:
            ambiguous[index] = distinct

    base_counts = {}
    for _cls, fn in claims.values():
        base = "exec" + IDENT.sub("_", fn)
        base_counts[base] = base_counts.get(base, 0) + 1

    rows, used = [], {}
    for index in sorted(claims):
        cls, fn = claims[index]
        base = "exec" + IDENT.sub("_", fn)
        if base_counts[base] > 1:
            base = "exec%s_%s" % (IDENT.sub("_", cls), IDENT.sub("_", fn))
        name = base if base not in used else "%s_%d" % (base, index)
        used.setdefault(name, index)
        rows.append((index, name, "declared",
                     ";".join("%s:%s:%s" % d for d in sorted(set(declared[index])))))
    for index in sorted(ambiguous):
        rows.append((index, "", "ambiguous",
                     ";".join("%s:%s:%s" % d for d in ambiguous[index])))
    rows.sort()

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "native_names.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["index", "name", "evidence", "declarers"])
        w.writerows(rows)

    declarations = sum(len(v) for v in declared.values())
    print("%d declarations over %d distinct explicit indices: %d named, %d ambiguous"
          % (declarations, len(declared), len(claims), len(ambiguous)))
    print("wrote %s (no addresses: GNatives is not initialised in the file)" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
