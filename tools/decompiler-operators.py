#!/usr/bin/env python3
"""Where the reconstruction's operators disagree with the shipped source (PLAN 3.5's last gap).

Every package ships its own `ScriptText`, so the reconstruction has a statement-level oracle for text as well
as for structure -- and the operator population is the cheapest part of it to compare, because an operator is a
token rather than a tree. This tool counts, per class, the logical negation in both trees and in both positions
(`!a` against `a!`), the parenthesised groups, and the full operator multiset, so a claim like "the decompiler
loses the negation" or "it flattens precedence" is a number rather than an impression.

    python3 tools/decompiler-operators.py $WORK/source-tree $WORK/UnrealScript -o inventory

`inventory/decompiler_operators.csv` is the per-class table (plus one row per operator and one census row); the two
trees are game-derived and stay under `$WORK`. Comments are stripped because a commented-out `!a` is not a
negation, and string literals are stripped because this corpus writes English (`"hurry!"`) inside them: without
that second pass 305 postfix hits in the source tree are prose.

The check is a synthetic pair of trees (no dump, no trees, nothing written to the checkout): five shapes that
separate the position, the operand kind, and the two literal passes.
"""
import argparse
import csv
import os
import re
import shutil
import sys
import tempfile

COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
LITERAL = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'")
NEG_PREFIX = re.compile(r"(?<![=!<>])![A-Za-z_(]")
NEG_POSTFIX = re.compile(r"([A-Za-z_0-9\)\]]+)!(?!=)")
# Longest first, so `&&` is one token and not two `&`, and `!=` is not a `!` plus `=`.
OPERATOR = re.compile(r"\*\*|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^~<>]")
OPS = ["&&", "||", "==", "!=", "<=", ">=", "<<", ">>", "**",
       "+", "-", "*", "/", "%", "&", "|", "^", "~", "<", ">"]

FIELDS = ["kind", "package", "class",
          "source_prefix_not", "source_postfix_not", "decompiled_prefix_not", "decompiled_postfix_not",
          "source_parens", "decompiled_parens", "operand_name", "operand_call",
          "source_operators", "decompiled_operators"]


def clean(text):
    """Drop comments, then replace every string literal with an empty one (offsets do not matter here)."""
    return LITERAL.sub('""', COMMENT.sub("", text))


def counts(text):
    """The operator counts of one class's text."""
    text = clean(text)
    prefix = len(NEG_PREFIX.findall(text))
    postfix = list(NEG_POSTFIX.finditer(text))
    name = sum(1 for m in postfix if not m.group(1).endswith(")"))
    call = len(postfix) - name
    ops = OPERATOR.findall(text)
    per = {op: 0 for op in OPS}
    for op in ops:
        per[op] = per.get(op, 0) + 1
    return {
        "prefix_not": prefix,
        "postfix_not": len(postfix),
        "operand_name": name,
        "operand_call": call,
        "parens": text.count("("),
        "operators": len(ops),
        "per_op": per,
    }


def tree(root):
    """(package, class) -> path, for every `.uc` under `<root>/<package>/`."""
    out = {}
    for pkg in sorted(os.listdir(root)):
        path = os.path.join(root, pkg)
        if not os.path.isdir(path):
            continue
        for name in sorted(os.listdir(path)):
            if name.endswith(".uc"):
                out[(pkg, name[:-3])] = os.path.join(path, name)
    return out


