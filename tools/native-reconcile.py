#!/usr/bin/env python3
"""Reconcile every `native()` declaration in the shipped script with the native function records (PLAN 3.9).

`inventory/natives.csv` lists 797 native functions over 92 classes, each with a token and a parameter count, and
`docs/NATIVE_VM.md` reports that 274 carry an explicit script index. The script text is the other half: a
`native function` declaration says the function has no script body, and `native(1234)` fixes its index. Nothing
has checked that the two agree class by class, which is the precondition for 3.9 (marking native bodies) and for
trusting the native list inside the compile loop.

Three things complicate the comparison, and each is handled explicitly rather than smoothed over:

* a header can wrap across lines (`native function Foo(` / `int A )`), so statements — not lines — are matched;
* `Core.Object` declares most of its natives as `operator` / `preoperator` (`native(129) static final
  preoperator bool ! ( bool A )`), which a function-shaped regex misses entirely;
* the compiler *synthesises* a function per operator signature (`Add_IntInt`, `AddEqual_VectorVector`,
  `AddAdd_PreInt`, …), so those records have no declaration to match. They are counted apart, not as mismatches.

The class-by-class counts answer "do the two halves agree"; the second question is per record, and it is the one
UNKNOWNS U1 and U3 left open: **every record that carries a token has to be explained by a declaration the class
itself made**, either by spelling that index out or by being one signature a single `operator` statement expands
into. `token_basis` answers it per record and `--tokens` writes `inventory/native_tokens.csv`, so "the record's
token is authoritative" becomes a statement with a basis per row rather than a rule of thumb.

    python3 tools/native-reconcile.py DUMP/System -o inventory
    python3 tools/native-reconcile.py DUMP/System --class Object --walk    # one line per disagreement
    python3 tools/native-reconcile.py --selftest                           # the classifier, no dump

Needs the dump (the text lives inside the packages), so it is a nightly stage rather than a `make ci` check; the
classifier and the operator-name rule are covered by `--selftest`, which is in `make ci`.
"""
import argparse
import collections
import csv
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from ue2pkg import Package  # noqa: E402

NATIVE_FN = re.compile(r"\bnative\b(?:\s*\(\s*(\d+)\s*\))?.*?\bfunction\s+(?:[\w<>\[\]]+\s+)?(\w+)\s*\(",
                       re.I | re.S)
NATIVE_OP = re.compile(r"\bnative\b(?:\s*\(\s*(\d+)\s*\))?.*?\b(?:pre|post)?operator\b\s*"
                       r"(?:\(\s*(\d+)\s*\))?\s*[\w<>\[\]]+\s*([+\-*/%<>=!&|^@~]{1,2})", re.I | re.S)
CLASS_NATIVE = re.compile(r"^\s*class\s+\w+.*\bnative\s*;", re.I | re.M)

# Compiler-generated operator records have no `native function` declaration behind them.
OPERATOR_RECORD = re.compile(
    r"^(?:Add|Subtract|Multiply|Divide|Percent|And|Or|Xor|Not|Concat|At|ClockwiseFrom|LeftShift|RightShift|"
    r"Less|Greater|Equal|NotEqual|Complement|Negate|Dot|Cross|Round)"
    r"(?:Add|Subtract|Multiply|Divide|Percent|And|Or|Xor|Less|Greater|Equal|Concat|At|Shift)*"
    r"(?:_[A-Za-z0-9]+)*$")


def is_operator_record(name):
    return bool(OPERATOR_RECORD.match(name))


TOKEN_COLS = ["package", "class", "function", "native_token", "basis"]

# What explains a record's token. `text-index` is the class spelling it out; `operator-expansion` is one
# `operator` statement becoming several typed records; `unindexed-declaration` would be the compiler assigning an
# index to a declaration that spelled none (U3's hypothesis, 0 of 274 on this build); the last two are the failures
# and are meant to be read, not counted quietly.
TOKEN_BASIS = ("text-index", "operator-expansion", "unindexed-declaration", "index-mismatch", "unresolved")


