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
          "labels_missing", "text_end", "text_continue", "byte_end", "end_agree", "end_checked", "verdict", "count",
          "note"]

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
    "arm_continue": "arms whose source case body carries a `continue` statement",
    "switch_continue": "`continue` statements inside a `switch` block -- case arms and `default:` bodies together",
    "arm_continue_all": "of those, the ones inside a case arm's body (every label group, matched or not)",
    "default_continue": "of those, the ones inside a `default:` body",
}

CENSUS_ORDER = ("text_switch", "text_case", "text_default", "text_break", "byte_lists", "byte_arms",
                "byte_defaults", "byte_break", "classes_joined", "label_agree", "label_more", "label_less",
                "text_only", "bytes_only", "labels_found", "labels_missing", "labels_by_position", "end_agree",
                "end_checked", "empty_agree", "fallthrough_text_other", "loop_text_other", "functions_joined",
                "arm_continue", "switch_continue", "arm_continue_all", "default_continue")

# A `case` label's expression: everything up to the `:` that is not a `::`, on one line, no statement separator.
# UnrealScript folds keyword case -- `Switch(Role)` and `State` are the build's own spelling -- so every pattern here
# is case-insensitive (`tools/text-branches.py` had already found 55 capitalised `State` declarations).
CASE = re.compile(r"(?<![#\w$.])case\b[ \t]+([^:;{}\n]+?)[ \t]*:(?!:)", re.I)
SWITCH = re.compile(r"(?<![#\w$.])switch[ \t]*\(", re.I)
# `default:` is a label; `default.Health` and `defaultproperties` are not.
DEFAULT = re.compile(r"(?<![#\w$.])default\b(?!\s*[.\w])", re.I)
BREAK = re.compile(r"(?<![#\w$.])break\b", re.I)
RETURN = re.compile(r"(?<![#\w$.])return\b", re.I)
CONTINUE = re.compile(r"(?<![#\w$.])continue\b", re.I)
GOTO = re.compile(r"(?<![#\w$.])goto\b", re.I)
INT = re.compile(r"[-+]?\d+")


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
def canon_text(expr):
    """`(kind, value)` for a `case` label's expression as the source spells it."""
    e = expr.strip()
    if not e:
        return ("expr", "")
    if len(e) >= 2 and e[0] in "'\"" and e[-1] == e[0]:
        return ("str", e[1:-1].replace(e[0] * 2, e[0]).lower())
    if INT.fullmatch(e):
        return ("int", str(int(e)))
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


def counts(text):
    c = collections.Counter()
    c["switch"] = len(SWITCH.findall(text))
    c["case"] = len(CASE.findall(text))
    c["default"] = len(DEFAULT.findall(text))
    c["break"] = len(BREAK.findall(text))
    c["return"] = len(RETURN.findall(text))
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


def mask_comments(text):
    """The buffer with **comments only** blanked: literals are kept, because a `case` label may *be* one.

    `tools/text-branches.py`'s `mask` blanks comments and literals both, which is right for counting keywords --
    and exactly wrong for reading a label: `case '0':` arrives as `case    :`. Both buffers are length-preserving
    and blank only comments here, so a position means the same thing in each.
    """
    out = list(text)
    n = len(text)
    i = 0
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
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


def label_groups(clive, live, span):
    """`[(open, close, [(label, start, end), ...])]` -- one group per `switch` block, a nested block's labels out.

    The list a `case` belongs to is its **innermost enclosing `switch`**, not every `case` token in the function:
    in `UWindowConsoleClientWindow.Notify` each outer arm opens a `switch (C)` of its own, so a flat label list ends
    the outer arm's body at the *inner* case and reports an arm that leaves on a `break` as falling through (five
    of the fifteen arms the two readings disagreed on).
    """
    spans = switch_spans(live, span[0], span[1])
    groups = []
    for ob, cb in spans:
        marks = []
        for mark in labels_in(clive, ob, cb):
            if any(o > ob and c <= cb and o <= mark[1] <= c for o, c in spans):
                continue                      # a nested switch's own label, not this list's
            marks.append(mark)
        if marks:
            groups.append((ob, cb, marks))
    groups.sort(key=lambda g: g[0])
    return groups


