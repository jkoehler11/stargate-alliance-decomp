#!/usr/bin/env python3
"""Which `GNatives` slots the image writes statically, and what it stores in them (U7's option 2).

`GNatives` (`0x006313e8`, `EX_Max` = 4,096 entries) is filled at startup: every slot gets the default stub
(`0x000404a0`) and individual slots are patched. U7 measured the *runtime* table from a guest dump
(`inventory/gnatives_slots.csv`, 356 registered slots). This tool reads the same table from the image's own code,
which is the other side of the question and needs no boot: which slots exist as a store instruction, in which
function, and what value the instruction stores.

Two forms target an absolute slot address, so both are visible statically:

* `C7 05 <disp32> <imm32>` -- `mov dword [slot], imm32`;
* `89 <modrm> <disp32>`    -- `mov [slot], reg`, where the value is the register's, loaded by the instruction that
  ends at the store: `A1 <disp32>` / `8B /r` (a pointer read from a data table) or `B8+r` / `C7 C0+r` (an immediate).

A loop that fills the table through a register (`mov [ecx+eax*4], edx`) is invisible here *by construction* -- the
modrm byte encodes no absolute address -- so "a slot with no store site" means "written by some path this scan
cannot see", not "never written". The numbers say how much of the map that is.

    python3 tools/gnatives-writes.py --xbe DUMP/default.xbe -o inventory      # the artifact
    python3 tools/gnatives-writes.py --check                                  # dump-free, in `make ci`
"""
import argparse
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from xbe import Xbe  # noqa: E402

BASE = 0x006313E8
MIRROR = 0x006353E8          # the second 4,096-entry table the same initialisers fill, 16 KB past `GNatives`
SLOTS = 4096
COLUMNS = ["address", "slot", "form", "value", "value_kind", "slot_literal", "function"]
STORE_MODRM = {0x05: 0, 0x0D: 1, 0x15: 2, 0x1D: 3, 0x25: 4, 0x2D: 5, 0x35: 6, 0x3D: 7}
LITERAL_PATTERN = b"\xc7\x05\xe8\x53\x63\x00"      # `mov dword [0x006353e8], imm32`: the slot annotation


def functions(path):
    """[(start, end, name)] from the symbol map, so a store site is reported with the function that makes it."""
    spans = []
    if not path or not os.path.isfile(path):
        return spans
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            spans.append((int(r["address"], 16), int(r["address"], 16) + int(r["size"]), r["name"]))
    spans.sort()
    return spans


def owner(spans, va):
    for start, end, name in spans:
        if start <= va < end:
            return name
    return ""


def slot_of(disp, base):
    return (disp - base) // 4 if base <= disp < base + SLOTS * 4 else None


def preceding_value(blob, site, reg):
    """The instruction that ends exactly at `site` wrote `reg`: return (value_address_or_imm, kind).

    Lengths are what make this safe: `A1`/`B8+r` are 5 bytes, `C7 C0+r`/`8B /r` are 6, so the instruction to look at
    starts at `site-5` or `site-6`. Walking backwards looking for a matching pattern instead lands inside the
    previous function's `ret`.
    """
    if site >= 5:
        a = blob[site - 5]
        if a == 0xA1 and reg == 0:
            return struct.unpack_from("<I", blob, site - 4)[0], "table"
        if 0xB8 <= a <= 0xBF and a - 0xB8 == reg:
            return struct.unpack_from("<I", blob, site - 4)[0], "imm"
    if site >= 6:
        a, modrm = blob[site - 6], blob[site - 5]
        if a == 0x8B and (modrm & 0xC7) == 0x05 and (modrm >> 3) == reg:
            return struct.unpack_from("<I", blob, site - 4)[0], "table"
        if a == 0xC7 and modrm == 0xC0 + reg:
            return struct.unpack_from("<I", blob, site - 4)[0], "imm"
    return None, ""


def slot_literal(blob, site, window=0x40):
    """The slot index the same setter writes to `0x006353E8`, when it is within `window` bytes of the store.

    Every one of these setters annotates its slot: `c7 05 e8 53 63 00 <index>` six bytes of pattern and the index
    as an immediate. It is the second, independent statement of which slot the store is for -- the store's own
    displacement is the first -- so the tool reports both instead of deriving one from the other.
    """
    at = blob.rfind(LITERAL_PATTERN, max(0, site - window), site)
    if at < 0 or at + 10 > len(blob):
        return None
    return struct.unpack_from("<I", blob, at + 6)[0]


