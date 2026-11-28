#!/usr/bin/env python3
"""What the jump graph says the build's branches are: `if`, `else`, an empty arm, or a short circuit (PLAN 3.7).

`inventory/jump_graph.csv` resolved every `Jump`/`JumpIfNot` operand to the file offset of the token the runtime
lands on (`tools/jump-graph.py`). This reads the graph back against the statement stream, which is what a decompiler
needs from it: a `JumpIfNot`'s target is the end of its `if` arm, and the token immediately before that target says
whether the arm has an `else` -- an `if`/`else` compiles to `JumpIfNot <else>` + `<then>` + `Jump <end>`, so the
`Jump` that sits at the end of the then-arm is the else test.

Every `JumpIfNot` is one of five things, and the split is a measurement rather than a reading of the source:

* **empty** -- the target is the statement right after the jump, so the arm is empty (`if (x) ;` survives codegen).
* **leave** -- the trailing `Jump` is a `break` or a `continue`: the arm ends by leaving its own loop (or its
  `switch`) rather than by skipping an else body. This is what a loop exit looks like, and the `Jump` that
  reaches for the loop's end is byte-identical in shape to an else skip. Six shapes are recognised, each
  measured on the join rather than by eye: the region the trailing `Jump` skips holds a backward
  `Jump`/`JumpIfNot` whose target begins at or before the condition (a `for`/`while` back edge, and a
  `do…until`'s bottom test, which is conditional); the arm is the trailing `Jump` alone and it lands on a
  backward jump that returns to the condition (`if (X) continue;` -- a `continue` in a `for` lands on the
  increment, not on the back edge); the trailing `Jump` is the arm's last statement and lands on the loop's
  `IteratorPop` (a `foreach`, which has no back edge); the arm's landing follows the case list's terminator
  (`0xffff`) as its previous statement (`if (X) { …; break; }` inside a `switch`, whose `break` leaves the switch
  and reaches no loop test at all); the then-block holds a nested `JumpIfNot` aimed at or past the target it sits
  inside, so the trailing `Jump` is that nested arm's exit and not this one's skip; and the arm's single `Jump`
  lands on a `for`'s increment block, where the statement after the landing is the back edge.
* **else** -- the statement in front of the target is a `Jump` that lands past it, and no loop of the arm's own
  returns from the skipped region: the then-arm skips an else.
* **if** -- everything else: a conditional forward jump over a straight arm.
* **expression** -- the jump (or its target) is not a statement at all, i.e. `&&`/`||` short-circuiting inside a
  condition; those are tokens, not branches, and counting them as arms is what a naive census would report.

**Every resolved token is a row.** A row for each of the five, not only the three that open an arm: an `empty`
arm used to be counted in the census and dropped from the artifact, so a join reading the rows saw a `JumpIfNot`
fewer than the graph resolved -- `text-branches` measured exactly that as **11** `jumps_unresolved` tokens, two of
them the `_drawScorch` pair that compile to `JumpIfNot` + `Return`. Each row also carries the arm's **`span`**: the
number of statements between the jump and its landing (0 for an empty arm, negative for a loop test at the bottom
of its body), so a reader places the arm without walking the stream again.

    python3 tools/jump-branches.py --system DUMP/System -o inventory/jump_branches.csv --write-doc docs/BYTECODE.md

`--check` is a synthetic self-test: it needs no dump and writes nothing.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(ROOT, "inventory", "jump_branches.csv")
DOC_MARK = ("<!-- generated:jump-branches -->", "<!-- /generated:jump-branches -->")
FIELDS = ["kind", "package", "class", "function", "offset", "category", "operand", "target", "span", "count", "note",
          "path"]

CENSUS_NOTE = {
    "jumpifnot": "`JumpIfNot` tokens the graph resolved",
    "else": "with a `Jump` at the end of the then-arm that lands past the target -- an else arm",
    "leave": "whose then-arm ends by leaving its own loop -- a `break` (a `for` back edge or a `foreach` `IteratorPop`)",
    "empty": "whose target is the statement right after the jump -- an empty then-arm (one row each since the join "
             "read them)",
    "if": "a conditional forward jump over a straight arm",
    "expression": "inside a condition (`&&`/`||` short-circuit), i.e. not a branch",
    "span_rows": "branch rows carrying a measured arm span -- every row whose jump and target are both statements",
    "rows": "branch rows written: one per resolved `JumpIfNot`, so this row's count equals the one above",
    "backward": "whose target is behind the jump -- a loop test at the bottom of its body",
    "scripts": "scripts with at least one branch",
    "operator_headers": "operator records whose header names the symbol, not the export name -- the name test the graph applies",
    "operator_jumps": "of those, the scripts carrying at least one jump -- the rest are stub bodies (25 are `native` records)",
    "jump": "`Jump` tokens the graph resolved, for scale",
    "arm_min": "the shortest then-arm, in statements",
    "arm_max": "the longest",
    "switch": "`Switch` tokens",
    "switch_scripts": "scripts carrying one",
    "switch_list": "case lists (a chain of `Case` tokens ending on the `0xffff` terminator)",
    "switch_no_list": "switches whose next statement is not a `Case` -- no list at all",
    "switch_cases": "`Case` tokens inside a list, terminator included",
    "case_outside": "`Case` tokens in no list",
    "empty_arm": "arms whose body is the next case of their list -- a shared `case A: case B:` body",
    "switch_arm_min": "the shortest case list, in cases",
    "switch_arm_max": "the longest",
}


def load_tool(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def classify(nodes, edges):
    """One script's `[(offset, category, operand, target)]` for every `JumpIfNot` the graph resolved.

    `edges` is `tools/jump-graph.py`'s list of `(token offset, op name, operand, target file offset)`.
    """
    stmts = [n for n in nodes if n["name"] not in JG.TERMINATORS]
    starts = [n["offset"] for n in stmts]
    where = {n["offset"]: i for i, n in enumerate(stmts)}
    by_edge = {o: (op, v, t) for o, op, v, t in edges}
    out = []
    for offset, op, value, target in edges:
        if op != "JumpIfNot":
            continue
        if offset not in where or target not in where:
            out.append((offset, "expression", value, target))
            continue
        i = where[offset]
        follow = starts[i + 1] if i + 1 < len(starts) else None
        if follow == target:
            # An empty then-arm. `if (x) ; else y;` is *not* separable here and must not be claimed: with no then-arm
            # there is nothing to jump over, so the stream is identical to `if (!x) y;`.
            out.append((offset, "empty", value, target))
            continue
        prev = stmts[where[target] - 1] if where[target] > 0 else None
        if prev is not None and prev["name"] == "Jump" and prev["offset"] in by_edge and by_edge[prev["offset"]][2] > target:
            # An else skip and a loop `break` compile to the same three tokens (`JumpIfNot`, the then-arm, a `Jump`
            # past the target), so the trailing `Jump` alone cannot tell them apart. What separates them is what the
            # jump skips over: a `break` skips the rest of its own loop, whose back edge returns to a test that
            # begins at or before the condition. A then-arm nested inside a *different* loop (one that starts inside
            # the arm) does not qualify, because its back edge's test is after the condition.
            span = which = where[target]
            rest = len(stmts)
            while which < rest and stmts[which]["offset"] < by_edge[prev["offset"]][2]:
                which += 1
            skipped = stmts[span:which]

            def back(n):
                """A jump that returns to the condition or before it -- this loop's own back edge."""
                return (n["offset"] in by_edge and by_edge[n["offset"]][2] < n["offset"]
                        and by_edge[n["offset"]][2] <= offset)

            # `JumpIfNot` as well as `Jump`: a `do…until` is tested at the bottom and jumps back conditionally, so
            # a `break` out of it skips a body whose only back edge is not a `Jump`. The target test keeps a loop
            # that merely *starts* inside the skipped region out of this (`nodes4`'s second case in `selftest`).
            leaves = any(n["name"] in ("Jump", "JumpIfNot") and back(n) for n in skipped)
            # A `foreach` has no `Jump` back edge -- the engine's `IteratorNext` advances it and the loop leaves
            # through `IteratorPop` -- so a then-arm that ends by landing *on* the `IteratorPop` is the same
            # `break`, one loop kind over. A `foreach` inside an else body ends the arm with its own `IteratorPop`,
            # which is the arm's last statement and not the jump's target, so this does not fire on it.
            if not leaves and which < len(stmts) and stmts[which]["offset"] == by_edge[prev["offset"]][2] \
                    and stmts[which]["name"] == "IteratorPop":
                leaves = True
            # `if (X) continue;` -- the whole then-arm is the one `Jump`, and it lands on the loop's increment:
            # the back edge that returns to the condition is the *next* statement, past the jump's target rather
            # than inside the region it skips. Requiring the arm to be the `Jump` alone is what keeps a real
            # `if`/`else` at the bottom of a loop body (whose trailing `Jump` lands right in front of the same
            # back edge) an `else`.
            if not leaves and follow == prev["offset"] and which < len(stmts) \
                    and stmts[which]["name"] in ("Jump", "JumpIfNot") and back(stmts[which]):
                leaves = True
            # `if (X) { …; break; }` inside a `switch`: the `break` leaves the switch, so there is no loop test
            # to find. What it does leave behind is the case list's terminator -- a `Case` whose operand is
            # `0xffff` -- and the switch's own code begins on the statement right after it, so the terminator is
            # the last statement before the trailing `Jump`'s landing. That adjacency is what separates this from
            # a `switch` *inside* an else body: there the terminator is only one statement among the many the arm
            # skips, so it is not the one the landing follows.
            if not leaves and which > 0 and stmts[which - 1]["name"] == "Case" \
                    and len(stmts[which - 1]["operands"]) == 2 \
                    and (stmts[which - 1]["operands"][0] | (stmts[which - 1]["operands"][1] << 8)) == 0xFFFF:
                leaves = True
            # A then-arm never reaches past its own else. `if (A) { …; if (B) continue; }` writes the inner
            # `continue` as `JumpIfNot <past this arm>` + `Jump <increment>`, so the *last* statement before the
            # outer target is a nested arm's jump and the outer `if` reads as an `else` it does not have. The
            # nesting invariant is what separates them: a real then-block holds no `JumpIfNot` aimed at or past
            # the target it is inside. Measured on the join, this moves one arm and none in an agreeing function.
            if not leaves and any(n["name"] == "JumpIfNot" and n["offset"] in by_edge
                                  and by_edge[n["offset"]][2] >= target for n in stmts[i + 1:span]):
                leaves = True
            # `if (X) continue;` in a `for`: the landing is the increment block itself, not the back edge, so the
            # test that returns to the condition is the statement *after* the landing. The then-arm being the one
            # `Jump` is again what keeps a real `if`/`else` at the bottom of a loop body out of this.
            if not leaves and follow == prev["offset"] and which + 1 < len(stmts) \
                    and stmts[which]["name"] not in ("Jump", "JumpIfNot") \
                    and stmts[which + 1]["name"] in ("Jump", "JumpIfNot") and back(stmts[which + 1]):
                leaves = True
            out.append((offset, "leave" if leaves else "else", value, target))
        else:
            out.append((offset, "if", value, target))
    return out, len(stmts)


