#!/usr/bin/env python3
"""Parse the shipped source's declarations and score them against the packages (PLAN 3.6, front end stage 2a).

Stage 2 is a parser, and its first half is declarations: a class header, its `var`s, `function`s, `state`s, `struct`s,
`enum`s and `const`s. Those are exactly the things `inventory/classes.csv` already counts from the package export
tables, so the parser can be scored instead of trusted -- per class, six numbers and a super name, each side measured
independently.

    python3 tools/uscript-decls.py --tree $WORK/scripts-all -o inventory
    python3 tools/uscript-decls.py --check        # synthetic corpus, no tree needed

`inventory/uscript_decls.csv` is the per-file table; the summary line reports how many files agree with the package on
class name, super, and each count. Agreement is reported, not assumed: a convention mismatch (does `properties` count
`var` names or statements?) shows up as a column that agrees less than the others, which is a measurement to read
rather than a bug to hide.
"""

import argparse
import csv
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import importlib.util

_spec = importlib.util.spec_from_file_location("uscript_lex", os.path.join(ROOT, "tools", "uscript-lex.py"))
lexer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lexer)

SKIP = {"space", "comment", "macro", "high"}

# The same build guard `tools/text-branches.py` measured: this corpus is the Xbox image, so XBox and DEBUG are defined
# and the PS2/PC branches were never compiled. `#ifdefPS2` spells the directive with no space, so the macro is the rest
# of the word, and names are case-folded (reading `#ifdefXBOX` case-sensitively breaks 8 functions there).
BUILD_DEFINED = frozenset({"xbox", "debug"})
GUARD = re.compile(r"^\s*#\s*(ifdef|ifndef|else|endif)\s*(\w*)", re.I)


def unbuilt_removed(toks):
    """Drop every token inside a branch this build did not compile."""
    out = []
    stack = []
    active = True
    for kind, val in toks:
        if kind == "directive":
            m = GUARD.match(val)
            if not m:
                continue
            what, name = m.group(1).lower(), m.group(2).lower()
            if what in ("ifdef", "ifndef"):
                cond = (name in BUILD_DEFINED) if what == "ifdef" else (name not in BUILD_DEFINED)
                stack.append((active, cond))
                active = active and cond
            elif what == "else" and stack:
                parent, cond = stack[-1]
                active = parent and not cond
                stack[-1] = (parent, not cond)
            elif what == "endif" and stack:
                active = stack.pop()[0]
            continue
        if active:
            out.append((kind, val))
    return out


def tokens_of(text):
    toks, bad = lexer.lex(text)
    if bad is not None:
        return None
    out = []
    for kind, start, end in toks:
        if kind in SKIP:
            continue
        out.append((kind, text[start:end]))
    return out


def declarations(text):
    """Return (class, super, counts) for one file, counting only declarations at class scope."""
    toks = tokens_of(text)
    if toks is None:
        return None
    toks = unbuilt_removed(toks)
    cls = super_name = None
    counts = {"var": 0, "function": 0, "native_function": 0, "state": 0, "struct": 0, "enum": 0, "const": 0}
    depth = 0
    state_depths = set()      # the brace depth of each `state` block body
    pending_state = False
    i = 0
    while i < len(toks):
        kind, val = toks[i]
        if kind == "op" and val == "{":
            depth += 1
            if pending_state:
                state_depths.add(depth)
                pending_state = False
            i += 1
            continue
        if kind == "op" and val == "}":
            state_depths.discard(depth)
            depth = max(0, depth - 1)
            i += 1
            continue
        if kind != "keyword":
            i += 1
            continue
        val = val.lower()   # UnrealScript is case-insensitive: `class ClientMover Extends Mover` is valid
        # Counting state functions as `functions` drops agreement 82.4% -> 79.3% (measured), so this table excludes
        # them and the count stays at class scope.
        if depth != 0:
            i += 1
            continue
        if val == "class" and cls is None:
            j = i + 1
            while j < len(toks) and toks[j][0] in ("ident", "keyword"):
                if toks[j][0] == "ident" and cls is None:
                    cls = toks[j][1]
                elif toks[j][1].lower() == "extends" and j + 1 < len(toks):
                    super_name = toks[j + 1][1]
                    break
                j += 1
            i = j
            continue
        if val == "state":
            counts["state"] += 1
            pending_state = True
            i += 1
            continue
        if val in ("struct", "enum"):
            counts[val] += 1
            i += 1
            continue
        if val == "const":
            counts["const"] += 1
            i += 1
            continue
        if val == "delegate":
            # the export table carries one property per delegate, named `__<name>__delegate`; measured: counting these
            # flips exactly 38 of the 116 var disagreements to agreement
            counts["var"] += 1
            i += 1
            continue
        if val in ("function", "event"):
            # `native function` is a separate column in the package table
            native = i > 0 and toks[i - 1][1].lower() == "native"
            counts["native_function" if native else "function"] += 1
            i += 1
            continue
        if val == "var":
            native = i > 0 and toks[i - 1][1].lower() == "native"
            # the statement runs to `;` at this depth; each comma-separated segment ends on its name
            j = i + 1
            segment = []
            while j < len(toks) and not (toks[j][0] == "op" and toks[j][1] in (";", "{")):
                if toks[j][0] == "op" and toks[j][1] == ",":
                    counts["var"] += 1 if any(t[0] == "ident" for t in segment) else 0
                    segment = []
                else:
                    segment.append(toks[j])
                j += 1
            counts["var"] += 1 if any(t[0] == "ident" for t in segment) else 0
            i = j
            continue
        i += 1
    return (cls, super_name, counts)


