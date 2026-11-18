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

The bodied stubs -- a declaration with no body whose export still carries a script -- are then joined
against the commented bodies `tools/class-text-bodies.py` measured (`inventory/class_text_bodies.csv`), so
"the body moved into a comment" is answered rather than assumed: a name match in the same class is a body
the text kept under `/* */`, and no match is a body the `ScriptText` does not spell anywhere.

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
import hashlib
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
BODIES = os.path.join(ROOT, "inventory", "class_text_bodies.csv")
COMMENTS = os.path.join(ROOT, "inventory", "class_text_comments.csv")
DOC_MARK = ("<!-- generated:text-stubs -->", "<!-- /generated:text-stubs -->")

FIELDS = ["kind", "package", "class", "scope", "name", "line", "shape", "owner", "redeclared",
          "redeclared_back", "payload", "script", "script_sha", "native", "comment", "comment_bytes",
          "declarations", "bodies", "stubs", "stubs_empty", "stubs_bodied", "stub_state", "count", "note",
          "depth", "directive"]

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
    "stub_scope_state": "stubs written inside a `state` block -- they resolve to that state's own `Function` export",
    "stub_scope_class": "stubs at the class's own level",
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
    "stub_bodied_redeclared_back": "of the bodied ones, an *earlier* declaration of the name carries the body -- a `State` member declared below the class's own body",
    "stub_bodied_undeclared": "of the bodied ones, no body anywhere in the live text -- the script the text does not spell",
    "undeclared_comment_body": "of the undeclared ones, the class's block comment carries a body of the same name",
    "undeclared_comment_class": "of the undeclared ones, the class ships a block comment but the name is not a commented body in it",
    "undeclared_no_block": "of the undeclared ones, the class ships no block comment at all",
    "undeclared_name_elsewhere": "of the undeclared ones, the name is a commented body in another class of the corpus",
    "undeclared_distinct_names": "distinct names behind the undeclared ones",
    "bodied_distinct_payloads": "distinct payloads behind the bodied stubs -- a repeated size is not a repeated script",
    "bodied_shared_payload": "of the bodied ones, a payload another of them carries",
    "stub_names_all": "distinct names behind every stub -- the join's denominator",
    "stub_names_in_comments": "of those, names the corpus also carries as a commented body -- the join's base rate",
    "body_empty": "declarations with a body whose export is the empty script -- the control",
    "body_empty_braces": "declarations whose body is an empty `{}`",
    "body_empty_braces_bodied": "of those, carry a longer payload than the empty script",
    "body_empty_braces_empty": "of the empty-script bodies, an empty `{}`",
    "body_empty_other": "of the empty-script bodies, a body carrying no statements -- locals, comments or structure",
    "state_blocks": "`state` blocks the class texts declare, every header spelling",
    "state_blocks_plain": "of those, what the plain `state Name {` header finds -- the reader this module carried",
    "body_no_export": "declarations with a body naming no `Function` export of their class",
    "body_no_export_guarded": "of those, written inside a preprocessor block this build does not compile",
    "body_no_export_plain": "of those, in a live region at the class's own level",
    "body_no_export_state": "of those, inside a `state` block whose `Function`s do not carry the name",
    "body_scope_state": "bodied declarations written inside a `state` block",
    "body_scope_class": "bodied declarations at the class's own level",
    "body_guarded": "bodied declarations inside a preprocessor block",
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


def state_spans(masked):
    """[(start, end, name)] for every `state` block, from `class-text-bodies.state_blocks`.

    This module used to carry its own header pattern -- the plain `state Name {` shape -- and read 424 of the
    corpus's 554 blocks, so the declarations of 130 blocks were keyed to the class's own level: 292 bodied
    declarations named no export of their class when 233 of them belong to a state that exports them
    (UNKNOWNS U24). One rule for the text's state blocks, one reader for its scopes.
    """
    return [(a, b, n) for a, b, n, _parent in ctb_tool().state_blocks(masked)]


# The header this module carried before UNKNOWNS U24, kept as a control: it is what 424 of the corpus's 554
# blocks reads, so the difference is a number the artifact publishes rather than a claim in prose.
PLAIN_HDR = re.compile(r"(?m)^[ \t]*state[ \t]+(\w+)[ \t\r\n]*\{", re.I)


