#!/usr/bin/env python3
"""What a declaration without a body means in this build: the stub's owner and its compiled script.

`tools/class-text-bodies.py` counts a declaration's body with a brace walk, and 906 of the corpus's 11,411
declarations have none. That is two different questions and the tool was only answering the first: a
declaration can have no body because the language lets it (`function Foo();` -- an engine hook), because the
body sits after an `#endif`, or because the exported `ScriptText` stops in the middle of one. The other half
is what the *build* did with it, and every declaration has an export: the payload's own `Function` record.

    tools/text-stubs.py --system DUMP/System -o inventory/text_stubs.csv --write-doc docs/PACKAGE_SCRIPT_TEXT.md
    tools/text-stubs.py --report
    tools/text-stubs.py --check-doc docs/PACKAGE_SCRIPT_TEXT.md   # from the committed artifact
    tools/text-stubs.py --check                                   # synthetic, no dump, writes nothing

The payload read is the one U13 established, used here as a test rather than a parser: a `Function` payload
ends `<u32 length><script><7-byte trailer>`, so a script that is exactly the compiler's empty body reads as
the seven bytes `03 00 00 00 04 0b 47` immediately before the trailer (`04 0b 47` is `Return`). A stub whose
payload carries that is a declaration the compiler agreed had no body; one whose payload is longer is a stub
the build *did* compile, i.e. a `ScriptText` that lost a body the package still carries. Nothing here prints
or commits script bytes: the artifact carries sizes, verdicts and names.

Reads the packages' exports and `ScriptText`; no bytecode walk, no boot, no Ghidra.
"""
import argparse
import collections
import csv
import importlib.util
import io
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402

ARTIFACT = os.path.join(ROOT, "inventory", "text_stubs.csv")
DOC = os.path.join(ROOT, "docs", "PACKAGE_SCRIPT_TEXT.md")
DOC_MARK = ("<!-- generated:text-stubs -->", "<!-- /generated:text-stubs -->")

FIELDS = ["kind", "package", "class", "name", "line", "shape", "owner", "redeclared", "payload", "script",
          "native", "declarations", "bodies", "stubs", "stubs_empty", "stubs_bodied", "stub_state", "count",
          "note"]

CENSUS_NOTE = {
    "classes": "class exports whose `ScriptText` parses",
    "classes_declared": "of those, carrying at least one declaration",
    "packages": "packages with at least one declaration",
    "declarations": "declarations the grammar's rule reads in the live text",
    "bodies": "of those, a body the brace walk resolves",
    "stubs": "of those, declaration-only: no body between the declaration and the next one",
    "stub_semicolon": "stubs with the declaration's `;` before any `{` -- a bodyless declaration",
    "stub_no_brace": "stubs with no `{` in the window at all -- the body sits after an `#endif`",
    "stub_unbalanced": "stubs whose braces never close -- a `ScriptText` that ends inside a body",
    "stub_commented": "stub windows holding a block comment",
    "stub_class_export": "stubs whose name the class itself exports as a `Function`",
    "stub_state_export": "stubs whose name only a `State` of the class exports",
    "stub_no_export": "stubs naming no `Function` export of their class",
    "stub_redeclared": "stubs whose name the same text declares again, with a body",
    "stub_native": "stubs whose export carries `FUNC_Native`",
    "stub_empty": "stubs whose export is the compiler's empty script -- the declaration was all there was",
    "stub_bodied": "stubs whose export carries a longer script -- a body the text lost",
    "stub_empty_class": "of the empty ones, the class's own export",
    "stub_empty_state": "of the empty ones, a `State` of the class exports it",
    "stub_bodied_class": "of the bodied ones, the class's own export -- the body the text lost",
    "stub_bodied_state": "of the bodied ones, a `State` of the class exports it -- the text declares it, the state implements it",
    "stub_bodied_redeclared": "of the bodied ones, the same text declares the name again with a body -- a forward declaration",
    "stub_bodied_undeclared": "of the bodied ones, no body anywhere in the live text -- the script the text does not spell",
    "body_empty": "declarations with a body whose export is the empty script -- the control",
    "body_empty_braces": "declarations whose body is an empty `{}`",
    "body_empty_braces_bodied": "of those, carry a longer payload than the empty script",
    "body_empty_braces_empty": "of the empty-script bodies, an empty `{}`",
    "body_empty_other": "of the empty-script bodies, a body carrying no statements -- locals, comments or structure",
    "body_no_export": "declarations with a body naming no `Function` export of their class",
    "distinct_stub_names": "distinct names behind the stubs",
    "all_stub_classes": "classes whose every declaration is a stub",
    "half_stub_classes": "classes with four or more declarations and at least half of them stubs",
    "largest_stub_payload": "the largest payload behind a stub",
}

