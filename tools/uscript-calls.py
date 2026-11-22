#!/usr/bin/env python3
"""Find the calls the bytecode recorded, in the source (PLAN 3.6, front end stage 2c).

`inventory/script_calls.csv` is **19,350** call sites read out of the packages: for each one the class, the function it
sits in, and the target it resolves to. That is an oracle for expression-level parsing that needs no full grammar to be
useful — for every recorded call, is the target's name present as a call in that function's body in the shipped source?

It is a *recall* measure, deliberately: the packages record calls that resolve to a known script function, the source
contains every call including natives and engine builtins, so exact counts on both sides would not be comparable. What
this answers is the question the next stage actually needs — when the emitter produces a call, is it a call the source
spells at that place?

    python3 tools/uscript-calls.py --tree $WORK/scripts-all --calls inventory/script_calls.csv -o inventory
    python3 tools/uscript-calls.py --check        # synthetic corpus, no tree needed
"""

import argparse
import csv
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

_spec = importlib.util.spec_from_file_location("uscript_lex", os.path.join(ROOT, "tools", "uscript-lex.py"))
lexer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lexer)

_spec2 = importlib.util.spec_from_file_location("uscript_decls", os.path.join(ROOT, "tools", "uscript-decls.py"))
decls = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(decls)

SKIP = {"space", "comment", "macro", "high"}

# `if (`, `while (` and friends are keywords, never calls; the list is the language's own control words.
NOT_CALLS = {"if", "while", "for", "switch", "return", "until", "foreach", "case", "class", "state",
             "function", "event", "delegate", "operator", "struct", "enum", "new"}


# A package records an operator as a function: `==` on two MapItems compiles to `EqualEqual_MapItemMapItem`. The
# source has no such name, so the mapping is by prefix and the check is "does this body contain the symbol".
OP_SYMBOL = {
    "equalequal": "==", "notequal": "!=", "lessequal": "<=", "greaterequal": ">=",
    "less": "<", "greater": ">", "plus": "+", "add": "+", "minus": "-", "subtract": "-",
    "times": "*", "multiply": "*", "divide": "/", "percent": "%", "modulo": "%",
    "clockwisefrom": ">>", "counterclockwisefrom": "<<", "complement": "~", "concat": "$",
    "andand": "&&", "oror": "||", "xor": "^^",
}


def operator_symbol(target):
    """The symbol a recorded operator target stands for, or None when the target is an ordinary call."""
    low = target.lower()
    for prefix in sorted(OP_SYMBOL, key=len, reverse=True):
        if low.startswith(prefix + "_"):
            return OP_SYMBOL[prefix]
    return None

def tokens_of(text):
    toks, bad = lexer.lex(text)
    if bad is not None:
        return None
    out = [(k, text[s:e]) for k, s, e in toks if k not in SKIP]
    return decls.unbuilt_removed(out)


def bodies(text):
    """Map function name -> set of call names found in its body, for class-scope declarations."""
    toks = tokens_of(text)
    if toks is None:
        return None
    out = {}
    depth = 0
    state_depths = set()      # a `state Bot { function Tick() ... }` body is still this class's code
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
        if depth == 0 and kind == "keyword" and val.lower() == "state":
            pending_state = True
            i += 1
            continue
        if (depth == 0 or depth in state_depths) and kind == "keyword" and val.lower() in ("function", "event"):
            # the body follows the closing paren of the signature
            j = i + 1
            name = None
            while j < len(toks) and not (toks[j][0] == "op" and toks[j][1] == "("):
                if toks[j][0] == "ident":
                    name = toks[j][1]
                j += 1
            while j < len(toks) and not (toks[j][0] == "op" and toks[j][1] in ("{", ";")):
                j += 1
            if j >= len(toks) or toks[j][1] == ";":
                i = j + 1
                continue
            # walk the body, collecting `Name(` at any depth
            calls = set()
            ops = set()
            words = set()      # every identifier in the body: an operator can be spelled by name (`A ClockwiseFrom B`)
            brace = 0
            k = j
            while k < len(toks):
                kk, vv = toks[k]
                if kk == "op" and vv == "{":
                    brace += 1
                elif kk == "op" and vv == "}":
                    brace -= 1
                    if brace == 0:
                        break
                elif kk in ("ident", "keyword"):
                    words.add(vv.lower())
                elif kk == "op" and vv in ("==", "!=", "<=", ">=", "<", ">", "+", "-", "*", "/", "%",
                                            ">>", "<<", "~", "$", "&&", "||", "^^"):
                    ops.add(vv)
                elif kk == "op" and vv == "(" and k > 0:
                    pk, pv = toks[k - 1]
                    # a function may be *named* with a keyword: `Travel("Map")`, `Import(...)`. `int(X)` and `bool(X)`
                    # are casts, so counting them costs nothing here -- this measure is recall, and a name that is not
                    # recorded simply never matches.
                    if pk in ("ident", "keyword") and pv.lower() not in NOT_CALLS:
                        calls.add(pv)
                k += 1
            if name:
                key = name.lower()
                if key in out:
                    out[key][0] |= calls
                    out[key][1] |= ops
                    out[key][2] |= words
                else:
                    out[key] = [calls, ops, words]
            i = k + 1
            continue
        i += 1
    return out


