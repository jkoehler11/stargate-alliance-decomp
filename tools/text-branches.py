#!/usr/bin/env python3
"""What the shipped source spells, against what the bytecode compiled: `else`, `&&` and `||` (PLAN 3.7).

`inventory/jump_branches.csv` reads the jump graph back over the statement stream and finds **3,877** `else`
arms (and **170** arms that end by leaving their own loop, which is a `break`). Each class also ships its `ScriptText`, so the same question can be asked of the source: how many `else`
keywords does the live text spell? The two counts are independent measurements -- one is compiled bytes, the
other a text buffer the build shipped -- so a class where they disagree is a class whose text is not the
revision the package compiled (`tools/class-text-comments.py` finds 39 whose whole body sits inside a comment).

The text is masked before it is counted: comments first, then literals, with a **length-preserving** mask so the
counts are over the same buffer (`tools/decompiler-operators.py`'s order -- doing literals first runs a quote
match from an apostrophe in prose to the next one thousands of lines later and blanks the code between).
`#else` is a preprocessor directive, not a branch, and gets its own column instead of being subtracted silently.

The **build guards are evaluated**, not counted: this corpus ships the Xbox image, so `XBox`/`XBOX` and `DEBUG` are
defined and `PS2` and `PC` are not, and a keyword inside a part the build does not compile is counted in a `dead_*`
column rather than in the text's own. The macro set is *chosen by the join*, and the falsifier for it (the
functions an inverted reading breaks) is recorded in `docs/BYTECODE.md`'s guard section. `#ifdefPS2` spells the
directive with no space after its name, so the macro is the rest of the word.

The same pass counts `&&` and `||`, which the bytecode cannot name directly, and `if`/`while`/`for` as the
condition-term model: one `JumpIfNot` per term is what the codegen should emit, and the leftover is what the
class's text declares and its package does not compile.

    tools/text-branches.py --system DUMP/System -o inventory/text_branches.csv --write-doc docs/BYTECODE.md
    tools/text-branches.py --system DUMP/System --functions -o inventory/text_branches.csv
    tools/text-branches.py --report
    tools/text-branches.py --check-doc docs/BYTECODE.md   # from the committed artifacts
    tools/text-branches.py --check-functions              # the two artifacts reconcile, no dump
    tools/text-branches.py --check                        # synthetic, no dump, writes nothing

`--functions` writes a second artifact, `inventory/text_branch_functions.csv`, one row per function either
side names: the `else`/`if`/`while`/`for` the live text spells in that body against the `else` arms and
`JumpIfNot` tokens `inventory/jump_branches.csv` has in it. The class artifact's `fn_*` columns are the same
split summed, so `--check-functions` holds the two files to each other without the dump (five identities per
class), which is what makes a per-function row a measurement rather than a second reading.

Reads the packages plus `inventory/jump_branches.csv`; no bytecode walk, no boot, no Ghidra. Counts only:
nothing here prints or commits source text.
"""
import argparse
import collections
import contextlib
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
FUNC_CSV_PATH = os.path.join(ROOT, "inventory", "text_branch_functions.csv")
FUNC_MARK = ("<!-- generated:text-branch-functions -->", "<!-- /generated:text-branch-functions -->")

FUNC_FIELDS = ["package", "class", "function", "class_verdict", "body", "record",
               "text_else", "text_if", "text_while", "text_for", "terms", "text_and", "text_or",
               "text_else_dead", "text_else_empty", "text_terms_dead", "text_continue",
               "text_continue_bare", "text_continue_block", "text_continue_other", "text_continue_dead",
               "continue_place",
               "byte_else", "byte_if", "byte_empty", "byte_expression", "byte_leave", "jumpifnot",
               "else_verdict", "term_verdict", "short", "surplus", "unpaired", "pp", "note", "census"]

FUNC_NOTE = {
    "functions": "functions either side names with a branch in it",
    "text_functions": "functions the live text declares with a body that carries a condition",
    "record_functions": "functions the branch census has at least one `JumpIfNot` in",
    "both": "functions both sides have",
    "text_only": "a body with a condition and no jump in the record",
    "record_only": "a jump in the record and no declared body",
    "else_agree": "`both` functions whose `else` count is equal on the two sides",
    "else_short": "the record has more `else` arms than the text spells",
    "else_surplus": "the text spells more `else` than the record has arms",
    "else_unpaired": "the text spells `else` in a body the record gives no arm at all",
    "else_none": "`both` functions with no `else` on either side (counted as equal above)",
    "else_test": "`both` functions with an `else` arm or keyword on either side",
    "else_test_agree": "of those, equal on the count",
    "term_agree": "`both` functions whose condition-term count equals their `JumpIfNot` count",
    "term_short": "more `JumpIfNot` tokens than `if` + `while` + `for`",
    "term_surplus": "more condition terms than `JumpIfNot` tokens",
    "op_functions": "`both` functions whose text carries `&&` or `||`",
    "op_agree": "of those, exact on the term count",
    "nop_functions": "`both` functions whose text carries neither",
    "nop_agree": "of those, exact on the term count",
    "agree_both": "`both` functions inside a class whose own `else` count agrees",
    "agree_else_agree": "of those, exact on `else` per function",
    "agree_else_short": "of those, short of the record",
    "agree_else_surplus": "of those, ahead of the record",
    "more_both": "`both` functions inside a `more` class",
    "more_else_agree": "of those, exact on `else`",
    "more_else_short": "of those, short of the record",
    "more_else_surplus": "of those, ahead of the record",
    "less_both": "`both` functions inside a `less` class",
    "less_else_agree": "of those, exact on `else`",
    "less_else_short": "of those, short of the record",
    "less_else_surplus": "of those, ahead of the record",
    "agree_else_unpaired": "of those, spelled in a body with no arm at all",
    "more_else_unpaired": "of those, spelled in a body with no arm at all",
    "less_else_unpaired": "of those, spelled in a body with no arm at all",
    "arms": "`else` arms the joined functions carry",
    "terms": "condition terms those functions declare",
    "jumpifnot": "`JumpIfNot` tokens those functions carry",
    "terms_outside": "condition terms in the class texts that sit outside every function body",
    "term_over": "terms above the `JumpIfNot` count, summed over the functions",
    "term_under": "terms below it, summed likewise",
    "jumps_unresolved": "`JumpIfNot` tokens the branch census resolves that carry no branch row",
    "pp_functions": "functions whose declaration sits inside a `#if…#endif` guard",
    "text_only_pp": "of the text-only functions, those inside such a guard",
    "text_else_dead": "`else` keywords in these bodies inside a part this build does not compile",
    "text_else_empty": "`else` keywords in these bodies whose block is empty -- no arm to skip",
    "text_terms_dead": "`if` + `while` + `for` in the body inside such a part",
    "text_continue": "`continue` statements in the body",
    "text_continue_bare": "`continue` that is the whole then-block -- `if (X) continue;`, the arm the landing rules read",
    "text_continue_block": "`if` then-block holding statements before its `continue` -- same tokens, but the arm is longer than the single `Jump` the rules test",
    "text_continue_other": "`continue` that is not a then-block's last statement at all (a loop body's own, a `switch` arm's)",
    "text_continue_dead": "`continue` inside a part this build never compiles",
    "continue_place_exact": "short functions whose shortfall equals their block-bodied `continue` count -- one arm per site",
    "continue_place_text_ahead": "short functions with more block-bodied `continue` sites than arms (one site's arm reads as an `if`: its `Jump` lands on the jump's own target)",
    "continue_place_record_ahead": "short functions with an arm and no block-bodied `continue` (an `else if` chain's header arm, spelled with one `else`)",
    "text_only_dead": "text-only bodies whose branch text is entirely in a part this build does not compile",
    "text_only_live": "text-only bodies whose compiled text carries a condition the record has no jump for",
    "continue_short": "short functions whose gap is within the body's `continue` count",
    "short_beyond_continue": "short functions whose gap exceeds it -- the shape `continue` does not explain",
    "raw_surplus_fixed": "functions whose raw `else` count was ahead of the record and whose compiled count is exact",
    "raw_agree_only": "functions whose raw count was exact and whose compiled count is not -- the model's falsifier",
}

FUNC_KEYS = tuple(FUNC_NOTE)

FIELDS = ["kind", "package", "class", "function", "live_else", "dead_else", "dead_terms", "comment_else",
          "and", "or", "if", "while", "for",
          "terms", "pp_else", "else_empty", "guards", "guard_dead", "text_functions", "record_functions",
          "byte_else", "byte_if",
          "byte_empty",
          "byte_expression", "byte_leave", "fn_arms", "fn_absent", "fn_short", "fn_long", "fn_outside", "fn_state",
          "fn_state_label", "fn_state_nested", "fn_top", "fn_unpaired", "fn_else", "fn_dead_else", "fn_lost",
          "verdict", "verdict_fn", "count", "note"]

