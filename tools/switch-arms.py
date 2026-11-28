#!/usr/bin/env python3
"""Where a `Case`'s arm body ends -- the instrument the switch join named as this page's next one (PLAN 3.7).

A `switch` is a chain of `Case` tokens: each carries the widened-code offset of the *next* case of its own list
(`execSwitch`, `0x0004ca20`), and the last carries `0xffff`. `inventory/switch_join.csv` measured how many arms a
list has; what a decompiler still cannot read from the stream is **which statements belong to an arm** -- the arm
bodies sit between consecutive `Case` tokens with nothing marking their end.

This walks the same scripts and measures that extent:

* an arm's body is the statements **after its `Case` token's own value expression** (a child of the token, so
  invisible in the statement stream) **up to the next `Case` of the same list**. The boundary is the chain, not a
  scan: a `switch` nested inside an arm is a list of its own, so its `Case` tokens are body statements here;
* the list's **terminator** case (`0xffff`) opens the `default:` body, whose extent is the switch's own end --
  measured from where the arms' `break` jumps land, not assumed.

An arm's last statement says how it leaves: a `Jump` is a `break` when it lands on that end, `elsewhere` when it
does not (a `continue`, or a jump out of a loop the switch sits in), and `loop` when the target is *behind* it --
a `for`/`while` loop's own back edge, so the arm runs on into the next case's code once the loop ends. A `Return`
leaves the function, and anything
else **falls into the next case's test code**. An arm with no body at all is the source's shared `case A: case B:`
body -- the same population `inventory/jump_branches.csv` counts as its `empty_arm` row, read from the other end and
held to it by `--check`. A `Return`'s operand is a token of its own, so where that operand is a bare variable the
statement stream hands it over as the arm's last statement: the operand is dropped before the exit is read, and the
arm ends on its `Return`.

There is **no compiler prefix to strip**: an arm's first statement is the source's own, which is why
`SetCategoryVolume` opens one with a `JumpIfNot` (`if (MusicVolume != NewVolume) {...}`) and `TestSwitch` opens one
with an `Assert`. The census counts those heads rather than assuming one.

An arm's **interior** is read too: a `Jump` that lands on the list's end *before* the arm's last token is a `break`
the last-statement column cannot see (`if (x) break;` with more code after it), and one that lands anywhere else is
a loop's own edge or a nested switch's break -- two columns that say whether the arm leaves by a `break` and whether
it merely *carries* one. Offsets are file offsets; the rows write them in the widened (guest) space, as the operand
census does.

An arm's **case value** is the `Case` token's own *child* expression -- invisible in the statement stream -- and it
is read here as well: `label_kind` is the operand census's vocabulary (`byte`, `int`, `string`, `name`, `expr`) and
`label` is the value itself, names resolved through the package's name table. That is what lets an arm be named
(`case 'Music':`) and compared with the shipped source's own `case` labels one arm at a time.

    python3 tools/switch-arms.py --system DUMP/System -o inventory/switch_arms.csv --write-doc docs/BYTECODE.md

`--check` needs no dump: it runs a synthetic stream through the same reader and holds the committed artifact's arm
total and empty-arm count to `inventory/switch_join.csv` and `inventory/jump_branches.csv`.
"""
import argparse
import collections
import csv
import importlib.util
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(ROOT, "inventory", "switch_arms.csv")
JUMP_CSV = os.path.join(ROOT, "inventory", "jump_branches.csv")
JOIN_CSV = os.path.join(ROOT, "inventory", "switch_join.csv")
DOC_MARK = ("<!-- generated:switch-arms -->", "<!-- /generated:switch-arms -->")
FIELDS = ["kind", "package", "path", "site", "arm", "case", "body_start", "body_end", "statements", "end",
          "interior_break", "interior_other", "first", "last", "label_kind", "label", "count", "note"]

CENSUS_NOTE = {
    "lists": "case lists (a chain of `Case` tokens ending on the `0xffff` terminator)",
    "arms": "testable arms, i.e. every case of every list but the terminator",
    "defaults": "terminator cases, each opening a `default:` body",
    "arm_statements": "statements inside arm bodies",
    "arm_min": "the shortest arm body, in statements",
    "arm_max": "the longest",
    "arm_mean_x10": "mean arm length, in tenths of a statement",
    "empty": "arms with no body at all -- the shared `case A: case B:` shape",
    "head_if": "arms whose first statement is a `JumpIfNot`, i.e. the source's own `if` at the head of the arm",
    "end_break": "arms that leave through a `Jump` landing on their list's end",
    "end_elsewhere": "arms that leave through a `Jump` landing somewhere else (`continue`, or out of a loop)",
    "end_return": "arms that leave through a `Return`",
    "end_loop": "arms whose last `Jump` is a loop's own back edge (its target is behind it), so the arm runs on into "
                "the next case's code once the loop ends",
    "return_operand": "arms whose last statement read as a token because a `Return`'s operand is a token of its own "
                      "in the statement stream",
    "end_fallthrough": "arms that stop with no jump and a case still after them, i.e. they run into the next case's code",
    "end_into_default": "arms that stop with no jump as their list's last arm, i.e. they run into the `default:` body",
    "arm_interior": "arms whose body holds a `Jump` to their list's end before the arm's last token -- a `break` the "
                    "last-statement column cannot see",
    "interior_jumps": "those `Jump`s, one per `break` site (an arm can carry several)",
    "arm_interior_other": "arms whose body holds another `Jump` before its last token -- a loop's own edge or a nested "
                          "switch's break",
    "interior_other_jumps": "those `Jump`s",
    "lists_interior_end": "lists with no `break` tail whose interior `Jump`s still agree on one offset past the "
                          "terminator, i.e. the switch's end read from a break that is not the arm's last token",
    "lists_unanimous": "lists whose `break` jumps all land on one offset -- the switch's end",
    "lists_split": "lists whose arm jumps disagree, so the end is not one offset",
    "lists_no_break": "lists with no `break` jump at all",
    "default_statements": "statements inside `default:` bodies",
    "default_min": "the shortest `default:` body, in statements",
    "default_max": "the longest",
    "default_open": "`default:` bodies whose end no `break` names, so the extent stops at the script's end",
    "nested": "bodies (an arm or a `default:`) carrying a `Switch` of their own",
    "one_arm": "lists with a single arm, where the body's end is the terminator itself",
    "label_byte": "arms whose case value is a `ByteConst`",
    "label_int": "arms whose case value is a compact integer token (`IntConstByte`, `IntZero`, `IntOne`, `IntConst`)",
    "label_string": "arms whose case value is a `StringConst`",
    "label_name": "arms whose case value is a `NameConst`",
    "label_expr": "arms whose case value is an expression the compiler emitted (`InstanceVariable`, `PrimitiveCast`, ...)",
}

