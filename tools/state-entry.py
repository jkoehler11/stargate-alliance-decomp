#!/usr/bin/env python3
"""Every `GotoState` in the shipped bytecode: where it sits and what it names (PLAN 3.10).

`tools/ai-states.py` reads the state machine out of each class's `ScriptText`: the declarations, the labels, the
overrides and the `GotoState` calls the source spells. That read is a floor on the machine in two directions -- a
call whose argument is an expression has no name to read, and a class whose text is missing or disabled shows
nothing at all -- and `docs/systems/AI.md` says so in its own words ("a state a native body or a `GotoState` with a
computed argument enters, which is the part of the machine this read cannot see").

This is the same question asked of the payloads: every `GotoState`/`GotoLabel`/`Goto` token in every script that
disassembles end to end, with the target and the label resolved through the package's **name table** (the operand
is a compact index into it, not an object reference). A site whose first argument is a `NameConst` is a literal
target; any other expression is `computed`, which is the population the text read cannot see.

A `Function` export is only half of the call sites, and the smaller half of the in-state ones. A state's own
code -- the label blocks between `Begin:` and the `LabelTable` -- is its `State` export's payload, and
`tools/state-code.py` had to anchor a walk of it on the payload's own 22-byte record. This census used to walk
`Function` payloads alone, so a `GotoState` in a label block was invisible and `GotoLabel` (`0x0d`) read as a
token the build never emits; both export kinds are walked now, and each row's `scope` column says which one it
came from (`function` or `state-code`).

The rows are `site` (one per call), `census` (the run-level counts the page quotes, so `--write-doc`,
`--report` and `--check-doc` are the same read) and `label` (one per state whose text and build disagree about a
label jump). Nothing here publishes script: a row names a function and a state, never a body.

Two joins follow the sites. A `GotoLabel` operand is a label name the state's own `LabelTable` also carries, so
each site is checked against that state's table -- the walk decodes the table it already ends on. A
`GotoState('X', 'Label')` names a label in *X*'s table rather than the caller's, so the second join resolves the
target state through the class chain (`inventory/classes.csv`) before looking the label up.

The text half of the jump join is `inventory/ai_states.csv`'s `gotos_label_names` (the labels a state's
`Goto('label')` calls name) and `label_names` (the labels its body declares); a state whose text and build name
the same labels the same number of times is one agreement, and `label_join` counts them per state.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(ROOT, "inventory", "state_entry.csv")
TEXT_CSV = os.path.join(ROOT, "inventory", "ai_states.csv")
DOC_MARK = ("<!-- generated:state-entry -->", "<!-- /generated:state-entry -->")
KINDS = ("GotoState", "GotoLabel", "Goto")
FIELDS = ["kind", "package", "class", "state", "function", "scope", "offset", "op", "target_kind", "target",
          "label", "args", "key", "count", "note"]


def load_tool(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def state_tool():
    """`tools/state-code.py` loaded once: its `record` and `region` anchor a `State` payload's walk.

    A state payload is `<header><script><LabelTable><0x47><22-byte record>`, and only the walk whose start the
    record's own `LabelTableOffset` fits runs from the state's first byte to the container's end. That is the same
    rule the state-code census uses, so the two artifacts agree on which payloads carry code at all: a record
    whose offset is `0xffff` has no table and no code.
    """
    global SC
    if SC is None:
        SC = load_tool("state_code", "state-code.py")
        SC.DIS = DIS
    return SC


def flat(nodes, out=None, depth=0):
    """Every token under `nodes`, depth first, kept with its nesting depth."""
    out = [] if out is None else out
    for n in nodes:
        out.append((n, depth))
        flat(n["children"], out, depth + 1)
    return out


def args_of(node):
    """A `parms` token's arguments: its children without the closing `EndFunctionParms`."""
    return [c for c in node["children"] if c.get("name") != "EndFunctionParms"]


def literal(operand, names):
    """A name-table operand as its name, or "" when the index is outside the table.

    Index 0 is a real entry, so the test is `< 0` rather than falsiness: the self-test's first case caught
    exactly that, a `NameConst 0` read as no name at all.
    """
    if operand is None or operand < 0:
        return ""
    return names[operand] if operand < len(names) else ""


def site_of(node, names):
    """(target_kind, target, label, arg count) for one `GotoState`-family token.

    The target is literal only when its first argument is a `NameConst`; anything else is an expression this
    build computes (a variable, a call, a conditional), so the read can say that such a site exists and not
    where it goes. A second argument is the state's entry label, the same shape `GotoState('X', 'DoCharge')`
    has in the text.
    """
    args = args_of(node)
    kind, target, label = "none", "", ""
    if args and args[0].get("name") == "NameConst":
        value = node_value(args[0])
        if value == 0:
            kind = "null"
        else:
            kind = "literal"
            target = literal(value, names)
    elif args:
        kind = "computed"
    if len(args) > 1:
        value = node_value(args[1]) if args[1].get("name") == "NameConst" else -1
        if args[1].get("name") != "NameConst":
            label = "computed"
        elif value > 0:
            label = literal(value, names)
    return kind, target, label, len(args)


def node_value(node):
    """The compact index a token's first operand encodes (the disassembler keeps operands as bytes).

    A truncated operand answers -1 rather than raising: the walk already refused a script it could not tile,
    so a token here has well-formed operands, and a census should not die on the one that does not.
    """
    data = node.get("operands") or b""
    if not data:
        return -1
    try:
        return DIS.read_index(data, 0)[0]
    except IndexError:
        return -1


def owner_of(pkg, exp):
    """(class name, state name or "") for a `Function` export, through its outer chain.

    A function declared inside a `state` block has the `State` export for an outer, and the class is the
    state's own outer -- taking the immediate outer answers the state's name and the class lookup then misses
    (2,137 of this corpus's `Function` exports sit in a state).
    """
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
    """Yield (package name, package, export, token tree) for every script that walks end to end."""
    from ue2pkg import Package                             # noqa: PLC0415 - only for a real package
    files = ([os.path.join(system, f) for f in sorted(os.listdir(system)) if f.lower().endswith(".u")]
             if os.path.isdir(system) else [system])
    epilogue = bytes([0x04, 0x0B, 0x47])
    for one in files:
        pkg = Package(one)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        package = os.path.splitext(os.path.basename(one))[0]
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
            if not ok or last != exp["name"]:
                continue
            if n >= 10 and pay[n - 10:n - 7] == epilogue:
                end = n - 7
            elif n >= 12 and pay[n - 12:n - 9] == epilogue:
                end = n - 9
            else:
                continue
            start = off + 16
            if start >= end:
                continue
            stats["seen"] += 1
            try:
                nodes = DIS.dis_parse(pay[start:end], table)
            except DIS.Refused:
                continue
            if sum(x["end"] - x["offset"] for x in nodes) != end - start:
                continue
            stats["walked"] += 1
            yield package, pkg, exp, nodes


