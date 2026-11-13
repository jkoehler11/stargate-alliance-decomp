#!/usr/bin/env python3
"""Read a class record's field list with the widths its own writers use, and measure what is left.

`docs/NATIVE_VM.md` had the *field order* of a cooked class export from `UClass::Serialize` (`0x0003a800`) but
not the *widths*, and every walk built on it assumed a `TArray` count is a 32-bit dword and that the two array
fields hold dwords. Three writers in the image say otherwise, and this tool re-runs the forward walk with what
they actually write (`$WORK/module-export/Core/`, never committed):

* `FUN_000637e0` serialises a count. When both the loading and the saving flag of the archive are clear it writes a
  raw 4-byte integer (`0x00037110`); otherwise it builds an `FCompactIndex` and writes it one byte at a time through
  `0x00037040`. A cooked package is a *saving* archive, so **every array count in the record is compact**.
* `FUN_0003d900` serialises the 12-byte-record array: the count, then per record one **object reference** (archive
  vtable `+0x18`) and two 4-byte dwords (`0x00037110`, `0x000370e0`). A record is 9 bytes when the reference is one
  byte long, which is the stride-9 unit the last run measured in the residue but could not name.
* `FUN_0003d160` serialises the two "dword" arrays: the count, then each element through archive vtable **`+0x1c`
  -- the *name* method** (what `UStruct::Serialize` uses for `FriendlyName`). They are `TArray<FName>`, so an
  element is two compact indices, not four bytes.
* `FUN_000394c0` (`UState::Serialize`) runs before `UClass`'s own fields and writes **8 + 8 + 2 + 4** bytes at
  `+0x80`, `+0x88`, `+0x90` and `+0x94` (`0x000371d0` writes 8, `0x00037080` writes 2, `0x000370e0` writes 4) --
  22 bytes the field table did not have.

So the fields this tool walks, from the end of the measured prefix, are: 22 bytes of state, five dwords, a compact
count plus that many 9-byte records, a config `FName` (two compact indices), then two compact counts with two
compact indices per element. It reports where that walk ends, how many bytes of the middle it explains, and how far
the block start still is -- `exact` is the count that lands on the defaults block, and `control` re-aims the same
walk 8 bytes short, because a rule that fits both has no discriminating power.

    python3 tools/class-record-fields.py --dump $SG1_DUMP --class AmbientCreatures.HerdAnimal
    python3 tools/class-record-fields.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-fields.py --report
    python3 tools/class-record-fields.py --check

Reads the packages and the committed prefix table; no boot, no defaults value decoded.
"""
import argparse
import collections
import csv
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAME = "Stargate_SG-1_-_The_Alliance-2005-12-15"
STATE_BYTES = 22          # +0x80 and +0x88 are 8 bytes, +0x90 is 2, +0x94 is 4
CLASS_DWORDS = 5          # +0x498 .. +0x4ac
MAX_COUNT = 256
ARTIFACT = "class_record_fields.csv"
COLUMNS = ["package", "class", "payload", "prefix_end", "block_start", "middle", "walk_end",
           "explained", "unexplained", "rec_count", "names_a", "names_b", "exact"]


def read_index(buf, off):
    """UE2 `FCompactIndex`: bit 7 is the sign, bit 6 means a byte follows, 6 bits then 7 per byte."""
    b = buf[off]
    sign, cont, val = b & 0x80, b & 0x40, b & 0x3F
    off += 1
    shift = 6
    while cont and shift < 32:
        b = buf[off]
        off += 1
        cont = b & 0x80
        val |= (b & 0x7F) << shift
        shift += 7
    return (-val if sign else val), off


def walk_row(data, start):
    """Same walk, but keeping the intermediate numbers the artifact wants."""
    try:
        o = start + STATE_BYTES + 4 * CLASS_DWORDS
        if o > len(data):
            return None
        count, o = read_index(data, o)
        if count < 0 or count > MAX_COUNT:
            return None
        for _ in range(count):
            _, o = read_index(data, o)
            o += 8
            if o > len(data):
                return None
        _, o = read_index(data, o)
        _, o = read_index(data, o)
        names = []
        for _ in range(2):
            n, o = read_index(data, o)
            if n < 0 or n > MAX_COUNT:
                return None
            for _ in range(n):
                _, o = read_index(data, o)
                _, o = read_index(data, o)
                if o > len(data):
                    return None
            names.append(n)
        return o, count, names[0], names[1]
    except IndexError:
        return None


def read_prefixes():
    out = {}
    path = os.path.join(ROOT, "inventory", "class_prefixes.csv")
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["prefix_end"] and r["block_start"]:
                out[(r["package"], r["class"])] = (int(r["prefix_end"]), int(r["block_start"]))
    return out


