#!/usr/bin/env python3
"""What the shipped source spells about its switches, against what the bytecode compiled (PLAN 3.7, U21).

`inventory/switch_arms.csv` measures a switch from the bytes: one row per arm, its case value (`label_kind`/
`label`) and how the arm leaves. Every class also ships its `ScriptText`, so the same construct has a second,
independent reading -- one the arm census could not use until the case token's own value expression was decided,
because an arm with no name cannot be matched against a `case` label.

This reads the source and joins it to the arms:

* **the census** counts `switch`, `case`, `default` and `break` in the masked text, per class and per function
  (comments and literals blanked with a length-preserving mask -- `tools/text-branches.py`'s, so both pages read
  one buffer);
* **the labels** are matched one arm at a time: an arm's `(kind, label)` canonicalises to an integer or a
  case-folded string, the text's own `case` expressions canonicalise the same way, and the match is consumed in
  arm order, so a repeated label takes the next occurrence;
* **the exits** are compared: the text's `case` body runs to the next `case`/`default` **of its own `switch`** (a
  nested block's labels belong to that block, not to the arm that opens it) and no further than the switch's own
  closing brace, and its *last statement* is classified (`break`, `return`, `continue`, `empty`, or anything else,
  which falls through). That is the bytecode's `end` column's own vocabulary, read from the source instead of the
  token stream. The separators that find that last statement are searched in the buffer whose **literals are
  blanked**, because a `;` or a `:` inside a string is not a statement boundary.

Two readings are controls. `default.Health` and `defaultproperties` are not `default:` labels, so the pattern
requires a word boundary and excludes a following `.`; and a function whose text has no `case` at all is counted
as `text-only`/`bytes-only` rather than as a disagreement of zero.

    python3 tools/text-switches.py --system DUMP/System -o inventory/text_switches.csv --write-doc docs/BYTECODE.md
    python3 tools/text-switches.py --check-doc docs/BYTECODE.md   # from the committed artifact, no dump
    python3 tools/text-switches.py --check                        # synthetic, no dump, writes nothing

Reads the 18 packages plus `inventory/switch_arms.csv`; no bytecode walk of its own, no boot, no Ghidra. Counts
only: nothing here prints or commits source text.
"""
import argparse
import collections
import csv
import importlib.util
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402

CSV_PATH = os.path.join(ROOT, "inventory", "text_switches.csv")
ARMS_CSV = os.path.join(ROOT, "inventory", "switch_arms.csv")
DOC_MARK = ("<!-- generated:text-switches -->", "<!-- /generated:text-switches -->")

FIELDS = ["kind", "package", "class", "function", "arm", "label_kind", "label", "text_switch", "text_case",
          "text_default", "text_break", "byte_lists", "byte_arms", "byte_defaults", "byte_break", "labels_found",
          "labels_missing", "text_end", "text_continue", "text_break_inner", "byte_end", "end_agree", "end_checked",
          "verdict", "count", "note"]

CENSUS_NOTE = {
    "text_switch": "`switch` keywords in the live text",
    "text_case": "`case` labels in the live text",
    "text_default": "`default:` labels in the live text (`default.`/`defaultproperties` excluded)",
    "text_break": "`break;` statements in the live text",
    "byte_lists": "case lists in `inventory/switch_arms.csv`",
    "byte_arms": "testable arms there",
    "byte_defaults": "terminator cases there, each a `default:` label in the source",
    "byte_break": "arms that leave through a `break` jump",
    "classes_joined": "classes with a readable `ScriptText` and at least one arm",
    "label_agree": "classes whose `case` labels are exactly the `Case` tokens the bytecode carries",
    "label_more": "classes whose live text spells more `case` labels",
    "label_less": "classes whose live text spells fewer",
    "text_only": "classes whose live text has a `case` and whose bytecode has no arm",
    "bytes_only": "classes with arms whose text spells no `case`",
    "labels_found": "arms whose case value was found as a `case` label in their function's text",
    "labels_missing": "arms whose case value names no label in that text (an expression, or text not that revision)",
    "labels_by_position": "of those, the ones matched by position because the source spells a symbolic constant "
                          "(`case MB_OK:`) the compiler folds to the arm's integer",
    "end_agree": "arms whose text body leaves the way the token stream does",
    "end_checked": "arms whose case value matched a text label, so their exit could be compared",
    "empty_agree": "of the shared `case A: case B:` bodies, the ones the source spells with no body too",
    "fallthrough_text_other": "of the arms that stop with no jump, the ones whose text case also ends in no "
                              "`break`/`return`",
    "loop_text_other": "of the arms whose last `Jump` is a loop's own back edge, the ones whose source case also ends "
                       "on a control construct rather than leaving",
    "functions_joined": "functions carrying a switch on both sides",
    "arm_continue": "matched arms whose own source `case` body carries a `continue` statement",
    "switch_continue": "`continue` statements inside a `switch` block -- case arms and `default:` bodies together",
    "arm_continue_all": "of those, the ones inside a case arm's body (every label group, matched or not)",
    "default_continue": "of those, the ones inside a `default:` body",
    "arm_break": "matched arms whose own source `case` body carries a `break` statement",
    "arm_break_inner": "of those, matched arms carrying a `break` an arm statement follows -- the interior break",
    "switch_break": "`break` statements inside a `switch` block -- case arms and `default:` bodies together",
    "arm_break_all": "of those, the ones inside a case arm's body (every label group, matched or not)",
    "arm_break_interior": "of those, the ones an arm statement follows, i.e. not the arm's last statement",
    "default_break": "of those, the ones inside a `default:` body",
    "enums_declared": "`enum` bodies the class texts declare",
    "enum_members": "members those enums declare (a member's value is its ordinal)",
    "labels_enum": "`case` labels in the live text spelled as an enum member, qualified or bare",
    "labels_hex": "`case` labels in the live text spelled as a hex integer (`case 0x26:`)",
    "labels_ambiguous": "`case` labels a bare enum member two enums place at two different ordinals, left unfolded",
    "labels_dead": "`case` labels inside a build-guard part this build never compiles",
    "labels_commented": "`case` labels inside a block comment (a commented-out copy of a function)",
    "classes_commented": "class texts carrying a `case` label inside a block comment",
    "bodies_state": "function bodies the text declares inside a `state` block",
    "arms_state": "arms those state bodies carry",
}

CENSUS_ORDER = ("text_switch", "text_case", "text_default", "text_break", "byte_lists", "byte_arms",
                "byte_defaults", "byte_break", "classes_joined", "label_agree", "label_more", "label_less",
                "text_only", "bytes_only", "labels_found", "labels_missing", "labels_by_position", "end_agree",
                "end_checked", "empty_agree", "fallthrough_text_other", "loop_text_other", "functions_joined",
                "arm_continue", "switch_continue", "arm_continue_all", "default_continue",
                "arm_break", "arm_break_inner", "switch_break", "arm_break_all", "arm_break_interior",
                "default_break", "enums_declared", "enum_members", "labels_enum", "labels_hex",
                "labels_ambiguous", "labels_dead", "labels_commented", "classes_commented", "bodies_state",
                "arms_state")

# A `case` label's expression: everything up to the `:` that is not a `::`, on one line, no statement separator.
# UnrealScript folds keyword case -- `Switch(Role)` and `State` are the build's own spelling -- so every pattern here
# is case-insensitive (`tools/text-branches.py` had already found 55 capitalised `State` declarations).
CASE = re.compile(r"(?<![#\w$.])case\b[ \t]+([^:;{}\n]+?)[ \t]*:(?!:)", re.I)
SWITCH = re.compile(r"(?<![#\w$.])switch\b", re.I)
# `default:` is a label; `default.Health` and `defaultproperties` are not.
DEFAULT = re.compile(r"(?<![#\w$.])default\b(?!\s*[.\w])", re.I)
BREAK = re.compile(r"(?<![#\w$.])break\b", re.I)
RETURN = re.compile(r"(?<![#\w$.])return\b", re.I)
CONTINUE = re.compile(r"(?<![#\w$.])continue\b", re.I)
GOTO = re.compile(r"(?<![#\w$.])goto\b", re.I)
INT = re.compile(r"[-+]?(?:0[xX][0-9a-fA-F]+|\d+)")
HEX = re.compile(r"[-+]?0[xX][0-9a-fA-F]+")
# A case label need not be a literal. `WindowConsole.KeyEvent` spells `case EInputKey.IK_Escape:` and
# `case IST_Press:` -- names the *compiler* folds to the arm's integer -- and `UWindowPulldownMenu.KeyDown` spells
# `case 0x26:` for the same kind of value. The enum is declared in the corpus's own text
# (`Engine.Interactions` opens `enum EInputKey { IK_None, // 0 ... }`), so the fold is a reading rather than a
# guess: a member's value is its declaration ordinal. Resolving it turns a symbolic label from a positional
# pairing (the fallback below, which needs the label count to equal the arm count) into a value match.
ENUM_DECL = re.compile(r"(?<![#\w$.])enum[ \t]+(\w+)\s*\{([^{}]*)\}", re.I)
ENUMS = {}                      # member (lower) -> {enum name (lower): ordinal}
ENUM_STATS = collections.Counter()
CLASS_ENUMS = {}                # (package, class lower) -> {enum lower: [member lower, ...]}
# A `state` block holds `function` bodies of its own, and a class may declare one under the name its class body
# already uses: the bytecode carries `WindowConsole.KeyEvent` and `WindowConsole.UWindow.KeyEvent` as two
# functions, so the text side has to know which body a span belongs to before the two can be joined.
STATE_DECL = re.compile(r"(?m)^[ \t]*(?:auto[ \t]+)?state[ \t]+(\w+)[ \t\r\n]*\{")


