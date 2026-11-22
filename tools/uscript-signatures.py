#!/usr/bin/env python3
"""Parse function signatures and score them against the packages (PLAN 3.6, front end stage 2b).

Stage 2a proved the declarations by counting them. A signature is finer: a name, a return type and an ordered parameter
list, and `inventory/natives.csv` already carries one of those per native — **797** rows of class, function and
`parameter_count`, read out of the package's own function records rather than out of the text. So the parser can be
scored on two axes at once: did it find the declaration the package says exists, and did it count the same number of
parameters?

    python3 tools/uscript-signatures.py --tree $WORK/scripts-all -o inventory
    python3 tools/uscript-signatures.py --check       # synthetic corpus, no tree needed

`inventory/uscript_signatures.csv` is the per-declaration table. The summary reports joined declarations, name
agreement, and parameter-count agreement, and lists a few mismatches by name so the next cause can be sampled rather
than guessed.
"""

import argparse
import csv
import importlib.util
import os
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


def tokens_of(text):
    toks, bad = lexer.lex(text)
    if bad is not None:
        return None
    out = [(k, v) for k, s, e in toks for v in [text[s:e]] if k not in SKIP]
    return decls.unbuilt_removed(out)


def signatures(text):
    """Every function/event declaration with its parameter list, at class scope."""
    toks = tokens_of(text)
    if toks is None:
        return None
    out = []
    depth = 0
    i = 0
    while i < len(toks):
        kind, val = toks[i]
        if kind == "op" and val == "{":
            depth += 1
            i += 1
            continue
        if kind == "op" and val == "}":
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth != 0 or kind != "keyword":
            i += 1
            continue
        low = val.lower()
        if low not in ("function", "event"):
            i += 1
            continue
        # name is the identifier before the opening paren; the return type (if any) sits between the two keywords
        j = i + 1
        name = None
        idents = []
        while j < len(toks) and not (toks[j][0] == "op" and toks[j][1] in ("(", ";")):
            # a return type is usually a keyword (`bool`, `int`, `string`), so words are both kinds
            if toks[j][0] in ("ident", "keyword"):
                name = toks[j][1]
                idents.append(toks[j][1])
            j += 1
        # `function bool Check(...)` declares a return type, `function Plain(...)` does not: the package's
        # `parameter_count` counts the return value as a parameter, which is why every miss was off by one.
        returns = len(idents) >= 2
        if j >= len(toks) or toks[j][1] != "(":
            i = j
            continue
        params = []
        seg = []
        depth_p = 0
        j += 1
        while j < len(toks):
            k2, v2 = toks[j]
            if k2 == "op" and v2 in ("(", "<"):
                depth_p += 1
            elif k2 == "op" and v2 in (")", ">"):
                if depth_p == 0 and v2 == ")":
                    break
                depth_p = max(0, depth_p - 1)
            elif k2 == "op" and v2 == "," and depth_p == 0:
                params.append(seg)
                seg = []
                j += 1
                continue
            seg.append(toks[j])
            j += 1
        if seg:
            params.append(seg)
        names = []
        for seg in params:
            if not seg:
                continue
            # the parameter name is the last bare identifier before an optional `= default`
            cut = seg
            for idx, (k, v) in enumerate(seg):
                if k == "op" and v == "=":
                    cut = seg[:idx]
                    break
            # a parameter can be *named* with a type word: `StrLen( coerce string String, ... )` -- the language is
            # case-insensitive, so `String` lexes as a keyword and an idents-only scan loses the parameter entirely
            words = [v for k, v in cut if k in ("ident", "keyword")]
            names.append(words[-1] if words else "")
        out.append({"function": name or "", "params": len(names), "returns": int(returns),
                    "slots": len(names) + int(returns),
                    "param_names": ";".join(names), "native": decls.is_native(toks, i)})
        i = j
    return out


