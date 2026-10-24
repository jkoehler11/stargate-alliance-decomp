#!/usr/bin/env python3
"""Which assert sites are still in no function, and what the bytes there actually are.

A build's assert site names __FILE__, so the site lies inside the function that asserts and the
function is named from it (tools/ghidra/ghidra_scripts/XbePost.java). A site with no function
therefore means either code the analysis never reached or a false positive: the xref scan looks for
the 32-bit address of the file string anywhere in an executable section, and this build marks .rdata
and .data executable too (docs/NATIVE.md), so a dword in a data table that happens to equal the
string's address is indistinguishable from a call site by address alone.

This separates the two without asking the decompiler. For each residual site it reports the section,
and -- when the section holds functions, so the site is in code -- the nearest 0xCC fill byte behind
it and whether a linear decode from the byte after that fill covers the site. That is the same
evidence tools/ghidra/ghidra_scripts/SeedGaps.java acts on, so a residual the seeder could have
taken shows up here as "code" instead of staying unexplained.

Usage:
    assert-residual.py default.xbe -o inventory --functions symbols/xbox_functions.csv \\
        --asserts build/ghidra/asserts.tsv

Writes inventory/xbe_assert_residual.csv. Reads the image only; changes nothing.
"""
import argparse
import bisect
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from xbe import Xbe  # noqa: E402

try:
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
except ImportError:                                        # pragma: no cover
    sys.exit("needs capstone: pip install capstone")

MAX_BACK = 0x1000


def load_functions(path):
    """address -> size, from symbols/xbox_functions.csv."""
    out = {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            out[int(row["address"], 16)] = int(row["size"])
    return out


def load_sites(path):
    """(site_va, module, source_file, string_va) for every assert xref."""
    out = []
    with open(path, newline="") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) < 4:
                continue
            out.append((int(p[3], 16), p[0], p[1], int(p[2], 16)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("xbe")
    ap.add_argument("-o", "--out", default=".",
                    help="directory to write inventory/xbe_assert_residual.csv into")
    ap.add_argument("--functions", default="symbols/xbox_functions.csv")
    ap.add_argument("--asserts", default="build/ghidra/asserts.tsv")
    args = ap.parse_args()

    xbe = Xbe(args.xbe)
    functions = load_functions(args.functions)
    starts = sorted(functions)

    def containing(va):
        i = bisect.bisect_right(starts, va) - 1
        if i < 0:
            return None
        a = starts[i]
        return a if va < a + functions[a] else None

    sites = load_sites(args.asserts)
    residual = [s for s in sites if containing(s[0]) is None]

    md = Cs(CS_ARCH_X86, CS_MODE_32)
    rows = []
    for site, module, source_file, string_va in residual:
        section = xbe.section_for(site)
        name = section.name if section else "?"
        entry, detail, verdict = "", "", ""
        lo = max(section.va, site - MAX_BACK)
        i = site - 1
        while i > lo:
            if xbe.data[xbe.va_to_offset(i)] == 0xCC:
                cand = i + 1
                if containing(cand) is not None:       # a fill byte inside a known body is not a boundary
                    i -= 1
                    continue
                buf = xbe.data[xbe.va_to_offset(cand):xbe.va_to_offset(cand) + MAX_BACK]
                end = cand
                covered = False
                for ins in md.disasm(buf, cand):
                    if ins.address != end:              # decode desynced: no evidence here
                        break
                    end = ins.address + ins.size
                    if ins.address <= site < end:
                        covered = True
                        detail = f"{ins.mnemonic} {ins.op_str}".strip()
                        break
                if covered:
                    entry = f"0x{cand:08x}"
                    verdict = "code"
                    break
            i -= 1
            if not verdict:
                # The assert call passes the file string as `push imm32`, so the dword has to follow
                # a 0x68 opcode. Where it does not, the site is a coincidental dword in a table: the
                # scan looks for the address anywhere in an "executable" section and this build marks
                # its data sections executable (docs/NATIVE.md).
                if xbe.data[xbe.va_to_offset(site - 1)] == 0x68:
                    verdict = "unreached code"
                    detail = "push of the file string, no fill byte decodes over it"
                else:
                    verdict = "not a call site"
                    detail = f"dword in data; byte before is {xbe.data[xbe.va_to_offset(site - 1)]:#04x}, not a push"
        ctx = xbe.read_va(site - 4, 12).hex()
        rows.append({
            "site_va": f"0x{site:08x}", "module": module, "source_file": source_file,
            "string_va": f"0x{string_va:08x}", "section": name, "verdict": verdict,
            "entry_va": entry, "instruction": detail, "bytes": ctx,
        })

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "xbe_assert_residual.csv")
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["site_va", "module", "source_file", "string_va",
                                           "section", "verdict", "entry_va", "instruction", "bytes"])
        w.writeheader()
        w.writerows(rows)

    counts = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print(f"assert sites: {len(sites)}, in no function: {len(rows)} "
          f"({', '.join(f'{k} {v}' for k, v in sorted(counts.items())) or 'none'}) -> {path}")


if __name__ == "__main__":
    main()
