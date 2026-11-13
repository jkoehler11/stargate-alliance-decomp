#!/usr/bin/env python3
"""Re-locate the class records whose defaults block start leaves no room for the record's own fields.

`tools/class-record-bound.py` reports **32** located rows that leave fewer than 42 bytes between the prefix and the
block, and that is less than `UClass::Serialize` writes unconditionally (22 `UState::Serialize` bytes and five
dwords). Those starts are wrong whatever the record array's position is. This tool asks the cheap question instead of
the tag walk's: a defaults block is a run of tagged properties, so a *candidate* start is any offset where one parses
and ends at the payload's end, and the record's fields then bound the candidate from below -- a start closer to the
prefix than `prefix + 42` cannot be the block.

Two filters make that a locator rather than a scan. `tools/class-defaults.py`'s `candidates()` already rejects a parse
that leaves more than 64 bytes behind it and counts tags whose type the class's own property record contradicts; this
tool adds the **room filter** (the start must clear the record's fixed fields) and reports what each one leaves. On the
SubAction classes the two filters disagree with the tag walk in a way that settles it: the walk's start carries a
**two**-property parse two bytes past the prefix, and the only candidate that clears the record is a single declared
tag at room 91 with a zero trailer.

    python3 tools/class-record-room.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-room.py --report
    python3 tools/class-record-room.py --check          # synthetic candidates, no dump, writes nothing

Reads the packages plus the committed prefix, class and property tables; no boot, no Ghidra round, no value decoded.
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
RECORD_FIELDS = 42                   # UState::Serialize's 22 bytes plus UClass's five dwords
TRAILER_MAX = 64                     # the bound class-defaults.py's candidates() already applies
ARTIFACT = "class_record_room.csv"
BOUND_ARTIFACT = "class_record_bound.csv"
COLUMNS = ["package", "class", "payload", "prefix_end", "located_start", "located_room", "located_props",
           "candidates", "declared_cands", "clearing", "best_start", "best_room", "best_props", "best_trailer",
           "best_declared", "verdict"]

VERDICT_RELOCATED = "relocated"
VERDICT_EMPTY = "empty-block"
VERDICT_REFUSED = "refused"
VERDICT_NO_TAG = "no-declared-tag"


def _load(mod):
    spec = importlib.util.spec_from_file_location(mod.replace("-", "_"), os.path.join(HERE, mod + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


cd = _load("class-defaults")


def declared_for(cls, by_class, supers):
    """Every property name the class or one of its ancestors declares."""
    out = set()
    seen = set()
    cur = cls
    while cur and cur not in seen:
        seen.add(cur)
        out |= by_class.get(cur, set())
        cur = supers.get(cur, "")
    return out


def pick(cands, declared, prefix_end, located=None, min_room=RECORD_FIELDS, payload=None, terminal_zero=False):
    """Split the parses into the ones that clear the record's fields and the ones that cannot be the block.

    `cands` is `class-defaults.py`'s own shape: `(nprops, start, names, trailer, bad)` per parse. A parse whose tags
    name nothing the class declares is not evidence for a start -- every payload has eight or nine of those -- so the
    count is reported and the chooser only sees the declared ones.

    A payload whose last byte is the `None` terminator, and whose parses name nothing the class declares, has one start
    the record's fields leave and nothing contradicts: the payload's last byte, an **empty** block. That is the weaker
    located verdict -- it says the class sets no default of its own, not that a tag was found -- so it is reported only
    when no declared tag-bearing parse clears the record.
    """
    declared_cands = [(n, s, names, t, b) for (n, s, names, t, b) in cands
                      if any(nm in declared for nm in names)]
    clearing = [c for c in declared_cands if c[1] >= prefix_end + min_room]
    row = {"candidates": len(cands), "declared_cands": len(declared_cands), "clearing": len(clearing)}
    if located is not None:
        match = [c for c in cands if c[1] == located]
        row["located_props"] = match[0][0] if match else ""
        row["located_room"] = located - prefix_end
    if clearing:
        # The start nearest the payload's end wins: a block that begins later leaves less of the payload to the
        # record, and the record's own fields are what the room filter already accepted.
        best = max(clearing, key=lambda c: (len([nm for nm in c[2] if nm in declared]), c[1]))
        row.update({"best_start": best[1], "best_room": best[1] - prefix_end, "best_props": best[0],
                    "best_trailer": best[3], "best_declared": ",".join(nm for nm in best[2] if nm in declared),
                    "verdict": VERDICT_RELOCATED})
    elif declared_cands:
        best = max(declared_cands, key=lambda c: c[1])
        row.update({"best_start": best[1], "best_room": best[1] - prefix_end, "best_props": best[0],
                    "best_trailer": best[3], "best_declared": ",".join(nm for nm in best[2] if nm in declared),
                    "verdict": VERDICT_REFUSED})
    elif terminal_zero and payload:
        row.update({"best_start": payload - 1, "best_room": payload - 1 - prefix_end, "best_props": 0,
                    "best_trailer": 0, "best_declared": "", "verdict": VERDICT_EMPTY})
    else:
        row.update({"best_start": "", "best_room": "", "best_props": "", "best_trailer": "",
                    "best_declared": "", "verdict": VERDICT_NO_TAG})
    return row


def short_rows(path):
    """The impossible rows: a located block start closer to the prefix than the record's fixed fields."""
    out = []
    if not path:
        path = os.path.join(ROOT, "inventory", BOUND_ARTIFACT)
    if not os.path.exists(path):
        print("no %s -- run make class-record-bound first" % path)
        raise SystemExit(1)
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if not r.get("block_start") or int(r["block_start"]) <= 0:
                continue
            room = int(r["block_start"]) - int(r["prefix_end"])
            if room < RECORD_FIELDS:
                out.append({"package": r["package"], "class": r["class"], "payload": int(r["payload"]),
                            "prefix_end": int(r["prefix_end"]), "located_start": int(r["block_start"]),
                            "room": room})
    return out