def token_basis(decls, records):
    """[(function, token, basis)] for every record of one class that carries a token, sorted by name.

    A record is checked against the class's own declarations first: the same name spelling the same index is
    `text-index`, the same name spelling a different one is `index-mismatch` (the text and the record disagree,
    which is a real defect), the same name with no index at all is `unindexed-declaration`. Only when the class's
    text never names the record does the operator rule apply -- and if it does not apply either, the record is
    `unresolved`, which on this build is one row in `UPreview`.
    """
    spelled = collections.defaultdict(set)
    unindexed = set()
    for name, index, _kind in decls:
        if not name:
            continue
        if index:
            spelled[name.lower()].add(str(int(index)))
        else:
            unindexed.add(name.lower())
    out = []
    for name in sorted(records):
        token = (records[name].get("native_token") or "0").strip()
        if token in ("", "0"):
            continue
        low = name.lower()
        if low in spelled:
            basis = "text-index" if token in spelled[low] else "index-mismatch"
        elif low in unindexed:
            basis = "unindexed-declaration"
        elif is_operator_record(name):
            basis = "operator-expansion"
        else:
            basis = "unresolved"
        out.append((name, token, basis))
    return out


def selftest():
    """The classifier and the operator rule, on fixtures: no dump, no package."""
    cases = 0
    text = (
        " class Object native ;\r\n"
        " native(113) final function GotoState( optional name NewState );\r\n"
        " native final function Unindexed( int A );\r\n"
        " native(150) static final operator bool < ( int A, int B );\r\n"
        " native(151) final function Drifted( int A );\r\n"
    )
    recs = {
        "GotoState": {"native_token": "113"},     # spelled out
        "Unindexed": {"native_token": "9"},       # the class names it, spells no index
        "Add_IntInt": {"native_token": "146"},    # an operator signature, nothing names it
        "Drifted": {"native_token": "152"},       # the two halves disagree
        "Mystery": {"native_token": "77"},        # nothing explains it
        "Zero": {"native_token": "0"},            # no token: not audited
    }
    got = {name: basis for name, _token, basis in token_basis(declarations(text), recs)}
    want = {"GotoState": "text-index", "Unindexed": "unindexed-declaration",
            "Add_IntInt": "operator-expansion", "Drifted": "index-mismatch", "Mystery": "unresolved"}
    assert got == want, "classified %r, expected %r" % (got, want)
    cases += 1
    assert set(want.values()) | {"text-index"} <= set(TOKEN_BASIS), "a basis is missing from TOKEN_BASIS"
    cases += 1
    # The operator statement's own index is parsed, so an operator record spelled out by hand still reads as text.
    assert any(k == "operator" and i == "150" for _n, i, k in declarations(text)), "operator index lost"
    cases += 1
    # The name rule is what distinguishes a synthesised signature from an unexplained record.
    for name, expect in (("Add_IntInt", True), ("Concat_StrStr", True), ("EqualEqual_ObjectObject", True),
                         ("GotoState", False), ("Created", False), ("P90Prototype", False)):
        assert is_operator_record(name) is expect, "%s: operator rule answered %r" % (name, not expect)
        cases += 1
    print("ok   native-reconcile: %d cases (five bases, spelled operator index, the operator-name rule)" % cases)
    return 0



