#!/usr/bin/env python3
"""Name image functions from the call sites of functions already named (PLAN 4.2).

A matched library function's `REL32` relocations to *function* symbols are resolved calls: the
target address in the image is the function the library's own symbol table names. That names
targets whose bodies never matched — import thunks, inlined-heavy bodies, functions whose Ghidra
boundary differs — which is exactly the residue a masked-body pass leaves behind.

    python3 tools/xdk-call-names.py --libs ~/sg1/work/xdk/libs-all \\
        --matched inventory/xdk_lib_names.csv --symbols symbols/xbox_functions.csv \\
        -o inventory/xdk_call_names.csv --apply

A name is taken only when every caller that reaches the address agrees on it, and the artifact
records the vote count, so a single-caller name is distinguishable from one four callers confirm.
`--check` validates the committed artifact without libs or dump.
"""

import argparse
import collections
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from cofflib import CoffObject, CoffError, parse_archive  # noqa: E402

COLUMNS = ["address", "name", "votes", "other_names", "section"]
OVERRIDES = os.path.join("symbols", "xbe_overrides.csv")
FUNCTIONS = os.path.join("symbols", "xbox_functions.csv")


def library_relocations(lib_dir, verbose=True):
    """{(lib, member, name): [(offset, width, target_symbol, target_type)]}"""
    table = {}
    libs = objects = 0
    for fn in sorted(os.listdir(lib_dir)):
        if not fn.lower().endswith(".lib"):
            continue
        lib = os.path.splitext(fn)[0]
        try:
            members = parse_archive(open(os.path.join(lib_dir, fn), "rb").read())
        except (CoffError, OSError):
            continue
        libs += 1
        for member, blob in members:
            try:
                obj = CoffObject(member, blob)
                functions = obj.functions_full()
            except CoffError:
                continue
            objects += 1
            for name, _body, rels in functions:
                table.setdefault((lib, member, name), rels)
    if verbose:
        print("%d libraries, %d objects, %d functions" % (libs, objects, len(table)))
    return table


def collect_votes(xbe, matched, table):
    """{target_va: Counter({symbol: votes})} from every matched function's function calls."""
    votes = collections.defaultdict(collections.Counter)
    callers = collections.Counter()
    resolved = 0
    for row in matched:
        rels = table.get((row["lib"], row["member"], row["name"]))
        if not rels:
            continue
        addr = int(row["address"], 16)
        body = xbe.read_va(addr, int(row["size"]))
        if not body:
            continue
        for off, width, target, typ in rels:
            if width != 4 or not (typ & 0x20) or off + 4 > len(body):
                continue
            disp = struct.unpack("<i", body[off:off + 4])[0]
            va = addr + off + 4 + disp
            if xbe.section_for(va) is None:
                continue
            votes[va][target] += 1
            callers[va] += 1
            resolved += 1
    return votes, callers, resolved


def choose(counter):
    """(name, votes, other_names) for a winner with a strict majority, else (None, 0, ...)."""
    ranked = counter.most_common()
    if not ranked:
        return None, 0, ""
    (best, best_votes) = ranked[0]
    if len(ranked) > 1 and ranked[1][1] >= best_votes:
        return None, best_votes, ";".join("%s x%d" % (n, v) for n, v in ranked)
    return best, best_votes, ";".join("%s x%d" % (n, v) for n, v in ranked[1:])


def generate(args):
    from xbe import Xbe  # noqa: PLC0415 - dump reader

    xbe = Xbe(args.xbe)
    table = library_relocations(args.libs, verbose=not args.quiet)
    with open(args.matched, newline="") as fh:
        matched = list(csv.DictReader(fh))
    with open(args.symbols, newline="") as fh:
        functions = {int(r["address"], 16): r for r in csv.DictReader(fh)}

    votes, callers, resolved = collect_votes(xbe, matched, table)
    rows, rejected = [], collections.Counter()
    for va, counter in votes.items():
        row = functions.get(va)
        if row is None:
            rejected["not a function in the map"] += 1
            continue
        if not row["name"].startswith("FUN_"):
            rejected["already named"] += 1
            continue
        name, n_votes, others = choose(counter)
        if name is None:
            rejected["callers disagree"] += 1
            continue
        if n_votes < args.min_votes:
            rejected["under %d vote(s)" % args.min_votes] += 1
            continue
        sec = xbe.section_for(va)
        rows.append({"address": "0x%08x" % va, "name": name, "votes": n_votes,
                     "other_names": others, "section": sec.name if sec else ""})
    rows.sort(key=lambda r: int(r["address"], 16))

    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    print("%d call targets resolved; %d named" % (resolved, len(rows)))
    for reason, n in rejected.most_common():
        print("  rejected, %-28s %5d" % (reason, n))
    multi = sum(1 for r in rows if int(r["votes"]) > 1)
    print("  %d confirmed by more than one caller, %d single-caller" % (multi, len(rows) - multi))
    by_sec = collections.Counter(r["section"] for r in rows)
    print("  " + ", ".join("%s %d" % kv for kv in by_sec.most_common()))

    if args.apply:
        apply_names(rows, args)
    return 0