def state_payloads(system, stats, tables, meta):
    """Yield (package name, package, state export, class export, token tree) for every state whose code walks.

    The class is the export's Outermost `Class`: `UState`'s payload belongs to the class that declares the state,
    and a state's functions are separate `Function` exports with the state as their outer -- the half `payloads()`
    already walks. `tables` is filled in place with the label names each walk ends on, keyed
    `(package, class, state)` in lower case: the walk already stands on the `LabelTable`, so its entries cost one
    decode and are what both label joins below resolve against.

    `meta` is filled for **every** `State` export, code or not: its table entries as `(name, offset)` pairs (the
    offset is what names the entry point -- the table entry at the first byte of code) and its own `Super`, which
    is UE2's state-level inheritance. A state whose payload is a 22-byte record can still have code one state over
    (`StandOpenTimed extends OpenTimedMover`), and that link is in the export table, not in any text.
    """
    from ue2pkg import Package                             # noqa: PLC0415 - only for a real package
    sc = state_tool()
    table = DIS.Table()
    files = ([os.path.join(system, f) for f in sorted(os.listdir(system)) if f.lower().endswith(".u")]
             if os.path.isdir(system) else [system])
    for one in files:
        pkg = Package(one)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        package = os.path.splitext(os.path.basename(one))[0]
        count = len(pkg.exports)
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "State":
                continue
            outer = pkg.exports[exp["outer"] - 1] if 0 < exp["outer"] <= count else None
            if outer is None or pkg.class_name_of(outer) != "Class":
                continue
            stats["state_payloads"] += 1
            entry = meta.setdefault((package.lower(), pkg.name(outer["name"]).lower(),
                                     pkg.name(exp["name"]).lower()),
                                    {"entries": [], "super": None,
                                     "names": (package, pkg.name(outer["name"]), pkg.name(exp["name"]))})
            sup = pkg.exports[exp["super"] - 1] if 0 < exp["super"] <= count else None
            if sup is not None and pkg.class_name_of(sup) == "State":
                sup_outer = pkg.exports[sup["outer"] - 1] if 0 < sup["outer"] <= count else None
                sup_cls = (pkg.name(sup_outer["name"]) if sup_outer is not None
                           and pkg.class_name_of(sup_outer) == "Class" else pkg.name(outer["name"]))
                entry["super"] = (sup_cls, pkg.name(sup["name"]))
            payload = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            rec = sc.record(payload)
            if rec is None or rec["label_offset"] == sc.NO_TABLE:
                continue
            stats["state_with_code"] += 1
            got = sc.region(payload, table, rec["label_offset"])
            if got is None or got["start_by"] != "field":
                continue
            stats["state_walked"] += 1
            toks = [nd for nd, _d in flat(got["nodes"]) if nd["name"] == "LabelTable"]
            pairs = []
            if toks:
                t = toks[-1]
                ents, _stop = sc.label_entries(payload[got["start"] + t["offset"] + 1:
                                                      got["start"] + t["end"]])
                pairs = [(pkg.name(nm), off) for nm, off in ents]
            entry["entries"] = pairs
            tables[(package.lower(), pkg.name(outer["name"]).lower(), pkg.name(exp["name"]).lower())] = \
                [name for name, _off in pairs]
            yield package, pkg, exp, outer, got["nodes"]


def site_rows(package, cls, state, func, scope, nodes, names):
    """One row per `GotoState`-family token under `nodes`, tagged with the export kind that carried it."""
    out = []
    for nd, _depth in flat(nodes):
        if nd.get("name") not in KINDS:
            continue
        kind, target, label, argc = site_of(nd, names)
        out.append({"kind": "site", "package": package, "class": cls, "state": state,
                    "function": func, "scope": scope, "offset": "%04x" % nd["offset"], "op": nd["name"],
                    "target_kind": kind, "target": target, "label": label, "args": argc,
                    "key": "", "count": "", "note": ""})
    return out


def read_dump(system):
    table = DIS.Table()
    stats = {"seen": 0, "walked": 0, "state_payloads": 0, "state_with_code": 0, "state_walked": 0}
    rows = []
    tables = {}
    label_tables = {}
    meta = {}
    for package, pkg, exp, nodes in payloads(system, table, stats):
        if package not in tables:
            tables[package] = [pkg.name(i) for i in range(pkg.name_count)]
        cls, state = owner_of(pkg, exp)
        rows.extend(site_rows(package, cls, state, pkg.name(exp["name"]), "function", nodes, tables[package]))
    for package, pkg, exp, outer, nodes in state_payloads(system, stats, label_tables, meta):
        if package not in tables:
            tables[package] = [pkg.name(i) for i in range(pkg.name_count)]
        rows.extend(site_rows(package, pkg.name(outer["name"]), pkg.name(exp["name"]), "", "state-code",
                              nodes, tables[package]))
    return rows, stats, label_tables, meta


def class_text_states(system, wanted):
    """`{(package, class, state): {"labels": set, "jumps": [(name, count)]}}` from each class's own `ScriptText`.

    `inventory/ai_states.csv` is a census of the `AIController` subtree, so a `GotoLabel` the build places in a
    state outside it -- the guns' `NormalFire`, a `Mover`, two ambient pawns -- had no text half at all and the
    join counted those sites and dropped them. The text is not missing, only unread: every class ships its own
    `ScriptText`, so the class that carries a site is read on demand and the join is the same one the AI census
    gets. Nothing is published: a state's labels are the `Goto('name')` operands and the labels its body declares.
    """
    from ue2pkg import Package                             # noqa: PLC0415 - only for a real package
    ast = load_tool("sg1_ai_states", "ai-states.py")
    text_of = ast.script_text_reader()
    out = {}
    by_pkg = {}
    for package, cls in wanted:
        by_pkg.setdefault(package, set()).add(cls)
    for pkg_name in sorted(by_pkg):
        path = os.path.join(system, pkg_name + ".u")
        if not os.path.isfile(path):
            continue
        pkg = Package(path)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        by_name = {}
        for export in pkg.exports:
            if pkg.class_name_of(export) == "Class":
                by_name.setdefault(pkg.name(export["name"]), export)
        for cls in sorted(by_pkg[pkg_name]):
            export = by_name.get(cls)
            text = text_of(pkg, export) if export else None
            for row in ast.states_in_text(text, pkg_name, cls, 0) if text else []:
                out[(pkg_name, cls, row["state"])] = {
                    "labels": set(row["label_names"].split()),
                    "jumps": row["gotos_label_names"].split(),
                    "count": int(row["gotos_label"] or 0),
                    "bare": int(row.get("gotos_label_bare") or 0),
                }
    return out


