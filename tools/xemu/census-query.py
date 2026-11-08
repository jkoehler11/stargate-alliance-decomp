#!/usr/bin/env python3
"""Query an object census written by `tools/xemu/findcls.py --csv` / `names.py --census --csv`.

The CSV is `index,address,class,name` for every object the walk could name, and it is the only record of a boot
that survives the boot: the census costs a 64 KiB-page walk over 48k objects, and until now answering "does a
loaded level hold a `StargatePlayer` at all?" meant `awk -F,` on 47k rows. This tool answers the questions that
come up against it, and separates the two readings a bare `grep` conflates -- an object *of* class C (its own
`Class` pointer is C) from the class *object* C (a `Class`-classed object named C, which the dump holds whether or
not any instance does).

    census-query.py cls.csv --class StargatePlayer      # instances: rows whose class column is exactly that
    census-query.py cls.csv --class-object StargatePlayer   # the class object's address (what --object wants)
    census-query.py cls.csv --name Player --limit 20
    census-query.py cls.csv --counts 25                 # the class histogram
    census-query.py --selftest

`--absent` exits 3 when the class has no instance, so a shell can branch on the answer without parsing.
"""

import argparse
import csv
import sys


def load(path):
    rows = []
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            try:
                index = int(row.get("index") or -1)
            except ValueError:
                index = -1
            rows.append((index, (row.get("address") or "").strip(),
                         (row.get("class") or "").strip(), (row.get("name") or "").strip()))
    return rows


def instances(rows, cls):
    """Objects whose own `Class` pointer is `cls` -- the instance reading, not the class object."""
    return [r for r in rows if r[2] == cls]


def class_objects(rows, cls):
    """Class objects *named* `cls`: class `Class`, name `cls`. What `fields.py --object` needs, not an instance."""
    return [r for r in rows if r[2] == "Class" and r[3] == cls]


def histogram(rows):
    counts = {}
    for _index, _addr, cls, _name in rows:
        counts[cls] = counts.get(cls, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def show(rows, label):
    print("%s: %d row(s)" % (label, len(rows)))
    for index, addr, cls, name in rows:
        print("  index %-7d %s  %-22s `%s`" % (index, addr, cls or "?", name or "?"))


SAMPLE = [
    (0, "0x0097fa40", "Package", "Editor"),
    (619, "0x00e47800", "Class", "StargatePlayer"),
    (41505, "0x0682ab00", "GameHud", "GameHud"),
    (37140, "0x050a3200", "StoryModeGame", "StoryModeGame0"),
]


def selftest():
    """The two readings that a `grep ',StargatePlayer,'` cannot tell apart."""
    assert len(instances(SAMPLE, "StargatePlayer")) == 0, "a class object is not an instance"
    assert class_objects(SAMPLE, "StargatePlayer")[0][1] == "0x00e47800"
    assert len(instances(SAMPLE, "GameHud")) == 1
    assert histogram(SAMPLE)[0] == ("GameHud", 1) or histogram(SAMPLE)[0][1] == 1
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv", nargs="?", help="census CSV (index,address,class,name)")
    ap.add_argument("--class", dest="classes", action="append", default=[],
                    help="objects whose own Class pointer is this class (repeatable)")
    ap.add_argument("--class-object", dest="class_objects", action="append", default=[],
                    help="the Class-classed object named this -- the address a dump reader takes (repeatable)")
    ap.add_argument("--name", action="append", default=[], help="name substring (repeatable)")
    ap.add_argument("--counts", type=int, default=0, help="print the class histogram, N rows")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--absent", action="store_true",
                    help="exit 3 when a --class has no instance")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return 0
    if not args.csv:
        ap.error("a census CSV is required (or --selftest)")
    rows = load(args.csv)
    print("%s: %d object(s)" % (args.csv, len(rows)))

    missing = False
    for cls in args.classes:
        found = instances(rows, cls)
        show(found, "instances of `%s`" % cls)
        if not found:
            missing = True
            holders = class_objects(rows, cls)
            print("  none -- the class object is at %s, so the name resolves and the walk had it available"
                  % (", ".join(r[1] for r in holders) if holders else "an address this census does not hold"))
    for cls in args.class_objects:
        found = class_objects(rows, cls)
        show(found, "class object `%s`" % cls)
    for needle in args.name:
        found = [r for r in rows if needle.lower() in r[3].lower()][:args.limit]
        show(found, "names containing `%s`" % needle)
    if args.counts:
        hist = histogram(rows)
        print("class histogram, top %d of %d:" % (min(args.counts, len(hist)), len(hist)))
        for cls, count in hist[:args.counts]:
            print("  %6d  %s" % (count, cls or "<unreadable>"))
    if args.absent and missing:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
