#!/usr/bin/env python3
"""Emit the import spec for Ghidra: XBE sections plus assert-anchored code sites.

Ghidra's binary loader treats the file as one flat block, which is wrong for an XBE (file offsets
and virtual addresses diverge after the first section). The spec lets `XbePre.java` carve the real
sections before analysis and `XbePost.java` name the functions that own assert strings. Native
names are not part of the spec: the dispatch table has no bytes in the file (docs/NATIVE_VM.md).

Usage: make-spec.py DUMP/default.xbe --out build/ghidra
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from xbe import Xbe  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xbe")
    ap.add_argument("--out", default="build/ghidra")
    ap.add_argument("--asserts", default="inventory/xbe_modules.csv")
    args = ap.parse_args()

    image = Xbe(args.xbe)
    os.makedirs(args.out, exist_ok=True)

    with open(os.path.join(args.out, "sections.tsv"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# name\tva\tvsize\tfoff\tfsize\tflags\n")
        fh.write(f"# base=0x{image.base:x} image=0x{image.image_size:x} certificate=0x{image.certificate:x} (entry not readable; docs/XBE_LAYOUT.md)\n")
        for s in image.sections:
            fh.write(f"{s.name}\t0x{s.va:x}\t0x{s.vsize:x}\t0x{s.foff:x}\t0x{s.fsize:x}\t0x{s.flags:x}\n")

    rows = 0
    with open(os.path.join(args.out, "asserts.tsv"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# module\tsource_file\tstring_va\txref_va\n")
        if os.path.isfile(args.asserts):
            with open(args.asserts, newline="", encoding="utf-8") as src:
                for row in csv.DictReader(src):
                    for va in (row["xref_vas"] or "").split():
                        fh.write(f"{row['module']}\t{row['source_file']}\t{row['string_va']}\t{va}\n")
                        rows += 1
    print(f"{len(image.sections)} sections, {rows} assert xrefs -> {args.out}")


if __name__ == "__main__":
    main()