def switch_census(stmts, links):
    """`(switches, chains, empty, outside, no_list)` for one script: the case lists and their arms.

    A `Case` token's operand is the guest offset of the next `Case` of its own list (`execSwitch`, `0x0004ca20`),
    so the list is a chain in the token stream and a switch inside a case body is a list of its own (`links` comes
    from `tools/jump-graph.py`, one tuple per link). A chain's last case is the `0xffff` terminator, so a list of
    `n` cases has `n-1` testable arms; an **empty arm** is a case whose body is the next case of its list -- the
    `case A: case B:` shared body. A `Switch` whose next statement is not a `Case` has no list at all, which is a
    shape this build has and the page names.
    """
    offs = [n["offset"] for n in stmts]
    index = {o: i for i, o in enumerate(offs)}
    names = {n["offset"]: n["name"] for n in stmts}
    cases = [o for o in offs if names[o] == "Case"]
    by = {a: b for a, _v, b in links}
    targets = set(by.values())
    heads = [c for c in cases if c not in targets]
    chains, used = [], set()
    for head in heads:
        seq, at = [head], head
        while at in by:
            at = by[at]
            if at in seq:
                break
            seq.append(at)
        chains.append(seq)
        used.update(seq)
    outside = [c for c in cases if c not in used]
    counted = []
    for seq in chains:
        here = 0
        for one, nxt in zip(seq, seq[1:]):
            i = index[one]
            if i + 1 < len(offs) and offs[i + 1] == nxt:
                here += 1
        counted.append((seq, here))
    chains = counted
    empty = sum(c for _s, c in chains)
    switches = [o for o in offs if names[o] == "Switch"]
    no_list = []
    for one in switches:
        i = index[one]
        if i + 1 >= len(offs) or names[offs[i + 1]] != "Case":
            no_list.append(one)
    return switches, chains, empty, outside, no_list


