#!/usr/bin/env python3
"""Locate the `GNatives` dispatch table in `default.xbe` and record what the evidence supports.

The table is not in the file. `GNatives` is a 4096-entry array in the *uninitialised* part of
`.data`: the image contains no bytes for it, and the native implementations are patched in at
startup. Any index -> address map built from file bytes is therefore a map of something else --
which is exactly what happened once here, and this tool now refuses to make that mistake twice.

Two things are written, and nothing else:

* `inventory/gnatives_candidates.csv` -- every run of dwords in `.data`/`.rdata` long enough to be
  a function-pointer table, with the structural tests that separate a dispatch table from a
  compiler-generated pointer array. A candidate is rejected when its entries are monotonically
  increasing (a dispatch table is ordered by declaration, not by code address) and when no code
  site indexes it (`call [table + reg*4]`).
* `inventory/gnatives_table.csv` -- the table the build's own VM asserts identify. The engine
  spells the array's name and its bound in the assert text around the interpreter
  ("GNatives[iNative]!=0", "iNative<EX_Max"), so the table address and entry count are read out of
  the code that asserts about them rather than guessed from a scan.

With `--runtime` the tool reads a 16 KB memory dump of the table taken from the emulator and joins
it with the declared indices, writing `inventory/gnatives_slots.csv` (index -> address -> name). The
dump itself is guest memory: it stays under `$WORK`, only the derived address map is committed
(`symbols/` is address structure, never content).

Usage:
    gnatives.py DUMP/default.xbe -o inventory [--runtime $WORK/gnatives/runtime.bin]
"""
import argparse
import csv
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xbe import Xbe  # noqa: E402

# The assert strings the interpreter is compiled with: the array's name and its bound.
TABLE_ASSERT = b"GNatives[iNative]!=0"
BOUND_ASSERT = b"iNative<EX_Max"
# `mov reg,[reg*4+disp32]` (8b 04/0c/14/1c/24/2c/34/3c) and `call [reg*4+disp32]` (ff 14) forms.
INDEXED = [(0x8B, r) for r in (0x04, 0x0C, 0x14, 0x1C, 0x24, 0x2C, 0x34, 0x3C)] + [(0xFF, 0x14)]
SIB_INDEXED = {0x85, 0x8D, 0x95, 0x9D, 0xA5, 0xAD, 0xB5, 0xBD}


def sections(image):
    text = [s for s in image.sections if s.name == ".text"][0]
    return text, [s for s in image.sections if s.name in (".data", ".rdata")]


