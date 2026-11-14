#!/usr/bin/env python3
"""Where the start changes that name no more of the class come from, and what geometry they have.

`tools/class-defaults.py`'s group rule moved **373 of 2,319** starts: the declared fraction rose on 314 and fell on
one, and **58 change without it moving at all** -- 22 to a different start, 36 to no block. On most of them the
chosen block names no property the class declares, which is the one case where the new start has no name evidence at
all and only its geometry behind it (`StargateContent.HackingPanel` lands on offset 1,
`StargateContent.DecipheringTrigger` on 46 from 87).

This reads those 58 rows and nothing else. The pre-rule choice is the same chooser without the closing-group rule, and
both starts are placed against the payload's **committed** closing groups -- inside one, on its end, in the 12-byte
window past it, or on neither -- along with how many of the block's tags the class declares, the gap to the group's
end, and the bytes the parse covers. That is the geometry the coverage table needs before it can call a start with no
declared name a located block.

    python3 tools/class-defaults-moved.py DUMP/System --sweep -o inventory
    python3 tools/class-defaults-moved.py --report
    python3 tools/class-defaults-moved.py --dump DUMP/System --class StargateContent.HackingPanel
    python3 tools/class-defaults-moved.py --check        # synthetic bodies, no dump, writes nothing

Reads the packages; no boot, no Ghidra round, no property value decoded.
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

ARTIFACT = "class_defaults_moved.csv"
TAIL = 12                              # `class-defaults.GROUP_TAIL`: the window it offers past an empty group end
WHERE = ("inside-group", "on-group-end", "in-group-tail", "past-group-tail", "before-first-group", "no-group")
KINDS = ("start-moved", "block-lost", "block-gained")
COLUMNS = ["package", "class", "kind", "declared", "count_before", "count_after", "known_before", "known_after",
           "fraction_before", "fraction_after", "before_start", "after_start", "before_where", "after_where",
           "before_covered", "group_end", "gap", "to_end", "trailer_before", "trailer_after", "first_before",
           "first_after"]


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cdef = _load("class-defaults")


def positioned(start, intervals, tail=TAIL):
    """How a start sits relative to the payload's committed closing groups.

    `inside-group` is a start the record's own field covers (`q <= start < end`), `on-group-end` is exactly its end --
    the start the guard adds -- and `in-group-tail` is inside the window `class-defaults.py` offers past an end that
    reads as an empty block. The rest are starts no group explains, which is what a start naming nothing needs.
    """
    if not intervals:
        return "no-group"
    nearest = None
    for q, end in intervals:
        if q <= start < end:
            return "inside-group"
        if end <= start and (nearest is None or end > nearest):
            nearest = end
    if nearest is None:
        return "before-first-group"
    if start == nearest:
        return "on-group-end"
    if start - nearest < tail:
        return "in-group-tail"
    return "past-group-tail"


def fraction(choice, declared):
    """The declared fraction of a `choose()` row: how many of the block's tag names the class (or an ancestor) has.

    A class with no block has fraction 0 in `class_defaults_per_class.csv` as well, so the two sides of a block that
    disappeared stay comparable.
    """
    if choice is None:
        return 0.0
    return sum(1 for n in choice[3] if n in declared) / float(choice[1])


def verdict(choice, declared, floor=2):
    """The artifact's `fully_declared` for a choice: every tag declared, no contradiction, and the floor met."""
    if choice is None:
        return 0
    known = sum(1 for n in choice[3] if n in declared)
    return 1 if known == choice[1] and choice[4] == 0 and choice[1] >= floor else 0


def comparison(before, after, declared):
    """`(kind, fraction_before, fraction_after)` when the chosen block changed without the declared fraction moving.

    The population is the row-level change the coverage table reads: a start that moved, a block that disappeared
    behind the rule, or one the rule recovered where there was none -- each at the same declared fraction, so nothing
    in the block's *names* says which side is better.
    """
    if before is None and after is None:
        return None
    fb, fa = fraction(before, declared), fraction(after, declared)
    if abs(fb - fa) > 5e-5:
        return None
    if before is None:
        return "block-gained", fb, fa
    if after is None:
        return "block-lost", fb, fa
    if before[2] != after[2]:
        return "start-moved", fb, fa
    return None


def trailer_of(choice, cands):
    """The candidate's own trailer: the bytes it leaves before the payload's last one (`choose` keeps only -trailer)."""
    if choice is None:
        return ""
    for c in cands:
        if c[1] == choice[2]:
            return c[3]
    return ""