def switch_rows(package, cls, func, path, stmts, links, census):
    """One row per case list (and per switch with no list), and the census it accumulates."""
    switches, chains, empty, outside, no_list = switch_census(stmts, links)
    if not switches:
        return []
    rows = []
    census["switch"] += len(switches)
    census["switch_list"] += len(chains)
    census["switch_no_list"] += len(no_list)
    census["switch_cases"] += sum(len(seq) for seq, _e in chains)
    census["case_outside"] += len(outside)
    census["switch_scripts"] += 1
    if chains:
        census["switch_arm_min"] = min([census.get("switch_arm_min", 99)] + [len(seq) for seq, _e in chains])
        census["switch_arm_max"] = max([census.get("switch_arm_max", 0)] + [len(seq) for seq, _e in chains])
        census["empty_arm"] += empty
    for seq, here in chains:
        rows.append({"kind": "switch", "package": package, "class": cls, "function": func,
                     "offset": "%04x" % seq[0], "category": "", "operand": "", "target": "%04x" % seq[-1],
                     "count": len(seq), "note": "list of %d case(s), %d empty arm(s)" % (len(seq), here),
                     "path": path})
    for one in no_list:
        rows.append({"kind": "switch", "package": package, "class": cls, "function": func,
                     "offset": "%04x" % one, "category": "", "operand": "", "target": "", "count": 0,
                     "note": "no case list", "path": path})
    return rows


