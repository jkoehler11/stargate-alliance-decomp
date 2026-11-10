#!/usr/bin/env python3
"""Mark every native body in the extracted script tree, and cross-link it (PLAN 3.9).

`tools/native-reconcile.py` answered *whether* the two halves agree: 681 `native function` declarations in the
shipped text against 797 native function records, 672 matched. What it did not do is the other half of 3.9 — put
a marker on each declaration in the extracted tree so a reader of `Pawn.uc` knows the body is C++, and give that
marker a place to point at. This tool does both, from the same package text:

    python3 tools/native-mark.py DUMP/System -o inventory --tree $WORK/UnrealScript
    python3 tools/native-mark.py --check        # the committed cross-link table, no dump
    python3 tools/native-mark.py --selftest     # the parser and the marker pass, on fixtures

For every `native function` declaration it writes one line above the declaration:

    // native - see src/Native/Engine/Pawn.cpp  token 0 binder intUPawexecSetViewPitch body 0x0031e4f0

and one row to `inventory/native_bodies.csv` — `package, class, kind, function, record, native_token, params,
binder, binder_va, body_va, module, src_native, marker` — so the tree the marker was written into stays out of git
(it is game-derived source) while *what was written into it* is a committed, auditable table. The row's key
`(package, class, function)` is also the key `inventory/natives.csv` uses, so the marker's token, parameter count
and flags are one join away; `binder`/`binder_va` come from `inventory/native_binder_names.csv` (the image's own
`int<prefix><Class>exec<Function>` literal) and `body_va` from `inventory/native_targets.csv` (the native
registration table's body pointer).

The marker is **ASCII** (`-` for the dash) and a pure function of the dump text: every line carrying the prefix is
removed and re-inserted on each pass, so a second `--tree` run is byte-identical. That is what makes the tree
regenerable and the row count checkable.

An `operator` declaration is marked too, and its marker names the record the declaration composes — an operator has
no function name to join on, and `(package, class, symbol)` is ambiguous because `Core.Object` declares `==` for both
`bool` and `Object`. `tools/native-operators.py` measured that one statement becomes exactly one record (97 of 97
bound), so the line gains a `record` field and nothing else changes; the compiler's own extra records (`++`/`--` and
the unary family) have no declaration to mark and stay in `inventory/native_tokens.csv` under basis
`operator-expansion`.

A `native event` is a third declaration form, and it was invisible to every counting pass: `native-reconcile` (and
this tool before it) looks for `native … function`, so the eleven `native event` declarations in the shipped text —
`ClientTravel`, `ClientHearSound`, `UpdateVehicle`, `GetFont`, `LogStatLine` and their kin — read as "record-only,
text-missing" in `inventory/native_residual.csv` while the text spells them plainly. They are marked like a function
(no `record` field: the declaration names its own function), and the marker is the only place the tree says so.
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

NATIVES = os.path.join(ROOT, "inventory", "natives.csv")
BINDERS = os.path.join(ROOT, "inventory", "native_binder_names.csv")
TARGETS = os.path.join(ROOT, "inventory", "native_targets.csv")
OPERATORS = os.path.join(ROOT, "inventory", "native_operators.csv")
MODULES = os.path.join(ROOT, "inventory", "function_modules.csv")
OUT = os.path.join(ROOT, "inventory", "native_bodies.csv")

COLS = ["package", "class", "kind", "function", "record", "native_token", "params", "binder", "binder_va",
        "body_va", "module", "src_native", "marker"]

# The one line the tree carries. `lstrip().startswith(MARK)` is the test for "this line is a marker, not source".
MARK = "// native - see src/Native/"

# A `native event`: `native event int Main( string Parms )`, `native simulated event DemoPlaySound ( … )`,
# `native final event Font GetFont( int XRes )`. The return type is optional, the modifiers are not part of the name.
EVENT = re.compile(r"\bnative\b(?:\s*\([^)]*\))?(?:\s+(?:static|final|simulated|private|protected|const|latent))*"
                   r"\s+event\s+(?:[\w<>\[\].]+\s+)?(?P<name>\w+)\s*\(", re.I)


def load_tool(name):
    """Import a sibling tool whose file name is not an identifier (`pkg-script-text.py`)."""
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PST = load_tool("pkg-script-text")
REC = load_tool("native-reconcile")
OPS = load_tool("native-operators")


def blank_comments(text):
    """Replace comments with spaces, keeping every offset: `declarations()` drops them, insertion needs the map.

    A length-preserving blank is what lets a statement offset found in the blanked text index the original. It is
    the same rule `native-reconcile.strip_comments` applies (block comments, then `//` to end of line), so the two
    cannot disagree about which statements exist.
    """
    out = list(text)
    for m in list(re.finditer(r"/\*.*?\*/", text, re.S)):
        for i in range(m.start(), m.end()):
            if out[i] not in "\r\n":
                out[i] = " "
    text = "".join(out)
    out = list(text)
    for m in re.finditer(r"//[^\n]*", text):
        for i in range(m.start(), m.end()):
            out[i] = " "
    return "".join(out)


def declaration_spans(text):
    """[(offset, name, index, kind)] — one per native declaration, offset = first character of its statement.

    A `function` is `native-reconcile`'s regex; an `operator` is `native-operators`' own `DECL`, because the former's
    one- or two-character symbol class cannot see `$`, `$=`, `>>>`, `ClockwiseFrom`, `Dot` or `Cross` and counted 92
    declarations where the text holds 97. Both run on a length-preserving blanked copy, so the offset survives into
    the original and the marker lands above the declaration's first line. `declarations()` on the original text is
    the cross-check in `class_rows()`: if the two ever disagree, the tree is not written at all rather than marked
    in the wrong places.
    """
    blank = blank_comments(text)
    spans = []
    start = 0
    for chunk in blank.split(";"):
        head = re.search(r"\S", chunk)
        if head and re.search(r"\bnative\b", chunk, re.I):
            if re.search(r"\boperator\b", chunk, re.I):
                for m in OPS.DECL.finditer(chunk):
                    spans.append((start + m.start(), m.group("symbol"),
                                  (m.group("token") or "").strip() or None, "operator"))
            elif re.search(r"\bevent\b", chunk, re.I):
                for m in EVENT.finditer(chunk):
                    spans.append((start + m.start(), m.group("name"), None, "event"))
            elif re.search(r"\bfunction\b", chunk, re.I):
                fn = REC.NATIVE_FN.search(chunk)
                off = start + head.start()
                spans.append((off, fn.group(2), fn.group(1), "function") if fn else (off, "", None, "other"))
        start += len(chunk) + 1
    return spans


def line_start(text, off):
    """Offset of the beginning of the line that holds `off`."""
    return text.rfind("\n", 0, off) + 1


def eol_of(text):
    return "\r\n" if "\r\n" in text else "\n"


def strip_markers(text):
    """Drop every existing marker line, so the pass is a pure function of the text rather than of its history."""
    keep = []
    for line in text.splitlines(True):
        if line.lstrip().startswith(MARK):
            continue
        keep.append(line)
    return "".join(keep)


def render(row):
    """The exact line written into the tree, from a row's own fields.

    An operator row adds `record <name>` after the token: the declaration's own name is a symbol (`==`), so without
    it the line would not say which C++ function the declaration became. Function rows carry no `record`, so their
    markers are byte-identical to the ones the tree already had.
    """
    return "%s%s/%s.cpp  token %s %sbinder %s body %s" % (
        MARK, row["package"], row["class"], row["native_token"] or "-",
        ("record %s " % row["record"]) if row.get("record") else "",
        row["binder"] or "-", row["body_va"] or "-")


def src_native(pkg, cls):
    return "src/Native/%s/%s.cpp" % (pkg, cls)


def mark_text(text, rows_by_name, op_rows=()):
    """Insert one marker line above each native declaration; return (text, inserted).

    `rows_by_name` is keyed by the declaration's function name; `op_rows` is the class's operator rows in
    declaration order, because the symbol alone cannot separate `Core.Object`'s two `==` declarations.
    """
    text = strip_markers(text)
    eol = eol_of(text)
    spots = []
    ops_seen = 0
    for off, name, _idx, kind in declaration_spans(text):
        if kind == "function":
            row = rows_by_name.get((name or "").lower())
            if row is None:
                continue
        elif kind == "event":
            row = rows_by_name.get((name or "").lower())
            if row is None:
                raise SystemExit("native-mark: the event %r has no row" % (name,))
        elif kind == "operator":
            if ops_seen >= len(op_rows):
                raise SystemExit("native-mark: more operator declarations than rows (%r)" % (name,))
            row = op_rows[ops_seen]
            ops_seen += 1
        else:
            continue
        spots.append((line_start(text, off), render(row)))
    if ops_seen != len(op_rows):
        raise SystemExit("native-mark: %d operator rows for %d operator declarations" % (len(op_rows), ops_seen))
    out = []
    last = 0
    for at, line in sorted(spots):
        out.append(text[last:at])
        out.append(line + eol)
        last = at
    out.append(text[last:])
    return "".join(out), len(spots)


def load_rows():
    recs = collections.defaultdict(dict)
    if os.path.isfile(NATIVES):
        with open(NATIVES, newline="") as fh:
            for r in csv.DictReader(fh):
                recs[(r["package"], r["class"])][r["function"].lower()] = r
    binder = {}
    if os.path.isfile(BINDERS):
        with open(BINDERS, newline="") as fh:
            for r in csv.DictReader(fh):
                binder[(r["class"].lower(), r["function"].lower())] = r
    target = {}
    if os.path.isfile(TARGETS):
        with open(TARGETS, newline="") as fh:
            for r in csv.DictReader(fh):
                target.setdefault((r["class"].lower(), r["function"].lower()), r)
    module = {}
    if os.path.isfile(MODULES):
        with open(MODULES, newline="") as fh:
            for r in csv.DictReader(fh):
                module[r["address"].lower()] = r["module"]
    return recs, binder, target, module


def load_ops():
    """{(package, class): [row]} — `inventory/native_operators.csv`, in the declaration order it was written in."""
    out = collections.defaultdict(list)
    if os.path.isfile(OPERATORS):
        with open(OPERATORS, newline="") as fh:
            for r in csv.DictReader(fh):
                out[(r["package"], r["class"])].append(r)
    return out


def op_rows(pkg_name, cls, text, rows, binder, module):
    """The rows for one class's `operator` declarations, joined to the table by declaration text, in order.

    The declaration text is the join key: `(package, class, symbol)` is ambiguous, because `Core.Object` declares
    `==` for both `bool` and `Object`. A disagreement between the text and the committed table raises rather than
    marking the wrong declaration.
    """
    decls = OPS.parse(text)
    if len(decls) != len(rows):
        raise SystemExit("native-mark: %s.%s: the text holds %d operator declarations, the table %d"
                         % (pkg_name, cls, len(decls), len(rows)))
    out = []
    for (decl, symbol, token, record), r in zip(decls, rows):
        if re.sub(r"\s+", " ", decl).strip() != r["declaration"] or symbol != r["symbol"]:
            raise SystemExit("native-mark: %s.%s: the text's declaration %r is not the table's %r"
                             % (pkg_name, cls, decl, r["declaration"]))
        operands = re.search(r"\(([^)]*)\)\s*$", r["declaration"])
        b = binder.get((cls.lower(), (r["record"] or "").lower()), {})
        row = collections.OrderedDict()
        row["package"] = pkg_name
        row["class"] = cls
        row["kind"] = "operator"
        row["function"] = symbol
        row["record"] = (r["record"] or "").strip()
        row["native_token"] = ((token or "") or (r["record_token"] or "")).strip()
        row["params"] = str(len(OPS.param_types(operands.group(1)))) if operands else ""
        row["binder"] = (b.get("binder") or "").strip()
        row["binder_va"] = (b.get("binder_va") or "").strip()
        row["body_va"] = (r["body_va"] or "").strip()
        row["module"] = module.get(row["body_va"].lower(), "not-in-census" if row["body_va"] else "")
        row["src_native"] = src_native(pkg_name, cls)
        row["marker"] = ""
        row["marker"] = render(row)
        out.append(row)
    return out


def event_rows(pkg_name, cls, text, recs, binder, target, module):
    """A row per `native event` declaration — the third form, joined by the function's own name."""
    rows = []
    for name in [n for _o, n, _t, k in declaration_spans(text) if k == "event"]:
        low = (name or "").lower()
        rec = recs.get((pkg_name, cls), {}).get(low, {})
        b = binder.get((cls.lower(), low), {})
        t = target.get((cls.lower(), low), {})
        body = (t.get("body") or "").strip()
        row = collections.OrderedDict()
        row["package"] = pkg_name
        row["class"] = cls
        row["kind"] = "event"
        row["function"] = name or ""
        row["record"] = ""
        row["native_token"] = (rec.get("native_token") or "").strip()
        row["params"] = (rec.get("parameter_count") or "").strip()
        row["binder"] = (b.get("binder") or "").strip()
        row["binder_va"] = (b.get("binder_va") or "").strip()
        row["body_va"] = body
        row["module"] = module.get(body.lower(), "not-in-census" if body else "")
        row["src_native"] = src_native(pkg_name, cls)
        row["marker"] = ""
        row["marker"] = render(row)
        rows.append(row)
    return rows