def census(rows, stats):
    out = []

    def add(key, count, note=""):
        out.append({"kind": "census", "key": key, "count": count, "note": note})

    sites = [r for r in rows if r["kind"] == "site"]
    add("payloads_seen", stats["seen"], "`Function` payloads whose header ends on `FriendlyName`")
    add("payloads_walked", stats["walked"], "of those, whose script walks end to end")
    add("sites", len(sites), "`GotoState`/`GotoLabel`/`Goto` tokens in those scripts")
    add("sites_function", len([r for r in sites if r["scope"] == "function"]),
        "of those, in a `Function` payload")
    add("sites_state_code", len([r for r in sites if r["scope"] == "state-code"]),
        "of those, in a `State` payload -- the state's own label blocks")
    add("state_payloads", stats.get("state_payloads", 0), "`State` exports of a class")
    add("state_with_code", stats.get("state_with_code", 0),
        "of those, whose record carries a label table (`LabelTableOffset` != `0xffff`)")
    add("state_walked", stats.get("state_walked", 0), "of those, whose walk the record's offset anchors")
    for kind in KINDS:
        add("sites_" + kind.lower(), len([r for r in sites if r["op"] == kind]), "`%s` sites" % kind)
    add("functions", len({(r["package"], r["class"], r["function"]) for r in sites if r["scope"] == "function"}),
        "functions holding one")
    add("classes", len({(r["package"], r["class"]) for r in sites}), "classes holding one")
    add("literal", len([r for r in sites if r["target_kind"] == "literal"]), "targets named by a `NameConst`")
    add("null", len([r for r in sites if r["target_kind"] == "null"]),
        "sites whose target is the name table's entry 0 -- `GotoState('')`, which leaves the current state")
    add("computed", len([r for r in sites if r["target_kind"] == "computed"]),
        "targets built from an expression, so the text read cannot name them")
    add("no_arg", len([r for r in sites if r["target_kind"] == "none"]), "sites with no argument at all")
    add("labelled", len([r for r in sites if r["label"]]), "sites that also pass an entry label")
    add("labelled_computed", len([r for r in sites if r["label"] == "computed"]), "of those, a computed label")
    add("in_state", len([r for r in sites if r.get("state")]), "sites inside a state")
    add("in_state_function", len([r for r in sites if r.get("state") and r["scope"] == "function"]),
        "of those, in a function the state declares")
    add("in_state_code", len([r for r in sites if r.get("state") and r["scope"] == "state-code"]),
        "of those, in the state's own code")
    add("in_class", len([r for r in sites if not r.get("state")]), "sites in a class-level function")
    add("targets", len({r["target"] for r in sites if r["target"] and r["op"] == "GotoState"}),
        "distinct literal state targets over the whole corpus")
    # The counts a comparison against the text needs are the `GotoState` population on its own: a `GotoLabel`
    # target is a label inside the state, not a state, and mixing the two made "sites in a state" read as if a
    # label jump were an entry.
    gs = [r for r in sites if r["op"] == "GotoState"]
    for key, rows_, note in (
            ("gs_literal", [r for r in gs if r["target_kind"] == "literal"],
             "of the `GotoState` sites, a `NameConst` target"),
            ("gs_null", [r for r in gs if r["target_kind"] == "null"],
             "of those, the null name -- `GotoState('')`, which leaves the current state"),
            ("gs_computed", [r for r in gs if r["target_kind"] == "computed"],
             "of those, an expression the text read cannot name"),
            ("gs_none", [r for r in gs if r["target_kind"] == "none"], "of those, no argument"),
            ("gs_labelled", [r for r in gs if r["label"]], "of those, an entry label"),
            ("gs_labelled_computed", [r for r in gs if r["label"] == "computed"],
             "of those, a computed label"),
            ("gs_in_state", [r for r in gs if r.get("state")], "of those, inside a state"),
            ("gs_in_state_function", [r for r in gs if r.get("state") and r["scope"] == "function"],
             "of those, in a function the state declares"),
            ("gs_in_state_code", [r for r in gs if r["scope"] == "state-code"],
             "of those, in the state's own code"),
            ("gs_in_class", [r for r in gs if not r.get("state")], "of those, in a class-level function")):
        add(key, len(rows_), note)
    add("gs_targets", len({r["target"] for r in gs if r["target"]}), "distinct literal state targets")
    add("gs_functions", len({(r["package"], r["class"], r["function"]) for r in gs if r["scope"] == "function"}),
        "functions holding a `GotoState`")
    add("gs_classes", len({(r["package"], r["class"]) for r in gs}), "classes holding one")
    add("label_targets", len({r["target"] for r in sites if r["target"] and r["op"] == "GotoLabel"}),
        "distinct literal label targets (`GotoLabel`, in state code only)")
    add("one_arg", len([r for r in sites if r["args"] == 1]), "sites passing one argument")
    add("two_arg", len([r for r in sites if r["args"] == 2]), "sites passing two")
    add("three_arg", len([r for r in sites if r["args"] > 2]), "sites passing more than two")
    return out


def text_control(sites):
    """The text census's `GotoState` counts beside this read's, over the classes that both cover.

    A comparison of two populations is not a control, so the bytecode side is restricted to the classes
    `inventory/ai_states.csv` measured -- the AI controllers -- and both splits are then the same classes read
    twice. `class_gotos` and `class_gotos_dynamic` repeat the class's dispatcher counts on every one of its state
    rows, so both are read once per class and never summed down a column: summing them over the 41 `BotEnemy` rows
    turns 40 class-level calls into 1,640 and every per-class delta into noise.
    """
    if not os.path.exists(TEXT_CSV):
        return []
    per = {}
    dyn = {}
    t_in = collections.Counter()
    t_dyn = collections.Counter()
    states = gotos = 0
    for r in csv.DictReader(open(TEXT_CSV, encoding="utf-8")):
        states += 1
        gotos += int(r.get("gotos") or 0)
        k = (r["package"], r["class"])
        per[k] = int(r.get("class_gotos") or 0)
        dyn[k] = int(r.get("class_gotos_dynamic") or 0)
        t_in[k] += int(r.get("gotos") or 0)
        t_dyn[k] += int(r.get("gotos_dynamic") or 0)
    mine = [s for s in sites if (s["package"], s["class"]) in per and s["op"] == "GotoState"]
    labels = [s for s in sites if (s["package"], s["class"]) in per and s["op"] != "GotoState"]
    out = [{"kind": "census", "key": "text_states", "count": states, "note": "`ai_states.csv` rows"},
           {"kind": "census", "key": "text_gotos_in_states", "count": gotos,
            "note": "`GotoState` in a state body, from the text"},
           {"kind": "census", "key": "text_gotos_in_classes", "count": sum(per.values()),
            "note": "`GotoState` in a class's own functions, from the text (per class, not per state)"},
           {"kind": "census", "key": "ai_classes", "count": len(per), "note": "classes both reads cover"},
           {"kind": "census", "key": "text_class_dynamic", "count": sum(dyn.values()),
            "note": "`GotoState` with a computed target in a class-level function, from the text"},
           {"kind": "census", "key": "ai_sites", "count": len(mine), "note": "bytecode sites in those classes"},
           {"kind": "census", "key": "ai_function", "count": len([s for s in mine if s["scope"] == "function"]),
            "note": "of those, in a `Function` payload"},
           {"kind": "census", "key": "ai_state_code", "count": len([s for s in mine if s["scope"] == "state-code"]),
            "note": "of those, in a `State` payload"},
           {"kind": "census", "key": "ai_in_state", "count": len([s for s in mine if s.get("state")]),
            "note": "of those, inside a state"},
           {"kind": "census", "key": "ai_function_in_state",
            "count": len([s for s in mine if s.get("state") and s["scope"] == "function"]),
            "note": "of those, in a function the state declares"},
           {"kind": "census", "key": "ai_state_code_entries",
            "count": len([s for s in mine if s["scope"] == "state-code"]),
            "note": "of those, in the state's own code"},
           {"kind": "census", "key": "ai_labels", "count": len(labels),
            "note": "`GotoLabel` sites in those classes -- label jumps, not state entries"},
           {"kind": "census", "key": "ai_in_class", "count": len([s for s in mine if not s.get("state")]),
            "note": "of those, in a class-level function"},
           {"kind": "census", "key": "ai_literal", "count": len([s for s in mine if s["target_kind"] == "literal"]),
            "note": "of those, a literal target"},
           {"kind": "census", "key": "ai_computed", "count": len([s for s in mine if s["target_kind"] == "computed"]),
            "note": "of those, a computed target -- what the text read cannot name"}]
    # Per class, because a total that agrees can hide two classes that do not: the row's `count` is the text's
    # deficit (negative) or surplus (positive) over the build at both places, and `target` is its verdict.
    rows = []
    for k in sorted(per):
        b_in = len([s for s in mine if (s["package"], s["class"]) == k and s.get("state")])
        b_cls = len([s for s in mine if (s["package"], s["class"]) == k and not s.get("state")])
        text_in = t_in[k] + t_dyn[k]
        text_cls = per[k] + dyn[k]
        delta = (text_in - b_in) + (text_cls - b_cls)
        rows.append({"kind": "class", "package": k[0], "class": k[1], "state": "", "function": "", "scope": "",
                     "offset": "", "op": "", "target_kind": "",
                     "target": "agree" if not delta else ("text-ahead" if delta > 0 else "bytecode-ahead"),
                     "label": "", "args": "", "key": "delta", "count": delta,
                     "note": "text %d in-state (+%d computed), %d class-level (+%d computed); build %d / %d"
                             % (t_in[k], t_dyn[k], per[k], dyn[k], b_in, b_cls)})
    out.append({"kind": "census", "key": "ai_agree", "count": len([r for r in rows if not r["count"]]),
                "note": "of those classes, the ones whose text and build agree to the site"})
    return out + rows


