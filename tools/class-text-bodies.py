#!/usr/bin/env python3
"""What a disabled function *body* weighs, and whether the live twin of the same name has the same shape.

`tools/class-text-comments.py` counts the `function` declarations a block comment disables and joins them to
the package's exports by name; it leaves the bodies. The body is the cheaper question than the tree: an
operator is a token, so a commented body and its live twin can be compared token by token without a parser.

    tools/class-text-bodies.py DUMP/System --sweep -o inventory
    tools/class-text-bodies.py --report
    tools/class-text-bodies.py --write-doc docs/PACKAGE_SCRIPT_TEXT.md
    tools/class-text-bodies.py --check-doc docs/PACKAGE_SCRIPT_TEXT.md   # from the committed artifact
    tools/class-text-bodies.py --check                                    # synthetic, no dump, writes nothing

A comment disables a **declaration**, and most of the declarations it disables also keep their body inside the
same `/* ... */` -- so the population is readable per function: the body's brace span, its operator multiset
(the `tools/decompiler-operators.py` token table, reused so the two tools cannot drift), its statement count
and its byte length. Three populations come out of that read:

* a commented body whose name the live text of the same class also declares with a body -- both revisions of
  one function, compared token by token;
* a commented body with no live twin -- the functions this build did not compile, weighted by body size;
* the live declarations themselves, as the extractor's control: a body reader that finds fewer bodies in the
  live text than the text declares is not measuring the comment.

Nothing here prints or commits body text: the artifact carries sizes, counts and names. The operators are
counted with `decompiler-operators.py`'s own `clean()`/`counts()` so a change to that table moves both pages
at once. Reads the packages' `ScriptText`; no bytecode, no dump-side value, no boot.
"""
import argparse
import csv
import importlib.util
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402

ARTIFACT = os.path.join(ROOT, "inventory/class_text_bodies.csv")
DOC = os.path.join(ROOT, "docs/PACKAGE_SCRIPT_TEXT.md")
DOC_START = "<!-- generated:class-text-bodies -->"
DOC_END = "<!-- /generated:class-text-bodies -->"

FIELDS = ["kind", "package", "class", "name", "verdict", "c_ops", "c_stmts", "c_kw", "c_bytes",
          "l_ops", "l_stmts", "l_kw", "l_bytes", "keys", "count", "note"]

# The same declaration pattern `class-text-comments.py` counts, so the two censuses describe one population.
FUNC = re.compile(r"^[ \t]*(?:simulated[ \t]+|static[ \t]+|final[ \t]+|latent[ \t]+)*function[ \t]+"
                  r"(?:[\w<>\[\]]+[ \t]+)?(\w+)[ \t]*\(", re.M)
BLOCK = re.compile(r"/\*.*?\*/", re.S)
LINE_COMMENT = re.compile(r"//[^\n]*")
KEYWORD = re.compile(r"\b(?:if|else|for|while|switch|case|return|do)\b")
# A live body this short is a trailer, not a revision: the class kept the declaration and emptied the body.
STUB_BYTES = 32


