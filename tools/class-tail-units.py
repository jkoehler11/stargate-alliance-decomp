#!/usr/bin/env python3
"""Read a class payload from its record's last streamed field to the block: units, closing run, block.

`tools/class-record-room.py` left two questions on its 32 rows: whether the four it *re-located* to a declared tag
(with a 14-25 byte tail) are a tag followed by a second table, and whether the 24 it called empty carry a bare
terminator. Both are questions about the bytes between the record and the end of the payload, and four classes in
the corpus answer them from the block side, because their block is **known**: `Engine.SubActionCameraShake`,
`SubActionFOV`, `SubActionSceneSpeed` and `SubActionTrigger` each declare one property and set it, so the single
declared tag that tiles the payload exactly is the block and not a candidate
(`tools/class-defaults.py`'s `single_declared_block`, all four at start 97).

Read forward, a payload's record ends in a stream of units and a short closing run:

    <name index> 01 00 00 00 <4 bytes>      ... <unit> repeated
    02 01 02 83 03 00 <Desc tag> <None>     the anchors: closing run, then the block

Three measurements follow, none of them assuming a tag grammar:

* **every one of the 32 payloads ends with at least one unit**, and the last unit's flag dword is a fixed landmark:
  on the four anchors the block starts **6** bytes past it. Corpus wide the same distance is 6 to 9 bytes on
  **1,180 of the 2,204** located rows (`--join`), so the closing run is the record's, not the block's.
* **a claimed block start inside a unit is not a block start.** All four re-located rows sit one byte before a
  unit's dword (or inside its four trailing bytes), and the parse that accepted them read that dword as a property
  value -- `bHidden`, `RunMovementAnims`, `FidgetAnims`, `KParams`. A block cannot begin inside the record.
* **the 24 empty ones carry no declared tag that tiles**, which is exactly the test that finds the four anchors. It
  is a negative -- there is no tag to find, not a tag read as empty -- and it is the strongest statement the payload
  supports, so the verdict says `no-declared-tag` rather than `empty`.

    python3 tools/class-tail-units.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-tail-units.py --report
    python3 tools/class-tail-units.py --join         # the closing-run distance, corpus wide
    python3 tools/class-tail-units.py --check        # synthetic payloads, no dump, writes nothing

Reads the packages plus the committed room and bound tables; no boot, no Ghidra round, no property value decoded.
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
UNIT_DWORD = b"\x01\x00\x00\x00"     # a record unit's flag field
UNIT_BODY = 8                        # the flag dword plus the four bytes the writer streams after it
UNIT_NAME = 3                        # a unit's name index is one to three bytes
ARTIFACT = "class_tail_units.csv"
ROOM_ARTIFACT = "class_record_room.csv"
BOUND_ARTIFACT = "class_record_bound.csv"
COLUMNS = ["package", "class", "payload", "located_start", "claimed_start", "claimed_room", "claimed_trailer",
           "units", "last_unit", "after_len", "after_hex", "tiles", "verdict"]

ANCHORS = [("Engine", "SubActionCameraShake"), ("Engine", "SubActionFOV"),
           ("Engine", "SubActionSceneSpeed"), ("Engine", "SubActionTrigger")]


def _load(mod):
    spec = importlib.util.spec_from_file_location(mod, os.path.join(HERE, mod + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def unit_offsets(data):
    """Every offset of a record unit's flag dword, in order."""
    out, i = [], data.find(UNIT_DWORD)
    while i >= 0:
        out.append(i)
        i = data.find(UNIT_DWORD, i + 1)
    return out


def inside_unit(claimed, units):
    """True when `claimed` falls in a unit's name index or its body -- the record, not the block."""
    return any(u - UNIT_NAME <= claimed <= u + UNIT_BODY for u in units)


def declared_single_tag(pkg, mod, data, declared):
    """The one declared tag that tiles the payload (start, name, type), or None."""
    for start in range(len(data)):
        try:
            props, end, reason = mod.read_block(data[start:], pkg, False)
        except Exception:
            continue
        if reason != "None" or len(props) != 1 or start + end != len(data):
            continue
        if props[0]["name"] in declared:
            return start, props[0]["name"], props[0]["type"]
    return None


def classify(claimed, units, tiles):
    """One verdict per payload, strongest evidence first: a located block, then a refuted claim."""
    if tiles:
        return "block"
    if units and inside_unit(claimed, units):
        return "record"
    return "no-declared-tag"


def closure_of(cls, supers, declared_all):
    out, cur, seen = set(), cls, set()
    while cur and cur not in seen:
        seen.add(cur)
        out |= declared_all.get(cur, set())
        cur = supers.get(cur)
    return out


def read_tables():
    supers, declared_all = {}, collections.defaultdict(set)
    for r in csv.DictReader(open(os.path.join(ROOT, "inventory", "classes.csv"), newline="")):
        supers[r["class"]] = r["super"]
    for r in csv.DictReader(open(os.path.join(ROOT, "inventory", "class_properties.csv"), newline="")):
        declared_all[r["class"]].add(r["property"])
    return supers, declared_all


def open_pkg(dump, name):
    pkg = Package(os.path.join(dump, "System", name + ".u"))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    return pkg


def class_export(pkg, cls):
    for e in pkg.exports:
        if pkg.class_name_of(e) == "Class" and pkg.name(e["name"]) == cls:
            return e
    return None


