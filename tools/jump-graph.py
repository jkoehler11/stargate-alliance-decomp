#!/usr/bin/env python3
"""Where a jump goes, in the layout the runtime uses: the widening model turns every operand into a jump edge (PLAN 3.7).

`tools/jump-census.py` measured that **no file-space reading** of a `Jump`/`JumpIfNot` operand lands on a token
start (absolute 23.11%, unsigned relative 13.69%, signed relative 13.69%, against a base rate of 45.57%), and the
two exec handlers say why: `Jump` (slot 6, `0x00047788..0x00047796`) and `JumpIfNot` (slot 7,
`0x00047837..0x0004783f`) both end `mov eax,[edx+0x48]; add eax,ecx; mov [esi+0xc],eax`, i.e.
`FFrame::Code = Node->Script + operand` against the script pointer of the buffer the loader has **widened**.

The widening is per-slot, but it is the same rule the `--sizefield` ladder already models: every compact **index**
operand is four bytes in the copy, opcodes and fixed operands (a jump's 2-byte operand is one) are unchanged. So a
script's *guest* layout is computable from the file -- file offset `a` sits at `a + <4 - width>` for every index
operand in front of it -- and the model is testable against the corpus without a boot: if the operands are guest
offsets, every one of them lands on the guest-space offset of a token start, against a base rate that is *lower*
than the file's (the widening inflates the denominator).

Measured over the 18 packages: **23,778 of 23,778** operands (100.00%) hit, against a guest base rate of 33.76%
(484,332 token starts over 1,434,446 guest bytes), while the same population read in file space hits 5,495
(23.11%). No operand runs past the script's end, so the graph has no dangling edge. That is what makes the jump
graph usable: each site's target is `file_by_guest[operand]`, the file offset of the token the runtime lands on.

    python3 tools/jump-graph.py --system DUMP/System -o inventory/jump_graph.csv --write-doc docs/BYTECODE.md
    python3 tools/jump-graph.py --system DUMP/System --report

A `Case` token's operand is the same kind of offset and belongs in the same graph: `execSwitch` (`0x0004ca20`) reads
it as `Node->Script + operand`, steps one byte past the `Case` opcode and reads the next word, so the 2-byte operand
is the guest offset of the next `Case` **of that list** (or `0xffff`, the terminator). The artifact carries one
`case` row per link, and reachability walks those links instead of the forward scan the page used to assume; a case
whose operand resolves to no `Case` token keeps the scan and is counted in `case_scanned`.

`--guest RAM --pkg StargateMenus.u` reads the same claim from real copies: `tools/xemu/script-ram.py`'s aligner
places a script in a `pmemsave` image, and the copy's own guest token starts (derived from the *aligned* widths,
not from the model) then say whether the operand is the offset of a token there. It prints to stdout only; no
dump and no artifact is written, and a guest copy is guest data, which never enters git.

`--check` is a synthetic self-test: it needs no dump and writes nothing.
"""
import argparse
import bisect
import collections
import csv
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(ROOT, "inventory", "jump_graph.csv")
DOC_MARK = ("<!-- generated:jump-graph -->", "<!-- /generated:jump-graph -->")
JUMPS = ("Jump", "JumpIfNot")
# A token after which the next token is not necessarily executed: an unconditional jump goes to its operand and a
# return ends the path. `JumpIfNot` is not here -- its fallthrough is the whole point of the instruction.
NO_FALLTHROUGH = ("Return", "Stop", "Jump")
EPILOGUE = bytes([0x04, 0x0B, 0x47])
# An **operator** record's script header names the declared symbol (`==`), while the export carries the composed
# name (`EqualEqual_MapItemMapItem`): the compiler stores the spelling the source used in the code's own header and
# composes the export name from the operand types. So `last == exp["name"]` rejects the two bodies `Engine.MaplistRecord`
# ships; identified by the export and the `Operator` flag (0x1000 in the tail's `FunctionFlags`), they walk like any
# other script. Measured: the 182-byte `==( MapItem, MapItem )` body yields 19 nodes and **6** `JumpIfNot`, the six
# condition terms its text spells (UNKNOWNS U23).
SYMBOL = re.compile(r"^[+\-*/%<>=!&|^@~$]{1,3}$")
FUNC_OPERATOR = 0x1000
# The script's own terminator is a token, and nothing jumps to it: it is the epilogue's last word, not a statement a
# path can reach, so counting it made every script look 10% dead (11,675 of the 15,891 unreachable statements).
TERMINATORS = ("EndOfScript", "EndFunction")
FIELDS = ["kind", "package", "class", "function", "offset", "op", "operand", "target", "key", "count", "note"]