def load_enums(texts):
    """Build the member table from every `enum NAME { A, B }` the corpus's class texts declare.

    `clive` is the comment-masked buffer, so an enum body arrives as plain identifiers. Two tables are built: the
    global one (`ENUMS`, member -> enum -> ordinal) and a per-class one (`CLASS_ENUMS`), because the same enum is
    declared again by more classes than the one that owns it and **the declarations disagree**. `TriggeredScript`
    declares `ETriggerType` with `TT_PlayerProximity` first; a class that re-declares it without the commented-out
    `TT_PawnProximity` places `TT_HumanPlayerProximity` at 5 instead of 4, and a global first-declaration table read
    that class's labels one arm off. A class's own declaration is the authority for its own `case` labels -- the
    enum is in scope there by construction -- so the global table is only the fallback for a class that declares
    no enum carrying the member.
    """
    ENUMS.clear()
    ENUM_STATS.clear()
    CLASS_ENUMS.clear()
    for key, t in texts.items():
        buf = t.get("clive") or ""
        own = {}
        for m in ENUM_DECL.finditer(buf):
            name = m.group(1).lower()
            members = [w.strip() for w in re.split(r"[,{}]+", m.group(2))]
            members = [w for w in members if re.fullmatch(r"[A-Za-z_]\w*", w)]
            ENUM_STATS["enums"] += 1
            own.setdefault(name, [w.lower() for w in members])
            for i, mem in enumerate(members):
                ENUMS.setdefault(mem.lower(), {}).setdefault(name, i)
                ENUM_STATS["members"] += 1
        CLASS_ENUMS[key] = own
    return ENUMS


def enum_value(expr, scope=None):
    """The integer a symbolic `case` label folds to, or `None` when no enum of this build declares it.

    `EInputKey.IK_Escape` is read through the enum the qualifier names; a bare `IST_Press` resolves to the enum
    that declares it. `scope` is the declaring class's own enum table (`CLASS_ENUMS`): a member the class itself
    declares folds through *that* declaration first, and only a member it does not declare falls back to the
    global table, where it must be unanimous. A bare name two enums of the same class place at two ordinals is
    still unresolved -- which one the source meant is a reading the label alone cannot make.
    """
    e = expr.strip()
    if not e:
        return None
    if "." in e:
        head, _dot, tail = e.rpartition(".")
        head, tail = head.strip().lower(), tail.strip().lower()
        if scope and head in scope:
            mem = scope[head]
            return mem.index(tail) if tail in mem else None
        return ENUMS.get(tail, {}).get(head)
    if scope:
        mine = [mem.index(e.lower()) for _name, mem in scope.items() if e.lower() in mem]
        if len(set(mine)) == 1:
            return mine[0]
        if mine:
            return None
    hits = ENUMS.get(e.lower())
    if hits and len(set(hits.values())) == 1:
        return next(iter(hits.values()))
    return None


