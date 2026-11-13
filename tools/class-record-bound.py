#!/usr/bin/env python3
"""Find the class record's array head by the writer's own rule, and bound the defaults block start with it.

U18's tenth entry left one test the tiling census could not give -- the array's own head checked against the located
block start, a bound that a tiled value cannot supply. The first attempt at it read the array at `prefix_end + 42`
(22 `UState::Serialize` bytes plus five dwords) and found 297 heads that passed both table tests with the block start
exactly twenty bytes past their end. That twenty is the measurement's own refutation: this page's gap census already
says the array does **not** start at the field table's position -- it starts 19 to 21 bytes later, after sixteen zero
bytes and a three to five byte compact front -- so the "count" at `+42` was the front's first value and the twenty-byte
margin was that same front read back as an array. A position that has to be assumed is not a measurement.

So this tool finds the head instead of assuming it, by the writer's own shape:

    prefix, 22 + 20 bytes, [3-5 compact values], [16 zero bytes], count, count x (reference, dword, dword), names...

`--position` reports the join that settles the offset from the bytes: on every class the committed gap census covers,
the compact value at `prefix_end + 42 + gap_len` must be that census's `arr_count`. `--sweep` then credits a head only
where all three of the archive's contracts hold -- every element an object reference inside the package's tables,
every index of both name arrays inside the name table -- and reports the block start against the head's end, which is
the earliest byte the block can begin at because the body writes nothing else before the defaults branch.

    python3 tools/class-record-bound.py --dump $SG1_DUMP --position
    python3 tools/class-record-bound.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-bound.py --report
    python3 tools/class-record-bound.py --check          # synthetic bodies, no dump, writes nothing

Reads the packages, the committed prefix table and the committed gap census; no boot, no Ghidra round.
"""
import argparse
import collections
import csv
import importlib.util
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402


