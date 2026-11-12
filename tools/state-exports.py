#!/usr/bin/env python3
"""Every AI controller state the package exports, against the states its text declares (PLAN 3.10, docs/systems/AI.md).

`tools/ai-states.py` reads the state bodies out of each class's shipped `ScriptText`, and that read leaves a
floor behind: **five classes carry more `UState` exports than their text declares** (`BotEnemy` 36/41, `BotRep`
2/4, `BotBase` 4/5, `BotLite` 4/5, `ScriptedController` 1/2), and four of the missing names are dispatched to by
`BotEnemy`'s own decision code while no class's text declares them. That read could only say *a state is
missing*; it could not say what the missing state *is*.

A `UState` is an export like any other, and its body is not in the class's text export at all -- it is the
`Function` exports whose `Outer` is the state. So the missing states can be read straight out of the export
table, with no boot and no decompiler: this tool lists every state export of the controller tree, resolves its
function children, and diffs the names against the text.

    python3 tools/state-exports.py --system DUMP/System -o inventory/state_exports.csv
    python3 tools/state-exports.py --system DUMP/System --write-doc docs/systems/AI.md
    python3 tools/state-exports.py --check          # synthetic, no dump

Columns: `package,class,state,in_text,children,functions,other_children,bytes,funcs`.

* `in_text` is `yes` when the class's own `ScriptText` declares a state of that name -- case-insensitively,
  because UnrealScript folds it and `HerdAnimal` declares `grazing` while dispatching `Grazing`.
* `children` is every export whose `Outer` is the state; `functions` the ones whose class resolves to
  `Function` and `other_children` the rest (a nested state, a text buffer). `funcs` names the functions.
* `bytes` sums the function exports' **serial size** -- the payload each one occupies in the package, not the
  script length alone (a function payload also carries its own trailer). It is a size, not a disassembly: what
  the body *does* still needs `tools/uscript-dis.py --script`.

A state the text declares and the package does not export is a disagreement in the other direction, and it is
reported as a row (`in_text=yes`, `children=0`) rather than dropped.
"""

import argparse
import csv
import importlib.util
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLUMNS = ["package", "class", "state", "in_text", "children", "functions", "other_children", "bytes", "funcs"]
DOC_MARK = ("<!-- generated:state-exports -->", "<!-- /generated:state-exports -->")