def load_tool(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TB = COTB = None


def text_tool():
    """`tools/text-branches.py` for its `mask`: one masking rule for every text census in the tree."""
    global TB
    if TB is None:
        TB = load_tool("text_branches", "text-branches.py")
    return TB


def ctb_tool():
    """`tools/class-text-bodies.py` for `FUNC` and `body_span`: one declaration rule, one brace walk."""
    global COTB
    if COTB is None:
        COTB = load_tool("class_text_bodies", "class-text-bodies.py")
    return COTB


# --- the text side ----------------------------------------------------------------------------------
def canon_text(expr, scope=None):
    """`(kind, value)` for a `case` label's expression as the source spells it.

    `scope` is the declaring class's own enum table: a member it declares folds through that declaration before
    the corpus-wide table is consulted (`enum_value`).
    """
    e = expr.strip()
    if not e:
        return ("expr", "")
    if len(e) >= 2 and e[0] in "'\"" and e[-1] == e[0]:
        return ("str", e[1:-1].replace(e[0] * 2, e[0]).lower())
    if INT.fullmatch(e):
        return ("int", str(int(e, 16) if HEX.fullmatch(e) else int(e)))
    v = enum_value(e, scope)
    if v is not None:
        return ("int", str(v))
    return ("expr", e.lower())


def canon_arm(kind, label):
    """`(kind, value)` for an arm's case value, in the vocabulary `canon_text` answers."""
    if kind in ("byte", "int"):
        try:
            return ("int", str(int(label)))
        except (TypeError, ValueError):
            return ("expr", "")
    if kind in ("string", "name"):
        return ("str", (label or "").lower())
    return ("expr", "")


def counts(text, dead=None):
    """`Counter` of the block keywords, with the parts this build never compiles left out when `dead` is given.

    `dead` is `tools/text-branches.py`'s `guard_map` for the same buffer: this corpus ships the Xbox text, so the
    `#ifdefPS2` branch of `SGMenuInteraction.KeyEvent` carries a `case IK_Tab:` the compiler never saw. A label
    inside a dead part is not a label the package built, and counting it made that class read one `case` "more"
    than its bytecode.
    """
    c = collections.Counter()
    for name, rx in (("switch", SWITCH), ("case", CASE), ("default", DEFAULT), ("break", BREAK),
                     ("return", RETURN)):
        c[name] = sum(1 for m in rx.finditer(text) if dead is None or not dead[m.start()])
    return c


def last_statement(seg, clean=None):
    """What a case body's own last statement is: `break`, `return`, `continue`, `goto`, `other` or `empty`.

    The reading is deliberately *the last statement of the body*, not the last thing executed inside it: a body
    that ends with a block (`if (...) { ... break; }`, `for (...) { ... }`) ends with a **control construct**, and
    the token stream ends it with the `JumpIfNot` that construct compiles to, so both sides call it the same
    thing -- the arm does not leave unconditionally, and the source falls through. That is the distinction the
    `end` column makes between a `Jump` to the list's end and no jump at all.

    Two corrections are measured into this reading. The separators are searched in `clean`, the same span with
    literals blanked, because a `;` or a `:` **inside a string** is not a statement boundary: `return ".m3u;.pls;.b4s";`
    read its last statement as `.b4s"` and `return "Quicksave: Current";` as `Current"` until this buffer was passed.
    A trailing `break;` after a `return` is left as it is: `GameMessage.GetString` spells every one of its 17 arms
    `return ...; break;`, and the compiler carries that dead `break` as a real `Jump` after the `Return`, so the
    stream ends those arms on the break too -- "last statement" is the same statement on both sides.
    """
    s = seg.rstrip()
    if not s:
        return "empty"
    # A body that holds nothing but preprocessor directives is empty: `case IK_MouseWheelUp:` is followed by its
    # shared body's `#ifndefPS2`/`#endif` and then the next label, and the directive lines are not statements.
    if all(not ln.strip() or ln.lstrip().startswith("#") for ln in s.splitlines()):
        return "empty"
    if s.endswith(";"):
        n = len(s)
        src = clean[:n] if clean is not None else s

        def cut_of(buf):
            return max(buf.rfind(";", 0, len(buf) - 1), buf.rfind("{", 0, len(buf) - 1),
                       buf.rfind("}", 0, len(buf) - 1), buf.rfind(":", 0, len(buf) - 1))

        cut = cut_of(src)
        stmt = s[cut + 1:].strip()
        low = stmt.lower()
        if re.match(r"break\b", low):
            return "break"
        if re.match(r"return\b", low):
            return "return"
        if re.match(r"continue\b", low):
            return "continue"
        if re.match(r"goto\b", low):
            return "goto"
        return "other"
    return "other"


def comment_spans(text):
    """`[("line" | "block", start, end)]` for every comment in one class text, in source order.

    A block comment **counts its `/*` depth**, because this text nests them: `StargateContent.TeamVoicePack`
    comments out a whole copy of `PlayerSpeech` (`/*function PlayerSpeech(...`), and inside that dead copy the
    `ORDER` arm carries a second disabled block. The first-`*/` rule ends the outer comment at the inner one and
    exposes two `case` labels (`'TAUNT'`, `'OTHER'`) at file scope -- a shape no compiler turns into code, and one
    the package contradicts: it carries 6 arms for that switch, not 8. A literal is skipped, so a `/*` inside a
    string is not a comment.
    """
    spans = []
    n = len(text)
    i = 0
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            spans.append(("line", i, j))
            i = j
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            depth, j = 1, i + 2
            while j < n and depth:
                if text[j] == "/" and j + 1 < n and text[j + 1] == "*":
                    depth += 1
                    j += 2
                    continue
                if text[j] == "*" and j + 1 < n and text[j + 1] == "/":
                    depth -= 1
                    j += 2
                    continue
                j += 1
            spans.append(("block", i, min(j, n)))
            i = j
        elif c in "\"'":
            q = c
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == q:
                    if i + 1 < n and text[i + 1] == q:
                        i += 2
                        continue
                    i += 1
                    break
                if text[i] == "\n":
                    break
                i += 1
        else:
            i += 1
    return spans


def mask_comments(text):
    """The buffer with **comments only** blanked: literals are kept, because a `case` label may *be* one.

    `tools/text-branches.py`'s `mask` blanks comments and literals both, which is right for counting keywords --
    and exactly wrong for reading a label: `case '0':` arrives as `case    :`. Both buffers are length-preserving
    and blank only comments here, so a position means the same thing in each. The spans are `comment_spans`', so
    this mask and the census that counts commented labels read one rule.
    """
    out = list(text)
    for _kind, a, b in comment_spans(text):
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "
    return "".join(out)


def closing_brace(clive, start, limit):
    """Where the block that follows `start` closes: the first `}` no `{` in the segment balances.

    A case body is bounded by two things, not one: the next `case`/`default` of its list **and** the switch's own
    closing brace. Without the second, the list's last arm reads on across the `}` into whatever follows the switch
    -- in `Actor.DisplayDebug` that is the next `switch`, so its last arm's body was the following switch's test
    code and the arm read as one that does not leave.
    """
    depth = 0
    for i in range(start, limit):
        c = clive[i]
        if c == "{":
            depth += 1
        elif c == "}":
            if depth == 0:
                return i
            depth -= 1
    return limit


def switch_spans(live, start, end):
    """`[(open_brace, close_brace)]` for every `switch (...) {...}` block in one function body, in source order.

    Found on `live`, the buffer whose literals are blanked: a `{` inside a string is not a block. A nested switch's
    span sits inside its parent's, so the two are distinguishable by position alone.
    """
    out = []
    for m in SWITCH.finditer(live, start, end):
        ob = live.find("{", m.end(), end)
        if ob < 0:
            continue
        out.append((ob, closing_brace(live, ob + 1, end)))
    return out


def label_groups(clive, live, span, dead=None, enums=None):
    """`[(open, close, [(label, start, end), ...])]` -- one group per `switch` block, a nested block's labels out.

    The list a `case` belongs to is its **innermost enclosing `switch`**, not every `case` token in the function:
    in `UWindowConsoleClientWindow.Notify` each outer arm opens a `switch (C)` of its own, so a flat label list ends
    the outer arm's body at the *inner* case and reports an arm that leaves on a `break` as falling through (five
    of the fifteen arms the two readings disagreed on).

    `dead` drops the labels of a build-guard part this build never compiled and `enums` folds a symbolic label
    through the declaring class's own enum table; both are the join's readings, not the block's shape.
    """
    spans = switch_spans(live, span[0], span[1])
    groups = []
    for ob, cb in spans:
        marks = []
        for mark in labels_in(clive, ob, cb, dead, enums):
            if any(o > ob and c <= cb and o <= mark[1] <= c for o, c in spans):
                continue                      # a nested switch's own label, not this list's
            marks.append(mark)
        if marks:
            groups.append((ob, cb, marks))
    groups.sort(key=lambda g: g[0])
    return groups


def labels_in(live, start, end, dead=None, enums=None):
    """`[( (kind, value) or None for a `default:`, match_start, match_end )]` for one function body's labels.

    `default:` is a boundary as well as a label: an arm's body ends where the next `case` **or** `default` of the
    list begins, so a case that ends the testable list must not read on into the `default:` body.
    """
    marks = [(canon_text(m.group(1), enums), m.start(), m.end()) for m in CASE.finditer(live, start, end)
             if dead is None or not dead[m.start()]]
    marks += [(None, m.start(), m.end()) for m in DEFAULT.finditer(live, start, end)
              if dead is None or not dead[m.start()]]
    marks.sort(key=lambda t: t[1])
    return marks


def state_spans(live):
    """`[(name, (open, close))]` for every `state NAME { ... }` block in one class text.

    A state body is where a class's second `KeyEvent` lives, and the bytecode names it in the function's path
    (`WindowConsole.UWindow.KeyEvent`), so a span has to carry the state it sits in for the two sides to meet.
    """
    ctb = ctb_tool()
    out = []
    for m in STATE_DECL.finditer(live):
        span = ctb.body_span(live, m.start(), len(live))
        if span is not None:
            out.append((m.group(1).lower(), span))
    return out


def function_spans(live):
    """`[(name, span, state)]` for one class text: the declaration rule and brace walk `tools/class-text-bodies.py`
    owns, plus the `state` block the declaration sits in (`""` for the class body)."""
    ctb = ctb_tool()
    states = state_spans(live)
    ms = list(ctb.FUNC.finditer(live))
    out = []
    for k, m in enumerate(ms):
        limit = ms[k + 1].start() if k + 1 < len(ms) else len(live)
        span = ctb.body_span(live, m.end(), limit)
        if span is None:
            continue
        enclosing = [nm for nm, (a, b) in states if a < m.start() < b]
        out.append((ctb.declared_name(live, m).lower(), span, enclosing[-1] if enclosing else ""))
    return out


def walk(system_dir):
    """`(package, class) -> {class, functions: {name: [(span, state), ...]}, live, dead, counts}` per class text."""
    pst = load_tool("pkg_script_text", "pkg-script-text.py")
    tb = text_tool()
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
            live, _comments = tb.mask(text)
            dead, _parts = tb.guard_map(live)
            clive = mask_comments(text)
            funcs = collections.defaultdict(list)
            for name, span, state in function_spans(live):
                funcs[name].append((span, state))
            name = pkg.name(exp["name"])
            # The class counts read the same two masks the join's labels do: comments by `mask_comments` (which
            # counts `/*` nesting) and literals by `text-branches.mask`, so a `case` the class counts and a `case`
            # the join reads are the same label. Counting on `mask`'s own comment rule left two labels of a
            # commented-out function live and one class reading "more" than its bytecode.
            rows[(package, name.lower())] = {"class": name, "live": live, "clive": clive, "raw": text,
                                             "dead": dead, "functions": funcs,
                                             "counts": counts(tb.mask(clive)[0], dead)}
    return rows


# --- the bytecode side ------------------------------------------------------------------------------
def arms_by_class(path=ARMS_CSV):
    """`(package, class) -> arm rows`, `(package, class, function, state) -> rows` and the per-class counters, from
    the committed census: the arms, the lists and the `break`s each class carries.

    A function's path is `Class.Function` in the class body and `Class.State.Function` in a `state` block, so the
    key carries the state: a class that declares `KeyEvent` twice (its own body and `state UWindow`'s) has two
    functions the bytecode keeps apart and a name-only key merged into one.
    """
    classes = collections.defaultdict(list)
    funcs = collections.defaultdict(list)
    meta = collections.defaultdict(collections.Counter)
    for r in csv.DictReader(open(path, encoding="utf-8")):
        kind = r.get("kind")
        if kind not in ("arm", "list", "default"):
            continue
        parts = (r.get("path") or "").split(".")
        if len(parts) < 2:
            continue
        cls, fn = parts[0], parts[-1]
        state = ".".join(parts[1:-1]).lower()
        key = (r["package"], cls.lower())
        if kind == "arm":
            classes[key].append(r)
            funcs[(r["package"], cls.lower(), fn.lower(), state)].append(r)
            meta[key]["break"] += int(r.get("end") == "break")
        elif kind == "list":
            meta[key]["lists"] += 1
        else:
            meta[key]["defaults"] += 1
    return classes, funcs, meta


# --- the join ----------------------------------------------------------------------------------------
def compare_function(arms, spans, clive, live=None, dead=None, enums=None):
    """Match every arm to a `case` label of **its own switch** and compare how each leaves.

    A function's arms carry their `site` (which `Switch` token they belong to) and its text carries one label group
    per `switch` block, so the join is block by block, not function by function: matching all of a function's arms
    against all of its labels paired an outer arm with the first case of the `switch` in its own body.

    Two matches are tried inside a block, and they are counted apart because they are different evidence: **by
    value**, where the arm's own literal canonicalises to the label the source spells (`case 'Music':`, `case 3:`,
    `case "mp3":`), and **by position**, where the label count equals the arm count but the source spells a symbolic
    constant (`case MB_OK:`) that only the compiler folds to the integer. A positional match is reported as such,
    never as a value match.
    """
    out = {"found": 0, "positional": 0, "checked": 0, "agree": 0, "detail": [], "blocks": 0, "arm_continue": 0,
           "arm_break": 0, "arm_break_inner": 0}
    best = None
    for span, _state in spans:
        groups = label_groups(clive, live if live is not None else clive, span, dead, enums)
        hit = 0
        for r in arms:
            c = canon_arm(r.get("label_kind"), r.get("label"))
            if c[0] != "expr" and any(lab == c for _o, _c, marks in groups for lab, _s, _e in marks):
                hit += 1
        if best is None or hit > best[0]:
            best = (hit, groups, span)
    if best is None:
        return out
    _hit, groups, span = best
    break_sites = text_tool().break_sites_detail(live if live is not None else clive, None, span[0], span[1])
    out["blocks"] = len(groups)
    pool = list(groups)
    sites = []
    for r in arms:
        if r.get("site", "1") not in sites:
            sites.append(r.get("site", "1"))
    for site in sites:
        mine = [r for r in arms if r.get("site", "1") == site]
        pick, best_hits = None, 0
        for g in pool:
            hits = sum(1 for r in mine
                       if canon_arm(r.get("label_kind"), r.get("label"))[0] != "expr"
                       and any(lab == canon_arm(r.get("label_kind"), r.get("label")) for lab, _s, _e in g[2]))
            if hits > best_hits:
                pick, best_hits = g, hits
        if pick is None and pool:
            pick = pool[0]
        if pick is not None:
            pool.remove(pick)
        labels = pick[2] if pick else []
        close = pick[1] if pick else span[1]
        cases = [i for i, (c, _s, _e) in enumerate(labels) if c is not None]
        symbolic = cases and all(labels[i][0][0] == "expr" for i in cases)
        hit = 0
        have = collections.Counter(c for c, _a, _b in labels if c is not None)
        for r in mine:
            c = canon_arm(r.get("label_kind"), r.get("label"))
            if c[0] != "expr" and have.get(c, 0):
                hit += 1
        # Positional matching is only honest when the source's labels are *symbolic*: `case MB_OK:` is a name the
        # compiler folds to the arm's integer, so the values cannot be compared. A literal label that failed to match
        # is a disagreement about the value, and it is left unmatched rather than paired up by position.
        positional = hit == 0 and len(cases) == len(mine) and bool(symbolic)
        if positional:
            out["positional"] += len(cases)
        used = [False] * len(labels)
        pairs = []
        for arm_index, r in enumerate(mine):
            c = canon_arm(r.get("label_kind"), r.get("label"))
            pick_i = None
            if positional:
                pick_i = cases[arm_index]
            elif c[0] != "expr":
                for i, (lab, _s, _e) in enumerate(labels):
                    if not used[i] and lab == c:
                        pick_i = i
                        break
            if pick_i is None:
                pairs.append((r, None))
                continue
            used[pick_i] = True
            pairs.append((r, pick_i))
        # The *leftovers* of a list pair the same way, one at a time: an expression-valued arm (`case ConsoleHotKey:`
        # compiles to a `Case` whose operand the VM evaluates, so the arm census labels it `expr`/`InstanceVariable`)
        # beside the one `case` label the list has left, which the source spells as a name. Pairing them is the same
        # reading the whole-list rule makes -- a symbolic label can only be that arm's value -- and it is counted in
        # the same `labels_by_position` column, never as a value match.
        left_arms = [k for k, (_r, i) in enumerate(pairs) if i is None]
        left_marks = [i for i in cases if not used[i]]
        if left_arms and len(left_arms) == len(left_marks) and all(labels[i][0][0] == "expr" for i in left_marks):
            for k, i in zip(left_arms, left_marks):
                used[i] = True
                pairs[k] = (pairs[k][0], i)
                out["positional"] += 1
        for r, pick_i in pairs:
            if pick_i is None:
                out["detail"].append((r, None, "", [], []))
                continue
            out["found"] += 1
            seg_end = labels[pick_i + 1][1] if pick_i + 1 < len(labels) else close
            seg_end = min(seg_end, close)
            # The `continue` statements an arm's own source body carries: the column that joins this census to
            # `tools/text-branches.py`'s continue split, one statement over from `switch-join.csv`'s arm counts.
            cs = text_tool().continue_sites(live if live is not None else clive, None,
                                            labels[pick_i][2], seg_end)
            if cs:
                out["arm_continue"] += 1
            # The `break` statements this arm's own source body carries, and which of them are interior: a `break`
            # that is not the body's last statement is the shape the arm census's last-token column cannot see.
            # The binding is read over the whole function body, because this arm's `switch` header sits in front of
            # the window the arm covers -- a read narrowed to the arm finds only the loops inside it.
            bs = [p for o, binds, _c, p, _d in break_sites
                  if binds == "switch" and labels[pick_i][2] <= o < seg_end]
            if bs:
                out["arm_break"] += 1
                out["arm_break_inner"] += int("interior" in bs)
            text_end = last_statement(clive[labels[pick_i][2]:seg_end],
                                      (live if live is not None else clive)[labels[pick_i][2]:seg_end])
            byte_end = r.get("end") or ""
            out["detail"].append((r, text_end, byte_end, [k for _o, k in cs], bs))
            if byte_end == "empty" or byte_end == "loop":
                out["checked"] += 1
                out["agree"] += int(text_end == ("empty" if byte_end == "empty" else "other"))
                continue
            if byte_end in ("fallthrough", "into-default"):
                out["checked"] += 1
                out["agree"] += int(text_end in ("other", "empty", ""))
                continue
            want = {"break": "break", "return": "return", "elsewhere": "continue"}.get(byte_end)
            if want is None:
                continue
            out["checked"] += 1
            out["agree"] += int(text_end == want or (want == "continue" and text_end == "goto"))
    return out


def switch_continue_census(texts):
    """`Counter` of the `continue` statements inside every `switch` block, split by case arm and `default:` body.

    The block census is the population, not the matched arms: an arm whose value the text could not be read for is
    still inside its block, and a `continue` in a `default:` body opens no arm either. A site inside a nested
    `switch` sits inside two spans, so each offset is counted once, in the innermost block that holds it.
    """
    census = collections.Counter()
    seen = {}
    for key, t in texts.items():
        for _fn, spans in t["functions"].items():
            for span, _state in spans:
                groups = label_groups(t["clive"], t["live"], span, t.get("dead"), CLASS_ENUMS.get(key))
                for ob, cb, marks in groups:
                    for off, _kind in text_tool().continue_sites(t["live"], None, ob, cb):
                        holder = [(o, c) for o, c, _m in groups if o <= off < c]
                        if not holder:
                            continue
                        ident = (key, off)
                        if ident in seen:
                            continue
                        bucket = "default"
                        for i, (lab, start, _end) in enumerate(marks):
                            if lab is not None and start <= off:
                                nxt = marks[i + 1][1] if i + 1 < len(marks) else cb
                                if off < min(nxt, cb):
                                    bucket = "arm"
                        seen[ident] = bucket
                        census["switch_continue"] += 1
                        census["arm_continue_all" if bucket == "arm" else "default_continue"] += 1
    return census


def switch_break_census(texts):
    """`Counter` of the `break` statements inside every `switch` block, split by case arm and `default:` body.

    The block census is the population, not the matched arms: an arm whose value the text could not be read for is
    still inside its block, and a `break` in a `default:` body opens no arm either. A site inside a nested `switch`
    sits inside two spans, so the blocks are walked innermost first and each offset is counted once, in the block
    that owns it -- the tail/interior reading is relative to that same arm, which is what the arms census's
    `interior_break` column reads on the compiled side.
    """
    census = collections.Counter()
    seen = set()
    for key, t in texts.items():
        for _fn, spans in t["functions"].items():
            for span, _state in spans:
                blocks = switch_spans(t["live"], span[0], span[1])
                if not blocks:
                    continue
                groups = {g[0]: g for g in label_groups(t["clive"], t["live"], span, t.get("dead"),
                                                        CLASS_ENUMS.get(key))}
                for off, binds, _con, place, _dead in text_tool().break_sites_detail(t["live"], None,
                                                                                    span[0], span[1]):
                    if binds != "switch":
                        continue
                    ident = (key, off)
                    if ident in seen:
                        continue
                    holders = [b for b in blocks if b[0] <= off < b[1]]
                    if not holders:
                        continue
                    ob, cb = min(holders, key=lambda b: b[1] - b[0])     # the innermost `switch` wins
                    marks = groups[ob][2] if ob in groups else []
                    bucket = "default"
                    for i, (lab, start, _end) in enumerate(marks):
                        if lab is not None and start <= off:
                            nxt = marks[i + 1][1] if i + 1 < len(marks) else cb
                            if off < min(nxt, cb):
                                bucket = "arm"
                    seen.add(ident)
                    census["switch_break"] += 1
                    if bucket == "arm":
                        census["arm_break_all"] += 1
                        if place == "interior":
                            census["arm_break_interior"] += 1
                    else:
                        census["default_break"] += 1
    return census


def label_spelling_census(texts):
    """How the live text spells its `case` labels: an enum member, or a hex integer.

    The population the fold is worth, counted once over the text rather than accumulated from the matcher (which
    asks `canon_text` the same label several times): a symbolic label is one the source wrote as a name and the
    compiler folded, and a hex label is one the integer rule itself had to learn. A label inside a dead build-guard
    part is not counted -- the build never compiled it -- and a bare member is folded through the declaring class's
    own enum table before the corpus-wide one.
    """
    census = collections.Counter()
    for key, t in texts.items():
        dead, scope = t.get("dead"), CLASS_ENUMS.get(key)
        for m in CASE.finditer(t.get("live") or ""):
            if dead is not None and dead[m.start()]:
                census["labels_dead"] += 1
                continue
            e = m.group(1).strip()
            if HEX.fullmatch(e):
                census["labels_hex"] += 1
            if enum_value(e, scope) is not None:
                census["labels_enum"] += 1
            elif "." not in e and len(set((ENUMS.get(e.lower()) or {}).values())) > 1 and \
                    not any(e.lower() in mem for mem in (scope or {}).values()):
                # A bare name two enums place at two ordinals is not folded: which enum the source meant is a
                # reading the label alone cannot make. A class that declares the member itself is that reading.
                census["labels_ambiguous"] += 1
        # A label the masker blanked is a label inside a comment: the raw text's count is the population, and
        # `clive`'s is what a label read may see. A label in a *block* comment is a commented-out copy of code
        # (`StargateContent.TeamVoicePack` carries a whole disabled `PlayerSpeech` with its `TAUNT`/`OTHER` cases);
        # a `// comment` that happens to spell `case` is prose and is not counted.
        raw = t.get("raw")
        if raw is not None:
            blocks = [(a, b) for kind, a, b in comment_spans(raw) if kind == "block"]
            n = sum(1 for m in CASE.finditer(raw) if any(a <= m.start() < b for a, b in blocks))
            census["labels_commented"] += n
            census["classes_commented"] += 1 if n else 0
    return census


def join(texts, arms_c, arms_f, meta):
    census = collections.Counter()
    rows = []
    for key in sorted(set(texts) | set(arms_c)):
        t = texts.get(key)
        arms = arms_c.get(key, [])
        cls_name = (t or {}).get("class", key[1])
        text_counts = (t or {}).get("counts", collections.Counter())
        mine = meta.get(key, collections.Counter())
        byte_labels = len(arms)          # one `Case` token per arm, the terminator's excluded here
        row = {"kind": "class", "package": key[0], "class": cls_name,
               "text_switch": text_counts.get("switch", ""), "text_case": text_counts.get("case", ""),
               "text_default": text_counts.get("default", ""), "text_break": text_counts.get("break", ""),
               "byte_lists": mine.get("lists", 0), "byte_arms": byte_labels,
               "byte_defaults": mine.get("defaults", 0), "byte_break": mine.get("break", 0),
               "verdict": "", "count": "", "note": ""}
        for k in ("text_switch", "text_case", "text_default", "text_break"):
            census[k] += int(row[k] or 0)
        for k in ("byte_lists", "byte_arms", "byte_defaults", "byte_break"):
            census[k] += int(row[k] or 0)
        if t is not None and arms:
            census["classes_joined"] += 1
            verdict = "agree" if row["text_case"] == byte_labels else ("more" if row["text_case"] > byte_labels
                                                                      else "less")
            row["verdict"] = verdict
            census["label_" + verdict] += 1
        elif arms:
            row["verdict"] = "bytes-only"
            census["bytes_only"] += 1
        elif text_counts.get("case"):
            row["verdict"] = "text-only"
            census["text_only"] += 1
        rows.append(row)
        if not t or not arms:
            continue
        for (package, cls_lower, fn_lower, state) in [k for k in arms_f if k[0] == key[0] and k[1] == key[1]]:
            fn_arms = arms_f[(package, cls_lower, fn_lower, state)]
            spans = [(span, st) for span, st in (t["functions"].get(fn_lower) or []) if st == state]
            got = compare_function(fn_arms, spans, t["clive"], t["live"], t.get("dead"), CLASS_ENUMS.get(key))
            found, checked, agree, detail = got["found"], got["checked"], got["agree"], got["detail"]
            match_kind = "value" if got["positional"] == 0 and found else ("position" if got["positional"] else "none")
            census["functions_joined"] += 1
            census["labels_found"] += found
            census["labels_missing"] += len(fn_arms) - found
            census["labels_by_position"] += got["positional"]
            census["end_checked"] += checked
            census["end_agree"] += agree
            census["arm_continue"] += got["arm_continue"]
            census["arm_break"] += got["arm_break"]
            census["arm_break_inner"] += got["arm_break_inner"]
            if state:
                census["bodies_state"] += 1
                census["arms_state"] += len(fn_arms)
            rows.append({"kind": "function", "package": package, "class": cls_name, "function": fn_lower,
                         "byte_arms": len(fn_arms), "labels_found": found,
                         "labels_missing": len(fn_arms) - found, "end_agree": agree, "end_checked": checked,
                         "verdict": "agree" if agree == checked else "differs", "label_kind": match_kind,
                         "count": "", "note": ("state %s" % state) if state else ""})
            for r, text_end, byte_end, conts, brks in detail:
                if text_end is None:
                    continue
                same = ("yes" if ((byte_end == "empty" and text_end == "empty")
                                  or (byte_end in ("fallthrough", "into-default") and text_end in ("other", "empty"))
                                  or (byte_end == "loop" and text_end == "other")
                                  or (byte_end == "break" and text_end == "break")
                                  or (byte_end == "return" and text_end == "return")
                                  or (byte_end == "elsewhere" and text_end in ("continue", "goto"))) else "no")
                if byte_end == "empty" and text_end == "empty":
                    census["empty_agree"] += 1
                if byte_end == "loop" and text_end == "other":
                    census["loop_text_other"] += 1
                if byte_end in ("fallthrough", "into-default") and text_end in ("other", "empty"):
                    census["fallthrough_text_other"] += 1
                rows.append({"kind": "arm", "package": package, "class": cls_name, "function": fn_lower,
                             "arm": r.get("arm"), "label_kind": r.get("label_kind"), "label": r.get("label"),
                             "text_end": text_end, "text_continue": "+".join(sorted(set(conts))),
                             "text_break_inner": "+".join(sorted(set(brks))),
                             "byte_end": byte_end, "end_agree": same, "count": "",
                             "note": "site %s" % (r.get("site") or "")})
    for key, value in switch_continue_census(texts).items():
        census[key] += value
    for key, value in switch_break_census(texts).items():
        census[key] += value
    for key, value in label_spelling_census(texts).items():
        census[key] += value
    census["enums_declared"] += ENUM_STATS["enums"]
    census["enum_members"] += ENUM_STATS["members"]
    for key in CENSUS_ORDER:
        rows.append({"kind": "census", "note": CENSUS_NOTE[key], "count": census[key]})
    return rows, census


# --- rendering ---------------------------------------------------------------------------------------
def render(rows):
    fh = io.StringIO()
    w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in FIELDS})
    return fh.getvalue()


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def census_value(rows, key):
    for r in rows:
        if r.get("kind") == "census" and r.get("note") == CENSUS_NOTE[key]:
            return int(r.get("count") or 0)
    return 0