def _load(mod):
    spec = importlib.util.spec_from_file_location(mod, os.path.join(HERE, mod + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


crf = _load("class-record-fields")

ROOT = os.path.dirname(HERE)
STATE_BYTES = crf.STATE_BYTES                  # UState::Serialize, 22 bytes at +0x80
CLASS_DWORDS = crf.CLASS_DWORDS                # five dwords at +0x498..+0x4ac
DOC_OFFSET = STATE_BYTES + 4 * CLASS_DWORDS    # the field table's position for +0x4d0 (not where the stream puts it)
ZERO_RUN = 16                                  # the four zero dwords immediately in front of the count
WINDOW = 26                                    # how far past the field table's position the count can sit
MAX_COUNT = 4096
MAX_NAMES = 4096
MAX_REF = 5
ARTIFACT = "class_record_bound.csv"
GAP_ARTIFACT = "class_record_gap_front.csv"
COLUMNS = ["package", "class", "payload", "prefix_end", "block_start", "middle",
           "heads", "head_off", "count", "end", "names_a", "names_b", "margin", "inside",
           "at42_value", "at42_count", "at42_inside", "gap_len", "gap_count", "gap_join"]


def read_count(buf, p):
    if p < 0 or p >= len(buf):
        return None
    try:
        v, e = crf.read_index(buf, p)
    except IndexError:
        return None
    if v < 0 or v > MAX_COUNT:
        return None
    return v, e


def walk_elements(buf, p, nimp, nex):
    """The record array as `FUN_0003d900` streams it; a reference outside both tables refuses it."""
    got = read_count(buf, p)
    if not got:
        return None
    count, o = got
    if count < 1:
        return None
    for _ in range(count):
        if o >= len(buf):
            return None
        try:
            v, e = crf.read_index(buf, o)
        except IndexError:
            return None
        if e - o > MAX_REF or e + 8 > len(buf):
            return None
        if not (v == 0 or (v > 0 and v <= nex) or (v < 0 and -v <= nimp)):
            return None
        o = e + 8
    return {"count": count, "end": o}


def walk_names(buf, p, nnames):
    """One `TArray<FName>` as `FUN_0003d160` streams it: a compact count then two compact indices per element."""
    got = read_count(buf, p)
    if not got:
        return None
    n, o = got
    if n > MAX_NAMES:
        return None
    for _ in range(n):
        for _ in range(2):
            if o >= len(buf):
                return None
            try:
                v, e = crf.read_index(buf, o)
            except IndexError:
                return None
            if not (0 <= v < nnames):
                return None
            o = e
    return {"count": n, "end": o}


def head_at(data, p, nimp, nex, nnames):
    """A whole credited head at `p`: the array and both name arrays, or None."""
    arr = walk_elements(data, p, nimp, nex)
    if not arr:
        return None
    a = walk_names(data, arr["end"], nnames)
    if not a:
        return None
    b = walk_names(data, a["end"], nnames)
    if not b:
        return None
    return {"count": arr["count"], "end": b["end"], "names_a": a["count"], "names_b": b["count"]}


def heads(data, prefix_end, block_start, nimp, nex, nnames):
    """Every credited head whose sixteen preceding bytes are zero, in the window past the field table's position."""
    out = []
    lo = prefix_end + DOC_OFFSET
    for p in range(lo, min(lo + WINDOW, len(data))):
        if p - ZERO_RUN < 0 or data[p - ZERO_RUN:p] != bytes(ZERO_RUN):
            continue
        h = head_at(data, p, nimp, nex, nnames)
        if not h or h["end"] > block_start:
            continue
        h["off"] = p - prefix_end
        h["margin"] = block_start - h["end"]
        out.append(h)
    return out


def measure(data, prefix_end, block_start, nimp, nex, nnames):
    row = {c: "" for c in COLUMNS}
    row.update({"payload": len(data), "prefix_end": prefix_end, "block_start": block_start,
                "middle": block_start - prefix_end, "heads": 0})
    if block_start <= prefix_end:
        return row
    got = heads(data, prefix_end, block_start, nimp, nex, nnames)
    row["heads"] = len(got)
    if got:
        best = got[-1]                      # the head nearest the block: the one the writer's tail leaves in front
        row.update({"head_off": best["off"], "count": best["count"], "end": best["end"],
                    "names_a": best["names_a"], "names_b": best["names_b"],
                    "margin": best["margin"], "inside": 0})
    # the control: the same test at the field table's own position, which is where the front starts
    p = prefix_end + DOC_OFFSET
    if p < len(data):
        try:
            row["at42_value"] = crf.read_index(data, p)[0]
        except IndexError:
            pass
        ctl = head_at(data, p, nimp, nex, nnames)
        if ctl:
            row["at42_count"] = ctl["count"]
            row["at42_inside"] = int(ctl["end"] > block_start)
    return row


def gap_census():
    out = {}
    path = os.path.join(ROOT, "inventory", GAP_ARTIFACT)
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["gap_len"] and r["arr_count"]:
                out[(r["package"], r["class"])] = (int(r["gap_len"]), int(r["arr_count"]))
    return out


def sweep(args, join=False):
    prefixes = crf.read_prefixes()
    gaps = gap_census()
    rows = []
    for fn in sorted(os.listdir(os.path.join(args.dump, "System"))):
        if not fn.endswith(".u"):
            continue
        pkg = Package(os.path.join(args.dump, "System", fn))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        name = fn[:-2]
        nimp, nex, nnames = len(pkg.imports), len(pkg.exports), len(pkg.names)
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            key = (name, pkg.name(exp["name"]))
            if key not in prefixes:
                continue
            start, block = prefixes[key]
            if block <= 0:
                continue
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            row = measure(data, start, block, nimp, nex, nnames)
            row.update({"package": name, "class": key[1]})
            if join and key in gaps:
                gap_len, arr_count = gaps[key]
                row["gap_len"] = gap_len
                row["gap_count"] = arr_count
                p = start + DOC_OFFSET + gap_len
                try:
                    row["gap_join"] = int(crf.read_index(data, p)[0] == arr_count)
                except IndexError:
                    row["gap_join"] = 0
            rows.append(row)
    if not join and args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
        print("wrote %s: %d rows" % (path, len(rows)))
    report(rows)
    return 0


def _ints(rows, key):
    return [int(r[key]) for r in rows if r[key] not in ("", None)]


def _pct(n, d):
    return 100.0 * n / d if d else 0.0


def report(rows=None):
    if rows is None:
        path = os.path.join(ROOT, "inventory", ARTIFACT)
        if not os.path.exists(path):
            print("no %s -- run --sweep first" % ARTIFACT)
            return 1
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    rows = [r for r in rows if int(r.get("block_start") or 0) > 0]
    n = len(rows)
    print("classes with a located block start: %d" % n)
    with_head = [r for r in rows if int(r.get("heads") or 0) >= 1]
    print("a credited head after the sixteen zero bytes: %d (%.1f%%)" % (len(with_head), _pct(len(with_head), n)))
    print("  heads per class: %s" % collections.Counter(int(r["heads"]) for r in with_head).most_common(5))
    print("  the head's offset past the prefix: %s"
          % collections.Counter(_ints(with_head, "head_off")).most_common(8))
    print("  the count on those rows: median %s, most common %s"
          % (statistics.median(_ints(with_head, "count")) if with_head else "-",
             collections.Counter(_ints(with_head, "count")).most_common(5)))
    print("  name arrays: first %s, second %s"
          % (collections.Counter(_ints(with_head, "names_a")).most_common(4),
             collections.Counter(_ints(with_head, "names_b")).most_common(4)))
    if with_head:
        m = _ints(with_head, "margin")
        print("  the block start past the head's end: median %d, most common %s"
              % (statistics.median(m), collections.Counter(m).most_common(8)))
        print("  the block start inside that end: %d" % sum(int(r["inside"]) for r in with_head))
    print("the field table's own position (+%d): the value bytes there are a front value on %d, and a head that "
          "passes both tests and fits before the block on %d"
          % (DOC_OFFSET, sum(1 for r in rows if r["at42_value"] not in ("", None)),
             sum(1 for r in rows if r["at42_count"] not in ("", None))))
    joined = [r for r in rows if r["gap_join"] not in ("", None)]
    if joined:
        print("the join with the committed gap census: the compact value at prefix + %d + gap_len equals its "
              "arr_count on %d of %d" % (DOC_OFFSET, sum(int(r["gap_join"]) for r in joined), len(joined)))
    print("located rows that leave less than %d bytes between the prefix and the block: %d"
          % (DOC_OFFSET, sum(1 for r in rows if int(r["middle"]) < DOC_OFFSET)))
    return 0


def _enc(v):
    sign = 0x80 if v < 0 else 0
    v = abs(v)
    if v < 0x40:
        return bytes([sign | v])
    if v < 0x2000:
        return bytes([sign | 0x40 | (v & 0x3F), v >> 6])
    return bytes([sign | 0x40 | (v & 0x3F), 0x80 | ((v >> 6) & 0x7F), v >> 13])


def _body(count, refs, names_a, names_b, front=(2, 0, 0, 4), gap=0):
    """prefix, the 42-byte middle, the front, the sixteen zero bytes, the array, two name arrays, the block."""
    elems = b"".join(_enc(ref) + b"\x11\x22\x33\x44" + b"\x55\x66\x77\x88" for ref in refs)
    body = b"".join(_enc(v) for v in front) + bytes(ZERO_RUN)
    body += _enc(count) + elems
    body += _enc(len(names_a)) + b"".join(_enc(n) + _enc(0) for n in names_a)
    body += _enc(len(names_b)) + b"".join(_enc(n) + _enc(0) for n in names_b)
    prelude = bytes(range(1, STATE_BYTES + 1)) + b"\x01\x00\x00\x00" * CLASS_DWORDS
    head = bytes(3) + prelude
    block = len(head) + len(body) + gap
    return head + body + bytes(max(0, gap)), 3, block


def selftest():
    cases = 0
    # 1. a head behind the zero run is found, credited, and bounds the block exactly
    data, start, block = _body(3, (0, 2, -1), (1, 2), (3,), gap=0)
    row = measure(data, start, block, 4, 8, 9)
    assert int(row["heads"]) == 1 and int(row["count"]) == 3, row
    assert int(row["head_off"]) == DOC_OFFSET + 4 + ZERO_RUN, row
    assert int(row["margin"]) == 0 and int(row["names_a"]) == 2 and int(row["names_b"]) == 1, row
    cases += 1
    # 2. no sixteen zero bytes in front, no head: the position is the writer's rule and not a scan
    data, start, block = _body(3, (0, 1, 2), (1,), (2,), front=(2, 0, 0, 4), gap=0)
    holed = bytearray(data)
    holed[start + DOC_OFFSET + 8] = 0x07
    row = measure(bytes(holed), start, block, 4, 8, 9)
    assert int(row["heads"]) == 0, row
    cases += 1
    # 3. a reference outside both tables refuses the head
    data, start, block = _body(2, (0, 99), (1,), (2,), gap=0)
    row = measure(data, start, block, 4, 8, 9)
    assert int(row["heads"]) == 0, row
    cases += 1
    # 4. a name index past the name table refuses it, and a head that runs past the block is not a bound
    data, start, block = _body(2, (0, 1), (1, 99), (2,), gap=0)
    assert int(measure(data, start, block, 4, 8, 9)["heads"]) == 0
    data, start, block = _body(4, (0, 1, 2, 3), (1,), (2,), gap=0)
    assert int(measure(data, start, block - 20, 4, 8, 9)["heads"]) == 0
    cases += 1
    # 5. the field table's position is a front value, not a count: reported separately from the head
    data, start, block = _body(2, (0, 1), (1,), (2,), front=(55, 0, 0, 4), gap=0)
    row = measure(data, start, block, 4, 8, 9)
    assert int(row["at42_value"]) == 55 and row["at42_count"] in ("", None), row
    assert int(row["heads"]) == 1 and int(row["count"]) == 2, row
    cases += 1
    print("class-record-bound: selftest passed (%d cases)" % cases)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default=os.environ.get("SG1_DUMP", ""))
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--position", action="store_true", help="join the head offset with the committed gap census")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return selftest()
    if args.report:
        return report()
    if args.sweep or args.position:
        dump = args.dump
        game = "Stargate_SG-1_-_The_Alliance-2005-12-15"
        if dump and os.path.isdir(os.path.join(dump, game)):
            dump = os.path.join(dump, game)
        if not dump or not os.path.isdir(os.path.join(dump, "System")):
            print("no System/ under %r -- set SG1_DUMP" % dump)
            return 2
        args.dump = dump
        return sweep(args, join=True)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