def class_supers():
    """`(package, class)` -> `(super package, super class)`, lower case, from the committed class table."""
    path = os.path.join(ROOT, "inventory", "classes.csv")
    out = {}
    if not os.path.exists(path):
        return out
    for r in csv.DictReader(open(path, encoding="utf-8")):
        out[(r["package"].lower(), r["class"].lower())] = (r["package_of_super"].lower(), (r["super"] or "").lower())
    return out


def resolve_table(package, cls, state, tables, supers, meta=None):
    """`(label names, how)` for `state` as the caller's class chain and the state's own `Super` declare it.

    A `GotoState('Hunt', 'DoCharge')` names a state by name, not by owner: `Hunt` can be declared by an ancestor,
    so the lookup walks `inventory/classes.csv`'s supers the way `tools/ai-states.py` resolves a target. `None`
    means no state of that name on the chain carries a table.

    The chain is not the only link. A record-only state can inherit its code from a **sibling** state, and UE2
    records that in the `State` export's own `Super` (a `UStruct` super, one level below the class): the timed
    `Mover` states are 22-byte records whose `Super` is `OpenTimedMover`, the state that owns the `Open`/`Close`
    table. So when the class chain finds the state and it has no table, the state's `Super` is followed before the
    class's -- `how` says which one answered, so the doc can count the sites that needed the second link.
    """
    p, c, s = package.lower(), cls.lower(), state.lower()
    seen = set()
    how = ""
    for _ in range(40):
        key = (p, c, s)
        if key in seen:
            return None, how
        seen.add(key)
        if key in tables:
            return tables[key], how
        m = (meta or {}).get(key)
        if m and m.get("super") and m["super"][1].lower() != s:
            how = "super:%s.%s" % m["super"]
            p, s = p, m["super"][1].lower()
            if m["super"][0].lower() != c:
                c = m["super"][0].lower()
            continue
        edge = supers.get((p, c))
        if not edge or not edge[1]:
            return None, how
        p, c = edge
    return None, how


def table_for(package, cls, state, tables, supers, meta=None):
    """The label names of `state`, or `None` when no state on the chain carries a table (see `resolve_table`)."""
    return resolve_table(package, cls, state, tables, supers, meta)[0]


def entry_point(entries):
    """The table entry the runtime starts at: the one at the first byte of code, `Begin` first among aliases.

    Six tables name two labels at offset 0 (`ShieldCover` carries `Begin` and `Turn` on its first byte), so the
    entry point is a *set* of names there; `Begin` is preferred because that is the name the runtime looks up when
    a bare `GotoState('X')` passes no label -- FName 100 is `Begin` in the image's own name reserve
    (`tools/gname-seed.py`), not merely the compiler's habit. `entry_names` is the whole set.
    """
    if not entries:
        return None
    low = min(pair[1] for pair in entries)
    names = [name for name, off in entries if off == low]
    return next((n for n in names if n.lower() == "begin"), names[0]), low


def entry_names(entries):
    """Every label the table puts on its first byte of code."""
    if not entries:
        return []
    low = min(pair[1] for pair in entries)
    return [name for name, off in entries if off == low]