def class_rows(pkg_name, cls, text, recs, binder, target, module, ops=None):
    """Every native declaration in one class's text, cross-linked: (function rows, operator rows, event rows)."""
    decls = [d for d in REC.declarations(text) if d[2] == "function"]
    spans = [(n, i, k) for _o, n, i, k in declaration_spans(text) if k == "function"]
    if [(n, i) for n, i, _k in spans] != [(n, i) for n, i, _k in decls]:
        raise SystemExit("native-mark: %s.%s: the blanking pass sees %r, declarations() sees %r"
                         % (pkg_name, cls, spans, [(n, i, k) for n, i, k in decls]))
    rows = []
    for name, index, _kind in decls:
        low = (name or "").lower()
        rec = recs.get((pkg_name, cls), {}).get(low, {})
        b = binder.get((cls.lower(), low), {})
        t = target.get((cls.lower(), low), {})
        body = (t.get("body") or "").strip()
        row = collections.OrderedDict()
        row["package"] = pkg_name
        row["class"] = cls
        row["kind"] = "function"
        row["function"] = name or ""
        row["record"] = ""
        row["native_token"] = (index or (rec.get("native_token") or "")).strip()
        row["params"] = (rec.get("parameter_count") or "").strip()
        row["binder"] = (b.get("binder") or "").strip()
        row["binder_va"] = (b.get("binder_va") or "").strip()
        row["body_va"] = body
        row["module"] = module.get(body.lower(), "not-in-census" if body else "")
        row["src_native"] = src_native(pkg_name, cls)
        row["marker"] = ""
        row["marker"] = render(row)
        rows.append(row)
    return (rows, op_rows(pkg_name, cls, text, (ops or {}).get((pkg_name, cls), []), binder, module),
            event_rows(pkg_name, cls, text, recs, binder, target, module))