def sweep(args):
    room = list(csv.DictReader(open(os.path.join(ROOT, "inventory", ROOM_ARTIFACT), newline="")))
    prop = _load("prop-stream")
    supers, declared_all = read_tables()
    by_pkg = collections.defaultdict(list)
    for r in room:
        by_pkg[r["package"]].append(r)
    rows = []
    for name, rs in by_pkg.items():
        pkg = open_pkg(args.dump, name)
        for r in rs:
            exp = class_export(pkg, r["class"])
            if exp is None:
                continue
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            units = unit_offsets(data)
            last = units[-1] if units else None
            found = declared_single_tag(pkg, prop, data, closure_of(r["class"], supers, declared_all))
            row = {"package": name, "class": r["class"], "payload": len(data),
                   "located_start": int(r["located_start"]), "claimed_start": int(r["best_start"]),
                   "claimed_room": int(r["best_room"] or 0),
                   "claimed_trailer": int(r["best_trailer"] or 0),
                   "units": " ".join(str(u) for u in units), "last_unit": last if last is not None else "",
                   "after_len": len(data) - (last + UNIT_BODY) if last is not None else "",
                   "after_hex": data[last + UNIT_BODY:].hex() if last is not None else "",
                   "tiles": "%d:%s:%s" % found if found else ""}
            row["verdict"] = classify(row["claimed_start"], units, found)
            rows.append(row)
    rows.sort(key=lambda r: (r["verdict"], r["package"], r["class"]))
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
        print("wrote %s: %d rows" % (path, len(rows)))
    return rows


def join(args):
    """How far past the record's last unit a *located* block start sits, over the whole corpus."""
    bound = list(csv.DictReader(open(os.path.join(ROOT, "inventory", BOUND_ARTIFACT), newline="")))
    by_pkg = collections.defaultdict(list)
    for r in bound:
        if r["block_start"]:
            by_pkg[r["package"]].append(r)
    hist = collections.Counter()
    for name, rs in by_pkg.items():
        pkg = open_pkg(args.dump, name)
        for r in rs:
            exp = class_export(pkg, r["class"])
            if exp is None:
                continue
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            b = int(r["block_start"])
            if b > len(data):
                continue
            units = [u for u in unit_offsets(data) if u + UNIT_BODY <= b]
            hist[(b - (units[-1] + UNIT_BODY)) if units else None] += 1
    total = sum(hist.values())
    print("%d located block starts; distance from the record unit's flag dword to the block start:" % total)
    near = sum(v for k, v in hist.items() if k is not None and k <= 12)
    print("  %d within 12 bytes (%.1f%%), %d with no unit in front, %d further back"
          % (near, 100.0 * near / total, hist.get(None, 0), total - near - hist.get(None, 0)))
    for k in sorted(k for k in hist if k is not None and k <= 12):
        print("  %2d bytes  %5d  %5.1f%%" % (k, hist[k], 100.0 * hist[k] / total))
    return 0


def report(rows=None):
    if rows is None:
        path = os.path.join(ROOT, "inventory", ARTIFACT)
        rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    counts = collections.Counter(r["verdict"] for r in rows)
    print("%d payloads: %s" % (len(rows), ", ".join("%s %d" % (k, v) for k, v in counts.most_common())))
    print()
    print("%-17s %-36s %3s %3s %-11s %4s %4s %4s %-14s %s"
          % ("package", "class", "pay", "uni", "units", "last", "aft", "clam", "verdict", "tiles"))
    for r in rows:
        print("%-17s %-36s %3s %3d %-11s %4s %4s %4s %-14s %s"
              % (r["package"], r["class"], r["payload"], len(r["units"].split()) if r["units"] else 0,
                 r["units"].replace(" ", ",") if r["units"] else "-", r["last_unit"] or "-",
                 r["after_len"], r["claimed_start"], r["verdict"], r["tiles"] or "-"))
    print()
    print("bytes after the record's last unit (32 payloads):")
    for k, v in collections.Counter(int(r["after_len"]) for r in rows if r["after_len"] != "").most_common():
        print("  %2d bytes  %d rows" % (k, v))
    return 0


def selftest():
    """Synthetic payloads: the three verdicts the classifier has to tell apart."""
    unit = b"\x44\x0f" + UNIT_DWORD + b"\xdd\x8e\x44\xa2"
    cases = [
        ("a declared tag tiles", b"\x00" * 30 + unit + b"\x02\x01\x02\x83\x03\x00\x68\x02\x5d\x04\x00", 40,
         "block", 32),
        ("a claim inside the unit body", b"\x00" * 30 + unit + b"\x00" * 6, 39, "record", 32),
        ("a claim a byte before the name", b"\x00" * 30 + unit + b"\x00" * 6, 30, "record", 32),
        ("a claim past every unit", b"\x00" * 30 + unit + b"\x00" * 6, 8, "no-declared-tag", 32),
        ("no unit at all", b"\x00" * 30, 8, "no-declared-tag", None),
    ]
    bad = 0
    for label, data, claimed, want, want_unit in cases:
        units = unit_offsets(data)
        got = classify(claimed, units, {"tile": 1} if want == "block" else None)
        if got != want:
            print("FAIL class-tail-units: %s answered %s, expected %s" % (label, got, want))
            bad += 1
        if (units[0] if units else None) != want_unit:
            print("FAIL class-tail-units: %s found units %s, expected %s" % (label, units, want_unit))
            bad += 1
    if bad:
        return 1
    print("ok   class-tail-units: unit location and the three verdicts on %d synthetic payloads" % len(cases))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default=os.environ.get("SG1_DUMP", ""))
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--join", action="store_true",
                    help="distance from the record's last unit to each located block start, corpus wide")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return selftest()
    if args.sweep:
        if not args.dump:
            print("FAIL class-tail-units: --sweep needs --dump")
            return 1
        return report(sweep(args))
    if args.join:
        if not args.dump:
            print("FAIL class-tail-units: --join needs --dump")
            return 1
        return join(args)
    if args.report:
        return report()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