CENSUS_ORDER = ("lists", "arms", "defaults", "arm_statements", "arm_min", "arm_max", "arm_mean_x10", "empty",
                "head_if", "end_break", "end_elsewhere", "end_return", "end_loop", "return_operand",
                "end_fallthrough", "end_into_default",
                "arm_interior", "interior_jumps", "arm_interior_other", "interior_other_jumps", "lists_interior_end",
                "lists_unanimous",
                "lists_split", "lists_no_break", "default_statements", "default_min", "default_max",
                "default_open", "nested", "one_arm",
                "label_byte", "label_int", "label_string", "label_name", "label_expr")


def load_tool(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# A leaf token a `Return` can be handed as its whole operand. Only a token in this set, starting exactly where the
# `Return` ends, is read as that operand rather than as a statement of the arm.
# The postfix operators that wrap a value in a type without changing which value it is: a `case` label may be
# written as a cast, and its own operand stream is `<type byte> <expression>`.
CAST_HEADS = frozenset(("PrimitiveCast", "MetaClassCast"))

RETURN_OPERAND_LEAVES = frozenset((
    "LocalVariable", "BoolVariable", "InstanceVariable", "IntConst", "IntConstByte", "FloatConst", "ByteConst",
    "True", "False", "Nothing", "ObjectConst", "NameConst", "StringConst", "VectorConst", "RotationConst",
))


def label_of(node, resolve=None):
    """`(kind, text)` for one `Case` token's own value expression.

    The value is the token's first child, so it is invisible in the statement stream but readable here: a
    `ByteConst`, a compact int token, a `StringConst`, a `NameConst` (resolved to its name when a package is in
    hand) or -- on 35 of the 200 shipped sites -- an expression the compiler emitted instead of a literal
    (`InstanceVariable`, `PrimitiveCast`). Kinds are the vocabulary `tools/uscript-dis.py --switches` already
    counts, so an arm's value and the operand census's case-value column cannot disagree about what a token is.
    """
    kids = node.get("children") or []
    if not kids:
        return "none", ""
    k = kids[0]
    name = k.get("name") or ""
    raw = k.get("operands") or b""
    if name == "ByteConst":
        return "byte", str(raw[0] if raw else 0)
    if name == "IntConstByte":
        return "int", str(raw[0] if raw else 0)
    if name == "IntZero":
        return "int", "0"
    if name == "IntOne":
        return "int", "1"
    if name == "IntConst":
        return "int", str(int.from_bytes(raw[:4], "little", signed=True)) if len(raw) >= 4 else ""
    if name == "StringConst":
        return "string", raw.split(b"\x00", 1)[0].decode("latin-1")
    if name in CAST_HEADS and k.get("children"):
        # A case value the compiler typed: `case EPlayerPos.POS_Auto:` writes `PrimitiveCast <type> <literal>`, so
        # the value is underneath the cast. `GameProfile.TextPositionDescription`'s five arms were read as "an
        # expression" for want of this, and its text spells the same five values `0..4`.
        return label_of({"children": k["children"][:1]}, resolve)
    if name == "NameConst":
        # The operand is the archive's *compact* index (bit 7 sign, bit 6 "more bytes", six bits then seven),
        # not a fixed-width integer: `74 06` is 0x1b4 = `Music`, and reading it as a word answers the name 1,216
        # entries away (`read_index`, the reader every other index operand in this tree uses).
        if resolve is not None and raw:
            try:
                index, _end = DIS.read_index(raw, 0)
            except IndexError:
                return "name", ""
            got = resolve(index)
            if got:
                return "name", got
        return "name", ""
    return "expr", name


def measure_script(stmts, links, edges):
    """`(heads, census)` for one script: every list with its arms, its `default:` body and its own end.

    `stmts` are the script's statement tokens in stream order, `links` the `Case` chain from `jump-graph`'s walk and
    `edges` its resolved jumps `(offset, op, operand, target file offset)`. Offsets handed back are **file** offsets;
    the rows convert them to the widened (guest) space the operands are written in, which is what a decompiler reads a
    jump in.
    """
    census = collections.Counter()
    heads = []
    if not stmts:
        return heads, census
    offs = [n["offset"] for n in stmts]
    index = {o: i for i, o in enumerate(offs)}
    jump_to = {o: t for o, op, _v, t in edges if op == "Jump"}
    # The lists come from the same rule the join uses (`jump-branches.switch_census`), so an arm here is an arm there.
    _sw, chains, _empty, _out, _no_list = JB.switch_census(stmts, links)
    for chain, _counted in chains:
        census["lists"] += 1
        if len(chain) < 2:
            # A chain of one case is the terminator alone: a `switch` with no case, which the join names separately.
            census["defaults"] += 1
            continue
        pos = [index[c] for c in chain]
        term = pos[-1]
        arms = []
        for i in range(len(pos) - 1):
            body = stmts[pos[i] + 1:pos[i + 1]]
            # A `Return`'s operand is a token of its own: `dis_parse` leaves a simple leaf operand (a bare
            # `LocalVariable`) as a sibling of the `Return` rather than as its child, so an arm's last "statement"
            # can be that expression. The arm ends where its last *statement* does, so a trailing **leaf** token that
            # starts exactly where the `Return` ends is dropped before the exit is read -- two arms in the corpus take
            # that shape (`StreamPlaylistManager.InsertInPlaylist` arms 1 and 2). A trailing `Jump` or `Return` after
            # a `Return` is *not* dropped: that is dead code the arm's own stream carries.
            operands = 0
            while (len(body) >= 2 and body[-2]["name"] == "Return"
                   and body[-1]["name"] in RETURN_OPERAND_LEAVES
                   and body[-1]["offset"] == body[-2]["end"]):
                body = body[:-1]
                operands += 1
            if operands:
                census["return_operand"] += 1
            last = body[-1] if body else None
            if last is None:
                end = "empty"
                census["empty"] += 1
            elif last["name"] == "Return":
                end = "return"
            elif last["name"] == "Jump" and last["offset"] in jump_to:
                end = "jump"            # a `break` or a `continue`: the list's own end decides which, below
            elif i == len(pos) - 2:
                # The list's last testable arm: what follows it is the terminator, so a body that stops without a
                # jump runs into the `default:` arm rather than into a case's own code.
                end = "into-default"
            else:
                end = "fallthrough"
            if body:
                census["arm_statements"] += len(body)
                census["arm_min"] = min(census.get("arm_min", 10 ** 9), len(body))
                census["arm_max"] = max(census.get("arm_max", 0), len(body))
                if body[0]["name"] == "JumpIfNot":
                    census["head_if"] += 1
            if any(n["name"] == "Switch" for n in body):
                census["nested"] += 1
            arms.append({"arm": i + 1, "case": chain[i], "node": stmts[pos[i]], "body": body, "end": end})
            census["arms"] += 1
        if len(arms) == 1:
            census["one_arm"] += 1
        # The list's end is where its `break` jumps land -- and a `Jump` out of an arm is only a `break` if it lands
        # on that one offset, so the category is settled here rather than guessed from the jump's direction.
        breaks = sorted({jump_to[r["body"][-1]["offset"]] for r in arms
                         if r["body"] and r["body"][-1]["name"] == "Jump"
                         and r["body"][-1]["offset"] in jump_to
                         and jump_to[r["body"][-1]["offset"]] > offs[term]})
        if not breaks:
            end_at = None
            census["lists_no_break"] += 1
            # A list whose arms all leave by `return` still carries `break`s inside them; when those agree on one
            # offset past the terminator, that offset is the switch's end and the `default:` body can be bounded.
            inner = sorted({jump_to[n["offset"]] for r in arms for n in r["body"][:-1]
                            if n["name"] == "Jump" and n["offset"] in jump_to
                            and jump_to[n["offset"]] > offs[term]})
            if len(inner) == 1:
                end_at = inner[0]
                census["lists_interior_end"] += 1
        elif len(breaks) == 1:
            end_at = breaks[0]
            census["lists_unanimous"] += 1
        else:
            end_at = None
            census["lists_split"] += 1
        for r in arms:
            if r["end"] == "jump":
                r["end"] = ("break" if end_at is not None and jump_to[r["body"][-1]["offset"]] == end_at
                            else "elsewhere")
                if r["end"] == "elsewhere":
                    # Direction decides what an `elsewhere` jump is: a target *behind* the jump is a loop's own back
                    # edge inside the arm, so the arm does not leave by it -- the loop ends and the arm runs on into
                    # the next case's code, which is what the source's `for (...)` body says too.
                    tail = r["body"][-1]
                    if jump_to[tail["offset"]] < tail["offset"]:
                        r["end"] = "loop"
            # The interior: a `Jump` on the list's end *before* the arm's last token is a `break` this column cannot
            # see, and any other `Jump` there is a loop's own edge or a nested switch's break.
            inner_break = inner_other = 0
            for n in r["body"][:-1]:
                if n["name"] != "Jump" or n["offset"] not in jump_to:
                    continue
                if end_at is not None and jump_to[n["offset"]] == end_at:
                    inner_break += 1
                else:
                    inner_other += 1
            r["interior_break"], r["interior_other"] = inner_break, inner_other
            if inner_break:
                census["arm_interior"] += 1
                census["interior_jumps"] += inner_break
            if inner_other:
                census["arm_interior_other"] += 1
                census["interior_other_jumps"] += inner_other
            if r["end"] != "empty":
                census["end_" + r["end"].replace("-", "_")] += 1
        # The terminator case opens the `default:` body, whose extent is the end the breaks name.
        tail = [n for n in stmts[term + 1:] if end_at is None or n["offset"] < end_at]
        if end_at is None:
            census["default_open"] += 1
        if any(n["name"] == "Switch" for n in tail):
            census["nested"] += 1
        census["defaults"] += 1
        census["default_statements"] += len(tail)
        census["default_min"] = min(census.get("default_min", 10 ** 9), len(tail))
        census["default_max"] = max(census.get("default_max", 0), len(tail))
        heads.append({"case": chain[0], "arms": arms,
                      "default": {"case": chain[-1], "node": stmts[term], "body": tail, "end_at": end_at},
                      "end": end_at})
    return heads, census


def script_rows(package, path, stmts, links, edges, f2g, census, resolve=None):
    """Every row one script contributes -- its arms (with their case values), its `default:` body and a list row
    per switch."""
    heads, local = measure_script(stmts, links, edges)
    # The census is merged before the early return: a list of one case (a `switch` with no case) writes no row of its
    # own but is still a list, and dropping it here made 200 lists read as 198.
    for key, value in local.items():
        if key.endswith("_min"):
            census[key] = min(census.get(key, value), value)
        elif key.endswith("_max"):
            census[key] = max(census.get(key, value), value)
        elif key in CENSUS_ORDER:
            census[key] += value
    if not heads:
        return []
    rows = []

    def g(off):
        return "0x%04x" % f2g[off] if off in f2g else ""

    for site, head in enumerate(heads, start=1):
        for r in head["arms"]:
            body = r["body"]
            kind, label = label_of(r["node"], resolve)
            census["label_" + ("expr" if kind == "none" else kind)] += 1
            rows.append({
                "kind": "arm", "package": package, "path": path, "site": site, "arm": r["arm"],
                "case": g(r["case"]),
                "body_start": g(body[0]["offset"]) if body else "",
                "body_end": g(body[-1]["end"]) if body else "",
                "statements": len(body), "end": r["end"],
                "interior_break": r.get("interior_break", 0), "interior_other": r.get("interior_other", 0),
                "first": body[0]["name"] if body else "", "last": body[-1]["name"] if body else "",
                "label_kind": kind, "label": label,
                "note": "",
            })
        tail = head["default"]["body"]
        rows.append({
            "kind": "default", "package": package, "path": path, "site": site, "arm": "",
            "case": g(head["default"]["case"]),
            "body_start": g(tail[0]["offset"]) if tail else "",
            "body_end": g(tail[-1]["end"]) if tail else "",
            "statements": len(tail), "end": "end" if head["default"]["end_at"] is not None else "open",
            "first": tail[0]["name"] if tail else "", "last": tail[-1]["name"] if tail else "",
            "note": "" if head["default"]["end_at"] is not None else "no `break` names this list's end",
        })
        rows.append({"kind": "list", "package": package, "path": path, "site": site, "arm": "",
                     "case": g(head["case"]), "body_start": "", "body_end": "",
                     "statements": sum(len(r["body"]) for r in head["arms"]),
                     "end": "end" if head["end"] is not None else "open", "first": "", "last": "",
                     "note": "%d arm(s), end %s" % (len(head["arms"]),
                                                    g(head["end"]) if head["end"] is not None else "not named")})
    return rows


def namer(pkg):
    """A bounds-safe `index -> name` for one package's name table: a `NameConst` operand is a table index."""
    def resolve(i):
        try:
            if i < 0 or i >= len(pkg.names):
                return ""
            return pkg.name(i)
        except Exception:  # noqa: BLE001 -- a name table read must never sink a census
            return ""
    return resolve


def read_dump(system):
    table = DIS.Table()
    stats = {"seen": 0, "walked": 0, "packages": 0}
    census = collections.Counter()
    rows = []
    for package, pkg, exp, body, nodes, spans in JG.payloads(system, table, stats):
        edges, st = JG.measure(body, nodes, spans)
        # A script with no `Case` link at all still walks: a list of one case is the terminator alone (`0xffff`),
        # which is the join's "a switch with no case" row -- skipping those scripts made them vanish from the census.
        links = st.get("case_links") or []
        stmts = [n for n in nodes if n["name"] not in JG.TERMINATORS]
        path = DIS.export_path(pkg, exp)
        starts, _glen = JG.guest_map(spans)
        f2g = {a: at for at, a in starts.items()}
        rows.extend(script_rows(package, path, stmts, links, edges, f2g, census,
                                resolve=namer(pkg)))
    if census["arms"]:
        # One tenth of a statement, so the block quotes an integer rather than a rounded float.
        census["arm_mean_x10"] = int(round(10.0 * census["arm_statements"] / census["arms"]))
    out = ([r for r in rows if r["kind"] == "arm"] + [r for r in rows if r["kind"] == "default"]
           + [r for r in rows if r["kind"] == "list"])
    for key in CENSUS_ORDER:
        out.append({"kind": "census", "note": CENSUS_NOTE[key], "count": census[key]})
    return out, stats


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
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def census_value(rows, key):
    for r in rows:
        if r.get("kind") == "census" and r.get("note") == CENSUS_NOTE[key]:
            return int(r.get("count") or 0)
    return 0


def comma(n):
    return "{:,}".format(int(n))


def distribution(arm_rows):
    """Arm length buckets, counted from the rows so the table cannot drift from the artifact."""
    out = []
    for lo, hi, label in ((0, 0, "0 (shared body)"), (1, 2, "1-2"), (3, 5, "3-5"), (6, 10, "6-10"),
                          (11, 10 ** 9, "11+")):
        out.append((label, len([r for r in arm_rows if lo <= int(r["statements"]) <= hi])))
    return out


def doc_block(rows):
    arms = [r for r in rows if r.get("kind") == "arm"]
    if not arms:
        return "\n(no arm census rows)\n"
    empty = census_value(rows, "empty")
    longest = max(int(r["statements"]) for r in arms)
    longest_rows = [r for r in arms if int(r["statements"]) == longest][:3]
    lines = ["**Every `Case` arm's body runs from its own case token to the next case of its list.** **%s** case "
             "lists hold **%s** testable arms carrying **%s** statements (**%s** to **%s** each, mean **%s**), and "
             "**%s** `default:` bodies carry **%s** more beside the `0xffff` terminator case (**%s** to **%s**)."
             % (comma(census_value(rows, "lists")), comma(census_value(rows, "arms")),
                comma(census_value(rows, "arm_statements")), census_value(rows, "arm_min"),
                census_value(rows, "arm_max"), "%.1f" % (census_value(rows, "arm_mean_x10") / 10.0),
                comma(census_value(rows, "defaults")), comma(census_value(rows, "default_statements")),
                census_value(rows, "default_min"), census_value(rows, "default_max"))]
    lines += ["", "| arm body | arms |", "|---|---:|"]
    for label, n in distribution(arms):
        lines.append("| %s | %s |" % (label, comma(n)))
    lines += ["",
              "**%s** arms carry no body at all -- the source's shared `case A: case B:` body, the same population "
              "`inventory/jump_branches.csv` counts as its `empty_arm` row, so this walk and that one read one shape "
              "to one number." % comma(empty)]
    lines += ["",
              "**How an arm leaves is a measurement, and `break` is not the only way out.** **%s** arms end in a "
              "`Jump` that lands on their list's own end (a `break`), **%s** in a `Jump` that lands elsewhere (a "
              "`continue`, or out of a loop the switch sits in), **%s** whose last `Jump` is a loop's own back edge "
              "-- its target is behind it, so the arm runs on into the next case's code once the loop ends -- and "
              "**%s** in a `Return`. The rest stop with no jump "
              "at all: **%s** run on into the *next* case's code and **%s** are their list's last arm, so they run "
              "into the `default:` body instead. Neither is an arm the next case owns -- the statements after the "
              "next `Case` are that case's own body -- so a decompiler ends the arm where the chain does."
              % (comma(census_value(rows, "end_break")), comma(census_value(rows, "end_elsewhere")),
                 comma(census_value(rows, "end_loop")), comma(census_value(rows, "end_return")),
                 comma(census_value(rows, "end_fallthrough")),
                 comma(census_value(rows, "end_into_default")))]
    if census_value(rows, "return_operand"):
        lines += ["",
                  "**%s** arm(s) needed the return's operand put back: a `Return`'s expression is a token of its own, "
                  "and where that token is a bare variable the statement stream hands it over as the arm's last "
                  "*statement*. The arm ends on the `Return` -- `StreamPlaylistManager.InsertInPlaylist`'s first two "
                  "arms, whose source returns `bResult`, are the whole population."
                  % comma(census_value(rows, "return_operand"))]
    lines += ["",
              "**The switch's end is where its breaks land, and the lists agree on it.** **%s** of **%s** lists put "
              "every `break` of every arm on one offset, so that offset is the switch's own end and it bounds the "
              "`default:` body; **%s** list(s) jump to more than one target and **%s** carry no `break` in their "
              "arms' last token at all. **%s** of those still name an end from a `break` *inside* an arm -- one that "
              "lands past the terminator and is the only such offset -- and the rest stop at the script's end and are "
              "counted rather than assumed."
              % (comma(census_value(rows, "lists_unanimous")), comma(census_value(rows, "lists")),
                 comma(census_value(rows, "lists_split")), comma(census_value(rows, "lists_no_break")),
                 comma(census_value(rows, "lists_interior_end")))]
    lines += ["",
              "**An arm can carry a `break` and not leave by it, and that is what the last-statement column cannot "
              "see.** **%s** arms hold **%s** `Jump`s landing on their list's end *before* their last token "
              "(`if (x) break;` followed by more code) -- the stream half of the text's own interior breaks -- and "
              "**%s** hold **%s** other `Jump`s there, loop edges and a nested switch's own breaks, which is the "
              "control that says the first count is not just \"every jump in a body\"."
              % (comma(census_value(rows, "arm_interior")), comma(census_value(rows, "interior_jumps")),
                 comma(census_value(rows, "arm_interior_other")),
                 comma(census_value(rows, "interior_other_jumps")))]
    lines += ["",
              "**There is no compiler prefix in front of an arm, which is why the extent is the token stream and not a "
              "shape.** **%s** arms open with a `JumpIfNot` and the rest open with whatever the source's first "
              "statement compiles to -- `SetCategoryVolume`'s `if (MusicVolume != NewVolume)` at the head of a case, "
              "`TestSwitch`'s `Assert` at the head of another -- so a decompiler reads the body's first statement "
              "rather than skipping a fixed one." % comma(census_value(rows, "head_if"))]
    kinds = collections.Counter(r["label_kind"] for r in arms)
    lines += ["",
              "**An arm's test value is readable, and it is a literal on %s of %s arms.** The `Case` token's own "
              "value expression is a *child* of the token, so it never appears in the statement stream, but it is "
              "there: **%s** arms carry a `ByteConst`, **%s** a compact integer token, **%s** a `StringConst` and "
              "**%s** a `NameConst`, resolved here through the package's name table (`case 'Music':`); **%s** carry "
              "an expression the compiler emitted instead of a folded value (`InstanceVariable`, `PrimitiveCast`). "
              "That split is `inventory/switch_operands.csv`'s per-switch census read one level down, and it is "
              "what makes the shipped source comparable with a single arm rather than with a class's total."
              % (comma(census_value(rows, "arms") - kinds["expr"]), comma(census_value(rows, "arms")),
                 comma(kinds["byte"]), comma(kinds["int"]), comma(kinds["string"]), comma(kinds["name"]),
                 comma(kinds["expr"]))]
    examples = []
    for kind in ("byte", "int", "string", "name", "expr"):
        got = [r for r in arms if r["label_kind"] == kind]
        if got:
            examples.append((kind, got[0]))
    if examples:
        lines += ["", "| label kind | arms | one arm | case value |", "|---|---:|---|---|"]
        for kind, r in examples:
            shown = r["label"] if kind != "expr" else "`%s`" % r["label"]
            lines.append("| %s | %s | `%s` arm %s | %s |" % (kind, comma(kinds[kind]), r["path"], r["arm"], shown))
    if longest_rows:
        lines += ["", "| longest arm | arm | statements |", "|---|---|---:"]
        for r in longest_rows:
            lines.append("| `%s` | %s at %s | %d |" % (r["path"], r["arm"], r["case"], int(r["statements"])))
    lines += ["",
              "**%s** bodies carry a `Switch` of their own, which is why the extent is the `Case` chain and not the "
              "next `Case` token in the stream: a nested switch's cases are statements of the body they sit in."
              % comma(census_value(rows, "nested"))]
    lines += ["", "The rows are `inventory/switch_arms.csv` (`tools/switch-arms.py`), one per arm, per `default:` "
              "body and per list, with offsets in the widened (guest) code a jump operand is written in; "
              "`--check-doc` re-renders this block from the committed artifact, and `--check` holds its arm and "
              "empty-arm totals to `inventory/switch_join.csv` and `inventory/jump_branches.csv` without the dump."]
    return "\n".join(lines) + "\n"


def splice(path, block):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    lo, hi = DOC_MARK
    if lo not in text or hi not in text:
        raise SystemExit("FAIL switch-arms: markers not found in %s" % path)
    head, rest = text.split(lo, 1)
    _old, tail = rest.split(hi, 1)
    return head + lo + "\n" + block + hi + tail


def write_doc(path, rows):
    # Splice **before** opening for write: `open(path, "w")` truncates the page, and a splice evaluated inside the same
    # statement then reads an empty file and reports its markers missing (measured 2026-11-17, which emptied the page).
    page = splice(path, doc_block(rows))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)


