#!/usr/bin/env python3
"""What each AI state's body does, statement by statement, from the shipped text (PLAN 3.10, docs/systems/AI.md).

`tools/ai-states.py` measures the machine's *shape*: which state declares which label, which function it
overrides, where a literal `GotoState` sends control and how often it sleeps. `docs/systems/AI.md` closed with
the residue that shape leaves -- *"whether a body does what its name says is a per-state read"* -- and named the
three bodies worth reading first: `BotEnemy.ShootFromCover`, `BotEnemy.CoveredAttack` and
`ScriptedController.Scripting`.

This tool performs that read mechanically. It walks one state body's statement tree (blocks, labels, `if`/loop/
`switch` headers, simple statements) and reduces every node to a *kind* plus the identifiers it calls, so a
475-line body becomes an outline a reader can follow in one page without the body itself leaving the machine.

    python3 tools/ai-state-body.py --system DUMP/System -o inventory/ai_state_bodies.csv
    python3 tools/ai-state-body.py --system DUMP/System --outline BotEnemy.ShootFromCover
    python3 tools/ai-state-body.py --system DUMP/System --write-doc docs/systems/AI.md
    python3 tools/ai-state-body.py --check          # synthetic, no dump

**Only counts and identifier names are written.** The outline mode prints to stdout for a human reading; the
committed artifact carries per-state counts and the top callee names -- the same identifier vocabulary
`inventory/ai_states.csv` already publishes -- and never a statement's text. AGENTS.md's rule applies: the
body's code does not enter git.

Columns: `package,class,state,lines,stmts,override_stmts,overrides,labels,ifs,elses,loops,switches,cases,sleeps,
sleep_seconds,gotostates,gotos_label,calls,distinct_calls,waits,moves,anims,assigns,returns,other_blocks,call_top`.

* `stmts` counts the state's own simple statements (calls, assignments, `return`, `break`, `continue`) at any
  depth; `override_stmts` counts the ones inside a `function`/`event` the state overrides, and `overrides` how
  many of those blocks there are. The two are separate because an override is a handler the engine calls, not a
  step the state runs -- folding them together would let a state that only declares `SetEnemy` read as busy.
* `labels` counts jump targets, not actions; `sleep_seconds` sums the literal argument of every `Sleep(...)`,
  while a sleep whose argument is an expression (`Sleep(WaitTime)`) adds to `sleeps` and to the `computed`
  residue the report names, not to the total.
* `calls` counts every call occurrence; `distinct_calls` how many names; `call_top` the eight most frequent as
  `Name:count`.
* `waits` counts the calls a latency idiom uses to hand control back to the world (`Finish*`, `Wait*`);
  `moves` the locomotion calls (`Move*`, `Strafe*`); `anims` the animation calls (`PlayAnim`, `TweenAnim`,
  `LoopAnim`). A body with no `Sleep` and no `Finish`/`Wait` call runs to completion in one tick, which is the
  one property of a state an outline can settle outright.
* `other_blocks` counts compound headers that are none of `if`/`else`/loop/`switch`/function -- the parser's own
  residue, kept so a new construct shows up as a number instead of being silently folded into one of the others.
"""

import argparse
import csv
import importlib.util
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLUMNS = ["package", "class", "state", "lines", "stmts", "override_stmts", "overrides", "labels", "ifs",
           "elses", "loops", "switches", "cases", "sleeps", "sleep_seconds", "gotostates", "gotos_label",
           "calls", "distinct_calls", "waits", "moves", "anims", "assigns", "returns", "other_blocks",
           "call_top"]
DOC_MARK = ("<!-- generated:ai-state-bodies -->", "<!-- /generated:ai-state-bodies -->")

LABEL_AT = re.compile(r"(\w+)[ \t]*:(?!:)")
CALL = re.compile(r"([A-Za-z_]\w*)[ \t]*\(")
KEYWORDS = {"if", "else", "while", "for", "foreach", "do", "switch", "case", "default", "return", "break",
            "continue", "until", "new", "function", "event"}
SLEEP = re.compile(r"(?<![\w.])Sleep[ \t]*\(([^)]*)\)")
NUM = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*$")
WAIT = re.compile(r"^(?:Finish|Wait)\w*$")
MOVE = re.compile(r"^(?:Move|Strafe)\w*$")
ANIM = re.compile(r"^(?:Play|Tween|Loop)(?:Anim|Sound)\w*$")
ASSIGN = re.compile(r"(?<![=!<>+\-*/%])=(?!=)")