def site_rows(package, cls, func, path, verdicts, stmts, census):
    """One row per resolved `JumpIfNot`: the token, its landing, and the arm's span in statements.

    Every verdict becomes a row, `empty` and `expression` included. A verdict with no row is a token no join can
    see, and that is not hypothetical: with only `if`/`else`/`leave` written, `text-branches` measured **11**
    `JumpIfNot` tokens its per-function join could not account for -- the `empty` arms, two of them the
    `_drawScorch` pair (`JumpIfNot` + `Return`, the landing the statement right after the jump).

    `span` is the number of statements between the jump and the statement it lands on: 0 for an empty arm,
    negative for a loop test at the bottom of its body (the landing is behind the jump), blank where the landing is
    not a statement at all.
    """
    where = {n["offset"]: i for i, n in enumerate(stmts)}
    names = {n["offset"]: n["name"] for n in stmts}
    rows = []
    for offset, category, value, target in verdicts:
        span, note = "", ""
        if offset in where and target in where:
            span = where[target] - where[offset] - 1
            census["span_rows"] += 1
            if category == "empty":
                note = "empty arm: the landing is the statement right after the jump (`%s`)" % names[target]
        else:
            note = "no statement: the jump or its target is not one (%s)" % category
        rows.append({"kind": "branch", "package": package, "class": cls, "function": func,
                     "offset": "%04x" % offset, "category": category, "operand": value,
                     "target": "%04x" % target, "span": span, "count": "", "note": note, "path": path})
        census["rows"] += 1
    return rows


def read_dump(system):
    table = JG.DIS.Table()
    stats = {"seen": 0, "walked": 0, "packages": 0}
    census = collections.Counter()
    rows, arms = [], []
    for package, pkg, exp, body, nodes, spans in JG.payloads(system, table, stats):
        edges, _st = JG.measure(body, nodes, spans)
        cls, _state = JG.owner_of(pkg, exp)
        func = pkg.name(exp["name"])
        # The class path, kept identical to `inventory/switch_operands.csv`'s `path` so the two switch censuses join
        # on an equality: `cls` above is the *owning* class, which merges a class's own function with its state's (U20).
        path = JG.DIS.export_path(pkg, exp)
        stmts = [n for n in nodes if n["name"] not in JG.TERMINATORS]
        rows.extend(switch_rows(package, cls, func, path, stmts, _st["case_links"], census))
        if not edges:
            continue
        verdicts, n_stmts = classify(nodes, edges)
        if not verdicts:
            continue
        counts = collections.Counter(v[1] for v in verdicts)
        census["scripts"] += 1
        census["jump"] += len([e for e in edges if e[1] == "Jump"])
        census["jumpifnot"] += len(verdicts)
        census["backward"] += len([v for v in verdicts if v[3] < v[0]])
        for key in ("else", "leave", "empty", "if", "expression"):
            census[key] += counts[key]
        sits = site_rows(package, cls, func, path, verdicts, stmts, census)
        arms.extend(int(r["span"]) for r in sits if str(r["span"]) not in ("", "None"))
        rows.append({"kind": "script", "package": package, "class": cls, "function": func, "offset": "",
                     "path": path, "category": "", "operand": "", "target": "", "count": len(verdicts),
                     "note": "else %d, leave %d, empty %d, if %d, expression %d, %d statement(s)"
                     % (counts["else"], counts["leave"], counts["empty"], counts["if"], counts["expression"],
                        n_stmts)})
        rows.extend(sits)
    if census["rows"] != census["jumpifnot"]:
        # The artifact is the population every join reads, so a verdict without a row is a token lost to the join.
        # This is the gate for it: `text-branches` read the hole as 11 `jumps_unresolved` tokens for three sessions.
        print("FAIL jump-branches: %d branch rows for %d resolved `JumpIfNot` tokens"
              % (census["rows"], census["jumpifnot"]))
        raise SystemExit(1)
    out = ([r for r in rows if r["kind"] == "branch"] + [r for r in rows if r["kind"] == "switch"]
           + [r for r in rows if r["kind"] == "script"])
    census["operator_headers"] = stats.get("operator_headers", 0)
    census["operator_jumps"] = stats.get("operator_jumps", 0)
    for key in ("jumpifnot", "rows", "span_rows", "else", "leave", "empty", "if", "expression", "backward", "scripts",
                "jump", "switch", "switch_scripts", "switch_list", "switch_no_list", "switch_cases", "case_outside",
                "empty_arm", "switch_arm_min", "switch_arm_max", "operator_headers", "operator_jumps"):
        out.append({"kind": "census", "package": "", "class": "", "function": "", "offset": "",
                    "category": "", "operand": "", "target": "", "count": census[key],
                    "note": CENSUS_NOTE.get(key, "")})
    out.append({"kind": "census", "package": "", "class": "", "function": "", "offset": "", "category": "",
                "operand": "", "target": "", "count": min(arms) if arms else 0, "note": CENSUS_NOTE["arm_min"]})
    out.append({"kind": "census", "package": "", "class": "", "function": "", "offset": "", "category": "",
                "operand": "", "target": "", "count": max(arms) if arms else 0, "note": CENSUS_NOTE["arm_max"]})
    return out