def dwords(image, section):
    """The initialised bytes of a section as dwords; `.data` is mostly uninitialised (BSS)."""
    avail = min(section.fsize, len(image.data) - section.foff)
    count = max(0, avail // 4)
    return list(struct.unpack_from("<%dI" % count, image.data, section.foff))


def code_xrefs(text, image, va):
    pattern = struct.pack("<I", va)
    body = image.data[text.foff:text.foff + text.fsize]
    out, i = [], body.find(pattern)
    while i != -1:
        out.append(text.va + i)
        i = body.find(pattern, i + 1)
    return out


def xref_kind(text, image, site):
    """How the code reaches an address: an indexed table use, a plain address load, or neither."""
    body = image.data[text.foff:text.foff + text.fsize]
    off = site - text.va

    def at(k):
        return body[k] if 0 <= k < len(body) else None

    # `call [reg*4+disp]` / `mov reg,[reg*4+disp]`: the immediate is the 4th-7th byte of the inset.
    for op, modrm in INDEXED:
        for back in range(1, 8):
            if at(off - back) == op and at(off + 1 - back) == modrm and at(off + 2 - back) in SIB_INDEXED:
                return "indexed"
    # `mov reg, imm32` (b8..bf) / `push imm32` (68) / `mov [mem], imm32` (c7 05) reach it too.
    if at(off - 5) in range(0xB8, 0xC0) or at(off - 5) == 0x68:
        return "address-load"
    if at(off - 2) == 0xC7 and at(off - 1) == 0x05:
        return "address-load"
    return "other"


def scan_candidates(text, image, floors):
    lo, hi = text.va, text.va + text.vsize
    rows = []
    for section in floors:
        vals = dwords(image, section)
        i = 0
        while i < len(vals):
            if vals[i] == 0 or lo <= vals[i] < hi:
                j = i
                while j < len(vals) and (vals[j] == 0 or lo <= vals[j] < hi):
                    j += 1
                if j - i >= 128:
                    rows.append({"section": section, "start": section.va + i * 4, "vals": vals[i:j]})
                i = j
            else:
                i += 1
    out = []
    for row in rows:
        run = row["vals"]
        live = [v for v in run if v]
        if not live:
            continue
        steps = [run[k + 1] - run[k] for k in range(len(run) - 1)]
        monotonic = sum(1 for s in steps if s >= 0) / len(steps)
        xrefs = code_xrefs(text, image, row["start"])
        kinds = sorted({xref_kind(text, image, site) for site in xrefs})
        verdict = "candidate"
        if monotonic > 0.95:
            verdict = "rejected:monotonic"
        elif "indexed" not in kinds:
            verdict = "rejected:no-indexed-xref"
        out.append({
            "section": row["section"].name,
            "start": row["start"],
            "entries": len(run),
            "non_null": len(live),
            "distinct": len(set(live)),
            "monotonic_pct": round(100.0 * monotonic, 1),
            "entry0_null": int(run[0] == 0),
            "code_xrefs": len(xrefs),
            "xref_kinds": "|".join(kinds),
            "xref_vas": " ".join("0x%08x" % v for v in xrefs[:8]),
            "verdict": verdict,
        })
    out.sort(key=lambda r: (-r["non_null"], r["start"]))
    return out


def find_string(image, needle):
    """VA of a NUL-terminated ASCII/UTF-16 string in any section that holds initialised bytes."""
    off = image.data.find(needle)
    if off < 0:
        return None
    for s in image.sections:
        if s.foff <= off < s.foff + s.fsize:
            return s.va + (off - s.foff)
    return None


def table_from_asserts(text, image):
    """Read the array address and its bound out of the interpreter's own assert sites."""
    table_va = find_string(image, TABLE_ASSERT)
    bound_va = find_string(image, BOUND_ASSERT)
    if not table_va:
        return None
    sites = code_xrefs(text, image, table_va)
    if not sites:
        return None
    site = sites[0]
    body = image.data[text.foff:text.foff + text.fsize]
    start = max(0, site - text.va - 96)
    window = body[start:start + 192]
    base = text.va + start
    found, bound, bound_site = set(), None, None
    for k in range(len(window) - 7):
        if window[k] == 0x66 and window[k + 1] == 0x3D:                    # cmp ax, imm16
            bound, bound_site = struct.unpack_from("<H", window, k + 2)[0], base + k
        for op, modrm in INDEXED:
            if window[k] == op and window[k + 1] == modrm and window[k + 2] in SIB_INDEXED:
                found.add(struct.unpack_from("<I", window, k + 3)[0])
    if len(found) != 1:
        return None
    table = found.pop()
    lo = text.va
    in_file = any(s.va <= table < s.va + min(s.vsize, s.fsize) for s in image.sections)
    return {
        "table_va": table,
        "entries": bound,
        "bound_site": bound_site,
        "assert_va": table_va,
        "bound_assert_va": bound_va,
        "assert_site": site,
        "in_file_bytes": int(in_file),
    }


ENTRIES = 4096          # EX_Max, read from the interpreter's assert
TABLE_BYTES = ENTRIES * 4
DEFAULT_STUB = 0x000404a0   # what the startup initialiser leaves in every unclaimed slot


def load_runtime(path, table_va, image):
    """Validate a runtime dump and return its dwords, or exit with the reason it is unusable."""
    data = open(path, "rb").read()
    if len(data) != TABLE_BYTES:
        sys.exit("%s: %d bytes, expected %d (EX_Max x 4)" % (path, len(data), TABLE_BYTES))
    vals = list(struct.unpack("<%dI" % ENTRIES, data))
    text = [s for s in image.sections if s.name == ".text"][0]
    lo, hi = text.va, text.va + text.vsize
    outside = [v for v in vals if v and not lo <= v < hi]
    if outside:
        sys.exit("%s: %d entries point outside .text -- not this table" % (path, len(outside)))
    if len(set(vals)) == 1:
        sys.exit("%s: every entry is %#x -- the dump was taken before anything registered"
                 % (path, vals[0]))
    return vals


def write_slots(path, vals, names_path):
    """index -> address -> name, with what each name rests on kept in its own column."""
    declared = {}
    with open(names_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            declared[int(row["index"])] = row
    rows = []
    for index, va in enumerate(vals):
        if va == DEFAULT_STUB:
            continue
        entry = declared.get(index)
        rows.append({
            "index": index,
            "address": "0x%08x" % va,
            "name": entry["name"] if entry else "",
            "evidence": "declared" if entry else "slot-only",
            "declarers": entry["declarers"] if entry else "",
        })
    columns = ["index", "address", "name", "evidence", "declarers"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return rows, declared


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xbe")
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--runtime", default="",
                    help="16 KB dump of GNatives taken from the running build (see docs/XEMU.md)")
    ap.add_argument("--native-names", default="inventory/native_names.csv")
    args = ap.parse_args()

    image = Xbe(args.xbe)
    text, floors = sections(image)
    cands = scan_candidates(text, image, floors)

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "gnatives_candidates.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["section", "start_va", "entries", "non_null", "distinct", "monotonic_pct",
                    "entry0_null", "code_xrefs", "xref_kinds", "xref_vas", "verdict"])
        for r in cands:
            w.writerow([r["section"], "0x%08x" % r["start"], r["entries"], r["non_null"],
                        r["distinct"], r["monotonic_pct"], r["entry0_null"], r["code_xrefs"],
                        r["xref_kinds"], r["xref_vas"], r["verdict"]])

    table = table_from_asserts(text, image)
    runtime = None
    if args.runtime and table:
        runtime = load_runtime(args.runtime, table["table_va"], image)
    path = os.path.join(args.out, "gnatives_table.csv")
    columns = ["name", "address", "entries", "in_file_bytes", "evidence"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        w.writeheader()
        if table:
            w.writerow({
                "name": "GNatives",
                "address": "0x%08x" % table["table_va"],
                "entries": table["entries"],
                "in_file_bytes": table["in_file_bytes"],
                "evidence": "assert '%s' at 0x%08x; indexed load at the same site; bound "
                            "'%s' (%s)" % (TABLE_ASSERT.decode(), table["assert_site"],
                                           BOUND_ASSERT.decode(),
                                           "0x%04x" % table["entries"] if table["entries"] else "?"),
            })

    candidates = [r for r in cands if r["verdict"] == "candidate"]
    print("%d pointer-run candidates: %d pass, %d rejected"
          % (len(cands), len(candidates), len(cands) - len(candidates)))
    for r in cands[:6]:
        print("  0x%08x %-7s %5d entries %5.1f%% monotonic xrefs=%s -> %s"
              % (r["start"], r["section"], r["entries"], r["monotonic_pct"],
                 r["xref_kinds"] or "-", r["verdict"]))
    if table:
        print("GNatives: 0x%08x, %s entries, initialised bytes in file: %s"
              % (table["table_va"], table["entries"] or "?", "yes" if table["in_file_bytes"] else "no"))
    else:
        print("GNatives: not identified -- the assert scan did not resolve a single table address")
    print("wrote %s and gnatives_candidates.csv" % path)

    if runtime is not None:
        slots_path = os.path.join(args.out, "gnatives_slots.csv")
        rows, declared = write_slots(slots_path, runtime, args.native_names)
        named = sum(1 for r in rows if r["name"])
        registered = {r["index"] for r in rows}
        absent = [i for i in declared if i not in registered]
        print("runtime dump: %d of %d slots registered, %d distinct, %d named from declarations, "
              "%d slot-only" % (len(rows), ENTRIES, len({r["address"] for r in rows}), named,
                                len(rows) - named))
        if absent:
            print("declared indices still at the default stub: %s"
                  % ", ".join(str(i) for i in sorted(absent)[:12]))
        print("wrote %s" % slots_path)
    return 0 if table else 1


if __name__ == "__main__":
    sys.exit(main())