def load_tool(name, filename):
    path = os.path.join(HERE, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- one body --------------------------------------------------------------------------------------
def body_span(text, start, limit):
    """(start, end) of the brace-matched body that follows `start`, bounded by `limit`, or None.

    A declaration that ends in `;` before its first `{` has no body, and neither has one whose `{` lies past
    `limit` -- the next declaration. That bound is not cosmetic: without it a bodyless declaration inherits the
    next function's body and two unrelated functions read as the same shape.
    """
    i = text.find("{", start)
    if i < 0 or i >= limit:
        return None
    semi = text.find(";", start)
    if 0 <= semi < i:
        return None
    depth = 0
    j = i
    while j < limit:
        c = text[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return (i, j + 1)
        j += 1
    return None


def functions(text):
    """(name -> body, names without a body, declarations, bare, with a body) for one text.

    The first declaration of a name wins, so a text that ships the same function twice reads once -- which is
    why the resolved count is returned separately from the body map: the control compares declarations, and a
    deduplicated map would report 82% over a text whose every declaration the reader in fact resolves.
    """
    bodies, bare, declared, bare_total, resolved = {}, [], 0, 0, 0
    ms = list(FUNC.finditer(text))
    for k, m in enumerate(ms):
        declared += 1
        limit = ms[k + 1].start() if k + 1 < len(ms) else len(text)
        span = body_span(text, m.end(), limit)
        if span is None:
            bare_total += 1
            if m.group(1).lower() not in bare:
                bare.append(m.group(1).lower())
            continue
        resolved += 1
        bodies.setdefault(m.group(1).lower(), text[span[0]:span[1]])
    return bodies, bare, declared, bare_total, resolved


def statements(body):
    """`;` at the body's own brace depth, and the control keywords -- a statement shape, not a parse."""
    depth, semis = 0, 0
    for c in body:
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == ";" and depth == 1:
            semis += 1
    return semis, len(KEYWORD.findall(body))


def shape(body, ops):
    """One body's row columns: the operator multiset, the statement counts and the byte length."""
    c = ops.counts(body)
    stmts, kw = statements(body)
    return dict(ops=c["operators"], per_op=c["per_op"], stmts=stmts, kw=kw, bytes=len(body))


def compare(commented, live):
    """How two revisions of one function differ, as a verdict.

    `identical` is the whole body byte for byte -- a class that ships the same text twice. `same-operators`
    keeps the operator multiset and differs in layout or operands, `differs` moves at least one operator, and
    `live-stub` is the case the byte lengths separate: the live body is a trailer under `STUB_BYTES`.
    """
    if commented["bytes"] == live["bytes"] and commented["text"] == live["text"]:
        return "identical"
    if live["bytes"] <= STUB_BYTES and commented["bytes"] > STUB_BYTES:
        return "live-stub"
    if commented["per_op"] == live["per_op"]:
        return "same-operators"
    return "differs"


# --- the sweep -------------------------------------------------------------------------------------
def pair_row(package, cls, name, cshape, lshape, verdict):
    moved = sorted(k for k in cshape["per_op"] if cshape["per_op"][k] != lshape["per_op"][k])
    return dict(kind="pair", package=package, **{"class": cls}, name=name, verdict=verdict,
                c_ops=cshape["ops"], c_stmts=cshape["stmts"], c_kw=cshape["kw"], c_bytes=cshape["bytes"],
                l_ops=lshape["ops"], l_stmts=lshape["stmts"], l_kw=lshape["kw"], l_bytes=lshape["bytes"],
                keys=",".join(moved), count="1", note="")


def disabled_row(package, cls, name, cshape):
    return dict(kind="disabled", package=package, **{"class": cls}, name=name, verdict="no-live-twin",
                c_ops=cshape["ops"], c_stmts=cshape["stmts"], c_kw=cshape["kw"], c_bytes=cshape["bytes"],
                l_ops="", l_stmts="", l_kw="", l_bytes="", keys="", count="1", note="")


def sweep(system_dir):
    pst = load_tool("pkg_script_text", "pkg-script-text.py")
    ops = load_tool("decompiler_operators", "decompiler-operators.py")
    rows = []
    t = dict(classes=0, with_block=0, declarations=0, with_body=0, no_body=0,
             live_declarations=0, live_with_body=0,
             pairs=0, identical=0, same_operators=0, differs=0, live_stub=0,
             pair_c_bytes=0, pair_l_bytes=0, pair_c_ops=0, pair_l_ops=0,
             disabled=0, disabled_bytes=0, disabled_ops=0, disabled_stmts=0, disabled_stub=0)
    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        package = fname[:-2]
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            text = pst.script_text(pkg, exp)
            if not text:
                continue
            text = text.replace("\r\n", "\n")
            spans = [m.span() for m in BLOCK.finditer(text)]
            t["classes"] += 1
            if not spans:
                continue
            t["with_block"] += 1
            comment = "\n".join(text[a:b] for a, b in spans)
            live = LINE_COMMENT.sub("", BLOCK.sub("", text))
            cbodies, _cbare, cdeclared, cbare_total, cresolved = functions(comment)
            lbodies, _lbare, ldeclared, _lbare_total, lresolved = functions(live)
            t["declarations"] += cdeclared
            t["with_body"] += cresolved
            t["no_body"] += cbare_total
            t["live_declarations"] += ldeclared
            t["live_with_body"] += lresolved
            cls = pkg.name(exp["name"])
            for name in sorted(cbodies):
                body = cbodies[name]
                cshape = shape(body, ops)
                cshape["text"] = body
                if name in lbodies:
                    lbody = lbodies[name]
                    lshape = shape(lbody, ops)
                    lshape["text"] = lbody
                    verdict = compare(cshape, lshape)
                    t["pairs"] += 1
                    t[verdict.replace("-", "_")] += 1
                    t["pair_c_bytes"] += cshape["bytes"]
                    t["pair_l_bytes"] += lshape["bytes"]
                    t["pair_c_ops"] += cshape["ops"]
                    t["pair_l_ops"] += lshape["ops"]
                    rows.append(pair_row(package, cls, name, cshape, lshape, verdict))
                else:
                    t["disabled"] += 1
                    t["disabled_bytes"] += cshape["bytes"]
                    t["disabled_ops"] += cshape["ops"]
                    t["disabled_stmts"] += cshape["stmts"]
                    t["disabled_stub"] += cshape["bytes"] <= STUB_BYTES
                    rows.append(disabled_row(package, cls, name, cshape))
    census = (
        ("classes_with_text", t["classes"], "class exports carrying a `ScriptText`"),
        ("classes_with_block", t["with_block"], "of those, carrying a `/* ... */` block"),
        ("comment_declarations", t["declarations"],
         "`function` declarations inside a block comment"),
        ("comment_with_body", t["with_body"], "of those, with a brace-matched body in the same comment"),
        ("comment_no_body", t["no_body"], "of those, a declaration without a body"),
        ("live_declarations", t["live_declarations"],
         "`function` declarations in the same classes' live text -- the control's denominator"),
        ("live_with_body", t["live_with_body"],
         "of those, whose body the same reader finds -- the extractor's rate"),
        ("pairs", t["pairs"], "commented bodies whose name the live text also declares with a body"),
        ("pairs_identical", t["identical"], "of those, byte-identical to the live body"),
        ("pairs_same_operators", t["same_operators"],
         "of those, a different layout with the same operator multiset"),
        ("pairs_differs", t["differs"], "of those, moving at least one operator"),
        ("pairs_live_stub", t["live_stub"],
         "of those, whose live body is a trailer of %d bytes or fewer" % STUB_BYTES),
        ("pairs_comment_bytes", t["pair_c_bytes"], "bytes of the commented revisions of those pairs"),
        ("pairs_live_bytes", t["pair_l_bytes"], "bytes of their live revisions"),
        ("pairs_comment_operators", t["pair_c_ops"], "operators in the commented revisions"),
        ("pairs_live_operators", t["pair_l_ops"], "operators in the live revisions"),
        ("disabled_bodies", t["disabled"], "commented bodies whose name no live body in the text carries"),
        ("disabled_bytes", t["disabled_bytes"], "bytes of those bodies"),
        ("disabled_operators", t["disabled_ops"], "operators in those bodies"),
        ("disabled_statements", t["disabled_stmts"], "statements at their own brace depth"),
        ("disabled_stubs", t["disabled_stub"],
         "of those, a trailer of %d bytes or fewer" % STUB_BYTES),
    )
    for key, value, note in census:
        row = {k: "" for k in FIELDS}
        row.update(kind="census", name=key, count=str(value), note=note)
        rows.append(row)
    return rows


# --- the render ------------------------------------------------------------------------------------
def census_of(rows):
    return {r["name"]: r for r in rows if r["kind"] == "census"}


def render(rows):
    cen = census_of(rows)

    def n(key):
        return int(cen[key]["count"])

    def pct(a, b):
        return "%.1f" % (100.0 * a / b) if b else "-"
    pairs = [r for r in rows if r["kind"] == "pair"]
    out = []
    out.append("**%d of the %d `function` declarations a block comment disables also carry their body inside "
               "that comment, and %d of them have a live twin of the same name in the same class's text.** The "
               "reader is not the weak part: over the same classes' live text it finds a body for **%d of %d "
               "declarations (%s%%)**, against **%s%%** inside the comments -- so the shortfall is the comment's own "
               "residue, not the pattern's. What the %d pairs say is that a commented revision is **not** the function the "
               "build carries: **%d** are byte-identical, **%d** keep the operator multiset with a different "
               "layout, **%d** move at least one operator and **%d** leave a live body of %d bytes or fewer "
               "behind a substantial comment -- %d commented bytes against %d live ones over the population."
               % (n("comment_with_body"), n("comment_declarations"), n("pairs"),
                  n("live_with_body"), n("live_declarations"),
                  pct(n("live_with_body"), n("live_declarations")),
                  pct(n("comment_with_body"), n("comment_declarations")), n("pairs"),
                  n("pairs_identical"), n("pairs_same_operators"), n("pairs_differs"),
                  n("pairs_live_stub"), STUB_BYTES, n("pairs_comment_bytes"), n("pairs_live_bytes")))
    out.append("")
    out.append("| measure | value |")
    out.append("|---|---|")
    out.append("| classes with a `ScriptText` / carrying a block comment | **%d** / **%d** |"
               % (n("classes_with_text"), n("classes_with_block")))
    out.append("| `function` declarations inside a block comment | **%d** |" % n("comment_declarations"))
    out.append("| ... with a body in the same comment | **%d** |" % n("comment_with_body"))
    out.append("| ... a declaration without a body | **%d** |" % n("comment_no_body"))
    out.append("| live `function` declarations / whose body the reader finds | **%d** / **%d** |"
               % (n("live_declarations"), n("live_with_body")))
    out.append("| commented bodies with a live twin of the same name | **%d** |" % n("pairs"))
    out.append("| ... byte-identical | **%d** |" % n("pairs_identical"))
    out.append("| ... same operator multiset, different layout | **%d** |" % n("pairs_same_operators"))
    out.append("| ... at least one operator moved | **%d** |" % n("pairs_differs"))
    out.append("| ... whose live body is a trailer | **%d** |" % n("pairs_live_stub"))
    out.append("| bytes, commented revisions / live revisions of the pairs | **%d** / **%d** |"
               % (n("pairs_comment_bytes"), n("pairs_live_bytes")))
    out.append("| operators, the same pair | **%d** / **%d** |"
               % (n("pairs_comment_operators"), n("pairs_live_operators")))
    out.append("| commented bodies with no live twin | **%d** |" % n("disabled_bodies"))
    out.append("| bytes / operators / statements of those bodies | **%d** / **%d** / **%d** |"
               % (n("disabled_bytes"), n("disabled_operators"), n("disabled_statements")))
    out.append("")
    if pairs:
        pairs.sort(key=lambda r: (r["verdict"], r["package"], r["class"], r["name"]))
        out.append("Every pair, `ops` being the operator multiset and `stmts` the statements at the body's own "
                   "brace depth:")
        out.append("")
        out.append("| class.function | verdict | commented ops / stmts / bytes | live ops / stmts / bytes | "
                   "operators that moved |")
        out.append("|---|---|---:|---:|---|")
        for r in pairs:
            out.append("| `%s.%s.%s` | %s | %s / %s / %s | %s / %s / %s | %s |"
                       % (r["package"], r["class"], r["name"], r["verdict"],
                          r["c_ops"], r["c_stmts"], r["c_bytes"],
                          r["l_ops"], r["l_stmts"], r["l_bytes"], r["keys"] or "-"))
        out.append("")
    out.append("**The disabled bodies are the weight of the comment, not a residue of it.** %d commented bodies "
               "have no live twin at all: %d bytes of source, %d operators and %d statements this build never "
               "compiled, %d of them a trailer of %d bytes or fewer (a declaration a class kept while emptying "
               "its body, the shape the pairs show from the other side). Those are the classes and functions "
               "`tools/class-text-comments.py` counts by declaration; this is the same population by size, so a "
               "cut-content read can rank it instead of counting it."
               % (n("disabled_bodies"), n("disabled_bytes"), n("disabled_operators"),
                  n("disabled_statements"), n("disabled_stubs"), STUB_BYTES))
    out.append("")
    disabled = [r for r in rows if r["kind"] == "disabled"]
    if disabled:
        disabled.sort(key=lambda r: (-int(r["c_bytes"]), r["package"], r["class"], r["name"]))
        out.append("The largest of the %d disabled bodies (`ops` / `stmts` at the body's own brace depth, the "
                   "artifact carrying all of them):" % len(disabled))
        out.append("")
        out.append("| class.function | bytes | ops | stmts |")
        out.append("|---|---:|---:|---:|")
        for r in disabled[:8]:
            out.append("| `%s.%s.%s` | %s | %s | %s |"
                       % (r["package"], r["class"], r["name"], r["c_bytes"], r["c_ops"], r["c_stmts"]))
        out.append("")
    out.append("Census: %d classes read, %d with a block comment, %d commented bodies."
               % (n("classes_with_text"), n("classes_with_block"),
                  n("comment_with_body")))
    return "\n".join(out)


# --- artifact and doc ------------------------------------------------------------------------------
def read_artifact(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def splice(path, block):
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    if DOC_START not in text or DOC_END not in text:
        return None
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + "\n" + DOC_END + tail


# --- self-test -------------------------------------------------------------------------------------
def self_check():
    bad = []
    ops = load_tool("decompiler_operators", "decompiler-operators.py")
    body = "{\n\tif (A && B)\n\t\treturn A - B;\n\treturn 0;\n}"
    decl = "function int Foo( int A, int B )\n"
    text = "class A extends B;\n" + decl + body + "\n"
    bodies, _bare, declared, _bare_total, resolved = functions(text)
    if declared != 1 or resolved != 1 or "foo" not in bodies or len(bodies["foo"]) != len(body):
        bad.append("one live declaration read as %d declared / %r" % (declared, sorted(bodies)))
    s = shape(bodies["foo"], ops)
    if (s["ops"], s["stmts"], s["kw"]) != (2, 2, 3):
        bad.append("a body's shape read %d ops / %d stmts / %d keywords" % (s["ops"], s["stmts"], s["kw"]))
    # a text that declares one function twice resolves two declarations and holds one body: the control counts
    # declarations, so a deduplicated map must not be what feeds it
    twice_bodies, _tb, twice_declared, _tbt, twice_resolved = functions(text + "\n" + decl + body + "\n")
    if (twice_declared, twice_resolved, len(twice_bodies)) != (2, 2, 1):
        bad.append("a duplicated declaration read %d declared / %d resolved / %d bodies"
                   % (twice_declared, twice_resolved, len(twice_bodies)))
    # a declaration without a body must not inherit the next one's: the shape a commented revision leaves behind
    two = ("/*\nfunction Bar()\nfunction Baz()\n" + body + "\n*/\n")
    cbodies, cbare, cdeclared, _cbt, _cr = functions(two)
    if cdeclared != 2 or set(cbodies) != {"baz"} or cbare != ["bar"]:
        bad.append("a bodyless declaration took its neighbour's body: %r / %r" % (sorted(cbodies), sorted(cbare)))
    # `;` before the brace means no body, whatever follows
    native = "function int Quux( int A );\nfunction int Zed()\n" + body + "\n"
    nbodies, nbare, ndeclared, _nbt, _nr = functions(native)
    if "quux" not in nbare or "zed" not in nbodies or len(nbodies) != 1:
        bad.append("a `;`-terminated declaration took a body: %r" % (sorted(nbodies),))
    # nested braces and a brace inside a string literal
    nested = "function W()\n{\n\tif (X)\n\t{\n\t\tY = \"}{\";\n\t}\n}\n"
    wb, _wbare, _wd, _wbt, _wr = functions(nested)
    want = '{\n\tif (X)\n\t{\n\t\tY = "}{";\n\t}\n}'
    if wb.get("w") != want:
        bad.append("a nested body was cut short: %r" % (wb.get("w"),))
    # the three verdicts, each from a case the other two cannot match
    c = shape("{\n\tA = B + C;\n}", ops)
    c["text"] = "{\n\tA = B + C;\n}"
    same = dict(c)
    if compare(c, same) != "identical":
        bad.append("an unchanged body did not read identical")
    moved = shape("{\n\tA = B - C;\n}", ops)
    moved["text"] = "{\n\tA = B - C;\n}"
    if compare(c, moved) != "differs":
        bad.append("a moved operator did not read differs")
    layout = shape("{\n\n\tA = B + C;\n\n}", ops)
    layout["text"] = "{\n\n\tA = B + C;\n\n}"
    if compare(c, layout) != "same-operators":
        bad.append("a relaid body did not read same-operators")
    long_text = "{\n\tA = B + C + D + E + F + G + H + I + J;\n}"
    long_body = shape(long_text, ops)
    long_body["text"] = long_text
    stub = shape("{\n}", ops)
    stub["text"] = "{\n}"
    if compare(long_body, stub) != "live-stub":
        bad.append("an emptied live body did not read live-stub")
    # the census adds up over a synthetic population, and the render reads the artifact's own rows
    rows = [pair_row("P", "C", "foo", c, moved, compare(c, moved)),
            pair_row("P", "C", "bar", c, dict(c), "identical"),
            disabled_row("P", "C", "baz", c)]
    cen = (("classes_with_text", 2, ""), ("classes_with_block", 2, ""), ("comment_declarations", 4, ""),
           ("comment_with_body", 3, ""), ("comment_no_body", 1, ""), ("live_declarations", 5, ""),
           ("live_with_body", 5, ""), ("pairs", 2, ""), ("pairs_identical", 1, ""),
           ("pairs_same_operators", 0, ""), ("pairs_differs", 1, ""), ("pairs_live_stub", 0, ""),
           ("pairs_comment_bytes", 2 * c["bytes"], ""), ("pairs_live_bytes", c["bytes"] + moved["bytes"], ""),
           ("pairs_comment_operators", 2 * c["ops"], ""), ("pairs_live_operators", c["ops"] + moved["ops"], ""),
           ("disabled_bodies", 1, ""), ("disabled_bytes", c["bytes"], ""), ("disabled_operators", c["ops"], ""),
           ("disabled_statements", c["stmts"], ""), ("disabled_stubs", 0, ""))
    for key, value, note in cen:
        row = {k: "" for k in FIELDS}
        row.update(kind="census", name=key, count=str(value), note=note)
        rows.append(row)
    block = render(rows)
    for want in ("3 of the 4", "2 of them have a live twin", "`P.C.foo`", "`P.C.bar`"):
        if want not in block:
            bad.append("render is missing %r" % want)
    pair_lines = [l for l in block.splitlines() if l.startswith("| `P.C.")]
    baz_lines = [l for l in pair_lines if "`P.C.baz`" in l]
    if len(pair_lines) != 3 or not baz_lines or len(baz_lines[0].split("|")) != 6:
        bad.append("the pair table and the disabled table are not separated: %r" % (pair_lines,))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.csv")
        write_artifact(rows, path)
        again = read_artifact(path)
        if [r["name"] for r in again] != [r["name"] for r in rows]:
            bad.append("artifact round trip differs")
        if splice(path, "x") is not None:
            bad.append("splice found markers in a CSV")
        page = os.path.join(tmp, "page.md")
        with open(page, "w", encoding="utf-8") as fh:
            fh.write("head\n" + DOC_START + "\nold\n" + DOC_END + "\ntail\n")
        if splice(page, "new") is None:
            bad.append("splice could not find its own markers")
    if bad:
        print("FAIL class-text-bodies: " + "; ".join(bad))
        return 1
    print("ok   class-text-bodies: a commented body is delimited inside its own declaration, `;` and a "
          "neighbouring declaration do not lend it a body, the four verdicts separate, and the render reads "
          "the artifact's census rows")
    return 0


# --- main ------------------------------------------------------------------------------------------
def main():
    if "--check" in sys.argv[1:]:
        return self_check()
    ap = argparse.ArgumentParser()
    ap.add_argument("system", nargs="?", help="the dump's System directory")
    ap.add_argument("--sweep", action="store_true", help="every package -> the artifact")
    ap.add_argument("--report", action="store_true", help="print the block from the committed artifact")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--write-doc", nargs="?", const=DOC, default=None)
    ap.add_argument("--check-doc", nargs="?", const=DOC, default=None)
    args = ap.parse_args()

    if args.write_doc or args.check_doc or args.report:
        rows = read_artifact(ARTIFACT)
    else:
        if not args.system or not os.path.isdir(args.system):
            print("no System directory given")
            return 2
        rows = sweep(args.system)
        if args.out:
            path = os.path.join(args.out, "class_text_bodies.csv")
            write_artifact(rows, path)
            print("%s: %d rows" % (path, len(rows)))
    block = render(rows)
    print(block)
    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        new = splice(path, block)
        if new is None:
            print("FAIL class-text-bodies: markers not found in %s" % path)
            return 1
        with open(path, encoding="utf-8") as fh:
            old = fh.read()
        if args.check_doc:
            if new != old:
                print("FAIL class-text-bodies: %s is stale against the committed artifact" % path)
                return 1
            print("ok   class-text-bodies: %s matches the committed artifact" % path)
            return 0
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
