#!/usr/bin/env python3
"""Lex the shipped UnrealScript: every byte classified, or the tool refuses (PLAN 3.6, the front end's first stage).

`docs/COMPILER.md` measures the situation: Epic's public UE2 Runtime compiles, but reads these packages as
`Bad expr token 03`, so the compile loop needs a front end written here rather than a better download. That front
end starts with a lexer, and the shipped source is the oracle for it: 4,637 `.uc` files, ~12 MB, written by the
toolchain the packages came from.

The gate is coverage. Every byte of every file is either whitespace, a comment, a string, a quoted name, a number,
an identifier, a keyword, a directive, a macro or an operator -- and the token spans concatenate back to the input
with no gap and no overlap. A byte that matches nothing is reported with its offset and stops the run, because "the
lexer is nearly right" is how a front end starts producing plausible wrong output.

    python3 tools/uscript-lex.py --tree $WORK/scripts-all -o inventory
    python3 tools/uscript-lex.py --check          # synthetic corpus, no dump needed

`inventory/uscript_lex.csv` is the per-file table plus a census row; the source tree is game-derived and stays under
`$WORK`.
"""

import argparse
import csv
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Longest match first: two-character operators before their one-character prefixes, `>>>` before `>>`.
OPERATORS = [
    ">>>", "<<=", ">>=", "**=", "...",
    "==", "!=", "<=", ">=", "&&", "||", "++", "--", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
    "<<", ">>", "**", "~~", "~=", "^^", "@=", "$=", "->",
    "+", "-", "*", "/", "%", "=", "<", ">", "!", "&", "|", "^", "~", "@", "$", "?", ":", ".", ",",
    ";", "(", ")", "[", "]", "{", "}", "#",
]

KEYWORDS = {
    "abstract", "array", "assert", "auto", "bool", "break", "byte", "case", "class", "coerce", "config",
    "const", "continue", "default", "defaultproperties", "delegate", "deprecated", "do", "else", "enum",
    "event", "exec", "expands", "export", "extends", "false", "final", "float", "for", "foreach", "function",
    "global", "goto", "if", "ignores", "import", "input", "int", "interface", "iterator", "latent", "local",
    "name", "native", "new", "none", "not", "operator", "optional", "out", "postoperator", "preoperator",
    "private", "protected", "public", "reliable", "repnotify", "return", "returns", "self", "simulated",
    "singular", "state", "static", "string", "struct", "super", "switch", "transient", "travel", "true",
    "unreliable", "until", "var", "vector", "while", "within",
}

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
NUMBER = re.compile(r"0[xX][0-9A-Fa-f]+|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?[fF]?")
DIRECTIVE = re.compile(r"#[A-Za-z][A-Za-z0-9]*")
MACRO = re.compile(r"`[A-Za-z_][A-Za-z0-9_]*")


