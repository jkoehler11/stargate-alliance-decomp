#!/usr/bin/env python3
"""Which translation unit an unattributed function belongs to (PLAN 4.5).

`inventory/function_modules.csv` (tools/module-split.py) attributes a function to a module only when the build
already named it that way -- 1,348 of 14,957 -- because the module comes from the `__FILE__` assert the function
carries. The other 13,609 are `(unnamed)` or one of the three name-only buckets, even though the code around them is
attributed: Perception compiled this build per translation unit, so a file's functions sit together in address order
(that is the same property `inventory/function_modules.csv`'s `run` column measures from the other side).

Three rules were scored against held-out labels (`--holdout N` hides N asserted labels, re-runs, and counts how
many come back). Two of them are the obvious ones and both fail: voting a module from the functions a function
calls and is called by hands the image to `Core`, because every file calls Core and Core has the most labels, and
weighting a callee by how many callers it has does not rescue it (a *fewer* labels, still under half right). The
rule that ships is **address locality**: a function belongs to the file of the nearest attributed function, which
is what "the TU's code is contiguous" means measured rather than assumed, and its labels are scored two ways: **95.7%** where the two neighbours name the same module and **68.8%** where they contradict each other (a contradicted label moves when a new assert lands beside it, and `--holdout` prints the split). The numbers in
`docs/MODULE_ATTRIBUTION.md` are what that run printed.

**A module label is not a name** (`docs/CONVENTIONS.md`): it says which translation unit's code this is, and
nothing about what the function does. A function no assertion sits near -- a CRT stub, a library routine, code
between two TUs -- keeps `(unnamed)`.

    python3 tools/module-attribution.py --xbe DUMP/default.xbe -o inventory       # the artifact (locality)
    python3 tools/module-attribution.py --xbe DUMP/default.xbe --holdout 400      # every rule, scored; writes nothing
    python3 tools/module-attribution.py --check                                   # dump-free, in `make ci`

**A scoring run writes no artifact.** `--holdout` hides asserted labels, so the rows that carry one come back
labelled `propagated` from a neighbour -- and a run that wrote the file shipped exactly that: 354 rows citing a
neighbour instead of their own assert, 43 of them a different module. The artifact is the full-seed run; the holdout
is a score.
"""
import argparse
import bisect
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from xbe import Xbe  # noqa: E402

COLUMNS = ["address", "name", "size", "asserted_module", "module", "method", "rule", "depth",
           "votes", "share", "evidence"]
MAX_ROUNDS = 3
MIN_VOTES = 2
MIN_SHARE = 0.6667
RULES = ("locality", "neighbours", "weighted")
DEFAULT_RULE = "locality"
# How far a locality label may reach. Swept by `--holdout`; see docs/MODULE_ATTRIBUTION.md.
GAPS = (0x200, 0x400, 0x800, 0x1000, 0x2000, 0x4000, None)
DEFAULT_GAP = 0x800


def is_module(label):
    """A real module label: the three name-only buckets and `(unnamed)` are not modules."""
    return bool(label) and not label.startswith("(")