def render(rows):
    import io
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
            return int(r["count"])
    return 0


def pct(a, b):
    return (100.0 * a / b) if b else 0.0


def comma(n):
    return "{:,}".format(int(n))


def doc_block(rows):
    total = count(rows, "jumpifnot")
    if not total:
        return "\n(no branch census rows)\n"
    # A list of one case is the terminator alone: a `switch` with no case. The names come from the rows, so the
    # sentence cannot drift from the artifact.
    bare = sorted({"%s.%s" % (r["class"], r["function"]) for r in rows
                   if r.get("kind") == "switch" and str(r.get("count")) == "1"})
    bare_text = (" (`%s`)" % "`, `".join(bare)) if bare else ""
    spans = collections.Counter()
    for r in rows:
        if r.get("kind") == "branch" and str(r.get("span", "")) not in ("", "None"):
            spans[int(r["span"])] += 1
    neg = [n for n in spans if n < 0]
    span_text = ("**The span is a row's own column, so the join places an arm without walking the stream again.** "
                 "**%s** of the **%s** rows carry one: **%s** arms are a single statement, **%s** two and **%s** "
                 "three; **%s** measure **zero** -- an empty then-arm, whose landing is the statement right after "
                 "the jump, which is what the `empty` criterion above names -- and **%s** measure **negative**, a "
                 "loop test at the bottom of its body whose target is behind it (the widest, **%d** statements "
                 "back). The longest forward arm is **%s** statements.\n"
                 % (comma(count(rows, "span_rows")), comma(count(rows, "rows")), comma(spans.get(1, 0)),
                    comma(spans.get(2, 0)), comma(spans.get(3, 0)), comma(spans.get(0, 0)),
                    comma(sum(spans[n] for n in neg)), abs(min(neg)) if neg else 0, comma(count(rows, "arm_max"))))
    return ("\n**%s** `JumpIfNot` tokens resolve to **%s** branches with an `else` arm (**%s%%**), **%s** whose then-arm "
            "ends by leaving its own loop -- a `break` (**%s%%**) -- **%s** "
            "conditional jumps over a straight arm (**%s%%**) and **%s** empty then-arms (**%s%%**). **%s** of them "
            "jump **backward**, i.e. a loop test at the bottom of its body, and **%s** name no statement at all -- a "
            "`&&`/`||` short circuit would be the only way that happens, and this build has none, so a condition's "
            "sub-jumps take the same shape as an `if` and the stream alone cannot separate them. A then-arm is %s to "
            "%s statements long, counted as the distance from the jump to its target -- negative for a loop test, as "
            "expected.\n"
            % (comma(total), comma(count(rows, "else")), "%.2f" % pct(count(rows, "else"), total),
               comma(count(rows, "leave")), "%.2f" % pct(count(rows, "leave"), total),
               comma(count(rows, "if")), "%.2f" % pct(count(rows, "if"), total),
               comma(count(rows, "empty")), "%.2f" % pct(count(rows, "empty"), total),
               comma(count(rows, "backward")), comma(count(rows, "expression")),
               count(rows, "arm_min"), count(rows, "arm_max"))
            + span_text
            + ("**A switch is a chain, and its arms are counted here too.** **%s** `Switch` tokens over **%s**"
               " scripts hold **%s** case lists covering **%s** of the **%s** `Case` tokens (**%s** are in no list),"
               " and a list runs **%s** to **%s** cases -- its last is the `0xffff` terminator, so a list of `n`"
               " carries `n-1` testable arms. **%s** of those arms are **empty**, i.e. their body is the next case of"
               " their list: the source's shared `case A: case B:` body, which a decompiler must render as one arm"
               " with two values rather than as an arm that does nothing. **%s** list(s) carry only the terminator,"
               " i.e. a `switch` with no case at all%s; **%s** `Switch`"
               " tokens are followed by a statement that is not a `Case`, which is this walk's test for a list that is"
               " not there, so no switch in this build is missing one. Both counts are rows of"
               " `inventory/jump_branches.csv`, which is the population that shape needs before it can be called a"
               " compiler defect rather than a reader's."
               % (comma(count(rows, "switch")), comma(count(rows, "switch_scripts")), comma(count(rows, "switch_list")),
                  comma(count(rows, "switch_cases")), comma(count(rows, "switch_cases") + count(rows, "case_outside")),
                  comma(count(rows, "case_outside")), count(rows, "switch_arm_min"), count(rows, "switch_arm_max"),
                  comma(count(rows, "empty_arm")), len(bare), bare_text, comma(count(rows, "switch_no_list")))))