def check_doc(path, rows):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    lo, hi = DOC_MARK
    if lo not in text or hi not in text:
        print("FAIL switch-arms: markers not found in %s" % path)
        return 1
    if text.split(lo, 1)[1].split(hi, 1)[0] != "\n" + doc_block(rows):
        print("FAIL switch-arms: %s is stale -- regenerate with `make switch-arms`" % path)
        return 1
    print("ok   switch-arms: docs block reproduces from inventory/switch_arms.csv")
    return 0


def cross_check(rows):
    """Hold the committed artifact to the two censuses it must agree with, without the dump."""
    arms = [r for r in rows if r.get("kind") == "arm"]
    if not arms:
        print("FAIL switch-arms: inventory/switch_arms.csv has no arm rows")
        return 1
    chain_arms = sum(int(r["chain"]) for r in read_csv(JOIN_CSV) if r.get("chain", "") not in ("", None))
    if len(arms) != chain_arms:
        print("FAIL switch-arms: %d arm rows against the join's %d arms -- the two walks count different lists"
              % (len(arms), chain_arms))
        return 1
    empty = 0
    # The other walk's own wording, read from its module so the two cannot drift apart.
    for r in read_csv(JUMP_CSV):
        if r.get("kind") == "census" and r.get("note") == JB.CENSUS_NOTE["empty_arm"]:
            empty = int(r["count"])
    mine = len([r for r in arms if r["end"] == "empty"])
    if mine != empty:
        print("FAIL switch-arms: %d empty arms here against `jump_branches.csv`'s %d -- the shared-body count is one "
              "shape read twice and must be one number" % (mine, empty))
        return 1
    ends = collections.Counter(r["end"] for r in arms)
    kinds = collections.Counter(r.get("label_kind", "") for r in arms)
    if sum(kinds.values()) != len(arms) or kinds["none"]:
        print("FAIL switch-arms: the label kinds cover %d of %d arms (%r) -- every arm carries a case value"
              % (sum(kinds.values()), len(arms), dict(kinds)))
        return 1
    print("ok   switch-arms: %d arms, %d empty, %d break, %d elsewhere, %d loop, %d return, %d fallthrough, %d into "
          "the default arm, %d carrying an interior break (%d sites) -- matches the join and jump_branches; labels %s"
          % (len(arms), mine, ends["break"], ends["elsewhere"], ends["loop"], ends["return"], ends["fallthrough"],
             ends["into-default"],
             len([r for r in arms if int(r.get("interior_break") or 0)]),
             sum(int(r.get("interior_break") or 0) for r in arms),
             ", ".join("%s %d" % (k, kinds[k]) for k in ("byte", "int", "string", "name", "expr") if kinds[k])))
    return 0


