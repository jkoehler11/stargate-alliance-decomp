#!/usr/bin/env python3
"""Locate the UnrealScript native dispatch table (`GNatives`) in the XBE.

The VM calls natives by index through a static array of function pointers, so the table looks like
a run of 4-byte values that are either null or the entry address of a real function. That shape
alone is not unique -- this image has two dozen long runs of pointers into code -- so every run is
scored with evidence only a dispatch table satisfies:

  1. entry 0 is null (UE leaves index 0 unused);
  2. the run begins at a section boundary (the array is a standalone object, not a field inside
     another structure);
  3. every non-null entry points into `.text` -- not merely into a section flagged executable;
  4. exactly one code reference to the table start (the interpreter's dispatch site). A vtable or
     callback array is referenced from every site that installs it, and usually not from code at
     all.

Exactly one candidate satisfies all four (`0x00598e00`), and the script side corroborates it: 270
of the 274 `native()` indices declared with an explicit index land on a distinct, non-null slot.
The script writes the mapping for the best-evidenced table and the evidence for every candidate, so
`docs/NATIVE_VM.md` can cite measurements instead of intuition.

Usage: gnatives.py DUMP/default.xbe [--symbols symbols/xbox_functions.csv] [--natives inventory/natives.csv]
"""
import argparse
import csv
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xbe import Xbe  # noqa: E402

MIN_RUN = 300
STRUCTURAL_TESTS = 4


def load_function_entries(path):
    entries = set()
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            entries.add(int(row["address"], 16))
    return entries


