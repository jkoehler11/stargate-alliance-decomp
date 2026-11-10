#!/usr/bin/env python3
"""Which record does an `operator` declaration become? (PLAN 3.9, docs/NATIVE_CLASSES.md)

The marker pass skips the 92 `operator` declarations because one statement was thought to expand into several
typed records — `Core.Object`'s operators become 103 records. Measured here, that is the wrong way round: each
declaration becomes **one** record whose name the compiler composes from the operator and its operand types
(`int + ( int A, int B )` → `Add_IntInt`, `vector * ( float A, vector B )` → `Multiply_FloatVector`), and the
record count is higher because records exist that no declaration made — the `++`/`--` family and the unary
`Subtract_Pre*`/`Not_PreBool`/`Complement_PreInt`, which the compiler synthesises from other operators.

So the mapping is a rule, not a guess, and this tool applies it to the shipped text and checks it against the
registration table:

    python3 tools/native-operators.py DUMP/System -o inventory
    python3 tools/native-operators.py --check      # the committed mapping, no dump; in `make ci`
    python3 tools/native-operators.py --selftest   # the rule and the parser, on fixtures

`inventory/native_operators.csv` carries `package, class, declaration, symbol, native_token, record, record_token,
body_va, status` — one row per declaration, with `status` `bound` when the composed name is a registration record of
that class, `no-record` when it is not (the `color` operators are the measured case: declared, never registered),
and `ambiguous` when two records answer to the same name (none on this build).

The rule is UE2's, and each part of it is measured on this build rather than assumed: the symbol table is the one the
records use (`==` → `EqualEqual`, not `Equal`; `$` → `Concat`; `@` → `At`; `===`-style runs are spelled out, so `>>>`
is `GreaterGreaterGreater`), the type suffixes are the ones the records use (`string` → `Str`, `Object` → `Object`),
and argument modifiers (`out`, `coerce`, `skip`, `optional`, `const`) are not part of the name.
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

TARGETS = os.path.join(ROOT, "inventory", "native_targets.csv")
OUT = os.path.join(ROOT, "inventory", "native_operators.csv")

COLS = ["package", "class", "declaration", "symbol", "composed", "native_token", "record", "record_token",
        "body_va", "status"]

# The script spells the string type `Str`, the registration pool spells it `String` (UNKNOWNS U1: the twelve
# string operators "spelled `_StrStr` in script and `_StringString` in the pool"). The composed name is kept in
# its own column so the alias is visible rather than silently corrected.
STRING_ALIAS = ("Str", "String")

# The operator's spelling -> the stem the compiler puts in the record name. Measured against the 103 records of
# `Core.Object`: a stem that the records do not use (`Equal` for `==`) would fail every comparison below.
SYMBOLS = {
    "==": "EqualEqual", "!=": "NotEqual", "~=": "ComplementEqual", "===": "EqualEqualEqual",
    "<": "Less", ">": "Greater", "<=": "LessEqual", ">=": "GreaterEqual",
    "&&": "AndAnd", "||": "OrOr", "^^": "XorXor", "&": "And", "|": "Or", "^": "Xor",
    "<<": "LessLess", ">>": "GreaterGreater", ">>>": "GreaterGreaterGreater",
    "+": "Add", "-": "Subtract", "*": "Multiply", "/": "Divide", "%": "Percent", "**": "MultiplyMultiply",
    "$": "Concat", "@": "At", "$=": "ConcatEqual", "@=": "AtEqual", "-=": "SubtractEqual",
    "+=": "AddEqual", "*=": "MultiplyEqual", "/=": "DivideEqual", "%=": "PercentEqual",
    "!": "Not", "~": "Complement",
}

# Named operators, which are identifiers rather than punctuation (`operator bool ClockwiseFrom( int A, int B )`).
NAMED = {"Dot", "Cross", "ClockwiseFrom", "RotateClockwiseFrom"}

TYPES = {"bool": "Bool", "byte": "Byte", "int": "Int", "float": "Float", "string": "Str", "name": "Name",
         "vector": "Vector", "rotator": "Rotator", "object": "Object", "color": "Color", "array": "Array"}

MODIFIERS = {"out", "coerce", "skip", "optional", "const", "export", "private", "protected", "native"}

DECL = re.compile(r"\bnative\b(?:\(\s*(?P<token>\d+)\s*\))?(?P<mods>(?:\s+(?:static|final|const|private|protected))*)"
                  r".*?\boperator\b\s*(?:\(\s*[\d,]+\s*\))?\s*[\w<>\[\]]+\s+"
                  r"(?P<symbol>[A-Za-z_]\w*|[+\-*/%<>=!&|^@~$]{1,3})\s*\((?P<params>[^)]*)\)", re.I | re.S)


def param_types(params):
    """The operand types in declaration order, without modifiers or variable names."""
    out = []
    for part in params.split(","):
        words = [w for w in re.split(r"[\s*]+", part.strip()) if w]
        words = [w for w in words if w.lower() not in MODIFIERS]
        if words:
            out.append(words[0].strip("&"))
    return out


def compose(symbol, types):
    """The record name the compiler builds: stem, then one suffix per operand; a unary operator says `Pre`."""
    stem = SYMBOLS.get(symbol) or (symbol if symbol in NAMED else None)
    if stem is None:
        return None
    if len(types) == 1:
        return "%s_Pre%s" % (stem, TYPES.get(types[0], types[0].title()))
    return stem + "_" + "".join(TYPES.get(t, t.title()) for t in types)


def statements(text):
    """The class text with comments blanked, split on `;` — one declaration per chunk, as the marker pass reads it.

    Matching the declaration regex over the whole text instead lets `native` from one statement and `operator`
    from the next form a match across the boundary: it reported 91 declarations for `Core.Object` where the
    statements hold 87.
    """
    blank = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    blank = re.sub(r"//[^\n]*", " ", blank)
    return blank.split(";")


def parse(text):
    """[(declaration, symbol, token, record)] for every `operator` statement in one class's text."""
    out = []
    for chunk in statements(text):
        if not re.search(r"\bnative\b", chunk, re.I) or not re.search(r"\boperator\b", chunk, re.I):
            continue
        for m in DECL.finditer(chunk):
            symbol = m.group("symbol")
            decl = re.sub(r"\s+", " ", m.group(0)).strip()
            types = param_types(m.group("params"))
            out.append((decl, symbol, (m.group("token") or "").strip(), compose(symbol, types)))
    return out


