#!/usr/bin/env python3
"""Account for the library-section bodies the naming passes leave behind (PLAN 4.2).

Two passes name library code: masked body equality (`xdk-lib-match.py`) and call edges
(`xdk-call-names.py`). What is left is this script's subject, and it answers one question per
body: is it in the supplied libraries at all?

* **trim** — a prefix of the image body equals a library function, so the function is that
  library function and Ghidra's body runs past its `ret`. Naming evidence.
* **shift** — a suffix equals one, i.e. Ghidra started the function late. Reported, not named:
  the name belongs at `address + k`, which is usually inside the same Ghidra function.
* **no counterpart** — no library body matches at any start offset 0..24 under the same quality
  rules the main pass uses. The body is not from the supplied set.

    python3 tools/xdk-lib-residue.py --libs ~/sg1/work/xdk/libs-all \\
        --xbe $SG1_DUMP/.../default.xbe -o inventory/xdk_lib_residue.csv --apply
"""

import argparse
import collections
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("xdk_lib_match", os.path.join(ROOT, "tools", "xdk-lib-match.py"))
lm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lm)

COLUMNS = ["address", "size", "section", "cause", "name", "lib", "trim", "note"]
SECTIONS = ("XNET", "XONLINE", "D3D", "D3DX", "XGRPH", "DSOUND", "XPP")
FUNCTIONS = os.path.join("symbols", "xbox_functions.csv")
OVERRIDES = os.path.join("symbols", "xbe_overrides.csv")
MAX_SHIFT = 24


def trusted(entry, sig_names, min_len, min_len_no_reloc):
    size = entry["sig"][0]
    if size < min_len:
        return False
    if not entry["relocs"] and size < min_len_no_reloc:
        return False
    return len(sig_names.get(entry["sig"], {entry["name"]})) <= 1


def shape(body):
    if not body:
        return "no bytes"
    if body[0] == 0xE9 or (body[0] == 0xFF and len(body) > 1 and body[1] == 0x25):
        return "thunk"
    if len(body) <= 8 and body[-1] in (0xC3, 0xC2):
        return "stub"
    return "body"


def generate(args):
    from xbe import Xbe  # noqa: PLC0415

    xbe = Xbe(args.xbe)
    index, sig_names = lm.load_index(args.libs, verbose=not args.quiet)
    functions = list(csv.DictReader(open(args.symbols, encoding="utf-8")))
    rows = [r for r in functions
            if r["name"].startswith("FUN_")
            and xbe.section_for(int(r["address"], 16)) is not None
            and xbe.section_for(int(r["address"], 16)).name in SECTIONS]
    if args.quiet:
        print("%d library-section FUN_* bodies" % len(rows))

    out, causes = [], collections.Counter()
    for r in rows:
        addr, size = int(r["address"], 16), int(r["size"])
        body = xbe.read_va(addr, size)
        sec = xbe.section_for(addr)
        row = {"address": r["address"], "size": size, "section": sec.name if sec else "",
               "cause": "no counterpart", "name": "", "lib": "", "trim": "",
               "note": shape(body)}
        found = None
        for kind, order in (("trim", range(1, MAX_SHIFT + 1)), ("shift", range(1, MAX_SHIFT + 1))):
            for k in order:
                if len(body) - k < 3:
                    break
                name, entry = lm.match_function(index, body[:-k] if kind == "trim" else body[k:])
                if name and trusted(entry, sig_names, args.min_len, args.min_len_no_reloc):
                    found = (kind, k, name, entry)
                    break
            if found:
                break
        if found:
            kind, k, name, entry = found
            row.update({"cause": kind, "name": name, "lib": entry["lib"], "trim": k})
            causes[kind] += 1
        else:
            causes["no counterpart"] += 1
            causes["  (%s shape)" % row["note"]] += 1
        out.append(row)

    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(out)

    named = [r for r in out if r["cause"] == "trim" and r["name"]]
    print("%d bodies accounted for; %d named by a trim match" % (len(out), len(named)))
    for cause, n in sorted(causes.items()):
        if not cause.startswith("  "):
            print("  %-16s %4d" % (cause, n))
    for r in named[:5]:
        print("  %s %s (trim %s, %s)" % (r["address"], r["name"][:40], r["trim"], r["lib"]))
    if args.apply and named:
        apply_names(named, args)
    return 0


def apply_names(named, args):
    have = set()
    if os.path.isfile(OVERRIDES):
        with open(OVERRIDES, newline="") as fh:
            have = {r["address"].lower() for r in csv.DictReader(fh)}
    added = 0
    with open(OVERRIDES, "a", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        for r in named:
            if r["address"].lower() in have:
                continue
            w.writerow([r["address"], r["name"],
                        "masked body equals %s.lib minus %s trailing bytes"
                        % (r["lib"], r["trim"]), "", "", "", "", "", "", ""])
            added += 1
    renamed = 0
    by_addr = {r["address"].lower(): r for r in named}
    tmp = args.symbols + ".tmp"
    with open(args.symbols, newline="") as fh, open(tmp, "w", newline="") as out:
        rd = csv.DictReader(fh)
        w = csv.DictWriter(out, fieldnames=rd.fieldnames, lineterminator="\r\n")
        w.writeheader()
        for row in rd:
            hit = by_addr.get(row["address"].lower())
            if hit and row["name"].startswith("FUN_"):
                row["name"] = hit["name"]
                renamed += 1
            w.writerow(row)
    os.replace(tmp, args.symbols)
    print("overrides: +%d rows; function map: %d renamed" % (added, renamed))


def check(args):
    problems = []
    if not os.path.isfile(args.out):
        raise SystemExit("%s missing -- run the generator" % args.out)
    with open(args.out, newline="") as fh:
        rd = csv.DictReader(fh)
        if rd.fieldnames != COLUMNS:
            raise SystemExit("%s columns are %r" % (args.out, rd.fieldnames))
        rows = list(rd)
    causes = collections.Counter(r["cause"] for r in rows)
    unknown = [c for c in causes if c not in ("trim", "shift", "no counterpart")]
    if unknown:
        problems.append("unknown cause(s): %s" % ", ".join(sorted(unknown)))
    for r in rows:
        if r["cause"] in ("trim", "shift") and (not r["name"] or not r["lib"]):
            problems.append("%s has cause %s but no name/lib" % (r["address"], r["cause"]))
        if r["cause"] == "no counterpart" and r["name"]:
            problems.append("%s is both matched and unmatched" % r["address"])
    if problems:
        print("xdk-lib-residue: %d problem(s)" % len(problems))
        for p in problems[:10]:
            print("  " + p)
        return 1
    print("ok   xdk-lib-residue: %d rows (%d trim, %d shift, %d no counterpart)"
          % (len(rows), causes["trim"], causes["shift"], causes["no counterpart"]))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--libs")
    ap.add_argument("--xbe")
    ap.add_argument("--symbols", default=FUNCTIONS)
    ap.add_argument("-o", "--out", default=os.path.join("inventory", "xdk_lib_residue.csv"))
    ap.add_argument("--min-len", type=int, default=12)
    ap.add_argument("--min-len-no-reloc", type=int, default=24)
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