# The compiler's empty body: `Return` with no operand, and the u32 length in front of it.
EMPTY_SCRIPT = b"\x03\x00\x00\x00\x04\x0b\x47"
TRAILERS = (7, 9)          # 9 for a Net function, which serialises `RepOffset` before its flags
FUNC_NATIVE = 0x400


def load_tool(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CTB = None
PST = None


def ctb_tool():
    """`tools/class-text-bodies.py`, loaded once -- the declaration rule and the brace walk are its own."""
    global CTB
    if CTB is None:
        CTB = load_tool("class_text_bodies", "class-text-bodies.py")
    return CTB


def pst_tool():
    global PST
    if PST is None:
        PST = load_tool("pkg_script_text", "pkg-script-text.py")
    return PST


def empty_script(payload):
    """True when the payload ends with the compiler's empty script in front of a 7- or 9-byte trailer."""
    for trailer in TRAILERS:
        lo, hi = len(payload) - 7 - trailer, len(payload) - trailer
        if lo >= 0 and payload[lo:hi] == EMPTY_SCRIPT:
            return True
    return False


def window_shape(masked_window):
    """(shape, brace offset) for the bytes between a declaration and the next one, on a masked buffer."""
    brace = masked_window.find("{")
    semi = masked_window.find(";")
    if brace < 0:
        return ("semicolon" if semi >= 0 else "no-brace"), -1
    if 0 <= semi < brace:
        return "semicolon", brace
    depth, k = 0, brace
    while k < len(masked_window):
        c = masked_window[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return "body", brace
        k += 1
    return "unbalanced", brace


def classify(text, resolve=None):
    """Rows for one class text: every declaration, whether it has a body, and where its implementation is.

    `resolve(name)` answers `None` or `(owner, payload, flags)` for the class's `Function` export of that
    name -- a callable rather than a package so the self-test drives the same walk with a synthetic answer.
    """
    ctb = ctb_tool()
    masked = ctb.mask(text)
    ms = list(ctb.FUNC.finditer(masked))
    spans = {}
    rows = []
    for k, m in enumerate(ms):
        limit = ms[k + 1].start() if k + 1 < len(ms) else len(masked)
        spans.setdefault(m.group(1).lower(), []).append(
            (m.start(), ctb.body_span(masked, m.end(), limit) is not None))
    for k, m in enumerate(ms):
        limit = ms[k + 1].start() if k + 1 < len(ms) else len(masked)
        span = ctb.body_span(masked, m.end(), limit)
        body = span is not None
        name = m.group(1).lower()
        row = {"name": name, "line": masked.count("\n", 0, m.start()) + 1, "body": body,
               "shape": "", "commented": False, "owner": "", "redeclared": False, "payload": "",
               "script": "", "native": "", "empty_body": ""}
        hit = resolve(name) if resolve else None
        if hit is None:
            if not body:
                row["owner"] = "none"
        else:
            owner, payload, flags = hit
            row["owner"] = "state" if owner else "class"
            row["payload"] = len(payload)
            row["script"] = "empty" if empty_script(payload) else "body"
            row["native"] = "yes" if flags & FUNC_NATIVE else ""
        if body:
            # an empty `{}` body is the control the no-code test needs: it compiles to no statements too, so the
            # test separates "no code" from "code" and not "no body" from "body"
            row["empty_body"] = "yes" if not masked[span[0] + 1:span[1] - 1].strip() else ""
        else:
            row["shape"], _brace = window_shape(masked[m.end():limit])
            row["commented"] = bool(ctb.BLOCK.search(text[m.end():limit]))
            row["redeclared"] = any(b and st > m.start() for st, b in spans[name])
        rows.append(row)
    return rows


def index_exports(pkg):
    """(class, name) -> (owner, payload, flags) for every `Function` export of a package.

    A `Function`'s Outer is a `State` as often as a `Class`, and the state's name is not a class: the walk to
    the nearest enclosing `Class` is what makes the key `(class, function)`. A class-owned export wins over a
    state's, because that is the one a class-level declaration refers to.
    """
    out = {}
    for e in pkg.exports:
        if pkg.class_name_of(e) != "Function":
            continue
        name = pkg.name(e["name"]).lower()
        o, seen, owner, cls = e["outer"], 0, None, None
        while o and seen < 8:
            oe = pkg.exports[o - 1]
            kind = pkg.class_name_of(oe)
            nm = pkg.name(oe["name"]).lower()
            if kind == "State":
                owner = nm
            elif kind == "Class":
                cls = nm
                break
            o = oe["outer"]
            seen += 1
        if cls is None:
            continue
        payload = pkg.data[e["offset"]:e["offset"] + e["size"]] if e["size"] else b""
        key = (cls, name)
        prev = out.get(key)
        if prev is not None and prev[0] is None:      # a class-owned export wins over a state's
            continue
        out[key] = (owner, payload, e["flags"])
    return out


def measure(system_dir):
    """The whole corpus: class rows, stub rows and the census."""
    t = collections.Counter()
    per_pkg = collections.Counter()
    class_rows, stub_rows = [], []
    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        package = fname[:-2]
        exports = index_exports(pkg)
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            text = pst_tool().script_text(pkg, exp)
            if not text:
                continue
            text = text.replace("\r\n", "\n")
            cls = pkg.name(exp["name"]).lower()
            t["classes"] += 1
            rows = classify(text, lambda n, c=cls, e=exports: e.get((c, n)))
            if not rows:
                continue
            t["classes_declared"] += 1
            bodies = [r for r in rows if r["body"]]
            stubs = [r for r in rows if not r["body"]]
            t["declarations"] += len(rows)
            t["bodies"] += len(bodies)
            t["stubs"] += len(stubs)
            per_pkg[package] += len(stubs)
            empty = bodied = state = 0
            for r in stubs:
                t["stub_" + r["shape"].replace("-", "_")] += 1
                t["stub_" + ("no" if r["owner"] == "none" else r["owner"]) + "_export"] += 1
                if r["commented"]:
                    t["stub_commented"] += 1
                if r["redeclared"]:
                    t["stub_redeclared"] += 1
                if r["native"]:
                    t["stub_native"] += 1
                if r["script"] == "empty":
                    t["stub_empty"] += 1
                    t["stub_empty_" + ("state" if r["owner"] == "state" else "class")] += 1
                    empty += 1
                elif r["script"] == "body":
                    t["stub_bodied"] += 1
                    t["stub_bodied_" + ("redeclared" if r["redeclared"] else "undeclared")] += 1
                    t["stub_bodied_" + ("state" if r["owner"] == "state" else "class")] += 1
                    bodied += 1
                    t["largest_stub_payload"] = max(t["largest_stub_payload"], int(r["payload"]))
                if r["owner"] == "state":
                    state += 1
                stub_rows.append(dict(r, kind="stub", package=package, **{"class": cls}))
            for r in bodies:
                if r["owner"] == "none":
                    t["body_no_export"] += 1
                if r["empty_body"]:
                    t["body_empty_braces"] += 1
                if r["script"] == "empty":
                    t["body_empty"] += 1
                    t["body_empty_braces_empty" if r["empty_body"] else "body_empty_other"] += 1
                if r["empty_body"] and r["script"] != "empty":
                    t["body_empty_braces_bodied"] += 1
            class_rows.append({"kind": "class", "package": package, "class": cls,
                               "declarations": len(rows), "bodies": len(bodies), "stubs": len(stubs),
                               "stubs_empty": empty, "stubs_bodied": bodied, "stub_state": state,
                               "note": "%d of %d declaration(s) are stubs" % (len(stubs), len(rows))})
            if stubs and not bodies:
                t["all_stub_classes"] += 1
            if len(stubs) >= 4 and 2 * len(stubs) >= len(rows):
                t["half_stub_classes"] += 1
    t["packages"] = len(per_pkg)
    t["distinct_stub_names"] = len({r["name"] for r in stub_rows})
    census = [{"kind": "census", "name": k, "count": v, "note": CENSUS_NOTE[k]}
              for k, v in t.items() if k in CENSUS_NOTE]
    return class_rows + stub_rows + census


# --- the page --------------------------------------------------------------------------------------
def render(rows):
    fh = io.StringIO()
    w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in FIELDS})
    return fh.getvalue()


