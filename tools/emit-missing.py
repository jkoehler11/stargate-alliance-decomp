#!/usr/bin/env python3
"""What a differing body is *missing* -- the aggregate view `emit_differ.csv` cannot give.

`emit-differ.py` classifies a differing body at its **first** node, which is the right question for "which rule is
wrong" and the wrong one for "what bytes are absent": `Pawn.SurfaceReduceDamage`'s first difference is its outer
`JumpIfNot`'s distance, so the row reads *a jump moved because something downstream is missing* and never names the
missing token. That body's answer was one `Nothing` in a call's parameter list, and it took a flattened diff of both
streams to see it.

This tool flattens both token trees in document order, masks the operands that are **positions in the compiler's
code array** (a `Jump`/`JumpIfNot` word, a `Context`/`ClassContext` skip word) and diffs the two sequences. The
report is two multisets per body -- tokens only the shipped stream carries (missing) and tokens only ours carries
(invented) -- grouped so that a family is a set of bodies with the same answer rather than one example. A family of
six bodies that are each missing one `Nothing` is one rule; six rows that each name a different jump is not.

    python3 tools/uscript-emit.py --tree $WORK/scripts-all --dump .../System -o $WORK/emit \\
        --residue-out $WORK/residue
    python3 tools/emit-missing.py --residue $WORK/residue/residue.csv              # the shapes, largest first
    python3 tools/emit-missing.py --residue ... --only Pawn.SurfaceReduceDamage --detail
    python3 tools/emit-missing.py --check                # synthetic streams, no dump, writes nothing

`--residue` reads the emitter's `--residue-out` CSV; only rows whose reason starts with `an emitted body that
differs` are read (a refusal has no `ours`, and its reason already names what the walk could not model).
"""

import argparse
import collections
import csv
import difflib
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIFFERS = "an emitted body that differs"
# The tokens whose first operand is a code-array position, not a value.
JUMPS = {0x06: "Jump", 0x07: "JumpIfNot"}
CONTEXT = {0x19, 0x12}