def load_packages():
    """package -> class -> row, from the export-derived table."""
    out = {}
    path = os.path.join(ROOT, "inventory", "classes.csv")
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            out.setdefault(row["class"], row)
    return out


def run(tree, out_dir):
    pkgs = load_packages()
    rows = []
    agree = {"class": 0, "super": 0, "var": 0, "function": 0, "native_function": 0,
             "state": 0, "struct": 0, "enum": 0, "const": 0}
    scored = 0
    seen = {}
    for dirpath, _dirs, files in os.walk(tree):
        for name in sorted(files):
            if not name.endswith(".uc"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, "r", encoding="latin-1", newline="") as fh:
                text = fh.read()
            got = declarations(text)
            rel = os.path.relpath(path, tree)
            if got is None:
                rows.append({"file": rel, "class": "", "super": "", "status": "unlexable"})
                continue
            cls, sup, counts = got
            row = {"file": rel, "class": cls or "", "super": sup or "", "status": "parsed", **counts}
            if cls and cls in seen:
                row["status"] = "duplicate"
                rows.append(row)
                continue
            if cls:
                seen[cls] = rel
            pkg = pkgs.get(cls) if cls else None
            if pkg:
                scored += 1
                row["package_super"] = pkg["super"]
                row["status"] = "scored"
                if (sup or "").lower() == (pkg["super"] or "").lower():
                    agree["super"] += 1
                agree["class"] += 1
                # `functions` is the total (natives included) and `native_functions` a subset of it, so the
                # non-native count is the difference -- pairing it against the total over-counts by every native.
                for col, key in (("function", "functions"), ("native_function", "native_functions"),
                                 ("var", "properties"), ("state", "states"), ("struct", "structs"),
                                 ("enum", "enums"), ("const", "consts")):
                    try:
                        theirs = int(pkg[key])
                        if col == "function":
                            theirs -= int(pkg.get("native_functions") or 0)
                        if theirs == counts[col]:
                            agree[col] += 1
                    except (KeyError, ValueError):
                        pass
            rows.append(row)
    fields = ["file", "class", "super", "status", "var", "function", "native_function", "state",
              "struct", "enum", "const", "package_super"]
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "uscript_decls.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})
    print("%d file(s), %d joined to a package row" % (len(rows), scored))
    if scored:
        for key in ("class", "super", "var", "function", "native_function", "state", "struct", "enum", "const"):
            print("   %-16s %5d / %5d  %5.1f%%" % (key, agree[key], scored, 100.0 * agree[key] / scored))
    print("wrote %s" % os.path.relpath(path, ROOT))
    return 0


SYNTH = """class Probe extends Actor
    config(Engine) native;
var int Count, Other;
var() string Label;
var array<int> Items;
native function bool Check(optional int N);
function DoIt() { local int T; T = 1; }
state Idle { function Tick() { } }
struct Point { var float X, Y; };
enum EFlag { A, B };
const Limit = 4;
"""


def check(_args):
    got = declarations(SYNTH)
    if got is None:
        print("FAIL uscript-decls: the synthetic corpus does not lex")
        return 1
    cls, sup, counts = got
    # `Tick` lives inside `state Idle` and is *not* counted: the measured convention excludes state functions
    want = {"var": 4, "function": 1, "native_function": 1, "state": 1, "struct": 1, "enum": 1, "const": 1}
    if cls != "Probe" or sup != "Actor":
        print("FAIL uscript-decls: header parsed as %r extends %r" % (cls, sup))
        return 1
    for key, value in want.items():
        if counts[key] != value:
            print("FAIL uscript-decls: %s counted %d, expected %d" % (key, counts[key], value))
            return 1
    print("ok uscript-decls: class=%s extends %s, %s" % (cls, sup, counts))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tree")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check(args)
    if not args.tree:
        print("FAIL uscript-decls: --tree is required (the source corpus stays under $WORK)")
        return 2
    return run(args.tree, args.out)


if __name__ == "__main__":
    sys.exit(main())
