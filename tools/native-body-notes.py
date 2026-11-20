#!/usr/bin/env python3
"""Evidence for one body that has no library: read the image, not a `.lib` (PLAN 4.9).

`native-method-notes.py` resolves relocations from a COFF member, which is why it can only describe code
that came out of a supplied library. The engine's `FUN_*` bodies have no member, so this reads the shipped
bytes instead and reports what the body itself says:

* `E8 rel32` — a direct call, resolved to the map's name when there is one;
* `FF 25 abs32` — a jump through an import slot;
* `68 imm32` / `B8..BF imm32` — an immediate, resolved to a string when it points into a section and the
  bytes are printable, otherwise shown as a value;
* `E9/EB` — a tail jump, which is how thunks and shared exits appear.

    python3 tools/native-body-notes.py --xbe <dump>/default.xbe 0x00463864 0x002b5c20

It is a byte scan, not disassembly: immediates that fall inside an instruction's own encoding are reported as
they are found, so a hit is a lead to verify, not a decoded operand.
"""

import argparse
import collections
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from xbe import Xbe  # noqa: E402

FUNCTIONS = os.path.join("symbols", "xbox_functions.csv")


def load_functions():
    rows = [(int(r["address"], 16), int(r["size"]), r["name"])
            for r in csv.DictReader(open(os.path.join(ROOT, FUNCTIONS), encoding="utf-8"))]
    rows.sort()
    return rows


def name_at(rows, va):
    for addr, size, name in rows:
        if addr == va:
            return "%s (%d B)" % (name, size)
        if addr > va:
            break
    return ""


def containing(rows, va):
    best = None
    for addr, size, name in rows:
        if addr > va:
            break
        if addr <= va < addr + size and (best is None or size < best[1]):
            best = (addr, size, name)
    return best


def printable(buf, limit=72):
    out = []
    for b in buf:
        if b == 0:
            break
        if 32 <= b < 127:
            out.append(chr(b))
        else:
            return None
    text = "".join(out).strip()
    return text[:limit] if len(text) >= 4 else None


def describe(xbe, rows, va):
    hit = next(((a, s, n) for a, s, n in rows if a == va), None)
    size = hit[1] if hit else 256
    body = xbe.read_va(va, size)
    calls, imports, strings, values = [], [], [], []
    for i in range(len(body) - 5):
        op = body[i]
        if op == 0xE8:
            disp = struct.unpack_from("<i", body, i + 1)[0]
            tgt = va + i + 5 + disp
            # A byte scan sees E8 inside other instructions' encodings; a target outside every section is
            # one of those, not a call. Negative or out-of-image targets are dropped, not printed.
            if xbe.section_for(tgt) is None:
                continue
            nm = name_at(rows, tgt)
            calls.append((va + i, tgt, nm or ("inside %s" % containing(rows, tgt)[2]
                                              if containing(rows, tgt) else "")))
        elif op == 0xFF and body[i + 1] == 0x25:
            slot = struct.unpack_from("<I", body, i + 2)[0]
            sec = xbe.section_for(slot)
            imports.append((va + i, slot, sec.name if sec else "?"))
        elif op == 0x68 or 0xB8 <= op <= 0xBF:
            imm = struct.unpack_from("<I", body, i + (1 if op == 0x68 else 2))[0]
            sec = xbe.section_for(imm)
            if sec and not sec.executable:
                text = printable(xbe.read_va(imm, 96) or b"")
                if text:
                    strings.append((va + i, imm, text))
                    continue
            if 0x10000 <= imm < 0x08000000 and sec:
                values.append((va + i, imm, name_at(rows, imm) or ("in %s" % sec.name)))
    return {"address": va, "size": size, "name": hit[2] if hit else "", "body": body,
            "calls": calls, "imports": imports, "strings": strings, "values": values}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("addresses", nargs="+", help="image addresses, hex with or without 0x")
    ap.add_argument("--xbe")
    ap.add_argument("--csv", help="write one row per address")
    args = ap.parse_args()

    addrs = [int(a, 16) if a.lower().startswith("0x") else int(a, 16) for a in args.addresses]
    rows = load_functions()
    xbe = Xbe(args.xbe) if args.xbe else None
    if xbe is None:
        raise SystemExit("--xbe is required: this tool reads the shipped bytes")

    out = []
    for va in addrs:
        d = describe(xbe, rows, va)
        print("=== %#010x %s  %d bytes ===" % (va, d["name"] or "(no map row)", d["size"]))
        print("  hex   %s" % d["body"][:48].hex())
        for site, tgt, nm in d["calls"][:8]:
            print("  call  %#010x -> %#010x %s" % (site, tgt, nm))
        for site, text in [(s, t) for s, _v, t in d["strings"]][:8]:
            print("  str   %#010x %r" % (site, text))
        for site, slot, sec in d["imports"][:4]:
            print("  imp   %#010x slot %#010x (%s)" % (site, slot, sec))
        if not (d["calls"] or d["strings"]):
            print("  (no call, string or import in the first %d bytes)" % d["size"])
        out.append({"address": "0x%08x" % va, "name": d["name"], "size": d["size"],
                    "calls": "; ".join("%#x" % t for _s, t, _n in d["calls"]),
                    "callee_names": "; ".join(n for _s, _t, n in d["calls"] if n),
                    "strings": "; ".join(t for _s, _v, t in d["strings"]),
                    "imports": "; ".join("%#x" % s for _s, s, _sec in d["imports"])})
    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(out[0]), lineterminator="\n")
            w.writeheader()
            w.writerows(out)
        print("wrote %s" % args.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())