def selftest():
    """A synthetic stream with every shape, so a rule that stops seeing one fails here rather than on the page."""
    def node(offset, name, end):
        return {"offset": offset, "name": name, "operands": b"", "end": end, "children": [], "opcode": 0,
                "native": None}

    # `switch (x) { case A: a; b; break; case B: case C: c; break; default: d; }`
    # Case A at 1 (link 9), Case B at 9 (link 11), Case C at 11 (link 16), terminator at 16.
    stmts = [node(0, "Switch", 1),
             node(1, "Case", 3), node(3, "Let", 4), node(4, "Let", 5), node(5, "Jump", 9),
             node(9, "Case", 11), node(11, "Case", 13), node(13, "Let", 14), node(14, "Jump", 16),
             node(16, "Case", 19), node(19, "Let", 20), node(20, "Jump", 22), node(22, "Return", 23)]
    links = [(1, 9, 9), (9, 11, 11), (11, 16, 16)]
    edges = [(5, "Jump", 4, 22), (14, "Jump", 4, 22), (20, "Jump", 8, 22)]
    heads, census = measure_script(stmts, links, edges)
    got = [(r["arm"], len(r["body"]), r["end"]) for r in heads[0]["arms"]]
    if got != [(1, 3, "break"), (2, 0, "empty"), (3, 2, "break")]:
        print("FAIL switch-arms: the arm walk read %r" % (got,))
        return 1
    if len(heads[0]["default"]["body"]) != 2 or heads[0]["end"] != 22:
        print("FAIL switch-arms: default %r end %r" % (len(heads[0]["default"]["body"]), heads[0]["end"]))
        return 1
    if (census["lists"], census["arms"], census["empty"], census["end_break"], census["nested"],
            census["lists_unanimous"], census["end_into_default"]) != (1, 3, 1, 2, 0, 1, 0):
        print("FAIL switch-arms: census %r" % (dict(census),))
        return 1
    # A `continue` out of an arm: a `Jump` that does not land on the list's end is not a `break`, and a *forward* one
    # is not a loop's back edge either.
    stmts2 = [node(0, "Switch", 1), node(1, "Case", 3), node(3, "Jump", 5), node(5, "Case", 8),
              node(8, "Jump", 10), node(10, "Case", 11), node(11, "Return", 12)]
    heads2, census2 = measure_script(stmts2, [(1, 5, 5), (5, 10, 10)],
                                     [(3, "Jump", 1, 6), (8, "Jump", 2, 10)])
    if ([r["end"] for r in heads2[0]["arms"]] != ["elsewhere", "elsewhere"]
            or census2["end_elsewhere"] != 2 or census2["end_loop"]):
        print("FAIL switch-arms: a continue out of an arm read %r" % ([r["end"] for r in heads2[0]["arms"]],))
        return 1
    # A `for (...)` at the end of an arm: its last token is the loop's back edge, whose target is behind it, so the
    # arm does not leave by that jump -- it runs on into the next case's code once the loop ends.
    stmts5 = [node(0, "Switch", 1), node(1, "Case", 3), node(3, "Let", 4), node(4, "Jump", 9),
              node(9, "Case", 10), node(10, "Jump", 12), node(14, "Case", 15), node(16, "Return", 17)]
    heads5, census5 = measure_script(stmts5, [(1, 9, 9), (9, 14, 14)],
                                     [(4, "Jump", 1, 3), (10, "Jump", 2, 16)])
    if [r["end"] for r in heads5[0]["arms"]] != ["loop", "break"] or census5["end_loop"] != 1:
        print("FAIL switch-arms: a loop's back edge at the end of an arm read %r"
              % ([r["end"] for r in heads5[0]["arms"]],))
        return 1
    # A `Jump` on the list's end before the arm's last token is a `break` the exit column cannot see, and one that
    # lands behind itself there is a loop's edge -- the interior columns' positive and negative cases in one stream.
    stmts7 = [node(0, "Switch", 1), node(1, "Case", 3),
              node(3, "Jump", 4), node(4, "Let", 5), node(5, "Jump", 6), node(6, "Return", 7),
              node(7, "Case", 8), node(8, "Jump", 10),
              node(10, "Case", 11), node(11, "Return", 12)]
    heads7, census7 = measure_script(stmts7, [(1, 7, 7), (7, 10, 10)],
                                     [(3, "Jump", 1, 15), (5, "Jump", 1, 4), (8, "Jump", 1, 15)])
    interior = [(r["end"], r["interior_break"], r["interior_other"]) for r in heads7[0]["arms"]]
    if interior != [("return", 1, 1), ("break", 0, 0)]:
        print("FAIL switch-arms: the interior columns read %r" % (interior,))
        return 1
    if (census7["arm_interior"], census7["interior_jumps"], census7["arm_interior_other"],
            census7["interior_other_jumps"]) != (1, 1, 1, 1):
        print("FAIL switch-arms: the interior census read %r" % (dict(census7),))
        return 1
    # A `Return` whose operand is a bare variable: `dis_parse` hands that operand over as the arm's last statement.
    stmts6 = [node(0, "Switch", 1), node(1, "Case", 5), node(5, "Return", 6), node(6, "LocalVariable", 8),
              node(8, "Case", 10), node(10, "Return", 11)]
    heads6, census6 = measure_script(stmts6, [(1, 8, 8)], [])
    if [r["end"] for r in heads6[0]["arms"]] != ["return"] or census6["return_operand"] != 1:
        print("FAIL switch-arms: a return whose operand is a trailing token read %r"
              % ([r["end"] for r in heads6[0]["arms"]],))
        return 1
    # A body that stops with no jump: a middle arm runs into the next case's code, the last one into the default body.
    stmts4 = [node(0, "Switch", 1), node(1, "Case", 3), node(3, "Let", 4), node(4, "Case", 7),
              node(7, "Let", 8), node(8, "Case", 11), node(11, "Return", 12)]
    heads4, census4 = measure_script(stmts4, [(1, 4, 4), (4, 8, 8)], [])
    if [r["end"] for r in heads4[0]["arms"]] != ["fallthrough", "into-default"]:
        print("FAIL switch-arms: a body that stops with no jump read %r" % ([r["end"] for r in heads4[0]["arms"]],))
        return 1
    # A nested switch inside an arm: the outer arm's end is the chain's next case, not the inner `Case`.
    stmts3 = [node(0, "Switch", 1), node(1, "Case", 3), node(3, "Switch", 4), node(4, "Case", 7),
              node(7, "Let", 8), node(8, "Case", 11), node(11, "Let", 12), node(12, "Case", 15),
              node(15, "Return", 16)]
    heads3, census3 = measure_script(stmts3, [(1, 12, 12), (4, 8, 8)], [])
    if len(heads3) != 2 or [len(r["body"]) for r in heads3[0]["arms"]] != [5] or census3["nested"] != 1:
        print("FAIL switch-arms: a nested switch read %r" % ([len(r["body"]) for r in heads3[0]["arms"]],))
        return 1
    # An arm's case value is the token's own child, and its kind is the operand census's vocabulary.
    def with_label(name, operands):
        return {"offset": 0, "name": "Case", "operands": b"", "end": 1, "opcode": 0,
                "children": [{"name": name, "operands": operands, "children": [], "end": 1, "opcode": 0}]}
    if label_of(with_label("ByteConst", b"\x07")) != ("byte", "7"):
        print("FAIL switch-arms: a byte case value read %r" % (label_of(with_label("ByteConst", b"\x07")),))
        return 1
    if label_of(with_label("IntConstByte", b"\x02")) != ("int", "2"):
        print("FAIL switch-arms: a compact int case value read %r" % (label_of(with_label("IntConstByte", b"\x02")),))
        return 1
    if label_of(with_label("StringConst", b"mp3\x00")) != ("string", "mp3"):
        print("FAIL switch-arms: a string case value read %r" % (label_of(with_label("StringConst", b"mp3\x00")),))
        return 1
    if label_of(with_label("NameConst", b"\x02\x00"), resolve=lambda i: {2: "Music"}.get(i, "")) != ("name", "Music"):
        print("FAIL switch-arms: a name case value read %r" % (label_of(with_label("NameConst", b"\x02\x00")),))
        return 1
    if label_of({"children": []}) != ("none", ""):
        print("FAIL switch-arms: a case token with no value expression is not `none`")
        return 1
    import tempfile
    rows = [{"kind": "arm", "path": "P.C.F", "arm": "1", "case": "0x0001", "statements": "2", "end": "break",
             "label_kind": "byte", "label": "3"},
            {"kind": "arm", "path": "P.C.F", "arm": "2", "case": "0x0005", "statements": "0", "end": "empty",
             "label_kind": "int", "label": "0"}]
    for key in CENSUS_ORDER:
        rows.append({"kind": "census", "note": CENSUS_NOTE[key],
                     "count": 2 if key in ("lists", "arms", "arm_max", "end_break") else 1})
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "rows.csv")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(render(rows))
        if doc_block(read_csv(path)) != doc_block(rows):
            print("FAIL switch-arms: the block does not reproduce from its own CSV")
            return 1
    finally:
        for name in os.listdir(tmp):
            os.unlink(os.path.join(tmp, name))
        os.rmdir(tmp)
    print("ok   switch-arms: an arm's body ends at its list's next case, a shared body and a nested switch are each "
          "named, a `continue` out of an arm is not a `break`, a loop's back edge is not an exit, a `Return`'s "
          "trailing operand is not a statement, the list's end is its breaks' target and the block "
          "reproduces from its CSV")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--system", help="the dump's System directory (or one package)")
    ap.add_argument("-o", "--out", default=CSV_PATH)
    ap.add_argument("--write-doc", metavar="PAGE")
    ap.add_argument("--check-doc", metavar="PAGE")
    ap.add_argument("--report", action="store_true", help="print the census, write nothing")
    ap.add_argument("--check", action="store_true", help="synthetic self-test plus a dump-free cross-check")
    args = ap.parse_args()
    if args.check:
        bad = selftest()
        if bad:
            return bad
        return cross_check(read_csv(args.out)) if os.path.exists(args.out) else 0
    if args.check_doc:
        return check_doc(args.check_doc, read_csv(args.out))
    if not args.system and args.write_doc:
        # The block renders from the artifact alone, so a page correction never needs the dump.
        rows = read_csv(args.out)
        if not rows:
            ap.error("%s does not exist -- a doc render needs the artifact or --system" % args.out)
        write_doc(args.write_doc, rows)
        print("ok   switch-arms: wrote %s from %s" % (args.write_doc, args.out))
        return 0
    if not args.system:
        ap.error("a measurement needs --system (or --check)")
    rows, stats = read_dump(args.system)
    if args.write_doc:
        write_doc(args.write_doc, rows)
    if args.report or not args.write_doc:
        print(doc_block(rows).strip())
    if not args.report:
        with open(args.out, "w", encoding="utf-8", newline="") as fh:
            fh.write(render(rows))
        print("wrote %s (%d rows) -- walked %d of %d payloads" % (args.out, len(rows), stats["walked"],
                                                                stats["seen"]))
    return 0


JG = load_tool("jump_graph", "jump-graph.py")
JB = load_tool("jump_branches", "jump-branches.py")
DIS = JG.DIS

if __name__ == "__main__":
    sys.exit(main())