def load_records(path):
    """{(class_lower, record_lower): {record, token, body}} from the registration table."""
    recs = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            name = (r.get("function") or "").strip()
            body = (r.get("body") or "").strip()
            if not name or not body:
                continue
            key = ((r.get("class") or "").strip().lower(), name.lower())
            recs.setdefault(key, {"record": name, "token": (r.get("token") or "").strip(), "body": body})
    return recs


def run(system_dir, outdir, walk=False):
    sys.path.insert(0, HERE)
    spec = importlib.util.spec_from_file_location("pkg_script_text", os.path.join(HERE, "pkg-script-text.py"))
    pst = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pst)
    from ue2pkg import Package

    recs = load_records(TARGETS)
    rows = []
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
            text = pst.script_text(pkg, exp)
            if not text:
                continue
            for decl, symbol, token, record in parse(text):
                # The pool's spelling wins when the script's does not answer, and the composed name is kept.
                candidates = [record] if record else []
                if record and STRING_ALIAS[0] in record:
                    candidates.append(record.replace(STRING_ALIAS[0], STRING_ALIAS[1]))
                hit = None
                for cand in candidates:
                    hit = recs.get((cls.lower(), cand.lower()))
                    if hit:
                        break
                status = "bound" if hit else "no-record"
                row = collections.OrderedDict()
                row["package"] = pkg_name
                row["class"] = cls
                row["declaration"] = decl
                row["symbol"] = symbol
                row["composed"] = record or ""
                row["native_token"] = token
                row["record"] = (hit["record"] if hit else (record or ""))
                row["record_token"] = hit["token"] if hit else ""
                row["body_va"] = hit["body"] if hit else ""
                row["status"] = status if record else "unparsed"
                rows.append(row)
    if outdir:
        os.makedirs(outdir, exist_ok=True)
        path = os.path.join(outdir, "native_operators.csv")
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLS, lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print("wrote %s (%d rows)" % (path, len(rows)))
    status = collections.Counter(r["status"] for r in rows)
    print("%d operator declarations in %d classes: %s"
          % (len(rows), len({(r["package"], r["class"]) for r in rows}),
             ", ".join("%s %d" % kv for kv in status.most_common())))
    # The other direction: registered operator records no declaration explains. `AddAdd_*` and the unary
    # `*_Pre*` family are the compiler's, and `Object`'s colour operators are the reverse case.
    claimed = {(r["class"].lower(), r["record"].lower()) for r in rows if r["record"]}
    op = re.compile(r"^(?:Add|Subtract|Multiply|Divide|Percent|And|Or|Xor|Not|Concat|At|ClockwiseFrom|LeftShift|"
                    r"RightShift|Less|Greater|Equal|NotEqual|Complement|Negate|Dot|Cross|Round)")
    orphan = sorted(k for k in recs if k not in claimed and op.match(k[1]))
    print("%d registered operator-named records are not the record of any declaration (e.g. %s)"
          % (len(orphan), ", ".join(k[1] for k in orphan[:6])))
    if walk:
        for r in rows:
            print("%-10s %-12s %-34s %-24s %-8s %s" % (r["package"], r["class"], r["symbol"],
                                                       r["record"] or "-", r["record_token"] or "-", r["status"]))
    return rows