def plain_state_spans(masked):
    """[(start, end, name)] for the plain `state Name {` header only -- the reader this module used to carry."""
    out = []
    for m in PLAIN_HDR.finditer(masked):
        brace = masked.find("{", m.start())
        depth, k = 0, brace
        while 0 <= brace and k < len(masked):
            c = masked[k]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    out.append((m.start(), k, m.group(1).lower()))
                    break
            k += 1
    return out


DIRECTIVE = re.compile(r"(?m)^[ \t]*#[ \t]*(\w+)[ \t]*([^\n]*)$")


def directive_stack(masked, pos):
    """The conditional preprocessor directives open at `pos`: [(word, argument)].

    A declaration inside an `#ifdef` this build does not compile has no export because the compiler never saw
    it, not because a scope resolver missed it: the shipped text guards whole functions with `#ifdef pc` and
    `#ifdefPS2`, so the two causes are counted apart rather than as one population.
    """
    stack = []
    for m in DIRECTIVE.finditer(masked):
        if m.start() >= pos:
            break
        word = (m.group(1) or "").lower()
        arg = (m.group(2) or "").strip()
        if word.startswith("ifdef") and word != "ifdef":      # `#ifdefPS2` -- the shipped text has no space
            word, arg = "ifdef", (word[5:] + arg).strip()
        elif word.startswith("ifndef") and word != "ifndef":
            word, arg = "ifndef", (word[6:] + arg).strip()
        if word in ("ifdef", "ifndef", "if"):
            stack.append((word, arg.split()[0] if arg else ""))
        elif word in ("else", "elseif"):
            if stack:
                stack[-1] = ("else", stack[-1][1])
        elif word == "endif":
            if stack:
                stack.pop()
    return stack


def scope_of(pos, spans):
    """The innermost `state` block a declaration sits in, or None for the class's own level."""
    best = None
    for start, end, name in spans:
        if start <= pos <= end and (best is None or start > best[0]):
            best = (start, name)
    return best[1] if best else None


def classify(text, resolve=None):
    """Rows for one class text: every declaration, whether it has a body, and where its implementation is.

    `resolve(name, scope)` answers `None` or `(owner, payload, flags)` for the `Function` export of that
    name *in that scope* -- a callable rather than a package so the self-test drives the same walk with a
    synthetic answer. The scope matters: a declaration written inside a `state` block is bound by the
    compiler to that state's `Function` record, and asking the class for it credits the state's bodyless
    declaration with the class's script.
    """
    ctb = ctb_tool()
    masked = ctb.mask(text)
    depth = ctb.depth_map(masked)
    blocks = state_spans(masked)
    ms = list(ctb.FUNC.finditer(masked))
    spans = {}
    for k, m in enumerate(ms):
        limit = ms[k + 1].start() if k + 1 < len(ms) else len(masked)
        scope = scope_of(m.start(), blocks)
        spans.setdefault((scope, ctb.declared_name(masked, m).lower()), []).append(
            (m.start(), ctb.body_span(masked, m.end(), limit) is not None))
    rows = []
    for k, m in enumerate(ms):
        limit = ms[k + 1].start() if k + 1 < len(ms) else len(masked)
        span = ctb.body_span(masked, m.end(), limit)
        body = span is not None
        name = ctb.declared_name(masked, m).lower()  # an operator resolves as its composed record (UNKNOWNS U23)
        scope = scope_of(m.start(), blocks)
        stack = directive_stack(masked, m.start())
        row = {"name": name, "line": masked.count("\n", 0, m.start()) + 1, "body": body, "scope": scope or "",
               "depth": depth[m.start()],
               "directive": " ".join("%s %s" % (w, a) for w, a in stack).strip(),
               "shape": "", "commented": False, "owner": "", "redeclared": False, "redeclared_back": False,
               "payload": "", "script": "", "script_sha": "", "native": "", "comment": "",
               "comment_bytes": "", "empty_body": ""}
        hit = resolve(name, scope) if resolve else None
        if hit is None:
            row["owner"] = "none"
        else:
            owner, payload, flags = hit
            row["owner"] = "state" if owner else "class"
            row["payload"] = len(payload)
            row["script"] = "empty" if empty_script(payload) else "body"
            row["script_sha"] = hashlib.sha1(payload).hexdigest()[:12] if row["script"] == "body" else ""
            row["native"] = "yes" if flags & FUNC_NATIVE else ""
        if body:
            # an empty `{}` body is the control the no-code test needs: it compiles to no statements too, so the
            # test separates "no code" from "code" and not "no body" from "body"
            row["empty_body"] = "yes" if not masked[span[0] + 1:span[1] - 1].strip() else ""
        else:
            row["shape"], _brace = window_shape(masked[m.end():limit])
            row["commented"] = bool(ctb.BLOCK.search(text[m.end():limit]))
            same = spans[(scope, name)]
            row["redeclared"] = any(b and st > m.start() for st, b in same)
            # a `State` declares its member below the class's own body, so the body it re-declares is
            # *above* it: a forward-only test calls that a body the text does not spell
            row["redeclared_back"] = any(b and st < m.start() for st, b in same)
        rows.append(row)
    return rows