CENSUS_NOTE = {
    "classes": "classes whose `ScriptText` was readable",
    "packages": "packages with at least one joined class",
    "joined": "of those, classes the branch census also has",
    "agree": "classes whose live text spells exactly the `else` arms the bytecode has",
    "more": "classes whose live text spells more `else` than the bytecode has arms",
    "less": "classes whose live text spells fewer",
    "no_branch": "classes with readable text and no branch at all in the bytecode",
    "arms_no_text": "classes with branch arms but no readable `ScriptText`",
    "text_else": "`else` keywords in the live text, i.e. the parts this build compiles",
    "dead_else": "`else` keywords inside a build guard this build does not compile",
    "else_empty": "`else` keywords whose block is empty (`else { }`), which has no arm to skip",
    "dead_if": "`if` keywords in those parts",
    "dead_while": "`while` keywords in those parts",
    "dead_for": "`for` keywords in those parts",
    "dead_terms": "`if` + `while` + `for` in those parts",
    "guards": "`#if…#endif` parts (a then-segment or an `#else` segment)",
    "guard_dead": "of those, the parts no enclosing branch of this build compiles",
    "classes_guarded": "class texts carrying at least one guard part",
    "fn_dead_else": "dead `else` keywords inside a function body",
    "fn_lost": "dead `else` keywords outside every function body",
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
    "byte_leave": "then-arms that end by leaving their own loop -- a `break` there",
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
    "joined_fn": "classes with a readable class text and a branch in the record (the function-scope verdict)",
    "agree_fn": "of those, the ones whose in-body `else` count equals the record's `else` arms",
    "more_fn": "of those, the text spells more inside its function bodies",
    "less_fn": "of those, the record carries more arms than the in-body text spells",
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

CENSUS_KEYS = ("classes", "packages", "joined", "joined_fn", "agree", "more", "less", "agree_fn",
               "more_fn", "less_fn", "no_branch", "arms_no_text",
               "text_else", "dead_else", "else_empty", "dead_if", "dead_while", "dead_for", "dead_terms", "guards",
               "guard_dead", "classes_guarded", "fn_dead_else", "fn_lost",
               "comment_else", "and", "or", "if", "while", "for", "terms", "pp_else",
               "text_functions", "no_branch_functions", "no_branch_terms", "max_gap", "byte_else", "byte_if",
               "byte_empty", "byte_expression", "byte_leave", "jumpifnot", "terms_exact", "no_and_or", "no_and_or_exact",
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
# A build guard: the shipped text carries the PS2 branch of the engine (`#ifdefPS2` .. `#endif`, no space after the
# directive name), which this package never compiled -- so a function declared inside one is text the build did not
# build, not a body the join lost. `#[ \t]*if` matches `#if`, `#ifdef`, `#ifndef` and the run-together `#ifdefPS2`.
PP_OPEN = re.compile(r"(?m)^[ \t]*#[ \t]*if")
PP_CLOSE = re.compile(r"(?m)^[ \t]*#[ \t]*endif")
# A label statement -- `Begin:` at the start of a line. `state-tools`' entry census reads the state's `LabelTable`
# against these (tools/state-code.py), so the pattern is here with the other text scanners rather than there.
LABEL = re.compile(r"^[ \t]*([A-Za-z_]\w*)[ \t]*:(?!:)", re.M)
# The build guard: `#ifdefPS2`, `#ifndefPS2`, `#ifdefPC`, `#ifdefXBox`, `#ifdefDEBUG` .. `#endif`, with the macro as
# the rest of the word (there is no space after the directive name). This corpus is the Xbox image -- `default.xbe`,
# `symbols/xbox_functions.csv` -- and `DEBUG` is defined in it too, which the join *selects*: with `XBox`+`DEBUG`
# defined no function that agreed on the raw count breaks (the four other macro sets break 1, 8, 20 and 27), so the
# PS2 and PC branches are the ones this build never compiled. Macro names are case-folded, and that is measured as
# well: `#ifdefXBOX` read case-sensitively breaks 8 functions, so the preprocessor folds it.
GUARD_OPEN = re.compile(r"(?m)^[ \t]*#[ \t]*(ifdef|ifndef)(\w*)")
BUILD_DEFINED = frozenset({"xbox", "debug"})
# `continue` is not a branch keyword, but the shortfall population is `if (X) continue;`, so its body count is what
# bounds the arms the record has and the text does not spell.
CONTINUE = re.compile(r"(?<![\w$])continue(?![\w$])")
# An `else` whose block is empty has nothing to skip, so the compiler emits no arm for it: `if (X) y; else { }` is
# the straight-if shape. That is a text fact, not a stream reading, and it is what the last one-sided rows are.
ELSE_EMPTY = re.compile(r"(?<![\w$])else(?![\w$])[ \t\r\n]*(?:\{[ \t\r\n]*\}|;)")


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
    c["continue"] = len(CONTINUE.findall(text))
    return c


def else_in(text):
    """`else` keywords in a body, counted the way `counts` counts them (no `#else`, no `elsewhere`)."""
    return sum(1 for m in WORD.finditer(text) if m.group(1) == "else")


def guard_map(text):
    """`(dead, parts)`: `dead[i]` is 1 where `text` sits in a part this build does not compile.

    A part is a `#ifdef`/`#ifndef` then-segment or an `#else` segment; nesting is a stack, and a keyword is dead
    when *any* enclosing part is inactive. Activity is the preprocessor's own rule -- `#ifdefX` is active when `X`
    is defined, `#ifndefX` when it is not, and `#else` flips -- evaluated against `BUILD_DEFINED`. No directive in
    this corpus carries an expression, so there is no condition to parse; an unbalanced `#endif` is ignored (the
    2,319 texts all end at depth 0).
    """
    dead = bytearray(len(text))
    events = []
    for m in GUARD_OPEN.finditer(text):
        events.append((m.start(), "open", m.group(1).lower(), m.group(2)))
    for m in PP_ELSE.finditer(text):
        events.append((m.start(), "else", "", ""))
    for m in PP_CLOSE.finditer(text):
        events.append((m.start(), "close", "", ""))
    events.sort()
    stack, parts = [], []

    def active():
        for f in stack:
            if not f["parent"] or f["cond"] == f["other"]:
                return False
        return True

    pos = 0
    for at, kind, directive, macro in events:
        on = 1 if not active() else 0
        dead[pos:at] = bytes([on]) * (at - pos)
        pos = at
        if kind == "open":
            name = macro.lower()
            cond = (name in BUILD_DEFINED) if directive == "ifdef" else (name not in BUILD_DEFINED)
            stack.append({"parent": active(), "cond": cond, "other": False, "kind": directive,
                          "spell": "#" + directive + macro})
            parts.append({"spell": "#" + directive + macro, "kind": directive, "part": "then",
                          "open": at, "dead": 0 if active() else 1})
        elif kind == "else":
            if not stack:
                continue
            stack[-1]["other"] = not stack[-1]["other"]
            parts.append({"spell": stack[-1]["spell"], "kind": stack[-1]["kind"], "part": "else",
                          "open": at, "dead": 0 if active() else 1})
        elif kind == "close":
            if stack:
                stack.pop()
    on = 1 if not active() else 0
    dead[pos:] = bytes([on]) * (len(text) - pos)
    return dead, parts


def keyword_split(text, dead, a=0, b=None):
    """`(live, dead)`: Counters of `else`/`if`/`while`/`for` in `text[a:b]`, split by the guard map."""
    b = len(text) if b is None else b
    live, gone = collections.Counter(), collections.Counter()
    for m in WORD.finditer(text[a:b]):
        (gone if dead[a + m.start()] else live)[m.group(1)] += 1
    return live, gone


def empty_else(text, dead, a=0, b=None):
    """`else` keywords in `text[a:b]` whose block is empty -- no code to skip, so no arm in the record."""
    b = len(text) if b is None else b
    return sum(1 for m in ELSE_EMPTY.finditer(text, a, b) if dead is None or not dead[m.start()])


def _block_open(text, a, close):
    """The `{` matching `text[close] == '}'` inside `text[a:]`, or -1."""
    depth, i = 0, close
    while i >= a:
        if text[i] == "}":
            depth += 1
        elif text[i] == "{":
            depth -= 1
            if depth == 0:
                return i
        i -= 1
    return -1


def _header_is_if(text, a, close):
    """Whether the `(` matching `text[close] == ')'` opens an `if`'s condition, inside `text[a:]`."""
    depth, i = 0, close
    while i >= a:
        if text[i] == ")":
            depth += 1
        elif text[i] == "(":
            depth -= 1
            if depth == 0:
                break
        i -= 1
    if i < a:
        return False
    j = i - 1
    while j >= a and text[j] in " \t\r\n":
        j -= 1
    k = j
    while k >= a and (text[k].isalnum() or text[k] == "_"):
        k -= 1
    return text[k + 1:j + 1].lower() == "if"


def continue_sites(text, dead, a=0, b=None):
    """`[(offset, kind)]` for every `continue` statement in `text[a:b]`, classified by its then-block.

    `if (X) continue;` and `if (X) { …; continue; }` compile to the same three tokens an `if`/`else` does
    (`JumpIfNot` over a then-arm that ends in a `Jump` past the target), which is why the record has more `else`
    arms than the text spells `else`. The split is what says which sites the landing rules can read: **bare** is
    the whole then-block (the arm is the single `Jump`, which the rules test), **block** a then-block that holds
    statements in front of its `continue` (the arm is longer, so the rules cannot see it), **other** a `continue`
    that is not a then-block's last statement at all (a loop body's own, a `switch` arm's). A `continue` inside a
    part this build never compiles is `dead`, the same split `text_else_dead` makes on the `else` side.
    """
    b = len(text) if b is None else b
    out = []
    for m in CONTINUE.finditer(text, a, b):
        q = m.end()
        while q < b and text[q] in " \t\r\n":
            q += 1
        if q >= b or text[q] != ";":
            continue                       # an identifier that merely starts with the word
        kind = "other"
        r = q + 1
        while r < b and text[r] in " \t\r\n":
            r += 1
        if r < b and text[r] == "}":
            ob = _block_open(text, a, r)
            if ob >= 0:
                j = ob - 1
                while j >= a and text[j] in " \t\r\n":
                    j -= 1
                if j >= a and text[j] == ")" and _header_is_if(text, a, j):
                    # a comment (masked to spaces) in front of the `continue` is not a statement: the block
                    # then holds nothing but the continue, which is the bare shape one brace over.
                    kind = "bare" if not text[ob + 1:m.start()].strip(" \t\r\n;") else "block"
        else:
            j = m.start() - 1
            while j >= a and text[j] in " \t\r\n":
                j -= 1
            if j >= a and text[j] == ")" and _header_is_if(text, a, j):
                kind = "bare"
        if kind == "other":
            # The brace walk only speaks for a then-block it can name: a then-arm that is the last statement of
            # a function body closes a block whose opener is no `if`, and the header in front of the continue is
            # still the one that owns it.
            j = m.start() - 1
            while j >= a and text[j] in " \t\r\n":
                j -= 1
            if j >= a and text[j] == ")" and _header_is_if(text, a, j):
                kind = "bare"
        if dead is not None and dead[m.start()]:
            kind += "_dead"
        out.append((m.start(), kind))
    return out


def function_continue_sites(text, dead):
    """`name -> Counter of bare/block/other` (+ their `_dead` halves) over one class text's function bodies."""
    ctb = ctb_tool()
    out = collections.defaultdict(collections.Counter)
    ms = list(ctb.FUNC.finditer(text))
    for k, m in enumerate(ms):
        limit = ms[k + 1].start() if k + 1 < len(ms) else len(text)
        span = ctb.body_span(text, m.end(), limit)
        if span is None:
            continue
        for _off, kind in continue_sites(text, dead, span[0], span[1]):
            out[m.group(1).lower()][kind] += 1
    return out


def function_bodies(live, dead=None):
    """`(name -> Counter of the keyword in the body, name -> the dead part of it, names, spans)` for one text.

    Spans come from `tools/class-text-bodies.py` -- its `FUNC` and its brace walk bounded by the next
    declaration -- applied to the **masked** buffer, so a declaration inside a comment is not a declaration and
    a body's braces are the code's own. A name declared with no body is returned in the set and absent from the
    map, because the two are different facts: the text knows the function but shows no arms either.
    """
    ctb = ctb_tool()
    if dead is None:
        dead = guard_map(live)[0]
    kw, kwdead, names, spans = {}, {}, set(), []
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
        c = counts(live[span[0]:span[1]])
        c_live, c_dead = keyword_split(live, dead, span[0], span[1])
        e_empty = empty_else(live, dead, span[0], span[1])
        body = kw.setdefault(name, collections.Counter())
        gone = kwdead.setdefault(name, collections.Counter())
        for key in ("else", "if", "while", "for"):
            body[key] += c_live[key]
            gone[key] += c_dead[key]
        body["else"] -= e_empty      # an empty block has no arm to skip
        body["else_empty"] += e_empty
        for key in ("and", "or", "continue"):
            body[key] += c[key]
        spans.append(span)
    return kw, kwdead, names, spans


def pp_depth(text):
    """Per-offset `#if…` nesting depth, so a declaration can be placed inside a build guard."""
    depth = bytearray(len(text) + 1)
    events = sorted([(m.start(), 1) for m in PP_OPEN.finditer(text)]
                    + [(m.start(), -1) for m in PP_CLOSE.finditer(text)])
    pos, cur = 0, 0
    for p, delta in events:
        depth[pos:p] = bytes([cur]) * (p - pos)
        cur = max(0, cur + delta)
        pos = p
    depth[pos:] = bytes([cur]) * (len(text) + 1 - pos)
    return depth


def guarded_functions(live):
    """Names whose declaration sits inside a `#if…#endif` guard -- the PS2 branch of this build's source."""
    depth = pp_depth(live)
    return {m.group(1).lower() for m in ctb_tool().FUNC.finditer(live) if depth[m.start()]}


def function_else(live, dead=None):
    """`(name -> `else` in the body, every name declared, the body spans)` for one class text."""
    kw, _dead, names, spans = function_bodies(live, dead)
    return {n: c["else"] for n, c in kw.items()}, names, spans


def state_split(live, fn_spans, total_else, depth=None, dead=None):
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
            if dead is not None and dead[pos]:
                continue
            if ELSE_EMPTY.match(live, pos):
                continue    # an empty block is not an arm, so it is not state code either
            if any(a <= pos < b for a, b in fn_spans):
                continue
            if depth[pos] == floor:
                label += 1
            else:
                nested += 1
    outside = total_else - sum(
        keyword_split(live, dead if dead is not None else bytearray(len(live)), a, b)[0]["else"]
        - empty_else(live, dead, a, b) for a, b in fn_spans)
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
            dead, parts = guard_map(live)
            fn_kw, fn_dead, fn_names, fn_spans = function_bodies(live, dead)
            fn_else = {n: c["else"] for n, c in fn_kw.items()}
            c_live, c_dead = keyword_split(live, dead)
            c = counts(live)
            raw_else = c["else"]
            e_empty = empty_else(live, dead)
            for key in ("else", "if", "while", "for"):
                c[key] = c_live[key]
            c["else"] -= e_empty    # an empty block is no arm: it is a keyword with nothing to skip
            fn_label, fn_nested, fn_top, state_blocks = state_split(live, fn_spans, c["else"], depth, dead)
            rows[(package, pkg.name(exp["name"]).lower())] = {
                "class": pkg.name(exp["name"]),
                "live": c,
                "dead": c_dead,
                "else_empty": e_empty,
                "raw_else": raw_else,
                "guards": len(parts),
                "guard_dead": sum(1 for p in parts if p["dead"]),
                "comment_else": counts(comments)["else"],
                "functions": len(ctb.FUNC.findall(live)),
                "decl_grammar": len(ctb.FUNC_GRAMMAR.findall(live)),
                "decl_trailing": len(ctb.FUNC_TRAIL.findall(live)),
                "state_blocks_capital": len(STATE_CAPITAL.findall(live)),
                "fn_else": fn_else, "fn_names": fn_names, "fn_kw": fn_kw, "fn_dead": fn_dead,
                "fn_pp": guarded_functions(live),
                "fn_continue": function_continue_sites(live, dead),
                "fn_state": fn_label + fn_nested, "fn_state_label": fn_label, "fn_state_nested": fn_nested,
                "fn_top": fn_top, "state_blocks": state_blocks, "front_truncated": front_truncated(text),
                "decl_legacy": len(ctb.FUNC_LEGACY.findall(live)),
                "decl_no_function": len(FUNC_NO_KEYWORD.findall(live)),
            }
    return rows


def arm_counts(path=BRANCHES):
    """`(package, class)` -> Counter of branch categories, the `else` arms keyed by function, the same keyed by
    category, and the census the branch tool printed.

    The `else` map is the key a class-level split needs and a class total cannot give; the per-category map is
    what the per-function artifact reads, so one pass over one committed census feeds both."""
    out = collections.defaultdict(collections.Counter)
    by_fn = collections.defaultdict(collections.Counter)
    by_cat = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
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
        by_cat[key][(r["function"] or "").lower()][r["category"]] += 1
        if r["category"] == "else":
            by_fn[key][(r["function"] or "").lower()] += 1
    return out, by_fn, by_cat, census


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
               "live_else": "", "dead_else": "", "dead_terms": "", "comment_else": "",
               "and": "", "or": "",
               "if": "", "while": "", "for": "",
               "terms": "", "pp_else": "", "guards": "", "guard_dead": "",
               "text_functions": "", "record_functions": records.get(key, ""),
               "byte_else": a["else"], "byte_if": a["if"],
               "byte_empty": a["empty"], "byte_expression": a["expression"], "byte_leave": a["leave"],
               "fn_arms": "", "fn_absent": "",
               "fn_short": "", "fn_long": "", "fn_outside": "", "fn_state_label": "", "fn_state_nested": "",
               "fn_unpaired": "", "fn_else": "", "fn_dead_else": "", "fn_lost": "",
               "verdict": "", "verdict_fn": "", "count": "", "note": ""}
        census["byte_else"] += a["else"]
        census["byte_if"] += a["if"]
        census["byte_empty"] += a["empty"]
        census["byte_expression"] += a["expression"]
        census["byte_leave"] += a["leave"]
        if t is None:
            row["verdict"] = "no-text"
            census["arms_no_text"] += 1
            rows.append(row)
            continue
        c = t["live"]
        terms = c["if"] + c["while"] + c["for"]
        row.update({"live_else": c["else"], "comment_else": t["comment_else"], "and": c["and"], "or": c["or"],
                    "if": c["if"], "while": c["while"], "for": c["for"], "terms": terms, "pp_else": c["pp_else"],
                    "dead_else": t["dead"]["else"], "else_empty": t["else_empty"],
                    "dead_terms": t["dead"]["if"] + t["dead"]["while"]
                    + t["dead"]["for"], "guards": t["guards"], "guard_dead": t["guard_dead"],
                    "text_functions": t["functions"]})
        census["dead_else"] += t["dead"]["else"]
        census["else_empty"] += t["else_empty"]
        if c["else"] + t["dead"]["else"] + t["else_empty"] != t["raw_else"]:
            print("FAIL text-branches: %s.%s reads %d `else`, %d dead + %d empty do not reconcile with %d"
                  % (key[0], key[1], c["else"], t["dead"]["else"], t["else_empty"], t["raw_else"]))
            raise SystemExit(1)
        census["dead_if"] += t["dead"]["if"]
        census["dead_while"] += t["dead"]["while"]
        census["dead_for"] += t["dead"]["for"]
        census["dead_terms"] += t["dead"]["if"] + t["dead"]["while"] + t["dead"]["for"]
        census["guards"] += t["guards"]
        census["guard_dead"] += t["guard_dead"]
        census["classes_guarded"] += 1 if t["guards"] else 0
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
            _dead_in = sum(x["else"] for x in t["fn_dead"].values())
            row.update({"fn_arms": 0, "fn_absent": 0, "fn_short": 0, "fn_long": 0, "fn_outside": 0,
                        "fn_state_label": 0, "fn_state_nested": 0, "fn_unpaired": 0,
                        "fn_else": sum(t["fn_else"].values()), "fn_dead_else": _dead_in,
                        "fn_lost": t["dead"]["else"] - _dead_in, "else_empty": t["else_empty"]})
            census["fn_dead_else"] += _dead_in
            census["fn_lost"] += t["dead"]["else"] - _dead_in
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
        dead_in_fns = sum(x["else"] for x in t["fn_dead"].values())
        dead_outside = t["dead"]["else"] - dead_in_fns
        # The same split on the text the guards take out: `live + dead` is the raw keyword count, and a keyword
        # sits either in a body or outside every body -- six identities, not a claim about either half.
        census["fn_dead_else"] += dead_in_fns
        census["fn_lost"] += dead_outside
        if dead_outside < 0 or dead_in_fns + dead_outside != t["dead"]["else"]:
            print("FAIL text-branches: the dead-keyword split does not reconcile for %s.%s (%d+%d vs %d)"
                  % (key[0], key[1], dead_in_fns, dead_outside, t["dead"]["else"]))
            raise SystemExit(1)
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
                    "fn_else": in_fns, "fn_dead_else": dead_in_fns, "fn_lost": dead_outside,
                    "fn_state_label": t["fn_state_label"], "fn_state_nested": t["fn_state_nested"],
                    "fn_top": t["fn_top"], "fn_unpaired": unpaired})
        # The same comparison at the scope both sides are counted on: the text's keywords *inside its function
        # bodies* against the arms the record carries for those bodies. `outside` is state code and prose (see the
        # split above), so a class's `more` verdict can be two state-body keywords against no `Function` arms.
        row["verdict_fn"] = verdict(in_fns, a["else"])
        census["joined_fn"] += 1
        census[row["verdict_fn"] + "_fn"] += 1
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
        jin = a["else"] + a["leave"] + a["if"] + a["empty"] + a["expression"]
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


