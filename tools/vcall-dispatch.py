#!/usr/bin/env python3
"""Every `.text` site that dispatches through displacement `+0x4c`/`+0x50` -- slots 19 and 20 (U8).

`inventory/class_table_slot_pairs.csv` settled *what is installed* in the two slots: `0x00020f00` (a one-byte
`ret`) twice in 327 of the 492 tables, and `0x000c4f60`/`0x000c6d00` in the 165 under `Actor`. It did not
settle *who calls them*, and that is the question U8 still carries. A virtual call is `FF /2` through a
register plus a fixed displacement, which is a byte pattern rather than a resolved call, so the census of
those sites is a `.text` scan and needs no guest:

    slot 19 -> [reg+19*4] = [reg+0x4c]
    slot 20 -> [reg+20*4] = [reg+0x50]

The displacement is **not unique to this table** -- any object with fields at those offsets produces the same
bytes -- so a row here is a *candidate* dispatch of the pair, not a proof. What the census does establish is
the population and its shape: how many sites, through which registers, inside which functions. A site that
sits inside a named engine body names the caller's role even when the receiver is untyped, which is the
useful half of the answer; the runtime half is a breakpoint on the two bodies
(`tools/vcall-stops.py` reads a `tools/xemu/bp-args.py` record into caller/receiver rows).

    python3 tools/vcall-dispatch.py --xbe $SG1_DUMP/default.xbe
    python3 tools/vcall-dispatch.py --xbe ... -o /tmp/x.csv
    python3 tools/vcall-dispatch.py --check        # synthetic bytes, no dump, writes nothing

Addresses and counts only: nothing here carries game data.
"""
import argparse
import csv
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from xbe import Xbe  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INVENTORY = os.path.join(REPO, "inventory")
FUNCTIONS = os.path.join(REPO, "symbols", "xbox_functions.csv")
OUT = os.path.join(INVENTORY, "vcall_dispatch_sites.csv")

# `call [reg+disp8]` and `call [reg+disp32]`: the six general registers, in modrm order.
REGISTERS = {0: "eax", 1: "ecx", 2: "edx", 3: "ebx", 5: "ebp", 6: "esi", 7: "edi"}
DISP8 = 0x40
DISP32 = 0x80
SLOTS = {0x4C: 19, 0x50: 20}
HEADER = ["site_va", "section", "slot", "disp", "reg", "function_va", "function"]


