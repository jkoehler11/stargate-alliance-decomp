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

The rows are `site` (one per call) and `census` (the run-level counts the page quotes, so `--write-doc`,
`--report` and `--check-doc` are the same read). Nothing here publishes script: a row names a function and a
state, never a body.
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


def state_payloads(system, stats):
    """Yield (package name, package, state export, class export, token tree) for every state whose code walks.

    The class is the export's Outermost `Class`: `UState`'s payload belongs to the class that declares the state,
    and a state's functions are separate `Function` exports with the state as their outer -- the half `payloads()`
    already walks.
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
            payload = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            rec = sc.record(payload)
            if rec is None or rec["label_offset"] == sc.NO_TABLE:
                continue
            stats["state_with_code"] += 1
            got = sc.region(payload, table, rec["label_offset"])
            if got is None or got["start_by"] != "field":
                continue
            stats["state_walked"] += 1
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
    for package, pkg, exp, nodes in payloads(system, table, stats):
        if package not in tables:
            tables[package] = [pkg.name(i) for i in range(pkg.name_count)]
        cls, state = owner_of(pkg, exp)
        rows.extend(site_rows(package, cls, state, pkg.name(exp["name"]), "function", nodes, tables[package]))
    for package, pkg, exp, outer, nodes in state_payloads(system, stats):
        if package not in tables:
            tables[package] = [pkg.name(i) for i in range(pkg.name_count)]
        rows.extend(site_rows(package, pkg.name(outer["name"]), pkg.name(exp["name"]), "", "state-code",
                              nodes, tables[package]))
    return rows, stats


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
    rows, stats = read_dump(args.system)
    if not rows:
        sys.exit("state-entry: no GotoState site under %s" % args.system)
    rows = rows + census(rows, stats)
    rows = rows + text_control([r for r in rows if r["kind"] == "site"])
    return emit(rows, args)


DIS = None
SC = None


if __name__ == "__main__":
    sys.exit(main())
