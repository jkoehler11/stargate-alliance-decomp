#!/usr/bin/env python3
"""What of a class's shipped `ScriptText` is commented out, and which classes keep their whole body there.

`docs/PACKAGE_SCRIPT_TEXT.md` reads the text as "the class declaration and its functions" and counts
declarations across the whole buffer. Measured here, that buffer is not uniformly live source: **252 of the
2,319 classes** carry a `/* ... */` block, and in **39** of them the remainder outside every comment is the
declaration alone -- natives, vars, functions and sometimes a `defaultproperties` block sit *inside* the
comment. The class record is the second witness: all 39 are `functions=0, properties=0` in
`inventory/classes.csv` and **none** of the 213 classes that keep a live body is, so the comment is where
that body went.

The 39 matter beyond the census. Both `defaultproperties` blocks the build ships as text (`Core.Locale` 50
properties, `GUI.SGVertScrollBar` 13) are in that population, so `tools/defaults-emit.py --verify` has never
been reading this build's defaults: it is reading a commented-out revision of the class, which is what that
page's "The shipped text is not an oracle for defaults" section left as three unmeasured candidates.

    tools/class-text-comments.py DUMP/System --sweep -o inventory
    tools/class-text-comments.py --report
    tools/class-text-comments.py --write-doc docs/PACKAGE_SCRIPT_TEXT.md
    tools/class-text-comments.py --check-doc docs/PACKAGE_SCRIPT_TEXT.md   # from the committed artifact
    tools/class-text-comments.py --check                                    # synthetic, no dump, writes nothing

Reads the packages and `inventory/classes.csv`; no bytecode, no value, no boot. The counts are sizes and
declaration heights, never source text: nothing here prints or commits game-derived strings.
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

ARTIFACT = os.path.join(ROOT, "inventory/class_text_comments.csv")
CLASSES = os.path.join(ROOT, "inventory/classes.csv")
SOURCES = os.path.join(ROOT, "inventory/script_text_natives.csv")
DOC = os.path.join(ROOT, "docs/PACKAGE_SCRIPT_TEXT.md")
DOC_START = "<!-- generated:class-text-comments -->"
DOC_END = "<!-- /generated:class-text-comments -->"

FIELDS = ["kind", "package", "key", "payload", "text_bytes", "comment_bytes", "live_bytes", "blocks",
          "live_functions", "live_vars", "comment_functions", "comment_vars", "comment_defaults",
          "live_defaults", "comment_natives", "line_comment_natives", "record_functions",
          "record_properties", "verdict", "count", "note"]

BLOCK = re.compile(r"/\*.*?\*/", re.S)
LINE_COMMENT = re.compile(r"//[^\n]*")
# `function` at the head of a line, the shape `pkg-script-text.py` counts -- a mention of the word inside
# prose is not a declaration, and the difference is 306 rows against 213 over this corpus.
FUNC = re.compile(r"^[ \t]*(?:simulated[ \t]+|static[ \t]+|final[ \t]+|latent[ \t]+)*function[ \t]+"
                  r"(?:[\w<>\[\]]+[ \t]+)?(\w+)[ \t]*\(", re.M)
VAR = re.compile(r"^[ \t]*var\b", re.M)
DEFAULTS = re.compile(r"^[ \t]*_?defaultproperties[ \t]*$", re.M)
# The declaration pattern `pkg-script-text.py` counts natives with, so the two agree.
NATIVE_FN = re.compile(r"\bnative\b(?:\s*\(\s*(\d+)\s*\))?[^\n;]*?\bfunction\s+"
                       r"(?:[\w<>\[\]]+\s+)?(\w+)\s*\(", re.IGNORECASE)


def load_tool(name, filename):
    path = os.path.join(HERE, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- one class's text ------------------------------------------------------------------------------
def declarations_in(text, pattern, spans):
    """How many matches of `pattern` start inside one of `spans` (block-comment ranges)."""
    return sum(1 for m in pattern.finditer(text) if any(a <= m.start() < b for a, b in spans))


def analyse(text):
    """Sizes and declaration heights for one class's ScriptText.

    The live part is the buffer with every block comment and every `//` run removed; the comment part is the
    block comments themselves. A class is `body-in-comment` when its live part declares no function and no
    `var` -- the shape a commented-out revision leaves behind. That verdict is a statement about the two
    counters, not about the bytes: a class can have a live body and still carry a large comment (213 do).
    """
    t = text.replace("\r\n", "\n")
    spans = [m.span() for m in BLOCK.finditer(t)]
    blocks = [t[a:b] for a, b in spans]
    live = LINE_COMMENT.sub("", BLOCK.sub("", t)).strip()
    body = "\n".join(blocks)
    # `//` runs are the other way a declaration is disabled, and on this corpus they hold more natives than the
    # blocks do: counting the block only is how the page came to call 701 live when the live count is 681.
    no_block = BLOCK.sub("", t)
    line_spans = [m.span() for m in LINE_COMMENT.finditer(no_block)]
    live_functions = len(FUNC.findall(live))
    live_vars = len(VAR.findall(live))
    out = {
        "text_bytes": len(t),
        "comment_bytes": sum(b - a for a, b in spans),
        "live_bytes": len(live),
        "blocks": len(blocks),
        "live_functions": live_functions,
        "live_vars": live_vars,
        "comment_functions": len(FUNC.findall(body)),
        "comment_vars": len(VAR.findall(body)),
        "comment_defaults": len(DEFAULTS.findall(body)),
        "live_defaults": len(DEFAULTS.findall(live)),
        "comment_natives": declarations_in(t, NATIVE_FN, spans),
        "line_comment_natives": declarations_in(no_block, NATIVE_FN, line_spans),
    }
    if not spans:
        out["verdict"] = "no-comment"
    elif live_functions == 0 and live_vars == 0:
        out["verdict"] = "body-in-comment"
    else:
        out["verdict"] = "live-body"
    return out


def source_natives(path):
    """The whole-buffer native total `inventory/script_text_natives.csv` credits to the source."""
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return sum(int(r["natives_in_source"]) for r in csv.DictReader(fh))
    except (OSError, KeyError, ValueError):
        return 0


def records(path):
    """`inventory/classes.csv` -> (package, class) -> (functions, properties), as the second witness."""
    out = {}
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                out[(r["package"], r["class"])] = (r["functions"], r["properties"])
    except OSError:
        pass
    return out


# --- the sweep -------------------------------------------------------------------------------------
def class_row(exp, package, cls, a, fn, pr, verdict):
    """One `class` row.

    `verdict` is `analyse()`'s, except `line-comment` for a class that carries no block but disables a
    declaration in a `//` run -- those classes are outside the block census by construction.
    """
    return dict(kind="class", package=package, key=cls, payload=str(exp["size"]),
                text_bytes=str(a["text_bytes"]), comment_bytes=str(a["comment_bytes"]),
                live_bytes=str(a["live_bytes"]), blocks=str(a["blocks"]),
                live_functions=str(a["live_functions"]), live_vars=str(a["live_vars"]),
                comment_functions=str(a["comment_functions"]), comment_vars=str(a["comment_vars"]),
                comment_defaults=str(a["comment_defaults"]), live_defaults=str(a["live_defaults"]),
                comment_natives=str(a["comment_natives"]),
                line_comment_natives=str(a["line_comment_natives"]),
                record_functions=fn, record_properties=pr, verdict=verdict, count="1", note="")


def sweep(system_dir, recs):
    pst = load_tool("pkg_script_text", "pkg-script-text.py")
    rows = []
    per_pkg = {}
    totals = dict(classes=0, comment=0, body_in_comment=0, live=0, comment_bytes=0, text_bytes=0,
                  comment_defaults=0, live_defaults=0, comment_functions=0, comment_vars=0,
                  comment_natives=0, line_comment_natives=0, line_classes=0)
    zero_hits = 0
    live_zero = 0
    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        package = fname[:-2]
        per_pkg[package] = [0, 0, 0, 0]
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            text = pst.script_text(pkg, exp)
            if not text:
                continue
            a = analyse(text)
            cls = pkg.name(exp["name"])
            key = (package, cls)
            fn, pr = recs.get(key, ("", ""))
            totals["classes"] += 1
            per_pkg[package][0] += 1
            totals["text_bytes"] += a["text_bytes"]
            totals["line_comment_natives"] += a["line_comment_natives"]
            per_pkg[package][3] += a["line_comment_natives"]
            if a["verdict"] == "no-comment":
                # A class can disable a declaration in a `//` run without carrying a block, and then the block
                # census never sees it -- Engine's ten are all in such classes.
                if a["line_comment_natives"]:
                    totals["line_classes"] += 1
                    rows.append(class_row(exp, package, cls, a, fn, pr, "line-comment"))
                continue
            totals["comment"] += 1
            totals["comment_bytes"] += a["comment_bytes"]
            totals["comment_functions"] += a["comment_functions"]
            totals["comment_vars"] += a["comment_vars"]
            totals["comment_natives"] += a["comment_natives"]
            totals["comment_defaults"] += a["comment_defaults"]
            totals["live_defaults"] += a["live_defaults"]
            per_pkg[package][1] += 1
            zero = (fn == "0" and pr == "0")
            if a["verdict"] == "body-in-comment":
                totals["body_in_comment"] += 1
                per_pkg[package][2] += 1
                zero_hits += zero
            else:
                totals["live"] += 1
                live_zero += zero
            rows.append(class_row(exp, package, cls, a, fn, pr, a["verdict"]))
    corpus_zero = sum(1 for fn, pr in recs.values() if fn == "0" and pr == "0")
    for package, (classes, comment, body, line) in sorted(per_pkg.items()):
        if not comment and not line:
            continue
        rows.append(dict(kind="package", package=package, key="", payload=str(classes),
                         blocks=str(comment), count=str(body), verdict="", note="",
                         line_comment_natives=str(line),
                         text_bytes="", comment_bytes="", live_bytes="", live_functions="",
                         live_vars="", comment_functions="", comment_vars="", comment_defaults="",
                         live_defaults="", comment_natives="", record_functions="",
                         record_properties=""))
    census = (("classes_with_text", totals["classes"],
               "classes whose export carries a ScriptText"),
              ("classes_with_block_comment", totals["comment"],
               "classes whose text carries a `/* ... */` block"),
              ("classes_body_in_comment", totals["body_in_comment"],
               "of those, the live part declares no function and no var"),
              ("classes_live_body", totals["live"],
               "of those, the live part declares a function or a var"),
              ("comment_functions", totals["comment_functions"],
               "`function` declarations inside a block comment"),
              ("comment_vars", totals["comment_vars"], "`var` declarations inside a block comment"),
              ("comment_natives", totals["comment_natives"],
               "`native function` declarations inside a block comment"),
              ("line_comment_natives", totals["line_comment_natives"],
               "`native function` declarations inside a `//` run"),
              ("line_comment_classes", totals["line_classes"],
               "classes whose only disabled natives are in `//` runs"),
              ("natives_live", source_natives(SOURCES) - totals["comment_natives"]
               - totals["line_comment_natives"],
               "native declarations left live once both comment forms are subtracted"),
              ("comment_defaults", totals["comment_defaults"],
               "`defaultproperties` blocks inside a block comment"),
              ("live_defaults", totals["live_defaults"],
               "`defaultproperties` blocks outside every comment"),
              ("body_in_comment_record_zero", zero_hits,
               "body-in-comment classes whose record declares 0 functions and 0 properties"),
              ("live_body_record_zero", live_zero,
               "live-body classes whose record declares 0 functions and 0 properties"),
              ("corpus_record_zero", corpus_zero,
               "classes in `inventory/classes.csv` declaring 0 functions and 0 properties"),
              ("natives_in_source", source_natives(SOURCES),
               "native declarations `inventory/script_text_natives.csv` credits to the source"),
              ("text_bytes", totals["text_bytes"], "bytes of `ScriptText` over every class"),
              ("comment_bytes", totals["comment_bytes"],
               "of those, bytes inside a block comment"))
    for key, value, note in census:
        row = {k: "" for k in FIELDS}
        row.update(kind="census", key=key, count=str(value), note=note)
        rows.append(row)
    return rows


# --- the render ------------------------------------------------------------------------------------
def census_of(rows):
    return {r["key"]: r for r in rows if r["kind"] == "census"}


def render(rows):
    cen = census_of(rows)
    def n(key):
        return int(cen[key]["count"])
    classes = [r for r in rows if r["kind"] == "class" and r["verdict"] == "body-in-comment"]
    packages = [r for r in rows if r["kind"] == "package"]
    out = []
    out.append("**%d of %d classes ship a commented-out block, and %d of them have nothing live but the "
               "declaration.** The other %d carry a live body beside the comment, so a block comment is not "
               "itself the finding -- what separates the two is what the live remainder declares."
               % (n("classes_with_block_comment"), n("classes_with_text"), n("classes_body_in_comment"),
                  n("classes_live_body")))
    out.append("")
    out.append("| measure | value |")
    out.append("|---|---|")
    out.append("| classes with a `ScriptText` | **%d** |" % n("classes_with_text"))
    out.append("| carrying a `/* ... */` block | **%d** |" % n("classes_with_block_comment"))
    out.append("| live part declares no `function` and no `var` | **%d** |" % n("classes_body_in_comment"))
    out.append("| live part declares a body | **%d** |" % n("classes_live_body"))
    out.append("| `function` declarations inside a block comment | **%d** |" % n("comment_functions"))
    out.append("| `var` declarations inside a block comment | **%d** |" % n("comment_vars"))
    out.append("| `native function` declarations inside a block comment | **%d** |" % n("comment_natives"))
    out.append("| `native function` declarations inside a `//` run | **%d** |" % n("line_comment_natives"))
    out.append("| native declarations left live | **%d** of %d |" % (n("natives_live"), n("natives_in_source")))
    out.append("| `defaultproperties` blocks inside a block comment | **%d** |" % n("comment_defaults"))
    out.append("| `defaultproperties` blocks outside every comment | **%d** |" % n("live_defaults"))
    out.append("| bytes of text / of it inside a block comment | **%d** / **%d** |"
               % (n("text_bytes"), n("comment_bytes")))
    out.append("")
    out.append("**The class record agrees, and the base rate is why that alone proves nothing.** All **%d** "
               "body-in-comment classes are `functions=0, properties=0` in `inventory/classes.csv`, and **%d** "
               "of the %d classes that keep a live body are -- but **%d of the corpus's %d** classes record "
               "`0/0` anyway, so a record cannot name one. The text can, and the pair of rates is the control: "
               "inside the comment population the split is %d of %d against %d of %d."
               % (n("body_in_comment_record_zero"), n("live_body_record_zero"), n("classes_live_body"),
                  n("corpus_record_zero"), n("classes_with_text"),
                  n("body_in_comment_record_zero"), n("classes_body_in_comment"),
                  n("live_body_record_zero"), n("classes_live_body")))
    out.append("")
    out.append("Per package (`classes` / `with a comment` / `body in the comment`):")
    out.append("")
    out.append("| package | classes | with a block comment | body in the comment |")
    out.append("|---|---:|---:|---:|")
    for r in packages:
        out.append("| `%s` | %s | %s | %s |" % (r["package"], r["payload"], r["blocks"], r["count"]))
    out.append("")
    out.append("The %d body-in-comment classes, `payload` being the class export's serial size:" % len(classes))
    out.append("")
    out.append("| class | payload | text bytes | commented | `defaultproperties` in the comment |")
    out.append("|---|---:|---:|---:|---:|")
    for r in classes:
        out.append("| `%s.%s` | %s | %s | %s | %s |"
                   % (r["package"], r["key"], r["payload"], r["text_bytes"], r["comment_bytes"],
                      r["comment_defaults"] if r["comment_defaults"] not in ("", "0") else "-"))
    out.append("")
    flagged = [r for r in classes if r["comment_defaults"] not in ("", "0")]
    if flagged:
        out.append("**Every `defaultproperties` block the build ships as text is in that population** -- `%s` "
                   "-- so the text cannot serve as an oracle for this build's compiled defaults: the block it "
                   "carries belongs to a revision of the class that the package did not compile. "
                   "`tools/defaults-emit.py --verify` now prints the same status per block."
                   % "`, `".join("%s.%s" % (r["package"], r["key"]) for r in flagged))
    else:
        out.append("No body-in-comment class carries a `defaultproperties` block, so the two are disjoint.")
    out.append("")
    natives = [r for r in rows if r["kind"] == "class" and r["comment_natives"] not in ("", "0")]
    if natives:
        out.append("")
        out.append("**%d of the `native function` declarations a whole-buffer count credits are inside a block "
                   "comment**, and they sit in `%s` -- classes whose live part declares none of them, so the "
                   "source-side native total is %d against the %d such a count reports. `StargateContent`'s "
                   "3-against-0 in `inventory/script_text_natives.csv` is one of those classes and not a package "
                   "the inventory mis-attributes."
                   % (n("comment_natives"), "`, `".join("%s.%s" % (r["package"], r["key"]) for r in natives),
                      n("natives_in_source") - n("comment_natives"), n("natives_in_source")))
    inline = [r for r in rows if r["kind"] == "class" and r["line_comment_natives"] not in ("", "0")]
    if inline:
        out.append("")
        out.append("**The other comment form disables more of them: %d `native function` declarations sit inside "
                   "a `//` run**, in %d classes of which %d carry no block at all (`%s`). A block-only count is "
                   "what called this corpus %d live; with both forms subtracted the source declares **%d** live "
                   "native functions, so any per-class attribution that starts from the whole-buffer number "
                   "inherits the %d disabled ones."
                   % (n("line_comment_natives"), len(inline), n("line_comment_classes"),
                      "`, `".join("%s.%s" % (r["package"], r["key"]) for r in inline),
                      n("natives_in_source") - n("comment_natives"), n("natives_live"),
                      n("natives_in_source") - n("natives_live")))
    out.append("")
    out.append("Census: %d classes read, %d with a block comment." % (n("classes_with_text"),
                                                                     n("classes_with_block_comment")))
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
    live = "class A extends B;\nvar int X;\nfunction Foo()\n{\n}\n"
    a = analyse(live)
    if a["verdict"] != "no-comment" or a["blocks"] != 0:
        bad.append("a text with no comment: %r" % (a["verdict"],))
    if a["live_functions"] != 1 or a["live_vars"] != 1:
        bad.append("live counters: %d functions, %d vars" % (a["live_functions"], a["live_vars"]))
    # an older revision of the same class, wholly inside a block comment
    whole = ("class A extends B;\n/*\n" + live +
             "native function int Bar( int i );\ndefaultproperties\n{\n\tX=3\n}\n*/\n")
    b = analyse(whole)
    if b["verdict"] != "body-in-comment":
        bad.append("a fully commented body read as %r" % (b["verdict"],))
    if b["comment_functions"] != 1 or b["comment_defaults"] != 1 or b["comment_natives"] != 1:
        bad.append("comment counters: %d functions, %d defaults, %d natives"
                   % (b["comment_functions"], b["comment_defaults"], b["comment_natives"]))
    if b["live_functions"] or b["live_vars"]:
        bad.append("the commented revision leaked into the live part")
    if b["live_defaults"] != 0:
        bad.append("a commented block was counted as live")
    # a live body beside a comment must NOT be read as a commented-out class
    half = live + "/*\nvar int Y;\ndefaultproperties\n{\n\tY=1\n}\n*/\n"
    c = analyse(half)
    if c["verdict"] != "live-body":
        bad.append("a text with a live body read as %r" % (c["verdict"],))
    if c["live_defaults"] != 0 or c["comment_defaults"] != 1:
        bad.append("a live body beside a comment split wrong: %r" % (c,))
    # a `//` line comment is not a block, and a commented-out block must not be invisible
    d = analyse("class A extends B;\n// /* not a block */\nvar int X;\n")
    if d["blocks"] != 1 or d["verdict"] != "live-body":
        bad.append("a line comment holding `/* */` broke the block count: %r" % (d,))
    # a `//`-runnable declaration is disabled as well, and it sits outside every block: Engine's ten are this shape
    inline_text = ("class A extends B;\n//native final function int Foo( int A );\n"
                   "native function Bar( int B );\n")
    inline = analyse(inline_text)
    if inline["line_comment_natives"] != 1 or inline["comment_natives"] != 0:
        bad.append("a `//`-commented native split as %d line / %d block"
                   % (inline["line_comment_natives"], inline["comment_natives"]))
    if inline["verdict"] != "no-comment" or inline["blocks"] != 0:
        bad.append("a `//`-only class read as %r with %d blocks"
                   % (inline["verdict"], inline["blocks"]))
    # a class whose only disabled declaration is a `//` one still gets a row, under its own verdict
    lit = class_row({"size": 1, "index": 0}, "P", "Inline", inline, "1", "1", "line-comment")
    if lit["line_comment_natives"] != "1" or lit["verdict"] != "line-comment":
        bad.append("the line-comment row lost its count: %r" % (lit,))
    # the count is not double-counted: the same declaration inside a block is a block one, not both
    both = analyse("class A extends B;\n/*\n//native function Baz( int C );\n*/\n")
    if both["comment_natives"] != 1 or both["line_comment_natives"] != 0:
        bad.append("a `//` inside a block counted twice: %r" % (both,))
    # the census adds up over a synthetic package, and the record witness is read by key
    rows = []
    for cls, verdict_rows in (("Body", whole), ("Live", half), ("Inline", inline_text)):
        a = analyse(verdict_rows)
        row = {k: str(a[k]) for k in FIELDS if k in a}
        row.update(kind="class", package="P", key=cls, payload="1", verdict=a["verdict"], count="1")
        rows.append(row)
    rows.append(dict(kind="package", package="P", payload="3", blocks="2", count="1",
                     line_comment_natives="1",
                     **{k: "" for k in FIELDS if k not in ("kind", "package", "payload", "blocks", "count",
                                                          "line_comment_natives")}))
    for key, value, note in (("classes_with_text", 3, ""), ("classes_with_block_comment", 2, ""),
                             ("classes_body_in_comment", 1, ""), ("classes_live_body", 1, ""),
                             ("comment_functions", 2, ""), ("comment_vars", 2, ""),
                             ("comment_natives", 1, ""), ("line_comment_natives", 1, ""),
                             ("line_comment_classes", 1, ""), ("natives_live", 705, ""),
                             ("comment_defaults", 2, ""),
                             ("live_defaults", 0, ""), ("text_bytes", 1, ""), ("comment_bytes", 1, ""),
                             ("body_in_comment_record_zero", 1, ""), ("live_body_record_zero", 0, ""),
                             ("corpus_record_zero", 9, ""), ("natives_in_source", 707, "")):
        row = {k: "" for k in FIELDS}
        row.update(kind="census", key=key, count=str(value), note=note)
        rows.append(row)
    block = render(rows)
    for want in ("2 of 3", "1 of them", "`P.Body`", "9", "706 against the 707", "**705** live",
                 "`P.Inline`"):
        if want not in block:
            bad.append("render is missing %r" % want)
    if render(rows).count("`P.Live`") != 0:
        bad.append("a live-body class was listed as body-in-comment")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.csv")
        write_artifact(rows, path)
        again = read_artifact(path)
        if [r["key"] for r in again] != [r["key"] for r in rows]:
            bad.append("artifact round trip differs")
        if splice(path, "x") is not None:
            bad.append("splice found markers in a CSV")
        page = os.path.join(tmp, "page.md")
        with open(page, "w", encoding="utf-8") as fh:
            fh.write("head\n" + DOC_START + "\nold\n" + DOC_END + "\ntail\n")
        if splice(page, "new") is None:
            bad.append("splice could not find its own markers")
    if bad:
        print("FAIL class-text-comments: " + "; ".join(bad))
        return 1
    print("ok   class-text-comments: a commented-out revision reads apart from a live body, a `//` holding "
          "`/* */` is not a block, the record witness is joined by key, and the render lists only the "
          "body-in-comment population")
    return 0


# --- main ------------------------------------------------------------------------------------------
def main():
    if "--check" in sys.argv[1:]:
        return self_check()
    ap = argparse.ArgumentParser()
    ap.add_argument("system", nargs="?", help="the dump's System directory")
    ap.add_argument("--sweep", action="store_true", help="every package -> the artifact")
    ap.add_argument("--report", action="store_true", help="print the totals from the committed artifact")
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
        rows = sweep(args.system, records(CLASSES))
        if args.out:
            path = os.path.join(args.out, "class_text_comments.csv")
            write_artifact(rows, path)
            print("%s: %d rows" % (path, len(rows)))
    block = render(rows)
    print(block)
    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        new = splice(path, block)
        if new is None:
            print("FAIL class-text-comments: markers not found in %s" % path)
            return 1
        with open(path, encoding="utf-8") as fh:
            old = fh.read()
        if args.check_doc:
            if new != old:
                print("FAIL class-text-comments: %s is stale against the committed artifact" % path)
                return 1
            print("ok   class-text-comments: %s matches the committed artifact" % path)
            return 0
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