# --- the per-function artifact ----------------------------------------------------------------------
def function_rows(texts, fn_cat, class_verdicts, class_terms=None, census_jif=None):
    """`(rows, census)` for the same join one level down: one row per function either side names.

    The class-level verdict is carried onto every row, because a class whose `else` totals agree can still be a
    cancellation -- a function with more arms than its text spells beside one with fewer -- and only the
    per-function row can say whether that is what the 82.7% is.
    """
    census = collections.Counter()
    rows = []
    keys = set()
    for key, t in texts.items():
        dead = t.get("fn_dead") or {}
        for name, c in (t.get("fn_kw") or {}).items():
            d = dead.get(name) or collections.Counter()
            if (c["if"] + c["while"] + c["for"] or c["else"]
                    or d["else"] or d["if"] + d["while"] + d["for"]):
                keys.add((key, name))
    for key, by in fn_cat.items():
        for name, c in by.items():
            if sum(c.values()):
                keys.add((key, name))
    for key, name in sorted(keys):
        t = texts.get(key)
        body = ((t or {}).get("fn_kw") or {}).get(name)
        gone = ((t or {}).get("fn_dead") or {}).get(name) or collections.Counter()
        cc = ((t or {}).get("fn_continue") or {}).get(name) or collections.Counter()
        term_dead = gone["if"] + gone["while"] + gone["for"]
        cats = (fn_cat.get(key) or {}).get(name) or collections.Counter()
        byte_else, byte_if = cats["else"], cats["if"]
        jin = byte_else + byte_if + cats["empty"] + cats["expression"] + cats["leave"]
        terms = (body["if"] + body["while"] + body["for"]) if body else 0
        text_else = body["else"] if body else 0
        row = {"package": key[0], "class": (t or {}).get("class") or key[1], "function": name,
               "class_verdict": class_verdicts.get(key, ""), "body": "body" if body else "",
               "record": "jump" if jin else "",
               "text_else": text_else, "text_if": body["if"] if body else 0,
               "text_while": body["while"] if body else 0, "text_for": body["for"] if body else 0,
               "terms": terms, "text_and": body["and"] if body else 0, "text_or": body["or"] if body else 0,
               "text_else_dead": gone["else"], "text_else_empty": body["else_empty"] if body else 0,
               "text_terms_dead": term_dead,
               "text_continue": body["continue"] if body else 0,
               "text_continue_bare": cc["bare"], "text_continue_block": cc["block"],
               "text_continue_other": cc["other"], "text_continue_dead": cc["bare_dead"] + cc["block_dead"]
               + cc["other_dead"], "continue_place": "",
               "byte_else": byte_else, "byte_if": byte_if, "byte_empty": cats["empty"],
               "byte_expression": cats["expression"], "byte_leave": cats["leave"], "jumpifnot": jin,
               "short": 0, "surplus": 0, "unpaired": 0,
               "pp": "yes" if name in ((t or {}).get("fn_pp") or set()) else "", "note": ""}
        if row["pp"]:
            census["pp_functions"] += 1
        census["functions"] += 1
        census["text_else_dead"] += gone["else"]
        census["text_else_empty"] += row["text_else_empty"]
        census["text_terms_dead"] += term_dead
        census["text_continue"] += row["text_continue"]
        for one in ("bare", "block", "other", "dead"):
            census["text_continue_" + one] += row["text_continue_" + one]
        if body is None:
            row["else_verdict"] = row["term_verdict"] = "no-text"
            row["short"] = byte_else  # every arm of it is one the text cannot account for
            census["record_only"] += 1
            census["record_functions"] += 1
            census["arms"] += byte_else
            census["jumpifnot"] += jin
            rows.append(row)
            continue
        census["text_functions"] += 1
        census["terms"] += terms
        if not jin:
            row["else_verdict"] = row["term_verdict"] = "no-record"
            row["unpaired"] = text_else  # a body with no jump in the record at all: its `else` has no arm
            census["text_only"] += 1
            census["text_only_pp"] += 1 if row["pp"] else 0
            # A text-only body whose branches are all in a part this build never compiled is not a join hole:
            # the compiled body has no condition for the record to have a jump for.
            if (gone["else"] or term_dead) and not (terms or text_else):
                census["text_only_dead"] += 1
            else:
                census["text_only_live"] += 1
            rows.append(row)
            continue
        census["both"] += 1
        census["record_functions"] += 1
        census["arms"] += byte_else
        census["jumpifnot"] += jin
        # A body with no *arm* at all is a different fact from one with fewer arms than keywords, and the class
        # artifact already separates them as `fn_unpaired` against `fn_long`; the row keeps the same rule or the
        # two artifacts disagree on six classes (a function whose jumps are all `if`).
        dv = ("agree" if text_else == byte_else else
              ("unpaired" if byte_else == 0 else ("short" if text_else < byte_else else "surplus")))
        tv = "agree" if terms == jin else ("short" if terms < jin else "surplus")
        row["else_verdict"], row["term_verdict"] = dv, tv
        row["short"] = max(0, byte_else - text_else)
        row["surplus"] = max(0, text_else - byte_else) if byte_else else 0
        row["unpaired"] = text_else if byte_else == 0 else 0
        # The guard model's own score, from the same rows: what the raw count got wrong either way.
        raw_else = text_else + gone["else"] + row["text_else_empty"]
        if raw_else > byte_else and text_else == byte_else:
            census["raw_surplus_fixed"] += 1
        if raw_else == byte_else and text_else != byte_else:
            census["raw_agree_only"] += 1
        if dv == "short":
            if row["short"] > row["text_continue"]:
                census["short_beyond_continue"] += 1
            else:
                census["continue_short"] += 1
            # Place the arm on the statement it belongs to. A block-bodied `continue` compiles to the same three
            # tokens an `if`/`else` does and the text spells no `else` at either, so the arm is the `continue`,
            # one site per arm; where the counts differ the two shapes that explain it are body facts.
            row["continue_place"] = ("exact" if row["short"] == row["text_continue_block"] else
                                     ("text_ahead" if row["text_continue_block"] > row["short"] else "record_ahead"))
            census["continue_place_" + row["continue_place"]] += 1
        if text_else == byte_else == 0:
            census["else_none"] += 1
        census["else_" + dv] += 1
        census["term_" + tv] += 1
        if body["and"] + body["or"]:
            census["op_functions"] += 1
            census["op_agree"] += 1 if tv == "agree" else 0
        else:
            census["nop_functions"] += 1
            census["nop_agree"] += 1 if tv == "agree" else 0
        cv = row["class_verdict"]
        if cv in ("agree", "more", "less"):
            census[cv + "_both"] += 1
            census[cv + "_else_" + dv] += 1
        rows.append(row)
    # The `else` test is only a test where there is an `else` to find: the 0=0 population would inflate the rate
    # the way a class-level count does when most of its functions are branch-free.
    census["else_test"] = census["both"] - census["else_none"]
    census["else_test_agree"] = census["else_agree"] - census["else_none"]
    # The class census counts a class's whole `if`/`while`/`for`; this artifact counts the ones inside a declared
    # body, so the difference is the condition terms in state code -- the bucket `fn_state` measures for `else`
    # only. It is what keeps a class-level term surplus from reading as a per-function one.
    if class_terms:
        per_class = collections.Counter()
        for r in rows:
            per_class[(r["package"], (r["class"] or "").lower())] += int(r["terms"] or 0)
        for key, total in class_terms.items():
            outside = int(total or 0) - per_class[key]
            if outside < 0:
                print("FAIL text-branches: %s.%s declares %d terms in functions, its class text %s"
                      % (key[0], key[1], per_class[key], total))
                raise SystemExit(1)
            census["terms_outside"] += outside
    for r in rows:
        d = int(r["terms"] or 0) - int(r["jumpifnot"] or 0)
        census["term_over" if d > 0 else "term_under"] += abs(d)
    census["jumps_unresolved"] = max(0, (census_jif or 0) - census["jumpifnot"])
    missing = sorted(k for k, v in census.items() if k not in FUNC_KEYS and v)
    if missing:
        print("FAIL text-branches: function census keys accumulated but never written: %s" % ", ".join(missing))
        raise SystemExit(1)
    out = list(rows)
    for k in FUNC_KEYS:
        out.append({"package": "census", "function": k, "census": census[k], "note": FUNC_NOTE[k]})
    return out