def natives():
    out = {}
    path = os.path.join(ROOT, "inventory", "natives.csv")
    if not os.path.exists(path):
        return out
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            out[(row["class"].lower(), row["function"].lower())] = row
    return out


def run(tree, out_dir):
    known = natives()
    rows = []
    joined = name_ok = count_ok = 0
    misses = []
    for dirpath, _dirs, files in os.walk(tree):
        for name in sorted(files):
            if not name.endswith(".uc"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, "r", encoding="latin-1", newline="") as fh:
                text = fh.read()
            got = signatures(text)
            cls = None
            if got is not None:
                cls, _sup, _counts = decls.declarations(text)
            rel = os.path.relpath(path, tree)
            for sig in got or []:
                row = {"file": rel, "class": cls or "", **sig, "native_count": "", "count_match": ""}
                key = ((cls or "").lower(), sig["function"].lower())
                if key in known:
                    joined += 1
                    name_ok += 1
                    row["native_count"] = known[key]["parameter_count"]
                    if int(known[key]["parameter_count"]) == sig["slots"]:
                        count_ok += 1
                        row["count_match"] = "yes"
                    else:
                        row["count_match"] = "no"
                        if len(misses) < 12:
                            misses.append((cls, sig["function"], sig["slots"], known[key]["parameter_count"]))
                rows.append(row)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "uscript_signatures.csv")
    fields = ["file", "class", "function", "params", "returns", "slots", "param_names", "native",
              "native_count", "count_match"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})
    print("%d declaration(s) parsed, %d joined to a package native record" % (len(rows), joined))
    if joined:
        print("   parameter count agrees: %d / %d  %5.1f%%" % (count_ok, joined, 100.0 * count_ok / joined))
        for cls, fn, mine, theirs in misses:
            print("   %-24s %-32s mine=%-3s package=%s" % (cls, fn, mine, theirs))
    print("wrote %s" % os.path.relpath(path, ROOT))
    return 0


SYNTH = """class Probe extends Actor
    native;
native(881) final simulated static function WeaponRecord GetWeaponRecord( coerce string ClassName );
native function bool Check(optional int N, out array<int> Items);
event Touch(Actor Other, name EventName);
function Plain() { local int T; T = 1; }
"""


def check(_args):
    got = signatures(SYNTH)
    if got is None:
        print("FAIL uscript-signatures: the synthetic corpus does not lex")
        return 1
    by = {s["function"]: s for s in got}
    want = {"GetWeaponRecord": (1, True), "Check": (2, True), "Touch": (2, False), "Plain": (0, False)}
    slots = {"GetWeaponRecord": 2, "Check": 3, "Touch": 2, "Plain": 0}
    for fn, (count, native) in want.items():
        if fn not in by:
            print("FAIL uscript-signatures: %s not found" % fn)
            return 1
        if by[fn]["params"] != count:
            print("FAIL uscript-signatures: %s counted %d params, expected %d"
                  % (fn, by[fn]["params"], count))
            return 1
        if by[fn]["native"] != native:
            print("FAIL uscript-signatures: %s native=%s, expected %s" % (fn, by[fn]["native"], native))
            return 1
    for fn, want_slots in slots.items():
        if by[fn]["slots"] != want_slots:
            print("FAIL uscript-signatures: %s has %d slots, expected %d" % (fn, by[fn]["slots"], want_slots))
            return 1
    if by["Touch"]["returns"] != 0:
        print("FAIL uscript-signatures: an event should carry no return slot")
        return 1
    if by["Check"]["param_names"] != "N;Items":
        print("FAIL uscript-signatures: parameter names read %r" % by["Check"]["param_names"])
        return 1
    print("ok uscript-signatures: %d declaration(s), natives and parameter names agree" % len(by))
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
        print("FAIL uscript-signatures: --tree is required (the source corpus stays under $WORK)")
        return 2
    return run(args.tree, args.out)


if __name__ == "__main__":
    sys.exit(main())
