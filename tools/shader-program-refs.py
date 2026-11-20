#!/usr/bin/env python3
"""Which shipped shader programs are used, and through what? (PLAN 4.9 / Phase 6)

`docs/NATIVE_NOTES.md` established that the image carries shader assembly source. Presence is not use, so
this asks the image's bytes two questions per program:

* **is it referenced** — a `push imm32` or `mov reg, imm32` whose immediate is the program's address;
* **through what** — the containing function (a tiny per-program wrapper), and the function *that* calls,
  which is the dispatcher.

Both are byte scans rather than disassembly, so counts are lower bounds and the page says so.

    python3 tools/shader-program-refs.py --xbe ~/sg1/work/dump-local/default.xbe \\
        -o inventory/shader_programs.csv
"""

import argparse
import collections
import csv
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from xbe import Xbe  # noqa: E402

COLUMNS = ["address", "version", "bytes", "first_line", "ref_kind", "wrapper", "dispatcher",
           "dispatcher_name"]
VERSION = re.compile(rb"(?:^|\x00)(x?)(vs\.1\.1|ps\.1\.1|ps\.1\.4)\r?\n")
FUNCTIONS = os.path.join("symbols", "xbox_functions.csv")


def programs(xbe):
    """[(va, version, text)] — one entry per C string that begins with a shader version token."""
    out = []
    for m in VERSION.finditer(xbe.data):
        start = m.start(1)  # includes the optional leading "x"
        end = xbe.data.find(b"\x00", start)
        if end < 0:
            continue
        blob = xbe.data[start:end]
        va = xbe.offset_to_va(start)
        if va is None:
            continue
        out.append((va, (m.group(1) + m.group(2)).decode(), blob.decode("latin1", "replace")))
    return out


def references(xbe, targets):
    """{program_va: (kind, site_va)} for the first reference found."""
    found = {}
    for s in xbe.sections:
        if not s.executable or not s.fsize:
            continue
        blob = xbe.data[s.foff:s.foff + s.fsize]
        for i in range(len(blob) - 5):
            b = blob[i]
            if b == 0x68:
                kind = "push"
            elif 0xB8 <= b <= 0xBF:
                kind = "mov"
            else:
                continue
            imm = struct.unpack_from("<I", blob, i + 1)[0]
            if imm in targets and imm not in found:
                found[imm] = (kind, s.va + i)
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out", default=os.path.join("inventory", "shader_programs.csv"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if args.check:
        return check(args)
    if not args.xbe:
        raise SystemExit("--xbe required to generate")

    xbe = Xbe(args.xbe)
    progs = programs(xbe)
    refs = references(xbe, {va for va, _v, _t in progs})

    rows = [(int(r["address"], 16), int(r["size"]), r["name"])
            for r in csv.DictReader(open(FUNCTIONS, encoding="utf-8"))]
    rows.sort()

    def containing(va, limit=512):
        best = None
        for addr, size, name in rows:
            if addr <= va < addr + size and size <= limit and (best is None or size < best[1]):
                best = (addr, size, name)
        return best

    def call_target(func_va, func_size):
        off = xbe.va_to_offset(func_va)
        body = xbe.data[off:off + func_size]
        for k in range(len(body) - 5):
            if body[k] == 0xE8:
                disp = struct.unpack_from("<i", body, k + 1)[0]
                tgt = func_va + k + 5 + disp
                if 0x10000 <= tgt < 0x08000000:
                    return tgt
        return None

    names = {addr: name for addr, _s, name in rows}
    out = []
    for va, version, text in progs:
        kind, site = refs.get(va, ("", 0))
        wrapper = containing(site) if site else None
        # Only a push wrapper is small enough for "the function's first E8" to be its dispatch call;
        # the mov-referenced programs sit inside larger functions, where that scan finds noise.
        dispatcher = call_target(wrapper[0], wrapper[1]) if (wrapper and kind == "push") else None
        out.append({
            "address": "0x%08x" % va, "version": version, "bytes": len(text),
            "first_line": (text.splitlines()[1].strip()[:40] if text.count("\n") else ""),
            "ref_kind": kind,
            "wrapper": "0x%08x" % wrapper[0] if wrapper else "",
            "dispatcher": "0x%08x" % dispatcher if dispatcher else "",
            "dispatcher_name": names.get(dispatcher, "") if dispatcher else "",
        })
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(out)

    print("%d programs" % len(out),
          dict(collections.Counter(r["version"] for r in out)), sep=": ")
    print("  referenced: %d (push %d, mov %d)"
          % (sum(1 for r in out if r["ref_kind"]), sum(1 for r in out if r["ref_kind"] == "push"),
             sum(1 for r in out if r["ref_kind"] == "mov")))
    for d, n in collections.Counter((r["dispatcher"], r["dispatcher_name"], r["version"])
                                    for r in out if r["dispatcher"]).most_common():
        print("  %s %-22s %-7s %d program(s)" % (d[0], d[1] or "?", d[2], n))
    return 0


def check(args):
    if not os.path.isfile(args.out):
        raise SystemExit("%s missing -- run the generator" % args.out)
    with open(args.out, newline="") as fh:
        rd = csv.DictReader(fh)
        if rd.fieldnames != COLUMNS:
            raise SystemExit("%s columns are %r" % (args.out, rd.fieldnames))
        rows = list(rd)
    problems = []
    if not rows:
        problems.append("no rows")
    if len({r["address"] for r in rows}) != len(rows):
        problems.append("duplicate addresses")
    for r in rows:
        if int(r["bytes"]) < 24:
            problems.append("%s is shorter than a program" % r["address"])
        # A reference inside a function larger than the wrapper limit (512 B) has no "wrapper" row, and one
        # pushed program's wrapper has no dispatch call as its first `E8`. Both are counted, not failed.
        # What must never happen is a dispatcher without a wrapper.
        if r["dispatcher"] and not r["wrapper"]:
            problems.append("%s has a dispatcher but no wrapper" % r["address"])
    if problems:
        print("shader-program-refs: %d problem(s)" % len(problems))
        for p in problems[:10]:
            print("  " + p)
        return 1
    print("ok   shader-program-refs: %d programs, %d referenced, %d dispatched"
          % (len(rows), sum(1 for r in rows if r["ref_kind"]),
             sum(1 for r in rows if r["dispatcher"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())