def comma(n):
    return "{:,}".format(int(n))


def _elsewhere(path=ARMS_CSV):
    """The arms census's `elsewhere` row: an arm that left by a jump landing off its list's end -- a `continue`."""
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("kind") == "census" and "landing somewhere else" in (r.get("note") or ""):
                return int(r.get("count") or 0)
    return 0


def pct(a, b):
    return "%.1f%%" % (100.0 * a / b) if b else "n/a"


def doc_block(rows):
    if not any(r.get("kind") == "census" for r in rows):
        return "\n(no text-switch census rows)\n"
    joined = census_value(rows, "classes_joined")
    lines = ["**The shipped source spells the switches, and on most classes it spells the same number of labels "
             "the token stream does.** **%s** classes carry a readable `ScriptText` and at least one arm: **%s** "
             "spell exactly as many `case` labels as their package has `Case` tokens, **%s** spell more and **%s** "
             "fewer, with **%s** text-only and **%s** bytes-only population(s) beside them."
             % (comma(joined), comma(census_value(rows, "label_agree")), comma(census_value(rows, "label_more")),
                comma(census_value(rows, "label_less")), comma(census_value(rows, "text_only")),
                comma(census_value(rows, "bytes_only")))]
    lines += ["",
              "**Corpus totals:** **%s** `switch` and **%s** `case` in the live text against **%s** arm(s) and "
              "**%s** terminator case(s) in `inventory/switch_arms.csv`, so the source spells a `default:` label "
              "for **%s** of the **%s** lists and a `case` label for **%s** of the **%s** arms."
              % (comma(census_value(rows, "text_switch")), comma(census_value(rows, "text_case")),
                 comma(census_value(rows, "byte_arms")), comma(census_value(rows, "byte_defaults")),
                 comma(census_value(rows, "text_default")), comma(census_value(rows, "byte_defaults")),
                 comma(census_value(rows, "text_case")), comma(census_value(rows, "byte_arms")))]
    lines += ["",
              "**An arm is matched by its own value, and that is what the label column in the arms census bought.** "
              "**%s** of **%s** arms find a `case` label with the same canonical value in their function's text; "
              "**%s** more are paired **by position** -- either because every label in that list is a symbolic "
              "constant (`case MB_OK:`) with no value the corpus declares, or because the list's leftovers are one "
              "expression-valued arm (`case ConsoleHotKey:` compiles to a `Case` whose operand the VM evaluates, so "
              "the arm census reads it as `expr`/`InstanceVariable`) beside the one symbolic label the list has "
              "left. Both are a different reading from a value match and are counted apart; **%s** are matched by "
              "neither -- an expression-valued case with no label beside it, or a text that is not the revision the "
              "package compiled."
              % (comma(census_value(rows, "labels_found") - census_value(rows, "labels_by_position")),
                 comma(census_value(rows, "labels_found") + census_value(rows, "labels_missing")),
                 comma(census_value(rows, "labels_by_position")),
                 comma(census_value(rows, "labels_missing")))]
    lines += ["",
              "**The source's symbolic labels are folded by the corpus's own enum declarations, and the declaring "
              "class's table is the authority.** **%s** `enum` bodies in the class texts declare **%s** members, "
              "**%s** `case` label(s) are spelled as one of those members -- `case EInputKey.IK_Escape:` qualified "
              "or `case IST_Press:` bare -- and **%s** are spelled as a hex integer (`case 0x26:`), which the "
              "integer rule reads as its value. A member's value is its declaration ordinal, so the arm's integer "
              "and the label meet as equal values; the two spellings agree where a function writes one and another "
              "writes the other: `UWindowPulldownMenu.KeyDown` spells `0x26` for the arm `WindowConsole.KeyEvent` "
              "spells `IK_Up`, and both names exist in this build's `EInputKey`. The same enum is declared again by "
              "classes that are not its owner and the declarations disagree: `TriggeredScript` declares "
              "`ETriggerType` with `TT_PlayerProximity` first, and a re-declaration without the commented-out "
              "`TT_PawnProximity` puts `TT_HumanPlayerProximity` one ordinal higher. A label folds through the "
              "table of the class that spells it, so `TT_ClassProximity` is 1 in `TriggeredScript.IsRelevant` and "
              "`SG1_TEALC` is 3 in `CommandInterface.ConvertEnumToName` (bare in a class that declares "
              "`InteractionType` beside `TranslationUser`), and **%s** label(s) stay unfolded because no table "
              "places them."
              % (comma(census_value(rows, "enums_declared")), comma(census_value(rows, "enum_members")),
                 comma(census_value(rows, "labels_enum")), comma(census_value(rows, "labels_hex")),
                 comma(census_value(rows, "labels_ambiguous")))]
    lines += ["",
              "**Three parts of the source are not labels of the build, and the state bodies are a second function "
              "under one name.** The mask reads all three apart: **%s** `case` label(s) sit inside a build-guard "
              "part this build never compiles (`SGMenuInteraction.KeyEvent`'s `#ifdefPS2` `IK_Tab`, "
              "`WeaponZoomInteraction.KeyEvent`'s `IK_Joy1` in the same guard); **%s** sit inside a block comment, "
              "in **%s** class texts that comment out a switch -- of those, the two that a first-`*/` rule left live "
              "are `StargateContent.TeamVoicePack`'s `TAUNT` and `OTHER` cases, inside a whole disabled copy of "
              "`PlayerSpeech` whose own `ORDER` arm carries a second `/* */`, so the depth is counted (the arm "
              "count is the check: the package carries 6 arms for that switch, not 8); and %s function body(ies) "
              "hold **%s** arm(s) inside a `state` block. The bytecode names that body apart -- "
              "`WindowConsole.KeyEvent` is 3 arms and `WindowConsole.UWindow.KeyEvent` is 14 -- so the join keys on "
              "the body, and a `case` list of the state's pairs with the state's arms."
              % (comma(census_value(rows, "labels_dead")), comma(census_value(rows, "labels_commented")),
                 comma(census_value(rows, "classes_commented")), comma(census_value(rows, "bodies_state")),
                 comma(census_value(rows, "arms_state")))]
    lines += ["",
              "**How an arm leaves agrees with the source, on the arms the text could be read for.** Of **%s** "
              "matched arms, **%s** leave the way the token stream says (`break` to a `break;`, `return` to a "
              "`return`, `continue` to a `continue`/`goto`, a loop's back edge to a `for`/`while` body that ends the "
              "case); the shared `case A: case B:` body is spelled with no "
              "body in **%s** of them, and **%s** of the arms that stop with no jump at all also end their source "
              "`case` with no `break`/`return`, **%s** of the loop-back arms on a control construct."
              % (comma(census_value(rows, "end_checked")), comma(census_value(rows, "end_agree")),
                 comma(census_value(rows, "empty_agree")), comma(census_value(rows, "fallthrough_text_other")),
                 comma(census_value(rows, "loop_text_other")))]
    lines += ["",
              "**No arm's source body carries a `continue`, and none crosses a case boundary in this build.** The "
              "`text_continue` column of the arm rows reads each matched arm's own case body: **%s** of the **%s** "
              "matched arms hold one, and the block census finds **%s** `continue` statement(s) inside a `switch` "
              "block at all -- **%s** in a case arm, **%s** in a `default:` body -- so the two functions whose "
              "`continue`s neighbour a `switch` (`GUIMultiComponent.mapcontrols`, "
              "`CommandInterface.executescriptcommand`) have theirs outside every block. The byte side says the "
              "same thing from the other direction: an arm that left by a `continue` jump would land off its "
              "list's end, and the `elsewhere` row of `inventory/switch_arms.csv` is **%s**. A renderer can treat "
              "a `switch` as opaque to `continue` in this corpus."
              % (comma(census_value(rows, "arm_continue")), comma(census_value(rows, "end_checked")),
                 comma(census_value(rows, "switch_continue")), comma(census_value(rows, "arm_continue_all")),
                 comma(census_value(rows, "default_continue")),
                 _elsewhere())]
    wild = [r for r in rows if r.get("kind") == "arm" and r.get("end_agree") == "no"]
    if wild:
        lines += ["", "**%s** arm(s) leave differently in the two readings; the first are listed here so the next "
                      "reader starts from a name rather than a count." % comma(len(wild)),
                  "", "| arm | case value | text | bytes |", "|---|---|---|---|"]
        for r in wild[:8]:
            lines.append("| `%s.%s` arm %s | %s | %s | %s |" % (r["class"], r["function"], r["arm"],
                                                               r.get("label") or "", r["text_end"],
                                                               r["byte_end"]))
    lines += ["", "The rows are `inventory/text_switches.csv` (`tools/text-switches.py`), one per class, per "
                  "function and per matched arm; `make text-switches` re-runs the read, `--check-doc` re-renders "
                  "this block from the committed artifact and `--check` holds the classifier to a synthetic "
                  "stream, all without the dump."]
    return "\n".join(lines) + "\n"