def labels_in(live, start, end):
    """`[( (kind, value) or None for a `default:`, match_start, match_end )]` for one function body's labels.

    `default:` is a boundary as well as a label: an arm's body ends where the next `case` **or** `default` of the
    list begins, so a case that ends the testable list must not read on into the `default:` body.
    """
    marks = [(canon_text(m.group(1)), m.start(), m.end()) for m in CASE.finditer(live, start, end)]
    marks += [(None, m.start(), m.end()) for m in DEFAULT.finditer(live, start, end)]
    marks.sort(key=lambda t: t[1])
    return marks


def function_spans(live):
    """`[(name, span)]` for one class text: the declaration rule and brace walk `tools/class-text-bodies.py` owns."""
    ctb = ctb_tool()
    ms = list(ctb.FUNC.finditer(live))
    out = []
    for k, m in enumerate(ms):
        limit = ms[k + 1].start() if k + 1 < len(ms) else len(live)
        span = ctb.body_span(live, m.end(), limit)
        if span is None:
            continue
        out.append((m.group(1).lower(), span))
    return out


def walk(system_dir):
    """`(package, class) -> {class, functions: {name: [span, ...]}, live, counts}` for every readable class text."""
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
            funcs = collections.defaultdict(list)
            for name, span in function_spans(live):
                funcs[name].append(span)
            name = pkg.name(exp["name"])
            rows[(package, name.lower())] = {"class": name, "live": live, "clive": mask_comments(text),
                                             "functions": funcs, "counts": counts(live)}
    return rows


# --- the bytecode side ------------------------------------------------------------------------------
def arms_by_class(path=ARMS_CSV):
    """`(package, class) -> arm rows`, `(package, class, function) -> rows` and the per-class counters, from the
    committed census: the arms, the lists and the `break`s each class carries."""
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
        key = (r["package"], cls.lower())
        if kind == "arm":
            classes[key].append(r)
            funcs[(r["package"], cls.lower(), fn.lower())].append(r)
            meta[key]["break"] += int(r.get("end") == "break")
        elif kind == "list":
            meta[key]["lists"] += 1
        else:
            meta[key]["defaults"] += 1
    return classes, funcs, meta