def scan_blocks(blocks, spans, read_va=None, base=BASE):
    """[(va, blob)] -> rows. `read_va` resolves a `table` value to the dword it points at, when it is mapped."""
    rows = []
    for sec_va, blob in blocks:
        for i in range(len(blob) - 10):
            op, modrm = blob[i], blob[i + 1]
            if op == 0xC7 and modrm == 0x05:
                disp = struct.unpack_from("<I", blob, i + 2)[0]
                slot = slot_of(disp, base)
                if slot is None:
                    continue
                value, kind = struct.unpack_from("<I", blob, i + 6)[0], "imm"
            elif op == 0x89 and modrm in STORE_MODRM:
                disp = struct.unpack_from("<I", blob, i + 2)[0]
                slot = slot_of(disp, base)
                if slot is None:
                    continue
                value, kind = preceding_value(blob, i, STORE_MODRM[modrm])
                if kind == "table":
                    if read_va is None:
                        kind = "table-missing"          # no image to read the pointer through (synthetic runs)
                    else:
                        got = read_va(value)
                        if got is None:
                            kind = "table-unread"
                            value = None
                        else:
                            value = got
            else:
                continue
            va = sec_va + i
            literal = slot_literal(blob, i)
            rows.append({"address": "%#010x" % va, "slot": slot, "form": "%#04x" % ((op << 8) | modrm),
                         "value": "" if value is None else "%#010x" % value, "value_kind": kind,
                         "slot_literal": "" if literal is None else "%d" % literal,
                         "function": owner(spans, va)})
    rows.sort(key=lambda r: (r["slot"], int(r["address"], 16)))
    return rows


def runtime(path=os.path.join(ROOT, "inventory", "gnatives_slots.csv")):
    out = {}
    if os.path.isfile(path):
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                out[int(r["index"])] = r["address"].lower()
    return out


def report(rows, rt):
    """The numbers the doc quotes: coverage of the runtime map, and agreement where both sides name a slot."""
    slots = {}
    for r in rows:
        slots.setdefault(r["slot"], []).append(r)
    both = set(slots) & set(rt)
    agree = [s for s in both if any(r["value"].lower() == rt[s] for r in slots[s] if r["value"])]
    out = ["%d store sites over %d slots; %d of them store a value" %
           (len(rows), len(slots), sum(1 for r in rows if r["value"]))]
    out.append("runtime table %d slots: static and runtime %d, static only %d, runtime only %d"
               % (len(rt), len(both), len(set(slots) - set(rt)), len(set(rt) - set(slots))))
    out.append("of the %d shared slots, %d store the runtime address" % (len(both), len(agree)))
    lit = [r for r in rows if r["slot_literal"]]
    same = [r for r in lit if int(r["slot_literal"]) == r["slot"]]
    out.append("%d of the %d sites also carry their slot index as an immediate at %#010x, %d of them agreeing"
               % (len(lit), len(rows), MIRROR, len(same)))
    return out


SYNTHETIC = bytes([
    0xA1, 0x04, 0x24, 0x5C, 0x00,              # 0: mov eax, [0x005c2404]
    0x89, 0x05, 0xE8, 0x13, 0x63, 0x00,        # 5: mov [GNatives+0], eax   -> slot 0 via the table
    0xC7, 0x05, 0xF0, 0x13, 0x63, 0x00,        # 11: mov dword [GNatives+8], imm32 -> slot 2
    0x00, 0x10, 0x40, 0x00,
    0x89, 0x0D, 0xE8, 0x33, 0x63, 0x00,        # 21: a store outside the range (base-0x2000) is ignored
    0x89, 0x15, 0xE8, 0x13, 0x63, 0x00,        # 27: mov [GNatives+0], edx with no preceding write -> no value
])