def sweep(args):
    wanted = {(r["package"], r["class"]): r for r in short_rows(args.rows)}
    supers = cd.super_map()
    by_class = cd.declared_properties()
    mod = cd.load_prop_stream()
    rows = []
    for fn in sorted(os.listdir(os.path.join(args.dump, "System"))):
        if not fn.endswith(".u"):
            continue
        name = fn[:-2]
        keys = [k for k in wanted if k[0] == name]
        if not keys:
            continue
        pkg = Package(os.path.join(args.dump, "System", fn))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            key = (name, pkg.name(exp["name"]))
            if key not in wanted:
                continue
            base = wanted[key]
            cands, data = cd.candidates(pkg, mod, exp, floor=1)
            row = {c: "" for c in COLUMNS}
            row.update({"package": name, "class": key[1], "payload": len(data),
                        "prefix_end": base["prefix_end"], "located_start": base["located_start"]})
            row.update(pick(cands, declared_for(key[1], by_class, supers),
                            base["prefix_end"], located=base["located_start"],
                            payload=len(data), terminal_zero=bool(data) and data[-1] == 0))
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
    report(rows)
    return 0


def _ints(rows, key):
    return [int(r[key]) for r in rows if r[key] not in ("", None)]


def report(rows=None):
    if rows is None:
        path = os.path.join(ROOT, "inventory", ARTIFACT)
        if not os.path.exists(path):
            print("no %s -- run --sweep first" % ARTIFACT)
            return 1
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    n = len(rows)
    print("rows the located start leaves under %d bytes of room: %d" % (RECORD_FIELDS, n))
    print("  verdicts: %s" % collections.Counter(r["verdict"] for r in rows).most_common())
    print("  parses per payload: median %s, most common %s"
          % (_median(_ints(rows, "candidates")), collections.Counter(_ints(rows, "candidates")).most_common(4)))
    print("  of those, tags naming a declared property: %s"
          % collections.Counter(_ints(rows, "declared_cands")).most_common(6))
    reloc = [r for r in rows if r["verdict"] == VERDICT_RELOCATED]
    if reloc:
        print("  re-located (%d): rooms %s, trailers %s, properties %d distinct"
              % (len(reloc), collections.Counter(_ints(reloc, "best_room")).most_common(8),
                 collections.Counter(_ints(reloc, "best_trailer")).most_common(5),
                 len({r["best_declared"] for r in reloc})))
    ref = [r for r in rows if r["verdict"] == VERDICT_REFUSED]
    if ref:
        print("  refused (%d): their best declared parse still sits at room %s"
              % (len(ref), collections.Counter(_ints(ref, "best_room")).most_common(6)))
    empty = [r for r in rows if r["verdict"] == VERDICT_EMPTY]
    if empty:
        print("  empty block at the payload's last byte (%d): rooms %d..%d, parses per payload %s"
              % (len(empty), min(_ints(empty, "best_room")), max(_ints(empty, "best_room")),
                 collections.Counter(_ints(empty, "candidates")).most_common(4)))
    none = [r for r in rows if r["verdict"] == VERDICT_NO_TAG]
    if none:
        print("  no declared tag anywhere (%d): payloads %d..%d, parses per payload %s"
              % (len(none), min(_ints(none, "payload")), max(_ints(none, "payload")),
                 collections.Counter(_ints(none, "candidates")).most_common(4)))
    return 0


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return "-"
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def check():
    """Synthetic candidates: the room filter, not the parse, is what decides, and all three verdicts are reachable."""
    declared = {"Desc", "Tone"}
    cases = 0
    # a two-property parse two bytes past the prefix (what the tag walk chose) against the record's room
    cands = [(2, 8, ["Desc", "Tone"], 0, 0), (1, 97, ["Desc"], 0, 0)]
    row = pick(cands, declared, 6, located=8)
    assert row["verdict"] == VERDICT_RELOCATED, row
    assert row["best_start"] == 97 and row["best_room"] == 91, row
    assert row["clearing"] == 1 and row["located_props"] == 2, row
    cases += 1
    # nothing clears the record: the start is refused, and the best declared parse is reported as the reason
    row = pick([(2, 1, ["Desc", "Tone"], 45, 0), (1, 4, ["Tone"], 45, 0)], declared, 5, located=1)
    assert row["verdict"] == VERDICT_REFUSED, row
    assert row["best_start"] == 4 and row["best_room"] == -1, row
    cases += 1
    # parses exist but no tag names anything the class declares: an empty-valued class is not a located block
    row = pick([(1, 67, ["Subject"], 24, 0), (1, 91, ["Core"], 0, 0)], declared, 5, located=1)
    assert row["verdict"] == VERDICT_NO_TAG, row
    assert row["declared_cands"] == 0 and row["best_start"] == "", row
    cases += 1
    # the room filter is a bound and not a preference: exactly 42 clears, 41 does not
    for start, want in ((47, VERDICT_RELOCATED), (46, VERDICT_REFUSED)):
        row = pick([(1, start, ["Tone"], 0, 0)], declared, 5)
        assert row["verdict"] == want, (start, row)
        cases += 1
    # no declared tag, and the payload ends on the terminator: the one start the record leaves is an empty block
    row = pick([(1, 67, ["Subject"], 24, 0)], declared, 5, payload=96, terminal_zero=True)
    assert row["verdict"] == VERDICT_EMPTY and row["best_start"] == 95 and row["best_room"] == 90, row
    cases += 1
    # the same payload without the terminator is not a located start at all
    row = pick([(1, 67, ["Subject"], 24, 0)], declared, 5, payload=96, terminal_zero=False)
    assert row["verdict"] == VERDICT_NO_TAG, row
    cases += 1
    # a declared tag nearer the payload's end wins over a declared tag earlier in it
    row = pick([(1, 70, ["Tone"], 10, 0), (2, 88, ["Desc", "Tone"], 0, 0)], declared, 5)
    assert row["best_start"] == 88 and row["best_props"] == 2, row
    cases += 1
    print("class-record-room: selftest passed (%d cases)" % cases)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", help="game directory (System/*.u)")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("-o", "--out", help="inventory directory to write")
    ap.add_argument("--rows", help="the bound artifact to read instead of inventory/%s" % BOUND_ARTIFACT)
    args = ap.parse_args()
    if args.check:
        return check()
    if args.report or not (args.sweep or args.dump):
        return report()
    if not args.dump:
        ap.error("--sweep needs --dump")
    return sweep(args)


if __name__ == "__main__":
    sys.exit(main())