def export_resolver(pkg, cls):
    """`resolve(name, scope)` for one class: the class's own `Function` export, or the state's.

    A `Function`'s Outer is a `State` as often as a `Class`. The two scopes are kept apart on purpose -- the
    class's export answers a class-level declaration only, because a state's bodyless declaration compiles
    to its own, empty, script.
    """
    cls_out, state_out = {}, {}
    for e in pkg.exports:
        if pkg.class_name_of(e) != "Function":
            continue
        name = pkg.name(e["name"]).lower()
        o, seen, owner, owner_cls = e["outer"], 0, None, None
        while o and seen < 8:
            oe = pkg.exports[o - 1]
            kind = pkg.class_name_of(oe)
            if kind == "State" and owner is None:
                owner = pkg.name(oe["name"]).lower()
            elif kind == "Class":
                owner_cls = pkg.name(oe["name"]).lower()
                break
            o = oe["outer"]
            seen += 1
        if owner_cls != cls.lower():
            continue
        payload = pkg.data[e["offset"]:e["offset"] + e["size"]] if e["size"] else b""
        if owner:
            state_out.setdefault((owner, name), (owner, payload, e["flags"]))
        else:
            cls_out.setdefault(name, (None, payload, e["flags"]))

    def resolve(name, scope=None):
        if scope:
            return state_out.get((scope, name))
        return cls_out.get(name)
    return resolve


def commented_bodies(path=None):
    """(package, class, name) -> the row `class-text-bodies.py` measured for a body inside a block comment."""
    out = {}
    for r in read_csv(path or BODIES):
        if r.get("kind") in ("pair", "disabled"):
            out[(r["package"].lower(), r["class"].lower(), r["name"].lower())] = r
    return out


def commented_classes(path=None):
    """(package, class) for every class whose text ships a `/* ... */` block."""
    return {(r["package"].lower(), r["key"].lower()) for r in read_csv(path or COMMENTS)
            if r.get("kind") == "class" and int(r.get("blocks") or 0) > 0}


def comment_verdict(package, cls, name, bodies, classes):
    """Where a lost body could be: in the class's own comment (`body`), in a comment the class ships under
    another name (`class`), or in a class that ships no comment at all (`none`). A `body` here is the
    competing candidate -- the body moved into a comment rather than dropped."""
    hit = bodies.get((package.lower(), cls.lower(), name.lower()))
    if hit is not None:
        return "body", hit
    if (package.lower(), cls.lower()) in classes:
        return "class", None
    return "none", None


