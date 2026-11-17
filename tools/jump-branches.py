#!/usr/bin/env python3
"""What the jump graph says the build's branches are: `if`, `else`, an empty arm, or a short circuit (PLAN 3.7).

`inventory/jump_graph.csv` resolved every `Jump`/`JumpIfNot` operand to the file offset of the token the runtime
lands on (`tools/jump-graph.py`). This reads the graph back against the statement stream, which is what a decompiler
needs from it: a `JumpIfNot`'s target is the end of its `if` arm, and the token immediately before that target says
whether the arm has an `else` -- an `if`/`else` compiles to `JumpIfNot <else>` + `<then>` + `Jump <end>`, so the
`Jump` that sits at the end of the then-arm is the else test.

Every `JumpIfNot` is one of four things, and the split is a measurement rather than a reading of the source:

* **empty** -- the target is the statement right after the jump, so the arm is empty (`if (x) ;` survives codegen).
* **else** -- the statement in front of the target is a `Jump` that lands past it: the then-arm skips an else.
* **if** -- everything else: a conditional forward jump over a straight arm.
* **expression** -- the jump (or its target) is not a statement at all, i.e. `&&`/`||` short-circuiting inside a
  condition; those are tokens, not branches, and counting them as arms is what a naive census would report.

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
FIELDS = ["kind", "package", "class", "function", "offset", "category", "operand", "target", "count", "note"]

CENSUS_NOTE = {
    "jumpifnot": "`JumpIfNot` tokens the graph resolved",
    "else": "with a `Jump` at the end of the then-arm that lands past the target -- an else arm",
    "empty": "whose target is the statement right after the jump -- an empty then-arm",
    "if": "a conditional forward jump over a straight arm",
    "expression": "inside a condition (`&&`/`||` short-circuit), i.e. not a branch",
    "backward": "whose target is behind the jump -- a loop test at the bottom of its body",
    "scripts": "scripts with at least one branch",
    "jump": "`Jump` tokens the graph resolved, for scale",
    "arm_min": "the shortest then-arm, in statements",
    "arm_max": "the longest",
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
            out.append((offset, "else", value, target))
        else:
            out.append((offset, "if", value, target))
    return out, len(stmts)


def read_dump(system):
    table = JG.DIS.Table()
    stats = {"seen": 0, "walked": 0, "packages": 0}
    census = collections.Counter()
    rows, arms = [], []
    for package, pkg, exp, body, nodes, spans in JG.payloads(system, table, stats):
        edges, _st = JG.measure(body, nodes, spans)
        if not edges:
            continue
        verdicts, n_stmts = classify(nodes, edges)
        if not verdicts:
            continue
        cls, _state = JG.owner_of(pkg, exp)
        func = pkg.name(exp["name"])
        counts = collections.Counter(v[1] for v in verdicts)
        census["scripts"] += 1
        census["jump"] += len([e for e in edges if e[1] == "Jump"])
        census["jumpifnot"] += len(verdicts)
        census["backward"] += len([v for v in verdicts if v[3] < v[0]])
        for key in ("else", "empty", "if", "expression"):
            census[key] += counts[key]
        where = {n["offset"]: i for i, n in enumerate([n for n in nodes if n["name"] not in JG.TERMINATORS])}
        for offset, category, value, target in verdicts:
            if category == "expression":
                continue
            arms.append(where[target] - where[offset] - 1)
        rows.append({"kind": "script", "package": package, "class": cls, "function": func, "offset": "",
                     "category": "", "operand": "", "target": "", "count": len(verdicts),
                     "note": "else %d, empty %d, if %d, expression %d, %d statement(s)"
                     % (counts["else"], counts["empty"], counts["if"], counts["expression"], n_stmts)})
        for offset, category, value, target in verdicts:
            if category in ("if", "else"):
                rows.append({"kind": "branch", "package": package, "class": cls, "function": func,
                             "offset": "%04x" % offset, "category": category, "operand": value,
                             "target": "%04x" % target, "count": "", "note": ""})
    out = [r for r in rows if r["kind"] == "branch"] + [r for r in rows if r["kind"] == "script"]
    for key in ("jumpifnot", "else", "empty", "if", "expression", "backward", "scripts", "jump"):
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
    return ("\n**%s** `JumpIfNot` tokens resolve to **%s** branches with an `else` arm (**%s%%**), **%s** "
            "conditional jumps over a straight arm (**%s%%**) and **%s** empty then-arms (**%s%%**). **%s** of them "
            "jump **backward**, i.e. a loop test at the bottom of its body, and **%s** name no statement at all -- a "
            "`&&`/`||` short circuit would be the only way that happens, and this build has none, so a condition's "
            "sub-jumps take the same shape as an `if` and the stream alone cannot separate them. A then-arm is %s to "
            "%s statements long, counted as the distance from the jump to its target -- negative for a loop test, as "
            "expected.\n"
            % (comma(total), comma(count(rows, "else")), "%.2f" % pct(count(rows, "else"), total),
               comma(count(rows, "if")), "%.2f" % pct(count(rows, "if"), total),
               comma(count(rows, "empty")), "%.2f" % pct(count(rows, "empty"), total),
               comma(count(rows, "backward")), comma(count(rows, "expression")),
               count(rows, "arm_min"), count(rows, "arm_max")))


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
    print("ok   jump-branches: docs block reproduces from inventory/jump_branches.csv")
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
    # The block reproduces from its own CSV.
    import tempfile
    rows = [{"kind": "census", "count": v, "note": CENSUS_NOTE[k]} for k, v in
            (("jumpifnot", 4), ("else", 1), ("if", 1), ("empty", 1), ("expression", 0), ("backward", 1),
             ("scripts", 1), ("jump", 1), ("arm_min", 1), ("arm_max", 2))]
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
    print("ok   jump-branches: else, empty, if and expression each read as themselves, and the block reproduces from"
          " its CSV")
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