def label_join(sites, tables, meta=None, text_states=None):
    """The text's `Goto('label')` jumps beside the build's `GotoLabel` tokens, per state and per label.

    Three things are measured here and each one is a different claim:

    * **the join** -- a state whose text spells `Goto('Begin')` twice and whose `State` payload carries two
      `GotoLabel` operands naming `Begin` is one agreement. `inventory/ai_states.csv` covers the `AIController`
      subtree; every other state the build jumps in has its class's own text read here (`class_text_states`), so
      the join covers the whole corpus and a state outside the AI census is joined rather than only counted.
    * **the table** -- a `GotoLabel` operand is a label the state's own `LabelTable` must carry, or the runtime has
      nothing to jump to; the walk's own table decode is the oracle.
    * **the entry label** -- `GotoState('X', 'Label')` names a label in `X`, resolved through the class chain and
      then through `X`'s own `Super`, so a target whose code is a record alone names the label of the state it
      inherits from.
    """
    rows, out = [], []
    text_states = text_states or {}

    def add(key, count, note=""):
        out.append({"kind": "census", "key": key, "count": count, "note": note})

    lab = [r for r in sites if r["op"] == "GotoLabel" and r["target"]]
    inside = outside = 0
    for r in lab:
        names = {x.lower() for x in tables.get((r["package"].lower(), r["class"].lower(), r["state"].lower()), [])}
        if r["target"].lower() in names:
            inside += 1
        else:
            outside += 1
    add("labels_in_table", inside, "`GotoLabel` operands that name an entry of their own state's `LabelTable`")
    add("labels_outside_table", outside, "of those, a label no table of that state names")
    add("label_states", len({(r["package"], r["class"], r["state"]) for r in lab}),
        "states whose own code jumps to a label")

    entry = [r for r in sites if r["op"] == "GotoState" and r["label"] and r["label"] != "computed"]
    literal = [r for r in entry if r["target_kind"] == "literal"]
    supers = class_supers()
    hit = absent = nocode = via_super = 0
    unresolved = []
    for r in literal:
        names, how = resolve_table(r["package"], r["class"], r["target"], tables, supers, meta)
        if names is None:
            nocode += 1
            unresolved.append((r, "nocode", None))
        elif r["label"].lower() in {x.lower() for x in names}:
            hit += 1
            if how:
                via_super += 1
        else:
            absent += 1
            unresolved.append((r, "absent", how))
    add("entry_labels", len(entry), "`GotoState('X', 'Label')` sites with a literal entry label")
    add("entry_labels_literal_target", len(literal), "of those, whose state target is a `NameConst` too")
    add("entry_labels_in_table", hit, "of those, the label an entry of the target state's own table")
    add("entry_labels_via_state_super", via_super,
        "of those, resolved through the target state's own `Super` -- its code is the state it inherits from")
    add("entry_labels_absent", absent, "of those, a label the target state's table does not name")
    add("entry_labels_no_code", nocode, "of those, whose target state carries a record and no table")
    # A bare `GotoState('X')` passes no label, so the native enters `X` at FName 100 -- and the image's name reserve
    # spells slot 100 `Begin` (`tools/gname-seed.py`, docs/systems/STATE_NATIVES.md). A table whose entry byte
    # carries another name therefore has no `Begin` to land on: the lookup misses, clears the frame's code pointer
    # and the state is entered without running. This is the corpus side of the native read, so it is counted here
    # rather than in the page's prose.
    bare = [r for r in sites if r["op"] == "GotoState" and r["target_kind"] == "literal" and r["target"]
            and not r["label"] and r["args"] == 1]
    on_begin = other = no_table = 0
    for r in bare:
        names, how = resolve_table(r["package"], r["class"], r["target"], tables, supers, meta)
        if names is None:
            no_table += 1
            continue
        if "begin" in {x.lower() for x in names}:
            on_begin += 1
            continue
        other += 1
        names0 = [n for n, off in (meta.get((r["package"].lower(), r["class"].lower(), r["target"].lower()),
                                            {}).get("entries") or []) if off == 0]
        rows.append({"kind": "bare", "package": r["package"], "class": r["class"], "state": r["state"],
                     "function": r.get("function", ""), "scope": r.get("scope", ""),
                     "offset": r.get("offset", ""), "op": "GotoState", "target_kind": r["target_kind"],
                     "target": r["target"], "label": "", "args": r.get("args", ""), "key": "no-begin",
                     "count": 1,
                     "note": "the entry byte carries %s, not `Begin`%s"
                             % (" and ".join("`%s`" % n for n in names0) or "no name",
                                " (reached via %s)" % how if how else "")})
    add("bare_entries", len(bare), "`GotoState('X')` sites with a literal target and no label")
    add("bare_entries_begin", on_begin,
        "of those, whose target's table carries `Begin` -- the name the default label resolves to")
    add("bare_entries_other_label", other,
        "of those, whose target's entry byte carries another name -- the lookup misses and the state does not run")
    add("bare_entries_no_table", no_table, "of those, whose target carries no table at all")
    # The same link seen from the other side: how many record-only states are record-only *here* only, because the
    # code they run is the `Super` state's. That is the population a "record-only means no code" reading would miss.
    inherited = [k for k in meta if k not in tables and meta[k].get("super")]
    with_code = [k for k in inherited if resolve_table(k[0], k[1], k[2], tables, supers, meta)[0] is not None]
    add("record_states", len([k for k in meta if k not in tables]),
        "`State` exports whose own payload is a record with no label table")
    add("record_states_super", len(inherited), "of those, that name a `Super` state")
    add("record_states_inherited_code", len(with_code),
        "of those, whose `Super` chain reaches a state with a table -- the code they run")
    # Which table entry is the entry point? The table's own offsets are into the state's code, and the entry the
    # runtime starts at is the one at the first byte -- so `Begin` is the name a compiler gives it, not the rule.
    point = {}
    alias = []
    for key, m in sorted(meta.items()):
        if not m["entries"]:
            continue
        point[key] = entry_point(m["entries"])
        names = entry_names(m["entries"])
        if len(names) > 1:
            alias.append((key, names))
            rows.append({"kind": "alias", "package": m["names"][0], "class": m["names"][1],
                         "state": m["names"][2], "function": "", "scope": "", "offset": "%04x" % min(o for _n, o in m["entries"]),
                         "op": "LabelTable", "target_kind": "", "target": " ".join(names), "label": "",
                         "args": "", "key": "entry", "count": len(names),
                         "note": "%d names on the first byte of code" % len(names)})
    add("entry_points", len(point), "states whose table names an entry point")
    add("entry_points_at_zero", len([1 for name, off in point.values() if off == 0]),
        "of those, whose entry point is the first byte of code (offset 0)")
    add("entry_points_begin", len([1 for name, _off in point.values() if name.lower() == "begin"]),
        "of those, called `Begin`")
    add("entry_points_alias", len(alias),
        "of those, whose first byte carries two label names -- the entry point is a set there")
    add("label_jumps_to_entry", len([r for r in lab
                                     if point.get((r["package"].lower(), r["class"].lower(),
                                                   r["state"].lower()), ("", -1))[0].lower() == r["target"].lower()]),
        "`GotoLabel` sites whose operand is their own state's entry point -- a jump back to the top of the block")
    for key in sorted(point):
        name, off = point[key]
        pkg_name, cls_name, st_name = meta[key].get("names", (key[0], key[1], key[2]))
        rows.append({"kind": "entry", "package": pkg_name, "class": cls_name, "state": st_name,
                     "function": "", "scope": "", "offset": "%04x" % off, "op": "LabelTable",
                     "target_kind": "", "target": name, "label": "", "args": "", "key": "",
                     "count": off, "note": ""})

    for r, why, how in unresolved:
        chain = ""
        if meta.get((r["package"].lower(), r["class"].lower(), r["target"].lower())):
            chain = "%s.%s has a record and no table" % (r["class"], r["target"])
        rows.append({"kind": why, "package": r["package"], "class": r["class"], "state": r["state"],
                     "function": r.get("function", ""), "scope": r.get("scope", ""),
                     "offset": r.get("offset", ""), "op": "GotoState", "target_kind": r["target_kind"],
                     "target": r["target"], "label": r["label"], "args": r.get("args", ""),
                     "key": "entry-label", "count": 1,
                     "note": (("the label is not an entry of the target's table" + (" (reached via %s)" % how
                                                                                  if how else ""))
                              if why == "absent" else chain or "no state of that name in the caller's chain")})

    if not os.path.exists(TEXT_CSV) and not text_states:
        return out
    text, counts, bare_total = {}, {}, [0]
    if os.path.exists(TEXT_CSV):
        for r in csv.DictReader(open(TEXT_CSV, encoding="utf-8")):
            k = (r["package"], r["class"], r["state"])
            counts[k] = int(r.get("gotos_label") or 0)
            text[k] = {x.lower() for x in (r.get("gotos_label_names") or "").split()}
            bare_total[0] += int(r.get("gotos_label_bare") or 0)
    bare_total[0] += sum(v.get("bare", 0) for v in text_states.values())
    build = collections.defaultdict(collections.Counter)
    spelled_target = {}
    for r in lab:
        build[(r["package"], r["class"], r["state"])][r["target"].lower()] += 1
        spelled_target.setdefault((r["package"].lower(), r["class"].lower(), r["state"].lower()), {})[
            r["target"].lower()] = r["target"]

    def joined_key(k):
        """(label names the text spells, how many jumps) for one state, from either text half."""
        if k in counts:
            return text[k], counts[k]
        return {x.lower() for x in text_states[k]["jumps"]}, text_states[k]["count"]

    def agrees(k):
        want, n = joined_key(k)
        return want == set(build[k]) and sum(build[k].values()) == n, want, n

    spelled = {k: v for k, v in counts.items() if v}
    add("text_label_states", len(spelled), "states whose text spells a `Goto('label')`")
    add("text_label_jumps", sum(spelled.values()), "`Goto('label')` calls in those states, from the text")
    add("text_label_bare", bare_total[0],
        "of those, spelled `goto 'label'` without parentheses -- the same `GotoLabel` op")
    both = sorted(k for k in build if k in spelled)
    add("ai_label_states", len(both), "states both reads cover")
    add("ai_label_jumps", sum(sum(build[k].values()) for k in both), "`GotoLabel` sites in those states")
    add("ai_label_agree", len([k for k in both if agrees(k)[0]]),
        "of those, the states whose text and build name the same labels as often")
    # The states the AI census does not measure: the class that carries the site is read here instead, so these
    # are joined on the same terms and not merely counted.
    extra = {k: v for k, v in text_states.items() if k not in counts and v["count"]}
    add("extra_label_states", len(extra),
        "states outside `ai_states.csv` whose own class text is read here for the join")
    add("extra_label_jumps", sum(v["count"] for v in extra.values()),
        "`Goto('label')` calls in those states, from the text")
    joined = sorted(k for k in build if k in counts or k in extra)
    add("label_join_states", len(joined), "states with a `GotoLabel` site and a text half")
    add("label_join_agree", len([k for k in joined if agrees(k)[0]]),
        "of those, the states whose text and build name the same labels as often")
    add("label_join_jumps", sum(sum(build[k].values()) for k in joined),
        "`GotoLabel` sites the join covers")
    add("label_build_only_states", len([k for k in build if k not in counts and k not in extra]),
        "states the build places a label jump in with no text half at all")
    add("label_build_only_jumps", sum(sum(build[k].values()) for k in build
                                      if k not in counts and k not in extra),
        "label jumps in those states")
    alltext = {k: v for k, v in counts.items()}
    alltext.update({k: v["count"] for k, v in text_states.items() if k not in alltext})
    add("label_text_only_states", len([k for k, n in alltext.items() if n and k not in build]),
        "states whose text spells a jump the build carries no `GotoLabel` for")
    for k in joined:
        ok, want, n = agrees(k)
        if ok:
            continue
        got = set(build[k])
        rows.append({"kind": "label", "package": k[0], "class": k[1], "state": k[2], "function": "",
                     "scope": "", "offset": "", "op": "GotoLabel", "target_kind": "",
                     "target": "text-ahead" if (len(got) < len(want) or sum(build[k].values()) < n)
                     else "bytecode-ahead",
                     "label": "", "args": "", "key": "delta",
                     "count": n - sum(build[k].values()),
                     "note": "text %d %s; build %d %s" % (n, " ".join(sorted(want)),
                                                          sum(build[k].values()), " ".join(sorted(got)))})
    for k in sorted(k for k in build if k not in counts and k not in extra):
        rows.append({"kind": "label", "package": k[0], "class": k[1], "state": k[2], "function": "",
                     "scope": "", "offset": "", "op": "GotoLabel", "target_kind": "", "target": "text-none",
                     "label": "", "args": "", "key": "delta", "count": sum(build[k].values()),
                     "note": "build %d %s, and the state's own text spells no `Goto`"
                             % (sum(build[k].values()),
                                " ".join("`%s`" % spelled_target.get((k[0].lower(), k[1].lower(), k[2].lower()),
                                                                    {}).get(x, x)
                                         for x in sorted(build[k])))})
    return out + rows