def splice(path, block):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    lo, hi = DOC_MARK
    if lo not in text or hi not in text:
        raise SystemExit("FAIL text-switches: markers not found in %s" % path)
    head, rest = text.split(lo, 1)
    _old, tail = rest.split(hi, 1)
    return head + lo + "\n" + block + hi + tail


def write_doc(path, rows):
    # Splice before opening for write: `open("w")` truncates and the splice then reads an empty page.
    page = splice(path, doc_block(rows))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)


def check_doc(path, rows):
    bad = []
    arms = [r for r in rows if r.get("kind") == "arm"]
    n = len([r for r in arms if r.get("text_continue")])
    if n != census_value(rows, "arm_continue"):
        bad.append("the `arm_continue` census row says %d, the arm rows hold %d"
                   % (census_value(rows, "arm_continue"), n))
    if census_value(rows, "switch_continue") != (census_value(rows, "arm_continue_all")
                                                 + census_value(rows, "default_continue")):
        bad.append("the block census (%d) does not split into its arm (%d) and `default:` (%d) halves"
                   % (census_value(rows, "switch_continue"), census_value(rows, "arm_continue_all"),
                      census_value(rows, "default_continue")))
    if n > census_value(rows, "arm_continue_all"):
        bad.append("%d matched arms carry a `continue`, the whole block population has only %d"
                   % (n, census_value(rows, "arm_continue_all")))
    if bad:
        for line in bad[:20]:
            print("FAIL text-switches: %s" % line)
        print("FAIL text-switches: inventory/text_switches.csv does not reconcile its continue columns")
        return 1
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    lo, hi = DOC_MARK
    if lo not in text or hi not in text:
        print("FAIL text-switches: markers not found in %s" % path)
        return 1
    if text.split(lo, 1)[1].split(hi, 1)[0] != "\n" + doc_block(rows):
        print("FAIL text-switches: %s is stale -- regenerate with `make text-switches`" % path)
        return 1
    print("ok   text-switches: docs block reproduces from inventory/text_switches.csv")
    return 0