def run(system_dir, outdir, tree=None, walk=False):
    recs, binder, target, module = load_rows()
    ops = load_ops()
    all_rows = []
    marked = mismatched = written = 0
    noparent = []
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
            text = PST.script_text(pkg, exp)
            if not text:
                continue
            rows, orows, erows = class_rows(pkg_name, cls, text, recs, binder, target, module, ops)
            all_rows.extend(rows)
            all_rows.extend(orows)
            all_rows.extend(erows)
            if tree:
                # Every class is written, not just the 92 that declare natives: a tree missing the other 2,227
                # files is an overlay, not the extracted tree, and cannot be diffed against one.
                body, count = text, 0
                if rows or orows or erows:
                    body, count = mark_text(text, {r["function"].lower(): r for r in rows + erows}, orows)
                path = os.path.join(tree, pkg_name, cls + ".uc")
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", newline="", encoding="latin1") as fh:
                    fh.write(body)
                marked += count
                written += 1
                if count != len(rows) + len(orows) + len(erows):
                    mismatched += 1
                    noparent.append("%s.%s (%d of %d)"
                                    % (pkg_name, cls, count, len(rows) + len(orows) + len(erows)))
            if walk:
                for r in rows:
                    print("%-14s %-22s %-26s token=%-5s body=%-10s %s"
                          % (r["package"], r["class"], r["function"], r["native_token"] or "-",
                             r["body_va"] or "-", r["module"]))
    if outdir:
        os.makedirs(outdir, exist_ok=True)
        path = os.path.join(outdir, "native_bodies.csv")
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLS, lineterminator="\n")
            w.writeheader()
            for r in all_rows:
                w.writerow(r)
        print("wrote %s (%d rows)" % (path, len(all_rows)))
    fn = [r for r in all_rows if r["kind"] == "function"]
    op = [r for r in all_rows if r["kind"] == "operator"]
    ev = [r for r in all_rows if r["kind"] == "event"]
    total = collections.Counter()
    for r in fn:
        total["decl"] += 1
        total["record"] += bool(r["native_token"]) or bool(r["params"])
        total["body"] += bool(r["body_va"])
        total["binder"] += bool(r["binder"])
        total["census"] += bool(r["body_va"]) and r["module"] != "not-in-census"
    print("%d native function declarations in %d classes: %d with a record, %d with the image's binder literal,"
          " %d with a body address (%d of those in the function census)"
          % (total["decl"], len({(r["package"], r["class"]) for r in fn}), total["record"],
             total["binder"], total["body"], total["census"]))
    ototal = collections.Counter()
    for r in op:
        ototal["decl"] += 1
        ototal["record"] += bool(r["record"])
        ototal["binder"] += bool(r["binder"])
        ototal["body"] += bool(r["body_va"])
    print("%d operator declarations in %d classes: %d with a composed record, %d with the image's binder literal,"
          " %d with a body address"
          % (ototal["decl"], len({(r["package"], r["class"]) for r in op}), ototal["record"], ototal["binder"],
             ototal["body"]))
    etotal = collections.Counter()
    for r in ev:
        etotal["decl"] += 1
        etotal["binder"] += bool(r["binder"])
        etotal["body"] += bool(r["body_va"])
    print("%d native event declarations in %d classes: %d with the image's binder literal, %d with a body address"
          % (etotal["decl"], len({(r["package"], r["class"]) for r in ev}), etotal["binder"], etotal["body"]))
    mods = collections.Counter(r["module"] for r in all_rows)
    print("bodies by module attribution: " + ", ".join("%s %d" % (k or "(no body)", v)
                                                       for k, v in mods.most_common()))
    if tree:
        print("%d marker lines written into %s (%d class files; %d class(es) where a declaration went unmarked)"
              % (marked, tree, written, mismatched))
    if noparent:
        print("classes where the declaration count and the marked count differ: %s" % ", ".join(noparent))
    nobody = [r for r in all_rows if not r["body_va"]]
    if nobody:
        print("%d declaration(s) with no body in the image: %s"
              % (len(nobody), ", ".join("%s.%s" % (r["class"], r["function"]) for r in nobody)))
    return all_rows


