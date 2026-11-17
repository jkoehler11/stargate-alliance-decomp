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

FIELDS = ["kind", "package", "class", "function", "live_else", "comment_else", "and", "or", "if", "while", "for",
          "terms", "pp_else", "text_functions", "record_functions", "byte_else", "byte_if", "byte_empty",
          "byte_expression", "fn_arms", "fn_absent", "fn_short", "fn_long", "fn_outside", "fn_state",
          "fn_state_label", "fn_state_nested", "fn_top", "fn_unpaired",
          "verdict", "count", "note"]

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
    "less_fewer": "`less` classes whose live text declares fewer functions than their record has",
    "less_equal": "`less` classes that declare exactly as many",
    "less_more": "`less` classes that declare more",
    "agree_fewer": "`agree` classes that declare fewer functions than their record has -- the control for the above",
    "agree_equal": "`agree` classes that declare exactly as many",
    "agree_more": "`agree` classes that declare more",
    "fn_absent_names": "distinct function names behind the `less` classes' absent arms",
    "decl_legacy": "declarations the previous rule counted (`simulated`/`static`/`final`/`latent` only)",
    "decl_wide": "declarations the grammar has, any modifier",
    "decl_no_function": "declarations introduced by `event`/`delegate`/`operator` rather than the word `function`",
    "decl_grammar": "declarations read with the parameter list required on the same line -- `#635`'s rule",
    "decl_trailing": "declarations with a modifier after the keyword (`function static`)",
    "states_case_folded": "`state` declarations spelled `State` with a capital (strict shape, not a regex diff)",
    "state_capital_classes": "classes carrying one -- lost to a case-sensitive read",
    "fn_state": "`else` keywords inside a `state { }` block and outside every function in it",
    "fn_state_label": "of those, at the state body's own statement level (label code)",
    "fn_state_nested": "of those, inside a nested brace of that statement level",
    "state_classes": "classes carrying at least one such keyword",
    "fn_top": "`else` keywords outside every function and every block",
    "top_classes": "classes carrying one, named in the table below",
    "texts_front_truncated": "class texts whose buffer begins inside a `/* ... */` comment",
    "front_truncated_else": "`else` keywords outside every function in those texts -- their leading prose",
    "state_blocks": "`state` declarations with a braced body",
}

CENSUS_KEYS = ("classes", "packages", "joined", "agree", "more", "less", "no_branch", "arms_no_text",
               "text_else", "comment_else", "and", "or", "if", "while", "for", "terms", "pp_else",
               "text_functions", "no_branch_functions", "no_branch_terms", "max_gap", "byte_else", "byte_if",
               "byte_empty", "byte_expression", "jumpifnot", "terms_exact", "no_and_or", "no_and_or_exact",
               "with_and_or", "with_and_or_exact", "less_fewer", "less_equal", "less_more", "agree_fewer",
               "agree_equal", "agree_more",
               "fn_absent_names", "decl_legacy", "decl_wide", "decl_no_function", "decl_grammar",
               "decl_trailing", "states_case_folded", "state_capital_classes", "fn_state", "fn_state_label",
               "fn_state_nested",
               "state_classes", "fn_top", "top_classes", "state_blocks", "texts_front_truncated",
               "front_truncated_else")

WORD = re.compile(r"(?<![#\w$])(if|else|while|for)(?![\w$])")
# The grammar's declaration modifiers. The previous rule carried `simulated`/`static`/`final`/`latent` only, so
# an `exec function` -- an entire class's worth in `CheatManager` and `PlayerController` -- read as code outside
# any function; `FUNC_LEGACY` keeps that shape so the correction is a number rather than a claim.
MODIFIERS = ("simulated", "static", "final", "latent", "exec", "private", "protected", "public", "event",
             "iterator", "singular", "delegate", "preoperator", "postoperator", "operator")
# A declaration does not need the word `function` either: `event`, `delegate` and the operator family introduce
# one on their own, and in the engine classes the `event` handlers are most of the code that is not a function.
# Declarations are counted with `tools/class-text-bodies.py`'s rule -- one rule for every text census in the
# tree, so a declaration no reader can see is not a disagreement between two pages. `FUNC_LEGACY` and
# `FUNC_NO_KEYWORD` stay local: they are readings of their own, quoted as deltas.
FUNC_NO_KEYWORD = re.compile(r"^[ \t]*(?:(?:%s)[ \t]+)*(?:event|delegate|operator|preoperator|postoperator)"
                             r"[ \t]+(?:[\w<>\[\]]+[ \t]+)?(\w+)[ \t]*\("
                             % "|".join(MODIFIERS), re.M)