def sweep(args):
    prefixes = read_prefixes()
    rows = []
    for fn in sorted(os.listdir(os.path.join(args.dump, "System"))):
        if not fn.endswith(".u"):
            continue
        pkg = Package(os.path.join(args.dump, "System", fn))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        name = fn[:-2]
        for k, exp in enumerate(pkg.exports, start=1):
            if pkg.class_name_of(exp) != "Class":
                continue
            key = (name, pkg.name(exp["name"]))
            if key not in prefixes:
                continue
            start, block = prefixes[key]
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            got = walk_row(data, start)
            row = {"package": name, "class": key[1], "payload": len(data), "prefix_end": start,
                   "block_start": block, "middle": block - start, "walk_end": "", "explained": "",
                   "unexplained": "", "rec_count": "", "names_a": "", "names_b": "", "exact": 0}
            if got:
                end, count, a, b = got
                row.update({"walk_end": end, "explained": end - start, "unexplained": block - end,
                            "rec_count": count, "names_a": a, "names_b": b, "exact": int(end == block)})
            rows.append(row)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
        print("wrote %s: %d rows" % (path, len(rows)))
    report(rows)
    return 0


def report(rows):
    parsed = [r for r in rows if r["walk_end"] != ""]
    print("classes with a located block start: %d" % len(rows))
    print("the measured field list walks off the payload: %d" % (len(rows) - len(parsed)))
    print("it ends exactly on the block start: %d" % sum(r["exact"] for r in rows))
    if not parsed:
        return
    expl = [r["explained"] for r in parsed]
    unex = [r["unexplained"] for r in parsed]
    print("bytes explained: median %d, p90 %d, max %d"
          % (statistics.median(expl), sorted(expl)[int(len(expl) * 0.9)], max(expl)))
    print("bytes left before the block start: median %d, p90 %d, min %d, negative %d"
          % (statistics.median(unex), sorted(unex)[int(len(unex) * 0.9)], min(unex),
             sum(1 for u in unex if u < 0)))
    counts = collections.Counter(r["rec_count"] for r in parsed)
    print("record-array counts: %s" % counts.most_common(6))
    print("name arrays: zero-a %d, zero-b %d"
          % (sum(1 for r in parsed if r["names_a"] == 0), sum(1 for r in parsed if r["names_b"] == 0)))


def one(args):
    prefixes = read_prefixes()
    pkg = Package(os.path.join(args.dump, "System", args.package + ".u"))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    for k, exp in enumerate(pkg.exports, start=1):
        if pkg.class_name_of(exp) != "Class" or pkg.name(exp["name"]) != args.cls:
            continue
        start, block = prefixes[(args.package, args.cls)]
        data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
        got = walk_row(data, start)
        print("%s.%s payload %d prefix_end %d block_start %d middle %d"
              % (args.package, args.cls, len(data), start, block, block - start))
        if not got:
            print("the measured field list does not walk inside this payload")
            return 1
        end, count, a, b = got
        print("walk ends %d: %d bytes explained, %d left before the block start (records %d, names %d/%d)"
              % (end, end - start, block - end, count, a, b))
        return 0
    print("no such class")
    return 2


def self_check():
    """Synthetic record: 22 state bytes, five dwords, one record, one name, two names."""
    def compact(v):
        sign, v = (0x80, -v) if v < 0 else (0, v)
        out = v & 0x3F
        v >>= 6
        cont = 0x40 if v else 0
        return bytes([sign | cont | out]) if not v else bytes([sign | 0x40 | out]) + compact(v << 6)

    body = bytes(STATE_BYTES) + bytes(4 * CLASS_DWORDS)
    body += compact(2) + compact(1) + bytes(8) + compact(3) + bytes(8)   # two 9-byte records
    body += compact(4) + compact(1)                                      # config FName
    body += compact(1) + compact(2) + compact(0)                         # one name
    body += compact(2) + compact(3) + compact(0) + compact(5) + compact(1)
    got = walk_row(body, 0)
    assert got, "synthetic record did not walk"
    assert got[0] == len(body), "walk ended %d, expected %d" % (got[0], len(body))
    assert got[1:] == (2, 1, 2), got
    short = walk_row(body[:-1], 0)
    assert short is None, "a truncated record must not walk"
    print("self-check ok: %d-byte record walks to its own end; a truncated copy is refused" % len(body))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", help="dump root (the directory holding System/, Maps/, ...)")
    ap.add_argument("--class", dest="cls", help="one class, with --package")
    ap.add_argument("--package", default="", help="package of --class")
    ap.add_argument("--sweep", action="store_true", help="walk every located class")
    ap.add_argument("-o", "--out", help="artifact directory")
    ap.add_argument("--report", action="store_true", help="print the committed artifact's tables")
    ap.add_argument("--check", action="store_true", help="self-test, no dump")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.report:
        path = os.path.join(ROOT, "inventory", ARTIFACT)
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        report(rows)
        return 0
    if not args.dump:
        ap.error("--dump is required (or --check)")
    if args.cls:
        if "." in args.cls and not args.package:
            args.package, args.cls = args.cls.split(".", 1)
        return one(args)
    if args.sweep:
        return sweep(args)
    ap.error("pick --sweep, --class or --report")


if __name__ == "__main__":
    sys.exit(main())