def render(rows):
    buf = []
    import io
    fh = io.StringIO()
    w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in FIELDS})
    buf.append(fh.getvalue())
    return "".join(buf)


def read_csv(path):
    if not os.path.exists(path):
        return []
    return list(csv.DictReader(open(path, encoding="utf-8")))


def count(rows, key):
    for r in rows:
        if r.get("kind") == "census" and r.get("key") == key:
            return int(r["count"])
    return 0


def doc_block(rows):
    sites = [r for r in rows if r.get("kind") == "site"]
    per = collections.Counter((r["target"], r.get("class", "")) for r in sites
                              if r["target"] and r["op"] == "GotoState")
    targets = collections.Counter(r["target"] for r in sites if r["target"] and r["op"] == "GotoState")
    lines = []
    lines.append("")
    lines.append("%s `GotoState` sites over %d functions in %d classes of the %s scripts that walk end to end: "
                 "**%d** name their target with a `NameConst`, **%d** name the null name (`GotoState('')`, which "
                 "leaves the current state), **%d** build it from an expression, **%d** pass no argument at all, "
                 "and **%d** also pass an entry label (**%d** of those computed)."
                 % ("{:,}".format(count(rows, "sites_gotostate")), count(rows, "gs_functions"),
                    count(rows, "gs_classes"), "{:,}".format(count(rows, "payloads_walked")),
                    count(rows, "gs_literal"), count(rows, "gs_null"), count(rows, "gs_computed"),
                    count(rows, "gs_none"), count(rows, "gs_labelled"), count(rows, "gs_labelled_computed")))
    lines.append("")
    lines.append("Where the call sits is a second split: over the whole corpus **%d** sites are inside a state "
                 "and **%d** are in a class-level function -- and the in-state half comes from two export kinds, "
                 "**%d** in a `Function` the state declares and **%d** in the state's own code (its `State` "
                 "payload), which this census did not walk before. Restricted to the **%d** classes "
                 "`inventory/ai_states.csv` measured -- the same classes read twice, so the counts are a "
                 "control and not two populations -- the bytecode has **%d** in-state sites (**%d** in a "
                 "function, **%d** in state code) where the text spells **%d**, and **%d** class-level sites "
                 "where the text spells **%d** literal and **%d** computed."
                 % (count(rows, "gs_in_state"), count(rows, "gs_in_class"), count(rows, "gs_in_state_function"),
                    count(rows, "gs_in_state_code"), count(rows, "ai_classes"),
                    count(rows, "ai_in_state"), count(rows, "ai_function_in_state"),
                    count(rows, "ai_state_code_entries"),
                    count(rows, "text_gotos_in_states"), count(rows, "ai_in_class"),
                    count(rows, "text_gotos_in_classes"), count(rows, "text_class_dynamic")))
    lines.append("")
    off = [r for r in rows if r.get("kind") == "class" and int(r.get("count") or 0)]
    lines.append("**%d** of those **%d** classes agree to the site; the ones that do not are %s."
                 % (count(rows, "ai_agree"), count(rows, "ai_classes"),
                    ", ".join("`%s` (%+d)" % (r["class"], int(r["count"]))
                              for r in sorted(off, key=lambda r: int(r["count"]))) or "none"))
    lines.append("")
    lines.append("**%d** of the corpus's sites are `GotoLabel` (`0x0d`), and every one of them is in state code: "
                 "**0** sits in any `Function` payload, which is why the branch census could report the token as "
                 "one the build never emits. They name **%d** distinct labels."
                 % (count(rows, "sites_gotolabel"), count(rows, "label_targets")))
    lines.append("")
    lines.append("The labels those jumps name are the text's own, and the join now covers the whole corpus: of "
                 "the **%d** states that carry a `GotoLabel` site, **%d** are `ai_states.csv`'s (**%d** text "
                 "calls against **%d** sites, **%d** naming the same labels as often) and **%d** sit outside that "
                 "census -- the guns' `NormalFire`, a `Mover`, a pickup, a summoning loop and two ambient pawns -- "
                 "where the class's own text is read for this join (**%d** calls against **%d** sites). "
                 "**%s** name the same labels as often as the text does; **%d** state carries a jump its text "
                 "does not spell, and **%d** text `Goto` has no site."
                 % (count(rows, "label_join_states") + count(rows, "label_build_only_states"),
                    count(rows, "ai_label_states"),
                    count(rows, "text_label_jumps"), count(rows, "ai_label_jumps"),
                    count(rows, "ai_label_agree"), count(rows, "extra_label_states"),
                    count(rows, "extra_label_jumps"),
                    count(rows, "label_join_jumps") - count(rows, "ai_label_jumps"),
                    ("All %d joined states" % count(rows, "label_join_agree")
                     if count(rows, "label_join_agree") == count(rows, "label_join_states")
                     else "%d of %d joined states" % (count(rows, "label_join_agree"),
                                                          count(rows, "label_join_states"))),
                    count(rows, "label_build_only_states"), count(rows, "label_text_only_states")))
    lines.append("")
    lines.append("| label | jumps | states | classes |")
    lines.append("| --- | ---: | --- | --- |")
    lab = [r for r in sites if r["op"] == "GotoLabel" and r["target"]]
    for name, n in sorted(collections.Counter(r["target"] for r in lab).items(),
                          key=lambda kv: (-kv[1], kv[0]))[:12]:
        states = sorted({r["state"] for r in lab if r["target"] == name})
        lines.append("| `%s` | %d | %s | %s |"
                     % (name, n, ", ".join("`%s`" % s for s in states[:3]) + (" …" if len(states) > 3 else ""),
                        ", ".join("`%s`" % c for c in sorted({r["class"] for r in lab if r["target"] == name})[:2])))
    lines.append("")
    lines.append("Every label jump names a label its own state's table carries: **%d of %d** operands land on an "
                 "entry of that `LabelTable` and **%d** names a label no table of the state carries. The walk "
                 "already ends on that table, so its entries are one decode beside it rather than a second read."
                 % (count(rows, "labels_in_table"), count(rows, "sites_gotolabel"),
                    count(rows, "labels_outside_table")))
    lines.append("")
    lines.append("**%d** `GotoState` sites also pass an entry label, and it is the *target's* label: **%d** of "
                 "them name a literal state too, and resolved through the class chain and then through the "
                 "target state's own `Super` **%d** of those name an entry of that state's table (**%d** of them "
                 "through `Super` -- the timed `Mover` states, whose payload is a record and whose code is "
                 "`OpenTimedMover`'s), **%d** names a label the table does not carry, and **%d** names a state no "
                 "chain reaches a table for."
                 % (count(rows, "entry_labels"), count(rows, "entry_labels_literal_target"),
                    count(rows, "entry_labels_in_table"), count(rows, "entry_labels_via_state_super"),
                    count(rows, "entry_labels_absent"), count(rows, "entry_labels_no_code")))
    lines.append("")
    lines.append("A record-only payload is not proof of no code. Of the **%d** `State` exports whose payload is a "
                 "record with no table, **%d** name a `Super` state and **%d** reach a table through it: the code "
                 "they run is the state they inherit from, which is the mechanism `BotSG.InvisibleRest extends "
                 "RestFormation` shows in the text and the export table shows for every one of them."
                 % (count(rows, "record_states"), count(rows, "record_states_super"),
                    count(rows, "record_states_inherited_code")))
    lines.append("")
    ent = [r for r in rows if r.get("kind") == "entry"]
    names = collections.Counter(r["target"] for r in ent)
    other = [n for n in names if n.lower() != "begin"]
    lines.append("The entry point is a name the table carries, not a convention: **%d** of the **%d** tables put "
                 "their entry at the first byte of code, **%d** call it `Begin` and the other **%d** the "
                 "statement the block starts at (%s). In **%d** of them the first byte carries two names, so "
                 "entering the state and jumping to its top are the same address. **%d** of the **%d** label "
                 "jumps name their own state's entry point that way -- a jump back to the top of the block -- "
                 "and the rest jump forward inside it."
                 % (count(rows, "entry_points_at_zero"), count(rows, "entry_points"),
                    count(rows, "entry_points_begin"),
                    count(rows, "entry_points") - count(rows, "entry_points_begin"),
                    ", ".join("`%s` ×%d" % (n, names[n]) for n in
                              sorted(other, key=lambda n: (-names[n], n))),
                    count(rows, "entry_points_alias"),
                    count(rows, "label_jumps_to_entry"), count(rows, "sites_gotolabel")))
    offlab = [r for r in rows if r.get("kind") == "label"]
    if offlab:
        lines.append("")
        lines.append("The states whose label jumps the two reads count differently: %s."
                     % "; ".join("`%s.%s` -- %s" % (r["class"], r["state"], r["note"]) for r in offlab))
    lines.append("")
    lines.append("The states the `GotoState` sites enter:")
    lines.append("")
    lines.append("| target | sites | classes |")
    lines.append("| --- | ---: | --- |")
    for name, n in sorted(targets.items(), key=lambda kv: (-kv[1], kv[0]))[:14]:
        cls = sorted({c for (t, c) in per if t == name})
        lines.append("| `%s` | %d | %s |" % (name, n, ", ".join("`%s`" % c for c in cls[:3])))
    lines.append("")
    lines.append("The %d computed-target sites are the population behind the page's \"a state a native body or a "
                 "`GotoState` with a computed argument enters\": each one is a state this build can enter and "
                 "the text cannot name." % count(rows, "computed"))
    lines.append("")
    return "\n".join(lines)