def read_csv(path):
    if not os.path.exists(path):
        return []
    return list(csv.DictReader(open(path, encoding="utf-8")))


def count(rows, key):
    for r in rows:
        if r.get("kind") == "census" and r.get("name") == key:
            return int(r["count"] or 0)
    return 0


def comma(n):
    return "{:,}".format(int(n))


def pct(a, b):
    return (100.0 * a / b) if b else 0.0


def doc_block(rows):
    stubs = count(rows, "stubs")
    if not stubs:
        return "\n(no stub census rows)\n"
    decl = count(rows, "declarations")
    bodies = count(rows, "bodies")
    empty = count(rows, "stub_empty")
    bodied = count(rows, "stub_bodied")
    semicolon = count(rows, "stub_semicolon")
    nobrace = count(rows, "stub_no_brace")
    unbalanced = count(rows, "stub_unbalanced")
    class_owned = count(rows, "stub_class_export")
    state_owned = count(rows, "stub_state_export")
    noexport = count(rows, "stub_no_export")
    redeclared = count(rows, "stub_redeclared")
    native = count(rows, "stub_native")
    commented = count(rows, "stub_commented")
    lines = []
    lines.append("")
    lines.append("**Of the %s declarations the grammar's rule reads, %s carry a body the brace walk resolves and "
                 "%s are declaration-only** (%s%%) -- and every one of them has an export, because a declaration "
                 "is a `Function` object whether or not it has statements. Read against that export, a stub is "
                 "one of two things: **%s carry the compiler's empty script** (`04 0b 47`, `Return` and nothing "
                 "else), so the text and the build agree there is no body, and **%s carry a longer script**, a "
                 "body the package kept. Of those **%s** are forward declarations -- the same text declares the "
                 "name again, with a body, further down -- so **%s carry a script no live body in the "
                 "`ScriptText` spells**. The control is what keeps that from being a stub test: **%s of the %s "
                 "bodied declarations compile to the same empty script too** -- **%s** an empty `{}` body and **%s** "
                 "a body carrying only locals, comments or structure -- so the test separates *no code* from "
                 "*code* and not a stub from a body. %s of the %s empty-`{}` bodies carry a longer payload than it."
                 % (comma(decl), comma(bodies), comma(stubs), "%.1f" % pct(stubs, decl), comma(empty),
                    comma(bodied), comma(count(rows, "stub_bodied_redeclared")),
                    comma(count(rows, "stub_bodied_undeclared")), comma(count(rows, "body_empty")), comma(bodies),
                    comma(count(rows, "body_empty_braces_empty")), comma(count(rows, "body_empty_other")),
                    comma(count(rows, "body_empty_braces_bodied")), comma(count(rows, "body_empty_braces"))))
    lines.append("")
    lines.append("| shape | stubs | what it is |")
    lines.append("|---|---:|---|")
    lines.append("| `;` before any `{` | %s | a bodyless declaration -- the parameter list may sit on the next line |"
                 % comma(semicolon))
    lines.append("| no `{` in the window | %s | the body sits after an `#endif`, outside the declaration's region |"
                 % comma(nobrace))
    lines.append("| braces never close | %s | the exported `ScriptText` ends inside a body |" % comma(unbalanced))
    lines.append("")
    lines.append("| owner | empty script | compiled body |")
    lines.append("|---|---:|---:|")
    lines.append("| the class itself | %s | %s |"
                 % (comma(count(rows, "stub_empty_class")), comma(count(rows, "stub_bodied_class"))))
    lines.append("| a `State` of the class | %s | %s |"
                 % (comma(count(rows, "stub_empty_state")), comma(count(rows, "stub_bodied_state"))))
    lines.append("")
    lines.append("A stub's name is never a name the package lacks: **%s name a `Function` the class itself "
                 "exports**, **%s one only a `State` of the class exports**, and **%s name no export at all**. "
                 "**%s** are declared a second time in the same text with a body, **%s** sit in a window that "
                 "holds a block comment and **%s** carry `FUNC_Native` -- so the population is a language shape "
                 "and not a reader's miss. %s distinct names are behind the %s stubs."
                 % (comma(class_owned), comma(state_owned), comma(noexport), comma(redeclared), comma(commented),
                    comma(native), comma(count(rows, "distinct_stub_names")), comma(stubs)))
    lines.append("")
    lines.append("| package | stubs |")
    lines.append("|---|---:|")
    pkg_rows = [r for r in rows if r.get("kind") == "class"]
    totals = collections.Counter()
    for r in pkg_rows:
        totals[r["package"]] += int(r["stubs"] or 0)
    for name, n in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0])):
        if n:
            lines.append("| `%s` | %s |" % (name, comma(n)))
    lines.append("")
    top = sorted((r for r in pkg_rows if int(r["stubs"] or 0)), key=lambda r: -int(r["stubs"]))[:8]
    lines.append("The concentration is a handful of classes, not a spread: **%s classes are all stubs** and "
                 "**%s hold at least half**. The eight with the most:"
                 % (comma(count(rows, "all_stub_classes")), comma(count(rows, "half_stub_classes"))))
    lines.append("")
    lines.append("| class | stubs / declarations | empty | bodied |")
    lines.append("|---|---:|---:|---:|")
    for r in top:
        lines.append("| `%s.%s` | %s / %s | %s | %s |"
                     % (r["package"], r["class"], comma(r["stubs"]), comma(r["declarations"]),
                        comma(r["stubs_empty"] or 0), comma(r["stubs_bodied"] or 0)))
    lines.append("")
    seen, bodied_rows = set(), []
    for r in sorted((r for r in rows if r.get("kind") == "stub" and r.get("script") == "body"),
                    key=lambda r: -int(r["payload"] or 0)):
        key = (r["package"], r["class"], r["name"])
        if key in seen:
            continue
        seen.add(key)
        bodied_rows.append(r)
        if len(bodied_rows) == 6:
            break
    if bodied_rows:
        lines.append("The stubs whose payload carries code are the ones where the text and the build disagree -- "
                     "three of them are the shape cases above (the two `#endif` windows and the truncated text) and "
                     "the rest are bodies no live declaration of that class spells. The largest:")
        lines.append("")
        lines.append("| function | payload | owner |")
        lines.append("|---|---:|---|")
        for r in bodied_rows:
            lines.append("| `%s.%s.%s` | %s | %s |" % (r["package"], r["class"], r["name"], comma(r["payload"]),
                                                       r["owner"]))
        lines.append("")
    return "\n".join(lines) + "\n"


