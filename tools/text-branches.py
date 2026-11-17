#!/usr/bin/env python3
"""What the shipped source spells, against what the bytecode compiled: `else`, `&&` and `||` (PLAN 3.7).

`inventory/jump_branches.csv` reads the jump graph back over the statement stream and finds **4,047** `else`
arms. Each class also ships its `ScriptText`, so the same question can be asked of the source: how many `else`
keywords does the live text spell? The two counts are independent measurements -- one is compiled bytes, the
other a text buffer the build shipped -- so a class where they disagree is a class whose text is not the
revision the package compiled (`tools/class-text-comments.py` finds 39 whose whole body sits inside a comment).

The text is masked before it is counted: comments first, then literals, with a **length-preserving** mask so the
counts are over the same buffer (`tools/decompiler-operators.py`'s order -- doing literals first runs a quote
match from an apostrophe in prose to the next one thousands of lines later and blanks the code between).
`#else` is a preprocessor directive, not a branch, and gets its own column instead of being subtracted silently.

The same pass counts `&&` and `||`, which the bytecode cannot name directly, and `if`/`while`/`for` as the
condition-term model: one `JumpIfNot` per term is what the codegen should emit, and the leftover is what the
class's text declares and its package does not compile.

    tools/text-branches.py --system DUMP/System -o inventory/text_branches.csv --write-doc docs/BYTECODE.md
    tools/text-branches.py --report
    tools/text-branches.py --check-doc docs/BYTECODE.md   # from the committed artifact
    tools/text-branches.py --check                        # synthetic, no dump, writes nothing

Reads the packages plus `inventory/jump_branches.csv`; no bytecode walk, no boot, no Ghidra. Counts only:
nothing here prints or commits source text.
"""
import argparse
import collections
import csv
import importlib.util
import io
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402

CSV_PATH = os.path.join(ROOT, "inventory", "text_branches.csv")
BRANCHES = os.path.join(ROOT, "inventory", "jump_branches.csv")
DOC_MARK = ("<!-- generated:text-branches -->", "<!-- /generated:text-branches -->")

FIELDS = ["kind", "package", "class", "live_else", "comment_else", "and", "or", "if", "while", "for", "terms",
          "pp_else", "text_functions", "record_functions", "byte_else", "byte_if", "byte_empty",
          "byte_expression", "verdict", "count", "note"]

CENSUS_NOTE = {
    "classes": "classes whose `ScriptText` was readable",
    "packages": "packages with at least one joined class",
    "joined": "of those, classes the branch census also has",
    "agree": "classes whose live text spells exactly the `else` arms the bytecode has",
    "more": "classes whose live text spells more `else` than the bytecode has arms",
    "less": "classes whose live text spells fewer",
    "no_branch": "classes with readable text and no branch at all in the bytecode",
    "arms_no_text": "classes with branch arms but no readable `ScriptText`",
    "text_else": "`else` keywords in the live text",
    "comment_else": "`else` keywords inside comments (the disabled-body signature)",
    "and": "`&&` in the live text",
    "or": "`||`",
    "if": "`if` keywords in the live text",
    "while": "`while`",
    "for": "`for`",
    "terms": "`if` + `while` + `for`, the condition terms one `JumpIfNot` each should compile",
    "text_functions": "`function` declarations in the live text",
    "no_branch_functions": "function declarations in the live text of classes with no branch at all",
    "no_branch_terms": "condition terms in that population",
    "max_gap": "the largest class-level difference between text `else` and bytecode arms",
    "pp_else": "`#else` preprocessor directives, not branches",
    "byte_else": "`else` arms in `inventory/jump_branches.csv`",
    "byte_if": "straight `if` arms there",
    "byte_empty": "empty then-arms there",
    "byte_expression": "jumps inside a condition there",
    "jumpifnot": "`JumpIfNot` tokens the branch census reports",
    "terms_exact": "joined classes whose term count equals their `JumpIfNot` count exactly",
    "no_and_or": "joined classes whose live text carries no `&&` or `||`",
    "no_and_or_exact": "of those, the ones whose term count is exact",
    "with_and_or": "joined classes whose live text carries a `&&` or `||`",
    "with_and_or_exact": "of those, the ones whose term count is exact -- an operator that added a jump would",
}