def row_for(pkg_name, cls, decl, data, intervals, before, after, kind, fb, fa, kept=()):
    """One artifact row: both starts placed against the committed groups, and what each block names."""
    def count(choice):
        return "" if choice is None else choice[1]

    def known(choice):
        return "" if choice is None else sum(1 for n in choice[3] if n in decl)

    def first_tag(choice):
        return "" if choice is None else (choice[3][0] if choice[3] else "")

    bw = "" if before is None else positioned(before[2], intervals)
    aw = "no-block" if after is None else positioned(after[2], intervals)
    cover_end = None
    if before is not None:
        for q, end in intervals:
            if q <= before[2] < end:
                cover_end = end
    nearest = None
    if after is not None:
        for _q, end in intervals:
            if end <= after[2] and (nearest is None or end > nearest):
                nearest = end
    return {
        "package": pkg_name,
        "class": cls,
        "kind": kind,
        "declared": len(decl),
        "count_before": count(before),
        "count_after": count(after),
        "known_before": known(before),
        "known_after": known(after),
        "fraction_before": round(fb, 4),
        "fraction_after": round(fa, 4),
        "before_start": "" if before is None else before[2],
        "after_start": "" if after is None else after[2],
        "before_where": bw,
        "after_where": aw,
        "before_covered": 1 if cover_end is not None else 0,
        "group_end": "" if nearest is None else nearest,
        "gap": "" if (nearest is None or after is None) else after[2] - nearest,
        "to_end": "" if after is None else len(data) - after[2],
        "trailer_before": trailer_of(before, kept),
        "trailer_after": trailer_of(after, kept),
        "first_before": first_tag(before),
        "first_after": first_tag(after),
    }


def classes_of(system_dir):
    """Every class export of every package, in name order, with the payload the chooser reads."""
    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        yield fname[:-2], pkg


def sweep(system_dir, outdir, wanted_class=None):
    mod = cdef.load_prop_stream()
    supers, declared_all = cdef.super_map(), cdef.declared_properties()
    rows, counts = [], collections.Counter()
    for pkg_name, pkg in classes_of(system_dir):
        nnames, nimports = len(pkg.names or []), len(pkg.imports or [])
        for exp in [e for e in pkg.exports if pkg.class_name_of(e) == "Class"]:
            cls = pkg.name(exp["name"])
            if wanted_class and "%s.%s" % (pkg_name, cls) not in (wanted_class, cls):
                continue
            decl, cur, seen = set(), cls, set()
            while cur and cur not in seen:
                seen.add(cur)
                decl |= declared_all.get(cur, set())
                cur = supers.get(cur)
            kinds = cdef.kinds_for_class(cls)
            cands, data = cdef.candidates(pkg, mod, exp, kinds=kinds)
            before = cdef.choose(list(cands), decl)
            orig_ids = {id(c) for c in cands}
            kept, _origins = cdef.guarded(list(cands), data, pkg, decl, kinds, nimports)
            after = cdef.choose(kept, decl)
            refused = len(cands) - sum(1 for c in kept if id(c) in orig_ids)
            if refused:
                counts["guarded-refused"] += 1
            if after is not None:
                counts["chosen"] += 1
                if refused and trailer_of(after, kept + list(cands)) not in ("", 0):
                    counts["chosen-behind-guarded"] += 1
                if trailer_of(after, kept + list(cands)) not in ("", 0):
                    # The chooser's comment says a post-rule candidate must run to the payload's last byte; count how
                    # often the chosen start leaves bytes behind, i.e. how much that restriction is actually doing.
                    counts["chosen-behind"] += 1
            if before is not None and after is not None and before[2] == after[2]:
                counts["same-start"] += 1
                continue
            got = comparison(before, after, decl)
            if got is None:
                if before is None or after is None:
                    counts["no-block-unchanged"] += 1
                else:
                    counts["fraction-up" if fraction(after, decl) > fraction(before, decl) else "fraction-down"] += 1
                continue
            kind, fb, fa = got
            counts[kind] += 1
            rows.append(row_for(pkg_name, cls, decl, data, cdef.group_intervals(data, nnames, nimports),
                                before, after, kind, fb, fa, kept + list(cands)))
    if wanted_class:
        if not rows:
            print("%s changes nothing at an unchanged declared fraction" % wanted_class)
        for r in rows:
            print("  ".join("%s=%s" % (k, r[k]) for k in COLUMNS))
        return 0, counts
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, ARTIFACT)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("wrote %s (%d rows)" % (path, len(rows)))
    report(rows, counts)
    return 0, counts