CENSUS_NOTE = {
    "jumps": "`Jump`/`JumpIfNot` tokens whose operand is readable",
    "scripts_with_a_jump": "scripts carrying at least one",
    "guest_hits": "operands that equal the guest-space offset of a token start (the widening model)",
    "file_absolute_hits": "the same operands read as a file-space absolute offset (the control)",
    "at_end": "operands equal to the script's guest length -- a jump to the epilogue",
    "past_end": "operands past the script's guest length -- a dangling edge",
    "misses": "operands that are no token start's guest offset",
    "guest_token_starts": "token starts over the same scripts, in the widened layout",
    "guest_bytes": "bytes of those scripts in the widened layout, the guest base rate's denominator",
    "file_token_starts": "token starts over the same scripts, in the file",
    "file_bytes": "bytes of those scripts in the file, the file base rate's denominator",
    "forward": "edges whose target is past the jump",
    "backward": "edges whose target is at or before the jump -- loop backedges",
    "tokens": "statement tokens in the scripts that carry a jump (an expression is not a flow node)",
    "terminators": "`EndOfScript`/`EndFunction` tokens, left out of the flow nodes because nothing jumps to them",
    "reachable": "of those, tokens a walk from the first token reaches over fallthrough and jump edges",
    "dead": "tokens no walk reaches (code behind a return or a jump with no incoming edge)",
    "dead_runs": "maximal runs of unreachable statements",
    "dead_after_return": "unreachable statements whose run starts directly behind a `Return`",
    "dead_after_stop": "the same behind a `Stop`",
    "dead_after_jump": "the same behind an unconditional `Jump`",
    "dead_after_other": "the same behind any other statement",
    "dead_runs_after_return": "runs behind a `Return`",
    "dead_runs_after_stop": "runs behind a `Stop`",
    "dead_runs_after_jump": "runs behind an unconditional `Jump`",
    "dead_runs_after_other": "runs behind any other statement",
    "dead_singletons": "runs that are one statement long",
    "dead_final": "unreachable statements that are their script's own last statement",
    "dead_return": "unreachable `Return` tokens",
    "dead_jump": "unreachable `Jump` tokens",
    "dead_iteratornext": "unreachable `IteratorNext` tokens",
    "dead_iteratornext_after_jump": "unreachable `IteratorNext` tokens whose predecessor is a `Jump`",
    "dead_return_after_return": "unreachable `Return`s whose own predecessor is another `Return`",
    "dead_scripts": "scripts with at least one unreachable statement",
    "jump_sites": "`Jump` tokens",
    "jumpifnot_sites": "`JumpIfNot` tokens",
    "jump_hits": "`Jump` tokens whose operand lands on a token start's guest offset",
    "jumpifnot_hits": "`JumpIfNot` tokens whose operand lands on a token start's guest offset",
    "payloads_seen": "Function exports with a located epilogue",
    "payloads_walked": "of those, scripts the table walks end to end",
    "packages": "packages walked",
    "cases": "`Case` tokens (the switch's case list entries, including the terminator)",
    "case_terminator": "`Case` tokens whose operand is `0xffff` -- the end of their list",
    "case_linked": "`Case` operands that resolve to the guest offset of a `Case` token -- the next case of the list",
    "case_off_list": "linked cases whose target is not the next `Case` in the stream -- a switch inside a case body",
    "case_not_a_case": "operands that resolve to a token start which is not a `Case`",
    "case_scanned": "cases the operand did not place, read by the forward scan instead",
    "dead_run_iterator_exit": "runs behind a `Jump` that lands on the statement after it -- a loop exit over an"
                              " `IteratorNext`",
    "dead_run_else_skip": "runs behind a `Jump` whose statement is the else arm a `JumpIfNot` lands on -- the"
                          " compiler's skip-the-else jump after a then-arm that jumped away",
    "dead_run_duplicate": "runs behind a `Jump` that targets its predecessor's own target",
    "dead_run_other": "runs behind a `Jump` in neither shape -- the tail a fourth one would land in",
}


