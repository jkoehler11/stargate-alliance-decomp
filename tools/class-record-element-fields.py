#!/usr/bin/env python3
"""Is a record-array element one reference plus two raw dwords, or three serialized fields?

`UClass::Serialize`'s array writer `FUN_0003d900` (`0x0003d900`, 306 bytes) was read for the first time. Its
element loop hands **three** pointers to the archive's `+0x18` slot, not one:

    0003d99a  MOV EAX,dword ptr [EDI]        ; TArray::Data
    0003d99c  LEA EDX,[EBP + EBP*0x2]        ; index * 3
    0003d9a0  LEA EAX,[EAX + EDX*0x4]        ; Data + index * 12
    0003d9a3  LEA ECX,[EAX + 0x8] / PUSH ECX ; element + 8
    0003d9a7  LEA EDX,[EAX + 0x4] / PUSH EDX ; element + 4
    0003d9ad  PUSH EAX                       ; element + 0
    0003d9ae  MOV ECX,ESI
    0003d9b0  CALL dword ptr [EDX + 0x18]    ; the archive's field slot, three fields at once

and the count is the same call the rest of the image uses (`FUN_000637e0`, 323 sites) with the array's `Num`
word (`TArray+0x4`) as its target, on both the save and the load path:

    0003d90e  MOV EAX,[EDI+0x8] / LEA EAX,[EAX+EAX*0x2] / SHL EAX,0x2   ; Max * 12
    0003d91e  LEA ECX,[EAX+EAX*0x2] / SHL ECX,0x2                        ; Num * 12
    0003d927  CALL [EDX+0x14]                                            ; the prealloc pair
    0003d941  CALL 0x000637e0                                            ; then the count, into Num

So the *memory* element is three dwords and the writer serializes all three, while the **measured** disk shape
(the shipped walk) is `[compact reference][dword][dword]` -- 9 bytes at width 1, and 199 bytes for
`AmbientCreatures.HerdAnimal`'s 22 elements exactly. This tool asks the corpus which one the bytes support:
it walks the array at the writer's own position (`prefix_end + 42`) with **three compact fields per element**
and compares the landing against the shipped one-reference walk (`inventory/class_record_array.csv`,
`doc_end`/`doc_residue`).

    python3 tools/class-record-element-fields.py --dump DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15 \
        --sweep -o inventory
    python3 tools/class-record-element-fields.py --report
    python3 tools/class-record-element-fields.py --check      # synthetic, no dump, writes nothing

A three-field walk that landed on the block start would make the element `3 x compact`; it does not (0 exact),
and it lands *short* of the one-reference walk by the same 17 bytes on four classes in five rather than by
6 bytes per element, so the second and third dwords are not compact references and the 9-byte element stands.
Reads the packages and one committed CSV; no boot, no value decoded.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from ue2pkg import Package  # noqa: E402

MAX_N = 4096            # a count beyond this is not a class tail
MAX_WIDTH = 5           # the writer's own bound for a compact value
HEAD = 42               # UState's 22 bytes and five dwords: where +0x4d0's array begins
FIELDS = 3              # the writer hands three pointers per element
TAILS = 2               # the name arrays both version gates write, i.e. the shipped tool's k=2 rows
ARTIFACT = "class_record_element_fields.csv"
COLUMNS = ["package", "class", "count", "end3", "residue3", "doc_end", "doc_residue", "shrink",
           "element_bytes", "wide_fields", "completes"]


def load(name, filename):
    path = os.path.join(HERE, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


crf = load("crf", "class-record-fields.py")


def walk3(buf, p, tails=TAILS):
    """The array at `p` with three compact fields per element, then `tails` name arrays.

    Returns (count, end, element_bytes, wide) or None when the grammar leaves the buffer.
    """
    try:
        n, o = crf.read_index(buf, p)
    except IndexError:
        return None
    if n < 1 or n > MAX_N:
        return None
    wide = 0
    body = 0
    for _ in range(n):
        for _ in range(FIELDS):
            try:
                _v, e = crf.read_index(buf, o)
            except IndexError:
                return None
            if e - o > MAX_WIDTH:
                return None
            if e - o > 1:
                wide += 1
            body += e - o
            o = e
    for _ in range(tails):
        try:
            c, o = crf.read_index(buf, o)
        except IndexError:
            return None
        if c < 0 or c > MAX_N:
            return None
        for _ in range(c):
            for _ in range(2):
                try:
                    _v, e = crf.read_index(buf, o)
                except IndexError:
                    return None
                if e - o > MAX_WIDTH:
                    return None
                o = e
    return n, o, body, wide


def measure(data, prefix_end, block_start, doc_end, doc_residue):
    row = {"count": "", "end3": "", "residue3": "", "doc_end": doc_end, "doc_residue": doc_residue,
           "shrink": "", "element_bytes": "", "wide_fields": "", "completes": 0}
    if block_start <= prefix_end:
        return row
    got = walk3(data, prefix_end + HEAD)
    if got is None:
        return row
    n, end, body, wide = got
    row.update({"count": n, "end3": end, "residue3": block_start - end,
                "shrink": int(doc_end) - end if str(doc_end).lstrip("-").isdigit() else "",
                "element_bytes": round(body / float(n), 2), "wide_fields": wide, "completes": 1})
    return row


def sweep(dump, out):
    rows = []
    table = {}
    path = os.path.join(ROOT, "inventory", "class_record_array.csv")
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["doc_k"] == str(TAILS):
                table[(r["package"], r["class"])] = r
    system = os.path.join(dump, "System")
    for fn in sorted(os.listdir(system)):
        if not fn.endswith(".u"):
            continue
        name = fn[:-2]
        pkg = Package(os.path.join(system, fn))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            cls = pkg.name(exp["name"])
            ref = table.get((name, cls))
            if not ref:
                continue
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            row = measure(data, int(ref["prefix_end"]), int(ref["block_start"]),
                          ref["doc_end"], ref["doc_residue"])
            row.update({"package": name, "class": cls})
            rows.append(row)
    if out:
        with open(os.path.join(out, ARTIFACT), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in COLUMNS})
    return rows


def stats(rows):
    done = [r for r in rows if r["completes"]]
    exact = sum(1 for r in done if r["residue3"] == 0)
    res = collections.Counter(r["residue3"] for r in done)
    shrink = collections.Counter(r["shrink"] for r in done if r["shrink"] != "")
    short = sum(v for k, v in shrink.items() if k > 0)
    longer = sum(v for k, v in shrink.items() if k < 0)
    return {"probed": len(rows), "done": len(done), "exact": exact, "res": res,
            "shrink": shrink, "short": short, "longer": longer}


def report(rows):
    s = stats(rows)
    print("classes the shipped walk read at k=2: %d" % s["probed"])
    print("three compact fields per element walk clean: %d" % s["done"])
    print("  landing exactly on the block start: %d" % s["exact"])
    print("  where it stops, relative to the block start: most common %s" % (s["res"].most_common(6),))
    print("  it stops SHORT of the one-reference walk on %d, past it on %d" % (s["short"], s["longer"]))
    print("  old_end - new_end: %s" % (s["shrink"].most_common(8),))
    if s["done"]:
        eb = [r["element_bytes"] for r in rows if r["completes"]]
        wide = sum(1 for r in rows if r["completes"] and r["wide_fields"])
        print("  bytes per element: median %.2f, and %d classes have a field wider than one byte"
              % (sorted(eb)[len(eb) // 2], wide))


def check():
    """Synthetic: a three-field walk lands where the fields put it, and a wide field moves the end."""
    table = {}

    def enc(v):
        if v in table:
            return table[v]
        raise AssertionError("no compact encoding for %d" % v)

    for a in range(256):
        for tail in (b"",):
            buf = bytes([a]) + tail
            try:
                v, e = crf.read_index(buf, 0)
            except IndexError:
                continue
            if e == len(buf) and v not in table:
                table[v] = buf
    for a in range(256):
        for b in range(256):
            buf = bytes([a, b])
            try:
                v, e = crf.read_index(buf, 0)
            except IndexError:
                continue
            if e == 2 and v not in table:
                table[v] = buf

    def build(count, second):
        out = bytearray(enc(count))
        for i in range(count):
            for j in range(FIELDS):
                out += enc(second if j == 1 else 1)
        out += enc(1) + enc(1) + enc(2)      # two one-element name arrays
        out += enc(1) + enc(1) + enc(2)
        return bytes(out)

    narrow = build(2, 1)
    got = walk3(narrow, 0, TAILS)
    assert got and got[0] == 2, got
    assert got[2] == 6 * len(enc(1)), got        # six equal-width fields
    assert got[1] == len(narrow), (got, len(narrow))
    wide = build(2, 0x50)
    got3 = walk3(wide, 0, TAILS)
    assert got3 and got3[1] == len(wide), got3
    assert got3[2] > got[2], got3                # the wide second field costs more
    assert walk3(wide, 1, TAILS) is None or walk3(wide, 1, TAILS)[1] != len(wide)
    print("check: ok (three-field walk, wide field, and a head one byte late)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", help="the game directory (the one holding System/)")
    ap.add_argument("--sweep", action="store_true", help="walk every class the shipped tool read at k=2")
    ap.add_argument("-o", "--out", help="write the artifact here")
    ap.add_argument("--report", action="store_true", help="summarise the committed artifact")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        check()
        return
    if args.report:
        path = os.path.join(ROOT, "inventory", ARTIFACT)
        with open(path, newline="") as fh:
            report(list(csv.DictReader(fh)))
        return
    if not args.dump:
        ap.error("--dump is required unless --report or --check is given")
    rows = sweep(args.dump, args.out)
    report(rows)


if __name__ == "__main__":
    main()
