#!/usr/bin/env python3
"""How a body nothing calls is reached: relative calls, data pointers, and a vtable slot.

`tools/xbe.py xrefs` (and every dword scan in this repo) finds an address only where it is stored as
a **32-bit dword**. Two things follow, and both cost a session on the save path:

* a *direct* call is `E8 rel32`, so it never appears in a dword scan -- "0 xrefs" does not mean
  "nothing calls it", it means "nothing stores its address";
* a **virtual** call is `call [reg+disp32]`, which stores an *offset*, not an address, so the only
  dword reference to a virtual's body is the one in the class's vtable.

So this tool answers one question with three scans, for each address given:

1. `call` -- every `E8 rel32` in `.text` whose target is the address (the direct callers).
2. `ref`  -- every dword anywhere in a file-backed section that equals the address (the dispatchers).
3. `slot` -- for a `ref` that lands inside a known class table (`inventory/xbe_class_tables.csv`),
   the owning class (`inventory/xbe_class_table_names.csv`), the slot index, and then every
   `call [reg+slot*4]` site in `.text` (the virtual call sites for that slot) with the function each
   one sits in. A call site's offset and the table entry's index agreeing is the validation: an
   address that happens to be stored in `.rdata` still has to match a call offset that exists.

Usage:
    vtable-reach.py default.xbe --va 0x00138680
    vtable-reach.py default.xbe --va 0x00138680,0x00136a40 --csv $WORK/reach.csv
    vtable-reach.py --check          # synthetic self-test, reads no dump and writes nothing

Reads the image and committed inventories only; writes only the file named by --csv.
"""
import argparse
import csv
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from xbe import Xbe  # noqa: E402

# call [reg+disp32] / call [reg+disp8]: FF /2, ModRM mod=10 or mod=01
CALL_RM32 = 0xFF
MOD_DISP32_LO, MOD_DISP32_HI = 0x90, 0x97
MOD_DISP8_LO, MOD_DISP8_HI = 0x50, 0x57


def rel_call_sites(text_body, text_va, target):
    """Sites of `E8 rel32` in a code blob whose target is `target`."""
    out = []
    pos = text_body.find(b"\xe8")
    while pos != -1:
        if pos + 5 <= len(text_body):
            rel = struct.unpack_from("<i", text_body, pos + 1)[0]
            site = text_va + pos
            if (site + 5 + rel) & 0xFFFFFFFF == target:
                out.append(site)
        pos = text_body.find(b"\xe8", pos + 1)
    return out


def dword_refs(sections, blob_at, target):
    """(va, section_name) of every dword equal to `target` in the given sections."""
    needle = struct.pack("<I", target & 0xFFFFFFFF)
    out = []
    for sec in sections:
        body = blob_at(sec)
        pos = body.find(needle)
        while pos != -1:
            if pos % 4 == 0:                       # dwords are 4-aligned in this image
                out.append((sec.va + pos, sec.name))
            pos = body.find(needle, pos + 1)
    return out


def indirect_call_sites(text_body, text_va, offset):
    """Sites of `call [reg+offset]` in a code blob."""
    out = []
    for i in range(len(text_body) - 6):
        if text_body[i] != CALL_RM32:
            continue
        modrm = text_body[i + 1]
        if MOD_DISP32_LO <= modrm <= MOD_DISP32_HI:
            if struct.unpack_from("<I", text_body, i + 2)[0] == offset:
                out.append((text_va + i, 4))
        elif MOD_DISP8_LO <= modrm <= MOD_DISP8_HI:
            if text_body[i + 2] == offset:
                out.append((text_va + i, 1))
    return out


def slot_of(tables, va):
    """(table_va, slots, index) for the class table holding `va`, or None."""
    for table_va, slots in tables:
        if table_va <= va < table_va + 4 * slots:
            return table_va, slots, (va - table_va) // 4
    return None


def containing(functions, va):
    """The function entry that contains `va`, as (address, size, name) or None."""
    best = None
    for addr, size, name in functions:
        if addr <= va < addr + size and (best is None or addr > best[0]):
            best = (addr, size, name)
    return best