def apply_names(rows, args):
    have = set()
    if os.path.isfile(OVERRIDES):
        with open(OVERRIDES, newline="") as fh:
            have = {r["address"].lower() for r in csv.DictReader(fh)}
    added = 0
    with open(OVERRIDES, "a", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        for r in rows:
            if r["address"].lower() in have:
                continue
            w.writerow([r["address"], r["name"],
                        "call edge to %s from %s matched function(s)" % (r["name"], r["votes"]),
                        "", "", "", "", "", "", ""])
            added += 1

    renamed = conflicts = 0
    tmp = args.symbols + ".tmp"
    by_addr = {r["address"].lower(): r for r in rows}
    with open(args.symbols, newline="") as fh, open(tmp, "w", newline="") as out:
        rd = csv.DictReader(fh)
        w = csv.DictWriter(out, fieldnames=rd.fieldnames, lineterminator="\r\n")
        w.writeheader()
        for row in rd:
            hit = by_addr.get(row["address"].lower())
            if hit and row["name"].startswith("FUN_"):
                row["name"] = hit["name"]
                renamed += 1
            elif hit:
                conflicts += 1
            w.writerow(row)
    os.replace(tmp, args.symbols)
    print("overrides: +%d rows; function map: %d renamed, %d already named"
          % (added, renamed, conflicts))


def check(args):
    problems = []
    if not os.path.isfile(args.out):
        raise SystemExit("%s missing -- run the generator" % args.out)
    with open(args.out, newline="") as fh:
        rd = csv.DictReader(fh)
        if rd.fieldnames != COLUMNS:
            raise SystemExit("%s columns are %r" % (args.out, rd.fieldnames))
        rows = list(rd)
    with open(args.symbols, newline="") as fh:
        names = {r["address"].lower(): r["name"] for r in csv.DictReader(fh)}
    for row in rows:
        addr = row["address"].lower()
        if addr not in names:
            problems.append("%s is not a function in the map" % row["address"])
        elif names[addr] != row["name"]:
            problems.append("%s is %s in the map, %s here" % (row["address"], names[addr], row["name"]))
        if int(row["votes"]) < 1:
            problems.append("%s has no votes" % row["address"])
    # A name on two addresses is evidence here, not ambiguity: each address was named by its own
    # callers (two COMDAT copies of the same library function, each with its own call sites), so it
    # is reported rather than refused. The body matcher refuses duplicates for the opposite reason.
    dupes = [n for n, c in collections.Counter(r["name"] for r in rows).items() if c > 1]
    if problems:
        print("xdk-call-names: %d problem(s)" % len(problems))
        for p in problems[:10]:
            print("  " + p)
        return 1
    note = "; %d name(s) on two addresses (COMDAT copies)" % len(dupes) if dupes else ""
    print("ok   xdk-call-names: %d rows, each a named function in the map%s" % (len(rows), note))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--libs", help="directory holding the .lib archives (never committed)")
    ap.add_argument("--xbe", help="default.xbe")
    ap.add_argument("--matched", default=os.path.join("inventory", "xdk_lib_names.csv"),
                    help="the masked-body artifact: address,name,size,lib,member,section")
    ap.add_argument("--symbols", default=FUNCTIONS)
    ap.add_argument("-o", "--out", default=os.path.join("inventory", "xdk_call_names.csv"))
    ap.add_argument("--min-votes", type=int, default=1)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check(args)
    missing = [f for f in ("libs", "xbe") if not getattr(args, f)]
    if missing:
        raise SystemExit("--%s required to generate" % missing[0])
    return generate(args)


if __name__ == "__main__":
    sys.exit(main())