def load_script_text():
    spec = importlib.util.spec_from_file_location("pkg_script_text", os.path.join(HERE, "pkg-script-text.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def declarations(text):
    """[(name, index, kind)] with kind in function / operator / other, one entry per declaration statement."""
    body = strip_comments(text).replace("\r", " ").replace("\n", " ")
    out = []
    for statement in body.split(";"):
        if not re.search(r"\bnative\b", statement, re.I):
            continue
        if not re.search(r"\b(function|operator)\b", statement, re.I):
            # `var native const int Foo` / `struct native Foo`: native *data*, not a native body.
            continue
        op = NATIVE_OP.search(statement)
        if op:
            out.append((op.group(3), op.group(1), "operator"))
            continue
        fn = NATIVE_FN.search(statement)
        if fn:
            out.append((fn.group(2), fn.group(1), "function"))
            continue
        out.append(("", None, "other"))
    return out


def csv_natives(path=None):
    path = path or os.path.join(ROOT, "inventory", "natives.csv")
    out = collections.defaultdict(dict)
    if os.path.isfile(path):
        with open(path, newline="") as fh:
            for r in csv.DictReader(fh):
                out[(r["package"], r["class"])][r["function"]] = r
    return out


COLS = ["package", "class", "class_is_native", "text_functions", "text_indexed", "operators",
        "operator_records", "csv_records", "matched", "text_only", "csv_only", "token_mismatch"]


def run(system_dir, outdir, only=None, walk=False):
    pst = load_script_text()
    known = csv_natives()
    rows = []
    token_rows = []
    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        pkg_name = fname[:-2]
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            cls = pkg.name(exp["name"])
            if only and cls != only:
                continue
            text = pst.script_text(pkg, exp)
            if not text:
                continue
            decls = declarations(text)
            names = collections.Counter(n for n, _i, k in decls if n and k == "function")
            operators = sum(1 for _n, _i, k in decls if k == "operator")
            records_all = known.get((pkg_name, cls), {})
            records = {k: v for k, v in records_all.items() if not is_operator_record(k)}
            operator_records = len(records_all) - len(records)
            # Names are compared case-insensitively: the package's name table spells some natives differently
            # from the source (`IsValid` in the records, `isValid` in the text), which is not a disagreement.
            by_lower = {k.lower(): k for k in records}
            lower_names = {n.lower() for n in names}
            matched = sorted(by_lower[n] for n in lower_names & set(by_lower))
            text_only = sorted(n for n in names if n.lower() not in by_lower)
            csv_only = sorted(k for low, k in by_lower.items() if low not in lower_names)
            indexed = [i for _n, i, k in decls if i]
            token_mismatch = []
            for name, index, kind in decls:
                if not index or kind != "function" or name.lower() not in by_lower:
                    continue
                token = (records[by_lower[name.lower()]].get("native_token") or "").strip()
                # `native_token` is decimal in natives.csv; 523 of the 797 records carry 0 (UNKNOWNS U1).
                if token and token != "0" and int(token) != int(index):
                    token_mismatch.append((name, index, token))
            if not names and not records and not operators:
                continue
            for name, token, basis in token_basis(decls, records_all):
                token_rows.append({"package": pkg_name, "class": cls, "function": name,
                                   "native_token": token, "basis": basis})
            rows.append({"package": pkg_name, "class": cls,
                         "class_is_native": int(bool(CLASS_NATIVE.search(text))),
                         "text_functions": len(names), "text_indexed": len(indexed), "operators": operators,
                         "operator_records": operator_records, "csv_records": len(records),
                         "matched": len(matched), "text_only": len(text_only), "csv_only": len(csv_only),
                         "token_mismatch": len(token_mismatch)})
            if walk:
                print("%-12s %-26s text %2d funcs (%d indexed) + %2d ops  csv %2d  matched %2d  "
                      "text-only %2d  csv-only %2d  tokens %d"
                      % (pkg_name, cls, len(names), len(indexed), operators, len(records), len(matched),
                         len(text_only), len(csv_only), len(token_mismatch)))
                for name in text_only:
                    print("      text-only  %s" % name)
                for name in csv_only:
                    print("      csv-only   %s" % name)
                for name, a, b in token_mismatch:
                    print("      token      %s text=%s csv=%s" % (name, a, b))
                for name, tok, basis in token_basis(decls, records_all):
                    if basis not in ("text-index", "operator-expansion"):
                        print("      basis      %s tok=%s %s" % (name, tok, basis))
    if outdir and not only:
        os.makedirs(outdir, exist_ok=True)
        path = os.path.join(outdir, "native_reconcile.csv")
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLS, lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print("wrote %s" % path)
        path = os.path.join(outdir, "native_tokens.csv")
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=TOKEN_COLS, lineterminator="\n")
            w.writeheader()
            for r in token_rows:
                w.writerow(r)
        print("wrote %s" % path)
    total = collections.Counter()
    for r in rows:
        for k in COLS[3:]:
            total[k] += r[k]
    print("%d classes: %d native functions in the text (%d indexed) and %d operator declarations expanded into "
          "%d records; %d function records, matched %d, text-only %d, csv-only %d, token mismatches %d"
          % (len(rows), total["text_functions"], total["text_indexed"], total["operators"],
             total["operator_records"], total["csv_records"], total["matched"], total["text_only"],
             total["csv_only"], total["token_mismatch"]))
    basis = collections.Counter(r["basis"] for r in token_rows)
    print("%d records carry a token: %s"
          % (len(token_rows), ", ".join("%s %d" % (b, basis[b]) for b in TOKEN_BASIS if basis[b])))
    for r in token_rows:
        if r["basis"] in ("unresolved", "index-mismatch", "unindexed-declaration"):
            print("  %-10s %-24s %-22s token %s" % (r["basis"], r["class"], r["function"], r["native_token"]))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", nargs="?", help="the dump's System directory")
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--class", dest="cls")
    ap.add_argument("--walk", action="store_true", help="one line per class plus the disagreeing names")
    ap.add_argument("--selftest", action="store_true", help="the token classifier on fixtures; needs no dump")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.path:
        ap.error("a System directory is required unless --selftest is given")
    run(args.path, args.out, only=args.cls, walk=args.walk)
    return 0


if __name__ == "__main__":
    sys.exit(main())
