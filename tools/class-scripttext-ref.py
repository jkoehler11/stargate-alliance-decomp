#!/usr/bin/env python3
"""Resolve the class record's third prefix field: it is an object reference, and its target is a `ScriptText` export.

`UStruct::Serialize` (`0x000392b0`) writes, in this order, the base `UField::Serialize`, an object reference
`Super`, an object reference `Children`, the `FriendlyName` through the archive's `+0x1c` slot (with the assert
`FriendlyName!=NAME_None` at `UnClass.cpp:0x2e2`), one dword behind the version gate `0x77`, two more dwords and
then `ScriptText`. The record's prefix is 3-6 bytes and `tools/class-prefix.py` reads it as a compact reference at
offset 0 that equals the export's `super` on 2,319 of 2,319, a single `00` (a null object reference) and one more
compact field -- which the page calls `ScriptText` because that is the next field the body writes.

That last field is the one this tool tests, and the test is not a width argument: the archive's `+0x18` slot takes
an **object reference**, so a positive value is an index into the package's export table and a negative one into its
import table. If the field is the class's script then it must land on an export of class `ScriptText`; if it were
`FriendlyName` (the field the body writes third) it would be a name index and land on nothing. Both spellings are
reported, so a wrong reading is visible rather than averaged:

    prefix: [compact Super][compact Children][compact third]

The census answers the question the class-record notes were left with -- whether the 20/27-byte residue in front of a
defaults block can be a `UStruct::Serialize` tail field. The body writes five fields after `Children`; the record
carries one reference to the script export and no `FriendlyName`, no line, no text position and no script bytes, so
the tail has nothing to put there.

    python3 tools/class-scripttext-ref.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-scripttext-ref.py --report
    python3 tools/class-scripttext-ref.py --check          # synthetic bodies, no dump, writes nothing
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402

ROOT = os.path.dirname(HERE)
ARTIFACT = "class_scripttext_refs.csv"
COLUMNS = ["package", "class", "prefix_end", "super", "children", "third", "kind", "target"]
GAME = "Stargate_SG-1_-_The_Alliance-2005-12-15"


def _load(mod):
    spec = importlib.util.spec_from_file_location(mod, os.path.join(HERE, mod + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


crf = _load("class-record-fields")
read_index = crf.read_index


def read_prefix(data):
    """(super, children, third, end) from the record's first three compact fields, or None."""
    try:
        sup, o = read_index(data, 0)
        ch, o = read_index(data, o)
        third, o = read_index(data, o)
    except IndexError:
        return None
    return sup, ch, third, o


def classify(third, nimp, nex, exp_names):
    """What the archive's object-reference method would name for `third`."""
    if third == 0:
        return "none", ""
    if third > 0:
        if third <= nex:
            return "export", exp_names[third - 1]
        return "export-out-of-range", ""
    if -third <= nimp:
        return "import", ""
    return "import-out-of-range", ""


def sweep(args):
    prefixes = crf.read_prefixes()
    rows = []
    for fn in sorted(os.listdir(os.path.join(args.dump, "System"))):
        if not fn.endswith(".u"):
            continue
        pkg = Package(os.path.join(args.dump, "System", fn))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        nimp, nex = len(pkg.imports), len(pkg.exports)
        exp_names = [pkg.name(e["name"]) for e in pkg.exports]
        name = fn[:-2]
        seen = set()
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            key = (name, pkg.name(exp["name"]))
            if key not in prefixes or key in seen:
                continue
            seen.add(key)
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            got = read_prefix(data)
            if not got:
                continue
            sup, ch, third, end = got
            kind, target = classify(third, nimp, nex, exp_names)
            rows.append({"package": name, "class": key[1], "prefix_end": prefixes[key][0],
                         "super": sup, "children": ch, "third": third, "kind": kind, "target": target})
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


def report(rows=None):
    if rows is None:
        path = os.path.join(ROOT, "inventory", ARTIFACT)
        if not os.path.exists(path):
            print("no %s -- run --sweep first" % ARTIFACT)
            return 1
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    kinds = collections.Counter(r["kind"] for r in rows)
    targets = collections.Counter(r["target"] for r in rows if r["kind"] == "export")
    children = collections.Counter(str(r["children"]) for r in rows)
    print("classes: %d" % len(rows))
    for k, n in kinds.most_common():
        print("  %-22s %d" % (k, n))
    print("  third target (top): %s" % ", ".join("%s %d" % (t or "?", n) for t, n in targets.most_common(5)))
    print("  children all zero: %d of %d" % (children.get("0", 0), len(rows)))
    return 0


def _enc(v):
    """The archive's `FCompactIndex` for `v`, the spelling `read_index` decodes."""
    sign = 0x80 if v < 0 else 0
    v = abs(v)
    if v < 0x40:
        return bytes([sign | v])
    if v < 0x2000:
        return bytes([sign | 0x40 | (v & 0x3F), v >> 6])
    return bytes([sign | 0x40 | (v & 0x3F), 0x80 | ((v >> 6) & 0x7F), v >> 13])


def selftest():
    cases = 0
    # 1. a three-field prefix decodes to the values that were encoded, and ends where it should
    body = _enc(110) + _enc(0) + _enc(332) + b"\x00" * 40
    sup, ch, third, end = read_prefix(body)
    assert (sup, ch, third) == (110, 0, 332), (sup, ch, third)
    assert end == len(_enc(110)) + 1 + len(_enc(332)), end
    cases += 1
    # 2. a negative second field is a sign-preserving decode (Super can be an import)
    body = _enc(-42) + _enc(0) + _enc(-5) + b"\x00" * 8
    sup, ch, third, end = read_prefix(body)
    assert (sup, third) == (-42, -5), (sup, third)
    cases += 1
    # 3. classify: the object-reference method, both tables and the null
    names = ["Class", "ScriptText", "Texture"]
    assert classify(2, 4, 3, names) == ("export", "ScriptText")
    assert classify(0, 4, 3, names) == ("none", "")
    assert classify(-4, 4, 3, names) == ("import", "")
    assert classify(-5, 4, 3, names) == ("import-out-of-range", "")
    assert classify(4, 4, 3, names) == ("export-out-of-range", "")
    cases += 1
    # 4. the codec round-trips at each width boundary
    for v in (0, 1, 63, 64, 0x1FFF, 0x2000, -1, -63, -0x2000):
        got, end = read_index(_enc(v), 0)
        assert got == v and end == len(_enc(v)), (v, got, end)
        cases += 1
    print("class-scripttext-ref: selftest passed (%d cases)" % cases)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default=os.environ.get("SG1_DUMP", ""))
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return selftest()
    if args.report:
        return report()
    if args.sweep:
        dump = args.dump
        if dump and os.path.isdir(os.path.join(dump, GAME)):
            dump = os.path.join(dump, GAME)
        if not dump or not os.path.isdir(os.path.join(dump, "System")):
            print("no System/ under %r -- set SG1_DUMP" % dump)
            return 2
        args.dump = dump
        return sweep(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
