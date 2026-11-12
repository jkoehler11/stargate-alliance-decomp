#!/usr/bin/env python3
"""What each AI state does, read from the class's own shipped text (PLAN 3.10, docs/systems/AI.md).

`docs/systems/AI.md` closed with a negative: *"What any state does. 165 states is a size, not a behaviour; the
state bodies are the script decompilation under `$WORK`, which this repository does not carry."* The premise is
wrong in one direction: every class ships its own `ScriptText` -- the original UnrealScript source, as a
`TextBuffer` export (`tools/pkg-script-text.py`) -- and a `state` block is part of that text. So the state bodies
are in the packages, not only in an extracted tree, and a state machine can be read without a boot, a decompiler
or a compiler.

    python3 tools/ai-states.py --system DUMP/System -o inventory/ai_states.csv
    python3 tools/ai-states.py --system DUMP/System --write-doc docs/systems/AI.md
    python3 tools/ai-states.py --check                      # synthetic, no dump

Columns: `package,class,state,parent,auto,ignores,labels,functions,gotos,gotos_label,sleeps,lines,targets,record_states`.

* `parent` is the state's `extends` clause, empty for a plain `state Foo {`.
* `ignores` is the count of names in the state's `ignores` list (a length, not the names: the list is behaviour,
  and one state's list is 30 entries long).
* `labels` counts the labels the state's own body declares at statement level -- the targets a `Goto` can name.
* `functions` counts the functions the state body overrides.
* `gotos` counts the `GotoState('name')` calls in the body and `gotos_dynamic` the ones whose target is an
  expression rather than a literal (`GotoState(Order.State)`) -- the second kind is why a state can carry no
  literal edge and still be part of the machine. `gotos_label` counts `Goto('label')` jumps, and `sleeps`
  counts latent `Sleep(...)` calls, which is what a state uses to wait for the world.
* `targets` is the distinct `GotoState` targets, space separated and sorted, so the whole tree's transition graph
  is a read off one column. **A target that is not a state of this class is kept as written**: a controller may
  hand control to a state it inherits, and dropping those would invent a smaller machine than the game runs.
* `record_states` is the class's own state count from `inventory/classes.csv` (the class record), repeated on every
  row of that class so `--check-doc` can compare the two counts from the artifact alone. A class that declares
  states in text but records none, or the reverse, is a disagreement the report names rather than smooths.

A comment is not behaviour: `// GotoState('Foo')` is masked before anything is counted, and the masking keeps
every offset, so the depth map that decides what is statement level is the same one the matches are read at.
"""

import argparse
import csv
import csv as _csv
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLUMNS = ["package", "class", "state", "parent", "auto", "ignores", "labels", "functions",
           "gotos", "gotos_dynamic", "gotos_label", "sleeps", "lines", "targets", "class_gotos",
           "class_targets", "record_states"]
DOC_MARK = ("<!-- generated:ai-states -->", "<!-- /generated:ai-states -->")

STATE_DECL = re.compile(r"(?m)^[ \t]*(auto[ \t]+)?state[ \t]*(?:\([ \t]*\d+[ \t]*\))?[ \t]*"
                        r"(\w+)(?:[ \t]+extends[ \t]+(\w+))?\s*\{")
FUNC = re.compile(r"(?m)^[ \t]*(?:(?:simulated|static|final|latent|event)[ \t]+)*"
                  r"(?:function|event)[ \t]+(?:[\w<>\[\]]+[ \t]+)?(\w+)[ \t]*\(")
IGNORES = re.compile(r"(?m)^[ \t]*ignores[ \t]+([\w \t,]+);")
LABEL = re.compile(r"(?m)^[ \t]*(\w+)[ \t]*:")
GOTO_STATE = re.compile(r"GotoState[ \t]*\([ \t]*'([^']*)'[ \t]*(?:,[ \t]*'([^']*)'[ \t]*)?\)")
GOTO_ANY = re.compile(r"GotoState[ \t]*\(")
GOTO_LABEL = re.compile(r"(?<![\w.])Goto[ \t]*\([ \t]*'([^']*)'[ \t]*\)")
SLEEP = re.compile(r"(?<![\w.])Sleep[ \t]*\(([^)]*)\)")


