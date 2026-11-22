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

`inventory/uscript_decls_residue.csv` names every disagreement (class, column, ours, theirs, file) so a residue is read
one row at a time instead of inferred from a percentage. The `struct` column is **void**: the packages export no
`UScriptStruct` at all -- `structs` is 0 in every one of the 2,319 rows -- so the count is reported and never scored.

Three lexer facts the residue taught: a *name* can be a keyword (`var String Name;` -- `Name` is the `name` type),
a `var` statement can put its names *after* an inline enum body (`var enum EMode { A, B } UClampMode, VClampMode;`),
and `state` is used as an identifier (a native parameter `out byte state`, a member `Segment.state`) as well as a
declaration.
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


def is_native(toks, i):
    """True when the declaration at `i` is native.

    `native(881) final simulated static function ...` puts six tokens between the two words, and the tokens between
    include the `native(<index>)` group, so the check walks back to the statement boundary rather than looking at `i-1`.
    """
    j = i - 1
    while j >= 0 and i - j <= 14:
        kind, val = toks[j]
        if kind == "op" and val in (";", "{", "}"):
            return False
        if val.lower() == "native":
            return True
        j -= 1
    return False


def is_state_decl(toks, i):
    """True when the `state` at `i` opens a state block rather than naming something.

    `state` is a keyword, and this corpus also uses it as an identifier: a native declaration's parameter
    (`out byte Key, out byte state`) and a member (`Segment.state`) both lex as keywords, and 19 GUI classes read as
    one state they do not have. A state header is `[auto] state [()] Name`, so the token after the optional empty
    parameter list decides, and a `.`/`,`/`(`/identifier in front rules the read out.
    """
    if i:
        kind, val = toks[i - 1]
        if kind == "ident" or (kind == "op" and val in (".", ",", "(")):
            return False
    j = i + 1
    if j < len(toks) and toks[j] == ("op", "("):
        if j + 1 >= len(toks) or toks[j + 1] != ("op", ")"):
            return False
        j += 2
    return j < len(toks) and toks[j][0] in ("ident", "keyword")   # `state Default`, `state Protected`


def var_declaration(toks, i, counts):
    """Count one `var` statement's declared names; returns the index of its `;`.

    Two shapes defeat a walk that stops at the first `;`/`{`. `var enum EMode { A, B } UClampMode, VClampMode;`
    declares its names *after* the brace group, and the inline enum is a declaration of its own (the package carries
    one `UEnum` per class). And a name can be a keyword: `var String Name;` names the property `Name`, which the
    lexer calls the `name` type.
    """
    j = i + 1
    depth = 0
    segments = []
    segment = []
    while j < len(toks):
        kind, val = toks[j]
        if kind == "op" and val == "{":
            depth += 1
            segment = []        # the enum/struct header declares no name; the names follow its close
        elif kind == "op" and val == "}":
            depth = max(0, depth - 1)
            if depth == 0:
                segment = []
        elif depth == 0 and kind == "op" and val == ";":
            break
        elif depth == 0 and kind == "op" and val == ",":
            segments.append(segment)
            segment = []
        elif depth == 0:
            if kind == "keyword" and val.lower() in ("enum", "struct"):
                counts[val.lower()] += 1
            segment.append((kind, val))
        j += 1
    segments.append(segment)
    for seg in segments:
        # a segment declares a name when its last *word* is one -- ident or keyword, since `Name` is a type too,
        # and an array suffix (`Ordered[32]`) sits behind the name it belongs to
        k = len(seg) - 1
        while k >= 0 and seg[k] == ("op", "]"):
            depth = 0
            while k >= 0:
                if seg[k] == ("op", "]"):
                    depth += 1
                elif seg[k] == ("op", "["):
                    depth -= 1
                    if depth == 0:
                        break
                k -= 1
            k -= 1
        if k >= 0 and seg[k][0] in ("ident", "keyword"):
            counts["var"] += 1
    return j


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
            if is_state_decl(toks, i):
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
            counts["native_function" if is_native(toks, i) else "function"] += 1
            i += 1
            continue
        if val in ("operator", "preoperator", "postoperator"):
            # `native(129) static final preoperator bool !( bool A );` never says `function`, but the package carries
            # one UFunction per operator: Object's 103 and Actor's 4 are the whole difference in their native column.
            if is_native(toks, i):
                counts["native_function"] += 1
            i += 1
            continue
        if val == "var":
            i = var_declaration(toks, i, counts)
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
    counted = {"class": 0, "super": 0, "var": 0, "function": 0, "native_function": 0,
               "state": 0, "enum": 0, "const": 0}     # `struct` is void: no package exports one
    residue = []
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
                counted["class"] += 1
                counted["super"] += 1
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
                    except (KeyError, ValueError):
                        continue
                    if col == "function":
                        theirs -= int(pkg.get("native_functions") or 0)
                    if col == "struct":
                        # void: `structs` is 0 in all 2,319 package rows, so scoring this column scores two zeroes
                        if counts[col]:
                            residue.append((col, cls, counts[col], theirs, rel))
                        continue
                    counted[col] += 1
                    if theirs == counts[col]:
                        agree[col] += 1
                    else:
                        residue.append((col, cls, counts[col], theirs, rel))
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
        for key in ("class", "super", "var", "function", "native_function", "state", "enum", "const"):
            print("   %-16s %5d / %5d  %5.1f%%" % (key, agree[key], counted[key],
                                                   100.0 * agree[key] / counted[key] if counted[key] else 0.0))
        print("   %-16s %s" % ("struct", "void -- the packages export none (0 in every package row)"))
    res_path = os.path.join(out_dir, "uscript_decls_residue.csv")
    with open(res_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["column", "class", "ours", "theirs", "file"])
        for col, cls, ours, theirs, rel in residue:
            w.writerow([col, cls, ours, theirs, rel])
    by_col = {}
    for col, _cls, _ours, _theirs, _rel in residue:
        by_col[col] = by_col.get(col, 0) + 1
    if residue:
        print("residue: %s" % ", ".join("%s %d" % (c, by_col[c]) for c in
                                        ("var", "function", "native_function", "state", "enum", "const", "struct")
                                        if c in by_col))
    print("wrote %s" % os.path.relpath(path, ROOT))
    print("wrote %s" % os.path.relpath(res_path, ROOT))
    return 0


SYNTH = """class Probe extends Actor
    config(Engine) native;
var int Count, Other;
var() string Label;
var array<int> Items;
var String Name;
var(Texture) enum ETexClampMode { TC_Wrap, TC_Clamp } UClampMode, VClampMode;
native function bool Check(optional int N);
native(129) static final preoperator bool !( bool A );
function DoIt( out byte state ) { local int T; T = 1; }
state Idle { function Tick() { } }
state() Toggle { }
state Protected { }
struct Point { var float X, Y; };
enum EFlag { A, B };
const Limit = 4;
function Hidden() { const Inner = 1; }
"""


def check(_args):
    got = declarations(SYNTH)
    if got is None:
        print("FAIL uscript-decls: the synthetic corpus does not lex")
        return 1
    cls, sup, counts = got
    # `Tick` lives inside `state Idle` and is *not* counted: the measured convention excludes state functions.
    # `Name` is a property whose name is a type word; the two clamp modes are named after an inline enum body; the
    # `state` parameter of `DoIt` is an identifier, not a state; the operator is a native function with no `function`;
    # `Inner` is a const inside a body and the package stores none, so class scope only (measured on CacheManager's 53).
    want = {"var": 7, "function": 2, "native_function": 2, "state": 3, "struct": 1, "enum": 2, "const": 1}
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