def lex(text):
    """Return (tokens, bad) where tokens are (kind, start, end) and bad is the first unclassifiable offset."""
    tokens = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n\f\v":
            j = i + 1
            while j < n and text[j] in " \t\r\n\f\v":
                j += 1
            tokens.append(("space", i, j))
            i = j
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            tokens.append(("comment", i, j))
            i = j
            continue
        if text.startswith("/*", i):
            # This text's block comments **nest**. `TeamVoicePack.uc` comments out a `PlayerSpeech` that itself
            # contains a `/* ... */` pair, and the compiler read the outer comment to its own terminator: the
            # package declares all 21 functions of that file, including the five that follow the inner close, and
            # a non-nesting scan left the remaining 26 lines of the commented-out body as code -- two unbalanced
            # `}` and every later declaration invisible. Measured, not assumed: the export table is the oracle.
            j, depth = i + 2, 1
            while j < n:
                if text.startswith("/*", j):
                    depth += 1
                    j += 2
                elif text.startswith("*/", j):
                    depth -= 1
                    j += 2
                    if depth == 0:
                        break
                else:
                    j += 1
            tokens.append(("comment", i, min(j, n)))
            i = min(j, n)
            continue
        if ch == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    break
                j += 1
            tokens.append(("string", i, min(j, n)))
            i = min(j, n)
            continue
        if ch == "'":
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "'":
                    j += 1
                    break
                j += 1
            tokens.append(("quoted", i, min(j, n)))
            i = min(j, n)
            continue
        if ch == "#":
            m = DIRECTIVE.match(text, i)
            if m:
                j = text.find("\n", i)
                j = n if j < 0 else j
                tokens.append(("directive", i, j))
                i = j
                continue
        if ch == "`":
            m = MACRO.match(text, i)
            if m:
                tokens.append(("macro", i, m.end()))
                i = m.end()
                continue
        m = NUMBER.match(text, i)
        if m:
            tokens.append(("number", i, m.end()))
            i = m.end()
            continue
        m = IDENT.match(text, i)
        if m:
            # UnrealScript is case-insensitive and the source uses it: `Var config int FearRadius;` is a var
            kind = "keyword" if m.group().lower() in KEYWORDS else "ident"
            tokens.append((kind, i, m.end()))
            i = m.end()
            continue
        for op in OPERATORS:
            if text.startswith(op, i):
                tokens.append(("op", i, i + len(op)))
                i += len(op)
                break
        else:
            if ord(ch) >= 0x80:
                # The shipped text is not always marker-clean: a class comment whose `//` was consumed elsewhere
                # leaves prose in the stream, and prose carries UTF-8 (`\xc2\xa9` for the copyright sign). Classify the
                # run so coverage still holds, and let the artifact say which files are not pure ASCII -- a parser
                # must refuse those in code position rather than guess.
                j = i + 1
                while j < n and ord(text[j]) >= 0x80:
                    j += 1
                tokens.append(("high", i, j))
                i = j
                continue
            return tokens, i
    return tokens, None


def coverage(text, tokens):
    """The token spans must tile the input exactly."""
    at = 0
    for _kind, start, end in tokens:
        if start != at:
            return start
        at = end
    return None if at == len(text) else at


def lex_tree(tree):
    rows = []
    totals = {}
    for dirpath, _dirs, files in os.walk(tree):
        for name in sorted(files):
            if not name.endswith(".uc"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, "r", encoding="latin-1", newline="") as fh:
                text = fh.read()
            tokens, bad = lex(text)
            gap = coverage(text, tokens) if bad is None else bad
            if gap is not None:
                return None, (os.path.relpath(path, tree), gap,
                              text[max(0, gap - 20):gap + 20].replace("\n", "\\n"))
            counts = {}
            for kind, _s, _e in tokens:
                counts[kind] = counts.get(kind, 0) + 1
            for kind, count in counts.items():
                totals[kind] = totals.get(kind, 0) + count
            rows.append({"file": os.path.relpath(path, tree), "bytes": len(text),
                         "tokens": len(tokens), **{k: counts.get(k, 0) for k in
                                                   ("space", "comment", "string", "quoted", "number",
                                                    "ident", "keyword", "directive", "macro", "op", "high")}})
    return (rows, totals), None


SYNTH = '''class Probe extends Actor;\r\n// a comment with a ! and a "quote\r\n/* block\r\n comment */\r\nvar int Count;\r\nenum EFoo { A, B };\r\nfunction bool Check(optional int N)\r\n{\r\n\tlocal string S;\r\n\tS = "hurry! \\"quoted\\" text";\r\n\tif (N >= 2 && N != 3) { return true; }\r\n\tforeach AllActors(class\'Actor\', A) { break; }\r\n\treturn N >>> 1 == 0x1f || `define(FOO);\r\n}\r\n'''