def report(rows, counts=None):
    """The split the coverage table needs: where each start sits, and whether its block names anything."""
    if counts:
        print("chooser: " + ", ".join("%s %d" % (k, v) for k, v in sorted(counts.items())))
    kinds = collections.Counter(r["kind"] for r in rows)
    where_after = collections.Counter(r["after_where"] for r in rows)
    where_before = collections.Counter(r["before_where"] or "no-block" for r in rows)
    print("%d rows change at an unchanged declared fraction: %s"
          % (len(rows), ", ".join("%s %d" % (k, v) for k, v in kinds.most_common())))
    print("  before: " + ", ".join("%s %d" % (k, v) for k, v in where_before.most_common()))
    print("  after:  " + ", ".join("%s %d" % (k, v) for k, v in where_after.most_common()))
    silent = [r for r in rows if r["known_after"] in ("", 0)]
    print("  %d name no declared property after the change (%d before)"
          % (len(silent), sum(1 for r in rows if r["known_before"] in ("", 0))))
    for label, pop in (("silent", silent), ("named", [r for r in rows if r not in silent])):
        w = collections.Counter(r["after_where"] for r in pop)
        print("  %-6s after: %s" % (label, ", ".join("%s %d" % (k, v) for k, v in w.most_common())))
    print("  %d of the pre-rule starts sat inside a committed group; %d of the new starts are on a group's end"
          % (sum(int(r["before_covered"]) for r in rows),
             sum(1 for r in rows if r["after_where"] == "on-group-end")))
    behind = [int(r["trailer_after"]) for r in rows if r["trailer_after"] != "" and int(r["trailer_after"]) != 0]
    print("  %d of the new starts leave bytes before the payload's last (trailers %s)"
          % (len(behind), sorted(set(behind))))


def load_rows(outdir=os.path.join(ROOT, "inventory")):
    with open(os.path.join(outdir, ARTIFACT), newline="") as fh:
        return list(csv.DictReader(fh))


def check():
    """Synthetic bodies: the placement rule, the fraction, and the three kinds of change. No dump, writes nothing."""
    intervals = [(10, 20), (30, 48)]
    cases = [
        (10, "inside-group"), (19, "inside-group"), (20, "on-group-end"),
        (21, "in-group-tail"), (31, "inside-group"), (32, "inside-group"),
        (47, "inside-group"), (48, "on-group-end"), (49, "in-group-tail"),
        (60, "past-group-tail"), (5, "before-first-group"),
    ]
    bad = []
    for start, want in cases:
        got = positioned(start, intervals)
        if got != want:
            bad.append("start %d placed %s, expected %s" % (start, got, want))
    if positioned(0, []) != "no-group":
        bad.append("a payload with no group answered %s" % positioned(0, []))
    decl = {"A", "B"}
    before = (None, 2, 5, ["A", "X"], 0)          # fraction 0.5
    same = (None, 2, 33, ["A", "Y"], 0)           # fraction 0.5, start moved
    better = (None, 2, 33, ["A", "B"], 0)         # fraction 1.0
    lost = comparison((None, 2, 5, ["X", "Y"], 0), None, decl)      # fraction 0 -> no block: unchanged
    gained = comparison(None, (None, 2, 5, ["X", "Y"], 0), decl)    # no block -> fraction 0: unchanged
    if comparison(before, same, decl) != ("start-moved", 0.5, 0.5):
        bad.append("an unchanged-fraction move was not reported: %r" % (comparison(before, same, decl),))
    if comparison(before, better, decl) is not None:
        bad.append("a fraction change was reported as unchanged")
    if comparison(before, (None, 2, 5, ["A", "X"], 0), decl) is not None:
        bad.append("an unmoved start was reported")
    if lost is None or lost[0] != "block-lost" or abs(lost[2]) > 1e-9:
        bad.append("a block lost at fraction 0.5 was not reported: %r" % (lost,))
    if gained is None or gained[0] != "block-gained":
        bad.append("a recovered block at fraction 0 was not reported: %r" % (gained,))
    if comparison(None, None, decl) is not None:
        bad.append("two empty sides were reported as a change")
    if abs(fraction(before, decl) - 0.5) > 1e-9 or fraction(None, decl) != 0.0:
        bad.append("fraction() answered %r / %r" % (fraction(before, decl), fraction(None, decl)))
    if verdict((None, 2, 5, ["A", "B"], 0), decl) != 1 or verdict((None, 1, 5, ["A"], 0), decl) != 0:
        bad.append("verdict() mis-scored a declared block or a one-tag block")
    if bad:
        print("FAIL class-defaults-moved: " + "; ".join(bad))
        return 1
    print("ok   class-defaults-moved: %d placements, 7 choices" % (len(cases) + 1))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("system", nargs="?", help="DUMP/System (the .u packages)")
    ap.add_argument("--dump", help="alias for the positional System directory")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--sweep", action="store_true", help="write the artifact over every class")
    ap.add_argument("--report", action="store_true", help="re-print the split from the committed artifact")
    ap.add_argument("--class", dest="klass", help="one class, printed instead of written")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        return check()
    if args.report:
        report(load_rows())
        return 0
    system = args.system or args.dump
    if not system:
        ap.error("a System directory is required unless --check or --report")
    rc, _counts = sweep(system, args.out, args.klass)
    return rc


if __name__ == "__main__":
    sys.exit(main())