def mask_comments(text):
    """Blank `//` and `/* */` comments in place, keeping every offset and every newline."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "'" or c == '"':
            quote, i = c, i + 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            for k in range(i, end):
                if text[k] != "\n":
                    out[k] = " "
            i = end
            continue
        i += 1
    return "".join(out)


def depth_map(text):
    """`depths[i]` is the brace depth *before* character `i`; a `{` carries its own opening depth."""
    n = len(text)
    depths = [0] * n
    depth = 0
    i = 0
    while i < n:
        c = text[i]
        if c == "'" or c == '"':
            quote = c
            depths[i] = depth
            i += 1
            while i < n:
                depths[i] = depth
                if text[i] == "\\":
                    i += 1
                    if i < n:
                        depths[i] = depth
                        i += 1
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if c == "{":
            depths[i] = depth
            depth += 1
            i += 1
            continue
        if c == "}":
            depth = max(0, depth - 1)
            depths[i] = depth
            i += 1
            continue
        depths[i] = depth
        i += 1
    return depths


def find_close(text, depths, open_at):
    """Index of the `}` that closes the `{` at `open_at`."""
    level = depths[open_at]
    for i in range(open_at + 1, len(text)):
        if depths[i] == level and text[i] == "}":
            return i
    return len(text)


def states_in_text(text, package, cls, record_states):
    """One row per `state` block the class's own text declares."""
    if not text:
        return []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    masked = mask_comments(text)
    depths = depth_map(masked)
    rows, spans = [], []
    for hit in STATE_DECL.finditer(masked):
        auto = "yes" if hit.group(1) else "no"
        name, parent = hit.group(2), hit.group(3) or ""
        open_at = hit.end() - 1
        close_at = find_close(masked, depths, open_at)
        spans.append((open_at, close_at))
        body = masked[open_at + 1:close_at]
        base, level = open_at + 1, depths[open_at] + 1
        labels = [m.group(1) for m in LABEL.finditer(body) if depths[base + m.start()] == level]
        functions = [m.group(1) for m in FUNC.finditer(body) if depths[base + m.start()] == level]
        ig = IGNORES.search(body)
        ignores = len([x for x in re.split(r"[,\s]+", ig.group(1)) if x]) if ig else 0
        gotos = GOTO_STATE.findall(body)
        targets = sorted({t[0] for t in gotos if t[0]})
        rows.append({
            "package": package, "class": cls, "state": name, "parent": parent, "auto": auto,
            "ignores": ignores, "labels": len(labels), "functions": len(functions),
            "gotos": len(gotos), "gotos_dynamic": len(GOTO_ANY.findall(body)) - len(gotos),
            "gotos_label": len(GOTO_LABEL.findall(body)),
            "sleeps": len(SLEEP.findall(body)),
            "lines": body.count("\n") + 1,
            "targets": " ".join(targets), "class_gotos": 0, "class_targets": "", "record_states": record_states,
        })
    if not rows:
        return rows
    # What pushes the controller into a state is not only a state: the tree's decision layer is class-level
    # functions (`WhatToDoNext`, `SetEnemy`, a timer), so the text with every state body blanked is measured too.
    outside = list(masked)
    for open_at, close_at in spans:
        for i in range(open_at + 1, close_at):
            if outside[i] != "\n":
                outside[i] = " "
    outside = "".join(outside)
    class_targets = sorted({t[0] for t in GOTO_STATE.findall(outside) if t[0]})
    for row in rows:
        row["class_gotos"] = len(GOTO_STATE.findall(outside))
        row["class_targets"] = " ".join(class_targets)
    return rows


def class_table(path=None):
    """{class: (super, package, record_states)} from the committed class table (no dump)."""
    path = path or os.path.join(ROOT, "inventory", "classes.csv")
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                states = int(row.get("states") or 0)
            except ValueError:
                states = 0
            out[row["class"]] = (row["super"], row["package"], states)
    return out


def walking_roots(table, roots):
    """Every class under the roots, in name order (the tree is `super` links across packages)."""
    under = {}
    for cls, (sup, _pkg, _n) in table.items():
        under.setdefault(sup, []).append(cls)
    seen, out = set(), []
    stack = list(roots)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        if name in table:
            out.append(name)
        stack.extend(under.get(name, []))
    return sorted(out)