# A block comment that carries a second one inside it: the compiler's own reading of `TeamVoicePack.uc`. The
# declarations after the inner `*/` are **still commented out**, so a lexer that closes on the first terminator
# reads `function Skipped()` and `var int Trapped` as code and reports the class with two extra names.
SYNTH_NESTED = ('class Probe extends Actor;\r\n'
                '/* outer /* inner */ function Skipped() { return; } var int Trapped; */\r\n'
                'var int Count;\r\n')

SYNTH_OP = '''function bool Same(string A, string B, bool X, bool Y)\r\n{\r\n\treturn (A ~= B) ^^ (X || Y);\r\n}\r\n'''


def check(_args):
    tokens, bad = lex(SYNTH)
    gap = coverage(SYNTH, tokens) if bad is None else bad
    if gap is not None:
        print("FAIL uscript-lex: the synthetic corpus is not classified at %d" % gap)
        return 1
    # The two-character operators the engine's own table declares and this list did not carry: `~=` is
    # `ComplementEqual_StrStr` (native 124) and `^^` is `Xor_BoolBool`. Lexed as `~` + `=` they looked like an
    # assignment, so every body whose condition was `A[i].Name ~= B` refused as "a receiver chain whose member is
    # not one name" -- the member scan met the `=` first.
    ops = "".join(SYNTH_OP[start:end] for kind, start, end in lex(SYNTH_OP)[0] if kind == "op")
    for need in ("~=", "^^"):
        if need not in ops:
            print("FAIL uscript-lex: %r is not one operator token" % need)
            return 1
    ntokens, nbad = lex(SYNTH_NESTED)
    if nbad is not None:
        print("FAIL uscript-lex: the nested-comment corpus is not classified at %d" % nbad)
        return 1
    ncomments = [SYNTH_NESTED[start:end] for kind, start, end in ntokens if kind == "comment"]
    if len(ncomments) != 1 or not ncomments[0].endswith("Trapped; */"):
        print("FAIL uscript-lex: a nested block comment closed on its inner terminator")
        return 1
    if any(v == "Skipped" for kind, _s, _e in ntokens if kind == "ident"
           for v in [SYNTH_NESTED[_s:_e]]):
        print("FAIL uscript-lex: a declaration inside a nested comment was read as code")
        return 1
    kinds = {}
    for kind, start, end in tokens:
        kinds[kind] = kinds.get(kind, 0) + 1
        if kind == "string" and "hurry!" not in SYNTH[start:end]:
            print("FAIL uscript-lex: a string token does not span its literal")
            return 1
        if kind == "comment" and "block" in SYNTH[start:end] and not SYNTH[start:end].endswith("*/"):
            print("FAIL uscript-lex: a block comment does not end on its terminator")
            return 1
    for need in ("comment", "string", "quoted", "number", "ident", "keyword", "op"):
        if not kinds.get(need):
            print("FAIL uscript-lex: the synthetic corpus produced no %s token" % need)
            return 1
    print("ok uscript-lex: %d tokens, %s" % (len(tokens), ", ".join("%s %d" % kv for kv in sorted(kinds.items()))))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tree", help="a directory of .uc files (use $WORK, never the repo)")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--check", action="store_true", help="self-test on a synthetic corpus (no tree needed)")
    args = ap.parse_args()
    if args.check:
        return check(args)
    if not args.tree:
        print("FAIL uscript-lex: --tree is required (the source corpus is game-derived and stays under $WORK)")
        return 2
    result, failure = lex_tree(args.tree)
    if failure:
        rel, gap, around = failure
        print("FAIL uscript-lex: %s is not classified at byte %d: %r" % (rel, gap, around))
        return 1
    rows, totals = result
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "uscript_lex.csv")
    fields = ["file", "bytes", "tokens", "space", "comment", "string", "quoted", "number", "ident",
              "keyword", "directive", "macro", "op", "high"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    total_bytes = sum(r["bytes"] for r in rows)
    print("%d file(s), %d bytes, %d tokens -- every byte classified"
          % (len(rows), total_bytes, sum(r["tokens"] for r in rows)))
    print("wrote %s" % os.path.relpath(path, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