def load_tables(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            out.append((int(row["table_va"], 16), int(row["slots"])))
    return out


def load_table_names(path):
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            out[int(row["table_va"], 16)] = row.get("class", "?")
    return out


def load_functions(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            out.append((int(row["address"], 16), int(row["size"]), row.get("name", "")))
    return out


def report(xbe, path, targets, tables, names, functions):
    """One dict per target; also returns the virtual call sites found for its slot."""
    dat = open(path, "rb").read()

    def blob_at(sec):
        return dat[sec.foff:sec.foff + sec.fsize]

    code = [s for s in xbe.sections if s.name == ".text"]
    rows = []
    for target in targets:
        row = {"va": target, "calls": [], "refs": [], "table": None, "class": None,
               "slot": None, "vcall_sites": []}
        for sec in code:
            row["calls"] = rel_call_sites(blob_at(sec), sec.va, target)
        row["refs"] = dword_refs(xbe.sections, blob_at, target)
        for ref_va, sec_name in row["refs"]:
            hit = slot_of(tables, ref_va)
            if hit:
                table_va, slots, index = hit
                row["table"] = table_va
                row["class"] = names.get(table_va, "?")
                row["slot"] = index
                row["slots"] = slots
                for sec in code:
                    row["vcall_sites"].extend(
                        indirect_call_sites(blob_at(sec), sec.va, index * 4))
                break
        rows.append(row)
    return rows


def print_report(rows, functions):
    for row in rows:
        print("0x%08x" % row["va"])
        if row["calls"]:
            for site in row["calls"]:
                fn = containing(functions, site)
                print("  call  site 0x%08x  in %s" % (
                    site, "%s (0x%08x)" % (fn[2], fn[0]) if fn else "no known function"))
        else:
            print("  call  none (no E8 rel32 targets this address)")
        for ref_va, sec in row["refs"]:
            print("  ref   0x%08x  .%s" % (ref_va, sec.lstrip(".").lower()))
        if row["slot"] is not None:
            print("  slot  %s table 0x%08x slot %d of %d -> dispatch offset +0x%x" % (
                row["class"], row["table"], row["slot"], row["slots"], row["slot"] * 4))
            # The offset alone does not identify the class: every object's slot 59 answers to
            # `call [reg+0xec]`, so these are candidates until the surrounding code names the object.
            sites = sorted(row["vcall_sites"])
            if sites:
                print("  vcall %d candidate site(s) for +0x%x (offset only, class not implied):"
                      % (len(sites), row["slot"] * 4))
                for site, width in sites:
                    fn = containing(functions, site)
                    print("        0x%08x  in %s" % (
                        site, "%s (0x%08x)" % (fn[2], fn[0]) if fn else "no known function"))
            else:
                print("  vcall none for +0x%x" % (row["slot"] * 4))


def self_test():
    """Synthetic image: a relative call, a dword in a vtable, and a call through slot 59."""
    text = bytearray(b"\x90" * 0x40)
    # 0x1000: e8 rel32 -> 0x1010 ; 0x1020: call [eax+0xec]
    text[0x00:0x05] = b"\xe8" + struct.pack("<i", 0x1010 - 0x1005)
    text[0x20:0x26] = b"\xff\x90" + struct.pack("<I", 0xEC)
    text[0x30:0x35] = b"\xe8" + struct.pack("<i", 0x2000 - 0x1035)
    data = bytearray(b"\x00" * 0x40)
    struct.pack_into("<I", data, 0x10, 0x1010)          # table 0x2010 slot 4 = 0x1010

    assert rel_call_sites(bytes(text), 0x1000, 0x1010) == [0x1000], "relative call"
    assert rel_call_sites(bytes(text), 0x1000, 0x9999) == [], "no false relative call"
    assert indirect_call_sites(bytes(text), 0x1000, 0xEC) == [(0x1020, 4)], "indirect call"
    assert dword_refs([], lambda s: b"", 0) == [], "empty sections"
    hit = slot_of([(0x2010, 5)], 0x2020)
    assert hit == (0x2010, 5, 4), "slot index"
    assert slot_of([(0x2010, 5)], 0x2024) is None, "past the last slot"
    assert containing([(0x1000, 0x40, "F")], 0x1020) == (0x1000, 0x40, "F"), "containing"
    # the dword scan is a pure function of a blob: check it against the synthetic table
    class _S:
        def __init__(self, va, body):
            self.va, self.name, self.body = va, ".rdata", body

        def foff(self):
            return 0

    sec = _S(0x2010, bytes(data))
    refs = dword_refs([sec], lambda s: s.body, 0x1010)
    assert refs == [(0x2020, ".rdata")], "dword ref %r" % (refs,)
    print("vtable-reach: self-test ok "
          "(1 relative call, 1 indirect call, 1 dword ref, slot 4 of 4, no dump read)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("xbe", nargs="?", help="the dump's default.xbe")
    ap.add_argument("--va", action="append", default=[],
                    help="address or comma list, repeatable (hex, 0x optional)")
    ap.add_argument("--tables", default="inventory/xbe_class_tables.csv")
    ap.add_argument("--table-names", default="inventory/xbe_class_table_names.csv")
    ap.add_argument("--functions", default="symbols/xbox_functions.csv")
    ap.add_argument("--csv", help="write the rows here as well")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump")
    args = ap.parse_args()

    if args.check:
        self_test()
        return 0
    if not args.xbe or not args.va:
        ap.error("need an xbe and at least one --va (or --check)")

    targets = []
    for spec in args.va:
        for part in spec.split(","):
            part = part.strip()
            if part:
                targets.append(int(part, 16))
    xbe = Xbe(args.xbe)
    rows = report(xbe, args.xbe, targets, load_tables(args.tables),
                  load_table_names(args.table_names), load_functions(args.functions))
    print_report(rows, load_functions(args.functions))
    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["va", "direct_call_sites", "dword_refs", "class", "slot",
                        "dispatch_offset", "vcall_sites"])
            for row in rows:
                w.writerow(["0x%08x" % row["va"],
                            ";".join("0x%08x" % s for s in row["calls"]),
                            ";".join("0x%08x" % s for s, _ in row["refs"]),
                            row["class"] or "", row["slot"] if row["slot"] is not None else "",
                            "+0x%x" % (row["slot"] * 4) if row["slot"] is not None else "",
                            ";".join("0x%08x" % s for s, _ in sorted(row["vcall_sites"]))])
    return 0


if __name__ == "__main__":
    sys.exit(main())