def selftest():
    cases = 0
    # A wrapped declaration, an indexed one, a commented-out one and native *data*: only the two are bodies.
    text = ("class Object native;\r\n"
            "var native const int Depth;\r\n"
            "// native function NotReal( int A );\r\n"
            "native(113) final function GotoState( optional name NewState );\r\n"
            "native function bool Wrapped(\r\n"
            "    Actor Goal, int Mode );\r\n"
            "simulated function Scripted() { GotoState('Idle'); }\r\n"
            "native(150) static final operator bool < ( int A, int B );\r\n")
    spans = declaration_spans(text)
    got = [(n, i, k) for _o, n, i, k in spans]
    want = [("GotoState", "113", "function"), ("Wrapped", None, "function"), ("<", "150", "operator")]
    assert got == want, "spans %r, expected %r" % (got, want)
    cases += 1
    # The offset is the statement's first character, so the marker lands above the declaration, not mid-line.
    off = [o for o, n, _i, _k in spans if n == "Wrapped"][0]
    assert text[off:off + 6] == "native", "offset %d points at %r" % (off, text[off:off + 12])
    assert line_start(text, off) == text.rfind("\r\n", 0, off) + 2, "line start is off"
    cases += 1
    rows = [{"package": "Core", "class": "Object", "function": "GotoState", "native_token": "113",
             "binder": "intUObjectexecGotoState", "body_va": "0x0006f4b0"},
            {"package": "Core", "class": "Object", "function": "Wrapped", "native_token": "",
             "binder": "", "body_va": ""}]
    by = {r["function"].lower(): r for r in rows}
    orow = {"package": "Core", "class": "Object", "kind": "operator", "function": "<", "record": "Less_IntInt",
            "native_token": "150", "binder": "intUObjectexecLess_IntInt", "body_va": "0x00041234"}
    marked, count = mark_text(text, by, [orow])
    assert count == 3, "marked %d, expected 3" % count
    assert "// native - see src/Native/Core/Object.cpp  token 113 binder intUObjectexecGotoState " \
           "body 0x0006f4b0\r\nnative(113) final function GotoState" in marked, marked
    assert "// native - see src/Native/Core/Object.cpp  token - binder - body -\r\nnative function bool Wrapped" \
           in marked, "an unmeasured native still gets a marker naming the gap"
    assert "NotReal" not in [l for l in marked.splitlines() if l.lstrip().startswith(MARK)][0]
    assert "// native - see src/Native/Core/Object.cpp  token 150 record Less_IntInt " \
           "binder intUObjectexecLess_IntInt body 0x00041234\r\n" \
           "native(150) static final operator bool < ( int A, int B );" in marked, marked
    cases += 1
    # The six declarations the old one- or two-character symbol class could not see must be spans, or 97 becomes 92.
    tricky = ("native(287) static final operator(20) string $ ( string A, string B );\r\n"
              "native static final operator(24) bool ClockwiseFrom( int A, int B );\r\n"
              "native(290) static final operator(22) int >>> ( int A, int B );\r\n")
    assert [(n, i, k) for _o, n, i, k in declaration_spans(tricky)] == \
        [("$", "287", "operator"), ("ClockwiseFrom", None, "operator"), (">>>", "290", "operator")], \
        [(n, i, k) for _o, n, i, k in declaration_spans(tricky)]
    cases += 1
    # Idempotent: the same pass over its own output is byte-identical, and a stale marker is replaced.
    again, count2 = mark_text(marked, by, [orow])
    assert again == marked and count2 == count, "the marker pass is not idempotent"
    stale = marked.replace("token 113", "token 999")
    fixed, _ = mark_text(stale, by, [orow])
    assert fixed == marked, "a stale marker was not replaced"
    assert "token 999" not in fixed
    cases += 1
    # render() and the row agree, so --check can police a committed table without the dump.
    assert render(rows[0]) == "// native - see src/Native/Core/Object.cpp  token 113 " \
                              "binder intUObjectexecGotoState body 0x0006f4b0", render(rows[0])
    assert src_native("Engine", "Pawn") == "src/Native/Engine/Pawn.cpp"
    # A `native event` is a third form: the modifiers and the return type are not part of the name, and a plain
    # `event` (no `native`) is script, not a body.
    evtext = ("native event int Main( string Parms );\r\n"
              "native simulated event DemoPlaySound ( sound Sound, optional float Volume );\r\n"
              "native final event Font GetFont( int XRes );\r\n"
              "event ScriptedOnly( int A ) { }\r\n")
    assert [(n, k) for _o, n, _i, k in declaration_spans(evtext)] == \
        [("Main", "event"), ("DemoPlaySound", "event"), ("GetFont", "event")], \
        [(n, k) for _o, n, _i, k in declaration_spans(evtext)]
    erow = {"package": "Core", "class": "Commandlet", "kind": "event", "function": "Main", "record": "",
            "native_token": "0", "binder": "intUCommandletexecMain", "body_va": "0x00066be0"}
    evrows = {"main": erow}
    for n in ("DemoPlaySound", "GetFont"):
        evrows[n.lower()] = dict(erow, function=n, binder="intXexec" + n, body_va="0x00012345")
    mx, mcount = mark_text(evtext, evrows, ())
    assert mcount == 3, "marked %d events, expected 3" % mcount
    assert "// native - see src/Native/Core/Commandlet.cpp  token 0 binder intUCommandletexecMain " \
           "body 0x00066be0\r\nnative event int Main( string Parms );" in mx, mx
    assert "ScriptedOnly" not in [l for l in mx.splitlines() if l.lstrip().startswith(MARK)][0]
    cases += 1
    assert render(orow) == "// native - see src/Native/Core/Object.cpp  token 150 record Less_IntInt " \
                           "binder intUObjectexecLess_IntInt body 0x00041234", render(orow)
    cases += 1
    print("ok   native-mark: %d cases (spans with a wrap, a comment and the six hard operator symbols, statement "
          "offsets, function and operator marker text, idempotency, a stale marker, the render rule)" % cases)
    return 0


