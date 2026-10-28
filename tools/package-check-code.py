#!/usr/bin/env python3
"""Where the package check lives in the image (PLAN 6.2).

`docs/PACKAGE_CHECK.md` could describe the file but not say whether the build ever reads it. This answers that from the
image, the same way the LIPSinc module was located: find every reference to the check file's own strings, attribute it to
the function that contains it, then walk one level of callers so the answer is "reached from here" and not "exists".

    python3 tools/package-check-code.py --xbe DUMP/.../default.xbe -o inventory   # needs the image
    python3 tools/package-check-code.py --check                                  # dump-free, in `make ci`

The artifact is `inventory/package_check_code.csv`. It does not decide policy: it names the functions a rebuild would
have to patch or satisfy, which is what `docs/PACKAGE_CHECK.md` needs to stop saying "not measured".
"""
import argparse
import bisect
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The strings the check code carries. `Packages.md5` is the file it opens; `PackageCheckInfo` is the class the records
# deserialise into, whose assert path is still in the image.
NEEDLES = ("Packages.md5", "PackageCheckInfo", "CheckInfo")
COLUMNS = ["address", "size", "name", "evidence", "callers", "depth2", "game_reaches"]


def build(xbe_path, symbols):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from xbe import Xbe                                             # noqa: PLC0415 - only with a dump

    xbe = Xbe(xbe_path)
    data = xbe.data
    sections = [(s.va, s.foff, s.fsize) for s in xbe.sections]

    def va_of(foff):
        for va, f, size in sections:
            if f <= foff < f + size:
                return va + (foff - f)
        return None

    def off_of(va):
        for v, f, size in sections:
            if v <= va < v + size:
                return f + (va - v)
        return None

    # every string form (ascii and utf-16) of the check code's own vocabulary
    strings, names = {}, {}
    for needle in NEEDLES:
        for enc in ("ascii", "utf-16-le"):
            blob = needle.encode(enc)
            start = 0
            while True:
                i = data.find(blob, start)
                if i < 0:
                    break
                va = va_of(i)
                if va is not None:
                    strings[va] = needle
                start = i + 1

    funcs = {int(r["address"], 16): (int(r["size"]), r["name"])
             for r in csv.DictReader(open(symbols, encoding="utf-8"))}
    starts = sorted(funcs)

    def owner(va):
        k = bisect.bisect_right(starts, va) - 1
        if k < 0:
            return None
        s = starts[k]
        return s if s <= va < s + funcs[s][0] else None

    # seeds: functions that reference one of the strings
    hits = {}
    for off in range(len(data) - 5):
        if data[off] != 0x68:                                           # push imm32
            continue
        target = struct.unpack_from("<I", data, off + 1)[0]
        if target not in strings:
            continue
        va = va_of(off)
        if va is None:
            continue
        f = owner(va)
        if f is not None:
            hits.setdefault(f, set()).add(target)

    # one level of callers for every seed, from `call rel32` sites in the whole image
    seeds = set(hits)
    callers = {f: set() for f in seeds}
    for off in range(len(data) - 5):
        if data[off] != 0xE8:
            continue
        rel = struct.unpack_from("<i", data, off + 1)[0]
        va = va_of(off)
        if va is None:
            continue
        target = va + 5 + rel
        if target in seeds:
            f = owner(va)
            if f is not None and f != target:
                callers[target].add(f)

    # depth 2, to answer "is it reachable from the game class" rather than "does something call it"
    depth2 = {f: set() for f in seeds}
    for f, first in callers.items():
        for c in first:
            for off in range(len(data) - 5):
                if data[off] != 0xE8:
                    continue
                rel = struct.unpack_from("<i", data, off + 1)[0]
                va = va_of(off)
                if va is None:
                    continue
                if va + 5 + rel == c:
                    g = owner(va)
                    if g is not None and g != c:
                        depth2[f].add(g)

    rows = []
    for f in sorted(seeds):
        size, name = funcs[f]
        first = sorted(callers[f])
        second = sorted(depth2[f] - set(first))
        reached = f in callers and any(funcs[c][1].startswith("Engine_UnGame") for c in first)
        rows.append({
            "address": "0x%08x" % f,
            "size": size,
            "name": name,
            "evidence": " | ".join(sorted({strings[t] for t in hits[f]})),
            "callers": " ".join("0x%08x" % c for c in first[:6]),
            "depth2": " ".join("0x%08x" % g for g in second[:6]),
            "game_reaches": "true" if reached or any(funcs[c][1].startswith("Engine_UnGame")
                                                     for c in first + second) else "false",
        })
    return rows


def check(path):
    rows = list(csv.DictReader(open(path)))
    problems = []
    if not rows:
        problems.append("no rows")
    for r in rows:
        if sorted(r) != sorted(COLUMNS):
            problems.append("columns")
            break
        if not r["evidence"]:
            problems.append("%s: no evidence string" % r["address"])
        if int(r["size"]) <= 0:
            problems.append("%s: no size" % r["address"])
    if problems:
        print("FAIL package-check-code: " + "; ".join(problems[:5]))
        return 1
    print("ok   package-check-code: %d functions reference the check file, %d reachable from the game class"
          % (len(rows), sum(1 for r in rows if r["game_reaches"] == "true")))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("--symbols", default=os.path.join(ROOT, "symbols", "xbox_functions.csv"))
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    path = os.path.join(args.out, "package_check_code.csv")
    if args.check:
        return check(path)
    if not args.xbe:
        ap.error("--xbe is required unless --check")
    rows = build(args.xbe, args.symbols)
    os.makedirs(args.out, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print("%s %5s %-26s %-22s callers %s" % (r["address"], r["size"], r["name"], r["evidence"],
                                                 r["callers"] or "-"))
    print("%d functions -> %s" % (len(rows), path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