def load_functions(path):
    out = {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                va = int(row["address"], 0)
            except (KeyError, ValueError):
                continue
            try:
                size = int(row.get("size") or 0, 0)
            except ValueError:
                size = 0
            out[va] = (row.get("name") or "", size)
    return out


def resolve(va, functions):
    """(function_va, symbol): exact start, then containment in a sized body, then nearest below."""
    if va in functions:
        return va, functions[va][0]
    best = None
    for start, (name, size) in functions.items():
        if start <= va and size and va < start + size:
            if best is None or start > best[0]:
                best = (start, name)
    if best:
        return best
    below = None
    for start in functions:
        if start <= va and (below is None or start > below):
            below = start
    if below is None:
        return None, ""
    return below, functions[below][0] + "+0x%x" % (va - below)


def scan(blob, base):
    """[(site_va, slot, disp, reg)] for every `FF /2` with a displacement in SLOTS."""
    sites = []
    for i in range(len(blob) - 6):
        if blob[i] != 0xFF:
            continue
        modrm = blob[i + 1]
        # FF /2 is `call r/m32`: reg field 2, and only the [reg+disp] (no SIB) forms are a vtable index.
        if (modrm >> 3) & 7 != 2:
            continue
        top = modrm & 0xC0
        rm = modrm & 7
        if rm == 4 or rm not in REGISTERS:      # SIB / esp base: a stack frame, not an object
            continue
        if top == DISP8:
            disp = blob[i + 2]
        elif top == DISP32:
            disp = struct.unpack_from("<I", blob, i + 2)[0]
        else:
            continue
        if disp not in SLOTS:
            continue
        sites.append((base + i, SLOTS[disp], disp, REGISTERS[rm]))
    return sites


def selftest():
    base = 0x1000
    blob = bytearray(b"\x90" * 64)
    # call [ecx+0x4c]   -> slot 19, displacement form
    blob[8:11] = b"\xff\x51\x4c"
    # call [esi+0x50] (disp32) -> slot 20
    blob[16:22] = b"\xff\x96\x50\x00\x00\x00"
    # call [eax+0x2c] -> not a slot, and `call [esp+0x4c]` (SIB) must not count either
    blob[24:27] = b"\xff\x50\x2c"
    blob[32:35] = b"\xff\x54\x24\x4c"
    sites = scan(bytes(blob), base)
    assert [(s[0], s[1], s[3]) for s in sites] == [(base + 8, 19, "ecx"), (base + 16, 20, "esi")], sites
    functions = {base + 4: ("FUN_00001004", 0x40)}
    assert resolve(base + 8, functions) == (base + 4, "FUN_00001004")
    assert resolve(base + 100, functions)[1].startswith("FUN_00001004+0x")
    print("selftest ok: two sites, displacement and SIB forms both classified")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out", default=OUT)
    ap.add_argument("--functions", default=FUNCTIONS)
    ap.add_argument("--check", action="store_true", help="synthetic self-test; no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        selftest()
        return 0
    if not args.xbe:
        ap.error("--xbe is required unless --check")
    xbe = Xbe(args.xbe)
    functions = load_functions(args.functions) if os.path.exists(args.functions) else {}
    rows = []
    for section in xbe.sections:
        if not section.executable:
            continue
        try:
            blob = xbe.read_va(section.va, section.vsize)
        except (KeyError, IndexError):
            continue
        for site, slot, disp, reg in scan(blob, section.va):
            fva, name = resolve(site, functions)
            rows.append({"site_va": "0x%08x" % site, "section": section.name, "slot": slot,
                         "disp": "0x%02x" % disp, "reg": reg,
                         "function_va": "0x%08x" % fva if fva is not None else "", "function": name})
    rows.sort(key=lambda r: (r["slot"], r["site_va"]))
    with open(args.out, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=HEADER, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    def summarise(label, subset):
        by_slot = {}
        by_reg = {}
        named = {}
        for row in subset:
            by_slot[row["slot"]] = by_slot.get(row["slot"], 0) + 1
            by_reg[row["reg"]] = by_reg.get(row["reg"], 0) + 1
            if not row["function"].startswith("FUN_"):
                named[row["function"]] = named.get(row["function"], 0) + 1
        print("%s: %d site(s) -- %s" % (label, len(subset),
                                        ", ".join("slot %s %d" % (k, v) for k, v in sorted(by_slot.items()))))
        print("  by register: " + ", ".join("%s %d" % (k, v) for k, v in sorted(by_reg.items())))
        print("  %d inside a named function, %d distinct" % (sum(named.values()), len(named)))
        for name, n in sorted(named.items(), key=lambda kv: (-kv[1], kv[0])):
            print("    %-40s %d" % (name, n))

    sections = {}
    for row in rows:
        sections[row["section"]] = sections.get(row["section"], 0) + 1
    print("%d site(s) in %d executable section(s)" % (len(rows), len(sections)))
    print("  per section: " + ", ".join("%s %d" % (k, v) for k, v in sorted(sections.items())))
    # The engine's own code is `.text`; the seven library sections are statically linked Microsoft code whose
    # `+0x50` is a field of some library struct, never slot 20 of a `UClass` table.
    summarise("game code (.text)", [r for r in rows if r["section"] == ".text"])
    summarise("statically linked libraries", [r for r in rows if r["section"] != ".text"])
    print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