def splice(path, block):
    text = open(path, encoding="utf-8").read()
    lo, hi = DOC_MARK
    if lo not in text or hi not in text:
        raise SystemExit("FAIL jump-branches: markers not found in %s" % path)
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
        print("FAIL jump-branches: markers not found in %s" % path)
        return 1
    if text.split(lo, 1)[1].split(hi, 1)[0] != doc_block(rows):
        print("FAIL jump-branches: %s is stale -- regenerate with `make jump-branches`" % path)
        return 1
    # One row per resolved token, so the artifact is the population every join reads. Read from the rows alone:
    # a verdict the writer drops is a `JumpIfNot` a per-function join cannot see (`text-branches` read that as 11).
    got = len([r for r in rows if r.get("kind") == "branch"])
    if got != count(rows, "rows") or got != count(rows, "jumpifnot"):
        print("FAIL jump-branches: %d branch rows against %d written and %d resolved `JumpIfNot` tokens"
              % (got, count(rows, "rows"), count(rows, "jumpifnot")))
        return 1
    print("ok   jump-branches: docs block reproduces from inventory/jump_branches.csv, and its %s branch rows are"
          " one per resolved `JumpIfNot` token" % comma(got))
    return 0


def selftest():
    """A synthetic script with one of each shape, so a rule that stops seeing a shape fails here."""
    def node(offset, name, end=None):
        return {"offset": offset, "name": name, "operands": b"", "end": end if end is not None else offset + 1,
                "children": [], "opcode": 0, "native": None}

    # `if (x) <then> else <else>`: `JumpIfNot <else>` at 0, the then-arm at 3, `Jump <end>` at 4, else at 6.
    nodes = [node(0, "JumpIfNot", 3), node(3, "Let", 4), node(4, "Jump", 6), node(6, "Let", 7),
             node(7, "Return", 8), node(9, "JumpIfNot", 12)]
    edges = [(0, "JumpIfNot", 6, 6), (4, "Jump", 7, 7), (9, "JumpIfNot", 3, 34)]
    got, n = classify(nodes, edges)
    if n != 6 or got != [(0, "else", 6, 6), (9, "expression", 3, 34)]:
        print("FAIL jump-branches: classify read %r / %d" % (got, n))
        return 1
    # An empty then-arm: the jump's own follow is its target.
    nodes2 = [node(0, "JumpIfNot", 3), node(3, "Return", 4), node(4, "Let", 5)]
    got, _n = classify(nodes2, [(0, "JumpIfNot", 3, 3)])
    if got != [(0, "empty", 3, 3)]:
        print("FAIL jump-branches: an empty arm read as %r" % (got,))
        return 1
    # A conditional jump with no else: the Jump in front of the target lands *at* it, not past it.
    nodes3 = [node(0, "JumpIfNot", 3), node(3, "Let", 4), node(4, "Return", 5)]
    got, _n = classify(nodes3, [(0, "JumpIfNot", 3, 4)])
    if got != [(0, "if", 3, 4)]:
        print("FAIL jump-branches: a bare if read as %r" % (got,))
        return 1
    # A loop `break` reads as `else` on the three tokens alone: a loop test at 0, the `if` at 2, its then-arm at 4,
    # the trailing `Jump 16` at 6, the rest of the loop body at 8..10 and the loop's back edge at 10, which returns
    # to the test at 0 -- *before* the condition -- so the trailing Jump leaves the loop and is a `break`.
    nodes4 = [node(0, "JumpIfNot", 3), node(2, "JumpIfNot", 5), node(4, "Let", 6), node(6, "Jump", 8),
              node(8, "Let", 9), node(10, "Jump", 11), node(16, "Return", 17)]
    edges4 = [(0, "JumpIfNot", 16, 16), (2, "JumpIfNot", 8, 8), (6, "Jump", 16, 16), (10, "Jump", 0, 0)]
    got, _n = classify(nodes4, edges4)
    if got != [(0, "if", 16, 16), (2, "leave", 8, 8)]:
        print("FAIL jump-branches: a loop break read as %r" % (got,))
        return 1
    # The same shape with the back edge returning *into* the arm (test at 9, after the condition at 2) is a loop
    # inside the else body, so the arm is an else.
    got, _n = classify(nodes4, edges4[:3] + [(10, "Jump", 9, 9)])
    if got != [(0, "if", 16, 16), (2, "else", 8, 8)]:
        print("FAIL jump-branches: a loop inside an else arm read as %r" % (got,))
        return 1
    # A `foreach` `break`: no `Jump` back edge at all -- the arm's trailing `Jump` lands on the loop's
    # `IteratorPop`, which is how that loop kind leaves.
    nodes5 = [node(0, "JumpIfNot", 3), node(2, "Let", 3), node(4, "Jump", 5), node(8, "IteratorNext", 9),
              node(10, "JumpIfNot", 11), node(14, "IteratorPop", 15), node(16, "Return", 17)]
    got, _n = classify(nodes5, [(0, "JumpIfNot", 8, 8), (4, "Jump", 14, 14), (10, "JumpIfNot", 2, 2)])
    if got != [(0, "leave", 8, 8), (10, "if", 2, 2)]:
        print("FAIL jump-branches: a foreach break read as %r" % (got,))
        return 1
    # `if (X) continue;`: the then-arm is the one `Jump`, it lands on the loop's increment at 8, and the back edge
    # to the condition at 0 is the *next* statement -- past the jump's target, not inside what it skips.
    nodes6 = [node(0, "JumpIfNot", 2), node(2, "Jump", 3), node(5, "Let", 6), node(8, "Jump", 9),
              node(10, "Return", 11)]
    got, _n = classify(nodes6, [(0, "JumpIfNot", 5, 5), (2, "Jump", 8, 8), (8, "Jump", 0, 0)])
    if got != [(0, "leave", 5, 5)]:
        print("FAIL jump-branches: a `continue` read as %r" % (got,))
        return 1
    # A `do…until` `break`: the loop's only back edge is the bottom test, a `JumpIfNot` to 0 inside the region the
    # trailing `Jump` skips -- a `Jump`-only rule saw an else here.
    nodes7 = [node(0, "JumpIfNot", 2), node(2, "Jump", 3), node(3, "Let", 4), node(4, "Let", 5),
              node(6, "JumpIfNot", 7), node(10, "Return", 11)]
    got, _n = classify(nodes7, [(0, "JumpIfNot", 3, 3), (2, "Jump", 10, 10), (6, "JumpIfNot", 0, 0)])
    if got != [(0, "leave", 3, 3), (6, "if", 0, 0)]:
        print("FAIL jump-branches: a `do…until` break read as %r" % (got,))
        return 1
    # A `switch` arm's `break`: no loop test anywhere, but the case list's `0xffff` terminator at 6 is the last
    # statement before the trailing `Jump 10` landing.
    case = {"offset": 6, "name": "Case", "operands": b"\xff\xff", "end": 7, "children": [], "opcode": 0,
            "native": None}
    nodes8 = [node(0, "JumpIfNot", 2), node(2, "Jump", 3), node(3, "Let", 4), case, node(10, "Return", 11)]
    got, _n = classify(nodes8, [(0, "JumpIfNot", 3, 3), (2, "Jump", 10, 10)])
    if got != [(0, "leave", 3, 3)]:
        print("FAIL jump-branches: a switch arm's break read as %r" % (got,))
        return 1
    # The same arm with the terminator past the landing is an else: that is a switch inside the else body, and the
    # arm skips it along with everything else rather than breaking out of it.
    case2 = dict(case, offset=12)
    nodes9 = nodes8[:3] + [node(10, "Return", 11), case2]
    got, _n = classify(nodes9, [(0, "JumpIfNot", 3, 3), (2, "Jump", 10, 10)])
    if got != [(0, "else", 3, 3)]:
        print("FAIL jump-branches: a switch inside an else body read as %r" % (got,))
        return 1
    # A then-block whose last statement is a nested arm's jump. The inner `JumpIfNot 8` sits inside the outer
    # arm's then-block and aims at the outer target (8), so the outer `if` has no else -- and the inner arm is
    # the `continue` onto the increment at 16, whose next statement is the loop's back edge.
    nodes10 = [node(0, "JumpIfNot", 4), node(4, "Let", 5), node(5, "JumpIfNot", 7), node(7, "Jump", 8),
               node(8, "Let", 9), node(14, "Let", 15), node(16, "AddAdd", 17), node(18, "Jump", 19)]
    edges10 = [(0, "JumpIfNot", 8, 8), (5, "JumpIfNot", 8, 8), (7, "Jump", 16, 16), (18, "Jump", 0, 0)]
    got, _n = classify(nodes10, edges10)
    if got != [(0, "leave", 8, 8), (5, "leave", 8, 8)]:
        print("FAIL jump-branches: a nested continue read as %r" % (got,))
        return 1
    # `if (X) continue;` in a `for`: the then-arm is the one `Jump` and lands on the increment at 12, whose next
    # statement is the back edge -- not on the back edge itself, which is the `while` shape above.
    nodes11 = [node(0, "JumpIfNot", 2), node(2, "Jump", 3), node(5, "Let", 6), node(12, "AddAdd", 13),
               node(14, "Jump", 15), node(20, "Return", 21)]
    got, _n = classify(nodes11, [(0, "JumpIfNot", 5, 5), (2, "Jump", 12, 12), (14, "Jump", 0, 0)])
    if got != [(0, "leave", 5, 5)]:
        print("FAIL jump-branches: a for-increment continue read as %r" % (got,))
        return 1
    # A case list with a shared body, and a switch whose next statement is not a `Case`.
    stmts = [node(0, "Switch", 1), node(1, "Case", 2), node(2, "Case", 3), node(3, "Let", 4),
             node(4, "Case", 5), node(5, "Return", 6), node(6, "Switch", 7), node(7, "Let", 8)]
    links = [(1, 2, 2), (2, 4, 4)]
    sw, chains, empty, outside, no_list = switch_census(stmts, links)
    if (sw, [len(seq) for seq, _e in chains], [e for _s, e in chains], empty, len(outside), no_list)             != ([0, 6], [3], [1], 1, 0, [6]):
        print("FAIL jump-branches: the switch census read %r / %r / %r / %r / %r"
              % (sw, chains, empty, outside, no_list))
        return 1
    # Every resolved token is a row, and the row carries the arm's span -- an empty arm's span is zero and its
    # note names the landing, an `expression` verdict has no statement to measure and still gets its row.
    stmts_ok = [node(0, "JumpIfNot", 1), node(1, "Let", 2), node(2, "Return", 3)]
    cen = collections.Counter()
    got = site_rows("P", "C", "F", "C.F", [(0, "empty", 1, 1), (0, "expression", 1, 99)], stmts_ok, cen)
    if ([r["span"] for r in got] != [0, ""] or cen["rows"] != 2 or cen["span_rows"] != 1
            or "statement right after the jump (`Let`)" not in got[0]["note"]
            or "no statement" not in got[1]["note"]):
        print("FAIL jump-branches: an empty arm's row read as %r" % (got,))
        return 1
    stmts_ok2 = [node(0, "JumpIfNot", 1), node(1, "Let", 2), node(2, "Let", 3), node(3, "Return", 4)]
    got = site_rows("P", "C", "F", "C.F", [(0, "if", 3, 3)], stmts_ok2, collections.Counter())
    if [r["span"] for r in got] != [2]:
        print("FAIL jump-branches: an arm's span read as %r" % (got,))
        return 1
    # The block reproduces from its own CSV.
    import tempfile
    rows = [{"kind": "census", "count": v, "note": CENSUS_NOTE[k]} for k, v in
            (("jumpifnot", 4), ("rows", 4), ("span_rows", 4), ("else", 1), ("leave", 1), ("if", 2), ("empty", 1),
             ("expression", 0), ("backward", 1),
             ("scripts", 1), ("jump", 1), ("arm_min", -3), ("arm_max", 2), ("switch", 1), ("switch_scripts", 1),
             ("switch_list", 1), ("switch_no_list", 0), ("switch_cases", 2), ("case_outside", 0), ("empty_arm", 1),
             ("switch_arm_min", 2), ("switch_arm_max", 2))]
    rows += [{"kind": "branch", "category": "else", "span": 2}, {"kind": "branch", "category": "if", "span": 1},
             {"kind": "branch", "category": "empty", "span": 0}, {"kind": "branch", "category": "if", "span": -3}]
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "rows.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(render(rows))
        if doc_block(read_csv(path)) != doc_block(rows):
            print("FAIL jump-branches: the block does not reproduce from its own CSV")
            return 1
    finally:
        for name in os.listdir(tmp):
            os.unlink(os.path.join(tmp, name))
        os.rmdir(tmp)
    print("ok   jump-branches: else, leave, empty, if and expression each read as themselves, a loop break is not an"
          " else and a loop inside an else arm is, a case list counts its empty arm and a switch with no list is"
          " named, every verdict is a row with its arm's span, and the block reproduces from its CSV")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--system", help="the dump's System directory (or one package)")
    ap.add_argument("-o", "--out", default=CSV_PATH, help="the artifact (default inventory/jump_branches.csv)")
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
        ap.error("a measurement needs --system (or --check)")
    rows = read_dump(args.system)
    if args.write_doc:
        write_doc(args.write_doc, rows)
    if args.report or not args.write_doc:
        print(doc_block(rows).strip())
    if not args.report:
        with open(args.out, "w", encoding="utf-8", newline="") as fh:
            fh.write(render(rows))
        print("wrote %s (%d rows)" % (args.out, len(rows)))
    return 0


JG = load_tool("jump_graph", "jump-graph.py")

if __name__ == "__main__":
    sys.exit(main())
