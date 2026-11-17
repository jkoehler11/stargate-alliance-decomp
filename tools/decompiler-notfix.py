#!/usr/bin/env python3
"""Move every postfix negation of a reconstructed tree in front of its operand (PLAN 3.5, the mechanical fix).

`tools/decompiler-operators.py` measures the defect: the pinned UELib renderer writes the logical negation *after* its
operand (`bFlag!`, `IsReady()!`), and this build's own 2,064 source negations are all prefix, so the reconstructed
tree is not UnrealScript and a compile pass would stop on the first one. A postfix site names its own operand, so the
correction needs no expression tree -- take the operand the `!` follows and move the `!` in front of it:

    python3 tools/decompiler-notfix.py $WORK/UnrealScript $WORK/UnrealScript-fixed -o inventory

The pass is byte-preserving apart from the moved character: every file keeps its size, so a diff of the two trees is
exactly the sites it reports. Comments and string literals are skipped by a scanner rather than a regex, `!=` is not a
negation, and an operand this tool cannot delimit is **refused and named** instead of being guessed at.
`inventory/decompiler_notfix.csv` is the count manifest (one row per package plus a census row).

    python3 tools/decompiler-notfix.py --check      # synthetic tree, no dump, nothing written to the checkout
"""
import argparse
import csv
import os
import shutil
import sys
import tempfile

WORD = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.")
# A character before the operand that would make `!operand` read differently from the site's own tree.
LEAD = set("!~-+*/%&|^<>=,?:")

FIELDS = ["kind", "package", "files", "sites_moved", "refused", "bytes_delta"]


