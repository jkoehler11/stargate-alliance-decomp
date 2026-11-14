#!/usr/bin/env python3
"""The located array ends past the block start on 95 rows, and the locator's head census does not single them out.

`inventory/class_record_array.csv` locates the record array from the payload's prefix: its `array_end` is the bare
array started at `prefix_end + 42` (22 `UState` bytes and five dwords), and `block_start` is where the defaults
buffer begins. On **95 of the 549** rows that carry both, the array's own end is *past* the block start -- the walk
swallowed the buffer -- and the census in `inventory/class_record_gap.csv` starts its parse from that end, which is
why a fifth of its rows read nothing at all.

That same artifact already carries the locator's own census (`heads`): every offset in `[prefix_end, block_start)`
whose array-plus-name-tail walk lands *exactly* on the block start, and the lowest such offset as `head_at` /
`head_off`. So the question "does the payload carry a record that closes on the block start" is already measured
per row; what was never joined is the two columns. This tool joins them and reports

* the overshoot population and its size (`array_end - block_start`, negative), against the 454 rows that land at or
  before the block;
* how many overshoot rows carry a head that closes on the block start, and at what offset -- a head is the payload's
  own evidence, independent of the documented position -- **against the same rate on the 454 rows that land at or
  before the block**. The overshoot rows carry one *less* often than the control, so "another head in the payload
  fixes them" is refused: the census is a property of the payload, not a discriminator of the overshoot;
* the same rows in `inventory/class_tail_run.csv`, which measures the block start from the *record* side: 93 of the
  95 are there, and on the 36 whose closing group parses the block start is confirmed from the record, which leaves
  the located tail -- not the payload and not the block -- as what the 95 have in common.

    python3 tools/class-record-overshoot.py --sweep -o inventory
    python3 tools/class-record-overshoot.py --report
    python3 tools/class-record-overshoot.py --check     # synthetic rows, writes nothing

Reads two committed artifacts; no dump, no boot, no Ghidra round.
"""
import argparse
import collections
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = "class_record_array.csv"        # the locator: array_end, block_start and its own head census
TAIL = "class_tail_run.csv"              # the block start measured from the record side
ARTIFACT = "class_record_overshoot.csv"
COLUMNS = ["package", "class", "payload", "prefix_end", "array_end", "block_start", "overshoot", "heads",
           "head_off", "head_count", "head_tails", "head_refs_ok", "doc_count", "doc_residue", "group_verdict",
           "group_last_unit", "group_run_len"]


def read_inventory(name):
    path = os.path.join(ROOT, "inventory", name)
    if not os.path.exists(path):
        return []
    return list(csv.DictReader(open(path, newline="", encoding="utf-8")))


def overshoot(row):
    """`array_end - block_start` when the located array ends past the buffer, else None."""
    if not row.get("array_end") or not row.get("block_start"):
        return None
    return int(row["array_end"]) - int(row["block_start"])


def join_row(row, tail):
    """One overshoot row, with the locator's head census and the record-side group where they exist."""
    over = overshoot(row)
    out = {c: "" for c in COLUMNS}
    out.update({"package": row["package"], "class": row["class"], "payload": row.get("payload", ""),
                "prefix_end": row.get("prefix_end", ""), "array_end": row["array_end"],
                "block_start": row["block_start"], "overshoot": over, "heads": row.get("heads", ""),
                "head_off": row.get("head_off", ""), "head_count": row.get("count", ""),
                "head_tails": row.get("tails", ""), "head_refs_ok": row.get("refs_ok", ""),
                "doc_count": row.get("doc_count", ""), "doc_residue": row.get("doc_residue", "")})
    if tail:
        out.update({"group_verdict": tail.get("verdict", ""), "group_last_unit": tail.get("last_unit", ""),
                    "group_run_len": tail.get("run_len", "")})
    return out