def run(tree, calls_csv, out_dir):
    recorded = {}
    with open(calls_csv, newline="") as fh:
        for row in csv.DictReader(fh):
            # the shipped text declares in `class`, the call was compiled in `function` of that class
            recorded.setdefault((row["class"].lower(), row["function"].lower()), set()).add(row["target"].lower())

    rows = []
    seen = set()      # recall is per (class, function); a class shipping in two directories must not count twice
    found = missed = 0
    for dirpath, _dirs, files in os.walk(tree):
        for name in sorted(files):
            if not name.endswith(".uc"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, "r", encoding="latin-1", newline="") as fh:
                text = fh.read()
            cls, _sup, _c = decls.declarations(text) or (None, None, None)
            got = bodies(text)
            rel = os.path.relpath(path, tree)
            if not cls or got is None:
                rows.append({"file": rel, "class": cls or "", "function": "", "calls": "",
                             "recorded": "", "found": "", "missing": "unparsed"})
                continue
            for fn, (calls, ops, words) in sorted(got.items()):
                want = recorded.get((cls.lower(), fn), set())
                # the recorded targets are lower-cased and the source keeps its own case: comparing the raw sets
                # matched only names the source happens to spell in lower case (`initialise`), which is how a correct
                # extractor scored 0.6%
                lowered = {c.lower() for c in calls}
                hit = set()
                for target in want:
                    symbol = operator_symbol(target)
                    if symbol is not None:
                        # an operator is a call the text may never name: `==` is `EqualEqual_MapItemMapItem`, while
                        # `ClockwiseFrom` is spelled as an infix word -- satisfy either the symbol or the word
                        if symbol in ops or target.split("_")[0] in words:
                            hit.add(target)
                    elif target in lowered:
                        hit.add(target)
                if (cls.lower(), fn) not in seen:
                    seen.add((cls.lower(), fn))
                    found += len(hit)
                    missed += len(want - lowered)
                rows.append({"file": rel, "class": cls, "function": fn,
                             "calls": ";".join(sorted(calls)),
                             "ops": ";".join(sorted(ops)),
                             "recorded": len(want), "found": len(hit),
                             "missing": ";".join(sorted(want - hit))})
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "uscript_calls.csv")
    fields = ["file", "class", "function", "calls", "ops", "recorded", "found", "missing"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})
    total = found + missed
    print("%d function bod(ies) parsed; recorded calls: %d found, %d missing" % (len(rows), found, missed))
    if total:
        print("   recall: %d / %d = %.1f%%" % (found, total, 100.0 * found / total))
    print("wrote %s" % os.path.relpath(path, ROOT))
    return 0


SYNTH = """class Probe extends Actor;
function Caller()
{
    local int I;
    Foo(1, 2);
    Bar.Baz(I);
    if (I > 0) { Qux(); }
    Super.Caller();
    SomeArray[I] = new class'Actor';
}
event Touch(Actor Other) { Other.Touch(Other); }
"""


def check(_args):
    got = bodies(SYNTH)
    if got is None:
        print("FAIL uscript-calls: the synthetic corpus does not lex")
        return 1
    entry = got.get("caller")
    if entry is None:
        print("FAIL uscript-calls: the function body was not found")
        return 1
    calls, ops, _words = entry
    if ">" not in ops:
        print("FAIL uscript-calls: the `>` operator was not collected (%s)" % sorted(ops))
        return 1
    for want in ("Foo", "Baz", "Qux", "Caller"):
        if want not in calls:
            print("FAIL uscript-calls: %s missing from %s" % (want, sorted(calls)))
            return 1
    for bad in ("if", "new"):
        if bad in calls:
            print("FAIL uscript-calls: %r was treated as a call" % bad)
            return 1
    if "touch" not in got:
        print("FAIL uscript-calls: the event body was not found")
        return 1
    print("ok uscript-calls: %d bod(ies), %s" % (len(got), sorted(got["caller"])))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tree")
    ap.add_argument("--calls", default=os.path.join(ROOT, "inventory", "script_calls.csv"))
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check(args)
    if not args.tree:
        print("FAIL uscript-calls: --tree is required (the source corpus stays under $WORK)")
        return 2
    return run(args.tree, args.calls, args.out)


if __name__ == "__main__":
    sys.exit(main())