def census(source_root, decompiled_root):
    """The rows of the artifact: one per shared class, one per operator, then the totals."""
    src, dec = tree(source_root), tree(decompiled_root)
    shared = sorted(set(src) & set(dec))
    rows, totals_per_op = [], {op: 0 for op in OPS}
    sums = {k: 0 for k in ("source_prefix_not", "source_postfix_not", "decompiled_prefix_not",
                           "decompiled_postfix_not", "source_parens", "decompiled_parens",
                           "operand_name", "operand_call", "source_operators", "decompiled_operators")}
    src_per_op = {op: 0 for op in OPS}
    for key in shared:
        a = counts(open(src[key], encoding="latin1").read())
        b = counts(open(dec[key], encoding="latin1").read())
        row = {
            "kind": "class", "package": key[0], "class": key[1],
            "source_prefix_not": a["prefix_not"], "source_postfix_not": a["postfix_not"],
            "decompiled_prefix_not": b["prefix_not"], "decompiled_postfix_not": b["postfix_not"],
            "source_parens": a["parens"], "decompiled_parens": b["parens"],
            "operand_name": b["operand_name"], "operand_call": b["operand_call"],
            "source_operators": a["operators"], "decompiled_operators": b["operators"],
        }
        for k, v in row.items():
            if k in sums:
                sums[k] += v
        for op in OPS:
            totals_per_op[op] += b["per_op"][op]
            src_per_op[op] += a["per_op"][op]
        rows.append(row)
    for op in OPS:
        rows.append({"kind": "operator", "package": "operator", "class": op,
                     "source_operators": src_per_op[op], "decompiled_operators": totals_per_op[op]})
    total = {"kind": "census", "package": "census", "class": "census"}
    total.update(sums)
    total["source_operators"] = sum(src_per_op.values())
    total["decompiled_operators"] = sum(totals_per_op.values())
    rows.append(total)
    return rows, len(src), len(dec), len(shared)


def write_csv(rows, path):
    with open(path, "w", newline="") as fh:
        out = csv.DictWriter(fh, fieldnames=FIELDS, restval="")
        out.writeheader()
        for row in rows:
            out.writerow(row)


def report(rows, nsrc, ndec, nshared):
    cls = [r for r in rows if r["kind"] == "class"]
    total = rows[-1]
    lost = [r for r in cls if r["source_prefix_not"] and not
            (r["decompiled_prefix_not"] + r["decompiled_postfix_not"])]
    print("%d classes in the source tree, %d in the reconstruction, %d in both" % (nsrc, ndec, nshared))
    print("negation: source %d prefix / %d postfix -- reconstruction %d prefix / %d postfix"
          % (total["source_prefix_not"], total["source_postfix_not"],
             total["decompiled_prefix_not"], total["decompiled_postfix_not"]))
    print("postfix operands: %d a name, %d a call or a parenthesised group (%.1f%% / %.1f%%)"
          % (total["operand_name"], total["operand_call"],
             100.0 * total["operand_name"] / max(1, total["decompiled_postfix_not"]),
             100.0 * total["operand_call"] / max(1, total["decompiled_postfix_not"])))
    print("parenthesised groups: source %d, reconstruction %d (%+.1f%%)"
          % (total["source_parens"], total["decompiled_parens"],
             100.0 * (total["decompiled_parens"] - total["source_parens"]) / max(1, total["source_parens"])))
    print("operator tokens: source %d, reconstruction %d" % (total["source_operators"],
                                                             total["decompiled_operators"]))
    print("classes that declare a negation and keep none: %d" % len(lost))


# --- the check: a synthetic pair of trees, no dump, nothing written to the checkout -------------

SYNTH_SOURCE = {
    "Core/Synth.uc": """
class Synth extends Object;
var bool bFlag;
function bool A(Pawn Other)
{
    if(!bFlag && Other != none) { return !(Other == none); }
    // a comment can hold a negation: if(!bFlag)
    Log("hurry! and !again");
    return true;
}
""",
    "Core/Gone.uc": """
class Gone extends Object;
var bool bFlag;
function bool B() { return !bFlag; }
""",
}

SYNTH_DECOMPILED = {
    "Core/Synth.uc": """
class Synth extends Object;
var bool bFlag;
function bool A(Pawn Other)
{
    if(bFlag! && Other != none) { return (Other == none)!; }
    Log("hurry! and !again");
    if(IsReady()!) { }
    return true;
}
""",
    # Gone.uc is absent from the reconstruction, so it is not a shared class and must not be counted.
}