SYNTHETIC_LIT = bytes([
    0xC7, 0x05, 0xE8, 0x53, 0x63, 0x00,        # 0: mov dword [0x006353e8], 5   (the slot annotation)
    0x05, 0x00, 0x00, 0x00,
    0xC7, 0x05, 0xFC, 0x13, 0x63, 0x00,        # 10: mov dword [GNatives+0x14], imm32 -> slot 5
    0x00, 0x20, 0x40, 0x00,
    0xCC,                                      # padding: the scanner stops 10 bytes short of the block's end
])


def synthetic():
    problems = []
    rows = scan_blocks([(0x1000, SYNTHETIC), (0x2000, SYNTHETIC_LIT)], [],
                       read_va=lambda va: 0x00401234 if va == 0x005C2404 else None)
    got = {(r["address"], r["slot"]): r for r in rows}
    if len(rows) != 4:
        problems.append("synthetic: expected 4 rows, got %d" % len(rows))
    a = got.get(("%#010x" % 0x1005, 0))
    if not a or a["value"] != "0x00401234" or a["value_kind"] != "table":
        problems.append("synthetic: the table-form store did not resolve through the pointer")
    b = got.get(("%#010x" % 0x100B, 2))
    if not b or b["value"] != "0x00401000" or b["value_kind"] != "imm":
        problems.append("synthetic: the immediate-form store did not resolve")
    if rows and any(r["slot_literal"] for r in rows if r["slot"] != 5):
        problems.append("synthetic: a store with no annotation in its window was given one")
    c = got.get(("%#010x" % 0x200A, 5))
    if not c or c["slot_literal"] != "5" or c["value"] != "0x00402000":
        problems.append("synthetic: the slot annotation was not read from the setter")
    return problems


def check(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    problems = list(synthetic())
    if not rows:
        problems.append("no rows")
    elif sorted(rows[0]) != sorted(COLUMNS):
        problems.append("columns")
    prev = None
    for r in rows:
        key = (int(r["slot"]), int(r["address"], 16))
        if prev is not None and key < prev:
            problems.append("rows not in (slot, address) order at %s" % r["address"])
            break
        prev = key
        if not 0 <= int(r["slot"]) < SLOTS:
            problems.append("%s: slot %s outside the table" % (r["address"], r["slot"]))
            break
        if r["value_kind"] not in ("imm", "table", "table-missing", ""):
            problems.append("%s: value_kind %r" % (r["address"], r["value_kind"]))
            break
        if bool(r["value"]) != (r["value_kind"] in ("imm", "table")):
            problems.append("%s: value %r disagrees with kind %r" % (r["address"], r["value"], r["value_kind"]))
            break
        if r.get("slot_literal") and int(r["slot_literal"]) != int(r["slot"]):
            problems.append("%s: slot literal %s disagrees with slot %s"
                            % (r["address"], r["slot_literal"], r["slot"]))
            break
    if problems:
        print("FAIL gnatives-writes: " + "; ".join(problems[:4]))
        return 1
    print("ok   gnatives-writes: %d store sites over %d slots (%d carrying their slot literal)"
          % (len(rows), len({r["slot"] for r in rows}), sum(1 for r in rows if r.get("slot_literal"))))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("--symbols", default=os.path.join(ROOT, "symbols", "xbox_functions.csv"))
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    path = os.path.join(args.out, "gnatives_write_sites.csv")
    if args.check:
        return check(path)
    if not args.xbe:
        sys.exit("--xbe is required (this reads the image; --check is the dump-free half)")
    xbe = Xbe(args.xbe)
    spans = functions(args.symbols)
    blocks = [(sec.va, xbe.data[sec.foff:sec.foff + sec.fsize]) for sec in xbe.sections]

    def read_va(va):
        try:
            return struct.unpack("<I", xbe.read_va(va, 4))[0]
        except Exception:                                    # unmapped, or past the end of a section
            return None

    rows = scan_blocks(blocks, spans, read_va=read_va)
    for line in report(rows, runtime()):
        print(line)
    near = scan_blocks(blocks, spans, read_va=read_va, base=MIRROR)
    print("%#010x: %d store sites over %d offsets -- offset 0 is the setters' slot annotation, the rest are "
          "other structures" % (MIRROR, len(near), len({r["slot"] for r in near})))
    os.makedirs(args.out, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("%d rows -> %s" % (len(rows), path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