def replace_block(text, block):
    begin, end = DOC_MARK
    i, j = text.find(begin), text.find(end)
    if i < 0 or j <= i:
        sys.exit("document has no %s ... %s block" % DOC_MARK)
    return text[: i + len(begin)] + block + "\n" + text[j:]


def emit(rows, args):
    rows = [r for r in rows if r.get("kind") != "census"] + [r for r in rows if r.get("kind") == "census"]
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
                print("FAIL %s: the generated block is stale" % path)
                return 1
            print("ok   state-entry-doc: %s matches its rows" % path)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(fresh)
            print("ok   state-entry-doc: wrote %s" % path)
    print("ok   state-entry: %d site(s) over %d function(s) in %d class(es); %d literal, %d computed, "
          "%d labelled; %d in a state (%d in a function, %d in state code), %d class-level"
          % (count(rows, "sites"), count(rows, "functions"), count(rows, "classes"),
             count(rows, "literal"), count(rows, "computed"), count(rows, "labelled"),
             count(rows, "in_state"), count(rows, "in_state_function"), count(rows, "in_state_code"),
             count(rows, "in_class")))
    return 0


# --- the dump-free self-test -------------------------------------------------------------------------

def token(name, operands=b"", children=None, offset=0):
    return {"name": name, "opcode": 0, "operands": operands, "children": children or [],
            "offset": offset, "end": offset + 1}