def func_render(rows):
    fh = io.StringIO()
    w = csv.DictWriter(fh, fieldnames=FUNC_FIELDS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in FUNC_FIELDS})
    return fh.getvalue()


def func_count(rows, key):
    for r in rows:
        if r.get("package") == "census" and r.get("function") == key:
            return int(r.get("census") or 0)
    return 0


def func_rows_of(rows):
    return [r for r in rows if r.get("package") != "census"]


def func_doc_block(rows):
    fns = func_rows_of(rows)
    both = func_count(rows, "both")
    if not both:
        return "\n(no per-function rows)\n"
    top = sorted(fns, key=lambda r: (-(int(r["short"] or 0) + int(r["surplus"] or 0)),
                                     r["package"], r["class"], r["function"]))[:8]

    def split(prefix):
        return (func_count(rows, prefix + "_both"), func_count(rows, prefix + "_else_agree"),
                func_count(rows, prefix + "_else_short"), func_count(rows, prefix + "_else_surplus"),
                func_count(rows, prefix + "_else_unpaired"))

    ga, gm, gl = split("agree"), split("more"), split("less")
    terms, jumps = func_count(rows, "terms"), func_count(rows, "jumpifnot")
    outside, unresolved = func_count(rows, "terms_outside"), func_count(rows, "jumps_unresolved")
    net_inside = terms - jumps
    v = dict(functions=comma(func_count(rows, "functions")),
             text_fns=comma(func_count(rows, "text_functions")),
             record_fns=comma(func_count(rows, "record_functions")), both=comma(both),
             text_only=comma(func_count(rows, "text_only")), record_only=comma(func_count(rows, "record_only")),
             text_only_pp=comma(func_count(rows, "text_only_pp")),
             else_agree=comma(func_count(rows, "else_agree")),
             else_pct="%.1f" % pct(func_count(rows, "else_agree"), both),
             else_short=comma(func_count(rows, "else_short")),
             else_surplus=comma(func_count(rows, "else_surplus")),
             else_unpaired=comma(func_count(rows, "else_unpaired")),
             else_none=comma(func_count(rows, "else_none")),
             else_test=comma(func_count(rows, "else_test")),
             else_test_agree=comma(func_count(rows, "else_test_agree")),
             else_test_pct="%.1f" % pct(func_count(rows, "else_test_agree"), func_count(rows, "else_test")),
             term_agree=comma(func_count(rows, "term_agree")),
             term_pct="%.1f" % pct(func_count(rows, "term_agree"), both),
             term_short=comma(func_count(rows, "term_short")),
             term_surplus=comma(func_count(rows, "term_surplus")),
             op_fns=comma(func_count(rows, "op_functions")), op_agree=comma(func_count(rows, "op_agree")),
             op_pct="%.1f" % pct(func_count(rows, "op_agree"), func_count(rows, "op_functions")),
             nop_fns=comma(func_count(rows, "nop_functions")), nop_agree=comma(func_count(rows, "nop_agree")),
             nop_pct="%.1f" % pct(func_count(rows, "nop_agree"), func_count(rows, "nop_functions")),
             terms=comma(terms), jumps=comma(jumps), outside=comma(outside),
             unresolved=comma(unresolved), surplus=comma(outside + net_inside - unresolved),
             net_inside="%+d" % net_inside if net_inside < 0 else comma(net_inside),
             agree_fns=comma(ga[0]), agree_ok=comma(ga[1]), agree_short=comma(ga[2]),
             agree_surplus=comma(ga[3]), agree_unpaired=comma(ga[4]), agree_cancel=comma(ga[0] - ga[1]),
             text_else_dead=comma(func_count(rows, "text_else_dead")),
             text_else_empty=comma(func_count(rows, "text_else_empty")),
             text_only_dead=comma(func_count(rows, "text_only_dead")),
             text_only_live=comma(func_count(rows, "text_only_live")),
             raw_surplus_fixed=comma(func_count(rows, "raw_surplus_fixed")),
             raw_agree_only=comma(func_count(rows, "raw_agree_only")),
             continue_short=comma(func_count(rows, "continue_short")),
             short_beyond_continue=comma(func_count(rows, "short_beyond_continue")),
             cont_bare=comma(func_count(rows, "text_continue_bare")),
             cont_block=comma(func_count(rows, "text_continue_block")),
             cont_other=comma(func_count(rows, "text_continue_other")),
             place_exact=comma(func_count(rows, "continue_place_exact")),
             place_text_ahead=comma(func_count(rows, "continue_place_text_ahead")),
             place_record_ahead=comma(func_count(rows, "continue_place_record_ahead")),
             kw_surplus=comma(sum(int(r["surplus"] or 0) for r in fns)),
             kw_unpaired=comma(sum(int(r["unpaired"] or 0) for r in fns)),
             more_fns=comma(gm[0]), more_ok=comma(gm[1]), more_short=comma(gm[2]), more_surplus=comma(gm[3]),
             more_unpaired=comma(gm[4]), more_span=comma(gm[0] - gm[1]),
             less_fns=comma(gl[0]), less_ok=comma(gl[1]), less_short=comma(gl[2]), less_surplus=comma(gl[3]),
             less_unpaired=comma(gl[4]), less_span=comma(gl[0] - gl[1]))
    out = ("\n**The same join one level down.** **{functions}** functions carry a branch on at least one side: "
           "**{text_fns}** the compiled text declares with a body that holds a condition, **{record_fns}** the branch "
           "census has a `JumpIfNot` in, **{both}** both, **{text_only}** text-only and **{record_only}** "
           "record-only; **{text_only_dead}** of the text-only bodies carry no compiled branch text at all -- every "
           "keyword they spell sits in a guard part this build never compiled -- and **{text_only_live}** are bodies "
           "whose compiled text has a condition the record has no jump for ({text_only_pp} of the {text_only} "
           "sit inside a guard, which is all the old `pp` flag measured). On the **{both}** functions both sides have, the `else` count is equal on "
           "**{else_agree}** (**{else_pct}%**) -- {else_none} of them because neither side spells one at all, so "
           "on the **{else_test}** functions that have an arm or a keyword the agreement is **{else_test_agree}** "
           "(**{else_test_pct}%**) -- short of the "
           "record on **{else_short}**, ahead of it on **{else_surplus}**, and **{else_unpaired}** spell `else` "
           "in a body the record gives no arm for at all; the condition-term model is exact on "
           "**{term_agree}** (**{term_pct}%**), with **{term_surplus}** functions ahead and **{term_short}** "
           "behind.\n"
           "\n**Two codegen rules close the text-ahead population, and what is left is the record's.** Every "
           "function whose raw `else` count was ahead of the record, or that spelled one in a body with no arm at "
           "all -- **{raw_surplus_fixed}** of them -- is exact once only compiled text counts: **{else_surplus}** "
           "still spell more than the record has arms (**{kw_surplus}** keywords) and **{else_unpaired}** spell one "
           "with no arm at all (**{kw_unpaired}**). Both rules are text facts, not stream readings: an `else` in a "
           "`#ifdef`/`#ifndef` part this build never compiles (**{text_else_dead}** keywords in these bodies) never "
           "reached the compiler, and an `else` whose block is empty (**{text_else_empty}** in these bodies) has "
           "nothing to skip, so `if (X) y; else {{ }}` is the straight-if shape the record carries. The other "
           "direction does not move: **{else_short}** functions carry more arms than their text spells, "
           "**{continue_short}** of them with a gap no larger than the body's `continue` count -- the "
           "`if (X) continue;` shape -- and **{short_beyond_continue}** beyond it, listed below. The model's own "
           "falsifier is **{raw_agree_only}**: functions that agreed on the raw count and are short once only "
           "compiled text counts -- zero under this macro set, and the alternatives break 1 to 27 of them "
           "(`docs/BYTECODE.md`'s guard section).\n"
           "\n**The shortfall is placed, one `continue` statement per arm.** Over the bodies the join shares there are "
           "**{cont_bare}** `if (X) continue;` (the whole then-block is the continue), **{cont_block}** "
           "`if (X) {{ …; continue; }}` (the then-block holds statements in front of it) and **{cont_other}** "
           "`continue` that is not a then-block's last statement at all. The landing rules read the first form, "
           "because its then-arm is the single `Jump` they test; the second writes the same three tokens with a "
           "longer arm, so it is what the record's extra arms are. Of the **{else_short}** short bodies "
           "**{place_exact}** carry exactly one block-bodied `continue` per arm, **{place_text_ahead}** carry one "
           "site more than arms -- that site's own arm reads as an `if`, its `Jump` landing on the jump's own "
           "target, so it is not an arm at all -- and **{place_record_ahead}** carry none, an `else if` chain's "
           "header arm, which the text spells with one `else` keyword. `continue_place` in "
           "`inventory/text_branch_functions.csv` holds the verdict per row.\n"
           "\n**A class whose totals agree is not a class whose functions agree.** The {agree_fns} functions "
           "inside the classes whose own `else` count agrees split **{agree_ok}** exact / {agree_short} short / "
           "{agree_surplus} ahead / {agree_unpaired} with no arm; inside the `less` classes the {less_fns} "
           "functions split {less_ok} / {less_short} / {less_surplus} / {less_unpaired}, and inside the `more` "
           "classes the {more_fns} functions split {more_ok} / {more_short} / {more_surplus} / "
           "{more_unpaired}. So a class-level agreement is a function-level "
           "cancellation on **{agree_cancel}** of its functions, and a class-level disagreement is one the text "
           "spells away on **{less_span}** of the `less` functions and **{more_span}** of the `more` ones -- the "
           "opposite of the same number, in the opposite population.\n"
           "\n**The operator rule is per function, where the operators are.** Of the {both} functions both "
           "sides have a branch in, **{op_fns}** carry `&&` or `||` in their text and are exact on the term "
           "count **{op_agree}** times (**{op_pct}%**); the **{nop_fns}** that carry neither are exact "
           "**{nop_agree}** times (**{nop_pct}%**). A class-level version of this test cannot say that, "
           "because a class with one `&&` anywhere is lumped with every function in it that has none.\n"
           "\n**The class-level term surplus is three populations, and a function's own count is none of them.** "
           "The live text declares **{terms}** condition terms inside a function body against the **{jumps}** "
           "`JumpIfNot` tokens the record carries there (**{net_inside}** more terms than jumps, net of the "
           "{term_surplus} functions ahead and {term_short} behind), while **{outside}** terms sit outside every "
           "function body -- state code, which a class-level count adds to a function's own -- and the branch "
           "census resolves **{unresolved}** tokens that carry no branch row. So the class-level surplus of "
           "**{surplus}** is {outside} outside a body + {net_inside} inside one - {unresolved} unresolved: a "
           "population sum, not a per-function defect.\n"
           "\n| class verdict | functions | `else` agree | short | ahead | no arm |\n"
           "| --- | ---: | ---: | ---: | ---: | ---: |\n"
           "| `agree` | {agree_fns} | {agree_ok} | {agree_short} | {agree_surplus} | {agree_unpaired} |\n"
           "| `more` | {more_fns} | {more_ok} | {more_short} | {more_surplus} | {more_unpaired} |\n"
           "| `less` | {less_fns} | {less_ok} | {less_short} | {less_surplus} | {less_unpaired} |\n"
           "\n| function | text `else` | arms | terms | `JumpIfNot` |\n| --- | ---: | ---: | ---: | ---: |\n"
           ).format(**v)
    for r in top:
        out += "| `%s.%s` | %s | %s | %s | %s |\n" % (r["class"], r["function"], comma(r["text_else"]),
                                                     comma(r["byte_else"]), comma(r["terms"]),
                                                     comma(r["jumpifnot"]))
    return out + residual_table(fns)