def load_tool(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def flat(nodes, out=None):
    out = [] if out is None else out
    for n in nodes:
        out.append(n)
        flat(n["children"], out)
    return out


def guest_map(spans):
    """`(guest starts, guest length)` under the widening model: `{guest offset: file offset}` for token starts.

    The model is the one `tools/xemu/script-ram.py` measured on real copies: an **index** operand is four bytes in
    the guest, every other span keeps its file width, so the guest offset of a file offset is that offset plus the
    bytes the widenings in front of it add.
    """
    starts, at = {}, 0
    for a, b, kind, _op in spans:
        if kind == "opcode":
            starts[at] = a
        at += 4 if kind == "index" else (b - a)
    return starts, at


def owner_of(pkg, exp):
    """The class a `Function` export belongs to, walking a `State` outer to its class."""
    state, cur = "", exp
    for _ in range(8):
        if cur["outer"] <= 0:
            break
        outer = pkg.exports[cur["outer"] - 1]
        kind = pkg.class_name_of(outer)
        if kind == "Class":
            return pkg.name(outer["name"]), state
        if kind == "State" and not state:
            state = pkg.name(outer["name"])
        cur = outer
    return "", state


def payloads(system, table, stats):
    """`(package, pkg, exp, body, nodes, spans)` for every `Function` export that walks end to end."""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from ue2pkg import Package                                   # noqa: PLC0415 - only for a real package
    files = ([os.path.join(system, f) for f in sorted(os.listdir(system)) if f.lower().endswith(".u")]
             if os.path.isdir(system) else [system])
    for one in files:
        pkg = Package(one)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        package = os.path.splitext(os.path.basename(one))[0]
        stats["packages"] += 1
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Function":
                continue
            pay = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            n = len(pay)
            off, last, ok = 0, None, True
            for _ in range(6):
                try:
                    last, off = DIS.read_index(pay, off)
                except IndexError:
                    ok = False
                    break
            if not ok:
                continue
            if n >= 10 and pay[n - 10:n - 7] == EPILOGUE:
                end, at = n - 7, n - 4
            elif n >= 12 and pay[n - 12:n - 9] == EPILOGUE:
                end, at = n - 9, n - 6
            else:
                continue
            if last != exp["name"]:
                # An operator's header names its symbol; the export carries the composed record name. The flag word
                # is the tail's own (`function-flags.py` reads the same one), so the exception cannot widen to a
                # function whose header simply disagrees with its export. Measured: the name test passes **27**
                # records over the eighteen packages -- both compiled `Engine.MaplistRecord` bodies and 25 `native`
                # operator records -- and only one of them carries a jump, so a second count (`operator_jumps`)
                # keeps the record total from reading as a population of walked branches.
                flags = int.from_bytes(pay[at:at + 4], "little")
                if not flags & FUNC_OPERATOR or not SYMBOL.match(pkg.name(last)):
                    continue
                stats["operator_headers"] = stats.get("operator_headers", 0) + 1
            start = off + 16
            if start >= end:
                continue
            stats["seen"] += 1
            body = pay[start:end]
            try:
                nodes = DIS.dis_parse(body, table)
                spans = DIS._spans_with_op(body, table)
            except (DIS.Refused, IndexError, DIS.struct.error):
                continue
            if sum(x["end"] - x["offset"] for x in nodes) != len(body):
                continue
            if last != exp["name"] and any(x["name"] in JUMPS for x in nodes):
                stats["operator_jumps"] = stats.get("operator_jumps", 0) + 1
            stats["walked"] += 1
            yield package, pkg, exp, body, nodes, spans


def reach(body, nodes, edges, starts):
    """`(reachable, dead, runs, seen)` token counts for one script: a walk from the first token over the edges.

    Fallthrough is the next token start after a token's end, and it is suppressed after a return, a stop and an
    unconditional jump. `edges` is `{token offset: target token offset}` and holds the jump sites the model resolved
    **and** each `Case` token's own link to the next case of its list (`case_chain`), so the switch is a measured
    edge here rather than a forward scan: `execSwitch` (`0x0004ca20`) reads the 2-byte operand, and on a mismatch
    does `Code = Node->Script + operand` / `Code++` (past the `Case` opcode) / reads the next word, which is the
    next `Case` token of the same list -- `0xffff` ends the list. A `Case` whose operand resolves to nothing keeps
    the scan as its fallback, and that count is `case_scanned`.
    """
    if not starts:
        return 0, 0
    seen = {starts[0]}
    todo = [starts[0]]
    # The flow nodes are the *statement* tokens, not every nested expression: a jump cannot target inside an
    # expression and fallthrough never lands on one, so counting children as tokens made 77% of the corpus look
    # unreachable when the walk was fine (measured 2026-11-17).
    by_offset = {n["offset"]: n for n in nodes}
    starts_set = set(starts)
    while todo:
        at = todo.pop()
        node = by_offset.get(at)
        if node is None:
            continue
        for nxt in ([edges[at]] if at in edges else []) + ([] if node["name"] in NO_FALLTHROUGH else [None]):
            if nxt is None:
                # `end` is exclusive, so the fallthrough is the start *at* it -- `bisect_right` skipped the token
                # next door and made every other token of a straight run look unreachable (measured 2026-11-17:
                # 111,861 of 484,332 reached, i.e. 77% "dead" with nothing behind a return).
                i = bisect.bisect_left(starts, node["end"])
                if i >= len(starts):
                    continue
                nxt = starts[i]
            if nxt in starts_set and nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    # The dead statements arrive in runs, and what sits in front of a run is the measurement: a run behind a
    # `Return` is a tail the compiler emitted (the implicit return), a run behind an unconditional `Jump` is a
    # block no branch enters. `(start offset, length, predecessor name, reaches the script's end)`.
    flow = sorted(nodes, key=lambda n: n["offset"])
    runs, i = [], 0
    while i < len(flow):
        if flow[i]["offset"] in seen:
            i += 1
            continue
        j = i
        while j + 1 < len(flow) and flow[j + 1]["offset"] not in seen:
            j += 1
        runs.append((flow[i]["offset"], j - i + 1, flow[i - 1]["name"] if i else "",
                     j == len(flow) - 1, flow[i - 1]["offset"] if i else None))
        i = j + 1
    return len(seen), len(starts) - len(seen), runs, seen


def measure(body, nodes, spans):
    """One script's edges and counts: `(edges, stats)`.

    `edges` is a list of `(token offset, op name, operand, target file offset)` -- the jump graph, in file space,
    derived from the guest layout the operands are written in.
    """
    starts, glen = guest_map(spans)
    file_starts = {a for a, b, kind, _op in spans if kind == "opcode"}
    flow = [n for n in nodes if n["name"] not in TERMINATORS]
    statements = sorted(n["offset"] for n in flow)
    stats = collections.Counter(tokens=len(statements), bytes=len(body), guest_starts=len(starts),
                                guest_bytes=glen, file_starts=len(file_starts),
                                terminators=len(nodes) - len(flow))
    edges, resolved = [], {}
    for n in flat(nodes):
        if n["name"] not in JUMPS:
            continue
        raw = n["operands"] or b""
        stats["jumps"] += 1
        stats["jump_sites" if n["name"] == "Jump" else "jumpifnot_sites"] += 1
        if len(raw) < 2:
            stats["misses"] += 1
            continue
        value = int.from_bytes(raw[:2], "little")
        if value in file_starts:
            stats["file_absolute_hits"] += 1
        if glen <= value <= glen + 2:
            stats["at_end"] += 1
            continue
        if value > glen + 2:
            stats["past_end"] += 1
            continue
        target = starts.get(value)
        if target is None:
            stats["misses"] += 1
            continue
        stats["guest_hits"] += 1
        stats["jump_hits" if n["name"] == "Jump" else "jumpifnot_hits"] += 1
        stats["backward" if target <= n["offset"] else "forward"] += 1
        edges.append((n["offset"], n["name"], value, target))
        resolved[n["offset"]] = target
    # The switch's case list: `Case <u16>` is the widened-code offset of the next `Case` of the *same* list, and
    # `0xffff` ends it -- `execSwitch` (`0x0004ca20`) jumps `Code = Node->Script + operand`, steps one byte past the
    # `Case` opcode, reads that word and repeats. So the list is in the payload, a switch inside a case body is a
    # list of its own (its first case is not the stream's next case), and the scan is only the fallback.
    g2f = {at: a for at, a in starts.items()}
    cases = sorted((n for n in flat(nodes) if n["name"] == "Case"), key=lambda n: n["offset"])
    case_starts = {n["offset"] for n in cases}
    hold = {n["offset"] for n in flow}
    links = []
    by_case = {n["offset"]: n for n in cases}
    for i, n in enumerate(cases):
        stats["cases"] += 1
        raw = (n["operands"] or b"")[:2]
        follow = cases[i + 1]["offset"] if i + 1 < len(cases) else None
        if len(raw) == 2:
            value = int.from_bytes(raw, "little")
            if value == 0xFFFF:
                stats["case_terminator"] += 1
                continue
            target = g2f.get(value)
            if target is not None and target in case_starts:
                stats["case_linked"] += 1
                stats["case_off_list"] += int(target != follow)
                links.append((n["offset"], value, target))
                resolved[n["offset"]] = target
                continue
            stats["case_not_a_case"] += 1
        # The fallback: a case whose operand resolves to no `Case` token keeps the page's forward scan, and the
        # count is printed so a corpus that stops resolving cannot hide behind the sentence.
        stats["case_scanned"] += 1
        if follow is not None and follow in hold:
            resolved.setdefault(n["offset"], follow)
    stats["case_links"] = links
    stats["edges"] = len(edges)
    ok, dead, runs, seen = reach(body, flow, resolved, statements)
    stats["reachable"], stats["dead"] = ok, dead
    stats["dead_runs"] = len(runs)
    # A run behind an unconditional `Jump` is the population a decompiler must not print as dead source, and it is
    # two shapes rather than one (measured 2026-11-17): a **loop-exit jump over an `IteratorNext`** -- the exit path
    # leaves the loop before the iterator's step, so the step has no entry and the code resumes at `IteratorPop` --
    # and the **skip-the-else `Jump`** of an `if` whose then-arm already jumped away (`break`, `continue`, `return`),
    # where the branch's own `JumpIfNot` lands on the statement right after the dead jump, i.e. the else arm begins
    # there. The third shape is a dead `Jump` to its predecessor's own target. `dead_run_other` is the tail a fourth
    # shape would land in, and `--check` asserts the four sum to `dead_runs_after_jump`.
    jn_targets = {resolved[n["offset"]] for n in flow
                  if n["name"] == "JumpIfNot" and n["offset"] in resolved}
    by_offset = {n["offset"]: n for n in flow}
    order = [n["offset"] for n in flow]
    where = {n["offset"]: i for i, n in enumerate(flow)}
    detail = []
    for start, length, prev, _at_end, prev_at in runs:
        key = ("after_return" if prev == "Return" else "after_stop" if prev == "Stop"
               else "after_jump" if prev == "Jump" else "after_other")
        stats["dead_" + key] += length
        stats["dead_runs_" + key] += 1
        if key != "after_jump":
            continue
        dead = by_offset[start]
        i = where[start]
        after = order[i + length] if i + length < len(order) else None
        p_to = resolved.get(prev_at)
        d_to = resolved.get(start)
        if dead["name"] == "IteratorNext" and p_to is not None and p_to == after:
            shape = "iterator_exit"
        elif p_to is not None and d_to is not None and p_to == d_to:
            shape = "duplicate"
        elif after is not None and after in jn_targets:
            shape = "else_skip"
        else:
            shape = "other"
        stats["dead_run_" + shape] += 1
        detail.append((start, dead["name"], d_to, p_to, after, shape, prev_at))
    stats["dead_runs_detail"] = detail
    stats["dead_singletons"] = len([r for r in runs if r[1] == 1])
    dead_nodes = [n for n in flow if n["offset"] not in seen]
    last = flow[-1]["offset"] if flow else None
    where = {n["offset"]: i for i, n in enumerate(flow)}
    stats["dead_final"] = len([n for n in dead_nodes if n["offset"] == last])
    stats["dead_return"] = len([n for n in dead_nodes if n["name"] == "Return"])
    stats["dead_jump"] = len([n for n in dead_nodes if n["name"] == "Jump"])
    stats["dead_iteratornext"] = len([n for n in dead_nodes if n["name"] == "IteratorNext"])
    stats["dead_return_after_return"] = len([n for n in dead_nodes if n["name"] == "Return"
                                            and where[n["offset"]] > 0
                                            and flow[where[n["offset"]] - 1]["name"] == "Return"])
    stats["dead_iteratornext_after_jump"] = len([n for n in dead_nodes if n["name"] == "IteratorNext"
                                                and where[n["offset"]] > 0
                                                and flow[where[n["offset"]] - 1]["name"] == "Jump"])
    stats["dead_scripts"] = 1 if dead else 0
    return edges, stats


def read_dump(system):
    table = DIS.Table()
    stats = {"seen": 0, "walked": 0, "packages": 0}
    census = collections.Counter()
    rows = []
    per_pkg = collections.defaultdict(collections.Counter)
    for package, pkg, exp, body, nodes, spans in payloads(system, table, stats):
        edges, st = measure(body, nodes, spans)
        census["tokens"] += st["tokens"]
        census["terminators"] += st["terminators"]
        census["reachable"] += st["reachable"]
        census["dead"] += st["dead"]
        for key in ("cases", "case_terminator", "case_linked", "case_off_list", "case_not_a_case", "case_scanned",
                    "dead_run_iterator_exit", "dead_run_else_skip", "dead_run_duplicate", "dead_run_other"):
            census[key] += st[key]
        for key in ("dead_runs", "dead_after_return", "dead_after_stop", "dead_after_jump", "dead_after_other",
                    "dead_runs_after_return", "dead_runs_after_stop", "dead_runs_after_jump",
                    "dead_runs_after_other", "dead_singletons", "dead_final", "dead_return", "dead_jump",
                    "dead_iteratornext", "dead_return_after_return", "dead_iteratornext_after_jump",
                    "dead_scripts"):
            census[key] += st[key]
        cls, _state = owner_of(pkg, exp)
        func = pkg.name(exp["name"])
        for start, name, d_to, p_to, after, shape, prev_at in st["dead_runs_detail"]:
            rows.append({"kind": "deadrun", "package": package, "class": cls, "function": func,
                         "offset": "%04x" % start, "op": name, "operand": d_to,
                         "target": "%04x" % after if after is not None else "", "key": "", "count": "",
                         "note": "%s, predecessor Jump at %04x -> %s" % (shape, prev_at,
                                                                        "%04x" % p_to if p_to is not None else "?")})
        for offset, value, target in st["case_links"]:
            rows.append({"kind": "case", "package": package, "class": cls, "function": func,
                         "offset": "%04x" % offset, "op": "Case", "operand": value,
                         "target": "%04x" % target, "key": "", "count": "",
                         "note": "next case of the list"})
        if not st["jumps"]:
            continue
        for key in ("jumps", "guest_hits", "file_absolute_hits", "at_end", "past_end", "misses", "forward", "backward",
                    "jump_sites", "jumpifnot_sites", "jump_hits", "jumpifnot_hits"):
            census[key] += st[key]
        census["guest_starts"] += st["guest_starts"]
        census["guest_bytes"] += st["guest_bytes"]
        census["file_starts"] += st["file_starts"]
        census["file_bytes"] += st["bytes"]
        census["scripts_with_a_jump"] += 1
        per_pkg[package]["scripts"] += 1
        per_pkg[package]["jumps"] += st["jumps"]
        per_pkg[package]["dead"] += st["dead"]
        per_pkg[package]["tokens"] += st["tokens"]
        for offset, op, value, target in edges:
            rows.append({"kind": "edge", "package": package, "class": cls, "function": func,
                         "offset": "%04x" % offset, "op": op, "operand": value, "target": "%04x" % target,
                         "key": "", "count": "", "note": "backward" if target <= offset else "forward"})
    census["packages"], census["seen"], census["walked"] = stats["packages"], stats["seen"], stats["walked"]
    out = ([r for r in rows if r["kind"] == "edge"] + [r for r in rows if r["kind"] == "case"]
           + [r for r in rows if r["kind"] == "deadrun"])
    for package in sorted(per_pkg):
        c = per_pkg[package]
        out.append({"kind": "package", "package": package, "class": "", "function": "", "offset": "", "op": "",
                    "operand": "", "target": "", "key": "package", "count": c["jumps"],
                    "note": "%d script(s), %d token(s), %d unreachable" % (c["scripts"], c["tokens"], c["dead"])})
    for key in ("jumps", "scripts_with_a_jump", "guest_hits", "file_absolute_hits", "at_end", "past_end", "misses",
                "cases", "case_terminator", "case_linked", "case_off_list", "case_not_a_case", "case_scanned",
                "dead_run_iterator_exit", "dead_run_else_skip", "dead_run_duplicate", "dead_run_other",
                "guest_token_starts", "guest_bytes", "file_token_starts", "file_bytes", "forward", "backward",
                "tokens", "terminators", "reachable", "dead", "dead_runs", "dead_after_return", "dead_after_stop",
                "dead_after_jump", "dead_after_other", "dead_runs_after_return", "dead_runs_after_stop",
                "dead_runs_after_jump", "dead_runs_after_other", "dead_singletons", "dead_final", "dead_return",
                "dead_jump", "dead_iteratornext", "dead_return_after_return", "dead_iteratornext_after_jump",
                "dead_scripts",
                "jump_sites", "jumpifnot_sites", "jump_hits", "jumpifnot_hits",
                "packages", "payloads_seen", "payloads_walked"):
            name = key
            if key == "guest_token_starts":
                name = "guest_starts"
            elif key == "file_token_starts":
                name = "file_starts"
            elif key == "file_bytes":
                name = "file_bytes"
            elif key == "payloads_seen":
                name = "seen"
            elif key == "payloads_walked":
                name = "walked"
            out.append({"kind": "census", "package": "", "class": "", "function": "", "offset": "", "op": "",
                        "operand": "", "target": "", "key": key, "count": census[name],
                        "note": CENSUS_NOTE.get(key, "")})
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
        if r.get("kind") == "census" and r.get("key") == key:
            return int(r["count"])
    return 0


def pct(a, b):
    return (100.0 * a / b) if b else 0.0


def doc_block(rows):
    jumps = count(rows, "jumps")
    hits = count(rows, "guest_hits")
    fabs = count(rows, "file_absolute_hits")
    gs, gb = count(rows, "guest_token_starts"), count(rows, "guest_bytes")
    fs, fb = count(rows, "file_token_starts"), count(rows, "file_bytes")
    lines = ["",
             "**%s of %s** `Jump`/`JumpIfNot` operands (**%s%%**) equal the **guest-space offset** of a token start"
             " under the widening model, against a guest base rate of **%s%%** (%s of %s widened bytes) and"
             " **%s** (%s%%) for the same population read as a file-space absolute offset -- the operand is the"
             " offset into the buffer the exec handler adds it to, and the compiler wrote it in the layout the"
             " loader restores." % (comma(hits), comma(jumps), "%.2f" % pct(hits, jumps), "%.2f" % pct(gs, gb),
                                    comma(gs), comma(gb), comma(fabs), "%.2f" % pct(fabs, jumps)),
             "",
             "**%s** operands run past the script's guest length and **%s** land on no token start, so the graph has"
             " **%s** edges over **%s** scripts -- **%s** forward and **%s** backward (a loop backedge). A walk from"
             " each script's first statement over those edges and fallthrough reaches **%s of %s** statements"
             " (**%s%%**); the **%s** `EndOfScript`/`EndFunction` terminators are left out of that count -- nothing"
             " jumps to them."
             % (comma(count(rows, "past_end")), comma(count(rows, "misses")), comma(jumps),
                comma(count(rows, "scripts_with_a_jump")), comma(count(rows, "forward")),
                comma(count(rows, "backward")), comma(count(rows, "reachable")), comma(count(rows, "tokens")),
                "%.2f" % pct(count(rows, "reachable"), count(rows, "tokens")),
                comma(count(rows, "terminators"))),
             "",
             "**%s** statements are unreachable, in **%s** runs over **%s** scripts, and the statement in front of a"
             " run says what they are: **%s** follow a `Return` (%s runs), **%s** an unconditional `Jump` (%s runs),"
             " **%s** anything else. **%s** of the runs are a single statement; **%s** statements are their script's"
             " own last one and **%s** of the unreachable `Return`s follow another `Return` -- the compiler's"
             " implicit return after an explicit one. **%s** are `Return` tokens and **%s** `Jump`s; **%s** are an"
             " `IteratorNext`, **%s** of those behind a `Jump` that lands on the instruction past it (a loop exit"
             " over the iterator step)."
             % (comma(count(rows, "dead")), comma(count(rows, "dead_runs")), comma(count(rows, "dead_scripts")),
                comma(count(rows, "dead_after_return")), comma(count(rows, "dead_runs_after_return")),
                comma(count(rows, "dead_after_jump")), comma(count(rows, "dead_runs_after_jump")),
                comma(count(rows, "dead_after_other")), comma(count(rows, "dead_singletons")),
                comma(count(rows, "dead_final")), comma(count(rows, "dead_return_after_return")),
                comma(count(rows, "dead_return")), comma(count(rows, "dead_jump")),
                comma(count(rows, "dead_iteratornext")), comma(count(rows, "dead_iteratornext_after_jump"))),
             ]
    lines += ["",
              "**The %s unreachable statements behind an unconditional `Jump` are two shapes, and the page names"
              " them now.** **%s** are the loop exit's own skip: the `Jump` lands on the statement just after an"
              " `IteratorNext`, so the iterator's step has no entry and the code resumes at `IteratorPop`. **%s** are"
              " the compiler's **skip-the-else** `Jump`: the then-arm ended in a jump away (`break`/`continue`/"
              "`return`), so the `Jump` that would step over the `else` arm is unreachable -- and the branch's own"
              " `JumpIfNot` lands on the statement right after it, i.e. the else arm begins there, in **%s of %s** of"
              " them. **%s** is a dead `Jump` whose target is its predecessor's own target, and **%s** are anything"
              " else. So none of the %s is dead *source*: the first shape is the loop's housekeeping and the second is"
              " the `if`/`else` codegen's, and a decompiler that prints the second as unreachable code is reading its"
              " own arm pair backwards."
              % (comma(count(rows, "dead_after_jump")), comma(count(rows, "dead_run_iterator_exit")),
                 comma(count(rows, "dead_run_else_skip")), comma(count(rows, "dead_run_else_skip")),
                 comma(count(rows, "dead_after_jump") - count(rows, "dead_run_iterator_exit")
                       - count(rows, "dead_run_duplicate") - count(rows, "dead_run_other")),
                 comma(count(rows, "dead_run_duplicate")), comma(count(rows, "dead_run_other")),
                 comma(count(rows, "dead_after_jump")))]
    lines += ["",
              "**%s** `Case` tokens carry a switch's case list, and **%s** of them are a link: the operand is the"
              " **guest-space offset** of the next `Case` of the *same* list, resolved in **%s of %s** (**%s%%**),"
              " and the other **%s** are the `0xffff` terminator. **%s** links skip a case token because the list"
              " belongs to a switch inside a case body, and **%s** case(s) needed the forward scan. The chain is the"
              " handler's own reading -- `execSwitch` (`0x0004ca20`) sets `Code = Node->Script + operand`, steps one"
              " byte past the `Case` opcode and reads the next word -- and it replaces the scan this page used to"
              " assume: every script's dead set is the same size either way (%s statements), so the scan was not"
              " wrong, only unproven."
              % (comma(count(rows, "cases")), comma(count(rows, "cases") - count(rows, "case_terminator")),
                 comma(count(rows, "case_linked")),
                 comma(count(rows, "cases") - count(rows, "case_terminator")),
                 "%.2f" % pct(count(rows, "case_linked"),
                               max(1, count(rows, "cases") - count(rows, "case_terminator"))),
                 comma(count(rows, "case_terminator")), comma(count(rows, "case_off_list")),
                 comma(count(rows, "case_scanned")), comma(count(rows, "dead")))]
    pkgs = [r for r in rows if r.get("kind") == "package"]
    if pkgs:
        lines += ["", "| package | scripts | jumps | tokens | unreachable |", "| --- | ---: | ---: | ---: | ---: |"]
        for r in pkgs:
            parts = r["note"].split(", ")
            script_n = parts[0].split()[0] if parts else "0"
            tok_n = parts[1].split()[0] if len(parts) > 1 else "0"
            dead_n = parts[2].split()[0] if len(parts) > 2 else "0"
            lines.append("| %s | %s | %s | %s | %s |" % (r["package"], script_n, r["count"], tok_n, dead_n))
    lines.append("")
    return "\n".join(lines) + "\n"


def comma(n):
    return "{:,}".format(int(n))


def splice(path, block):
    text = open(path, encoding="utf-8").read()
    lo, hi = DOC_MARK
    if lo not in text or hi not in text:
        raise SystemExit("FAIL jump-graph: markers not found in %s" % path)
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
        print("FAIL jump-graph: markers not found in %s" % path)
        return 1
    want = text.split(lo, 1)[1].split(hi, 1)[0]
    got = doc_block(rows)
    if want != got:
        print("FAIL jump-graph: %s is stale -- regenerate with `make jump-graph`" % path)
        return 1
    print("ok   jump-graph: docs block reproduces from inventory/jump_graph.csv")
    return 0


def guest_report(ram, system, pkgs, limit):
    """Read the claim from real copies: the copy's own aligned guest starts, not the model's."""
    ram_ram = load_tool("script_ram", os.path.join("xemu", "script-ram.py"))
    table = DIS.Table()
    stats = {"seen": 0, "walked": 0, "packages": 0}
    # A package name arrives with or without its `.u` (`the export names itself without one`), so normalise both
    # sides: the first attempt compared `StargateMenus` against `StargateMenus.u` and placed nothing at all.
    wanted = tuple(p.lower().removesuffix(".u") for p in pkgs)
    for package, pkg, exp, body, nodes, spans in payloads(system, table, stats):
        if wanted and package.lower() not in wanted:
            continue
        cls, _state = owner_of(pkg, exp)
        _edges, st = measure(body, nodes, spans)
        if not st["jumps"]:
            continue
        if limit is not None and limit[0] <= 0:
            continue
        at, matched, length, path, complete, rule = ram_ram.copy_best(ram, body, spans)
        if not complete:
            continue
        if limit is not None:
            limit[0] -= 1
        # The copy's own layout: cumulative widths from the alignment, not the model's.
        g, starts = 0, {}
        for a, fw, gw, kind, _op in path:
            if kind == "opcode":
                starts[g] = a
            g += gw
        model_starts, model_len = guest_map(spans)
        hits = file_hits = 0
        case_ops = case_hits = 0
        for n in flat(nodes):
            raw = n["operands"] or b""
            if n["name"] in JUMPS and len(raw) >= 2:
                value = int.from_bytes(raw[:2], "little")
                hits += int(value in starts)
                file_hits += int(value in {x for x in starts.values()})
            # A `Case` operand is the same kind of offset (`execSwitch` reads `Node->Script + operand`), so the
            # copy's *own* aligned starts must hold it too -- and a list's terminator is `0xffff`, not an offset.
            if n["name"] == "Case" and len(raw) >= 2:
                value = int.from_bytes(raw[:2], "little")
                if value != 0xFFFF:
                    case_ops += 1
                    case_hits += int(value in starts)
        print("  %s %s.%s: guest copy %d bytes (model %d), %d token start(s), jump operand in the copy's own"
              " starts %d, case link in them %d of %d, model layout agrees %s"
              % (package, cls, pkg.name(exp["name"]), g, model_len, len(starts), hits, case_hits, case_ops,
                 "yes" if sorted(starts) == sorted(model_starts) else "no (%d vs %d)"
                 % (len(starts), len(model_starts))))
    return 0


def selftest():
    """A synthetic script whose two readings disagree: the guest offset is not the file offset."""
    spans = [(0, 1, "opcode", 0x06), (1, 3, "fixed", 0x06), (3, 4, "index", 0x0F), (4, 5, "opcode", 0x04)]
    starts, glen = guest_map(spans)
    if starts != {0: 0, 7: 4} or glen != 8:
        print("FAIL jump-graph: the widening model read %r / %d, expected {0: 0, 7: 4} / 8" % (starts, glen))
        return 1
    body = bytes([0x06, 0x07, 0x00, 0x00, 0x04])

    def node(offset, name, operands=b"", end=0):
        return {"offset": offset, "name": name, "operands": operands, "end": end or offset + 1, "children": [],
                "opcode": 0, "native": None}

    good = [node(0, "Jump", bytes([7, 0]), 3), node(4, "Return", b"", 5)]
    edges, st = measure(body, good, spans)
    if st["guest_hits"] != 1 or st["file_absolute_hits"] or edges != [(0, "Jump", 7, 4)]:
        print("FAIL jump-graph: a guest-offset operand read as %r / %r" % (edges, dict(st)))
        return 1
    bad = [node(0, "Jump", bytes([4, 0]), 3), node(4, "Return", b"", 5)]
    _edges, st = measure(body, bad, spans)
    if st["guest_hits"] or not st["file_absolute_hits"]:
        print("FAIL jump-graph: a file-offset operand also read as a guest hit (%r)" % (dict(st),))
        return 1
    _edges, st = measure(body, [node(0, "Jump", bytes([8, 0]), 3), node(4, "Return", b"", 5)], spans)
    if st["at_end"] != 1 or st["guest_hits"]:
        print("FAIL jump-graph: an operand equal to the guest length read as %r" % (dict(st),))
        return 1
    # Reachability: the token behind a jump with no incoming edge is dead, and the target is not.
    spans2 = [(0, 1, "opcode", 0x06), (1, 3, "fixed", 0x06), (3, 4, "opcode", 0x04), (4, 5, "opcode", 0x04)]
    starts2, _glen2 = guest_map(spans2)
    if starts2 != {0: 0, 3: 3, 4: 4}:
        print("FAIL jump-graph: the second synthetic model read %r" % (starts2,))
        return 1
    nodes2 = [node(0, "Jump", bytes([3, 0]), 3), node(3, "Return", b"", 4), node(4, "Return", b"", 5)]
    body2 = bytes([0x06, 0x03, 0x00, 0x04, 0x04])
    _edges, st = measure(body2, nodes2, spans2)
    if st["reachable"] != 2 or st["dead"] != 1:
        print("FAIL jump-graph: reachability read %d/%d, expected 2 reachable and 1 dead"
              % (st["reachable"], st["dead"]))
        return 1
    # The two named shapes of a run behind a `Jump`. An `IteratorNext` the jump lands past is a loop exit ...
    spans5 = [(0, 1, "opcode", 0x06), (1, 3, "fixed", 0x06), (3, 4, "opcode", 0x15),
              (4, 5, "opcode", 0x15), (5, 6, "opcode", 0x15), (6, 7, "opcode", 0x16)]
    nodes5 = [node(0, "Jump", bytes([6, 0]), 3), node(3, "IteratorNext", b"", 4), node(6, "IteratorPop", b"", 7)]
    _e, st = measure(bytes([0x06, 0x06, 0x00, 0x15, 0x15, 0x15, 0x16]), nodes5, spans5)
    if st["dead_run_iterator_exit"] != 1 or st["dead_runs_after_jump"] != 1:
        print("FAIL jump-graph: a loop exit over an IteratorNext read %r" % (dict(st),))
        return 1
    # ... and a `Jump` the branch's own `JumpIfNot` lands past is the skip-the-else instruction.
    spans6 = [(0, 1, "opcode", 0x07), (1, 3, "fixed", 0x07), (3, 4, "opcode", 0x06), (4, 6, "fixed", 0x06),
              (6, 7, "opcode", 0x06), (7, 9, "fixed", 0x06), (9, 10, "opcode", 0x0F), (10, 11, "opcode", 0x04)]
    nodes6 = [node(0, "JumpIfNot", bytes([9, 0]), 3), node(3, "Jump", bytes([13, 0]), 4),
              node(6, "Jump", bytes([11, 0]), 7), node(9, "Let", b"", 10), node(11, "Return", b"", 12)]
    body6 = bytes([0x07, 9, 0, 0x06, 13, 0, 0x06, 11, 0, 0x0F, 0x04])
    _e, st = measure(body6, nodes6, spans6)
    if st["dead_run_else_skip"] != 1 or st["dead_runs_after_jump"] != 1 or st["dead_run_duplicate"]:
        print("FAIL jump-graph: a skip-the-else jump read %r" % (dict(st),))
        return 1
    for start, name, _d, _p, _after, shape, _prev in st["dead_runs_detail"]:
        if name != "Jump" or shape != "else_skip":
            print("FAIL jump-graph: the dead run read as %r" % (st["dead_runs_detail"],))
            return 1
    # The case chain: `Case <u16>` links to the next `Case` of its own list and `0xffff` ends it. Guest offsets are
    # the file offsets here because the spans carry no widened operand.
    spans3 = [(0, 1, "opcode", 0x0A), (1, 3, "case", 0x0A), (3, 4, "opcode", 0x0A),
              (4, 6, "case", 0x0A), (6, 7, "opcode", 0x04)]
    body3 = bytes([0x0A, 0x03, 0x00, 0x0A, 0xFF, 0xFF, 0x04])
    nodes3 = [node(0, "Case", bytes([3, 0]), 3), node(3, "Case", bytes([0xFF, 0xFF]), 6),
              node(6, "Return", b"", 7)]
    _e, st = measure(body3, nodes3, spans3)
    if st["cases"] != 2 or st["case_linked"] != 1 or st["case_terminator"] != 1 or st["case_scanned"] \
            or st["case_links"] != [(0, 3, 3)]:
        print("FAIL jump-graph: the case chain read %r" % (dict(st),))
        return 1
    # A case whose operand names no `Case` falls back to the scan and says so.
    nodes4 = [node(0, "Case", bytes([7, 0]), 3), node(3, "Case", bytes([0xFF, 0xFF]), 6),
              node(6, "Return", b"", 7)]
    _e, st = measure(bytes([0x0A, 0x07, 0x00, 0x0A, 0xFF, 0xFF, 0x04]), nodes4, spans3)
    if st["case_linked"] or st["case_scanned"] != 1:
        print("FAIL jump-graph: an unresolvable case operand read %r" % (dict(st),))
        return 1
    # The artifact round trip: a CSV of these rows renders the block the run would have written.
    rows = [{"kind": "census", "key": k, "count": v} for k, v in
            (("jumps", 1), ("guest_hits", 1), ("file_absolute_hits", 0), ("guest_token_starts", 2),
             ("guest_bytes", 8), ("file_token_starts", 2), ("file_bytes", 5), ("past_end", 0), ("misses", 0),
             ("scripts_with_a_jump", 1), ("forward", 1), ("backward", 0), ("reachable", 2), ("tokens", 2),
             ("dead", 0), ("cases", 2), ("case_terminator", 1), ("case_linked", 1), ("case_off_list", 0),
             ("case_scanned", 0), ("dead_run_iterator_exit", 0), ("dead_run_else_skip", 0),
             ("dead_run_duplicate", 0), ("dead_run_other", 0))]
    import tempfile
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "rows.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(render(rows))
        if doc_block(read_csv(path)) != doc_block(rows):
            print("FAIL jump-graph: the block does not reproduce from its own CSV")
            return 1
    finally:
        for name in os.listdir(tmp):
            os.unlink(os.path.join(tmp, name))
        os.rmdir(tmp)
    print("ok   jump-graph: the guest offset and the file offset disagree on the same script, the copy's length is"
          " the model's, reachability finds the dead tail, a loop exit and a skip-the-else jump are told apart, the"
          " case chain links and terminates, and the block"
          " reproduces from its CSV")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--system", help="the dump's System directory (or one package)")
    ap.add_argument("-o", "--out", default=CSV_PATH, help="the artifact (default inventory/jump_graph.csv)")
    ap.add_argument("--write-doc", metavar="PAGE", help="render the generated block into this page")
    ap.add_argument("--check-doc", metavar="PAGE", help="fail when the page's block is stale")
    ap.add_argument("--report", action="store_true", help="print the census, no write")
    ap.add_argument("--guest", metavar="RAM", help="a `pmemsave` image, to read the claim from real copies")
    ap.add_argument("--pkg", action="append", default=[], help="with --guest: only this package (repeatable)")
    ap.add_argument("--limit", type=int, default=8, help="with --guest: how many copies to place (default 8)")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; needs no dump and writes nothing")
    args = ap.parse_args()

    if args.check:
        return selftest()
    if args.check_doc:
        return check_doc(args.check_doc, read_csv(args.out))
    if args.guest:
        if not args.system:
            ap.error("--guest needs --system")
        return guest_report(open(args.guest, "rb").read(), args.system, tuple(args.pkg), [args.limit])
    if not args.system:
        ap.error("a measurement needs --system (or --check)")
    rows = read_dump(args.system)
    if args.write_doc:
        write_doc(args.write_doc, rows)
    if args.report or not args.write_doc:
        for r in rows:
            if r["kind"] == "census":
                print("%-26s %s  %s" % (r["key"], r["count"], r["note"]))
        for r in rows:
            if r["kind"] == "package":
                print("%-24s %s" % (r["package"], r["note"]))
    if not args.report:
        with open(args.out, "w", encoding="utf-8", newline="") as fh:
            fh.write(render(rows))
        print("wrote %s (%d rows)" % (args.out, len(rows)))
    return 0


DIS = None


def _boot():
    global DIS
    DIS = load_tool("uscript_dis", "uscript-dis.py")


_boot()

if __name__ == "__main__":
    sys.exit(main())