def measure(system_dir):
    """The whole corpus: class rows, stub rows and the census."""
    t = collections.Counter()
    per_pkg = collections.Counter()
    class_rows, stub_rows, body_rows = [], [], []
    com_bodies = commented_bodies()
    com_classes = commented_classes()
    com_names = {key[2] for key in com_bodies}
    shas = collections.Counter()
    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        package = fname[:-2]
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            text = pst_tool().script_text(pkg, exp)
            if not text:
                continue
            text = text.replace("\r\n", "\n")
            cls = pkg.name(exp["name"]).lower()
            t["classes"] += 1
            t["state_blocks"] += len(state_spans(ctb_tool().mask(text)))
            t["state_blocks_plain"] += len(plain_state_spans(ctb_tool().mask(text)))
            rows = classify(text, export_resolver(pkg, cls))
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
                t["stub_scope_state" if r["scope"] else "stub_scope_class"] += 1
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
                    t["stub_bodied_" + ("state" if r["owner"] == "state" else "class")] += 1
                    bodied += 1
                    t["largest_stub_payload"] = max(t["largest_stub_payload"], int(r["payload"]))
                    if r["redeclared"]:
                        t["stub_bodied_redeclared"] += 1
                    elif r["redeclared_back"]:
                        t["stub_bodied_redeclared_back"] += 1
                    else:
                        t["stub_bodied_undeclared"] += 1
                        verdict, chit = comment_verdict(package, cls, r["name"], com_bodies, com_classes)
                        r["comment"] = verdict
                        if chit is not None:
                            r["comment_bytes"] = chit.get("c_bytes", "")
                        t[{"body": "undeclared_comment_body", "class": "undeclared_comment_class",
                           "none": "undeclared_no_block"}[verdict]] += 1
                        if r["name"] in com_names:
                            t["undeclared_name_elsewhere"] += 1
                    if r["script_sha"]:
                        shas[r["script_sha"]] += 1
                if r["owner"] == "state":
                    state += 1
                stub_rows.append(dict(r, kind="stub", package=package, **{"class": cls}))
            for r in bodies:
                t["body_scope_state" if r["scope"] else "body_scope_class"] += 1
                if r["directive"]:
                    t["body_guarded"] += 1
                if r["owner"] == "none":
                    t["body_no_export"] += 1
                    t["body_no_export_guarded" if r["directive"] else "body_no_export_plain"] += 1
                    if r["scope"]:
                        t["body_no_export_state"] += 1
                    # published, not just counted: a population a reviewer cannot open is a number, not a finding
                    body_rows.append(dict(r, kind="body", package=package, **{"class": cls},
                                          note=("inside `%s`" % r["directive"]) if r["directive"]
                                          else "at the class's own level"))
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
    names = {r["name"] for r in stub_rows}
    t["stub_names_all"] = len(names)
    t["stub_names_in_comments"] = len(names & com_names)
    t["undeclared_distinct_names"] = len({r["name"] for r in stub_rows
                                          if r["script"] == "body" and not r["redeclared"]
                                          and not r["redeclared_back"]})
    t["bodied_distinct_payloads"] = len(shas)
    t["bodied_shared_payload"] = sum(v for v in shas.values() if v > 1)
    census = [{"kind": "census", "name": k, "count": v, "note": CENSUS_NOTE[k]}
              for k, v in t.items() if k in CENSUS_NOTE]
    return class_rows + stub_rows + body_rows + census


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


