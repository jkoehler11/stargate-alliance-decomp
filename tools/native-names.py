#!/usr/bin/env python3
"""Name the `GNatives` dispatch slots from the script packages' own native declarations.

Bytecode calls natives by index, and `inventory/natives.csv` records every native function the
shipped packages declare together with the index the package serialised for it. That makes the
dispatch table self-documenting: slot N of `GNatives` *is* the function the script calls as
`class.function` at index N.

Three cases have to stay apart, because only one of them is a name:

- **declared** -- one distinct `Class.Function` claims the index; the slot is that function.
- **slot-only** -- no shipped package declares the index. 721 of the 991 live slots are like this:
  engine-internal execs (renderer, Xbox services, Karma) that C++ calls directly and no script can
  reach.  ``The slot number is real evidence, the name is not, so the slot gets no name.
- **ambiguous** -- two different functions claim one index, or two indices claim one name. A
  conflict means the mapping is not understood yet; naming either side would be a guess.

`Editor` and `UPreview` are excluded: they are editor-side packages (not loaded by the game build)
and number their natives locally -- `UPreviewMapListClient.Created` claims index 2, which is a null
slot in the runtime table, so its index cannot be read in the same space.

Usage: native-names.py [--natives inventory/natives.csv] [--table inventory/gnatives_map.csv]
                        [-o inventory] [--ghidra-out build/ghidra/gnatives.tsv]
"""
import argparse
import csv
import os
import re
import sys

# Editor-side packages: present in the dump, not loaded by the shipped game, and their native
# indices are numbered inside the package rather than across the engine's global index space.
EXCLUDED_PACKAGES = {"Editor", "UPreview", "UnrealEd"}

IDENT = re.compile(r"[^A-Za-z0-9_]")


def load_table(path):
    slots = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            slots[int(row["index"])] = int(row["function_va"], 16)
    return slots


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
    ap.add_argument("--table", default="inventory/gnatives_map.csv")
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--ghidra-out", default="build/ghidra/gnatives.tsv")
    args = ap.parse_args()

    slots = load_table(args.table)
    declared = load_declarations(args.natives)

    # Pass 1: which distinct Class.Function claims each slot.
    claims = {}
    for index in sorted(slots):
        live = [d for d in declared.get(index, []) if d[0] not in EXCLUDED_PACKAGES]
        if len(declared.get(index, [])) == 0:
            continue
        distinct = sorted({(c, f) for _p, c, f in live})
        if len(distinct) == 1:
            claims[index] = distinct[0]

    # Pass 2: `exec<Function>` is the engine's own spelling for a native implementation, and
    # docs/CONVENTIONS.md asks for it; only a name that two slots would share gets the class prefix,
    # so the common case stays readable (`execTrace`, not `execActor_Trace`).
    base_counts = {}
    for cls, fn in claims.values():
        base_counts["exec" + IDENT.sub("_", fn)] = base_counts.get(
            "exec" + IDENT.sub("_", fn), 0) + 1

    rows = []
    named, slot_only, ambiguous = 0, 0, 0
    used = {}
    for index in sorted(slots):
        decls = declared.get(index, [])
        declarers = ";".join(f"{p}:{c}:{f}" for p, c, f in sorted(decls))
        if index not in claims:
            if decls:
                ambiguous += 1
                rows.append((index, slots[index], "", "ambiguous", declarers))
            else:
                slot_only += 1
                rows.append((index, slots[index], "", "slot-only", ""))
            continue
        cls, fn = claims[index]
        base = "exec" + IDENT.sub("_", fn)
        if base_counts[base] > 1:
            base = f"exec{IDENT.sub('_', cls)}_{IDENT.sub('_', fn)}"
        name = base
        if name in used:
            name = f"{base}_{index}"
        used.setdefault(name, index)
        named += 1
        rows.append((index, slots[index], name, "declared", declarers))

    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, "native_names.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["index", "function_va", "name", "evidence", "declarers"])
        for index, va, name, evidence, declarers in rows:
            w.writerow([index, f"0x{va:08x}", name, evidence, declarers])

    if args.ghidra_out:
        os.makedirs(os.path.dirname(args.ghidra_out), exist_ok=True)
        with open(args.ghidra_out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# index\tva\tname\tevidence\n")
            for index, va, name, evidence, _declarers in rows:
                fh.write(f"{index}\t0x{va:08x}\t{name}\t{evidence}\n")

    beyond = sorted(i for i in declared if i not in slots and i)
    print(f"{len(slots)} live GNatives slots: {named} named from a declaration, "
          f"{slot_only} slot-only (no declarer), {ambiguous} ambiguous")
    print(f"  {len({d for ds in declared.values() for d in ds})} native declarations, "
          f"{len(declared)} distinct explicit indices")
    if beyond:
        print(f"  declared indices outside the table: {', '.join(str(i) for i in beyond)}")
    print(f"wrote {out_path}" + (f" and {args.ghidra_out}" if args.ghidra_out else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