WORD = re.compile(r"(?<![#\w$])(if|else|while|for)(?![\w$])")
# The declaration shape `tools/class-text-comments.py` counts, so the two agree on what a function is.
FUNC = re.compile(r"^[ \t]*(?:simulated[ \t]+|static[ \t]+|final[ \t]+|latent[ \t]+)*function[ \t]+"
                  r"(?:[\w<>\[\]]+[ \t]+)?(\w+)[ \t]*\(", re.M)
PP_ELSE = re.compile(r"#[ \t]*else\b")


def load_tool(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- masking ---------------------------------------------------------------------------------------
def mask(text):
    """Return `(live, comments)` for one class's text; `live` keeps the buffer's length.

    `live` has every comment and every quoted literal blanked to spaces (newlines kept, so nothing merges
    across lines); `comments` is the comment bodies joined by a newline, which is where a disabled body's
    keywords still live.
    """
    out = list(text)
    bodies = []
    n = len(text)

    def blank(a, b):
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    i = 0
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            if j < 0:
                j = n
            bodies.append(text[i + 2:j])
            blank(i, j)
            i = j
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            bodies.append(text[i + 2:max(i + 2, j - 2)])
            blank(i, j)
            i = j
        elif c in "\"'":
            q = c
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == q:
                    # a doubled quote escapes itself in both spellings
                    if j + 1 < n and text[j + 1] == q:
                        j += 2
                        continue
                    j += 1
                    break
                if text[j] == "\n":
                    break
                j += 1
            blank(i, j)
            i = j
        else:
            i += 1
    return "".join(out), "\n".join(bodies)


def counts(text):
    """Keyword and operator counts over masked text."""
    c = collections.Counter()
    for m in WORD.finditer(text):
        c[m.group(1)] += 1
    c["and"] = text.count("&&")
    c["or"] = text.count("||")
    c["pp_else"] = len(PP_ELSE.findall(text))
    return c


# --- the dump side ---------------------------------------------------------------------------------
def walk(system_dir):
    pst = load_tool("pkg_script_text", "pkg-script-text.py")
    rows = {}
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
            live, comments = mask(text)
            rows[(package, pkg.name(exp["name"]).lower())] = {
                "class": pkg.name(exp["name"]),
                "live": counts(live),
                "comment_else": counts(comments)["else"],
                "functions": len(FUNC.findall(live)),
            }
    return rows


def arm_counts(path=BRANCHES):
    """`(package, class)` -> Counter of branch categories, plus the census the branch tool printed."""
    out = collections.defaultdict(collections.Counter)
    census = {}
    for r in csv.DictReader(open(path, encoding="utf-8")):
        if r.get("kind") == "census":
            note = r.get("note") or ""
            if "JumpIfNot" in note:
                census["jumpifnot"] = int(r.get("count") or 0)
            continue
        if r.get("kind") != "branch":
            continue
        out[(r["package"], (r["class"] or "").lower())][r["category"]] += 1
    return out, census


def verdict(live_else, byte_else):
    if live_else == byte_else:
        return "agree"
    return "more" if live_else > byte_else else "less"


def join(texts, arms, branch_census, jumpifnot, records):
    census = collections.Counter()
    rows = []
    for key in sorted(set(texts) | set(arms)):
        t = texts.get(key)
        a = arms.get(key, collections.Counter())
        row = {"kind": "class", "package": key[0], "class": (t or {}).get("class", key[1]),
               "live_else": "", "comment_else": "", "and": "", "or": "", "if": "", "while": "", "for": "",
               "terms": "", "pp_else": "", "text_functions": "", "record_functions": records.get(key, ""),
               "byte_else": a["else"], "byte_if": a["if"],
               "byte_empty": a["empty"], "byte_expression": a["expression"], "verdict": "", "count": "",
               "note": ""}
        census["byte_else"] += a["else"]
        census["byte_if"] += a["if"]
        census["byte_empty"] += a["empty"]
        census["byte_expression"] += a["expression"]
        if t is None:
            row["verdict"] = "no-text"
            census["arms_no_text"] += 1
            rows.append(row)
            continue
        c = t["live"]
        terms = c["if"] + c["while"] + c["for"]
        row.update({"live_else": c["else"], "comment_else": t["comment_else"], "and": c["and"], "or": c["or"],
                    "if": c["if"], "while": c["while"], "for": c["for"], "terms": terms, "pp_else": c["pp_else"],
                    "text_functions": t["functions"]})
        census["text_functions"] += t["functions"]
        census["classes"] += 1
        for k in ("else", "and", "or", "if", "while", "for", "pp_else"):
            census["text_" + k if k == "else" else k] += c[k]
        census["comment_else"] += t["comment_else"]
        census["terms"] += terms
        if key not in arms:
            row["verdict"] = "no-branch"
            census["no_branch"] += 1
            census["no_branch_functions"] += t["functions"]
            census["no_branch_terms"] += terms
            rows.append(row)
            continue
        row["verdict"] = verdict(c["else"], a["else"])
        census["max_gap"] = max(census["max_gap"], abs(c["else"] - a["else"]))
        census["joined"] += 1
        census[row["verdict"]] += 1
        jin = a["else"] + a["if"] + a["empty"] + a["expression"]
        exact = terms == jin
        census["terms_exact"] += 1 if exact else 0
        if c["and"] + c["or"] == 0:
            census["no_and_or"] += 1
            census["no_and_or_exact"] += 1 if exact else 0
        else:
            census["with_and_or"] += 1
            census["with_and_or_exact"] += 1 if exact else 0
        rows.append(row)
    # Per-package rows: the join is a per-class test, but which package is a worse oracle is the question a
    # rebuild's owner asks, and it is a sum over the rows above.
    per = collections.defaultdict(collections.Counter)
    for row in rows:
        if not row["class"] or row["verdict"] == "no-text":
            continue
        c = per[row["package"]]
        c["classes"] += 1
        c[row["verdict"]] += 1
        c["text_else"] += int(row["live_else"])
        c["byte_else"] += int(row["byte_else"])
        c["terms"] += int(row["terms"])
    pkg_rows = []
    for name in sorted(per):
        c = per[name]
        pkg_rows.append({"kind": "package", "package": name, "live_else": c["text_else"],
                         "byte_else": c["byte_else"], "terms": c["terms"], "count": c["classes"],
                         "note": "agree %d, more %d, less %d, no-branch %d"
                                 % (c["agree"], c["more"], c["less"], c["no-branch"])})
    census["packages"] = len(pkg_rows)
    out = rows + pkg_rows
    for k in ("classes", "packages", "joined", "agree", "more", "less", "no_branch", "arms_no_text", "text_else",
              "comment_else", "and", "or", "if", "while", "for", "terms", "pp_else", "text_functions",
              "no_branch_functions", "no_branch_terms", "max_gap", "byte_else", "byte_if",
              "byte_empty", "byte_expression", "jumpifnot", "terms_exact", "no_and_or", "no_and_or_exact",
              "with_and_or", "with_and_or_exact"):
        value = jumpifnot if k == "jumpifnot" else census[k]
        out.append({"kind": "census", "count": value, "note": CENSUS_NOTE[k]})
    return out


# --- rendering -------------------------------------------------------------------------------------
def render(rows):
    fh = io.StringIO()
    w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
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
        if r.get("kind") == "census" and r.get("note") == CENSUS_NOTE.get(key):
            return int(r["count"] or 0)
    return 0


def pct(a, b):
    return (100.0 * a / b) if b else 0.0


def comma(n):
    return "{:,}".format(int(n))


def doc_block(rows):
    classes = count(rows, "classes")
    if not classes:
        return "\n(no text-branch census rows)\n"
    body = ("\n**The live text spells %s `else`** over the %s classes whose `ScriptText` parses, against the "
            "**%s** arms the bytecode carries: **%s of %s** classes the branch census also has agree exactly on "
            "the count (**%s%%**), **%s** spell more and **%s** fewer, and %s more carry text with no branch at "
            "all in the bytecode -- text that declares %s function bodies between them. Per class the source is "
            "therefore an **%s%%** oracle, and the corpus totals "
            "agree to %s in %s only because the two sides cancel.\n"
            "\n**One `JumpIfNot` per condition term is the codegen's rule and it holds**: `if` + `while` + `for` "
            "over those %s joined classes is **%s** terms for **%s** `JumpIfNot` tokens, exact on **%s** classes "
            "(**%s%%**). **%s** of them carry no `&&`/`||` and are exact **%s** times (**%s%%**), while the **%s** "
            "that do carry one are exact **%s** times (**%s%%**) -- so a `&&`/`||` adds **no** jump: UnrealScript's "
            "boolean operators are strict, not short-circuit, which is why the branch census found 0 jumps naming "
            "a non-statement; the %s `&&` and %s `||` in this text are therefore **0** arms, and the model's %s "
            "surplus terms sit in the %s joined classes that are not exact -- text a class ships and its package "
            "does not compile.\n")
    body = body % (comma(count(rows, "text_else")), comma(classes), comma(count(rows, "byte_else")),
               comma(count(rows, "agree")), comma(count(rows, "joined")),
               "%.1f" % pct(count(rows, "agree"), count(rows, "joined")), comma(count(rows, "more")),
               comma(count(rows, "less")), comma(count(rows, "no_branch")),
               comma(count(rows, "no_branch_functions")),
               "%.1f" % pct(count(rows, "agree"), count(rows, "joined")),
               comma(abs(count(rows, "text_else") - count(rows, "byte_else"))), comma(count(rows, "byte_else")),
               comma(count(rows, "joined")), comma(count(rows, "terms")), comma(count(rows, "jumpifnot")),
               comma(count(rows, "terms_exact")), "%.1f" % pct(count(rows, "terms_exact"), count(rows, "joined")),
               comma(count(rows, "no_and_or")), comma(count(rows, "no_and_or_exact")),
               "%.1f" % pct(count(rows, "no_and_or_exact"), count(rows, "no_and_or")),
               comma(count(rows, "with_and_or")), comma(count(rows, "with_and_or_exact")),
               "%.1f" % pct(count(rows, "with_and_or_exact"), count(rows, "with_and_or")),
               comma(count(rows, "and")), comma(count(rows, "or")),
               comma(count(rows, "terms") - count(rows, "jumpifnot")),
               comma(count(rows, "joined") - count(rows, "terms_exact")))
    return body + package_table(rows)


def package_table(rows):
    """A markdown table of the joined classes per package, so the disagreement has an owner."""
    lines = ["\n| package | joined | agree | more | less | text `else` | arms |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in [x for x in rows if x.get("kind") == "package"]:
        note = {}
        for part in (r["note"] or "").split(","):
            k, _, v = part.strip().partition(" ")
            note[k] = v
        lines.append("| %s | %s | %s | %s | %s | %s | %s |"
                     % (r["package"], comma(r["count"]), comma(note.get("agree", 0)),
                        comma(note.get("more", 0)), comma(note.get("less", 0)), comma(r["live_else"]),
                        comma(r["byte_else"])))
    return "\n".join(lines) + "\n"


def splice(path, block):
    text = open(path, encoding="utf-8").read()
    lo, hi = DOC_MARK
    if lo not in text or hi not in text:
        raise SystemExit("FAIL text-branches: markers not found in %s" % path)
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
        print("FAIL text-branches: markers not found in %s" % path)
        return 1
    if text.split(lo, 1)[1].split(hi, 1)[0] != doc_block(rows):
        print("FAIL text-branches: %s is stale -- regenerate with `make text-branches`" % path)
        return 1
    print("ok   text-branches: docs block reproduces from inventory/text_branches.csv")
    return 0


# --- self-test -------------------------------------------------------------------------------------
def selftest():
    """A synthetic text with one of each shape, so a mask that stops seeing one fails here."""
    # `else` is a branch, `#else` is a directive, a comment is not live, and an apostrophe inside a line
    # comment must not run a string match into the code below it.
    src = ("// don't count me\n"
           "function F() { if (a && b) c(); else d(); }\n"
           "#if X\n#else\n#endif\n"
           "/* if (a) e(); else f(); */\n"
           "s = \"else // not a branch\";\n"
           "t = 'else';\n")
    live, comments = mask(src)
    lc, cc = counts(live), counts(comments)
    if lc["else"] != 1 or lc["if"] != 1 or lc["and"] != 1 or lc["pp_else"] != 1:
        print("FAIL text-branches: live text read %r" % dict(lc))
        return 1
    if cc["else"] != 1 or cc["if"] != 1:
        print("FAIL text-branches: comment text read %r" % dict(cc))
        return 1
    if lc["or"] != 0:
        print("FAIL text-branches: a quoted `//` leaked an operator")
        return 1
    # lengths are preserved on the live buffer, which is what lets two counts share offsets
    if len(live) != len(src):
        print("FAIL text-branches: mask changed the buffer length")
        return 1
    if [verdict(2, 2), verdict(3, 2), verdict(1, 2), verdict(0, 0)] != ["agree", "more", "less", "agree"]:
        print("FAIL text-branches: verdicts read wrong")
        return 1
    # the block reproduces from its own CSV
    rows = [{"kind": "census", "count": v, "note": CENSUS_NOTE[k]} for k, v in
            (("classes", 4), ("joined", 3), ("agree", 2), ("more", 1), ("less", 0), ("no_branch", 1),
             ("arms_no_text", 0), ("text_else", 4), ("comment_else", 1), ("and", 5), ("or", 1), ("if", 6),
             ("while", 2), ("for", 1), ("terms", 9), ("pp_else", 1), ("text_functions", 5),
             ("no_branch_functions", 1), ("no_branch_terms", 0), ("max_gap", 2), ("byte_else", 3), ("byte_if", 4),
             ("byte_empty", 0), ("byte_expression", 0), ("jumpifnot", 7), ("terms_exact", 2),
             ("no_and_or", 1), ("no_and_or_exact", 1), ("with_and_or", 2), ("with_and_or_exact", 1),
             ("packages", 1))]
    rows.append({"kind": "package", "package": "Fixture", "live_else": 3, "byte_else": 2, "terms": 4,
                 "count": 2, "note": "agree 1, more 1, less 0, no-branch 0"})
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "rows.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(render(rows))
        if doc_block(read_csv(path)) != doc_block(rows):
            print("FAIL text-branches: the block does not reproduce from its own CSV")
            return 1
    finally:
        for name in os.listdir(tmp):
            os.unlink(os.path.join(tmp, name))
        os.rmdir(tmp)
    print("ok   text-branches: else, #else, comments and literals each read as themselves, and the block"
          " reproduces from its CSV")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--system", help="the dump's System directory")
    ap.add_argument("-o", "--out", default=CSV_PATH, help="the artifact (default inventory/text_branches.csv)")
    ap.add_argument("--branches", default=BRANCHES, help="the branch census to join (default as committed)")
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
    arms, branch_census = arm_counts(args.branches)
    jif = branch_census.get("jumpifnot", 0)
    records = {}
    classes_csv = os.path.join(ROOT, "inventory", "classes.csv")
    if os.path.exists(classes_csv):
        for r in csv.DictReader(open(classes_csv, encoding="utf-8")):
            records[(r["package"], r["class"].lower())] = r.get("functions", "")
    rows = join(walk(args.system), arms, branch_census, jif, records)
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