def _dis():
    spec = importlib.util.spec_from_file_location("uscript_dis", os.path.join(ROOT, "tools", "uscript-dis.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def flatten(nodes, out=None):
    """The tree in document order: a parent precedes its children, which is the order the compiler wrote them in."""
    out = [] if out is None else out
    for n in nodes:
        out.append(n)
        flatten(n["children"], out)
    return out


def signature(node):
    """A token's identity for the diff: the opcode plus every operand that is *not* a code position."""
    if node["opcode"] in JUMPS:
        return (node["opcode"], b"<pos>")
    if node["opcode"] in CONTEXT and len(node["operands"]) >= 2:
        return (node["opcode"], node["operands"][2:])       # skip word, then the size byte and anything after it
    return (node["opcode"], node["operands"])


def label(node, dis):
    name = node["name"] or ("op%02x" % node["opcode"])
    if node.get("native"):
        name += "(%s)" % node["native"]
    if node["operands"] and node["opcode"] not in JUMPS:
        name += " " + node["operands"].hex()
    return name


def missing_of(ours_hex, theirs_hex, dis, table):
    """`(missing, invented, aligned)` for one body: what only theirs carries, what only ours does."""
    a, b = bytes.fromhex(ours_hex), bytes.fromhex(theirs_hex)
    ta, tb = flatten(dis.parse(a, table)), flatten(dis.parse(b, table))
    matcher = difflib.SequenceMatcher(a=[signature(n) for n in ta], b=[signature(n) for n in tb], autojunk=False)
    missing, invented, aligned = collections.Counter(), collections.Counter(), []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag in ("insert", "replace"):
            for n in tb[j1:j2]:
                missing[label(n, dis)] += 1
        if tag in ("delete", "replace"):
            for n in ta[i1:i2]:
                invented[label(n, dis)] += 1
        aligned.append((tag, i1, i2, j1, j2))
    return missing, invented, aligned


def read_residue(path):
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        return [r for r in csv.DictReader(fh) if (r.get("reason") or "").startswith(DIFFERS) and r.get("ours")]


def report(rows, dis, table, only=None, detail=False, out=sys.stdout):
    groups = collections.defaultdict(list)
    for r in rows:
        key = "%s.%s%s" % (r["class"], r["function"], (" [" + r["state"] + "]") if r.get("state") else "")
        if only and only.lower() not in key.lower():
            continue
        missing, invented, aligned = missing_of(r["ours"], r["theirs"], dis, table)
        delta = len(bytes.fromhex(r["theirs"])) - len(bytes.fromhex(r["ours"]))
        groups[(tuple(sorted(missing.items())), tuple(sorted(invented.items())))].append((key, delta, r))
    for (missing, invented), members in sorted(groups.items(), key=lambda kv: (-len(kv[1]), -abs(kv[1][0][1]))):
        print("%2d bod%s | missing %s | invented %s"
              % (len(members), "y" if len(members) == 1 else "ies", dict(missing) or "-", dict(invented) or "-"),
              file=out)
        for key, delta, _r in members:
            print("      %-52s %+d byte(s) against the shipped body" % (key, delta), file=out)
        if detail:
            for key, _delta, r in members:
                _m, _i, aligned = missing_of(r["ours"], r["theirs"], dis, table)
                print("    %s: %d difference block(s)" % (key, len(aligned)), file=out)
                for tag, i1, i2, j1, j2 in aligned:
                    print("      %s ours[%d:%d] theirs[%d:%d]" % (tag, i1, i2, j1, j2), file=out)
        print(file=out)
    return groups


def check():
    """No dump: two hand-built streams whose answer is known, and the mask that must not hide a real difference."""
    dis = _dis()
    table = dis.Table()
    trailer = "040b47"
    # A call with **one** `Nothing` the walk did not write: `1b 4f 27 <0b> 16` against `1b 4f 27 16`.
    missing, invented, _a = missing_of("1b4f2716" + trailer, "1b4f270b16" + trailer, dis, table)
    if dict(missing) != {"Nothing": 1} or invented:
        print("FAIL emit-missing: a written hole reads as %r / %r, expected {'Nothing': 1} / {}"
              % (dict(missing), dict(invented)))
        return 1
    # An int literal that ships as a cast rather than a folded float constant: `39 3f 1d 9cffffff` against
    # `1e 0000c8c2` (`Pawn.TakeFallingDamage`'s `-100`).
    missing, invented, _a = missing_of("1e0000c8c2" + trailer, "393f1d9cffffff" + trailer, dis, table)
    if dict(missing) != {"IntConst 9cffffff": 1, "PrimitiveCast 3f": 1} or dict(invented) != {"FloatConst 0000c8c2": 1}:
        print("FAIL emit-missing: a folded constant reads as %r / %r" % (dict(missing), dict(invented)))
        return 1
    # A jump word that moved is **masked**: `07 10 00` against `07 13 00` over the same statement is not a finding,
    # which is the whole point -- the classifier upstream names the jump, this tool must not.
    missing, invented, aligned = missing_of("0710002d012d" + trailer, "0713002d012d" + trailer, dis, table)
    if missing or invented or aligned:
        print("FAIL emit-missing: a moved jump word reads as %r / %r, expected no difference" % (dict(missing),
                                                                                                dict(invented)))
        return 1
    # ...but a `Context` skip word *is* masked and its size byte is not: `19 01 63 09 05 00 04` against the same
    # node with skip 0x06 is one difference at the skip, and with size 0x00 it is one difference at the size.
    missing, invented, _a = missing_of("1901630905000401" + trailer, "1901630906000401" + trailer, dis, table)
    if missing or invented:
        print("FAIL emit-missing: a moved Context skip word reads as %r / %r" % (dict(missing), dict(invented)))
        return 1
    missing, invented, _a = missing_of("1901630905000401" + trailer, "1901630905000001" + trailer, dis, table)
    if not missing or not invented:
        print("FAIL emit-missing: a Context whose size byte moved reads as no difference")
        return 1
    print("ok   emit-missing: 5 synthetic cases (a hole, a folded constant, a masked jump, a masked skip, a size byte)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--residue", help="the emitter's --residue-out CSV")
    ap.add_argument("--only", help="one body, by `Class.Function` (substring)")
    ap.add_argument("--detail", action="store_true", help="with --only: the difference blocks' token ranges")
    ap.add_argument("--check", action="store_true", help="synthetic streams, no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        return check()
    if not args.residue:
        ap.error("--residue is required (or pass --check)")
    dis = _dis()
    groups = report(read_residue(args.residue), dis, dis.Table(), only=args.only, detail=args.detail)
    bodies = sum(len(v) for v in groups.values())
    print("%d differing body(ies) over %d shape(s)" % (bodies, len(groups)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