def check(path=None):
    """Validate the committed cross-link table without a dump: shape, keys, and marker == render(row)."""
    path = path or OUT
    if not os.path.isfile(path):
        print("FAIL native-mark: %s is missing" % path)
        return 1
    rows = list(csv.DictReader(open(path, newline="")))
    bad = []
    seen = set()
    for i, r in enumerate(rows, start=2):
        key = (r["package"], r["class"], r["kind"], r["function"], r["record"])
        if key in seen:
            bad.append("line %d: duplicate key %s" % (i, ".".join(key)))
        seen.add(key)
        if r["kind"] not in ("function", "operator", "event"):
            bad.append("line %d: kind %r is not a function, an operator or an event" % (i, r["kind"]))
        if r["kind"] == "operator" and not r["record"]:
            bad.append("line %d: an operator row names no record" % i)
        if r["kind"] in ("function", "event") and r["record"]:
            bad.append("line %d: a %s row names a record" % (i, r["kind"]))
        if r["src_native"] != src_native(r["package"], r["class"]):
            bad.append("line %d: src_native %r does not follow the row's package/class" % (i, r["src_native"]))
        if r["marker"] != render(r):
            bad.append("line %d: marker %r is not the render of its own row" % (i, r["marker"]))
        if not r["marker"].startswith(MARK):
            bad.append("line %d: marker does not carry the %r prefix" % (i, MARK))
        for field in ("native_token", "params"):
            if r[field] and not r[field].isdigit():
                bad.append("line %d: %s %r is not a number" % (i, field, r[field]))
        for field in ("binder_va", "body_va"):
            if r[field] and not r[field].lower().startswith("0x"):
                bad.append("line %d: %s %r is not an address" % (i, field, r[field]))
    if bad:
        print("FAIL native-mark: %d row(s) bad in %s" % (len(bad), path))
        for line in bad[:8]:
            print("  " + line)
        return 1
    kinds = collections.Counter(r["kind"] for r in rows)
    print("ok   native-mark: %d rows in %s (%s), every marker the render of its own row"
          % (len(rows), os.path.relpath(path, ROOT),
             ", ".join("%s %d" % kv for kv in sorted(kinds.items()))))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", nargs="?", help="the dump's System directory")
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--tree", help="also write the marked script tree here (a $WORK path, never the repo)")
    ap.add_argument("--walk", action="store_true", help="one line per declaration")
    ap.add_argument("--check", action="store_true", help="validate the committed table; no dump")
    ap.add_argument("--selftest", action="store_true", help="the parser and the marker pass on fixtures; no dump")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.check:
        return check()
    if not args.path:
        ap.error("a System directory is required unless --check or --selftest is given")
    run(args.path, args.out, tree=args.tree, walk=args.walk)
    return 0


if __name__ == "__main__":
    sys.exit(main())