def load_rows(symbols, modules):
    mod = {}
    with open(modules, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            mod[r["address"].lower()] = r["module"]
    rows = []
    with open(symbols, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append({"address": r["address"], "name": r["name"], "size": int(r["size"]),
                         "asserted_module": mod.get(r["address"].lower(), "(unnamed)")})
    rows.sort(key=lambda r: int(r["address"], 16))
    return rows


def call_edges(xbe, rows):
    """{caller_va: set(callee_va)} over direct `E8 rel32` calls whose target is a known function start."""
    starts = {int(r["address"], 16) for r in rows}
    edges = {}
    for r in rows:
        va, size = int(r["address"], 16), r["size"]
        sec = xbe.section_for(va)
        if sec is None:                      # census rows that sit in .data/.rdata as data, not code
            continue
        off = sec.foff + (va - sec.va)
        blob = xbe.data[off:off + size]
        out = []
        for i in range(len(blob) - 4):
            if blob[i] == 0xE8:
                target = va + i + 5 + struct.unpack_from("<i", blob, i + 1)[0]
                if target in starts:
                    out.append(target)
        if out:
            edges[va] = set(out)
    return edges


def reverse(edges):
    callers = {}
    for src, dsts in edges.items():
        for d in dsts:
            callers.setdefault(d, set()).add(src)
    return callers


def votes_for(va, label, edges, callers, rule):
    """[(module, weight, neighbour_va)] -- one vote per direction, weighted by the rule."""
    out = []
    for callee in edges.get(va, ()):
        mod = label.get(callee)
        if not is_module(mod):
            continue
        out.append((mod, 1.0 if rule == "neighbours" else 1.0 / max(1, len(callers.get(callee, ()))), callee))
    for caller in callers.get(va, ()):
        mod = label.get(caller)
        if is_module(mod):
            out.append((mod, 1.0, caller))
    return out


def propagate_callgraph(rows, edges, callers, seeds, rule):
    label = dict(seeds)
    out = {}
    for rnd in range(1, MAX_ROUNDS + 1):
        fresh = {}
        for r in rows:
            va = int(r["address"], 16)
            if va in label:
                continue
            tally, by = {}, {}
            for mod, weight, other in votes_for(va, label, edges, callers, rule):
                tally[mod] = tally.get(mod, 0.0) + weight
                by.setdefault(mod, []).append(other)
            if not tally:
                continue
            total = sum(tally.values())
            best, votes = max(tally.items(), key=lambda kv: (kv[1], kv[0]))
            share = votes / total
            if votes >= MIN_VOTES and share >= MIN_SHARE:
                fresh[va] = (best, rnd, votes, share, by[best])
        if not fresh:
            break
        for va, rec in fresh.items():
            label[va] = rec[0]
            out[va] = rec
    return out


def propagate_locality(rows, seeds, max_gap=None):
    """Each unattributed function takes the module of the nearest attributed function in address order.

    Both neighbours are consulted and the closer one wins; the row records the distance. `max_gap` bounds how far a
    label may reach: the library sections carry almost no asserts, so an unbounded rule reaches thousands of bytes
    and hands 6,547 functions to whichever module happens to sit nearest. `--holdout`'s gap sweep picks the bound.
    """
    addrs = [int(r["address"], 16) for r in rows]
    seed_va = sorted(seeds)
    out = {}
    for va in addrs:
        if va in seeds:
            continue
        j = bisect.bisect_left(seed_va, va)
        left = seed_va[j - 1] if j > 0 else None
        right = seed_va[j] if j < len(seed_va) else None
        cands = []
        if left is not None:
            cands.append((va - left, left, seeds[left]))
        if right is not None:
            cands.append((right - va, right, seeds[right]))
        if not cands:
            continue
        cands.sort()
        dist, neighbour, mod = cands[0]
        if max_gap is not None and dist > max_gap:
            continue
        agree = len(cands) > 1 and cands[1][2] == mod
        out[va] = (mod, 1, 2.0 if agree else 1.0, 1.0 if agree else 0.5, [neighbour])
    return out


def run(rows, edges=None, holdout=0, rules=RULES, gap=DEFAULT_GAP):
    """Score every rule on the same held-out sample; return each rule's labels for that sample."""
    seeds = {int(r["address"], 16): r["asserted_module"] for r in rows if is_module(r["asserted_module"])}
    hidden = {}
    if holdout:
        asserted = sorted(seeds)
        step = max(1, len(asserted) // holdout)
        hidden = {va: seeds[va] for va in asserted[::step][:holdout]}
    seeds = {va: m for va, m in seeds.items() if va not in hidden}
    callers = reverse(edges or {})
    results = {}
    for rule in rules:
        if rule == "locality":
            results[rule] = propagate_locality(rows, seeds, gap)
        else:
            results[rule] = propagate_callgraph(rows, edges or {}, callers, seeds, rule)
    return seeds, hidden, results


def score_by_share(hidden, got):
    """Recovery split by the rule's own confidence: `agree` is both neighbours voting one module, `disputed` is
    one neighbour outvoting the other. A disputed label is what a new assert can flip (see docs/MODULE_ATTRIBUTION.md)."""
    out = {}
    for va, want in hidden.items():
        rec = got.get(va)
        if not rec:
            continue
        key = "agree" if rec[3] >= 1.0 else "disputed"
        hit, n = out.get(key, (0, 0))
        out[key] = (hit + (rec[0] == want), n + 1)
    return out


def score(hidden, got):
    """Recovery of the held-out labels: exact matches, and how many were labelled at all."""
    by_module = {}
    hit = total = labelled = 0
    for va, want in hidden.items():
        if va not in got:
            continue
        labelled += 1
        ok = got[va][0] == want
        hit += ok
        h, n = by_module.get(want, (0, 0))
        by_module[want] = (h + ok, n + 1)
    total = labelled
    return by_module, hit, total, labelled


def write(rows, got, rule, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            va = int(r["address"], 16)
            rec = got.get(va)
            base = {"address": r["address"], "name": r["name"], "size": r["size"],
                    "asserted_module": r["asserted_module"]}
            if rec:
                mod, depth, votes, share, ev = rec
                w.writerow(dict(base, module=mod, method="propagated", rule=rule, depth=depth,
                                votes="%.2f" % votes, share="%.3f" % share,
                                evidence=" ".join("%#010x" % v for v in sorted(ev))))
            else:
                asserted = is_module(r["asserted_module"])
                w.writerow(dict(base, module=r["asserted_module"], method="assert" if asserted else "none",
                                rule="", depth=0 if asserted else "", votes="", share="", evidence=""))


def totals(rows, got):
    counts = {}
    for r in rows:
        rec = got.get(int(r["address"], 16))
        mod = rec[0] if rec else r["asserted_module"]
        counts[mod] = counts.get(mod, 0) + 1
    return counts


SYNTHETIC = [
    # A chain and a star. 0x1020 has one Core caller and nothing else, so one vote must not be enough.
    # 0x1080 has one Engine caller (0x1060) and one Engine callee (0x10a0): two votes, labelled in round 1.
    # Locality: 0x1020 sits between two Core seeds; 0x1090 sits closer to the Engine seed at 0x10a0.
    (0x1000, "Core_UnObj", "Core"),
    (0x1020, "FUN_00001020", "(unnamed)"),
    (0x1060, "Engine_UnActor", "Engine"),
    (0x1080, "FUN_00001080", "(unnamed)"),
    (0x1090, "FUN_00001090", "(unnamed)"),
    (0x10A0, "Engine_UnChan", "Engine"),
    (0x1100, "Core_UnProp", "Core"),
]
SYNTHETIC_EDGES = {0x1000: {0x1020}, 0x1060: {0x1080}, 0x1080: {0x10A0}}


def synthetic():
    rows = [{"address": "%#010x" % va, "name": name, "size": 0x20, "asserted_module": mod}
            for va, name, mod in SYNTHETIC]
    rows.sort(key=lambda r: int(r["address"], 16))
    seeds = {int(r["address"], 16): r["asserted_module"] for r in rows if is_module(r["asserted_module"])}
    problems = []
    for rule in ("neighbours", "weighted"):
        got = propagate_callgraph(rows, SYNTHETIC_EDGES, reverse(SYNTHETIC_EDGES), seeds, rule)
        if 0x1080 not in got or got[0x1080][0] != "Engine" or got[0x1080][1] != 1:
            problems.append("%s: the two-vote star was not labelled Engine in round 1" % rule)
        if 0x1020 in got:
            problems.append("%s: a one-vote function was labelled" % rule)
    loc = propagate_locality(rows, seeds)
    if loc.get(0x1020, ("",))[0] != "Core":
        problems.append("locality: the near Core seed did not label 0x1020")
    if loc.get(0x1090, ("",))[0] != "Engine":
        problems.append("locality: the nearer Engine seed did not label 0x1090")
    return problems


def check(path, symbols):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    problems = list(synthetic())
    if not rows:
        problems.append("no rows")
    elif sorted(rows[0]) != sorted(COLUMNS):
        problems.append("columns")
    n_symbols = sum(1 for _ in open(symbols, encoding="utf-8")) - 1
    if len(rows) != n_symbols:
        problems.append("%d rows against %d symbols" % (len(rows), n_symbols))
    prev = None
    for r in rows:
        va = int(r["address"], 16)
        if prev is not None and va <= prev:
            problems.append("addresses not ascending at %s" % r["address"])
            break
        prev = va
        if r["method"] == "none" and (is_module(r["module"]) or r["depth"] or r["votes"]):
            problems.append("%s is unattributed but carries a label" % r["address"])
            break
        if r["method"] == "assert" and r["module"] != r["asserted_module"]:
            problems.append("%s overwrote its asserted module" % r["address"])
            break
        # The invariant the shipped artifact broke: a row whose own assert names a module is an `assert` row. A
        # `propagated` label on one means the file was written by a run that had hidden that label.
        if is_module(r["asserted_module"]) and r["method"] != "assert":
            problems.append("%s carries the asserted module %s but is labelled %s"
                            % (r["address"], r["asserted_module"], r["method"]))
            break
        if r["method"] == "propagated" and (r["rule"] not in RULES or not is_module(r["module"])
                                            or not r["evidence"]):
            problems.append("%s carries a label with no evidence" % r["address"])
            break
    if problems:
        print("FAIL module-attribution: " + "; ".join(problems[:5]))
        return 1
    labelled = sum(1 for r in rows if is_module(r["module"]))
    moved = sum(1 for r in rows if r["method"] == "propagated")
    print("ok   module-attribution: %d of %d functions carry a module (%d by propagation)"
          % (labelled, len(rows), moved))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe", help="DUMP/default.xbe")
    ap.add_argument("--symbols", default=os.path.join(ROOT, "symbols", "xbox_functions.csv"))
    ap.add_argument("--modules", default=os.path.join(ROOT, "inventory", "function_modules.csv"))
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--rule", default=DEFAULT_RULE, choices=RULES)
    ap.add_argument("--holdout", type=int, default=0,
                    help="hide this many asserted labels and score every rule (a scoring run: writes no artifact)")
    ap.add_argument("--max-gap", type=lambda v: int(v, 0), default=DEFAULT_GAP,
                    help="how far a locality label may reach, in bytes (0 = unbounded)")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    path = os.path.join(args.out, "function_module_attribution.csv")
    if args.check:
        return check(path, args.symbols)
    if not args.xbe:
        sys.exit("--xbe is required (this reads the image; --check is the dump-free half)")
    xbe = Xbe(args.xbe)
    rows = load_rows(args.symbols, args.modules)
    edges = call_edges(xbe, rows)
    gap = args.max_gap or None
    seeds, hidden, results = run(rows, edges, args.holdout, gap=gap)
    got = results[args.rule]
    print("%d functions, %d call edges, %d asserted seeds"
          % (len(rows), sum(len(v) for v in edges.values()), len(seeds)))
    if hidden:
        print("scoring run: %d asserted labels hidden, every rule scored on that sample; artifact NOT written"
              % len(hidden))
        for rule in RULES:
            by_module, hit, total, labelled_here = score(hidden, results[rule])
            print("  %-11s %4d labelled, %4d exact (%.1f%%)"
                  % (rule, labelled_here, hit, 100.0 * hit / max(1, total)))
            for mod, (h, n) in sorted(by_module.items(), key=lambda kv: -kv[1][1])[:6]:
                print("      %-22s %4d/%4d" % (mod, h, n))
        print("locality scored by its own confidence (share 1.000 = both neighbours agree):")
        for key in ("agree", "disputed"):
            hit, n = score_by_share(hidden, results["locality"]).get(key, (0, 0))
            print("  %-9s %4d of %4d exact (%.1f%%)" % (key, hit, n, 100.0 * hit / max(1, n)))
        print("locality gap sweep (gap, functions labelled out of %d, held-out exact):" % len(rows))
        for g in GAPS:
            loc = propagate_locality(rows, seeds, g)
            _bm, h, t, lab = score(hidden, loc)
            print("  %-9s %6d labelled  %4d exact of %4d (%.1f%%)"
                  % ("unbounded" if g is None else "%#x" % g, len(loc), h, t,
                     100.0 * h / max(1, t)))
        return 0
    os.makedirs(args.out, exist_ok=True)
    write(rows, got, args.rule, path)
    counts = totals(rows, got)
    labelled = sum(n for m, n in counts.items() if is_module(m))
    print("rule %s: %d of %d carry a module (%.2f%%), %d of them propagated"
          % (args.rule, labelled, len(rows), 100.0 * labelled / len(rows), len(got)))
    for mod, n in sorted(counts.items(), key=lambda kv: -kv[1])[:12]:
        print("  %-24s %5d" % (mod, n))
    print("%d rows -> %s" % (len(rows), path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