# --- the join ----------------------------------------------------------------------------------------
def compare_function(arms, spans, clive, live=None):
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
    out = {"found": 0, "positional": 0, "checked": 0, "agree": 0, "detail": [], "blocks": 0, "arm_continue": 0}
    best = None
    for span in spans:
        groups = label_groups(clive, live if live is not None else clive, span)
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
                out["detail"].append((r, None, "", []))
                continue
            used[pick_i] = True
            out["found"] += 1
            seg_end = labels[pick_i + 1][1] if pick_i + 1 < len(labels) else close
            seg_end = min(seg_end, close)
            # The `continue` statements an arm's own source body carries: the column that joins this census to
            # `tools/text-branches.py`'s continue split, one statement over from `switch-join.csv`'s arm counts.
            cs = text_tool().continue_sites(live if live is not None else clive, None,
                                            labels[pick_i][2], seg_end)
            if cs:
                out["arm_continue"] += 1
            text_end = last_statement(clive[labels[pick_i][2]:seg_end],
                                      (live if live is not None else clive)[labels[pick_i][2]:seg_end])
            byte_end = r.get("end") or ""
            out["detail"].append((r, text_end, byte_end, [k for _o, k in cs]))
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
            for span in spans:
                groups = label_groups(t["clive"], t["live"], span)
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
        for (package, cls_lower, fn_lower) in [k for k in arms_f if k[0] == key[0] and k[1] == key[1]]:
            fn_arms = arms_f[(package, cls_lower, fn_lower)]
            spans = t["functions"].get(fn_lower) or []
            got = compare_function(fn_arms, spans, t["clive"], t["live"])
            found, checked, agree, detail = got["found"], got["checked"], got["agree"], got["detail"]
            match_kind = "value" if got["positional"] == 0 and found else ("position" if got["positional"] else "none")
            census["functions_joined"] += 1
            census["labels_found"] += found
            census["labels_missing"] += len(fn_arms) - found
            census["labels_by_position"] += got["positional"]
            census["end_checked"] += checked
            census["end_agree"] += agree
            census["arm_continue"] += got["arm_continue"]
            rows.append({"kind": "function", "package": package, "class": cls_name, "function": fn_lower,
                         "byte_arms": len(fn_arms), "labels_found": found,
                         "labels_missing": len(fn_arms) - found, "end_agree": agree, "end_checked": checked,
                         "verdict": "agree" if agree == checked else "differs", "label_kind": match_kind,
                         "count": "", "note": ""})
            for r, text_end, byte_end, conts in detail:
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
                             "byte_end": byte_end, "end_agree": same, "count": "",
                             "note": "site %s" % (r.get("site") or "")})
    for key, value in switch_continue_census(texts).items():
        census[key] += value
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
              "**%s** more are paired **by position**, and only where every label in that function is a symbolic "
              "constant (`case MB_OK:`) the compiler folds to the arm's integer, which is a different reading and "
              "is counted apart; **%s** are matched by neither -- an expression-valued case, or a text that is not "
              "the revision the package compiled."
              % (comma(census_value(rows, "labels_found") - census_value(rows, "labels_by_position")),
                 comma(census_value(rows, "labels_found") + census_value(rows, "labels_missing")),
                 comma(census_value(rows, "labels_by_position")),
                 comma(census_value(rows, "labels_missing")))]
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
        fails.append("an empty body read %r" % last_statement("   "))
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
    if canon_arm("name", "Music") != ("str", "music") or canon_arm("byte", "7") != ("int", "7"):
        fails.append("an arm's value canonicalised as %r/%r" % (canon_arm("name", "Music"), canon_arm("byte", "7")))
    # The join, on a synthetic pair: arm 2 is the shared body, arm 3 falls through, arm 1 returns.
    arms = [{"arm": "1", "label_kind": "byte", "label": "0", "end": "return"},
            {"arm": "2", "label_kind": "byte", "label": "1", "end": "empty"},
            {"arm": "3", "label_kind": "byte", "label": "3", "end": "fallthrough"}]
    spans = [(live.find("{\n    switch"), len(live))]
    got = compare_function(arms, spans, live)
    if (got["found"], got["positional"], got["checked"], got["agree"]) != (3, 0, 3, 3):
        fails.append("the synthetic join read found=%d positional=%d checked=%d agree=%d"
                     % (got["found"], got["positional"], got["checked"], got["agree"]))
    # A symbolic label (`case MB_OK:`) is matched by position and counted as such, never as a value match.
    sym = ("function f(int n)\n{\n    switch (n)\n    {\n"
           "        case MB_OK:\n            return 1;\n"
           "        case MB_CANCEL:\n            return 2;\n"
           "    }\n    return 0;\n}\n")
    sym_spans = [(sym.find("{\n    switch"), len(sym))]
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
    nest_spans = [(nest.find("{\n    switch"), len(nest))]
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
    cont_spans = [(cont.find("{\n        switch"), len(cont))]
    got4 = compare_function([{"arm": "1", "label_kind": "byte", "label": "1", "end": "break"},
                             {"arm": "2", "label_kind": "byte", "label": "2", "end": "break"}],
                            cont_spans, cont)
    if got4["arm_continue"] != 1:
        fails.append("an arm carrying `if (x) continue;` read arm_continue=%d" % got4["arm_continue"])
    if [c for _r, _t, _b, c in got4["detail"]][1] != []:
        fails.append("an arm with no `continue` read %r" % (got4["detail"][1][3],))
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