def states_module():
    """`ai-states` loaded by path (the module name has a hyphen); its parser is what the graph is built on."""
    path = os.path.join(ROOT, "tools", "ai-states.py")
    spec = importlib.util.spec_from_file_location("sg1_ai_states", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def skip_string(text, i):
    quote = text[i]
    i += 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    return i


def close_of(text, depths, open_at, end):
    """Index of the `}` that closes the `{` at `open_at`, bounded by `end`."""
    level = depths[open_at]
    for i in range(open_at + 1, min(end, len(text))):
        if depths[i] == level and text[i] == "}":
            return i
    return end - 1


def walk(text, depths, start, end):
    """Statement tree of `text[start:end]`: `('label', name)`, `('simple', text)` or `('block', header, kids)`."""
    nodes, i, cur = [], start, None
    while i < end:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        if cur is None:
            hit = LABEL_AT.match(text, i)
            if hit and hit.group(1).lower() != "default":
                nodes.append(("label", hit.group(1)))
                i = hit.end()
                continue
            cur = i
        if c == "'" or c == '"':
            i = skip_string(text, i)
            continue
        if c == "{":
            header = " ".join(text[cur:i].split())
            close = close_of(text, depths, i, end)
            nodes.append(("block", header, walk(text, depths, i + 1, close)))
            i = close + 1
            cur = None
            continue
        if c == "}":
            body = " ".join(text[cur:i].split())
            if body:
                nodes.append(("simple", body))
            i += 1
            cur = None
            continue
        if c == ";":
            nodes.append(("simple", " ".join(text[cur:i].split())))
            i += 1
            cur = None
            continue
        i += 1
    return nodes


def flatten(nodes, depth=0):
    """Depth-first `(depth, kind, text)` triples, so a body can be printed as an outline or counted flat."""
    for node in nodes:
        if node[0] == "label":
            yield depth, "label", node[1]
        elif node[0] == "simple":
            yield depth, "stmt", node[1]
        else:
            yield depth, "block", node[1]
            for item in flatten(node[2], depth + 1):
                yield item


def classify(statement):
    """The statement's callee names, keywords excluded (`if (` and `new X(` are not calls)."""
    return [h.group(1) for h in CALL.finditer(statement) if h.group(1).lower() not in KEYWORDS]


def body_row(package, cls, state, lines, nodes):
    """Reduce one parsed body to a row of counts plus its most frequent callees."""
    acc = dict((key, 0) for key in ("stmts", "override_stmts", "overrides", "labels", "ifs", "elses", "loops",
                                    "switches", "cases", "sleeps", "gotostates", "gotos_label", "calls",
                                    "waits", "moves", "anims", "assigns", "returns", "other_blocks"))
    state_of = {"seconds": 0.0, "computed": 0}
    names = {}

    def take_call(name):
        names[name] = names.get(name, 0) + 1
        acc["calls"] += 1
        if WAIT.match(name):
            acc["waits"] += 1
        elif MOVE.match(name):
            acc["moves"] += 1
        elif ANIM.match(name):
            acc["anims"] += 1

    def visit(items, in_override):
        for node in items:
            if node[0] == "label":
                acc["labels"] += 1
                continue
            if node[0] == "block":
                head = node[1].split("(", 1)[0].strip().lower()
                if head.split(" ", 1)[0] in ("function", "event"):
                    acc["overrides"] += 1
                    visit(node[2], True)
                    continue
                if head == "if":
                    acc["ifs"] += 1
                elif head == "else":
                    acc["elses"] += 1
                    if head.startswith("else if") or " if " in (" " + head + " "):
                        acc["ifs"] += 1
                elif head in ("while", "for", "foreach", "do"):
                    acc["loops"] += 1
                elif head == "switch":
                    acc["switches"] += 1
                else:
                    acc["other_blocks"] += 1
                for name in classify(node[1]):
                    take_call(name)
                visit(node[2], in_override)
                continue
            text = node[1]
            low = text.lower()
            if low.startswith("ignores") or low.startswith("var ") or low.startswith("local "):
                continue  # a declaration, not a step the state runs
            acc["override_stmts" if in_override else "stmts"] += 1
            if low.startswith("case "):
                acc["cases"] += 1
            if low.startswith("return"):
                acc["returns"] += 1
            if low.startswith("break") or low.startswith("continue"):
                continue
            if "gotostate" in low:
                acc["gotostates"] += 1
            if re.search(r"(?<![\w.])Goto[ \t]*\(", text):
                acc["gotos_label"] += 1
            for hit in SLEEP.finditer(text):
                acc["sleeps"] += 1
                arg = NUM.match(hit.group(1))
                if arg:
                    state_of["seconds"] += float(arg.group(1))
                else:
                    state_of["computed"] += 1
            if ASSIGN.search(text):
                acc["assigns"] += 1
            for name in classify(text):
                take_call(name)

    visit(nodes, False)
    top = sorted(names.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
    row = {"package": package, "class": cls, "state": state, "lines": lines,
           "distinct_calls": len(names), "sleep_seconds": round(state_of["seconds"], 3),
           "call_top": " ".join("%s:%d" % (n, c) for n, c in top)}
    row.update(acc)
    return row, state_of["computed"]


def state_spans(text, mod):
    """`(state, parent, auto, body_nodes, lines, masked)` for every state block a class's text declares."""
    if not text:
        return []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    masked = mod.mask_comments(text)
    depths = mod.depth_map(masked)
    out = []
    for hit in mod.STATE_DECL.finditer(masked):
        open_at = hit.end() - 1
        close_at = mod.find_close(masked, depths, open_at)
        nodes = walk(masked, depths, open_at + 1, close_at)
        out.append((hit.group(2), hit.group(3) or "", "yes" if hit.group(1) else "no", nodes,
                    masked[open_at + 1:close_at].count("\n") + 1, masked))
    return out


def package_table(system):
    """{class: (package, export_text)} for the whole controller tree, opening each package once."""
    mod = states_module()
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from ue2pkg import Package  # noqa: E402
    text_of = mod.script_text_reader()
    table = mod.class_table()
    wanted = {}
    for cls in mod.walking_roots(table, ["AIController"]):
        wanted.setdefault(table[cls][1], []).append(cls)
    texts, missing = {}, []
    for pkg_name in sorted(wanted):
        path = os.path.join(system, pkg_name + ".u")
        if not os.path.isfile(path):
            missing.extend(wanted[pkg_name])
            continue
        pkg = Package(path)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        by_name = {}
        for export in pkg.exports:
            if pkg.class_name_of(export) == "Class":
                by_name.setdefault(pkg.name(export["name"]), export)
        for cls in wanted[pkg_name]:
            export = by_name.get(cls)
            text = text_of(pkg, export) if export else None
            if text is None:
                missing.append(cls)
                continue
            texts[cls] = (pkg_name, text)
    return texts, missing


def read_dump(system, want=None):
    """Every state body of the AI controller tree, read from the packages' own `ScriptText`."""
    mod = states_module()
    texts, missing = package_table(system)
    rows, computed = [], 0
    for cls in sorted(texts):
        package, text = texts[cls]
        for state, _parent, _auto, nodes, lines, _masked in state_spans(text, mod):
            if want and cls + "." + state not in want:
                continue
            row, c = body_row(package, cls, state, lines, nodes)
            computed += c
            rows.append(row)
    return sorted(rows, key=lambda r: (r["class"], r["state"])), missing, computed


def outline(package, cls, state, nodes):
    """Print one body as an indented outline: kind, the header it tests on, and the names it calls."""
    print("# %s.%s (%s) -- %d nodes" % (cls, state, package, len(list(flatten(nodes)))))
    for depth, kind, text in flatten(nodes):
        pad = "  " * depth
        if kind == "label":
            print("%s%s:" % (pad, text))
            continue
        names = classify(text)
        if kind == "block":
            print("%s[%s] %s" % (pad, text.split("(", 1)[0].strip().lower(), " ".join(names)))
        else:
            print("%s.- %s" % (pad, " ".join(names) or text[:60]))


def render(rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    for row in rows:
        w.writerow([row[k] for k in COLUMNS])
    return buf.getvalue()


INT_COLUMNS = ("lines", "stmts", "override_stmts", "overrides", "labels", "ifs", "elses", "loops", "switches",
               "cases", "sleeps", "gotostates", "gotos_label", "calls", "distinct_calls", "waits", "moves",
               "anims", "assigns", "returns", "other_blocks")


def read_csv(path):
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            for key in INT_COLUMNS:
                row[key] = int(row[key] or 0)
            row["sleep_seconds"] = float(row["sleep_seconds"] or 0)
            out.append(row)
    return out


def doc_block(rows):
    """The page's generated table: the bodies that carry the most statements, so a reader knows where to look."""
    total = sum(r["stmts"] for r in rows)
    secs = round(sum(r["sleep_seconds"] for r in rows), 1)
    loops = len([r for r in rows if r["loops"]])
    one_tick = len([r for r in rows if not r["sleeps"] and not r["waits"]])
    top = sorted(rows, key=lambda r: (-r["stmts"], r["class"], r["state"]))[:15]
    lines = ["",
             "**%d bodies hold %d simple statements (%d more inside overrides), %d sleeps (%s s of literal wait) "
             "and %d dispatch calls; %d loop, and %d run to completion in one tick with no `Sleep` and no "
             "`Finish`/`Wait` call.**"
             % (len(rows), total, sum(r["override_stmts"] for r in rows), sum(r["sleeps"] for r in rows), secs,
                sum(r["gotostates"] + r["gotos_label"] for r in rows), loops, one_tick),
             "",
             "| class | state | stmts | overrides | if | loop | sleeps | sleep s | calls | `GotoState` | "
             "top callees |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for r in top:
        lines.append("| `%s` | `%s` | %d | %d | %d | %d | %d | %g | %d | %d | %s |"
                     % (r["class"], r["state"], r["stmts"], r["overrides"], r["ifs"] + r["elses"], r["loops"],
                        r["sleeps"], r["sleep_seconds"], r["calls"], r["gotostates"],
                        ", ".join("`%s`" % c for c in r["call_top"].split()[:4]) or "—"))
    return "\n".join(lines)


def replace_block(text, block):
    begin, end = DOC_MARK
    i, j = text.find(begin), text.find(end)
    if i < 0 or j <= i:
        sys.exit("document has no %s ... %s block" % DOC_MARK)
    return text[: i + len(begin)] + block + "\n" + text[j:]


def emit(rows, args):
    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            fh.write(render(rows))
    path = args.write_doc or args.check_doc
    if path:
        with open(path, encoding="utf-8") as fh:
            current = fh.read()
        fresh = replace_block(current, doc_block(rows))
        if args.check_doc:
            if fresh != current:
                print("FAIL %s: the generated body table is stale" % path)
                return 1
            print("ok   ai-state-body-doc: %s matches its rows" % path)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(fresh)
            print("ok   ai-state-body-doc: wrote %s" % path)
    print("ok   ai-state-body: %d body(ies), %d statements, %d sleeps (%g s literal), %d dispatch calls"
          % (len(rows), sum(r["stmts"] for r in rows), sum(r["sleeps"] for r in rows),
             round(sum(r["sleep_seconds"] for r in rows), 3),
             sum(r["gotostates"] + r["gotos_label"] for r in rows)))
    return 0


SAMPLE = """// Classes.StargateGame.DemoBot
class DemoBot extends BotBase;

auto state Guard
{
    ignores SeePlayer, HearNoise;
    Begin:
    if (Enemy == None) {
        Sleep(0.5);
        Goto('Begin');
    } else {
        MoveTo(Enemy.Location);
        FinishRotation();
    }
    function bool SetEnemy(Pawn Other) {
        return false;
    }
    PlayAnim('Fire');
    GotoState('Hunt');
    return;
}

state Hunt extends Guard
{
    Sleep(WaitTime);
    A.B.WaitForLanding(1.5);
}
"""


def check():
    """Synthetic: no dump, nothing written to the tree."""
    mod = states_module()
    spans = state_spans(SAMPLE, mod)
    got = [(s[0], s[2], len(list(flatten(s[3]))), s[4]) for s in spans]
    want = [("Guard", "yes", 13, 17), ("Hunt", "no", 2, 4)]
    if got != want:
        print("FAIL state_spans:\n got  %r\n want %r" % (got, want))
        return 1
    guard = [s for s in spans if s[0] == "Guard"][0]
    row, computed = body_row("StargateGame", "DemoBot", "Guard", guard[4], guard[3])
    expect = {"stmts": 7, "override_stmts": 1, "overrides": 1, "labels": 1, "ifs": 1, "elses": 1, "loops": 0,
              "switches": 0, "cases": 0, "sleeps": 1, "gotostates": 1, "gotos_label": 1, "returns": 2,
              "assigns": 0, "other_blocks": 0, "distinct_calls": 6, "calls": 6, "waits": 1, "moves": 1, "anims": 1}
    for key, value in expect.items():
        if row[key] != value:
            print("FAIL body_row[%s]: got %r want %r (%r)" % (key, row[key], value, row))
            return 1
    if row["sleep_seconds"] != 0.5:
        print("FAIL sleep_seconds: got %r" % (row["sleep_seconds"],))
        return 1
    if computed != 0:
        print("FAIL a literal sleep was counted as computed: %r" % (computed,))
        return 1
    hunt = [s for s in spans if s[0] == "Hunt"][0]
    hrow, hcomputed = body_row("StargateGame", "DemoBot", "Hunt", hunt[4], hunt[3])
    if hrow["sleeps"] != 1 or hcomputed != 1 or hrow["sleep_seconds"] != 0:
        print("FAIL a computed Sleep must add to `computed`, not to the total: %r / %r" % (hrow, hcomputed))
        return 1
    if hrow["waits"] != 1:
        print("FAIL WaitForLanding is not counted as a wait: %r" % (hrow,))
        return 1
    if "GotoState" in dict(kv.split(":") for kv in hrow["call_top"].split()):
        print("FAIL a keyword was reported as a callee: %r" % (hrow["call_top"],))
        return 1
    block = doc_block([row, hrow])
    for needle in ("| `DemoBot` | `Guard` | 7 | 1 | 2 | 0 | 1 |", "**2 bodies hold"):
        if needle not in block:
            print("FAIL doc_block is missing %r:\n%s" % (needle, block))
            return 1
    print("ok   ai-state-body: a body's labels, blocks, statements, overrides, literal and computed sleeps, "
          "calls, waits, moves and anims are read; a comment and a keyword are not counted")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--system", help="the dump's System directory")
    ap.add_argument("--outline", action="append", default=None, metavar="CLASS.STATE",
                    help="print these bodies as outlines (repeatable)")
    ap.add_argument("-o", "--out", help="write the CSV here (default: stdout)")
    ap.add_argument("--write-doc", metavar="PATH", help="regenerate the page's block between its markers")
    ap.add_argument("--check-doc", metavar="PATH", help="fail if the page's block is stale (in `make ci`)")
    ap.add_argument("--from-csv", metavar="PATH", default=os.path.join(ROOT, "inventory", "ai_state_bodies.csv"),
                    help="with --check-doc and no --system: read this artifact instead")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; needs no dump")
    args = ap.parse_args()

    if args.check:
        return check()
    if args.check_doc and not args.system:
        rows = read_csv(args.from_csv)
        if not rows:
            sys.exit("ai-state-body: %s holds no rows" % args.from_csv)
        return emit(rows, args)
    if not args.system:
        sys.exit("ai-state-body: pass --system <DUMP/System>")
    mod = states_module()
    texts, missing = package_table(args.system)
    if missing:
        print("note: no ScriptText for %s" % ", ".join(sorted(missing)), file=sys.stderr)
    if args.outline:
        for want in args.outline:
            cls, _, state = want.partition(".")
            if cls not in texts:
                print("note: %s has no text" % cls, file=sys.stderr)
                continue
            package, text = texts[cls]
            for name, _p, _a, nodes, _lines, _masked in state_spans(text, mod):
                if name == state:
                    outline(package, cls, state, nodes)
        return 0
    rows, computed = [], 0
    for cls in sorted(texts):
        package, text = texts[cls]
        for state, _parent, _auto, nodes, lines, _masked in state_spans(text, mod):
            row, c = body_row(package, cls, state, lines, nodes)
            computed += c
            rows.append(row)
    rows.sort(key=lambda r: (r["class"], r["state"]))
    if computed:
        print("note: %d sleep(s) take a computed argument and add no seconds" % computed, file=sys.stderr)
    return emit(rows, args)


if __name__ == "__main__":
    sys.exit(main())