def residual_table(fns, cap=10):
    """The rows the compiled-text count does not explain, named -- a count with no name is what the next run reads."""
    pick = [r for r in fns if r.get("else_verdict") in ("surplus", "unpaired")]
    pick.sort(key=lambda r: (r["package"], r["class"], r["function"]))
    lines = ["\n| function | text `else` | arms | dead `else` | verdict |",
             "| --- | ---: | ---: | ---: | --- |"]
    for r in pick[:cap]:
        lines.append("| `%s.%s` | %s | %s | %s | `%s` |" % (r["class"], r["function"], comma(r["text_else"]),
                                                         comma(r["byte_else"]), comma(r["text_else_dead"]),
                                                         r["else_verdict"]))
    over = [r for r in fns if r.get("else_verdict") == "short"
            and int(r["short"] or 0) > int(r["text_continue"] or 0)]
    if over:
        lines += ["", "| function | arms - text | `continue` | dead `else` |", "| --- | ---: | ---: | ---: |"]
        for r in sorted(over, key=lambda r: (-int(r["short"] or 0), r["class"]))[:cap]:
            lines.append("| `%s.%s` | %s | %s | %s |" % (r["class"], r["function"], comma(r["short"]),
                                                       comma(r["text_continue"]), comma(r["text_else_dead"])))
    return "\n".join(lines) + "\n"


