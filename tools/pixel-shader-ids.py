#!/usr/bin/env python3
"""The pixel-shader id table: which shader each id compiles (PLAN 4.9 / Phase 6)

`docs/NATIVE_NOTES.md` established that the pixel dispatcher is a switch over ids 1..0x49 that maps each id
through a byte table to a case and jumps through a 32-entry table. This tool reads both tables straight out of
the dispatcher's own prologue — by byte pattern, so a different build fails loudly instead of silently reading
the wrong data — and then resolves each case to the shader program it compiles by following the case body's
first `call` to the per-program wrapper.

    python3 tools/pixel-shader-ids.py --xbe ~/sg1/work/dump-local/default.xbe \\
        -o inventory/pixel_shader_ids.csv

The wrapper -> program map comes from `inventory/shader_programs.csv` plus the same absolute-reference scan
`tools/shader-program-refs.py` uses, so the two artifacts agree by construction.
"""

import argparse
import bisect
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from xbe import Xbe  # noqa: E402

COLUMNS = ["id", "case", "case_address", "wrapper", "program", "version"]
PROGRAMS = os.path.join("inventory", "shader_programs.csv")
FUNCTIONS = os.path.join("symbols", "xbox_functions.csv")

# The dispatcher is unnamed in the map; its address is a property of this image. The patterns below are how
# the tool proves it is still looking at that function.
DISPATCHER = 0x002AF230
DEFAULT_CASE = 31


def tables(xbe, dispatcher):
    """(index_table_va, jump_table_va, id_count) read from the dispatcher's own instructions."""
    blob = xbe.read_va(dispatcher, 0x100)
    i = blob.find(b"\x0f\xb6\x88")          # movzx ecx, byte [eax+disp32]
    j = blob.find(b"\xff\x24\x8d")          # jmp dword near [ecx*4+disp32]
    if i < 0 or j < 0:
        raise SystemExit("dispatcher prologue not recognised at %#x" % dispatcher)
    index_va = struct.unpack_from("<I", blob, i + 3)[0]
    jump_va = struct.unpack_from("<I", blob, j + 3)[0]
    return index_va, jump_va


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xbe", default=os.environ.get("SG1_XBE") or os.path.expanduser("~/sg1/work/dump-local/default.xbe"))
    ap.add_argument("--functions", default=FUNCTIONS)
    ap.add_argument("--programs", default=PROGRAMS)
    ap.add_argument("--dispatcher", type=lambda s: int(s, 0), default=DISPATCHER)
    ap.add_argument("-o", "--out", default=os.path.join("inventory", "pixel_shader_ids.csv"))
    ap.add_argument("--check", action="store_true", help="write nothing; fail if the artifact is stale or wrong")
    args = ap.parse_args()

    xbe_path = os.path.expanduser(args.xbe)
    if not os.path.exists(xbe_path):
        # The CI runner has no game dump; `tool-check-contract` accepts exit 2 + "required".
        print("pixel-shader-ids: --xbe required (no dump at %s)" % xbe_path)
        return 2
    xbe = Xbe(xbe_path)
    index_va, jump_va = tables(xbe, args.dispatcher)

    index = xbe.read_va(index_va, 0x49)
    jump = list(struct.unpack("<32I", xbe.read_va(jump_va, 32 * 4)))

    rows = sorted((int(r["address"], 16), int(r["size"])) for r in csv.DictReader(open(args.functions, encoding="utf-8")))
    starts = [r[0] for r in rows]

    def owner(va):
        i = bisect.bisect_right(starts, va) - 1
        if i >= 0 and rows[i][0] <= va < rows[i][0] + rows[i][1]:
            return rows[i][0]
        return None

    programs = {}
    for r in csv.DictReader(open(args.programs, encoding="utf-8")):
        programs[int(r["address"], 16)] = r["version"]

    wrapper = {}
    for va in programs:
        for ref in xbe.xrefs_to(va):
            w = owner(ref)
            if w:
                wrapper[w] = va

    targets = sorted({t for t in jump if args.dispatcher < t < args.dispatcher + 0x1000})
    out = []
    for pid in range(1, 0x4A):
        case = index[pid - 1]
        if case >= DEFAULT_CASE:
            continue
        at = jump[case]
        nxt = min([u for u in targets if u > at] or [at + 0x400])
        blob = xbe.read_va(at, min(nxt, at + 0x400) - at)
        for i in range(len(blob) - 5):
            if blob[i] != 0xE8:
                continue
            rel = struct.unpack_from("<i", blob, i + 1)[0]
            tgt = (at + i + 5 + rel) & 0xFFFFFFFF
            if tgt in wrapper:
                pva = wrapper[tgt]
                out.append({"id": pid, "case": case, "case_address": "0x%08x" % at,
                            "wrapper": "0x%08x" % tgt, "program": "0x%08x" % pva,
                            "version": programs[pva]})

    wanted = [{k: str(r[k]) for k in COLUMNS} for r in out]
    if args.check:
        have = []
        if os.path.exists(args.out):
            have = [{k: r[k] for k in COLUMNS} for r in csv.DictReader(open(args.out, encoding="utf-8"))]
        if have != wanted:
            raise SystemExit("pixel-shader-ids: artifact is stale (%d rows on disk, %d measured)" % (len(have), len(wanted)))
        print("ok   pixel-shader-ids: %d rows, %d ids resolved" % (len(out), len({r['id'] for r in out})))
        return

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(out)
    print("wrote %s: %d rows, %d ids resolved, %d distinct programs"
          % (args.out, len(out), len({r['id'] for r in out}), len({r['program'] for r in out})))


if __name__ == "__main__":
    sys.exit(main())