def self_check():
    """Five shapes: prefix against postfix, a name against a call/group operand, two literal passes, and a
    class the reconstruction does not have. Then the artifact's own totals against the run's figures."""
    root = tempfile.mkdtemp(prefix="decompiler-operators-")
    try:
        for name, files in (("source-tree", SYNTH_SOURCE), ("UnrealScript", SYNTH_DECOMPILED)):
            for rel, text in files.items():
                path = os.path.join(root, name, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "w").write(text)
        rows, nsrc, ndec, nshared = census(os.path.join(root, "source-tree"),
                                           os.path.join(root, "UnrealScript"))
        total = rows[-1]
        checks = [
            ("the two classes in the source tree", nsrc, 2),
            ("the one class in the reconstruction", ndec, 1),
            ("the shared classes", nshared, 1),
            ("the source's prefix negations (one in the `if`, one before the call)", total["source_prefix_not"], 2),
            ("the source's postfix negations (the `!` inside the string is prose)", total["source_postfix_not"], 0),
            ("the reconstruction's prefix negations", total["decompiled_prefix_not"], 0),
            ("the reconstruction's postfix negations", total["decompiled_postfix_not"], 3),
            ("the operands that are a name", total["operand_name"], 1),
            ("the operands that are a call or a group", total["operand_call"], 2),
            ("the `&&` tokens in the source",
             [r for r in rows if r["kind"] == "operator" and r["class"] == "&&"][0]["source_operators"], 1),
            ("the `&&` tokens in the reconstruction",
             [r for r in rows if r["kind"] == "operator" and r["class"] == "&&"][0]["decompiled_operators"], 1),
            ("the `!=` that is a comparison and not a negation", total["decompiled_postfix_not"], 3),
            ("the census row's operator total against the operator rows",
             total["decompiled_operators"],
             sum(r["decompiled_operators"] for r in rows if r["kind"] == "operator")),
        ]
        bad = [(name, got, want) for name, got, want in checks if got != want]
        if bad:
            for name, got, want in bad:
                print("FAIL decompiler-operators: %s: got %r, expected %r" % (name, got, want))
            return 1
        path = os.path.join(root, "out.csv")
        write_csv(rows, path)
        written = list(csv.DictReader(open(path)))
        if written[-1]["kind"] != "census":
            print("FAIL decompiler-operators: the artifact does not end on its census row")
            return 1
        if int(written[-1]["decompiled_postfix_not"]) != 3:
            print("FAIL decompiler-operators: the census row is not the run's own figures")
            return 1
        print("ok decompiler-operators: 2 prefix in the source, 3 postfix in the reconstruction, "
              "1 name and 2 call/group operands, the string's `!` and the absent class not counted")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", nargs="?", help="the shipped source tree (tools/pkg-script-text.py --tree)")
    ap.add_argument("decompiled", nargs="?", help="the reconstructed tree (make decompile)")
    ap.add_argument("-o", "--out", help="write decompiler_operators.csv here")
    ap.add_argument("--check", action="store_true", help="run the synthetic self-test and exit")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if not args.source or not args.decompiled:
        ap.error("both trees are required unless --check is given")
    for path in (args.source, args.decompiled):
        if not os.path.isdir(path):
            print("decompiler-operators: not a tree: %s" % path, file=sys.stderr)
            return 2
    rows, nsrc, ndec, nshared = census(args.source, args.decompiled)
    if not nshared:
        print("decompiler-operators: the two trees share no class", file=sys.stderr)
        return 2
    report(rows, nsrc, ndec, nshared)
    if args.out:
        write_csv(rows, os.path.join(args.out, "decompiler_operators.csv"))
        print("wrote %s/decompiler_operators.csv (%d rows)" % (args.out, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