def script_text_reader():
    """`pkg-script-text.script_text`, loaded by path (the module name has a hyphen)."""
    path = os.path.join(ROOT, "tools", "pkg-script-text.py")
    spec = importlib.util.spec_from_file_location("sg1_pkg_script_text", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.script_text


def read_dump(system, roots, table):
    """Open each package once, read the tree's classes' text and parse their states."""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from ue2pkg import Package  # noqa: E402
    text_of = script_text_reader()
    wanted = {}
    for cls in walking_roots(table, roots):
        pkg = table[cls][1]
        wanted.setdefault(pkg, []).append(cls)
    rows, missing = [], []
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
        for cls in sorted(wanted[pkg_name]):
            export = by_name.get(cls)
            text = text_of(pkg, export) if export else None
            if text is None:
                missing.append(cls)
                continue
            rows.extend(states_in_text(text, pkg_name, cls, table[cls][2]))
    return sorted(rows, key=lambda r: (r["class"], r["state"])), missing


def render(rows):
    import io
    buf = io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    for row in rows:
        w.writerow([row[c] for c in COLUMNS])
    return buf.getvalue()


def read_csv(path):
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            for key in ("ignores", "labels", "functions", "gotos", "gotos_dynamic", "gotos_label", "sleeps", "lines",
                        "class_gotos", "record_states"):
                row[key] = int(row[key] or 0)
            out.append(row)
    return out


def hubs(rows, table):
    """(owner, state, incoming) for every literal `GotoState`, resolved along the class's super chain.

    A target name is not a key on its own: `Hunt` is a state of `BotEnemy` *and* of `ReetouBot`, and a
    `GotoState('Hunt')` in `JaffaBotHeavy` means the one it inherits. Resolution walks the caller's chain and
    names the class whose own text declares the state -- or reports the target as external, which is what an
    engine state (`Dead`, `GameEnded`) and the four unnamed exports below look like from here.
    """
    declared, folded = {}, {}
    for row in rows:
        declared.setdefault(row["class"], set()).add(row["state"])
        # `GotoState('Grazing')` reaches a state declared `grazing`: UnrealScript folds case, so the lookup does
        folded.setdefault(row["class"], {})[row["state"].lower()] = row["state"]
    chains = {}
    for cls in declared:
        chain, name, seen = [], cls, set()
        while name and name not in seen:
            seen.add(name)
            chain.append(name)
            name = table.get(name, ("", "", 0))[0]
        chains[cls] = chain

    def resolve(cls, target):
        for name in chains.get(cls, [cls]):
            hit = folded.get(name, {}).get(target.lower())
            if hit:
                return name, hit
        return None

    incoming, who, external = {}, {}, {}
    for row in rows:
        for target in row["targets"].split():
            owner = resolve(row["class"], target)
            if owner is None:
                external[target] = external.get(target, 0) + 1
                continue
            key = owner
            incoming[key] = incoming.get(key, 0) + 1
            who.setdefault(key, set()).add(row["class"])
    # a class's own functions are one dispatcher, not one per state it declares: read them once per class
    class_seen = {}
    for row in rows:
        class_seen.setdefault(row["class"], row["class_targets"])
    for cls, targets in class_seen.items():
        for target in targets.split():
            owner = resolve(cls, target)
            if owner is None:
                external[target] = external.get(target, 0) + 1
                continue
            incoming[owner] = incoming.get(owner, 0) + 1
            who.setdefault(owner, set()).add(cls)
    ranked = sorted(((o, t, n, sorted(who[(o, t)])) for (o, t), n in incoming.items()),
                    key=lambda x: (-x[2], x[1], x[0]))
    return ranked, external


def class_rows(rows):
    order, seen = [], {}
    for row in rows:
        if row["class"] not in seen:
            seen[row["class"]] = {"states": 0, "labels": 0, "gotos": 0, "record": row["record_states"],
                                  "package": row["package"]}
            order.append(row["class"])
        seen[row["class"]]["states"] += 1
        seen[row["class"]]["labels"] += row["labels"]
        seen[row["class"]]["gotos"] += row["gotos"]
    return order, seen


def doc_block(rows, table):
    order, per = class_rows(rows)
    ranked, external = hubs(rows, table)
    dynamic = sum(r["gotos_dynamic"] for r in rows)
    per_class_gotos = {}
    for row in rows:
        per_class_gotos[row["class"]] = row["class_gotos"]
    lines = ["", "**%d states over %d classes: %d declare a literal `GotoState`, %d a dynamic one, %d labels, "
                  "%d latent sleeps; the classes' own functions add %d `GotoState` calls outside every state.**"
             % (len(rows), len(order), len({r["class"] for r in rows if r["gotos"]}), dynamic,
                sum(r["labels"] for r in rows), sum(r["sleeps"] for r in rows), sum(per_class_gotos.values())),
             "| class | states in text | states in record | labels | `GotoState` |",
             "|---|---:|---:|---:|---:|"]
    for cls in order:
        d = per[cls]
        lines.append("| `%s` | %d | %d | %d | %d |" % (cls, d["states"], d["record"], d["labels"], d["gotos"]))
    if ranked:
        lines += ["", "States by in-degree — how many states or class dispatchers name each one with a literal "
                      "`GotoState`, each counted where the state is declared (a subclass naming an inherited state "
                      "counts on the ancestor):",
                  "", "| state | declared by | in-degree | named from |", "|---|---|---:|---|"]
        for owner, target, n, callers in ranked[:12]:
            lines.append("| `%s` | `%s` | %d | %s |" % (target, owner, n, ", ".join("`%s`" % c for c in callers[:4])
                                                         + (" …" if len(callers) > 4 else "")))
    if external:
        lines += ["", "`GotoState` targets that are no state of the calling class's chain — engine states and the "
                      "exports whose text is missing (below): %s."
                  % ", ".join("`%s` ×%d" % (k, external[k]) for k in sorted(external))]
    entry = {}
    for owner, target, n, _callers in ranked:
        entry.setdefault(owner, set()).add(target)
    alone = [r["state"] for r in rows
             if not r["gotos"] and not r["gotos_dynamic"] and r["state"] not in entry.get(r["class"], ())]
    if alone:
        lines += ["", "%d of %d states neither name another state nor are named by a literal `GotoState` from "
                      "either side (e.g. %s) — a state a native body or a `GotoState` with a computed argument "
                      "enters, which is the part of the machine this read cannot see."
                  % (len(alone), len(rows), ", ".join("`%s`" % s for s in alone[:6]))]
    short = [(c, per[c]["states"], per[c]["record"]) for c in order if per[c]["record"] > per[c]["states"]]
    if short:
        lines += ["", "%d class(es) carry more `UState` **exports** than their text declares — %s — and every extra "
                      "name is a state the class only *references* (`GotoState`, `IsInState`, `case`): the shipped "
                      "text is a floor on the machine, not its ceiling."
                  % (len(short), ", ".join("`%s` %d/%d" % (c, n, rec) for c, n, rec in short))]
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
        fresh = replace_block(current, doc_block(rows, class_table()))
        if args.check_doc:
            if fresh != current:
                print("FAIL %s: the generated block is stale" % path)
                return 1
            print("ok   ai-states-doc: %s matches its rows" % path)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(fresh)
            print("ok   ai-states-doc: wrote %s" % path)
    order, per = class_rows(rows)
    print("ok   ai-states: %d state(s) over %d class(es); %d declare a literal GotoState, %d a dynamic one; "
          "%d class(es) carry UState exports their text does not declare"
          % (len(rows), len(order), len({r["class"] for r in rows if r["gotos"]}),
             sum(r["gotos_dynamic"] for r in rows),
             len([c for c in order if per[c]["record"] > per[c]["states"]])))
    return 0


SAMPLE = """// Classes.StargateGame.DemoBot
class DemoBot extends BotBase;

state AlertSquad
{
\tignores SeePlayer, HearNoise, Bump;

\tfunction bool SetEnemy(Pawn Enemy)
\t{
\t\tswitch (Rand(3)) {
\t\tcase 0: return true;
\t\tdefault: break;
\t\t}
\t\t// GotoState('Commented');
\t\treturn false;
\t}

Begin:
\tFinishRotation();
\tGotoState('Hunt');
}

auto state Peace
{
\tSleep(0.5);
\tGoto('Begin');
Begin:
\tGotoState('AlertSquad', 'Begin');
}

state(42) Hunt extends AlertSquad
{
\tSleep(1.0);
}
"""


def check():
    """Synthetic: no dump, nothing written to the tree."""
    rows = states_in_text(SAMPLE, "StargateGame", "DemoBot", 3)
    got = [(r["state"], r["parent"], r["auto"], r["ignores"], r["labels"], r["functions"], r["gotos"],
            r["gotos_label"], r["sleeps"], r["targets"]) for r in rows]
    want = [("AlertSquad", "", "no", 3, 1, 1, 1, 0, 0, "Hunt"),
            ("Peace", "", "yes", 0, 1, 0, 1, 1, 1, "AlertSquad"),
            ("Hunt", "AlertSquad", "no", 0, 0, 0, 0, 0, 1, "")]
    if got != want:
        print("FAIL states_in_text:\n got  %r\n want %r" % (got, want))
        return 1
    if any(r["state"] == "Commented" for r in rows):
        print("FAIL a GotoState inside a comment was counted")
        return 1
    disp = SAMPLE.replace("class DemoBot extends BotBase;",
                          "class DemoBot extends BotBase;\n\nfunction WhatToDoNext()\n{\n\tGotoState('Waiting');\n}\n\n"
                          "auto state Waiting\n{\n\tSleep(0.25);\n}")
    drows = states_in_text(disp, "StargateGame", "DemoBot", 4)
    if {r["class_targets"] for r in drows} != {"Waiting"} or drows[0]["class_gotos"] != 1:
        print("FAIL the class-level dispatcher was not read: %r" % ([r["class_targets"] for r in drows],))
        return 1
    if any(r["state"] == "Waiting" and r["gotos"] for r in drows):
        print("FAIL a class-level GotoState was counted inside a state")
        return 1
    dh = {(o, t): n for o, t, n, _w in hubs(drows, {"DemoBot": ("BotBase", "x", 0)})[0]}
    if dh.get(("DemoBot", "Waiting")) != 1:
        print("FAIL the class-level dispatcher must count once per class, not once per state: %r" % (dh,))
        return 1
    folded = disp.replace("auto state Waiting", "auto state waiting")
    fh = {(o, t): n for o, t, n, _w in hubs(states_in_text(folded, "p", "DemoBot", 4),
                                            {"DemoBot": ("BotBase", "x", 0)})[0]}
    if fh.get(("DemoBot", "waiting")) != 1:
        print("FAIL a GotoState spelling that differs only in case must resolve: %r" % (fh,))
        return 1
    dup = SAMPLE.replace("\t\t// GotoState('Commented');", "\t\tInner:\n\t\tGotoState('Ghost');")
    alert = [r for r in states_in_text(dup, "p", "c", 0) if r["state"] == "AlertSquad"][0]
    if "Ghost" not in alert["targets"].split():
        print("FAIL a GotoState inside one of the state's own functions was not counted")
        return 1
    if alert["labels"] != 1:
        print("FAIL a label inside a function body was counted as a state label: %r" % alert)
        return 1
    table = {"DemoBot": ("BotBase", "StargateGame", 3), "BotBase": ("ScriptedController", "GamePlay", 0),
             "ScriptedController": ("Controller", "Engine", 0)}
    block = doc_block(rows, table)
    for needle in ("| `DemoBot` | 3 | 3 | 2 | 2 |", "| `Hunt` | `DemoBot` | 1 | `DemoBot` |"):
        if needle not in block:
            print("FAIL doc_block is missing %r:\n%s" % (needle, block))
            return 1
    hub = {t: n for _o, t, n, _who in hubs(rows, table)[0]}
    if hub != {"Hunt": 1, "AlertSquad": 1}:
        print("FAIL hubs: got %r" % (hub,))
        return 1
    print("ok   ai-states: a state's labels, overrides, gotos and sleeps are read; a commented GotoState and "
          "an inner-scope one are not")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--system", help="the dump's System directory")
    ap.add_argument("--root", action="append", default=None,
                    help="tree root to walk (repeatable; default AIController)")
    ap.add_argument("-o", "--out", help="write the CSV here (default: stdout)")
    ap.add_argument("--write-doc", metavar="PATH", help="regenerate the page's block between its markers")
    ap.add_argument("--check-doc", metavar="PATH", help="fail if the page's block is stale (in `make ci`)")
    ap.add_argument("--from-csv", metavar="PATH", default=os.path.join(ROOT, "inventory", "ai_states.csv"),
                    help="with --check-doc and no --system: read this artifact instead")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; needs no dump")
    args = ap.parse_args()

    if args.check:
        return check()
    if args.check_doc and not args.system:
        rows = read_csv(args.from_csv)
        if not rows:
            sys.exit("ai-states: %s holds no rows" % args.from_csv)
        return emit(rows, args)
    if not args.system:
        sys.exit("ai-states: pass --system <DUMP/System>")
    table = class_table()
    if not table:
        sys.exit("ai-states: inventory/classes.csv is missing")
    rows, missing = read_dump(args.system, args.root or ["AIController"], table)
    if missing:
        print("note: %d class(es) have no ScriptText: %s" % (len(missing), ", ".join(missing[:8])),
              file=sys.stderr)
    if not rows:
        sys.exit("ai-states: no state block under %s" % (args.root or ["AIController"]))
    return emit(rows, args)


if __name__ == "__main__":
    sys.exit(main())