def check():
    """Synthetic token trees: the four target shapes, the label, and the outer walk through a state.

    Only the classifier and the owner walk are testable this way, and they are where a bug would be: the
    corpus walk above is the same `dis_parse` every other census uses. The name table is two names long, so a
    `NameConst` pointing past it must answer an empty target rather than raise.
    """
    # The name table's entry 0 is the null name in every package of this build (`None`), so the synthetic
    # table spells it too: a `NameConst 0` is `GotoState('')`, not a state called None.
    names = ["None", "Mellee", "MelleeCharge"]
    good = []
    bad = []

    def want(got, expect, what):
        (good if got == expect else bad).append("%s: %r != %r" % (what, got, expect))

    one = token("GotoState", children=[token("NameConst", b"\x01"), token("EndFunctionParms")])
    want(site_of(one, names), ("literal", "Mellee", "", 1), "literal target")

    two = token("GotoState", children=[token("NameConst", b"\x02"), token("NameConst", b"\x01"),
                                       token("EndFunctionParms")])
    want(site_of(two, names), ("literal", "MelleeCharge", "Mellee", 2), "literal target and label")

    null = token("GotoState", children=[token("NameConst", b"\x00"), token("EndFunctionParms")])
    want(site_of(null, names), ("null", "", "", 1), "the null name leaves the state")

    null_label = token("GotoState", children=[token("NameConst", b"\x01"), token("NameConst", b"\x00"),
                                              token("EndFunctionParms")])
    want(site_of(null_label, names), ("literal", "Mellee", "", 2), "an index-0 label is no label")

    dyn = token("GotoState", children=[token("LocalVariable", b"\x03"), token("EndFunctionParms")])
    want(site_of(dyn, names)[0], "computed", "a computed target")

    dyn_label = token("GotoState", children=[token("NameConst", b"\x01"),
                                             token("NotEqual_NameName", children=[token("EndFunctionParms")]),
                                             token("EndFunctionParms")])
    want(site_of(dyn_label, names)[2], "computed", "a computed label")

    none = token("GotoState", children=[token("EndFunctionParms")])
    want(site_of(none, names), ("none", "", "", 0), "no argument")

    # 0x7f sets the continuation bit, so a one-byte "past the table" operand is not one: `40 01` is 64, and
    # the synthetic table is three names long.
    past = token("GotoState", children=[token("NameConst", b"\x40\x01"), token("EndFunctionParms")])
    want(site_of(past, names)[1], "", "an index past the name table")

    class Fake(object):
        def __init__(self, exports, names_):
            self.exports, self._names = exports, names_

        def name(self, i):
            return self._names[i]

        def class_name_of(self, e):
            return e["kind"]

    exports = [{"kind": "Class", "name": 2, "outer": 0},
               {"kind": "State", "name": 1, "outer": 1},
               {"kind": "Function", "name": 0, "outer": 2}]
    fake = Fake(exports, ["DoCharge", "MelleeCharge", "JaffaBotHeavy"])
    want(owner_of(fake, exports[2]), ("JaffaBotHeavy", "MelleeCharge"), "a function inside a state")
    exports2 = [{"kind": "Class", "name": 1, "outer": 0}, {"kind": "Function", "name": 0, "outer": 1}]
    fake2 = Fake(exports2, ["DoMellee", "BotEnemy"])
    want(owner_of(fake2, exports2[1]), ("BotEnemy", ""), "a class-level function")

    # The entry label belongs to the *target* state, so the resolution walks the class chain: `JaffaBotHeavy`
    # inherits `Hunt` from `BotEnemy`, and a state the chain does not declare answers None (a record with no
    # table, or no state at all -- the two the join reports apart only by the site's target kind).
    tabs = {("stargategame", "botenemy", "hunt"): ["Begin", "DoCharge"]}
    sups = {("stargategame", "jaffabotheavy"): ("stargategame", "botenemy")}
    want(table_for("StargateGame", "JaffaBotHeavy", "Hunt", tabs, sups), ["Begin", "DoCharge"],
         "an entry label resolves through the class chain")
    want(table_for("StargateGame", "JaffaBotHeavy", "Mellee", tabs, sups), None,
         "a state no chain class tables answers None")

    # A record-only state inherits its code from the state its export's own `Super` names -- UE2's state-level
    # inheritance, one level below the class chain. The timed `Mover` states are the corpus case.
    meta = {("engine", "mover", "standopentimed"): {"super": ("Mover", "OpenTimedMover"), "entries": []},
            ("engine", "mover", "opentimedmover"): {"super": None, "entries": [("Open", 0), ("Close", 103)]}}
    tabs2 = {("engine", "mover", "opentimedmover"): ["Open", "Close"]}
    want(resolve_table("Engine", "Mover", "StandOpenTimed", tabs2, {}, meta),
         (["Open", "Close"], "super:Mover.OpenTimedMover"),
         "a record state's table is its Super state's")
    want(resolve_table("Engine", "Mover", "StandOpenTimed", tabs2, {}),
         (None, ""), "without the export table the same state has no table")
    want(entry_point([("Open", 0), ("Close", 103)]), ("Open", 0),
         "the entry point is the entry at the first byte of code, not the name Begin")

    # The label join's text half is not the AI census: a class outside it is read on demand, so its states are
    # joined on the same terms instead of counted and dropped.
    gun_sites = [{"kind": "site", "package": "P", "class": "Gun", "state": "NormalFire", "function": "",
                  "scope": "state-code", "offset": "0073", "op": "GotoLabel", "target_kind": "literal",
                  "target": "WaitForRefire", "label": "", "args": "1"},
                 {"kind": "site", "package": "P", "class": "Turret", "state": "NormalFire", "function": "",
                  "scope": "state-code", "offset": "0082", "op": "GotoLabel", "target_kind": "literal",
                  "target": "Begin", "label": "", "args": "1"}]
    gun_text = {("P", "Gun", "NormalFire"): {"labels": {"Begin", "Refire"}, "jumps": ["WaitForRefire"], "count": 1},
                ("P", "Turret", "NormalFire"): {"labels": {"Begin", "Refire"}, "jumps": ["Begin"], "count": 1}}
    gun_rows = label_join(gun_sites, {}, {}, gun_text)
    gun_count = {r["key"]: r["count"] for r in gun_rows if r["kind"] == "census"}
    want(gun_count.get("extra_label_states"), 2, "a state outside the AI census is joined from its class text")
    want(gun_count.get("label_join_agree"), 2, "and it agrees with the build to the site")
    want(gun_count.get("label_build_only_states"), 0, "so no label site is left unjoined")

    label = token("GotoLabel", children=[token("NameConst", b"\x01"), token("EndFunctionParms")])
    payload_rows = site_rows("P", "C", "Mellee", "", "state-code", [label, one], names)
    want(len(payload_rows), 2, "a state payload's tokens are rows too")
    want((payload_rows[1]["scope"], payload_rows[1]["state"], payload_rows[1]["function"]),
         ("state-code", "Mellee", ""), "a state-code row names its state and no function")
    want(payload_rows[0]["op"], "GotoLabel", "the label token keeps its own opcode")

    rows = [{"kind": "site", "package": "P", "class": "C", "state": "Mellee", "function": "F",
             "scope": "function", "offset": "0000",
             "op": "GotoState", "target_kind": "literal", "target": "Mellee", "label": "", "args": "1"},
            {"kind": "site", "package": "P", "class": "C", "state": "", "function": "G", "scope": "function",
             "offset": "0004", "op": "GotoState", "target_kind": "computed", "target": "", "label": "",
             "args": "1"},
            {"kind": "census", "key": "sites", "count": "2", "note": ""}]
    want(render(rows).splitlines()[0].split(",")[0], "kind", "the CSV header")
    want(render(rows).splitlines()[0].split(",")[5], "scope", "the header carries the scope column")
    want(render(rows).count("\n"), 4, "one header and three rows")
    want("computed" in doc_block(rows), True, "the block quotes the computed count")
    for line in bad:
        print("FAIL state-entry --check: " + line)
    print("ok   state-entry --check: %d case(s) pass" % len(good))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--system", help="the dump's System directory")
    ap.add_argument("-o", "--out", help="write the CSV here")
    ap.add_argument("--write-doc", metavar="PATH", help="regenerate the page's block between its markers")
    ap.add_argument("--check-doc", metavar="PATH", help="fail if the page's block is stale (in `make ci`)")
    ap.add_argument("--from-csv", metavar="PATH", default=CSV_PATH,
                    help="with --check-doc and no --system: read this artifact instead")
    ap.add_argument("--report", action="store_true", help="print the census of the committed artifact")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; needs no dump")
    args = ap.parse_args()

    global DIS
    DIS = load_tool("uscript_dis", "uscript-dis.py")
    if args.check:
        return check()
    if args.report:
        rows = read_csv(args.from_csv)
        if not rows:
            sys.exit("state-entry: %s holds no rows" % args.from_csv)
        for r in rows:
            if r.get("kind") == "census":
                print("%-24s %6s  %s" % (r["key"], r["count"], r.get("note", "")))
        return 0
    if args.check_doc and not args.system:
        rows = read_csv(args.from_csv)
        if not rows:
            sys.exit("state-entry: %s holds no rows" % args.from_csv)
        return emit(rows, args)
    if not args.system:
        sys.exit("state-entry: pass --system <DUMP/System>")
    rows, stats, tables, meta = read_dump(args.system)
    if not rows:
        sys.exit("state-entry: no GotoState site under %s" % args.system)
    sites = [r for r in rows if r["kind"] == "site"]
    rows = rows + census(rows, stats)
    rows = rows + text_control(sites)
    # The AI census is the text half for its own subtree; every other class the build jumps in is read on demand,
    # so the label join no longer ends at the census boundary (PLAN 3.10, docs/systems/AI.md).
    wanted = {(r["package"], r["class"]) for r in sites if r["op"] == "GotoLabel" and r["target"]}
    text_states = class_text_states(args.system, wanted)
    rows = rows + label_join(sites, tables, meta, text_states)
    return emit(rows, args)


DIS = None
SC = None


if __name__ == "__main__":
    sys.exit(main())
