#!/usr/bin/env python3
"""Locate the engine's global `(Data, Num, Max)` triples by reading each writable section once.

`names.py --find` scores every aligned triple in `.data` and validates seventeen reserved names; on a loaded level
that ran over fifteen minutes on the `near12` boot without printing a candidate. The question is cheaper than that:
read the section whole (2.2 MB in 64 KiB stub chunks, seconds), keep the aligned triples whose shape is plausible
(`Data` a heap pointer, `Num` in the range an engine array reaches, `Num <= Cap <= 2*Num`), and validate the first
entry. Two entries agreeing is enough to name the triple; seventeen is what `--find` does for the *first* boot.

    python3 tools/xemu/gnames-find.py --port 12345 --xbe $SG1_DUMP/default.xbe

Measured on `near12` (a mission): `GNames` `0x006be0d8` -> `0x07740000` / 41,265, `GObjObjects` `0x006c21b0` ->
`0x041e0000` / 48,646. The triples sit in `.data` and do not move between boots; the arrays they point at do, so
the `Data` a previous boot reported is not evidence for this one (`read 0x7730000+0xc failed` was that mistake).
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import names  # noqa: E402
import objects  # noqa: E402

DEFAULT_SECTIONS = (".data", ".data1", ".bss")


def plausible(blob, va, guest, sections, min_num=1000, max_num=400000):
    """[(address, data, num, cap, first entry index)] for triples in `blob` that read as an engine array."""
    out = []
    for off in range(0, len(blob) - 12, 4):
        data, num, cap = struct.unpack_from("<III", blob, off)
        if not (0x01000000 <= data < 0x10000000 and min_num <= num <= max_num and num <= cap <= num * 2):
            continue
        try:
            entry = struct.unpack_from("<I", guest.read(data, 4), 0)[0]
            index = struct.unpack_from("<I", guest.read(entry, 4), 0)[0]
        except (IOError, OSError):
            continue
        out.append((va + off, data, num, cap, index))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--xbe", required=True, help="default.xbe of the dump, for the section table")
    ap.add_argument("--section", action="append", default=None, help="section to read (default .data)")
    args = ap.parse_args()

    guest = names.Guest(args.host, args.port)
    try:
        sections = objects.image_sections(args.xbe)
        print("image sections: " + ", ".join("%s %#x+%#x" % row for row in sections))
        for name in args.section or DEFAULT_SECTIONS:
            try:
                va, vsize = objects.section(sections, name)
            except Exception:
                print("section %-7s absent from the image" % name)
                continue
            blob = names.read_section(guest, va, vsize)
            print("section %-7s va=%#010x size=%#x read=%d" % (name, va, vsize, len(blob)))
            for addr, data, num, cap, index in plausible(blob, va, guest, sections):
                print("  triple at %#010x: data=%#010x num=%d cap=%d entry0 index=%d"
                      % (addr, data, num, cap, index))
    finally:
        guest.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