def states_module():
    """`ai-states` loaded by path (the module name has a hyphen); it owns the text-side parse."""
    path = os.path.join(ROOT, "tools", "ai-states.py")
    spec = importlib.util.spec_from_file_location("sg1_ai_states", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rows_for(package, cls, exports, text_states, class_outer):
    """`(package, class, state, ...)` rows for one class; `exports` are already name/class resolved.

    A row exists for every state export **and** for every state the text declares that no export carries, so
    neither side can hide a disagreement by being the one that is counted.
    """
    states = [e for e in exports if e["class"] == "State" and e["outer"] == class_outer]
    kids = {}
    for e in exports:
        if e["outer"] > 0:
            kids.setdefault(e["outer"], []).append(e)
    folded = {s.lower() for s in text_states}
    seen, rows = set(), []
    for export in sorted(states, key=lambda e: e["name"].lower()):
        children = kids.get(export["index"] + 1, [])
        funcs = sorted(c["name"] for c in children if c["class"] == "Function")
        others = [c for c in children if c["class"] != "Function"]
        rows.append({"package": package, "class": cls, "state": export["name"],
                     "in_text": "yes" if export["name"].lower() in folded else "no",
                     "children": len(children), "functions": len(funcs), "other_children": len(others),
                     "bytes": sum(c["size"] for c in children if c["class"] == "Function"),
                     "funcs": " ".join(funcs)})
        seen.add(export["name"].lower())
    for name in sorted(text_states):
        if name.lower() not in seen:
            rows.append({"package": package, "class": cls, "state": name, "in_text": "yes", "children": 0,
                         "functions": 0, "other_children": 0, "bytes": 0, "funcs": ""})
    return rows


def resolved(package):
    """`[{index, class, outer, name, size}]` -- every export, with its class name and own name resolved."""
    out = []
    for export in package.exports:
        out.append({"index": export["index"], "class": package.class_name_of(export),
                    "outer": export["outer"], "name": package.name(export["name"]),
                    "size": export["size"]})
    return out


def read_dump(system):
    """Every controller class's state exports, and the text-side state list for the same class."""
    mod = states_module()
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from ue2pkg import Package  # noqa: E402
    text_of = mod.script_text_reader()
    table = mod.class_table()
    by_package = {}
    for cls in mod.walking_roots(table, ["AIController"]):
        by_package.setdefault(table[cls][1], []).append(cls)
    rows, missing = [], []
    for package in sorted(by_package):
        path = os.path.join(system, package + ".u")
        if not os.path.isfile(path):
            missing.extend(by_package[package])
            continue
        pkg = Package(path)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        exports = resolved(pkg)
        classes = {}
        for e in exports:
            if e["class"] == "Class":
                classes.setdefault(e["name"], e)
        for cls in sorted(by_package[package]):
            export = classes.get(cls)
            if export is None:
                missing.append(cls)
                continue
            text = text_of(pkg, next(x for x in pkg.exports if x["index"] == export["index"]))
            declared = [s["state"] for s in mod.states_in_text(text or "", package, cls, 0)]
            rows.extend(rows_for(package, cls, exports, declared, export["index"] + 1))
    return rows, missing


def render(rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    for row in rows:
        w.writerow([row[c] for c in COLUMNS])
    return buf.getvalue()


def read_csv(path):
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            for key in ("children", "functions", "other_children", "bytes"):
                row[key] = int(row[key] or 0)
            out.append(row)
    return out


def doc_block(rows):
    """The states the text does not declare: the page's floor negative, answered by the export table."""
    absent = [r for r in rows if r["in_text"] == "no"]
    unknown = [r for r in rows if r["in_text"] == "yes" and r["children"] == 0]
    classes = sorted({r["class"] for r in absent})
    lines = ["",
             "**%d state exports over %d classes; %d are declared by no class text, and %d are declared by a "
             "text and not exported.**"
             % (len([r for r in rows if r["children"] or r["in_text"] == "no"]),
                len({r["class"] for r in rows}), len(absent), len(unknown)),
             "",
             "| class | state | functions | other children | bytes | callees the text cannot show |",
             "|---|---|---:|---:|---:|---|"]
    for r in sorted(absent, key=lambda r: (r["class"], r["state"].lower())):
        lines.append("| `%s` | `%s` | %d | %d | %d | %s |"
                     % (r["class"], r["state"], r["functions"], r["other_children"], r["bytes"],
                        ", ".join("`%s`" % f for f in r["funcs"].split()[:6]) or "—"))
    if classes:
        lines += ["",
                  "The %d class(es) whose export table outran their text: %s."
                  % (len(classes), ", ".join("`%s`" % c for c in classes))]
    if unknown:
        lines += ["", "Declared but not exported: %s."
                  % ", ".join("`%s.%s`" % (r["class"], r["state"]) for r in unknown)]
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
                print("FAIL %s: the generated state-export block is stale" % path)
                return 1
            print("ok   state-exports-doc: %s matches its rows" % path)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(fresh)
            print("ok   state-exports-doc: wrote %s" % path)
    absent = [r for r in rows if r["in_text"] == "no"]
    print("ok   state-exports: %d row(s) over %d class(es); %d state(s) the text does not declare, "
          "%d carrying functions"
          % (len(rows), len({r["class"] for r in rows}), len(absent),
             len([r for r in absent if r["functions"]])))
    return 0


def check():
    """Synthetic: an export table in memory, no dump and nothing written to the tree."""
    exports = [
        {"index": 0, "class": "Class", "outer": -1, "name": "DemoBot", "size": 0},
        {"index": 1, "class": "State", "outer": 1, "name": "Hunt", "size": 40},
        {"index": 2, "class": "Function", "outer": 2, "name": "BeginState", "size": 60},
        {"index": 3, "class": "Function", "outer": 2, "name": "Timer", "size": 40},
        {"index": 4, "class": "State", "outer": 1, "name": "GetInFormation", "size": 30},
        {"index": 5, "class": "Function", "outer": 5, "name": "WaitForManInFront", "size": 61},
        {"index": 6, "class": "TextBuffer", "outer": 5, "name": "Text", "size": 9},
        {"index": 7, "class": "State", "outer": 1, "name": "Grazing", "size": 20},
        {"index": 8, "class": "Function", "outer": 8, "name": "BeginState", "size": 55},
    ]
    rows = rows_for("StargateGame", "DemoBot", exports, ["Hunt", "grazing", "DeclaredOnly"], 1)
    got = [(r["state"], r["in_text"], r["children"], r["functions"], r["other_children"], r["bytes"], r["funcs"])
           for r in rows]
    want = [("GetInFormation", "no", 2, 1, 1, 61, "WaitForManInFront"),
            ("Grazing", "yes", 1, 1, 0, 55, "BeginState"),
            ("Hunt", "yes", 2, 2, 0, 100, "BeginState Timer"),
            ("DeclaredOnly", "yes", 0, 0, 0, 0, "")]
    if got != want:
        print("FAIL rows_for:\n got  %r\n want %r" % (got, want))
        return 1
    block = doc_block(rows)
    for needle in ("| `DemoBot` | `GetInFormation` | 1 | 1 | 61 | `WaitForManInFront` |",
                   "Declared but not exported: `DemoBot.DeclaredOnly`.",
                   "The 1 class(es) whose export table outran their text"):
        if needle not in block:
            print("FAIL doc_block is missing %r:\n%s" % (needle, block))
            return 1
    if "| `DemoBot` | `Hunt` |" in block:
        print("FAIL doc_block lists a state the text already declares:\n%s" % block)
        return 1
    print("ok   state-exports: a state export's function children, payload bytes, a folded name match and a "
          "declared-but-unexported state are all read; a declared state is not listed as missing")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--system", help="the dump's System directory")
    ap.add_argument("-o", "--out", help="write the CSV here (default: stdout)")
    ap.add_argument("--write-doc", metavar="PATH", help="regenerate the page's block between its markers")
    ap.add_argument("--check-doc", metavar="PATH", help="fail if the page's block is stale (in `make ci`)")
    ap.add_argument("--from-csv", metavar="PATH", default=os.path.join(ROOT, "inventory", "state_exports.csv"),
                    help="with --check-doc and no --system: read this artifact instead")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; needs no dump")
    args = ap.parse_args()

    if args.check:
        return check()
    if args.check_doc and not args.system:
        rows = read_csv(args.from_csv)
        if not rows:
            sys.exit("state-exports: %s holds no rows" % args.from_csv)
        return emit(rows, args)
    if not args.system:
        sys.exit("state-exports: pass --system <DUMP/System>")
    rows, missing = read_dump(args.system)
    if missing:
        print("note: no class export for %s" % ", ".join(sorted(missing)), file=sys.stderr)
    if not args.out and not args.write_doc and not args.check_doc:
        sys.stdout.write(render(rows))
        return 0
    return emit(rows, args)


if __name__ == "__main__":
    sys.exit(main())