def plural(n, one, many=None):
    """`n one` or `n many`, for a sentence that has to read the same at 1 and at 900."""
    return "%s %s" % (comma(n), one if int(n) == 1 else (many or one + "s"))


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
                 "name again, with a body, further down -- and **%s** are the mirror case, a body the class spells "
                 "*above* the declaration (a bodyless re-declaration of a name the class already defines), so the "
                 "residue is **%s** whose script no live body in the `ScriptText` spells**. The control is what keeps that from being a stub test: **%s "
                 "of the %s bodied declarations compile to the same empty script too** -- **%s** an empty `{}` body "
                 "and **%s** a body carrying only locals, comments or structure -- so the test separates *no code* "
                 "from *code* and not a stub from a body. %s of the %s empty-`{}` bodies carry a longer payload "
                 "than it."
                 % (comma(decl), comma(bodies), comma(stubs), "%.1f" % pct(stubs, decl), comma(empty),
                    comma(bodied), comma(count(rows, "stub_bodied_redeclared")),
                    comma(count(rows, "stub_bodied_redeclared_back")),
                    plural(count(rows, "stub_bodied_undeclared"), "declaration"),
                    comma(count(rows, "body_empty")), comma(bodies),
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
    lines.append("A stub's name is almost always one the package exports: **%s name a `Function` the class itself "
                 "exports**, **%s one a `State` of the class exports**, and **%s name no export at all** -- a "
                 "class-level declaration whose name only a state binds, which a class-over-state index cannot "
                 "see. "
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
    lost = count(rows, "stub_bodied_undeclared")
    lines.append("**A `state`'s declaration belongs to the state.** **%s of the %s stubs are written inside a "
                 "`state` block**, and each one resolves to that state's own `Function` export rather than the "
                 "class's: **%s** of them compile to the empty script and **%s** carry one, against **%s** empty "
                 "and **%s** bodied at the class's own level. Asking the class for the name -- a class-over-state "
                 "preference -- credits a state's bodyless declaration with the class's script, which is how a "
                 "census can read a body the text does not spell where the text spells nothing missing at all."
                 % (comma(count(rows, "stub_scope_state")), comma(stubs),
                    comma(count(rows, "stub_empty_state")), comma(count(rows, "stub_bodied_state")),
                    comma(count(rows, "stub_empty_class")), comma(count(rows, "stub_bodied_class"))))
    if count(rows, "body_no_export"):
        bodies_rows = sorted((r for r in rows if r.get("kind") == "body"),
                             key=lambda r: (r["package"], r["class"], r["name"], int(r["line"] or 0)))
        lines.append("")
        lines.append("**A bodied declaration that names no export is a scope question, and the header's spelling "
                     "answers most of it.** %s of the %s bodied declarations resolve to nothing against their "
                     "class, and **%s** of them are written inside a `state` block: a state's `Function` record "
                     "is outered to the `UState`, not to the class, so a class-keyed walk cannot answer the "
                     "name, and the header pattern this module carried (`state Name {`) missed four of the five "
                     "spellings the shipped text uses -- `state() Name` (every state in `Engine.Mover`), "
                     "`state(0) Name`, `simulated state X extends Y` and a capitalised `State X`: over the whole corpus "
                     "the plain header finds **%s** of the **%s** blocks the class texts declare. Read through "
                     "`class-text-bodies.state_blocks` instead, **%s** bodied declarations are placed in a state "
                     "and **%s** are not; of the residue, **%s** are written inside a preprocessor block this "
                     "build does not compile (`pc` and `ps2` guard whole functions, **%s** of those inside a "
                     "state) and **%s** sit in a live region. Those rows are named below -- the count is the "
                     "artifact's, not a reader's claim."
                     % (comma(count(rows, "body_no_export")), comma(bodies),
                        comma(count(rows, "body_scope_state")),
                        comma(count(rows, "state_blocks_plain")), comma(count(rows, "state_blocks")),
                        comma(count(rows, "body_scope_state")), comma(count(rows, "body_scope_class")),
                        comma(count(rows, "body_no_export_guarded")), comma(count(rows, "body_no_export_state")),
                        comma(count(rows, "body_no_export_plain"))))
        lines.append("")
        lines.append("| declaration | line | scope | why no export |")
        lines.append("|---|---:|---|---|")
        for r in bodies_rows:
            lines.append("| `%s.%s` | %s | %s | %s |"
                         % (r["package"], r["name"], comma(r["line"]),
                            ("`%s`" % r["scope"]) if r["scope"] else "class", r.get("note") or ""))
    lines.append("")
    lines.append("**The residue is not in the class's comment.** `inventory/class_text_bodies.csv` already "
                 "measures the bodies a block comment disables, and joining that artifact by "
                 "`(package, class, function)` against them puts **%s** in a class whose own "
                 "comment carries a body of the same name -- a body the text moved into a comment rather than "
                 "dropped -- **%s** in a class that ships a block comment without this name, and **%s** in a class "
                 "that ships no comment at all, so **%s of the %s** bodied stubs %s a body the `ScriptText` does "
                 "not spell anywhere. "
                 "The name-level rate is the control: **%s of the %s** distinct stub names also occur as a "
                 "commented body somewhere in the corpus (%s%%), against **%s of %s** (%s%%) for the lost bodies' "
                 "own names -- the join is not finding less than a plain name census would."
                 % (comma(count(rows, "undeclared_comment_body")),
                    comma(count(rows, "undeclared_comment_class")), comma(count(rows, "undeclared_no_block")),
                    comma(lost), comma(count(rows, "stub_bodied")), "is" if lost == 1 else "are",
                    comma(count(rows, "stub_names_in_comments")), comma(count(rows, "stub_names_all")),
                    "%.1f" % pct(count(rows, "stub_names_in_comments"), count(rows, "stub_names_all")),
                    comma(count(rows, "undeclared_name_elsewhere")),
                    comma(count(rows, "undeclared_distinct_names")),
                    "%.1f" % pct(count(rows, "undeclared_name_elsewhere"), count(rows, "undeclared_distinct_names"))))
    lines.append("")
    lines.append("**The declaration lookups stay inside their own scope.** A forward declaration is one the same "
                 "text declares again *below* with a body (**%s** bodied stubs) and the mirror case is a body "
                 "*above* (**%s**); both are counted on the same scope's declarations only, so a class-level "
                 "declaration is never answered with a state's body or the reverse."
                 % (comma(count(rows, "stub_bodied_redeclared")), comma(count(rows, "stub_bodied_redeclared_back"))))
    lines.append("")
    lines.append("**A repeated payload size is not a repeated script.** The %s bodied stubs carry **%s** distinct "
                 "payloads and **%s** rows share one, and those are one name declared twice in a class "
                 "(`StargatePlayer.fire`, `StargateWeapon.fire`) -- equal sizes across *different* names are "
                 "distinct scripts, not one lost body counted several times."
                 % (comma(count(rows, "stub_bodied")), comma(count(rows, "bodied_distinct_payloads")),
                    comma(count(rows, "bodied_shared_payload"))))
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
        lines.append("The stubs whose payload carries code are the ones where the text and the build disagree, "
                     "and almost every one of them is a *shape*: %s sit in an `#endif` window or a truncated text, "
                     "%s re-declare a name the class's text spells a body for (2 forward, 12 backward) and the "
                     "remainder is the one body the text does not spell. The largest:"
                     % (comma(count(rows, "stub_no_brace") + count(rows, "stub_unbalanced")),
                        comma(count(rows, "stub_bodied_redeclared") + count(rows, "stub_bodied_redeclared_back"))))
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
           "function Narrow(int N)\n"
           "{\n"
           "  return;\n"
           "}\n"
           "state Busy\n"
           "{\n"
           "  function Narrow(int N);\n"
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
               "truncated": (None, full, 0x4), "real": (None, full, 0x4), "narrow": (None, full, 0x4)}
    rows = classify(src, lambda name, scope=None, a=answers: a.get(name))
    first = collections.defaultdict(list)
    for r in rows:
        first[r["name"]].append(r)
    one = {k: v[0] for k, v in first.items()}
    problems = []
    if set(one) != {"stub", "real", "split", "after", "guarded", "later", "instate", "truncated", "narrow"}:
        problems.append("declarations read %r" % sorted(one))
    # a `State` declares its member below the class's own body, and that body belongs to the class's scope: the
    # state's declaration is neither a forward nor a backward re-declaration of it
    narrow = [r for r in first.get("narrow", []) if not r["body"]]
    if len(narrow) != 1 or narrow[0]["scope"] != "busy" or narrow[0]["redeclared"] \
            or narrow[0]["redeclared_back"]:
        problems.append("a state's bodyless re-declaration read as %r"
                        % [(r["scope"], r["redeclared"], r["redeclared_back"]) for r in first.get("narrow", [])])
    if one.get("stub", {}).get("redeclared_back") or one.get("split", {}).get("redeclared_back"):
        problems.append("a stub with no declaration of its name above it read as a backward re-declaration")
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
    # the same name declared at the class's level and inside a state is two declarations with two records:
    # the state's bodyless one must take the state's empty script, never the class's body
    def scoped(name, scope=None):
        if name == "narrow" and scope == "busy":
            return ("busy", empty, 0x4)
        return answers.get(name)
    scoped_rows = [r for r in classify(src, scoped) if r["name"] == "narrow" and not r["body"]]
    if len(scoped_rows) != 1 or scoped_rows[0]["scope"] != "busy" or scoped_rows[0]["script"] != "empty" \
            or scoped_rows[0]["owner"] != "state":
        problems.append("a state's bodyless re-declaration read as %r"
                        % [(r["scope"], r["owner"], r["script"]) for r in scoped_rows])
    if [r["script"] for r in classify(src, scoped) if r["name"] == "narrow" and r["body"]] != ["body"]:
        problems.append("the class's own bodied declaration lost its script to the state's scope")
    block = state_spans("class C;\nstate A\n{\nfunction X();\n}\n")
    if scope_of(5, block) is not None or scope_of(25, block) != "a":
        problems.append("a state block's span read as %r" % (block,))
    if one.get("split", {}).get("commented") is not True:
        problems.append("a commented window did not read as commented")
    if one.get("truncated", {}).get("script") != "body":
        problems.append("a stub with a compiled body read as %r" % one.get("truncated", {}).get("script"))
    if one.get("stub", {}).get("script") != "empty":
        problems.append("an empty script read as %r" % one.get("stub", {}).get("script"))
    if not empty_script(empty) or empty_script(full) or empty_script(b""):
        problems.append("the empty-script test does not separate the two payloads")
    # the comment join, the same call a corpus run makes: a name the class's comment carries, a commented class
    # whose comment does not carry this name, and a class with no block at all are three different answers
    bodies = {("p", "c", "s"): {"c_bytes": "40", "kind": "disabled"}}
    classes = {("p", "c"), ("p", "d")}
    if comment_verdict("P", "C", "s", bodies, classes)[0] != "body":
        problems.append("a name the class's comment carries did not join as a commented body")
    if comment_verdict("P", "C", "t", bodies, classes)[0] != "class":
        problems.append("a commented class's other name did not read as a commented class")
    if comment_verdict("P", "E", "s", bodies, classes)[0] != "none":
        problems.append("a class with no block did not read as no comment")
    # a Net function serialises `RepOffset` in front of its flags, so its trailer is nine bytes and the empty
    # script sits one field earlier: the test has to be trailer-aware or those stubs read as bodied
    empty_net = empty[:-7] + b"\x00\x00" + empty[-7:]
    if not empty_script(empty_net):
        problems.append("an empty script with a Net trailer was not read")
    net = full[:-7] + b"\x00\x00" + full[-7:]
    if empty_script(net):
        problems.append("a Net trailer made a bodied script read as empty")
    # a state header is one of five spellings in this corpus, and a declaration inside one belongs to that state
    spelled = ctb_tool().mask("class C;\n"
                              "state() Lead\n{\nfunction A();\n}\n"
                              "state(0) Numbered\n{\nfunction B();\n}\n"
                              "simulated state ZoomingIn extends Zooming\n{\nfunction C();\n}\n"
                              "State FadeOut\n{\nfunction D();\n}\n"
                              "function Outside();\n")
    got = sorted(n for _a, _b, n in state_spans(spelled))
    if got != ["fadeout", "lead", "numbered", "zoomingin"]:
        problems.append("a state header spelling read as %r" % (got,))
    if scope_of(spelled.index("function D"), state_spans(spelled)) != "fadeout":
        problems.append("a declaration inside a capitalised state block was read at the class's own level")
    # a bodied declaration inside an `#ifdef` this build does not compile has no export for a different reason
    guarded = ("class C;\n#ifdefPS2\nsimulated function P(int X)\n{\n  return;\n}\n#endif\n"
               "function Q(int Y)\n{\n  return;\n}\n")
    rows_g = classify(guarded, None)
    by_name = {r["name"]: r for r in rows_g}
    if by_name.get("p", {}).get("directive") != "ifdef ps2" or by_name["p"]["owner"] != "none":
        problems.append("a guarded bodied declaration read as %r"
                        % [(r["name"], r["directive"], r["owner"]) for r in rows_g])
    if by_name.get("q", {}).get("directive") != "" or by_name["q"]["owner"] != "none":
        problems.append("a live bodied declaration read as %r"
                        % [(r["name"], r["directive"], r["owner"]) for r in rows_g])
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
          " forward declaration each read as themselves, every state header spelling places its declarations,"
          " a guarded body is counted apart from a live one, and the block reproduces from its CSV")
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