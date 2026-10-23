#!/usr/bin/env python3
"""Locate the UnrealScript native dispatch table (`GNatives`) in the XBE.

The VM calls natives by index through a static array of function pointers, so the table looks like
a run of 4-byte values that are either null or the entry address of a real function. Scanning for
runs of exactly that shape is a much sharper test than "pointer into .text": strings and vtables
point elsewhere, so a long run of function-entry pointers in .data is nearly self-identifying.

The script never guesses which candidate is the table: it ranks them, prints the evidence, and
writes the mapping for the best one so `docs/NATIVE_VM.md` can state how confident each step is.

Usage: gnatives.py DUMP/default.xbe [--symbols symbols/xbox_functions.csv] [-o inventory]
"""
import argparse
import csv
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xbe import Xbe  # noqa: E402

MIN_RUN = 300


def load_function_entries(path):
    entries = set()
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            entries.add(int(row["address"], 16))
    return entries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xbe")
    ap.add_argument("--symbols", default="symbols/xbox_functions.csv")
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()
    if not os.path.isfile(args.symbols):
        sys.exit("symbols/xbox_functions.csv missing -- run `make ghidra` first")

    image = Xbe(args.xbe)
    functions = load_function_entries(args.symbols)
    code = [s for s in image.sections if s.name == ".text"]
    code_ranges = [(s.va, s.va + s.vsize) for s in code]

    def in_code(va):
        return any(lo <= va < hi for lo, hi in code_ranges)

    candidates = []
    # Dispatch tables live in initialised data, never in code.
    for section in image.sections:
        # XBE data sections also carry the executable flag (0x7), so "executable" cannot be used to
        # tell code from data here: use the name and require real file-backed bytes.
        if section.name == ".text" or section.fsize < 0x1000:
            continue
        blob = image.data[section.foff:section.foff + min(section.fsize, section.vsize)]
        words = len(blob) // 4
        values = struct.unpack_from(f"<{words}I", blob, 0)
        run_start = None
        run = []
        for i, value in enumerate(tuple(values) + (0xFFFFFFFF,)):
            # A table entry is null or points into executable memory. Requiring an exact match
            # against Ghidra's discovered function entries is too strict: the analyser misses some
            # targets, and one miss would break the run.
            if value == 0 or in_code(value):
                if run_start is None:
                    run_start = i
                run.append(value)
            else:
                if run_start is not None and len(run) >= MIN_RUN:
                    live = [v for v in run if v]
                    candidates.append({
                        "va": section.va + run_start * 4,
                        "length": len(run),
                        "nonnull": len(live),
                        "hits": sum(1 for v in live if v in functions),
                        "section": section.name,
                        "values": run,
                    })
                run_start, run = None, []
    candidates.sort(key=lambda c: (-c["nonnull"], -c["length"]))

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "gnatives_candidates.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "va", "section", "entries", "non_null", "known_function_hits",
                    "last_non_null"])
        for rank, c in enumerate(candidates, 1):
            last = max((i for i, v in enumerate(c["values"]) if v), default=-1)
            w.writerow([rank, f"0x{c['va']:08x}", c["section"], c["length"], c["nonnull"],
                        c["hits"], last])

    print(f"{len(candidates)} candidate tables (runs >= {MIN_RUN} entries)")
    for rank, c in enumerate(candidates[:args.top], 1):
        density = c["nonnull"] / c["length"]
        print(f"  #{rank} 0x{c['va']:08x} {c['section']:8s} entries={c['length']:5d} "
              f"non-null={c['nonnull']:5d} known-fn={c['hits']:5d} density={density:.2f}")

    if not candidates:
        print("no candidates: widen MIN_RUN or check the section carve")
        return 1

    best = candidates[0]
    last = max((i for i, v in enumerate(best["values"]) if v), default=-1)
    with open(os.path.join(args.out, "gnatives_map.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["index", "function_va", "note"])
        for index, value in enumerate(best["values"][:last + 1]):
            if not value:
                continue
            w.writerow([index, f"0x{value:08x}", "best-candidate table"])
    print(f"best candidate 0x{best['va']:08x}: {best['nonnull']} live entries up to index {last} "
          f"-> {args.out}/gnatives_map.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
