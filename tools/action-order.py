#!/usr/bin/env python3
"""The order inside an `Actions` array: the index, and the five classes that move it (PLAN 5.2).

`docs/MAP_ACTION_WIRING.md` decoded a sequence's `Actions` property as a count followed by that many compact
export indices, and left one thing open in its own words: *which end of the order runs first* -- "a per-element
order is a T3D-level read, not a census". `docs/MAP_EVENT_TARGETS.md` then measured how many of those wired
actions sit in a sequence a tag arms, and left the rest on "a start the package does not carry".

Both halves are answerable from text the build already ships, not from bytes guessed at: every class in this
build carries its own UnrealScript as a `TextBuffer` export (`docs/PACKAGE_SCRIPT_TEXT.md`), and the walk is
four lines of it. `ScriptedSequence.SetActions` reads `Actions[C.ActionNum]`, `ScriptedTriggerController`
starts that counter at 0, and `ScriptedAction.ProceedToNextAction` adds one -- so **the array runs from index 0
upward**, and the array is the schedule.

The exception set is five classes, and each one says so in its own body: `ACTION_GotoAction` sets the counter to
an absolute `ActionNumber`, `ACTION_EndSection` closes a block (and loops it while an iteration counter runs),
`ACTION_IfRandomPct` and `ACTION_IfCondition` fall through or skip to the block's end, and `ACTION_LeaveSequence`
returns no script at all, which ends the run. `ProceedToSectionEnd` is the skip: it nests `StartsSection` /
`EndsSection` from the current index.

So this tool does two reads and joins them:

* **the rules**, class by class, from each class's own shipped text -- which classes move the counter, which
  open and close a block, which end the run, and (for a sequence) whether the class gates its element on
  `bValidForTrigger` as `ScriptedTrigger.ValidAction` does;
* **the arrays**, map by map -- every `Actions` payload in index order, the class of each element, and how far a
  straight run down that index can get before something ends it (a refused element, a `LeaveSequence`, or a
  `GotoAction` whose target is past the array).

    python3 tools/action-order.py DUMP --sweep -o inventory
    python3 tools/action-order.py --report
    python3 tools/action-order.py --write-doc docs/MAP_ACTION_ORDER.md
    python3 tools/action-order.py --check-doc docs/MAP_ACTION_ORDER.md
    python3 tools/action-order.py --check          # synthetic, no dump, writes nothing

`DUMP` is the game directory (`.../Stargate_SG-1_-_The_Alliance-2005-12-15`), because the rules live in
`System/*.u` and the arrays in `Maps/*.unr`. Nothing here prints class text: a rule row carries marker counts
and a boolean, which is structure, not source.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from ue2pkg import Package  # noqa: E402

ARTIFACT = os.path.join(ROOT, "inventory/action_order.csv")
DOC = os.path.join(ROOT, "docs/MAP_ACTION_ORDER.md")
CLASSES = os.path.join(ROOT, "inventory/classes.csv")
DOC_START = "<!-- generated:action-order -->"
DOC_END = "<!-- /generated:action-order -->"

FIELDS = ["kind", "name", "package", "role", "arrays", "elements", "reach", "short", "goto", "goto_in",
          "goto_out", "goto_default", "sections", "unbalanced", "ends_run", "leaves", "gated_arrays",
          "gated_elements", "refused", "unknown_gate", "none_slots", "matinee_arrays", "matinee_elements",
          "moves", "branches", "opens", "closes", "gate", "note"]

# The abstract heads of the family: a placement of one names no behaviour of its own.
ACTION_BASES = {"ScriptedAction", "LatentScriptedAction"}


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ps = _load("prop_stream", "prop-stream.py")
aw = _load("action_wiring", "action-wiring.py")
cd = _load("class_defaults", "class-defaults.py")


def class_supers(path=CLASSES):
    supers = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            supers[row["class"]] = row.get("super", "")
    return supers


def family(supers, root):
    out = set()
    for cls in supers:
        seen, cur = set(), cls
        while cur in supers and cur not in seen:
            seen.add(cur)
            if supers[cur] == root:
                out.add(cls)
                break
            cur = supers[cur]
    return out


def inherited(cls, known, supers):
    """The value a class inherits from the nearest ancestor that states one: (value, from_class)."""
    cur, seen = cls, set()
    while cur and cur not in seen:
        if cur in known:
            return known[cur], cur
        seen.add(cur)
        cur = supers.get(cur, "")
    return None, ""


def gate_value(pkg, exp, cls, declared_all, supers):
    """(True/False/None) for the class's own `bValidForTrigger` default, read off its defaults block.

    `class-defaults`' candidates make a misparse visible: only a parse with no contradicted tag is read, and a
    class whose parses disagree answers None rather than a guess.
    """
    cands, _data = cd.candidates(pkg, ps, exp, limit=None, kinds=cd.kinds_for_class(cls))
    seen = set()
    for count, start, names, trailer, bad in cands:
        if bad or "bValidForTrigger" not in names:
            continue
        blk = pkg.data[exp["offset"]:exp["offset"] + exp["size"]][start:]
        try:
            props, _end, reason = ps.read_block(blk, pkg, True)
        except Exception:                                  # noqa: BLE001 - a short payload is a skip
            continue
        if reason != "None":
            continue
        for p in props:
            if p["name"] == "bValidForTrigger" and "value" in p:
                seen.add(bool(p["value"]))
    return seen.pop() if len(seen) == 1 else None


def text_rules(text):
    """Marker booleans for one class body. Structure only -- no text leaves this function."""
    moves = "ProceedToNextAction" in text
    goto = "ActionNumber" in text and "C.ActionNum =" in text
    branch = "ProceedToSectionEnd" in text
    leaves = "function ScriptedSequence GetScript" in text and "return None" in text
    opens = "function bool StartsSection()" in text and "return true" in text
    closes = "function bool EndsSection()" in text and "return true" in text
    gate = "bValidForTrigger" in text
    return dict(moves=moves, goto=goto, branches=branch, leaves=leaves, opens=opens, closes=closes, gate=gate)


def class_rules(system_dir, supers, verbose=True):
    """Every action class and every sequence class, reduced to its own rules.

    A sequence class is one whose body walks `Actions[C.ActionNum]`; the action family comes from the committed
    class tree, not from a name prefix (the build places `Action_PLAYANIM`).
    """
    actions = family(supers, "ScriptedAction") | ACTION_BASES
    pst = _load("pkg_script_text", "pkg-script-text.py")
    declared_all = cd.declared_properties()
    rows = {}
    for fname in sorted(os.listdir(system_dir)):
        if not fname.lower().endswith(".u"):
            continue
        pkgname = fname[:-2]
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            cls = pkg.name(exp["name"])
            text = pst.script_text(pkg, exp)
            if text is None:
                continue
            is_action = cls in actions
            is_holder = "function SetActions" in text or "function bool ValidAction" in text
            if not (is_action or is_holder):
                continue
            rule = text_rules(text)
            rule.update(kind="class", name=cls, package=pkgname,
                        role="holder" if is_holder else "action", note="")
            if is_action:
                own = gate_value(pkg, exp, cls, declared_all, supers)
                rule["own_gate"] = own
            rows[cls] = rule
    # the value a class actually runs with is the nearest stated one up its chain
    stated = {c: r["own_gate"] for c, r in rows.items() if r.get("own_gate") is not None}
    for cls, r in rows.items():
        value, source = inherited(cls, stated, supers)
        r["gate_value"] = value
        r["gate_source"] = source
    if verbose:
        print("rules: %d classes (%d holders, %d actions)" %
              (len(rows), sum(1 for r in rows.values() if r["role"] == "holder"),
               sum(1 for r in rows.values() if r["role"] == "action")))
    return rows


def read_blocks(pkg, skip_classes=("Class", "Function", "State", "ScriptStruct", "Const")):
    """Per export (1-based): the ordered `Actions` payload and the `ActionNumber` a goto carries."""
    blocks = {}
    for i, exp in enumerate(pkg.exports):
        data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
        if not data or pkg.class_name_of(exp) in skip_classes:
            continue
        found = ps.find_block(data, pkg, min_props=1)
        if not found:
            continue
        _count, start, _props = found
        props, _end, _reason = ps.read_block(data[start:], pkg, True)
        array, target = None, None
        for p in props:
            if p["name"] == "Actions" and p["type"] == "Array":
                raw = data[start + p["value_offset"]:start + p["value_offset"] + p["size"]]
                array = aw.decode_array_refs(raw)
            elif p["name"] == "ActionNumber" and p["type"] == "Int" and "value" in p:
                target = p["value"]
        if array is not None or target is not None:
            blocks[i + 1] = {"actions": array, "target": target}
    return blocks


def walk(refs, classes, rules, gated_holder=False, values=None, targets=None):
    """One array in index order -> (reach, stats).

    `reach` is how many elements a straight run down the index gets through: the first element that ends the
    run is included (it is reached, then ends it), everything past it is not. A run ends at an element whose
    class a gating holder refuses (`ScriptedTrigger.ValidAction` reads the element's own `bValidForTrigger`
    default), at a `LeaveSequence` (`GetScript` answers None), or at a `GotoAction` whose own `ActionNumber` is
    past the array's last index (`SetActions` then has no action to start). An in-range goto is not a cut -- it is a jump, counted on its own, and it makes this
    count an upper bound on what runs (a forward jump skips) and a lower bound on a loop (a backward one).
    """
    values = values or {}
    targets = targets or {}
    st = dict(n=len(refs), reach=0, ends_run=0, leaves=0, goto_in=0, goto_out=0, goto_default=0,
              refused=0, unknown_gate=0, sections=0, unbalanced=0, none_slots=0)
    nesting, depth_ok = 0, True
    cut = None
    for index, ref in enumerate(refs):
        cls = classes[ref - 1] if 0 < ref <= len(classes) else ""
        rule = rules.get(cls) or {}
        if rule.get("opens"):
            nesting += 1
        if rule.get("closes"):
            nesting -= 1
            if nesting < 0:
                depth_ok = False
        if cut is not None:
            continue
        if ref == 0:
            # an empty slot: `SetActions` reads `Actions[N] == None`, logs it and returns, so the run ends
            st["none_slots"] += 1
            cut = index + 1
            continue
        if rule.get("leaves"):
            st["leaves"] += 1
            cut = index + 1
            continue
        if rule.get("goto"):
            target = targets.get(ref)
            if target is None:
                # the placement states no `ActionNumber`, so the class default (0) applies: a jump to index 0
                st["goto_default"] += 1
                st["goto_in"] += 1
            elif 0 <= target < len(refs):
                st["goto_in"] += 1
            else:
                st["goto_out"] += 1
                st["ends_run"] += 1
                cut = index + 1
                continue
        if gated_holder:
            value = values.get(cls)
            if value is False:
                st["refused"] += 1
                cut = index + 1
                continue
            if value is None:
                st["unknown_gate"] += 1
        st["reach"] = index + 1
    st["sections"] = nesting
    st["unbalanced"] = 0 if depth_ok and nesting == 0 else 1
    return (cut if cut is not None else st["n"]), st


def measure(entries, blocks, rules, gated=frozenset(), values=None, targets=None,
            sequence_cls=frozenset(), sequence_only=True):
    """One map -> (row, per-holder stats, goto counts). No package needed, so `--check` can drive it."""
    classes = [e["class"] for e in entries]
    values = values or {}
    targets = targets or {}
    row = dict(kind="map", name="", package="", role="", arrays=0, elements=0, reach=0, short=0, goto=0,
               goto_in=0, goto_out=0, goto_default=0, sections=0, unbalanced=0, ends_run=0, leaves=0,
               gated_arrays=0, gated_elements=0, refused=0, unknown_gate=0, matinee_arrays=0,
               matinee_elements=0, none_slots=0, note="")
    per_holder = collections.defaultdict(lambda: collections.Counter())
    per_class = collections.Counter()
    for idx in sorted(blocks):
        blk = blocks[idx]
        if blk.get("actions") is None:
            continue
        count, refs = blk["actions"]
        holder = classes[idx - 1] if 0 < idx <= len(classes) else ""
        if sequence_only and holder not in sequence_cls:
            row["matinee_arrays"] += 1
            row["matinee_elements"] += len(refs)
            per_holder["(scene manager)"]["arrays"] += 1
            per_holder["(scene manager)"]["elements"] += len(refs)
            continue
        is_gated = holder in gated
        st_holder = per_holder[holder]
        st_holder["arrays"] += 1
        st_holder["elements"] += len(refs)
        for ref in refs:
            if 0 < ref <= len(classes):
                per_class[classes[ref - 1]] += 1
        if not refs:
            continue
        row["arrays"] += 1
        row["elements"] += len(refs)
        if is_gated:
            row["gated_arrays"] += 1
            row["gated_elements"] += len(refs)
            st_holder["gated_arrays"] += 1
            st_holder["gated_elements"] += len(refs)
        reach, st = walk(refs, classes, rules, is_gated, values, targets)
        row["reach"] += reach
        st_holder["reach"] += reach
        for key in ("goto_in", "goto_out", "goto_default", "ends_run", "leaves", "refused", "unknown_gate",
                    "sections", "unbalanced", "none_slots"):
            row[key] += st[key]
            st_holder[key] += st[key]
        row["goto"] += st["goto_in"] + st["goto_out"]
        st_holder["goto"] += st["goto_in"] + st["goto_out"]
        if reach < len(refs):
            row["short"] += 1
            st_holder["short"] += 1
    return row, per_holder, per_class


def sweep(dump, out, classes_path=CLASSES, only=None):
    maps_dir = os.path.join(dump, "Maps")
    system_dir = os.path.join(dump, "System")
    if not os.path.isdir(maps_dir):
        sys.exit("no Maps/ under %s" % dump)
    supers = class_supers(classes_path)
    rules = class_rules(system_dir, supers)
    # a gate is inherited: a subclass of a gating sequence applies it too
    marks = {c for c, r in rules.items() if r["role"] == "holder" and r.get("gate")}
    sequence_cls = family(supers, "ScriptedSequence") | {"ScriptedSequence"}
    gated = set(marks)
    for cls in supers:
        cur, seen = cls, set()
        while cur in supers and cur not in seen:
            seen.add(cur)
            if cur in marks:
                gated.add(cls)
                break
            cur = supers[cur]
    values = {c: r.get("gate_value") for c, r in rules.items() if r["role"] == "action"}
    rows = []
    per_holder = collections.defaultdict(collections.Counter)
    for name in sorted(os.listdir(maps_dir)):
        if not name.lower().endswith(".unr"):
            continue
        label = name[:-4]
        if only and only.lower() not in label.lower():
            continue
        try:
            pkg = Package(os.path.join(maps_dir, name))
            pkg.read_names()
            pkg.read_imports()
            pkg.read_exports()
        except Exception as exc:                           # an unreadable map is not a zero
            rows.append(dict(kind="map", name=label, package="", role="", arrays="", elements="", reach="",
                             short="", goto="", goto_in="", goto_out="", goto_unknown="", sections="",
                             unbalanced="", ends_run="", leaves="", gated_arrays="", gated_elements="",
                             refused="", unknown_gate="", note="unreadable: %s" % exc))
            continue
        entries = [{"class": pkg.class_name_of(e), "name": pkg.name(e["name"]), "outer": e["outer"]}
                   for e in pkg.exports]
        blocks = read_blocks(pkg)
        targets = {idx: blocks[idx]["target"] for idx in blocks if blocks[idx].get("target") is not None}
        row, holders, _per_class = measure(entries, blocks, rules, gated, values, targets, sequence_cls)
        row["name"] = label
        rows.append(row)
        for cls, info in holders.items():
            per_holder[cls].update(info)
        print("%-28s arrays=%-4s elements=%-5s reach=%-5s short=%-3s goto=%-3s/%-3s unbalanced=%-3s" %
              (label, row["arrays"], row["elements"], row["reach"], row["short"], row["goto_in"],
               row["goto_out"], row["unbalanced"]), flush=True)
    for cls in sorted(per_holder, key=lambda c: (-per_holder[c]["elements"], c)):
        r = dict(kind="holder", name=cls, package="", role="holder", note="")
        r.update({k: per_holder[cls].get(k, 0) for k in
                  ("arrays", "elements", "reach", "short", "goto", "goto_in", "goto_out", "goto_default",
                   "sections", "unbalanced", "ends_run", "leaves", "gated_arrays", "gated_elements", "refused",
                   "unknown_gate", "none_slots", "matinee_arrays", "matinee_elements")})
        rows.append(r)
    for cls in sorted(rules, key=lambda c: (rules[c]["role"], c)):
        rule = rules[cls]
        rows.append(dict(kind="class", name=cls, package=rule["package"], role=rule["role"], moves=int(rule["moves"]),
                         branches=int(rule["branches"]), opens=int(rule["opens"]), closes=int(rule["closes"]),
                         gate=int(rule["gate"]), goto=int(rule["goto"]), leaves=int(rule["leaves"]),
                         note="" if rule["role"] == "holder" else
                         "gate_value=%s;gate_source=%s" %
                         ("?" if rule.get("gate_value") is None else int(rule["gate_value"]),
                          rule.get("gate_source", ""))))
    census(rows)
    if out:
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, "action_order.csv")
        write_artifact(rows, path)
        print("%s: %d rows" % (path, len(rows)))
    return rows


def census(rows):
    keys = ("arrays", "elements", "reach", "short", "goto", "goto_in", "goto_out", "goto_default", "sections",
            "unbalanced", "ends_run", "leaves", "gated_arrays", "gated_elements", "refused", "unknown_gate",
            "none_slots", "matinee_arrays", "matinee_elements")
    maps = [r for r in rows if r["kind"] == "map" and r["elements"] != ""]
    total = collections.Counter()
    for r in maps:
        for key in keys:
            total[key] += int(r.get(key, 0) or 0)
    classes = [r for r in rows if r["kind"] == "class"]
    row = dict(kind="census", name="corpus", package="", role="")
    row.update({k: total[k] for k in keys})
    row["census"] = ""
    row["note"] = "maps=%d;unreadable=%d;holder_classes=%d;classes=%d;movers=%d;leaves_classes=%d;gated_classes=%d" % (
        len(maps), len([r for r in rows if r["kind"] == "map" and r["elements"] == ""]),
        len([r for r in rows if r["kind"] == "holder"]), len(classes),
        len([r for r in classes if r["role"] == "action" and r["moves"]]),
        len([r for r in classes if r["leaves"]]), len([r for r in classes if r["gate"]]))
    rows.append(row)
    return rows


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def note_values(row):
    out = {}
    for part in row.get("note", "").split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def num(row, key):
    """A count as an int from either a live row or a CSV round-trip (a missing column reads as "")."""
    value = row.get(key, 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def render(rows):
    c = [r for r in rows if r["kind"] == "census"][0]
    vals = note_values(c)
    classes = [r for r in rows if r["kind"] == "class"]
    movers = sorted(r["name"] for r in classes if r["role"] == "action" and num(r, "moves"))
    overrides = [m for m in movers if m != "ScriptedAction"]
    gate_false = [r["name"] for r in classes if "gate_value=0" in r["note"]]
    leavers = sorted(r["name"] for r in classes if num(r, "leaves"))
    gated = sorted(r["name"] for r in classes if num(r, "gate") and r["role"] == "holder")
    holders = [r for r in rows if r["kind"] == "holder" and r["name"] != "(scene manager)"]
    scene = [r for r in rows if r["kind"] == "holder" and r["name"] == "(scene manager)"]
    elements = num(c, "elements")
    reach = num(c, "reach")
    gotos = num(c, "goto")
    out = []
    out.append("**An `Actions` array runs from index 0 upward, and %d of the %d elements a map's sequences wire are "
               "reached by that straight run; the five classes that move the counter are the only branches in the "
               "schedule.**" % (reach, elements))
    out.append("")
    out.append("The order is not inferred from the bytes: it is the build's own walk, shipped as each class's own "
               "`ScriptText` (`docs/PACKAGE_SCRIPT_TEXT.md`). `ScriptedSequence.SetActions` reads "
               "`Actions[C.ActionNum]`; `ScriptedTriggerController.InitializeFor` sets that counter to 0 and calls "
               "`SetActions`; `ScriptedAction.ProceedToNextAction` is `C.ActionNum += 1`. So the array is the "
               "schedule, index 0 first, and the only thing that changes the order is a class that overrides that "
               "function.")
    out.append("")
    out.append("| measure | answer |")
    out.append("|---|---|")
    out.append("| maps read | **%s** |" % vals.get("maps", "?"))
    out.append("| classes whose body states a rule | **%s** (%s sequence, %s action) |"
               % (vals.get("classes", "?"),
                  sum(1 for r in classes if r["role"] == "holder"),
                  sum(1 for r in classes if r["role"] == "action")))
    out.append("| sequence classes carrying an `Actions` array in a map | **%d** |" % len(holders))
    out.append("| arrays those sequences carry | **%s** |" % c["arrays"])
    out.append("| elements wired by them | **%s** slots, of which **%s** are `None` (an empty slot, which ends "
               "the run where it sits) |" % (c["elements"], c["none_slots"]))
    out.append("| elements a straight index run reaches | **%s** |" % c["reach"])
    out.append("| slots naming an action (`elements - None`) | **%s** |" % (elements - num(c, "none_slots")))
    out.append("| arrays a straight run cannot finish | **%s** |" % c["short"])
    out.append("| `ACTION_GotoAction` elements wired | **%s** (%s target inside their own array, %s outside, "
               "%s state no target at all) |"
               % (gotos, c["goto_in"], c["goto_out"], c["goto_default"]))
    out.append("| arrays whose section nesting does not close | **%s** |" % c["unbalanced"])
    out.append("| elements reached by `ACTION_LeaveSequence` (which ends the run) | **%s** |" % c["leaves"])
    out.append("| elements a gating sequence refuses | **%s** |" % c["refused"])
    out.append("| scene-manager (camera) arrays, counted apart | **%s** arrays / **%s** elements |"
               % (c["matinee_arrays"], c["matinee_elements"]))
    out.append("")
    out.append("**Five classes move the counter, and each says how in its own body.**")
    out.append("")
    out.append("| class | what it does to the index |")
    out.append("|---|---|")
    out.append("| `ACTION_GotoAction` | sets it to an absolute `ActionNumber` (`Max(0, ActionNumber)`); a target "
               "behind the current index is a loop, and a placement that states none takes the class default 0 |")
    out.append("| `ACTION_EndSection` | repeats the block from `IterationSectionStart` while `IterationCounter` "
               "runs, else adds one |")
    out.append("| `ACTION_IfCondition` | adds one; if its `TriggeredCondition` actor is not enabled, calls "
               "`ProceedToSectionEnd` |")
    out.append("| `ACTION_IfProximity` | adds one; the same skip on a distance test |")
    out.append("| `ACTION_IfRandomPct` | adds one; the same skip with probability `1 - Probability` |")
    out.append("")
    out.append("`ProceedToSectionEnd` is the skip itself: it walks forward counting `StartsSection` / `EndsSection` "
               "and stops where the nesting goes negative, so a conditional jumps to the end of the block it opened. "
               "`ACTION_LeaveSequence.GetScript` answers None, which unmakes the current script and ends the run "
               "outright. The base `ScriptedAction` states the increment; the classes that override it are %s. "
               "Classes that end the run: %s. Classes that gate an element: %s."
               % (", ".join("`%s`" % m for m in overrides) or "none",
                  ", ".join("`%s`" % m for m in leavers) or "none",
                  ", ".join("`%s`" % m for m in gated) or "none"))
    out.append("")
    out.append("**The gate is per class, not per sequence.** `ScriptedTrigger` overrides `ValidAction` to read the "
               "element's own `bValidForTrigger` default, so a trigger runs the elements its classes accept and "
               "stops at the first one they do not -- `SetActions` warns `NOT VALID`, clears the current action and "
               "returns. The base `ScriptedAction` states the default as true and %d of the family state it "
               "false, so the gate is a class table, not a placement field. A sequence that is not a trigger has "
               "no `ValidAction` override at all and runs whatever the array names." % len(gate_false))
    out.append("")
    out.append("| sequence class | arrays | elements | reached | short | goto in / out / default | unbalanced |")
    out.append("|---|---|---|---|---|---|---|")
    for r in holders:
        out.append("| `%s` | %s | %s | %s | %s | %s / %s / %s | %s |"
                   % (r["name"], r["arrays"], r["elements"], r["reach"], r["short"], r["goto_in"], r["goto_out"],
                      r["goto_default"], r["unbalanced"]))
    out.append("")
    if scene:
        out.append("The scene manager's own `Actions` array (camera moves, `MatAction` elements) is counted apart: "
                   "**%s arrays / %s elements**, which are not scripted actions and are not part of the walk above."
                   % (scene[0]["arrays"], scene[0]["elements"]))
        out.append("")
    return "\n".join(out) + "\n"


def splice(path, block):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if DOC_START not in text or DOC_END not in text:
        sys.exit("markers not found in %s" % path)
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + "\n" + DOC_END + tail


def write_doc(path, rows):
    new = splice(path, render(rows))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new)
    print("wrote %s" % path)


def check_doc(path, rows):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if DOC_START not in text or DOC_END not in text:
        sys.exit("markers not found in %s" % path)
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    if head + DOC_START + "\n" + render(rows) + "\n" + DOC_END + tail != text:
        sys.exit("%s is stale: run --write-doc" % path)
    print("%s matches the artifact" % path)


def report(path=ARTIFACT):
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    sys.stdout.write(render(rows))


def check():
    """Synthetic bodies only: the walk, the three ways a run ends, and the section balance."""
    rules = {
        "ScriptedAction": dict(moves=True),
        "ACTION_MoveToPoint": dict(moves=False),
        "ACTION_GotoAction": dict(goto=True),
        "ACTION_IfCondition": dict(opens=True),
        "ACTION_EndSection": dict(closes=True),
        "ACTION_LeaveSequence": dict(leaves=True),
        "ScriptedTrigger": dict(gate=True),
        "ACTION_Run": dict(gate=True),
    }
    classes = ["ScriptedTrigger", "ACTION_Run", "ACTION_MoveToPoint", "ACTION_GotoAction", "ACTION_IfCondition",
               "ACTION_EndSection", "ACTION_LeaveSequence"]
    gated = {"ScriptedTrigger"}
    values = {"ACTION_Run": False, "ACTION_MoveToPoint": True}
    entries = [{"class": c, "name": c, "outer": 0} for c in classes]

    # 1. the run is the index, and a refused element ends it (index 1 is refused, index 0 reached)
    blocks = {1: {"actions": (4, [3, 2, 2, 3]), "target": None}}
    row, holders, _ = measure(entries, blocks, rules, gated, values, {}, {"ScriptedTrigger"})
    assert row["elements"] == 4, row["elements"]
    assert row["reach"] == 2, row["reach"]
    assert row["short"] == 1 and row["refused"] == 1, row

    # 2. an ungated holder runs the whole array in order
    blocks = {1: {"actions": (3, [3, 3, 2]), "target": None}}
    row, _h, _c = measure(entries, blocks, rules, gated, values, {}, {"ScriptedTrigger"})
    assert row["reach"] == 3 and row["short"] == 0, row

    # 3. ACTION_LeaveSequence is reached and then ends the run
    blocks = {1: {"actions": (3, [3, 7, 2]), "target": None}}
    row, _h, _c = measure(entries, blocks, rules, gated, values, {}, {"ScriptedTrigger"})
    assert row["reach"] == 2 and row["leaves"] == 1, row

    # 4. a goto's target is read from its own export, and one past the array ends the run there
    blocks = {1: {"actions": (3, [3, 4, 2]), "target": None}, 4: {"actions": None, "target": 9}}
    row, _h, _c = measure(entries, blocks, rules, gated, values, {4: 9}, {"ScriptedTrigger"})
    assert row["goto"] == 1 and row["goto_out"] == 1, row
    blocks = {1: {"actions": (3, [3, 4, 2]), "target": None}, 4: {"actions": None, "target": 0}}
    row, _h, _c = measure(entries, blocks, rules, gated, values, {4: 0}, {"ScriptedTrigger"})
    assert row["goto_in"] == 1 and row["goto_out"] == 0, row

    # 5. section nesting: a close with nothing open is unbalanced, a balanced pair is not
    blocks = {1: {"actions": (2, [5, 6]), "target": None}}
    row, _h, _c = measure(entries, blocks, rules, gated, values, {}, {"ScriptedTrigger"})
    assert row["unbalanced"] == 0, row
    blocks = {1: {"actions": (1, [6]), "target": None}}
    row, _h, _c = measure(entries, blocks, rules, gated, values, {}, {"ScriptedTrigger"})
    assert row["unbalanced"] == 1, row

    # 6. the rules come out of a body by its markers, and a class that states no gate inherits its parent's
    body = "function ProceedToNextAction(ScriptedController C)\n{\n\tC.ActionNum += 1;\n}\n"
    assert text_rules(body)["moves"] and not text_rules(body)["goto"], text_rules(body)
    supers = {"ACTION_Child": "ScriptedAction", "ScriptedAction": "Object"}
    value, source = inherited("ACTION_Child", {"ScriptedAction": True}, supers)
    assert value is True and source == "ScriptedAction", (value, source)

    # 7. the artifact round-trips through the CSV header both renderings read
    rows = [dict(kind="map", name="M00", elements=4, reach=2, short=1, note=""),
            dict(kind="census", name="corpus", elements=4, reach=2, short=1,
                 note="maps=1;unreadable=0;holder_classes=1;classes=1;movers=0;leaves_classes=0;gated_classes=0"),
            dict(kind="class", name="ScriptedAction", role="action", moves=1, note="gate_value=1;gate_source=")]
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "action_order.csv")
        write_artifact(rows, path)
        back = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
        assert [r["name"] for r in back] == ["M00", "corpus", "ScriptedAction"], back
        assert set(back[0]) == set(FIELDS), set(back[0]) ^ set(FIELDS)
        page = os.path.join(tmp, "doc.md")
        with open(page, "w", encoding="utf-8") as fh:
            fh.write("# page\n\n" + DOC_START + "\nold\n" + DOC_END + "\n")
        write_doc(page, back)
        check_doc(page, back)
    finally:
        for name in os.listdir(tmp):
            os.unlink(os.path.join(tmp, name))
        os.rmdir(tmp)
    print("ok")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump", nargs="?", help="the game directory (holds Maps/ and System/)")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--only", help="one map label substring")
    ap.add_argument("-o", "--out")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check()
    if args.report:
        report()
        return 0
    if args.write_doc or args.check_doc:
        with open(ARTIFACT, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if args.write_doc:
            write_doc(args.write_doc, rows)
        else:
            check_doc(args.check_doc, rows)
        return 0
    if args.sweep:
        if not args.dump:
            sys.exit("--sweep needs the game directory")
        sweep(args.dump, args.out, only=args.only)
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