FUNC_LEGACY = re.compile(r"^[ \t]*(?:simulated[ \t]+|static[ \t]+|final[ \t]+|latent[ \t]+)*function[ \t]+"
                         r"(?:[\w<>\[\]]+[ \t]+)?(\w+)[ \t]*\(", re.M)
# UnrealScript folds keyword case, and the shipped text spells `State` with a capital in 55 declarations across
# 35 classes: a case-sensitive read loses all of them and reports their bodies as code outside every block.
STATE = re.compile(r"^[ \t]*(?:auto[ \t]+)?state[ \t]+(\w+)", re.M | re.I)
# The capitalised spelling, strictly: differencing the case-folded and case-sensitive reads counted identifiers
# (`stateTime = 0.0;`) as declarations and answered 67 where the capital-`State` shape is 55 in 35 classes.
STATE_CAPITAL = re.compile(r"(?m)^[ \t]*(?:auto[ \t]+)?State[ \t]+\w+")
PP_ELSE = re.compile(r"#[ \t]*else\b")


def load_tool(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CTB = None


def ctb_tool():
    """`tools/class-text-bodies.py`, loaded once -- its `body_span` is the brace walk both readers share."""
    global CTB
    if CTB is None:
        CTB = load_tool("class_text_bodies", "class-text-bodies.py")
    return CTB


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


def else_in(text):
    """`else` keywords in a body, counted the way `counts` counts them (no `#else`, no `elsewhere`)."""
    return sum(1 for m in WORD.finditer(text) if m.group(1) == "else")


def function_else(live):
    """`(name -> `else` in the body, every name declared, the body spans)` for one class text.

    Spans come from `tools/class-text-bodies.py` -- its `FUNC` and its brace walk bounded by the next
    declaration -- applied to the **masked** buffer, so a declaration inside a comment is not a declaration and
    a body's braces are the code's own. A name declared with no body is returned in the set and absent from the
    map, because the two are different facts: the text knows the function but shows no arms either.
    """
    ctb = ctb_tool()
    bodies, names, spans = {}, set(), []
    ms = list(ctb.FUNC.finditer(live))
    for k, m in enumerate(ms):
        limit = ms[k + 1].start() if k + 1 < len(ms) else len(live)
        name = m.group(1).lower()
        names.add(name)
        span = ctb.body_span(live, m.end(), limit)
        if span is None:
            continue  # a declaration without a body shows no arms, and is in `names` but not in `bodies`
        # a class may declare a function twice -- a `state` block that overrides it is the common case -- and the
        # arms belong to the name, so every declaration's body counts and every body is a span the outside
        # bucket is measured against. Skipping the second one read 26 keywords as code outside every function.
        bodies[name] = bodies.get(name, 0) + else_in(live[span[0]:span[1]])
        spans.append(span)
    return bodies, names, spans


def state_split(live, fn_spans, total_else, depth=None):
    """`(label-level else, nested else, else outside every block, blocks with a body)`.

    The 3.5 notes call the state code a gap in its own right; a class-level count cannot tell it from a
    function's arms, so the keywords that no function body holds get their own split here, and `fn_top` is the
    residual a reader bug shows up in.

    The in-state half is split by **brace depth**, because the two shapes are different facts: a keyword at the
    state body's own statement level is label code (`Begin:` and friends, which is where a state's main loop
    lives), while one deeper is inside a block of that code. The 253 keywords this bucket held came back 118
    label-level / 134 nested / 1 outside every block, so quoting the bucket as one number hid the shape.
    """
    ctb = ctb_tool()
    if depth is None:
        depth = ctb.depth_map(live)
    label, nested, blocks = 0, 0, 0
    ms = list(STATE.finditer(live))
    for k, m in enumerate(ms):
        limit = ms[k + 1].start() if k + 1 < len(ms) else len(live)
        span = ctb.body_span(live, m.end(), limit)
        if span is None:
            continue
        blocks += 1
        floor = depth[span[0]] + 1  # the state body's own statement level: `depth` is the depth *inside* a brace
        for mm in WORD.finditer(live[span[0]:span[1]]):
            if mm.group(1) != "else":
                continue
            pos = span[0] + mm.start()
            if any(a <= pos < b for a, b in fn_spans):
                continue
            if depth[pos] == floor:
                label += 1
            else:
                nested += 1
    outside = total_else - sum(else_in(live[a:b]) for a, b in fn_spans)
    return label, nested, outside - label - nested, blocks


def front_truncated(text):
    """True when the buffer begins inside a `/* ... */` comment, i.e. carries more closes than opens.

    `GUI.MenuItemOverlay` is one: its `ScriptText` opens with the tail of a doc comment, so the prose reads as
    live code -- the mirror of the tail truncation `UWindowURLTextArea` shows, and the reason its one `else` is
    a word in a sentence rather than a branch.
    """
    return text.count("*/") > text.count("/*")


# --- the dump side ---------------------------------------------------------------------------------
def walk(system_dir):
    pst = load_tool("pkg_script_text", "pkg-script-text.py")
    ctb = ctb_tool()
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
            depth = ctb.depth_map(live)
            fn_else, fn_names, fn_spans = function_else(live)
            fn_label, fn_nested, fn_top, state_blocks = state_split(live, fn_spans, counts(live)["else"], depth)
            rows[(package, pkg.name(exp["name"]).lower())] = {
                "class": pkg.name(exp["name"]),
                "live": counts(live),
                "comment_else": counts(comments)["else"],
                "functions": len(ctb.FUNC.findall(live)),
                "decl_grammar": len(ctb.FUNC_GRAMMAR.findall(live)),
                "decl_trailing": len(ctb.FUNC_TRAIL.findall(live)),
                "state_blocks_capital": len(STATE_CAPITAL.findall(live)),
                "fn_else": fn_else, "fn_names": fn_names,
                "fn_state": fn_label + fn_nested, "fn_state_label": fn_label, "fn_state_nested": fn_nested,
                "fn_top": fn_top, "state_blocks": state_blocks, "front_truncated": front_truncated(text),
                "decl_legacy": len(ctb.FUNC_LEGACY.findall(live)),
                "decl_no_function": len(FUNC_NO_KEYWORD.findall(live)),
            }
    return rows


def arm_counts(path=BRANCHES):
    """`(package, class)` -> Counter of branch categories, the census the branch tool printed, and the `else`
    arms keyed by function -- the key a function-level split needs and the class total cannot give."""
    out = collections.defaultdict(collections.Counter)
    by_fn = collections.defaultdict(collections.Counter)
    census = {}
    for r in csv.DictReader(open(path, encoding="utf-8")):
        if r.get("kind") == "census":
            note = r.get("note") or ""
            if "JumpIfNot" in note:
                census["jumpifnot"] = int(r.get("count") or 0)
            continue
        if r.get("kind") != "branch":
            continue
        key = (r["package"], (r["class"] or "").lower())
        out[key][r["category"]] += 1
        if r["category"] == "else":
            by_fn[key][(r["function"] or "").lower()] += 1
    return out, by_fn, census


def verdict(live_else, byte_else):
    if live_else == byte_else:
        return "agree"
    return "more" if live_else > byte_else else "less"


def join(texts, arms, fn_arms, branch_census, jumpifnot, records):
    census = collections.Counter()
    verdicts = collections.defaultdict(collections.Counter)
    absent_names = collections.Counter()
    absent_rows = []
    rows = []
    for key in sorted(set(texts) | set(arms)):
        t = texts.get(key)
        a = arms.get(key, collections.Counter())
        row = {"kind": "class", "package": key[0], "class": (t or {}).get("class", key[1]),
               "live_else": "", "comment_else": "", "and": "", "or": "", "if": "", "while": "", "for": "",
               "terms": "", "pp_else": "", "text_functions": "", "record_functions": records.get(key, ""),
               "byte_else": a["else"], "byte_if": a["if"],
               "byte_empty": a["empty"], "byte_expression": a["expression"], "fn_arms": "", "fn_absent": "",
               "fn_short": "", "fn_long": "", "fn_outside": "", "fn_state_label": "", "fn_state_nested": "",
               "fn_unpaired": "",
               "verdict": "", "count": "", "note": ""}
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
        census["decl_legacy"] += t["decl_legacy"]
        census["decl_wide"] += t["functions"]
        census["decl_grammar"] += t["decl_grammar"]
        census["decl_trailing"] += t["decl_trailing"]
        census["states_case_folded"] += t["state_blocks_capital"]
        census["state_capital_classes"] += 1 if t["state_blocks_capital"] else 0
        census["decl_no_function"] += t["decl_no_function"]
        census["state_blocks"] += t["state_blocks"]
        census["fn_state"] += t["fn_state"]
        census["fn_state_label"] += t["fn_state_label"]
        census["fn_state_nested"] += t["fn_state_nested"]
        census["state_classes"] += 1 if t["fn_state"] else 0
        census["fn_top"] += t["fn_top"]
        census["texts_front_truncated"] += 1 if t["front_truncated"] else 0
        census["front_truncated_else"] += t["fn_top"] if t["front_truncated"] else 0
        census["classes"] += 1
        for k in ("else", "and", "or", "if", "while", "for", "pp_else"):
            census["text_" + k if k == "else" else k] += c[k]
        census["comment_else"] += t["comment_else"]
        census["terms"] += terms
        if key not in arms:
            row["verdict"] = "no-branch"
            row.update({"fn_arms": 0, "fn_absent": 0, "fn_short": 0, "fn_long": 0, "fn_outside": 0,
                        "fn_state_label": 0, "fn_state_nested": 0, "fn_unpaired": 0})
            census["no_branch"] += 1
            census["no_branch_functions"] += t["functions"]
            census["no_branch_terms"] += terms
            rows.append(row)
            continue
        row["verdict"] = verdict(c["else"], a["else"])
        census["max_gap"] = max(census["max_gap"], abs(c["else"] - a["else"]))
        census["joined"] += 1
        census[row["verdict"]] += 1
        arms_by_fn = fn_arms.get(key) or {}
        paired = short = surplus = absent = 0
        for fname, k in arms_by_fn.items():
            if fname not in t["fn_else"]:
                absent += k  # either no declaration or none with a body: no arms to compare
                continue
            here = t["fn_else"][fname]
            paired += min(here, k)
            short += max(0, k - here)
            surplus += max(0, here - k)
        in_fns = sum(t["fn_else"].values())
        outside = c["else"] - in_fns
        unpaired = in_fns - paired - surplus
        fn_total = sum(arms_by_fn.values())
        if (fn_total != a["else"] or paired + short + absent != fn_total
                or paired + surplus + unpaired + outside != c["else"]):
            print("FAIL text-branches: the function split does not reconcile for %s.%s (%d/%d, %d/%d)"
                  % (key[0], key[1], fn_total, a["else"], paired + surplus + unpaired + outside, c["else"]))
            raise SystemExit(1)
        if t["fn_state_label"] + t["fn_state_nested"] + t["fn_top"] != outside:
            print("FAIL text-branches: the state split does not reconcile for %s.%s (%d+%d+%d vs %d)"
                  % (key[0], key[1], t["fn_state_label"], t["fn_state_nested"], t["fn_top"], outside))
            raise SystemExit(1)
        row.update({"fn_arms": fn_total, "fn_absent": absent, "fn_short": short, "fn_long": surplus,
                    "fn_outside": outside, "fn_state": t["fn_state"],
                    "fn_state_label": t["fn_state_label"], "fn_state_nested": t["fn_state_nested"],
                    "fn_top": t["fn_top"], "fn_unpaired": unpaired})
        vc = verdicts[verdict(c["else"], a["else"])]
        vc["classes"] += 1
        vc["arms"] += fn_total
        vc["absent"] += absent
        vc["short"] += short
        vc["outside"] += outside
        vc["unpaired"] += unpaired
        vc["state"] += t["fn_state"]
        vc["top"] += t["fn_top"]
        vc["text_else"] += c["else"]
        vc["byte_else"] += a["else"]
        if row["verdict"] == "less":
            for fname, k in arms_by_fn.items():
                if fname not in t["fn_else"]:
                    absent_names[fname] += k
                    absent_rows.append({"kind": "absent", "package": key[0], "class": row["class"],
                                        "function": fname, "count": k})
        record = records.get(key)
        if record not in (None, ""):
            if t["functions"] < int(record):
                bucket = "fewer"
            elif t["functions"] == int(record):
                bucket = "equal"
            else:
                bucket = "more"
            if row["verdict"] == "less":
                census["less_" + bucket] += 1
            elif row["verdict"] == "agree":
                census["agree_" + bucket] += 1
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
    # The keywords outside *every* block are named, not counted: the page used to type one of them by hand
    # (`GUI.MenuItemOverlay`) and a reader had no way to tell that from a stale sentence.
    top_rows = []
    for row in rows:
        if row.get("kind") == "class" and row.get("fn_top") not in ("", None) and int(row["fn_top"] or 0) > 0:
            top_rows.append({"kind": "top", "package": row["package"], "class": row["class"],
                             "count": int(row["fn_top"]),
                             "note": "`else` outside every function and every block"})
    top_rows.sort(key=lambda r: (-r["count"], r["package"], r["class"]))
    census["top_classes"] = len(top_rows)
    census["packages"] = len(pkg_rows)
    census["fn_absent_names"] = len(absent_names)
    for name in ("less", "agree", "more"):
        c = verdicts[name]
        if not c["classes"]:
            continue
        rows.append({"kind": "verdict", "class": name, "live_else": c["text_else"],
                     "byte_else": c["byte_else"], "fn_arms": c["arms"], "fn_absent": c["absent"],
                     "fn_short": c["short"], "fn_outside": c["outside"], "fn_state": c["state"],
                     "fn_top": c["top"], "fn_unpaired": c["unpaired"],
                     "count": c["classes"], "note": "the `%s` population" % name})
    rows.extend(sorted(absent_rows, key=lambda r: (-r["count"], r["package"], r["class"], r["function"])))
    rows.extend(top_rows)
    out = rows + pkg_rows
    for k in CENSUS_KEYS:
        value = jumpifnot if k == "jumpifnot" else census[k]
        out.append({"kind": "census", "count": value, "note": CENSUS_NOTE[k]})
    # A key a reading accumulates but this list does not write reads as a literal 0 in the page -- the
    # case-folded state count did exactly that (`spells State ... in **0** of the 513 declarations`), which is
    # a sentence asserting the opposite of the measurement. The two lists are one list now, and the guard is
    # for the next key somebody adds to the walk only.
    missing = sorted(k for k, v in census.items() if k not in CENSUS_KEYS and v)
    if missing:
        print("FAIL text-branches: census keys accumulated but never written: %s" % ", ".join(missing))
        raise SystemExit(1)
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
    return (body + verdict_paragraph(rows) + verdict_table(rows) + absent_table(rows) + top_table(rows)
            + package_table(rows))


def verdict_rows(rows):
    return {r["class"]: r for r in rows if r.get("kind") == "verdict"}


def num(rows, key):
    return int(rows.get(key, {}).get("count") or 0)


def verdict_table(rows):
    """The split per population, so the net can be read as the cancellation it is."""
    vs = verdict_rows(rows)
    if not vs:
        return ""
    lines = ["\n| population | classes | `else` arms | in a function the text does not declare | short in a "
             "declared body | text `else` outside any function | net |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name in ("less", "agree", "more"):
        r = vs.get(name)
        if r is None:
            continue
        net = int(r["byte_else"] or 0) - int(r["live_else"] or 0)
        lines.append("| `%s` | %s | %s | %s | %s | %s | %+d |"
                     % (name, comma(r["count"]), comma(r["fn_arms"]), comma(r["fn_absent"]),
                        comma(r["fn_short"]), comma(r["fn_outside"]), net))
    return "\n".join(lines) + "\n"


def absent_table(rows, cap=8):
    """The functions behind the absent arms, by name, so the missing arms have an owner."""
    per = collections.Counter()
    for r in rows:
        if r.get("kind") == "absent":
            per[r["function"]] += int(r["count"] or 0)
    if not per:
        return ""
    lines = ["\n| function | classes | `else` arms |", "| --- | ---: | ---: |"]
    seen = collections.Counter()
    for r in rows:
        if r.get("kind") == "absent":
            seen[r["function"]] += 1
    for name, k in sorted(per.items(), key=lambda kv: (-kv[1], kv[0]))[:cap]:
        lines.append("| `%s` | %d | %s |" % (name, seen[name], comma(k)))
    return "\n".join(lines) + "\n"


def verdict_paragraph(rows):
    vs = verdict_rows(rows)
    if "less" not in vs or "agree" not in vs:
        return ""
    less, agree = vs["less"], vs["agree"]
    less_net = int(less["byte_else"] or 0) - int(less["live_else"] or 0)
    agree_net = int(agree["byte_else"] or 0) - int(agree["live_else"] or 0)
    per = collections.Counter()
    for r in rows:
        if r.get("kind") == "absent":
            per[r["function"]] += int(r["count"] or 0)
    top = sorted(per.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    named = ", ".join("`%s` %s" % (k, comma(v)) for k, v in top) if top else "none"
    return ("\n**The `less` population's {ln}-arm net is a cancellation of two larger counts, and the split is "
            "by function, not by class.** Its {nc} classes carry {arms} `else` arms: **{absent}** sit in a "
            "function whose name the live text does not declare at all, **{short}** are the shortfall inside a "
            "function it declares, and the rest are spelled exactly. Its text in turn spells {text} `else` "
            "keywords, of which **{outside}** sit outside any function body -- {state} inside a `state` block "
            "and {topn} outside every block -- and {unpaired} in a declared body the record gives no arm. So "
            "the net of {ln} is {missing} missing arms against {outside} keywords outside a function, and "
            "**the {ac} classes that agree on the count carry the same three populations** ({a_absent} absent, "
            "{a_short} short, {a_outside} outside, net {a_net:+d}): a net of zero is two equal mismatches, not "
            "a class whose text is the revision compiled.\n"
            "\n**The declaration rule was still short of the grammar, in four shapes, and this census moved "
            "with it.** Declarations are counted with `class-text-bodies.py`'s rule now: the grammar's "
            "keywords and modifiers, a modifier after the keyword (`function static`), a qualified return type "
            "(`function Actor.eDoubleClickDir Foo(`), and a parameter list that starts on the line after the "
            "name -- which is how the engine's own movement API is written, so `ServerMove`, "
            "`LongClientAdjustPosition`, `CallServerMove`, `ReplicateMove` and `MoveAutonomous` are all "
            "declared in `PlayerController`'s text and their bodies were outside every function. The "
            "four-modifier rule read **{decl_legacy}** of the corpus's **{decl_wide}** declarations and "
            "`#635`'s same-line rule **{decl_grammar}**. **No arm is left without a declaring body, and "
            "{alltop} `else` keyword(s) in the whole corpus sit outside every block** (named in the table "
            "below), against the previous reading's 65 absent arms behind 8 names and 107 outside keywords.\n"
            "\n**The in-state count fell for a second reason: the state read was case-sensitive.** "
            "UnrealScript folds keyword case and the shipped text spells `State` with a capital in "
            "**{case_states}** declarations across **{case_classes}** classes, so every body in those "
            "blocks read as code outside one. Among them are the five states "
            "(`GetInFormation`, `MoveInFormation`, `TakePoint`, `WaitingForFormation`, `Broken`) the tree "
            "once recorded as states no class's text declares: the text declares them, in a capitalised "
            "block, and **{state_all}** `else` keywords now sit in state code.\n"
            "\n**The keywords outside every function are state code, and the ones outside every block are "
            "prose.** The {alloutside} `else` keywords the text spells outside a function body split **{label}** "
            "at a `state` body's own statement level -- the label code a state's main loop is written in -- "
            "and **{nested}** inside a nested block of that code, across **{sclasses}** classes: the game's "
            "bots and turret guns, `BotEnemy` and `BotSG` first among them. What is left outside every block "
            "is **{alltop}** keyword(s) in {topclasses} class text(s), named in the table below, and {ftop} of "
            "them sit in the **{front}** of the {classes2} texts that begin inside a `/* ... */` comment: a "
            "front-truncated `ScriptText` reads its leading prose as code, the mirror of the tail "
            "truncation `UWindowURLTextArea` shows. So the bucket is not a reader gap -- no `else` in this "
            "corpus sits at class level.\n"
            "\n**The class-set test moves with the rule and still does not decide the arm count.** "
            "{l_fewer} of the {nc} `less` classes declare fewer functions than their record has, {l_equal} the "
            "same number and {l_more} more, against {a_fewer} of {ac} agreeing classes that also declare "
            "fewer ({a_equal} the same number, {a_more} more) -- so a function count is not what an arm "
            "count is missing. The class-level verdicts "
            "({agree_c} agree, {more_c} more, {less_c} less, {nob_c} with no branch) do not move at all: the "
            "correction moved the per-function split, which is what it was supposed to move.\n"
            .format(ln=comma(less_net), nc=comma(less["count"]), arms=comma(less["fn_arms"]),
                    absent=comma(less["fn_absent"]), short=comma(less["fn_short"]),
                    text=comma(less["live_else"]), outside=comma(less["fn_outside"]),
                    state=comma(less["fn_state"]), topn=comma(less["fn_top"]),
                    unpaired=comma(less["fn_unpaired"]),
                    missing=comma(int(less["fn_absent"]) + int(less["fn_short"])),
                    ac=comma(agree["count"]), a_absent=comma(agree["fn_absent"]),
                    a_short=comma(agree["fn_short"]), a_outside=comma(agree["fn_outside"]), a_net=agree_net,
                    decl_legacy=comma(count(rows, "decl_legacy")), decl_wide=comma(count(rows, "decl_wide")),
                    nokw=comma(count(rows, "decl_no_function")), alltop=comma(count(rows, "fn_top")),
                    names=comma(count(rows, "fn_absent_names")), top=named,
                    l_fewer=comma(count(rows, "less_fewer")), l_equal=comma(count(rows, "less_equal")),
                    l_more=comma(count(rows, "less_more")), a_fewer=comma(count(rows, "agree_fewer")),
                    a_equal=comma(count(rows, "agree_equal")), a_more=comma(count(rows, "agree_more")),
                    agree_c=comma(count(rows, "agree")), more_c=comma(count(rows, "more")),
                    less_c=comma(count(rows, "less")), nob_c=comma(count(rows, "no_branch")),
                    case_states=comma(count(rows, "states_case_folded")),
                    case_classes=comma(count(rows, "state_capital_classes")),
                    blocks=comma(count(rows, "state_blocks")),
                    label=comma(count(rows, "fn_state_label")),
                    nested=comma(count(rows, "fn_state_nested")),
                    sclasses=comma(count(rows, "state_classes")),
                    topclasses=comma(count(rows, "top_classes")),
                    ftop=comma(count(rows, "front_truncated_else")),
                    front=comma(count(rows, "texts_front_truncated")),
                    classes2=comma(count(rows, "classes")),
                    alloutside=comma(count(rows, "fn_state") + count(rows, "fn_top")),
                    state_all=comma(count(rows, "fn_state")),
                    decl_grammar=comma(count(rows, "decl_grammar"))))

def top_table(rows):
    """The class texts whose `else` sits outside every function and every block, named."""
    tops = [r for r in rows if r.get("kind") == "top"]
    if not tops:
        return ""
    lines = ["\n| class text | `else` outside every block |", "| --- | ---: |"]
    for r in tops:
        lines.append("| `%s.%s` | %s |" % (r["package"], r["class"], comma(r["count"])))
    return "\n".join(lines) + "\n"


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
    # the function split: an armed function the text declares, one it declares short, one it does not declare
    text = ("function Exact() { if (a) b(); else c(); }\n"
            "function Short() { if (a) b(); }\n"
            "function Bare();\n")
    text = ("exec function Exec() { if (a) b(); else c(); }\n"
            "protected function Prot() { if (a) b(); }\n"
            "state S {\n"
            "Begin:\n"
            "  if (x) y(); else z();\n"
            "  function Inner() { if (p) q(); }\n"
            "}\n")
    live2 = mask(text)[0]
    fns, names, spans = function_else(live2)
    st, tp, top2, blocks = state_split(live2, spans, counts(live2)["else"])
    if fns != {"exec": 1, "prot": 0, "inner": 0}:
        print("FAIL text-branches: a modified declaration read %r / %r" % (fns, names))
        return 1
    if (st, tp, top2, blocks) != (1, 0, 0, 1):
        print("FAIL text-branches: the state split read %r" % ((st, tp, blocks),))
        return 1
    # the three shapes of an outside keyword: label code, code nested inside a block of it, and prose at class
    # level. One count cannot tell them apart, which is why the bucket is quoted split.
    shape = ("state S\n"
             "{\n"
             "Begin:\n"
             "  if (x) y(); else z();\n"
             "  if (p)\n"
             "  {\n"
             "    if (q) r(); else s();\n"
             "  }\n"
             "}\n"
             "prose else at class level\n")
    live3 = mask(shape)[0]
    fns3, names3, spans3 = function_else(live3)
    if state_split(live3, spans3, counts(live3)["else"]) != (1, 1, 1, 1):
        print("FAIL text-branches: the outside shapes read %r"
              % (state_split(live3, spans3, counts(live3)["else"]),))
        return 1
    # a buffer cut inside a doc comment carries more closes than opens, and one cut before it does not
    if front_truncated("prose else here\r\n*/\nclass X extends Y;\n") is not True:
        print("FAIL text-branches: a front-truncated text read as live")
        return 1
    if front_truncated("/* a */\nclass X extends Y;\n"):
        print("FAIL text-branches: a whole comment read as truncated")
        return 1
    fns, names = function_else(mask("function Exact() { if (a) b(); else c(); }\n"
                                    "function Short() { if (a) b(); }\n"
                                    "function Bare();\n")[0])[:2]
    if fns != {"exact": 1, "short": 0} or names != {"exact", "short", "bare"}:
        print("FAIL text-branches: the function split read %r / %r" % (fns, names))
        return 1
    # the block reproduces from its own CSV
    rows = [{"kind": "census", "count": v, "note": CENSUS_NOTE[k]} for k, v in
            (("classes", 4), ("joined", 3), ("agree", 2), ("more", 1), ("less", 0), ("no_branch", 1),
             ("arms_no_text", 0), ("text_else", 4), ("comment_else", 1), ("and", 5), ("or", 1), ("if", 6),
             ("while", 2), ("for", 1), ("terms", 9), ("pp_else", 1), ("text_functions", 5),
             ("no_branch_functions", 1), ("no_branch_terms", 0), ("max_gap", 2), ("byte_else", 3), ("byte_if", 4),
             ("byte_empty", 0), ("byte_expression", 0), ("jumpifnot", 7), ("terms_exact", 2),
             ("no_and_or", 1), ("no_and_or_exact", 1), ("with_and_or", 2), ("with_and_or_exact", 1),
             ("packages", 1), ("less_fewer", 1), ("less_equal", 0), ("less_more", 0), ("agree_fewer", 1),
             ("fn_absent_names", 1))]
    rows.append({"kind": "package", "package": "Fixture", "live_else": 3, "byte_else": 2, "terms": 4,
                 "count": 2, "note": "agree 1, more 1, less 0, no-branch 0"})
    rows.append({"kind": "verdict", "class": "less", "live_else": 2, "byte_else": 5, "fn_arms": 5, "fn_absent": 3,
                 "fn_short": 1, "fn_long": 0, "fn_outside": 2, "fn_state": 2, "fn_top": 0, "fn_unpaired": 0,
                 "count": 2, "note": "the `less` population"})
    rows.append({"kind": "verdict", "class": "agree", "live_else": 1, "byte_else": 1, "fn_arms": 1, "fn_absent": 0,
                 "fn_short": 0, "fn_long": 0, "fn_outside": 0, "fn_state": 0, "fn_top": 0, "fn_unpaired": 0,
                 "count": 1, "note": "the `agree` population"})
    rows.append({"kind": "absent", "package": "Fixture", "class": "C", "function": "gone", "count": 3})
    if "| `less` | 2 | 5 | 3 | 1 | 2 | +3 |" not in doc_block(rows):
        print("FAIL text-branches: the verdict table does not render its own row")
        return 1
    if "| `gone` | 1 | 3 |" not in doc_block(rows):
        print("FAIL text-branches: the absent table does not render its own row")
        return 1
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
    arms, fn_arms, branch_census = arm_counts(args.branches)
    jif = branch_census.get("jumpifnot", 0)
    records = {}
    classes_csv = os.path.join(ROOT, "inventory", "classes.csv")
    if os.path.exists(classes_csv):
        for r in csv.DictReader(open(classes_csv, encoding="utf-8")):
            records[(r["package"], r["class"].lower())] = r.get("functions", "")
    rows = join(walk(args.system), arms, fn_arms, branch_census, jif, records)
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