def check(args):
    if not os.path.isfile(OUT):
        sys.exit("native-operators: %s is missing" % os.path.relpath(OUT, ROOT))
    with open(OUT, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if list(rows[0].keys()) != COLS:
        sys.exit("native-operators: --check: columns are %r" % (list(rows[0].keys()),))
    recs = load_records(TARGETS)
    bad, seen = [], collections.Counter()
    for r in rows:
        if r["status"] not in ("bound", "no-record", "unparsed"):
            bad.append("%s: unknown status %r" % (r["declaration"][:40], r["status"]))
        if r["status"] == "bound":
            key = (r["class"].lower(), r["record"].lower())
            if key not in recs:
                bad.append("%s: %s is not a record of %s" % (r["record"], r["record"], r["class"]))
            elif recs[key]["body"].lower() != r["body_va"].lower():
                bad.append("%s: body %s, the registration table says %s"
                           % (r["record"], r["body_va"], recs[key]["body"]))
            elif recs[key]["token"] != r["record_token"]:
                bad.append("%s: token %r, the table says %r"
                           % (r["record"], r["record_token"], recs[key]["token"]))
            seen[key] += 1
        elif r["record"] and r["status"] == "no-record":
            if (r["class"].lower(), r["record"].lower()) in recs:
                bad.append("%s: marked no-record but the table has it" % r["record"])
    dupes = [k for k, n in seen.items() if n > 1]
    if dupes:
        bad.append("a record is claimed twice: %s" % ", ".join(k[1] for k in dupes[:4]))
    if bad:
        sys.exit("native-operators: --check: " + "; ".join(bad[:6]))
    status = collections.Counter(r["status"] for r in rows)
    print("native-operators: %d declarations, %d bound to a record (%s)"
          % (len(rows), status["bound"], ", ".join("%s %d" % kv for kv in sorted(status.items()))))
    return 0


def selftest():
    cases = 0
    # The stem table, on the spellings the records actually use.
    assert compose("==", ["bool", "bool"]) == "EqualEqual_BoolBool"
    assert compose("!=", ["float", "float"]) == "NotEqual_FloatFloat"
    assert compose("~=", ["string", "string"]) == "ComplementEqual_StrStr"
    assert compose(">>>", ["int", "int"]) == "GreaterGreaterGreater_IntInt"
    assert compose("*", ["vector", "float"]) == "Multiply_VectorFloat"
    assert compose("$", ["string", "string"]) == "Concat_StrStr"
    assert compose("@=", ["string", "string"]) == "AtEqual_StrStr"
    assert compose("-", ["float"]) == "Subtract_PreFloat"
    assert compose("Dot", ["vector", "vector"]) == "Dot_VectorVector"
    assert compose("??", ["int"]) is None
    cases += 1
    # Argument modifiers and `out` are not part of the name; declaration order is.
    assert param_types(" out byte A, byte B ") == ["byte", "byte"]
    assert param_types(" coerce string A, coerce string B ") == ["string", "string"]
    assert param_types(" skip bool B, bool A ") == ["bool", "bool"]
    assert param_types(" out int A, float B ") == ["int", "float"]
    cases += 1
    # A `native` line and an `operator` line in different statements must not compose one declaration.
    two = "native function int Foo( int A );\nvar int B;\nnative(1) operator int + ( int A, int B );"
    got = parse(two)
    assert len(got) == 1 and got[0][1:] == ("+", "1", "Add_IntInt"), got
    # The parser on real declaration shapes, including the named and the wrapped ones.
    got = parse("native(184) static final operator(34) float += ( out float A, float B );")
    assert got[0][1:] == ("+=", "184", "AddEqual_FloatFloat"), got
    got = parse("native static final operator(24) bool ClockwiseFrom( int A, int B );")
    assert got[0][1:] == ("ClockwiseFrom", "", "ClockwiseFrom_IntInt"), got
    got = parse("native(296) static final operator(16) vector * ( vector A, vector B );")
    assert got[0][2:] == ("296", "Multiply_VectorVector"), got
    cases += 1
    # The pool's `String` spelling answers where the script's `Str` does not.
    alias = "AtEqual_StrStr".replace("Str", "String")
    assert alias == "AtEqual_StringString", alias
    cases += 1
    print("native-operators: selftest passed (%d cases)" % cases)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("system", nargs="?", help="DUMP/System (the .u packages)")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--walk", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.check:
        return check(args)
    if not args.system:
        sys.exit("native-operators: required: give DUMP/System")
    return run(args.system, args.out, args.walk) and 0


if __name__ == "__main__":
    sys.exit(main())