def load_declared_indices(path):
    """Native indices the shipped packages declare, with the names that claim them.

    Token 0 means the package serialised no index for that function (UELib's NativeToken is 0 for
    the bare `native` declarations), so it carries no slot information and is not evidence either
    way. Editor-only packages number their natives locally and are reported separately by
    `tools/native-names.py`, but they still count here: the table either covers them or it does not.
    """
    declared = {}
    if not path or not os.path.isfile(path):
        return declared
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
    ap.add_argument("xbe")
    ap.add_argument("--symbols", default="symbols/xbox_functions.csv")
    ap.add_argument("--natives", default="inventory/natives.csv")
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()
    if not os.path.isfile(args.symbols):
        sys.exit("symbols/xbox_functions.csv missing -- run `make ghidra` first")

    image = Xbe(args.xbe)
    functions = load_function_entries(args.symbols)
    declared = load_declared_indices(args.natives)
    text = [s for s in image.sections if s.name == ".text"]
    text_ranges = [(s.va, s.va + min(s.fsize, s.vsize)) for s in text]

    def in_text(va):
        return any(lo <= va < hi for lo, hi in text_ranges)

    def text_xrefs(va):
        needle = struct.pack("<I", va)
        hits = 0
        for s in text:
            blob = image.data[s.foff:s.foff + min(s.fsize, s.vsize)]
            start = 0
            while True:
                i = blob.find(needle, start)
                if i < 0:
                    break
                hits += 1
                start = i + 1
        return hits

    candidates = []
    # Dispatch tables live in initialised data, never in code. XBE data sections also carry the
    # executable flag, so "executable" cannot tell code from data here: use the name and require
    # real file-backed bytes.
    for section in image.sections:
        if section.name == ".text" or section.fsize < 0x1000:
            continue
        blob = image.data[section.foff:section.foff + min(section.fsize, section.vsize)]
        words = len(blob) // 4
        values = struct.unpack_from(f"<{words}I", blob, 0)
        run_start = None
        run = []
        for i, value in enumerate(tuple(values) + (0xFFFFFFFF,)):
            # A table entry is null or points into executable memory. Requiring an exact match
            # against Ghidra's discovered function entries is too strict: the analyser misses every
            # target that only the dispatch table refers to (all 991 of them here), and one miss
            # would break the run.
            if value == 0 or in_text(value):
                if run_start is None:
                    run_start = i
                run.append(value)
            else:
                if run_start is not None and len(run) >= MIN_RUN:
                    candidates.append(analyse(section, run_start, run, declared, functions,
                                              in_text, text_xrefs))
                run_start, run = None, []
    # Best evidence first: structural tests, then declared-index coverage, then size.
    candidates.sort(key=lambda c: (c["structural_hits"], c["declared_present"],
                                   c["declared_in_range"], c["nonnull"]), reverse=True)

    os.makedirs(args.out, exist_ok=True)
    evidence = os.path.join(args.out, "gnatives_candidates.csv")
    with open(evidence, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "va", "section", "entries", "non_null", "known_function_hits",
                    "entry0_null", "at_section_start", "all_in_text", "text_xrefs",
                    "structural_hits", "declared_in_range", "declared_present",
                    "declared_absent", "conflicting_names", "last_non_null"])
        for rank, c in enumerate(candidates, 1):
            w.writerow([rank, f"0x{c['va']:08x}", c["section"], c["length"], c["nonnull"],
                        c["hits"], int(c["entry0_null"]), int(c["at_section_start"]),
                        int(c["all_in_text"]), c["text_xrefs"], c["structural_hits"],
                        c["declared_in_range"], c["declared_present"], c["declared_absent"],
                        c["conflicting"], c["last_non_null"]])

    print(f"{len(candidates)} candidate tables (runs >= {MIN_RUN} entries), "
          f"{len(declared)} declared native indices")
    for rank, c in enumerate(candidates[:args.top], 1):
        print(f"  #{rank} 0x{c['va']:08x} {c['section']:8s} entries={c['length']:5d} "
              f"non-null={c['nonnull']:5d} known-fn={c['hits']:5d} "
              f"structural={c['structural_hits']}/{STRUCTURAL_TESTS} xrefs={c['text_xrefs']} "
              f"declared={c['declared_present']}/{c['declared_in_range']} in range")

    if not candidates:
        print("no candidates: widen MIN_RUN or check the section carve")
        return 1

    best = candidates[0]
    if best["structural_hits"] < STRUCTURAL_TESTS:
        print(f"WARNING: best candidate 0x{best['va']:08x} passes only "
              f"{best['structural_hits']}/{STRUCTURAL_TESTS} structural tests -- do not trust the "
              f"map without a second source of evidence", file=sys.stderr)

    last = best["last_non_null"]
    note = "table" if best["structural_hits"] == STRUCTURAL_TESTS else "best candidate, unconfirmed"
    with open(os.path.join(args.out, "gnatives_map.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["index", "function_va", "note"])
        for index, value in enumerate(best["values"][:last + 1]):
            if not value:
                continue
            w.writerow([index, f"0x{value:08x}", note])
    print(f"best candidate 0x{best['va']:08x}: {best['nonnull']} live entries up to index {last}, "
          f"{best['declared_present']} of {len(declared)} declared indices resolved "
          f"-> {args.out}/gnatives_map.csv")
    if best["absent"]:
        print("  declared but absent from the table: "
              + ", ".join(f"{i} ({','.join(sorted({c + '.' + f for _p, c, f in declared[i]}))})"
                          for i in best["absent"]))
    return 0


def analyse(section, run_start, run, declared, functions, in_text, text_xrefs):
    va = section.va + run_start * 4
    live = [v for v in run if v]
    in_range = [i for i in declared if i < len(run)]
    present = [i for i in in_range if run[i]]
    absent = sorted(i for i in declared if i not in present)
    conflicts = 0
    for i in present:
        names = {(cls, fn) for _pkg, cls, fn in declared[i]}
        if len(names) > 1:
            conflicts += 1
    xrefs = text_xrefs(va)
    entry0_null = run[0] == 0
    # The array's own first word is the only boundary this reader can check: a run that starts at
    # the section start begins on one. Runs that begin after a null word are recorded as not
    # starting on a boundary, which is why the vtables in .rdata fail here.
    at_start = run_start == 0
    all_in_text = all(in_text(v) for v in live)
    structural = sum([entry0_null, at_start, all_in_text, xrefs == 1])
    return {
        "va": va, "section": section.name, "length": len(run), "nonnull": len(live),
        "hits": sum(1 for v in live if v in functions),
        "values": run, "entry0_null": entry0_null, "at_section_start": at_start,
        "all_in_text": all_in_text, "text_xrefs": xrefs, "structural_hits": structural,
        "declared_in_range": len(in_range), "declared_present": len(present),
        "declared_absent": len(absent), "absent": absent, "conflicting": conflicts,
        "last_non_null": max((i for i, v in enumerate(run) if v), default=-1),
    }


if __name__ == "__main__":
    sys.exit(main())