def bangs(text):
    """Every logical negation outside comments and string/char literals, as raw offsets of the `!`."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c == '"' or c == "'":
            quote, i = c, i + 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if c == "!":
            nxt = text[i + 1] if i + 1 < n else ""
            prev = text[i - 1] if i else ""
            if nxt != "=" and prev not in "=!<>":
                out.append(i)
        i += 1
    return out


def is_prefix(text, at):
    """A `!` whose next non-space character starts an operand is already the prefix form."""
    j = at + 1
    while j < len(text) and text[j] in " \t\r\n":
        j += 1
    if j >= len(text):
        return True
    c = text[j]
    return c.isalnum() or c in "_(\"'"


def matching(text, at, open_c, close_c):
    """The offset of the bracket that opens the group closing at `at`, or None."""
    depth, k = 0, at
    while k >= 0:
        if text[k] == close_c:
            depth += 1
        elif text[k] == open_c:
            depth -= 1
            if depth == 0:
                return k
        k -= 1
    return None


def quote_start(text, at):
    """The offset of the opening quote of the literal closing at `at`, or None."""
    quote, k = text[at], at - 1
    while k >= 0:
        if text[k] == "\\":
            k -= 2
            continue
        if text[k] == quote:
            return k
        k -= 1
    return None


def operand_start(text, at):
    """Where the postfix expression a `!` at `at` follows begins, or None when it cannot be delimited.

    The renderer puts the `!` at the end of the *receiver* or the primary, not at the end of the whole expression
    (`PlayerController(C)!.IsSpectating()`, `Class'X'!.static.F()`, `Array[i]!.Field`), so the negation applies to the
    chain and the only correct place for it is in front of the chain's first character. Walk back over groups, name
    literals and dotted links until nothing of the chain is left.
    """
    j = at - 1
    while True:
        while j >= 0 and text[j] in " \t\r\n":
            j -= 1
        if j < 0:
            return None
        if text[j] == ")":
            k = matching(text, j, "(", ")")
        elif text[j] == "]":
            k = matching(text, j, "[", "]")
        elif text[j] in "\"'":
            k = quote_start(text, j)
        else:
            k = j
        if k is None:
            return None
        if text[j] == '"':
            # A string literal is not a bool: the site is not a negation this pass can place.
            return None
        m = k - 1
        while m >= 0 and (text[m].isalnum() or text[m] == "_"):
            m -= 1
        start = m + 1
        if text[j] == "'" and start == k:
            # A name literal with no type in front of it is not the shape this corpus writes.
            return None
        # A link of the chain: `A[i].B(x)` puts the `!` before `A`, not before `B`.
        if start > 0 and text[start - 1] == ".":
            j = start - 2
            continue
        return start


def rewrite(text):
    """(new_text, moved, refused) for one class file."""
    sites = [s for s in bangs(text) if not is_prefix(text, s)]
    edits, refused = [], []
    for s in sites:
        start = operand_start(text, s)
        if start is None:
            refused.append(s)
            continue
        lead = text[start - 1] if start else ""
        if lead in LEAD:
            # `!` in front of an operand whose left neighbour is an operator would re-associate the expression.
            refused.append(s)
            continue
        edits.append((start, s))
    inserts, dels = set(), set()
    for start, s in edits:
        inserts.add(start)
        dels.add(s)
    out = []
    for i, ch in enumerate(text):
        if i in inserts:
            out.append("!")
        if i in dels:
            continue
        out.append(ch)
    if len(text) in inserts:
        out.append("!")
    return "".join(out), len(edits), refused


def tree(root):
    for pkg in sorted(os.listdir(root)):
        path = os.path.join(root, pkg)
        if not os.path.isdir(path):
            continue
        for name in sorted(os.listdir(path)):
            if name.endswith(".uc"):
                yield pkg, name, os.path.join(path, name)


def run(src, out):
    """Rewrite the tree, then report the counts the manifest carries."""
    rows, moved_all, refused_all, delta_all = [], 0, 0, 0
    per_pkg = {}
    for pkg, name, path in tree(src):
        text = open(path, encoding="latin1").read()
        new, moved, refused = rewrite(text)
        target = os.path.join(out, pkg)
        os.makedirs(target, exist_ok=True)
        open(os.path.join(target, name), "w", encoding="latin1", newline="").write(new)
        row = per_pkg.setdefault(pkg, [0, 0, 0, 0])
        row[0] += 1
        row[1] += moved
        row[2] += len(refused)
        row[3] += len(new) - len(text)
        moved_all += moved
        refused_all += len(refused)
        delta_all += len(new) - len(text)
        if refused:
            print("refused %s/%s: %d site(s) at %s" % (pkg, name, len(refused),
                                                       ",".join(str(r) for r in refused[:5])))
    for pkg in sorted(per_pkg):
        files, moved, refused, delta = per_pkg[pkg]
        rows.append({"kind": "package", "package": pkg, "files": files, "sites_moved": moved,
                     "refused": refused, "bytes_delta": delta})
    rows.append({"kind": "census", "package": "census", "files": sum(r["files"] for r in rows),
                 "sites_moved": moved_all, "refused": refused_all, "bytes_delta": delta_all})
    return rows


def write_csv(rows, path):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, restval="")
        w.writeheader()
        for r in rows:
            w.writerow(r)


# --- the check: one synthetic class with every shape the scanner has to separate -----------------

SYNTH = """class Synth extends Object;
var bool bFlag;
var int i;
function bool A(Pawn Other, int j)
{
\tif(bFlag! && Other != none) { return (Other == none)!; }
\tif(IsReady()!) { bFlag = c!; }
\tif(Items[i]!) { }
\tif(!bFlag) { }
\tif(Array[i]!.Field && Class'Synth.Synth'!.static.F()) { }
\t// a line comment with bFlag! and !bFlag
\t/* a block comment with bFlag! */
\tLog("hurry! and !again");
\treturn Other.Some(Nested(1), 2)!;
}
"""

EXPECT = """class Synth extends Object;
var bool bFlag;
var int i;
function bool A(Pawn Other, int j)
{
\tif(!bFlag && Other != none) { return !(Other == none); }
\tif(!IsReady()) { bFlag = !c; }
\tif(!Items[i]) { }
\tif(!bFlag) { }
\tif(!Array[i].Field && !Class'Synth.Synth'.static.F()) { }
\t// a line comment with bFlag! and !bFlag
\t/* a block comment with bFlag! */
\tLog("hurry! and !again");
\treturn !Other.Some(Nested(1), 2);
}
"""

REFUSE = """class Bad extends Object;
var bool bFlag;
function bool B()
{
\treturn "x"!;
}
"""


def self_check():
    root = tempfile.mkdtemp(prefix="decompiler-notfix-")
    try:
        os.makedirs(os.path.join(root, "in", "Core"))
        open(os.path.join(root, "in", "Core", "Synth.uc"), "w").write(SYNTH)
        open(os.path.join(root, "in", "Core", "Bad.uc"), "w").write(REFUSE)
        rows = run(os.path.join(root, "in"), os.path.join(root, "out"))
        total = rows[-1]
        got = open(os.path.join(root, "out", "Core", "Synth.uc")).read()
        checks = [
            ("the exact rewritten text", got, EXPECT),
            ("the sites it moved", total["sites_moved"], 8),
            ("the site it refused", total["refused"], 1),
            ("the byte delta (a moved character, not an edited expression)", total["bytes_delta"], 0),
            ("the files it wrote", total["files"], 2),
        ]
        bad = [(n, g, w) for n, g, w in checks if g != w]
        if bad:
            for n, g, w in bad:
                print("FAIL decompiler-notfix: %s: got %r, expected %r" % (n, g, w))
            return 1
        # Idempotent: a fixed tree has nothing left to move.
        again, moved, refused = rewrite(EXPECT)
        if again != EXPECT or moved or refused:
            print("FAIL decompiler-notfix: the pass is not idempotent (%d moved, %d refused)" % (moved, len(refused)))
            return 1
        path = os.path.join(root, "out.csv")
        write_csv(rows, path)
        if list(csv.DictReader(open(path)))[-1]["kind"] != "census":
            print("FAIL decompiler-notfix: the manifest does not end on its census row")
            return 1
        print("ok decompiler-notfix: 8 sites moved, 1 refused, sizes unchanged, the fixed text is a fixpoint")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", nargs="?", help="the reconstructed tree (make decompile)")
    ap.add_argument("out", nargs="?", help="where to write the fixed tree (never in the repository)")
    ap.add_argument("-o", "--manifest", help="write decompiler_notfix.csv here")
    ap.add_argument("--check", action="store_true", help="run the synthetic self-test and exit")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if not args.source or not args.out:
        ap.error("both trees are required unless --check is given")
    if not os.path.isdir(args.source):
        print("decompiler-notfix: not a tree: %s" % args.source, file=sys.stderr)
        return 2
    rows = run(args.source, args.out)
    total = rows[-1]
    print("%d files, %d sites moved, %d refused, byte delta %d"
          % (total["files"], total["sites_moved"], total["refused"], total["bytes_delta"]))
    if args.manifest:
        write_csv(rows, os.path.join(args.manifest, "decompiler_notfix.csv"))
        print("wrote %s/decompiler_notfix.csv (%d rows)" % (args.manifest, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