def check_functions(class_path=CSV_PATH, func_path=FUNC_CSV_PATH):
    """The two artifacts are one measurement at two levels, so five identities per class must hold.

    `fn_arms` sums the function rows' `byte_else`, `fn_absent` their record-only arms, `fn_short`/`fn_long` their
    shortfall and surplus inside a declared body, and `fn_unpaired` the `else` of a body the record has no jump
    in. Two corpus identities follow from the same two files: the condition terms that sit outside every function
    body (the class census minus the function rows) and the `JumpIfNot` tokens the census resolves but no branch
    row carries. No dump is read: a disagreement is a stale artifact on one side or the other.
    """
    if not os.path.exists(func_path):
        print("FAIL text-branches: %s is missing" % func_path)
        return 1
    allrows = read_csv(class_path)
    frows = read_csv(func_path)
    cls = [r for r in allrows if r.get("kind") == "class"]
    fns = [r for r in frows if r.get("package") != "census"]
    per = collections.defaultdict(collections.Counter)
    for r in fns:
        k = (r["package"], (r["class"] or "").lower())
        per[k]["arms"] += int(r["byte_else"] or 0)
        per[k]["terms"] += int(r["terms"] or 0)
        per[k]["jumpifnot"] += int(r["jumpifnot"] or 0)
        per[k]["else"] += int(r["text_else"] or 0)
        per[k]["dead"] += int(r.get("text_else_dead") or 0)
        if not r.get("body"):
            per[k]["absent"] += int(r["byte_else"] or 0)
            continue
        per[k]["short"] += int(r["short"] or 0)
        per[k]["surplus"] += int(r["surplus"] or 0)
        per[k]["unpaired"] += int(r["unpaired"] or 0)
    bad = []
    for r in cls:
        if r.get("fn_arms") in ("", None):
            continue
        k = (r["package"], (r["class"] or "").lower())
        c = per.get(k, collections.Counter())
        for col, key in (("fn_arms", "arms"), ("fn_absent", "absent"), ("fn_short", "short"),
                         ("fn_long", "surplus"), ("fn_unpaired", "unpaired"), ("fn_else", "else"),
                         ("fn_dead_else", "dead")):
            if int(r.get(col) or 0) != c[key]:
                bad.append("%s.%s %s: class %s, functions %s" % (r["package"], r["class"], col,
                                                                 r.get(col) or 0, c[key]))
        # The text side's own two halves: what the build compiled, and what it shipped and did not build.
        for col, key in (("fn_else", "else"), ("fn_dead_else", "dead")):
            if int(r.get(col) or 0) != c[key]:
                bad.append("%s.%s %s: class %s, functions %s" % (r["package"], r["class"], col,
                                                                 r.get(col) or 0, c[key]))
        if (int(r.get("fn_else") or 0) + int(r.get("fn_outside") or 0) != int(r.get("live_else") or 0)
                or int(r.get("fn_dead_else") or 0) + int(r.get("fn_lost") or 0) != int(r.get("dead_else") or 0)):
            bad.append("%s.%s the in-body and outside split does not add up (live %s/%s, dead %s/%s)"
                       % (r["package"], r["class"], r.get("fn_else") or 0, r.get("fn_outside") or 0,
                          r.get("fn_dead_else") or 0, r.get("fn_lost") or 0))
    joined = len([r for r in cls if r.get("fn_arms") not in ("", None)])
    outside = sum(int(r.get("terms") or 0) for r in cls) - sum(int(r["terms"] or 0) for r in fns)
    if outside != func_count(frows, "terms_outside"):
        bad.append("condition terms outside a function body: class census says %d, functions artifact %d"
                   % (outside, func_count(frows, "terms_outside")))
    if count(allrows, "dead_else") != count(allrows, "fn_dead_else") + count(allrows, "fn_lost"):
        bad.append("dead `else` keywords: %d in the text, %d in a body + %d outside every body"
                   % (count(allrows, "dead_else"), count(allrows, "fn_dead_else"), count(allrows, "fn_lost")))
    if sum(int(r.get("text_else_dead") or 0) for r in fns) != count(allrows, "fn_dead_else"):
        bad.append("dead `else` in a body: class census says %d, function rows %d"
                   % (count(allrows, "fn_dead_else"), sum(int(r.get("text_else_dead") or 0) for r in fns)))
    unresolved = max(0, count(allrows, "jumpifnot") - sum(int(r["jumpifnot"] or 0) for r in fns))
    if unresolved != func_count(frows, "jumps_unresolved"):
        bad.append("`JumpIfNot` tokens with no branch row: class census says %d, functions artifact %d"
                   % (unresolved, func_count(frows, "jumps_unresolved")))
    # The `continue` split is four counts of one regex over the same bodies, and the placement verdict is a
    # subtraction of two of its columns: both are recomputed here so the file cannot hold a stale one.
    split = sum(int(r["text_continue_" + one] or 0) for r in fns for one in ("bare", "block", "other", "dead"))
    if split != func_count(frows, "text_continue"):
        bad.append("the `continue` split covers %d of the %d statements the rows count"
                   % (split, func_count(frows, "text_continue")))
    for r in fns:
        if r.get("text_continue_block") in (None, "") or not r.get("body"):
            continue                      # no text to place against: a fixture, or a body the text does not declare
        n = int(r.get("short") or 0)
        if not n:
            if r.get("continue_place"):
                bad.append("%s.%s carries a `continue_place` and no shortfall" % (r["package"], r["function"]))
            continue
        block = int(r.get("text_continue_block") or 0)
        want = ("exact" if block == n else ("text_ahead" if block > n else "record_ahead"))
        if (r.get("continue_place") or "") != want:
            bad.append("%s.%s continue_place: row says %r, %d arm(s) against %d block-bodied `continue`"
                       % (r["package"], r["function"], r.get("continue_place"), n, block))
    if bad:
        for line in bad[:20]:
            print("FAIL text-branches: %s" % line)
        print("FAIL text-branches: %d disagreement(s) between inventory/text_branches.csv and "
              "inventory/text_branch_functions.csv" % len(bad))
        return 1
    print("ok   text-branches: the per-function artifact reconciles with the class artifact on all %d classes, "
          "%d terms outside a body and %d unresolved jumps" % (joined, outside, unresolved))
    return 0


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
    body = ("\n**The text this build compiles spells %s `else`** over the %s classes whose `ScriptText` parses "
            "(`%s` more sit in a build guard it never compiled), against the "
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
            "surplus terms sit in the %s joined classes that are not exact -- a class-level population (the state "
            "code and the unresolved jumps below), not a per-function defect.\n")
    body = body % (comma(count(rows, "text_else")), comma(classes), comma(count(rows, "dead_else")),
               comma(count(rows, "byte_else")),
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
    guards = ("\n**The build guards are evaluated, and they are what the one-sided classes were.** The %s class "
              "texts carry **%s** `#if…#endif` parts, **%s** of them in a branch this build does not compile -- the "
              "corpus is the Xbox image (`default.xbe`) and `XBox` and `DEBUG` are defined in it, so the `PS2` and "
              "`PC` branches are the ones it never built -- and those parts hold **%s** `else` keywords plus **%s** "
              "`if`, **%s** `for` and **%s** `while`. A further **%s** keywords sit in a live block that is empty "
              "(`else { }`), which has nothing to skip and so no arm. Every count above is the compiled part: the "
              "`else` column is %s against the record's %s arms, and `--check-functions` holds `live + dead + "
              "empty` to each text's raw keyword count, so the split is a measurement and not a subtraction done in "
              "prose. The macro set is not assumed either: `docs/BYTECODE.md`'s guard section scores the "
              "alternatives on this join, and the number that decides between them is the functions a reading "
              "*breaks*, not the agreement it buys.\n"
              % (comma(classes), comma(count(rows, "guards")), comma(count(rows, "guard_dead")),
                 comma(count(rows, "dead_else")), comma(count(rows, "dead_if")), comma(count(rows, "dead_for")),
                 comma(count(rows, "dead_while")), comma(count(rows, "else_empty")),
                 comma(count(rows, "text_else")), comma(count(rows, "byte_else"))))
    scope = ("\n**The class count is two scopes, and the class-level `more` population is the state code.** **%s** "
             "`else` keywords of those %s sit outside every function body -- **%s** in a `state` block and **%s** "
             "outside every block -- while `inventory/jump_branches.csv` carries `Function` arms only, so a class "
             "count mixes the two. At the scope both sides are counted on -- the text's in-body keywords against "
             "the arms the record carries for those bodies -- **%s of %s** classes agree (**%s%%**), **%s** spell "
             "more and **%s** fewer, and `verdict_fn` in `inventory/text_branches.csv` holds that verdict per class. "
             "So the %s classes the class-level count calls `more` are the ones whose text spells state code, not a "
             "second revision of the class: the state payloads carry those keywords as their own `else` arms, "
             "measured in `docs/systems/AI.md`.\n"
             % (comma(count(rows, "fn_state") + count(rows, "fn_top")), comma(count(rows, "text_else")),
                comma(count(rows, "fn_state")), comma(count(rows, "fn_top")),
                comma(count(rows, "agree_fn")), comma(count(rows, "joined_fn")),
                "%.2f" % pct(count(rows, "agree_fn"), count(rows, "joined_fn")),
                comma(count(rows, "more_fn")), comma(count(rows, "less_fn")), comma(count(rows, "more"))))
    return (body + guards + scope + verdict_paragraph(rows) + verdict_table(rows) + absent_table(rows)
            + top_table(rows) + package_table(rows))


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
            "count is missing. The class-level verdicts move with the same rule -- "
            "({agree_c} agree, {more_c} more, {less_c} less, {nob_c} with no branch over the joined classes) -- "
            "because a class agrees only when every function in it does; the per-function row is where the size "
            "effect that made the class figure the weaker one goes away.\n"
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


def splice_text(text, block, marks):
    lo, hi = marks
    if lo not in text or hi not in text:
        raise SystemExit("FAIL text-branches: markers %s not found" % lo)
    head, rest = text.split(lo, 1)
    _old, tail = rest.split(hi, 1)
    return head + lo + block + hi + tail


def splice(path, block, marks=DOC_MARK):
    return splice_text(open(path, encoding="utf-8").read(), block, marks)


def write_doc(path, rows, frows=None):
    text = splice(path, doc_block(rows))  # splice into a string first: opening for "w" truncates
    if frows is not None:
        text = splice_text(text, func_doc_block(frows), FUNC_MARK)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def check_doc(path, rows, frows=None):
    text = open(path, encoding="utf-8").read()
    rc = 0
    for marks, block, artifact in ((DOC_MARK, doc_block(rows), "inventory/text_branches.csv"),
                                   (FUNC_MARK, func_doc_block(frows or []),
                                    "inventory/text_branch_functions.csv")):
        lo, hi = marks
        if lo not in text or hi not in text:
            print("FAIL text-branches: markers %s not found in %s" % (lo, path))
            rc = 1
            continue
        if text.split(lo, 1)[1].split(hi, 1)[0] != block:
            print("FAIL text-branches: %s is stale for %s -- regenerate with `make text-branches`"
                  % (path, artifact))
            rc = 1
    if not rc:
        print("ok   text-branches: both docs blocks reproduce from their committed artifacts")
    return rc


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
    # The build guard: `#ifdefPS2` is a branch this Xbox build never compiles, `#ifndefPS2` is one it does, and
    # `#else` flips. A reader that got the polarity backwards passes every count test above and fails here.
    gsrc = ("function F()\n{\n"
            "#ifdefPS2\n  if (a) b(); else c();\n#else\n  if (d) e(); else f();\n#endif\n"
            "#ifndefPS2\n  if (g) h(); else i();\n#endif\n"
            "}\n")
    glive = mask(gsrc)[0]
    gdead, gparts = guard_map(glive)
    gkl, gkd = keyword_split(glive, gdead)
    if (gkl["else"], gkd["else"], gkl["if"], gkd["if"]) != (2, 1, 2, 1):
        print("FAIL text-branches: the guard split read live=%r dead=%r" % (dict(gkl), dict(gkd)))
        return 1
    if len(gparts) != 3 or sum(1 for p in gparts if p["dead"]) != 1:
        print("FAIL text-branches: the guard parts read %r" % (gparts,))
        return 1
    gkw, gkwdead, _gname, _gspan = function_bodies(glive, gdead)
    if (gkw["f"]["else"], gkwdead["f"]["else"]) != (2, 1):
        print("FAIL text-branches: the body guard split read %r / %r" % (dict(gkw["f"]), dict(gkwdead["f"])))
        return 1
    # a buffer cut inside a doc comment carries more closes than opens, and one cut before it does not
    if front_truncated("prose else here\r\n*/\nclass X extends Y;\n") is not True:
        print("FAIL text-branches: a front-truncated text read as live")
        return 1
    if front_truncated("/* a */\nclass X extends Y;\n"):
        print("FAIL text-branches: a whole comment read as truncated")
        return 1
    # The `continue` split: the bare form is the whole then-block, the block form holds statements before it, a
    # `continue` in a loop body's own tail is neither, and a comment in front of one is not a statement.
    csrc = ("function F()\n{\n"
            "  for (i = 0; i < n; i++)\n  {\n"
            "    if (a) continue;\n"
            "    if (b) { Log(); continue; }\n"
            "    if (c)\n    {\n      // disabled\n      continue;\n    }\n"
            "    if (d) { y(); }\n"
            "    continue;\n"
            "  }\n"
            "}\n")
    clive = mask(csrc)[0]
    cdead = guard_map(clive)[0]
    ck = collections.Counter(kind for _o, kind in continue_sites(clive, cdead))
    if (ck["bare"], ck["block"], ck["other"]) != (2, 1, 1):
        print("FAIL text-branches: the `continue` split read %r" % dict(ck))
        return 1
    # the `else` of a `switch` arm's `continue` is not a then-block, and a masked comment must not make one
    if continue_sites(mask("function G() { if (x) /* y */ continue; }")[0], None)[0][1] != "bare":
        print("FAIL text-branches: a comment in front of a `continue` read as a statement")
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
             ("byte_empty", 0), ("byte_expression", 0), ("byte_leave", 0), ("jumpifnot", 7), ("terms_exact", 2),
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
    # the per-function split: an exact function, one the record has more arms in, one the text has no body for
    texts = {("P", "c"): {"class": "C", "fn_kw": {
        "exact": collections.Counter({"else": 1, "if": 1}),
        "short": collections.Counter({"if": 1}),
        "op": collections.Counter({"if": 1, "and": 1})}}}
    fn_cat = {("P", "c"): {"exact": collections.Counter({"else": 1, "if": 1}),
                           "short": collections.Counter({"else": 2, "if": 0}),
                           "gone": collections.Counter({"else": 1})}}
    frows = function_rows(texts, fn_cat, {("P", "c"): "less"})
    if (func_count(frows, "both"), func_count(frows, "record_only"), func_count(frows, "text_only")) != (2, 1, 1):
        print("FAIL text-branches: the per-function split read %r"
              % ([(k, func_count(frows, k)) for k in ("both", "record_only", "text_only")],))
        return 1
    if (func_count(frows, "else_agree"), func_count(frows, "else_short"),
            func_count(frows, "term_short")) != (1, 1, 2):
        print("FAIL text-branches: the per-function verdicts read %r"
              % ([(k, func_count(frows, k)) for k in ("else_agree", "else_short", "term_short")],))
        return 1
    if "<tr>" in func_doc_block(frows):
        print("FAIL text-branches: the per-function block renders a marker as text")
        return 1
    tmp2 = tempfile.mkdtemp()
    try:
        cpath, fpath = os.path.join(tmp2, "class.csv"), os.path.join(tmp2, "fn.csv")
        with open(cpath, "w", encoding="utf-8") as fh:
            fh.write(render([{"kind": "class", "package": "P", "class": "C", "fn_arms": 4, "fn_absent": 1,
                              "fn_short": 2, "fn_long": 0, "fn_unpaired": 0, "terms": 3, "live_else": 1,
                              "fn_else": 1, "fn_outside": 0, "dead_else": 0, "fn_dead_else": 0,
                              "fn_lost": 0}]))
        with open(fpath, "w", encoding="utf-8") as fh:
            fh.write(func_render(frows))
        if func_doc_block(read_csv(fpath)) != func_doc_block(frows):
            print("FAIL text-branches: the per-function block does not reproduce from its own CSV")
            return 1
        quiet = io.StringIO()
        with contextlib.redirect_stdout(quiet):
            rc_ok = check_functions(cpath, fpath)
            with open(fpath, "w", encoding="utf-8") as fh:  # one arm moved: the identity must bite
                fh.write(func_render([dict(r, byte_else=int(r.get("byte_else") or 0) + 1) for r in frows]))
            rc_bad = check_functions(cpath, fpath)
        if rc_ok != 0:
            print("FAIL text-branches: the two synthetic artifacts do not reconcile: %s" % quiet.getvalue().strip())
            return 1
        if rc_bad == 0:
            print("FAIL text-branches: the reconciliation passed on a corrupted function row")
            return 1
    finally:
        for name in os.listdir(tmp2):
            os.unlink(os.path.join(tmp2, name))
        os.rmdir(tmp2)
    print("ok   text-branches: else, #else, comments and literals each read as themselves, and the block"
          " reproduces from its CSV")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--system", help="the dump's System directory")
    ap.add_argument("-o", "--out", default=CSV_PATH, help="the artifact (default inventory/text_branches.csv)")
    ap.add_argument("--branches", default=BRANCHES, help="the branch census to join (default as committed)")
    ap.add_argument("--functions", action="store_true",
                    help="also write the per-function artifact (needs --system)")
    ap.add_argument("--functions-out", default=FUNC_CSV_PATH,
                    help="the per-function artifact (default inventory/text_branch_functions.csv)")
    ap.add_argument("--write-doc", metavar="PAGE", help="render the generated blocks into this page")
    ap.add_argument("--check-doc", metavar="PAGE", help="fail when either block is stale")
    ap.add_argument("--check-functions", action="store_true",
                    help="the two artifacts reconcile; no dump, writes nothing")
    ap.add_argument("--report", action="store_true", help="print the census, no write")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; needs no dump and writes nothing")
    args = ap.parse_args()
    if args.check:
        return selftest()
    if args.check_functions:
        return check_functions(args.out, args.functions_out)
    if args.check_doc:
        frows = read_csv(args.functions_out) if os.path.exists(args.functions_out) else []
        return check_doc(args.check_doc, read_csv(args.out), frows)
    if not args.system:
        ap.error("a measurement needs --system (or --check / --check-doc / --check-functions)")
    arms, fn_arms, fn_cat, branch_census = arm_counts(args.branches)
    jif = branch_census.get("jumpifnot", 0)
    records = {}
    classes_csv = os.path.join(ROOT, "inventory", "classes.csv")
    if os.path.exists(classes_csv):
        for r in csv.DictReader(open(classes_csv, encoding="utf-8")):
            records[(r["package"], r["class"].lower())] = r.get("functions", "")
    texts = walk(args.system)
    rows = join(texts, arms, fn_arms, branch_census, jif, records)
    frows = None
    if args.functions:
        classes = [r for r in rows if r.get("kind") == "class"]
        class_verdicts = {(r["package"], (r["class"] or "").lower()): r.get("verdict", "") for r in classes}
        class_terms = {(r["package"], (r["class"] or "").lower()): r.get("terms", 0) for r in classes}
        frows = function_rows(texts, fn_cat, class_verdicts, class_terms, jif)
    if args.write_doc:
        write_doc(args.write_doc, rows, frows)
    if args.report or not args.write_doc:
        print(doc_block(rows).strip())
        if frows is not None:
            print(func_doc_block(frows).strip())
    if not args.report:
        with open(args.out, "w", encoding="utf-8", newline="") as fh:
            fh.write(render(rows))
        print("wrote %s (%d rows)" % (args.out, len(rows)))
        if frows is not None:
            with open(args.functions_out, "w", encoding="utf-8", newline="") as fh:
                fh.write(func_render(frows))
            print("wrote %s (%d rows)" % (args.functions_out, len(frows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