# --- self-test ---------------------------------------------------------------------------------------
def selftest():
    """A synthetic class body with every shape the classifier claims, so a rule that stops seeing one fails here."""
    fails = []

    def body(text):
        live = text
        return live

    src = ("function int Pick(int n)\n{\n"
           "    switch (n)\n    {\n"
           "        case 0:\n            return 1;\n"
           "        case 1:\n        case 2:\n            DoThing();\n            break;\n"
           "        case 3:\n            Log(\"three\");\n"
           "        default:\n            default.Health = 0;\n            break;\n"
           "    }\n    return 0;\n}\n")
    live = body(src)
    c = counts(live)
    if c["switch"] != 1 or c["case"] != 4 or c["default"] != 1 or c["break"] != 2:
        fails.append("counts read %r" % (dict(c),))
    if DEFAULT.findall(live) and len(DEFAULT.findall(live)) != 1:
        fails.append("`default.Health` was read as a `default:` label")
    if last_statement("return 1;") != "return":
        fails.append("`return 1;` read %r" % last_statement("return 1;"))
    if last_statement("DoThing(); break;") != "break":
        fails.append("a body ending in `break;` read %r" % last_statement("DoThing(); break;"))
    if last_statement("   ") != "empty":
        fails.append("an empty body read %r" % (last_statement("   "),))
    if last_statement("#ifndefPS2\n#endif\n") != "empty":
        fails.append("a body holding only directives read %r" % (last_statement("#ifndefPS2\n#endif\n"),))
    if last_statement("Log(\"three\");") != "other":
        fails.append("a body that falls through read %r" % last_statement("Log(\"three\");"))
    # A body that ends with a control construct falls through: the token stream ends it on the `JumpIfNot`, not on
    # the `break` inside one of its arms, so both readings say the arm does not leave unconditionally.
    if last_statement("if (x) { Log(1); } else { break; }") != "other":
        fails.append("an `if`/`else` body read %r" % last_statement("if (x) { Log(1); } else { break; }"))
    if canon_text("'Music'") != ("str", "music"):
        fails.append("a quoted label read %r" % (canon_text("'Music'"),))
    if canon_text("-3") != ("int", "-3"):
        fails.append("a negative label read %r" % (canon_text("-3"),))
    # A hex label is an integer, and the enum fold reads a qualified and a bare member the same way. The table
    # is synthetic here so the test needs no dump: `enum EFoo { A, B, C }` answers `B` = 1.
    if canon_text("0x26") != ("int", "38") or canon_text("0X0d") != ("int", "13"):
        fails.append("a hex label read %r/%r" % (canon_text("0x26"), canon_text("0X0d")))
    load_enums({("x", "y"): {"clive": "enum EFoo\n{\n\tA,\n\tB,\n\tC\n};\n"}})
    if canon_text("EFoo.B") != ("int", "1") or canon_text("B") != ("int", "1"):
        fails.append("an enum member read %r/%r" % (canon_text("EFoo.B"), canon_text("B")))
    if canon_text("NotAnEnum.B") != ("expr", "notanenum.b"):
        fails.append("a qualified non-member read %r" % (canon_text("NotAnEnum.B"),))
    load_enums({})
    if canon_arm("name", "Music") != ("str", "music") or canon_arm("byte", "7") != ("int", "7"):
        fails.append("an arm's value canonicalised as %r/%r" % (canon_arm("name", "Music"), canon_arm("byte", "7")))
    # The join, on a synthetic pair: arm 2 is the shared body, arm 3 falls through, arm 1 returns.
    arms = [{"arm": "1", "label_kind": "byte", "label": "0", "end": "return"},
            {"arm": "2", "label_kind": "byte", "label": "1", "end": "empty"},
            {"arm": "3", "label_kind": "byte", "label": "3", "end": "fallthrough"}]
    spans = [((live.find("{\n    switch"), len(live)), "")]
    got = compare_function(arms, spans, live)
    if (got["found"], got["positional"], got["checked"], got["agree"]) != (3, 0, 3, 3):
        fails.append("the synthetic join read found=%d positional=%d checked=%d agree=%d"
                     % (got["found"], got["positional"], got["checked"], got["agree"]))
    # A symbolic label (`case MB_OK:`) is matched by position and counted as such, never as a value match.
    sym = ("function f(int n)\n{\n    switch (n)\n    {\n"
           "        case MB_OK:\n            return 1;\n"
           "        case MB_CANCEL:\n            return 2;\n"
           "    }\n    return 0;\n}\n")
    sym_spans = [((sym.find("{\n    switch"), len(sym)), "")]
    got2 = compare_function([{"arm": "1", "label_kind": "byte", "label": "0", "end": "return"},
                             {"arm": "2", "label_kind": "byte", "label": "1", "end": "return"}], sym_spans, sym)
    if (got2["found"], got2["positional"], got2["agree"]) != (2, 2, 2):
        fails.append("a symbolic label joined by position read found=%d positional=%d agree=%d"
                     % (got2["found"], got2["positional"], got2["agree"]))
    # A `;` or a `:` inside a quoted string is not a statement boundary: the cut is searched in the literal-blanked
    # buffer and the statement is read from the buffer that keeps the string.
    def litmask(t):
        return re.sub(r'"[^"]*"', lambda m: " " * len(m.group(0)), t)

    for seg in ('return ".m3u;.pls;.b4s";', 'return "Quicksave: Current";', 'return "Checkpoint:"@Name;'):
        if last_statement(seg, litmask(seg)) != "return":
            fails.append("a `;`/`:` inside a string broke the last statement of %r (read %r)"
                         % (seg, last_statement(seg, litmask(seg))))
    # The list a `case` belongs to is its innermost `switch`: an outer arm that opens a nested `switch` still ends on
    # its own `break`, and the nested arm is joined to the nested block rather than to the outer list's next case.
    nest = ("function f(int n)\n{\n    switch (n)\n    {\n"
            "        case 1:\n            switch (m)\n            {\n"
            "                case EditControl:\n                    DoThing();\n                    break;\n"
            "            }\n            break;\n"
            "        case 2:\n            return 3;\n"
            "    }\n    return 0;\n}\n")
    nest_spans = [((nest.find("{\n    switch"), len(nest)), "")]
    nest_arms = [{"site": "1", "arm": "1", "label_kind": "byte", "label": "1", "end": "break"},
                 {"site": "1", "arm": "2", "label_kind": "byte", "label": "2", "end": "return"},
                 {"site": "2", "arm": "1", "label_kind": "expr", "label": "", "end": "break"}]
    got3 = compare_function(nest_arms, nest_spans, nest, nest)
    if (got3["found"], got3["positional"], got3["checked"], got3["agree"]) != (3, 1, 3, 3):
        fails.append("a nested switch joined as found=%d positional=%d checked=%d agree=%d"
                     % (got3["found"], got3["positional"], got3["checked"], got3["agree"]))
    # The `continue` column: an arm whose body holds `if (x) continue;` carries one, and an arm next to it that
    # does not carries none -- the join `switch-join.csv` made for arm counts, one statement over.
    cont = ("function f(int n)\n{\n    for (i = 0; i < 3; i++)\n    {\n        switch (n)\n        {\n"
            "            case 1:\n                if (n > 2) continue;\n                break;\n"
            "            case 2:\n                DoThing();\n                break;\n"
            "            default:\n                break;\n        }\n    }\n}\n")
    cont_spans = [((cont.find("{\n        switch"), len(cont)), "")]
    got4 = compare_function([{"arm": "1", "label_kind": "byte", "label": "1", "end": "break"},
                             {"arm": "2", "label_kind": "byte", "label": "2", "end": "break"}],
                            cont_spans, cont)
    if got4["arm_continue"] != 1:
        fails.append("an arm carrying `if (x) continue;` read arm_continue=%d" % got4["arm_continue"])
    if [c for _r, _t, _b, c, _k in got4["detail"]][1] != []:
        fails.append("an arm with no `continue` read %r" % (got4["detail"][1][3],))
    # The break column reads the same arm bodies: the first arm spells its `break` at the tail and its
    # `if (x) continue;` before it, and the second carries neither an interior break nor a continue.
    if [k for _r, _t, _b, _c, k in got4["detail"]][0] != ["tail"]:
        fails.append("an arm whose only `break` is its tail read %r" % ([k for _r, _t, _b, _c, k in got4["detail"]][0],))
    # A label inside a build-guard part this build never compiles is not a label of this build: the PS2 branch of
    # `SGMenuInteraction.KeyEvent` spells `case IK_Tab:` beside the Xbox `case IK_T:`, and counting it cut the live
    # arm's body at the dead label, so a `break` read as a fall-through.
    guarded = ("function f(int n)\n{\n    switch (n)\n    {\n"
               "#ifndefPS2\n        case 1:\n#endif\n#ifdefPS2\n        case 9:\n#endif\n"
               "            DoThing();\n            break;\n"
               "        case 2:\n            return 3;\n    }\n    return 0;\n}\n")
    gdead, _gp = text_tool().guard_map(guarded)
    if counts(guarded, gdead)["case"] != 2:
        fails.append("a dead `case` label was counted (%d)" % counts(guarded, gdead)["case"])
    if counts(guarded)["case"] != 3:
        fails.append("the same list without the guard map did not read 3 labels")
    ggot = compare_function([{"arm": "1", "label_kind": "byte", "label": "1", "end": "break"},
                             {"arm": "2", "label_kind": "byte", "label": "2", "end": "return"}],
                            [((guarded.find("{\n    switch"), len(guarded)), "")], guarded, guarded, gdead)
    if (ggot["found"], ggot["agree"]) != (2, 2):
        fails.append("a guarded case list joined found=%d agree=%d" % (ggot["found"], ggot["agree"]))
    # The declaring class's own enum table is the authority: two classes spell one enum in different member order,
    # and a label folds through the table of the class that spells it, with the first declaration only a fallback.
    load_enums({("p", "first"): {"clive": "enum EType\n{\n\tA,\n\tB,\n\tC\n};\n"},
                ("p", "second"): {"clive": "enum EType\n{\n\tC,\n\tB,\n\tA\n};\n"}})
    if canon_text("A") != ("int", "0"):
        fails.append("the corpus-wide enum table read `A` as %r" % (canon_text("A"),))
    if canon_text("A", CLASS_ENUMS[("p", "second")]) != ("int", "2"):
        fails.append("a class's own enum declaration read `A` as %r"
                     % (canon_text("A", CLASS_ENUMS[("p", "second")]),))
    if canon_text("EType.A", CLASS_ENUMS[("p", "second")]) != ("int", "2"):
        fails.append("a qualified label read through the corpus table, not the class's own (%r)"
                     % (canon_text("EType.A", CLASS_ENUMS[("p", "second")]),))
    load_enums({})
    # A block comment nests here: the text of `StargateContent.TeamVoicePack` comments out a whole copy of a
    # function and carries a second disabled block inside it, so a first-`*/` rule exposes two `case` labels at
    # file scope and the package says the switch has 6 arms, not 8.
    nested = ("function f(int n)\n{\n    switch (n)\n    {\n"
              "/*function g(int n)\n{\n    case 1:\n        /*disabled*/\n        break;\n    case 2:\n"
              "        break;\n}\n*/\n        case 3:\n            return 1;\n    }\n    return 0;\n}\n")
    if counts(mask_comments(nested))["case"] != 1:
        fails.append("a nested block comment left %d `case` labels live"
                     % counts(mask_comments(nested))["case"])
    if [lab[1] for _o, _c, marks in label_groups(mask_comments(nested), nested,
                                                 (nested.find("{\n    switch"), len(nested)))
            for lab, _s, _e in marks] != ["3"]:
        fails.append("a nested block comment's labels reached the list")
    # A `state` body's function is a second body under the same name: the class body's `KeyEvent` and `state
    # UWindow`'s are two functions the bytecode names apart (`WindowConsole.KeyEvent`, `WindowConsole.UWindow.
    # KeyEvent`), so a span carries the state it sits in and the join pairs body with body.
    stated = ("function bool KeyEvent(EInputKey K)\n{\n\tswitch (K)\n\t{\n\tcase 1:\n\t\treturn true;\n\t}\n"
              "\treturn false;\n}\n\nstate UWindow\n{\n\tfunction bool KeyEvent(EInputKey K)\n\t{\n"
              "\t\tswitch (K)\n\t\t{\n\t\tcase 2:\n\t\t\treturn true;\n\t\t}\n\t\treturn false;\n\t}\n}\n")
    slive, _sc = text_tool().mask(stated)
    tagged = function_spans(slive)
    if [(n, st) for n, _sp, st in tagged] != [("keyevent", ""), ("keyevent", "uwindow")]:
        fails.append("the state bodies read %r" % ([(n, st) for n, _sp, st in tagged],))
    for _name, span, st in tagged:
        got_lab = [lab[1] for _o, _c, marks in label_groups(mask_comments(stated), slive, span)
                   for lab, _s, _e in marks]
        if got_lab != [("1" if st == "" else "2")]:
            fails.append("the %r body's labels read %r" % (st, got_lab))
    if fails:
        for f in fails:
            print("FAIL text-switches: %s" % f)
        return 1
    print("ok   text-switches: `switch`/`case`/`default`/`break` counted with comments and literals masked, "
          "`default.` is not a label, a case body's last statement is read through its blocks, an arm is matched "
          "by its own value and the shared body and the fallthrough agree with the bytes")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--system", help="the dump's System directory (or one package)")
    ap.add_argument("-o", "--out", default=CSV_PATH)
    ap.add_argument("--write-doc", metavar="PAGE")
    ap.add_argument("--check-doc", metavar="PAGE")
    ap.add_argument("--report", action="store_true", help="print the block, write nothing")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        return selftest()
    if args.check_doc:
        return check_doc(args.check_doc, read_csv(args.out))
    if not args.system:
        if args.write_doc:
            rows = read_csv(args.out)
            if not rows:
                ap.error("%s does not exist -- a doc render needs the artifact or --system" % args.out)
            write_doc(args.write_doc, rows)
            print("ok   text-switches: wrote %s from %s" % (args.write_doc, args.out))
            return 0
        ap.error("a measurement needs --system (or --check)")
    texts = walk(args.system)
    load_enums(texts)
    arms_c, arms_f, meta = arms_by_class()
    rows, census = join(texts, arms_c, arms_f, meta)
    if args.write_doc:
        write_doc(args.write_doc, rows)
    if args.report:
        print(doc_block(rows).strip())
    if not args.report:
        with open(args.out, "w", encoding="utf-8", newline="") as fh:
            fh.write(render(rows))
        print("wrote %s (%d rows) -- %d classes with text, %d arms, %d matched, %d exit(s) compared"
              % (args.out, len(rows), len(texts), census["byte_arms"], census["labels_found"],
                 census["end_checked"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