def splice(path, block):
    text = open(path, encoding="utf-8").read()
    lo, hi = DOC_MARK
    if lo not in text or hi not in text:
        raise SystemExit("FAIL text-stubs: markers not found in %s" % path)
    head, rest = text.split(lo, 1)
    _old, tail = rest.split(hi, 1)
    return head + lo + block + hi + tail


def write_doc(path, rows):
    new = splice(path, doc_block(rows))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new)


def check_doc(path, rows):
    text = open(path, encoding="utf-8").read()
    lo, hi = DOC_MARK
    if lo not in text or hi not in text:
        print("FAIL text-stubs: markers not found in %s" % path)
        return 1
    if text.split(lo, 1)[1].split(hi, 1)[0] != doc_block(rows):
        print("FAIL text-stubs: %s is stale -- regenerate with `make text-stubs`" % path)
        return 1
    print("ok   text-stubs: docs/PACKAGE_SCRIPT_TEXT.md reproduces from inventory/text_stubs.csv")
    return 0


# --- self-test -------------------------------------------------------------------------------------
def selftest():
    """A synthetic text with one of each shape, so a walk that stops seeing one fails before the corpus does.

    Every case is one the corpus has: a bodyless declaration whose parameter list is on the next line, a body
    after an `#endif`, a text that ends inside a body, a declaration the class exports from a `state`, a
    declaration that is really a forward declaration of a later body, and a bodyless declaration followed by a
    function with a body -- which a brace walk without a bound would read as the stub's own.
    """
    src = ("class Foo extends Bar;\n"
           "simulated function Stub(\n"
           "int Value);\n"
           "function Real(int X)\n"
           "{\n"
           "  if (X) return;\n"
           "}\n"
           "function Split(int Y)\n"
           "/* the body a build would compile is commented out */\n"
           "function After(int Q)\n"
           "{\n"
           "  return;\n"
           "}\n"
           "#ifdef XBOX\n"
           "function Guarded(int G);\n"
           "#endif\n"
           "function Later(int Z);\n"
           "function Later(int Z)\n"
           "{\n"
           "  return;\n"
           "}\n"
           "state Idle\n"
           "{\n"
           "  function InState();\n"
           "}\n"
           "function Truncated(int W)\n"
           "{\n"
           "  if (W)\n"
           "  {\n")
    empty = b"\x00\x00" + b"\x01\x02\x03\x04" + b"\x03\x00\x00\x00\x04\x0b\x47" + b"\x00\x00\x00\x00\x08\x02\x00"
    full = b"\x00\x00\x01\x02\x03\x04" + b"\x2a\x00\x00\x00" + b"\x0f" * 42 + b"\x04\x0b\x47" + \
        b"\x00\x00\x00\x00\x08\x02\x00"
    answers = {"stub": (None, empty, 0x4), "split": (None, empty, 0x4), "after": (None, full, 0x4),
               "guarded": (None, empty, 0x4), "later": (None, empty, 0x4), "instate": ("idle", empty, 0x4),
               "truncated": (None, full, 0x4), "real": (None, full, 0x4)}
    rows = classify(src, answers.get)
    first = collections.defaultdict(list)
    for r in rows:
        first[r["name"]].append(r)
    one = {k: v[0] for k, v in first.items()}
    problems = []
    if set(one) != {"stub", "real", "split", "after", "guarded", "later", "instate", "truncated"}:
        problems.append("declarations read %r" % sorted(one))
    if one.get("real", {}).get("body") is not True:
        problems.append("a bodied declaration read as a stub")
    if len(first.get("later", [])) != 2 or first["later"][0]["body"] or not first["later"][1]["body"]:
        problems.append("a duplicate declaration read as %r" % [(r["body"], r["line"]) for r in
                                                               first.get("later", [])])
    for name, shape in (("stub", "semicolon"), ("split", "no-brace"), ("guarded", "semicolon"),
                        ("truncated", "unbalanced")):
        if one.get(name, {}).get("shape") != shape:
            problems.append("%s read as %r, expected %r" % (name, one.get(name, {}).get("shape"), shape))
    if one.get("stub", {}).get("body") or one.get("split", {}).get("body"):
        problems.append("a bodyless declaration inherited the next function's body")
    if one.get("later", {}).get("redeclared") is not True:
        problems.append("a forward declaration was not matched to its later body")
    if one.get("stub", {}).get("redeclared") or one.get("after", {}).get("redeclared"):
        problems.append("a stub with no later body read as a forward declaration")
    if one.get("instate", {}).get("owner") != "state":
        problems.append("a state-owned export read as %r" % one.get("instate", {}).get("owner"))
    if one.get("split", {}).get("commented") is not True:
        problems.append("a commented window did not read as commented")
    if one.get("truncated", {}).get("script") != "body":
        problems.append("a stub with a compiled body read as %r" % one.get("truncated", {}).get("script"))
    if one.get("stub", {}).get("script") != "empty":
        problems.append("an empty script read as %r" % one.get("stub", {}).get("script"))
    if not empty_script(empty) or empty_script(full) or empty_script(b""):
        problems.append("the empty-script test does not separate the two payloads")
    # a Net function serialises `RepOffset` in front of its flags, so its trailer is nine bytes and the empty
    # script sits one field earlier: the test has to be trailer-aware or those stubs read as bodied
    empty_net = empty[:-7] + b"\x00\x00" + empty[-7:]
    if not empty_script(empty_net):
        problems.append("an empty script with a Net trailer was not read")
    net = full[:-7] + b"\x00\x00" + full[-7:]
    if empty_script(net):
        problems.append("a Net trailer made a bodied script read as empty")
    if problems:
        for p in problems:
            print("FAIL text-stubs: %s" % p)
        return 1
    # the artifact round-trips: render -> read -> the same block
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        page = os.path.join(tmp, "page.md")
        with open(page, "w", encoding="utf-8") as fh:
            fh.write("intro\n" + DOC_MARK[0] + "\nstale\n" + DOC_MARK[1] + "\ntail\n")
        rows2 = [{"kind": "class", "package": "P", "class": "c", "declarations": 3, "bodies": 1, "stubs": 2,
                  "stubs_empty": 1, "stubs_bodied": 1, "stub_state": 0},
                 {"kind": "stub", "package": "P", "class": "c", "name": "s", "payload": 40, "script": "body",
                  "owner": "class"},
                 {"kind": "census", "name": "stubs", "count": 2, "note": CENSUS_NOTE["stubs"]},
                 {"kind": "census", "name": "declarations", "count": 3, "note": CENSUS_NOTE["declarations"]},
                 {"kind": "census", "name": "bodies", "count": 1, "note": CENSUS_NOTE["bodies"]},
                 {"kind": "census", "name": "stub_empty", "count": 1, "note": CENSUS_NOTE["stub_empty"]},
                 {"kind": "census", "name": "stub_bodied", "count": 1, "note": CENSUS_NOTE["stub_bodied"]}]
        path = os.path.join(tmp, "a.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(render(rows2))
        if read_csv(path) != [{k: str(r.get(k, "")) for k in FIELDS} for r in rows2]:
            print("FAIL text-stubs: the artifact does not round-trip through its own CSV header")
            return 1
        write_doc(page, read_csv(path))
        if check_doc(page, read_csv(path)) != 0:
            return 1
        os.unlink(page)
    print("ok   text-stubs: a bodyless declaration, an #endif split, a truncated text, a state export and a"
          " forward declaration each read as themselves, and the block reproduces from its CSV")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--system", help="the dump's System directory")
    ap.add_argument("-o", "--out", default=ARTIFACT, help="the artifact (default inventory/text_stubs.csv)")
    ap.add_argument("--write-doc", metavar="PAGE", help="render the generated block into this page")
    ap.add_argument("--check-doc", metavar="PAGE", help="fail when the page's block is stale")
    ap.add_argument("--report", action="store_true", help="print the census, no write")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; needs no dump and writes nothing")
    args = ap.parse_args()
    if args.check:
        return selftest()
    if args.check_doc:
        return check_doc(args.check_doc, read_csv(args.out))
    if not args.system:
        ap.error("a measurement needs --system (or --check / --check-doc)")
    rows = measure(args.system)
    if args.write_doc:
        write_doc(args.write_doc, rows)
    if args.report or not args.write_doc:
        print(doc_block(rows).strip())
    if not args.report:
        with open(args.out, "w", encoding="utf-8", newline="") as fh:
            fh.write(render(rows))
        print("wrote %s (%d rows)" % (args.out, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())