def sweep(args):
    tails = {(r["package"], r["class"]): r for r in read_inventory(TAIL)}
    rows = [join_row(r, tails.get((r["package"], r["class"]))) for r in read_inventory(SOURCE)
            if overshoot(r) is not None and overshoot(r) > 0]
    if args.out:
        os.makedirs(os.path.join(ROOT, args.out), exist_ok=True)
        path = os.path.join(ROOT, args.out, ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
        print("wrote %s: %d rows" % (path, len(rows)))
    return rows


def report(rows=None):
    if rows is None:
        rows = read_inventory(ARTIFACT)
    located = [r for r in read_inventory(SOURCE) if overshoot(r) is not None]
    at_or_before = [r for r in located if overshoot(r) <= 0]
    print("%d located rows carry an array end and a block start; %d of them end past the block start (%.1f%%)"
          % (len(located), len(rows), 100.0 * len(rows) / max(1, len(located))))
    print("  overshoot (array_end - block_start): %s"
          % ", ".join("%d:%d" % kv for kv in collections.Counter(int(r["overshoot"]) for r in rows).most_common(8)))
    print("  depth as a share of the payload: median %.1f%%"
          % (100.0 * sorted(abs(int(r["overshoot"])) / max(1, int(r["payload"] or 1)) for r in rows)[len(rows) // 2]
             if rows else 0.0))
    withhead = [r for r in rows if r["heads"] and int(r["heads"]) > 0]
    print("  the locator's head census on those rows: heads %s"
          % ", ".join("%s:%d" % kv for kv in collections.Counter(r["heads"] for r in rows).most_common(6)))
    print("  a head closes exactly on the block start on %d of %d (%.1f%%)"
          % (len(withhead), len(rows), 100.0 * len(withhead) / max(1, len(rows))))
    if withhead:
        print("  its offset from the prefix: %s"
              % ", ".join("%s:%d" % kv for kv in collections.Counter(r["head_off"] for r in withhead).most_common(8)))
        print("  that head's array count %s, tails %s, references inside the tables on %d"
              % (", ".join("%s:%d" % kv for kv in collections.Counter(r["head_count"] for r in withhead).most_common(4)),
                 ", ".join("%s:%d" % kv for kv in collections.Counter(r["head_tails"] for r in withhead).most_common(4)),
                 sum(1 for r in withhead if r["head_refs_ok"] == "1")))
    print("  control, the %d rows that land at or before the block:" % len(at_or_before))
    print("    a head closes there on %d (%.1f%%)"
          % (sum(1 for r in at_or_before if r["heads"] and int(r["heads"]) > 0),
             100.0 * sum(1 for r in at_or_before if r["heads"] and int(r["heads"]) > 0) / max(1, len(at_or_before))))
    grouped = [r for r in rows if r["group_verdict"]]
    print("  the record side also carries these rows: %d of %d, and its closing group parses on %d"
          % (len(grouped), len(rows), sum(1 for r in grouped if r["group_verdict"] == "names-ref-name-list")))
    ok = [r for r in grouped if r["group_verdict"] == "names-ref-name-list"]
    if ok:
        print("    on those, the closing run is %s bytes long and ends on the block start"
              % ", ".join("%s:%d" % kv for kv in collections.Counter(r["group_run_len"] for r in ok).most_common(4)))
    return 0


def selftest():
    """Synthetic locator rows: the overshoot rule, a head join and two refusals."""
    bad = 0
    cases = [({"package": "P", "class": "A", "payload": "200", "prefix_end": "6", "array_end": "150",
               "block_start": "102", "heads": "1", "head_off": "61", "count": "2", "tails": "1", "refs_ok": "1"},
              48, True),
             ({"package": "P", "class": "B", "payload": "200", "prefix_end": "6", "array_end": "102",
               "block_start": "102", "heads": "0"}, 0, False),
             ({"package": "P", "class": "C", "payload": "200", "prefix_end": "6", "array_end": "90",
               "block_start": "102", "heads": "2"}, -12, False)]
    for row, want_over, included in cases:
        got = overshoot(row)
        if got != want_over:
            print("FAIL class-record-overshoot: %s answered %s, expected %s" % (row["class"], got, want_over))
            bad += 1
        if (got is not None and got > 0) != included:
            print("FAIL class-record-overshoot: %s included=%s, expected %s" % (row["class"], got > 0, included))
            bad += 1
    joined = join_row(cases[0][0], {"verdict": "names-ref-name-list", "last_unit": "94", "run_len": "8"})
    if joined["group_verdict"] != "names-ref-name-list" or joined["group_last_unit"] != "94":
        print("FAIL class-record-overshoot: the record-side join dropped the group columns")
        bad += 1
    if overshoot({"array_end": "", "block_start": "90"}) is not None:
        print("FAIL class-record-overshoot: a row without an array end answered an overshoot")
        bad += 1
    if bad:
        return 1
    print("ok   class-record-overshoot: the overshoot rule, an inclusive block start, a head join and the record-side join")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return selftest()
    if args.sweep:
        return report(sweep(args))
    if args.report:
        return report()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
