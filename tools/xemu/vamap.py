#!/usr/bin/env python3
"""Map guest physical addresses to the linear addresses the CPU uses, out of a whole-RAM dump (PLAN 7.3 / U15).

`drive.py --step="monitor=pmemsave 0 0x8000000 ..."` writes *physical* memory, and every address in these docs is a
*linear* one: the XBE's section table, `objects.py`'s walk, a class-table VA, a breakpoint. On this build the two
spaces are not the same, so an address read out of a dump cannot be used as a pointer value -- or armed as a
watchpoint -- until it is translated. The translation is in the dump: `info registers` gives `CR3` (measured
`0x0000f000`), and the page tables are ordinary 4 KiB RAM.

    python3 tools/xemu/vamap.py ram.bin --cr3 0xf000 --pa 0x6bfcb78
    python3 tools/xemu/vamap.py ram.bin --cr3 0xf000 --va 0x11000
    python3 tools/xemu/vamap.py ram.bin --cr3 0xf000 --stats

i386 two-level paging, PAE off (`CR4=0x610` sets PSE, not PAE), so a PDE is either a 4 MiB page or a page table of
1024 PTEs; both are handled and both are counted. `--pa` reports every linear address that maps the page holding the
address, `--va` the physical page behind a linear address, `--stats` the shape of the map (present PDEs, 4 MiB pages,
mapped 4 KiB pages, distinct physical pages).

A dump is guest data and stays on the desktop; this prints addresses, counts and flags only.
"""
import argparse
import os
import struct
import sys

PRESENT = 0x1
LARGE = 0x80
PAGE = 0x1000
DIRECTORY_SIZE = 0x1000
TABLES = 1024


def u32(blob, offset):
    if offset < 0 or offset + 4 > len(blob):
        raise IndexError("read %#x outside the dump (%d bytes)" % (offset, len(blob)))
    return struct.unpack_from("<I", blob, offset)[0]


def directory(blob, cr3):
    """[(index, pde)] for the 1024 page-directory entries, from the page at CR3."""
    base = cr3 & 0xFFFFF000
    return [(index, u32(blob, base + index * 4)) for index in range(TABLES)]


def linear_for(blob, cr3, pa, limit=8):
    """Every linear address that maps the 4 KiB page holding PA: [(linear, how, flags)]."""
    page = pa & 0xFFFFF000
    out = []
    for index, pde in directory(blob, cr3):
        if not pde & PRESENT:
            continue
        base = index << 22
        if pde & LARGE:
            start = pde & 0xFFC00000
            if start <= page < start + 0x400000:
                out.append((base | (pa & 0x3FFFFF), "4 MiB page", pde & 0xFFF))
        else:
            table = pde & 0xFFFFF000
            for slot in range(TABLES):
                try:
                    pte = u32(blob, table + slot * 4)
                except IndexError:
                    break
                if not pte & PRESENT:
                    continue
                if pte & 0xFFFFF000 == page:
                    out.append((base | (slot << 12) | (pa & 0xFFF), "4 KiB page", pte & 0xFFF))
                    if len(out) >= limit:
                        return out
    return out


def physical_for(blob, cr3, va, limit=8):
    """The physical address VA maps to (0 if it is not mapped), plus the largest-page kind that mapped it."""
    index = va >> 22
    pde = u32(blob, (cr3 & 0xFFFFF000) + index * 4)
    if not pde & PRESENT:
        return []
    if pde & LARGE:
        return [((pde & 0xFFC00000) | (va & 0x3FFFFF), "4 MiB page", pde & 0xFFF)]
    table = pde & 0xFFFFF000
    pte = u32(blob, table + ((va >> 12) & 0x3FF) * 4)
    if not pte & PRESENT:
        return []
    return [((pte & 0xFFFFF000) | (va & 0xFFF), "4 KiB page", pte & 0xFFF)]


def stats(blob, cr3):
    present = large = pages = 0
    physical = set()
    for _index, pde in directory(blob, cr3):
        if not pde & PRESENT:
            continue
        present += 1
        if pde & LARGE:
            large += 1
            start = pde & 0xFFC00000
            for step in range(0, 0x400000, PAGE):
                physical.add(start + step)
            continue
        table = pde & 0xFFFFF000
        try:
            entries = [u32(blob, table + slot * 4) for slot in range(TABLES)]
        except IndexError:
            continue
        for pte in entries:
            if pte & PRESENT:
                pages += 1
                physical.add(pte & 0xFFFFF000)
    return {"present_pdes": present, "large_pdes": large, "mapped_pages": pages + large * 1024,
            "distinct_physical_pages": len(physical)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dump", help="a whole-RAM pmemsave image")
    ap.add_argument("--cr3", type=lambda v: int(v, 0), required=True, help="the CR3 from `info registers`")
    ap.add_argument("--pa", type=lambda v: int(v, 0), action="append", default=[], help="physical address(es)")
    ap.add_argument("--va", type=lambda v: int(v, 0), action="append", default=[], help="linear address(es)")
    ap.add_argument("--stats", action="store_true", help="summarise the map instead")
    args = ap.parse_args()

    with open(args.dump, "rb") as handle:
        blob = handle.read()
    print("dump %s: %d KiB, CR3 %#x" % (os.path.basename(args.dump), len(blob) // 1024, args.cr3))

    if args.stats:
        got = stats(blob, args.cr3)
        print("   %(present_pdes)d present PDEs, %(large_pdes)d of them 4 MiB; %(mapped_pages)d mapped pages, "
              "%(distinct_physical_pages)d distinct physical pages" % got)

    for pa in args.pa:
        found = linear_for(blob, args.cr3, pa)
        if not found:
            print("%#010x is on no mapped page: a physical-only address" % pa)
        for linear, how, flags in found:
            print("%#010x -> linear %#010x (%s, flags %#x)" % (pa, linear, how, flags))

    for va in args.va:
        found = physical_for(blob, args.cr3, va)
        if not found:
            print("%#010x is not mapped" % va)
        for physical, how, flags in found:
            print("%#010x -> physical %#010x (%s, flags %#x)" % (va, physical, how, flags))
    return 0


if __name__ == "__main__":
    sys.exit(main())
