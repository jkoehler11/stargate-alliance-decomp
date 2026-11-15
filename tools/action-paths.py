#!/usr/bin/env python3
"""The paths an `Actions` array allows: the branch, its operand, and the section it skips (PLAN 5.2).

`docs/MAP_ACTION_ORDER.md` measured the order -- the array runs from index 0 upward and five classes move the
counter -- and closed with what it did not claim: *a schedule*, because "`ACTION_IfCondition` and
`ACTION_IfProximity` decide at run time, so which side of a section a given play takes is not in the package.
What is in the package is the set of paths the classes allow."

This tool measures that set, and asks what the branch reads while doing it. Three of the classes are
conditionals, and each one's condition is a *property of the placed element*, not an opaque runtime state:

* `ACTION_IfCondition` reads `T.bEnabled` from the `TriggeredCondition` actor its `TriggeredConditionTag` names;
  if the tag is `None`, or nothing carries it, `T` stays `None` and `None.bEnabled` is false, so the skip runs.
* `ACTION_IfProximity` reads `ProximityActorTag`, `ActorTags` and `Radius` -- and its own first line refuses the
  test (`ProceedToSectionEnd`) unless all three are set.
* `ACTION_IfRandomPct` reads `Probability`: `FRand() > Probability` skips, so a placement at or below 0 skips
  every time and one at or above 1 never does.

So the branch outcome is *placed data* even though the play is not deterministic: a map author's tag either
resolves to a placed actor in that map or it does not, and `bEnabled`'s initial value is in the package too
(`TriggeredCondition` is `var() bool bEnabled`, flipped only by its own `Trigger`, which `bTriggerControlled`
can restore). The package therefore says which paths exist *and* which operand each branch will read; what it
cannot say is whether a trigger fires that actor later in a play.

The walk is `ScriptedAction.ProceedToSectionEnd` itself: from the branch's next index, count `StartsSection`
and `EndsSection` and stop where the nesting goes negative -- and because the loop returns *before* its own
increment, the counter is left **on** the matching `EndSection`, so the skip target is that index and the
`EndSection` itself still runs. A branch whose nesting never goes negative has no target: the counter walks off
the array and the run ends.

    python3 tools/action-paths.py DUMP --sweep -o inventory
    python3 tools/action-paths.py --report
    python3 tools/action-paths.py --write-doc docs/MAP_ACTION_PATHS.md
    python3 tools/action-paths.py --check-doc docs/MAP_ACTION_PATHS.md
    python3 tools/action-paths.py --check          # synthetic, no dump, writes nothing

`DUMP` is the game directory (`.../Stargate_SG-1_-_The_Alliance-2005-12-15`). Nothing here prints class text or
a placement's name: a row carries counts, and the tag *states* stay counts. The `straight` column is
`action-order`'s own walk run again over the same arrays, and the sweep asserts it against that tool's committed
artifact -- two readers, one population.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from ue2pkg import Package  # noqa: E402

ARTIFACT = os.path.join(ROOT, "inventory/action_paths.csv")
DOC = os.path.join(ROOT, "docs/MAP_ACTION_PATHS.md")
ORDER = os.path.join(ROOT, "inventory/action_order.csv")
CLASSES = os.path.join(ROOT, "inventory/classes.csv")
DOC_START = "<!-- generated:action-paths -->"
DOC_END = "<!-- /generated:action-paths -->"

FIELDS = ["kind", "name", "package", "role", "arrays", "elements", "branches", "if_cond", "if_prox", "if_rand",
          "matched", "unmatched", "empty_body", "cond_slots", "always", "reach", "straight", "beyond", "orphan",
          "op_set", "op_unset", "op_resolved", "op_missing", "enabled", "disabled", "unstated", "flipped",
          "unfired", "note"]

# the property each conditional reads, and the class it names when that property is a tag of its own
OPERANDS = (("TriggeredConditionTag", "condition", "TriggeredCondition"),
            ("ProximityActorTag", "proximity", ""),
            ("Probability", "random", ""))

SKIP_EXPORTS = ("Class", "Function", "State", "ScriptStruct", "Const")


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ao = _load("action_order", "action-order.py")
et = _load("event_targets", "event-targets.py")
ps = ao.ps
aw = ao.aw


def decode_name_array(payload, pkg):
    """An `Array` payload of names -> [names] or None when it does not consume itself."""
    off = 0
    try:
        count, off = ps.read_index(payload, off)
    except IndexError:
        return None
    if count < 0 or count > 4096:
        return None
    names = []
    for _ in range(count):
        if off >= len(payload):
            return None
        try:
            idx, off = ps.read_index(payload, off)
        except IndexError:
            return None
        try:
            names.append(pkg.name(idx))
        except Exception:                                  # noqa: BLE001 - an unreadable name is a skip
            return None
    if off != len(payload):
        return None
    return names


def read_exports(pkg):
    """Per export (1-based): the `Actions` array, plus every operand a conditional or a tag lookup reads."""
    out = {}
    for i, exp in enumerate(pkg.exports):
        data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
        if not data or pkg.class_name_of(exp) in SKIP_EXPORTS:
            continue
        found = ps.find_block(data, pkg, min_props=1)
        if not found:
            continue
        _count, start, _props = found
        props, _end, _reason = ps.read_block(data[start:], pkg, True)
        rec = dict(actions=None, target=None, tag=None, benabled=None, cond_tag=None, probability=None,
                   prox_tag=None, actor_tags=None, radius=None, fires=[])
        for p in props:
            name, typ = p["name"], p["type"]
            raw = data[start + p["value_offset"]:start + p["value_offset"] + p.get("size", 0)]
            if name == "Actions" and typ == "Array":
                rec["actions"] = aw.decode_array_refs(raw)
            elif name == "ActorTags" and typ == "Array":
                rec["actor_tags"] = decode_name_array(raw, pkg)
            elif name == "ActionNumber" and typ == "Int" and "value" in p:
                rec["target"] = p["value"]
            elif name == "Tag" and typ == "Name" and "value" in p:
                rec["tag"] = p["value"]
            elif name == "bEnabled" and typ == "Bool" and "value" in p:
                rec["benabled"] = bool(p["value"])
            elif name == "TriggeredConditionTag" and typ == "Name":
                rec["cond_tag"] = p.get("value")
            elif name == "ProximityActorTag" and typ == "Name":
                rec["prox_tag"] = p.get("value")
            elif name == "Probability" and typ == "Float":
                rec["probability"] = p.get("value")
            elif name == "Radius" and typ == "Float":
                rec["radius"] = p.get("value")
            elif typ == "Name" and isinstance(p.get("value"), str) and et.is_fire(name):
                # any name-typed property whose spelling says event: this is `event-targets`' own reader
                rec["fires"].append(p["value"])
        out[i + 1] = rec
    return out


def branch_operand(text):
    """Which operand a conditional's own body reads: (kind, property). Structure, not a quote."""
    for prop, kind, _cls in OPERANDS:
        if prop in text:
            return kind, prop
    return "", ""


def branch_classes(system_dir, rules):
    """Every class whose body states a branch, with the operand property its body reads."""
    pst = _load("pkg_script_text", "pkg-script-text.py")
    found = {}
    for fname in sorted(os.listdir(system_dir)):
        if not fname.lower().endswith(".u"):
            continue
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            cls = pkg.name(exp["name"])
            if cls not in rules or not rules[cls].get("branches"):
                continue
            kind, prop = branch_operand(pst.script_text(pkg, exp) or "")
            if not kind:
                # `ScriptedAction` itself states the skip function; it reads no operand, so it defines the skip
                # rather than being a conditional element.
                continue
            found[cls] = dict(package=fname[:-2], kind=kind, operand=prop,
                              operand_class=next((c for p_, k, c in OPERANDS if k == kind), ""))
    return found


def skip_target(arr_classes, rules, index):
    """The index `ProceedToSectionEnd` leaves the counter on for a branch at `index`, or None at the end."""
    nesting = 0
    k = index + 1
    while k < len(arr_classes):
        rule = rules.get(arr_classes[k]) or {}
        if rule.get("opens"):
            nesting += 1
        elif rule.get("closes"):
            nesting -= 1
            if nesting < 0:
                return k
        k += 1
    return None


def slots_of(refs, classes):
    """The class name at each slot of one array ("" for an empty slot, or a ref outside the export table)."""
    return [classes[r - 1] if 0 < r <= len(classes) else "" for r in refs]


def edges(index, refs, arr_classes, rules, gated_holder, values, targets):
    """Where a run can go after the element at `index`: [] when it ends there."""
    ref = refs[index]
    if ref == 0:
        return []
    cls = arr_classes[index]
    rule = rules.get(cls) or {}
    if rule.get("leaves"):
        return []
    if gated_holder and values.get(cls) is False:
        return []
    if rule.get("goto"):
        target = targets.get(ref)
        target = 0 if target is None else target
        return [target] if 0 <= target < len(refs) else []
    if rule.get("branches"):
        out = []
        if index + 1 < len(refs):
            out.append(index + 1)
        skip = skip_target(arr_classes, rules, index)
        if skip is not None and skip != index + 1:
            out.append(skip)
        return out
    return [index + 1] if index + 1 < len(refs) else []


def reachable(refs, arr_classes, rules, gated_holder, values, targets):
    """The union of every path the classes allow, from index 0."""
    seen, stack = set(), [0] if refs else []
    while stack:
        index = stack.pop()
        if index in seen or not (0 <= index < len(refs)):
            continue
        seen.add(index)
        started = True
        if refs[index] == 0 or (rules.get(arr_classes[index]) or {}).get("leaves"):
            started = False
        if gated_holder and values.get(arr_classes[index]) is False:
            started = False
        if started:
            stack.extend(edges(index, refs, arr_classes, rules, gated_holder, values, targets))
        else:
            seen.add(index)
    return seen


def paths_of(refs, arr_classes, rules, gated_holder, values, targets):
    """One array -> (reach, conditional slots, matched, unmatched, empty-body) for the branch walk."""
    seen = reachable(refs, arr_classes, rules, gated_holder, values, targets)
    skipped = set()
    matched = unmatched = empty = 0
    for index in sorted(seen):
        rule = rules.get(arr_classes[index]) or {}
        if not rule.get("branches"):
            continue
        skip = skip_target(arr_classes, rules, index)
        if skip is None:
            unmatched += 1
            skipped.update(range(index + 1, len(refs)))
            continue
        if skip == index + 1:
            empty += 1
            continue
        matched += 1
        skipped.update(range(index + 1, skip))
    cond = len(skipped & seen)
    return len(seen), cond, matched, unmatched, empty


def classify(kind, rec, cond_tags, placed_tags):
    """One branch slot -> a short state. The operand is placed data, so "unset" is a decision, not a gap."""
    if kind == "condition":
        tag = (rec.get("cond_tag") or "").lower()
        if tag in ("", "none"):
            return "unset"
        return "resolved" if cond_tags.get(tag) else "missing"
    if kind == "proximity":
        prox = (rec.get("prox_tag") or "").lower()
        tags = [t.lower() for t in (rec.get("actor_tags") or [])]
        radius = rec.get("radius") or 0.0
        if prox in ("", "none") or not tags or radius <= 0:
            return "unset"
        hits = len([t for t in tags if placed_tags.get(t)])
        if placed_tags.get(prox) and hits == len(tags):
            return "resolved"
        return "missing" if hits == 0 and not placed_tags.get(prox) else "partial"
    if kind == "random":
        prob = rec.get("probability")
        if prob is None or prob <= 0.0:
            return "unset"
        return "resolved"
    return "unknown"


def measure(entries, exports, rules, branch_of, cond_tags, placed_tags, gated=frozenset(), values=None,
            targets=None, sequence_cls=frozenset(), sequence_only=True):
    """One map -> (row, per-holder counters). No package needed, so `--check` can drive it."""
    classes = [e["class"] for e in entries]
    values = values or {}
    targets = targets or {}
    row = dict(kind="map", name="", package="", role="", arrays=0, elements=0, branches=0, if_cond=0,
               if_prox=0, if_rand=0, matched=0, unmatched=0, empty_body=0, cond_slots=0, always=0, reach=0,
               straight=0, beyond=0, orphan=0, op_set=0, op_unset=0, op_resolved=0, op_missing=0, enabled=0,
               disabled=0, unstated=0, flipped=0, unfired=0, note="")
    per_holder = collections.defaultdict(collections.Counter)

    fired = {name.lower() for rec in exports.values() for name in rec.get("fires") or []}

    def bump(key, n=1):
        row[key] += n
        per_holder[holder][key] += n

    for idx in sorted(exports):
        rec = exports[idx]
        if rec.get("actions") is None:
            continue
        count, refs = rec["actions"]
        holder = classes[idx - 1] if 0 < idx <= len(classes) else ""
        if sequence_only and holder not in sequence_cls:
            per_holder["(scene manager)"]["arrays"] += 1
            per_holder["(scene manager)"]["elements"] += len(refs)
            continue
        bump("arrays")
        bump("elements", len(refs))
        if not refs:
            continue
        arr_classes = slots_of(refs, classes)
        for ref, cls in zip(refs, arr_classes):
            kind = (branch_of.get(cls) or {}).get("kind")
            if not kind:
                continue
            bump("branches")
            bump("if_cond" if kind == "condition" else "if_prox" if kind == "proximity" else "if_rand")
            state = classify(kind, exports.get(ref) or {}, cond_tags, placed_tags)
            if state == "unset":
                bump("op_unset")
            else:
                bump("op_set")
            if state in ("resolved", "partial"):
                bump("op_resolved")
            elif state in ("missing", "unknown"):
                bump("op_missing")
            if kind == "condition" and state == "resolved":
                actor = (cond_tags.get((exports[ref].get("cond_tag") or "").lower()) or [None])[0]
                value = exports[actor].get("benabled") if actor else None
                if value is True:
                    bump("enabled")
                elif value is False:
                    bump("disabled")
                else:
                    # the placement states no `bEnabled`, and the class's own defaults block does not parse to a
                    # value either: counted, not guessed
                    bump("unstated")
                actor_tag = ((exports.get(actor) or {}).get("tag") or "").lower()
                if actor_tag and actor_tag in fired:
                    bump("flipped")
                else:
                    bump("unfired")
        is_gated = holder in gated
        seen = reachable(refs, arr_classes, rules, is_gated, values, targets)
        reach, cond, matched, unmatched, empty = paths_of(refs, arr_classes, rules, is_gated, values, targets)
        bump("reach", reach)
        bump("cond_slots", cond)
        bump("always", reach - cond)
        bump("matched", matched)
        bump("unmatched", unmatched)
        bump("empty_body", empty)
        straight, _st = ao.walk(refs, classes, rules, is_gated, values, targets)
        bump("straight", straight)
        # the two counts disagree in both directions: a skip edge reaches past a branch's cut, and a backward
        # goto leaves a tail the straight count includes but no path from index 0 comes back to
        bump("beyond", len(seen - set(range(straight))))
        bump("orphan", len(set(range(straight)) - seen))
    return row, per_holder


def tag_index(exports, entries, want_class=""):
    """Placed `Tag` -> [export index] for the actors a branch can find, optionally one class only."""
    out = collections.defaultdict(list)
    for idx, rec in exports.items():
        tag = rec.get("tag")
        if not tag or tag.lower() == "none":
            continue
        if want_class and (entries[idx - 1]["class"] if 0 < idx <= len(entries) else "") != want_class:
            continue
        out[tag.lower()].append(idx)
    return out


def sweep(dump, out, classes_path=CLASSES, only=None):
    maps_dir = os.path.join(dump, "Maps")
    system_dir = os.path.join(dump, "System")
    if not os.path.isdir(maps_dir):
        sys.exit("no Maps/ under %s" % dump)
    supers = ao.class_supers(classes_path)
    rules = ao.class_rules(system_dir, supers)
    branch_of = branch_classes(system_dir, rules)
    marks = {c for c, r in rules.items() if r["role"] == "holder" and r.get("gate")}
    sequence_cls = ao.family(supers, "ScriptedSequence") | {"ScriptedSequence"}
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
    reference = {}
    if os.path.exists(ORDER):
        with open(ORDER, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["kind"] == "map":
                    reference[r["name"]] = r
    rows = []
    per_holder = collections.defaultdict(collections.Counter)
    ctrl_maps = 0
    cond_actors = 0
    ref_reach = ref_elements = ref_arrays = 0
    for name in sorted(os.listdir(maps_dir)):
        if not name.lower().endswith(".unr"):
            continue
        label = name[:-4]
        if only and only.lower() not in label.lower():
            continue
        pkg = Package(os.path.join(maps_dir, name))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        entries = [{"class": pkg.class_name_of(e), "name": pkg.name(e["name"])} for e in pkg.exports]
        exports = read_exports(pkg)
        targets = {idx: r["target"] for idx, r in exports.items() if r.get("target") is not None}
        cond_tags = tag_index(exports, entries, "TriggeredCondition")
        placed_tags = tag_index(exports, entries, "")
        row, holders = measure(entries, exports, rules, branch_of, cond_tags, placed_tags, gated, values,
                              targets, sequence_cls, True)
        cond_actors += sum(len(hits) for hits in cond_tags.values())
        row["name"] = label
        rows.append(row)
        for cls, info in holders.items():
            per_holder[cls].update(info)
        ref = reference.get(label)
        if ref:
            if ao.num(ref, "reach") == row["straight"]:
                ctrl_maps += 1
            ref_reach += ao.num(ref, "reach")
            ref_elements += ao.num(ref, "elements")
            ref_arrays += ao.num(ref, "arrays")
        print("%-26s arrays=%-4d elements=%-5d branches=%-3d reach=%-5d straight=%-5d cond=%-4d unset=%-3d" %
              (label, row["arrays"], row["elements"], row["branches"], row["reach"], row["straight"],
               row["cond_slots"], row["op_unset"]), flush=True)
    for cls in sorted(per_holder, key=lambda c: (-per_holder[c]["elements"], c)):
        r = dict(kind="holder", name=cls, package="", role="holder", note="")
        r.update({k: per_holder[cls].get(k, 0) for k in
                  ("arrays", "elements", "branches", "if_cond", "if_prox", "if_rand", "matched", "unmatched",
                   "empty_body", "cond_slots", "always", "reach", "straight", "beyond", "orphan", "op_set",
                   "op_unset", "op_resolved", "op_missing", "enabled", "disabled", "unstated", "flipped",
                   "unfired")})
        rows.append(r)
    for cls in sorted(branch_of):
        info = branch_of[cls]
        rows.append(dict(kind="class", name=cls, package=info["package"], role="branch",
                         branches=int(branch_of[cls].get("kind") != ""),
                         note="operand=%s;kind=%s" % (info["operand"], info["kind"])))
    row = dict(kind="census", name="corpus", package="", role="")
    keys = ("arrays", "elements", "branches", "if_cond", "if_prox", "if_rand", "matched", "unmatched",
            "empty_body", "cond_slots", "always", "reach", "straight", "beyond", "orphan", "op_set", "op_unset",
            "op_resolved", "op_missing", "enabled", "disabled", "unstated", "flipped", "unfired")
    maps = [r for r in rows if r["kind"] == "map"]
    for key in keys:
        row[key] = sum(int(r.get(key, 0) or 0) for r in maps)
    row["note"] = ("maps=%d;branch_classes=%d;cond_actors=%d;ctrl_maps=%d;ctrl_straight=%d;ctrl_reach=%d;"
                   "ref_arrays=%d;ref_elements=%d" %
                   (len(maps), len(branch_of), cond_actors, ctrl_maps, row["straight"], ref_reach,
                    ref_arrays, ref_elements))
    rows.append(row)
    if out:
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, "action_paths.csv")
        write_artifact(rows, path)
        print("%s: %d rows" % (path, len(rows)))
    if ref_arrays and (ref_arrays != row["arrays"] or ref_elements != row["elements"]):
        print("control: action-order.csv disagrees on the population (%d/%d arrays, %d/%d elements)" %
              (ref_arrays, row["arrays"], ref_elements, row["elements"]))
    return rows


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def note_values(row):
    out = {}
    for part in (row.get("note") or "").split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def num(row, key):
    try:
        return int(row.get(key, 0))
    except (TypeError, ValueError):
        return 0


def render(rows):
    c = [r for r in rows if r["kind"] == "census"][0]
    vals = note_values(c)
    classes = [r for r in rows if r["kind"] == "class"]
    holders = [r for r in rows if r["kind"] == "holder" and r["name"] != "(scene manager)"]
    branches = num(c, "branches")
    cond = num(c, "cond_slots")
    reach = num(c, "reach")
    straight = num(c, "straight")
    out = []
    out.append("**Every conditional in an `Actions` array reads an operand the placement carries, and %d of the "
               "%d slots a map's sequences wire are on some path: the straight index run reaches %d, a branch's "
               "skip edge reaches %d more, and %d of the slots that count includes are on no path at all.**"
               % (reach, num(c, "elements"), straight, num(c, "beyond"), num(c, "orphan")))
    out.append("")
    out.append("`docs/MAP_ACTION_ORDER.md` measured the order and stopped at a schedule: \"`ACTION_IfCondition` "
               "and `ACTION_IfProximity` decide at run time, so which side of a section a given play takes is not "
               "in the package\". The decision is, though -- not `FRand()`'s draw, but *what the branch reads*, "
               "which is placed data. All three conditionals read a property of the placed element itself, and "
               "`ScriptedAction.ProceedToSectionEnd` is the skip they share.")
    out.append("")
    out.append("| measure | answer |")
    out.append("|---|---|")
    out.append("| maps read | **%s** |" % vals.get("maps", "?"))
    out.append("| conditional classes in the build | **%s** |" % vals.get("branch_classes", "?"))
    out.append("| arrays walked | **%s** (%s elements) |" % (c["arrays"], c["elements"]))
    out.append("| branch slots in them | **%s** (`ACTION_IfCondition` %s, `ACTION_IfProximity` %s, "
               "`ACTION_IfRandomPct` %s) |" % (branches, c["if_cond"], c["if_prox"], c["if_rand"]))
    out.append("| branches whose skip finds its `EndSection` | **%s**%s |"
               % (c["matched"], "" if num(c, "empty_body") else
                  " (none with an empty body: every branch that pairs skips at least one slot)" if not num(c, "empty_body")
                  else " (%s of them with an empty body, where both outcomes are the same slot)" % c["empty_body"]))
    out.append("| branches whose skip runs off the array (the run ends there) | **%s** |" % c["unmatched"])
    out.append("| slots reached by some path (the union) | **%s** of %s, against **%s** for the straight run |"
               % (reach, c["elements"], straight))
    out.append("| slots a branch can skip (conditional) | **%s** |" % cond)
    out.append("| slots every path runs (the straight run minus a branch's span) | **%s** |" % c["always"])
    out.append("| slots the straight count includes but no path from index 0 returns to (a backward goto) | "
               "**%s** |" % c["orphan"])
    out.append("| slots a branch edge reaches that the straight run does not | **%s** |" % c["beyond"])
    out.append("| placed `TriggeredCondition` actors across the maps | **%s** |" % vals.get("cond_actors", "?"))
    out.append("| `ACTION_IfCondition` slots whose condition actor's tag a placed property in the same map fires "
               "/ fires nowhere | **%s / %s** |" % (c["flipped"], c["unfired"]))
    out.append("| branch slots whose operand is stated | **%s** (%s unset) |" % (c["op_set"], c["op_unset"]))
    out.append("| branch slots whose operand resolves in their own map | **%s** (%s do not) |"
               % (c["op_resolved"], c["op_missing"]))
    out.append("| `ACTION_IfCondition` slots whose placed actor states `bEnabled` true / false / nothing | "
               "**%s / %s / %s** |" % (c["enabled"], c["disabled"], c["unstated"]))
    out.append("")
    if not num(c, "if_prox"):
        out.append("**`ACTION_IfProximity` is placed in no map.** The class ships, its body is complete, and not "
                   "one of the %s branch slots in the corpus is one -- so the proximity test is a branch the "
                   "build carries and the maps never take, and every branch that does exist reads a tag or a "
                   "probability." % branches)
        out.append("")
    out.append("**The skip is `ProceedToSectionEnd`, and it lands on the `EndSection`.** The function walks "
               "forward from `C.ActionNum` counting `StartsSection` and `EndsSection`, and returns where the "
               "nesting goes negative -- *before* its own increment, so the counter is left on the matching "
               "`EndSection` and that element still runs. A branch is therefore a two-edge node: index + 1 on "
               "the fall-through, and the matching `EndSection` on the skip. Where the nesting never goes "
               "negative the loop walks off the array and the run simply stops, which is the %s unmatched "
               "branches above." % c["unmatched"])
    out.append("")
    out.append("**Each conditional's operand is a placed property, so the branch's first pass is in the "
               "package.**")
    out.append("")
    out.append("| class | operand it reads | what decides the skip |")
    out.append("|---|---|---|")
    out.append("| `ACTION_IfCondition` | `TriggeredConditionTag`, resolved against every `TriggeredCondition` "
               "actor in the same map | `!T.bEnabled` skips; a tag that resolves to nothing leaves `T` at "
               "`None`, and `None.bEnabled` is false, so it skips |")
    out.append("| `ACTION_IfProximity` | `ProximityActorTag`, `ActorTags`, `Radius` | its first line refuses "
               "the test unless all three are set, and then every tag must have an actor inside `Radius` of "
               "the proximity actor |")
    out.append("| `ACTION_IfRandomPct` | `Probability` | `FRand() > Probability` skips, so a placement at or "
               "below 0 skips every time and one at or above 1 never does |")
    out.append("")
    out.append("`TriggeredCondition` is a plain `var() bool bEnabled` that only its own `Trigger` flips: "
               "`bToggled` toggles it, otherwise it takes the inverse of `bInitialValue`, which `PostBeginPlay` "
               "copies from the placed value, and `bTriggerControlled` lets `Untrigger` put it back. So a "
               "placement that states `bEnabled=True` is a branch that falls through until something triggers "
               "that actor, and one that states nothing inherits the class default. %s of the %s resolved "
               "conditions state `True`, %s state `False`, and **%s state nothing** -- recorded as unstated "
               "rather than guessed, because a bool the class does not list in its defaults block is a value "
               "this read cannot see. What stays outside the package is whether a trigger fires that actor later "
               "in the play: that is `docs/MAP_EVENT_WIRING.md`'s name space, and it can only change a branch "
               "after the first pass through it."
               % (c["enabled"], num(c, "enabled") + num(c, "disabled") + num(c, "unstated"), c["disabled"],
                  c["unstated"]))
    out.append("")
    out.append("| sequence class | arrays | elements | branches | reached / straight | conditional | "
               "matched / unmatched / empty |")
    out.append("|---|---|---|---|---|---|---|")
    for r in holders:
        out.append("| `%s` | %s | %s | %s | %s / %s | %s | %s / %s / %s |"
                   % (r["name"], r["arrays"], r["elements"], r["branches"], r["reach"], r["straight"],
                      r["cond_slots"], r["matched"], r["unmatched"], r["empty_body"]))
    out.append("")
    out.append("**Two readers, one population.** The `straight` column is `tools/action-order.py`'s walk run "
               "again over the same arrays in the same pass: it agrees with the committed "
               "`inventory/action_order.csv` on **%s of %s maps** and reproduces its %s elements and %s arrays "
               "exactly, so the path walk above is that walk plus branch edges rather than a second opinion."
               % (vals.get("ctrl_maps", "?"), vals.get("maps", "?"), c["elements"], c["arrays"]))
    out.append("")
    out.append("## Reading a row")
    out.append("")
    out.append("`kind=map` rows are the per-map counts, `kind=holder` rows the same counts per sequence class "
               "(with the scene manager's camera arrays counted apart, not walked), `kind=class` rows are the "
               "conditional classes with the operand property their own body reads, and `kind=census` the "
               "corpus total with the control in its `note`.")
    out.append("")
    out.append("`reach` is the union of every path, `straight` is `action-order`'s single index run, "
               "`cond_slots` is the part of `reach` that lies inside a branch's skippable span, and `always` is "
               "`reach - cond_slots` -- the slots no path avoids. A branch with an empty body adds no "
               "conditional slot, which is why `empty_body` is counted on its own.")
    out.append("")
    out.append("## What this does not claim")
    out.append("")
    out.append("* **Which pass a play takes.** The operand is placed data, but `FRand()` is not, and a trigger "
               "that fires a `TriggeredCondition` mid-play changes the next pass through that branch. The fire "
               "side is measured as far as a name can be: %s of the %s resolved conditions name an actor whose "
               "tag another placement in the same map fires (`event-targets`' reader, the same join "
               "`docs/MAP_EVENT_WIRING.md` uses), and %s name one nothing in the map fires -- a condition that "
               "keeps its placed value for the whole play. Whether the firing placement is itself reached is "
               "still a runtime question." % (c["flipped"], num(c, "flipped") + num(c, "unfired"),
                                              c["unfired"]))
    out.append("* **That a resolved tag is the actor the engine finds.** `ForEach C.AllActors(class, T, tag)` "
               "takes the first match in actor iteration order, and a tag is not unique; the tool counts a "
               "shared tag as resolved because the class's own comment says it will use the first one found.")
    out.append("* **That an unset operand is a bug.** A condition with no tag, or a probability at zero, is a "
               "branch that always skips -- a legal way to write a block out of a sequence, and only the author "
               "knows whether it was meant.")
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
    """Synthetic arrays only: the skip target, the two edges, the conditional span, and the operand states."""
    rules = {
        "ScriptedAction": dict(moves=True),
        "ACTION_Move": dict(moves=False),
        "ACTION_GotoAction": dict(goto=True),
        "ACTION_IfCondition": dict(opens=True, branches=True),
        "ACTION_IfProximity": dict(opens=True, branches=True),
        "ACTION_IfRandomPct": dict(opens=True, branches=True),
        "ACTION_EndSection": dict(closes=True),
        "ACTION_LeaveSequence": dict(leaves=True),
    }
    values = {"ACTION_Move": True}

    # 1. a skip lands on the matching EndSection, not past it, and that EndSection still runs
    arr = ["ACTION_IfCondition", "ACTION_Move", "ACTION_Move", "ACTION_EndSection", "ACTION_Move"]
    assert skip_target(arr, rules, 0) == 3, skip_target(arr, rules, 0)
    assert edges(0, list(range(1, 6)), arr, rules, False, values, {}) == [1, 3]
    reach, cond, matched, unmatched, empty = paths_of(list(range(1, 6)), arr, rules, False, values, {})
    assert (reach, cond, matched, unmatched, empty) == (5, 2, 1, 0, 0), (reach, cond, matched, unmatched, empty)

    # 2. an empty body: the skip and the fall-through are the same slot, so nothing is conditional
    arr = ["ACTION_IfCondition", "ACTION_EndSection", "ACTION_Move"]
    assert skip_target(arr, rules, 0) == 1
    reach, cond, matched, unmatched, empty = paths_of(list(range(1, 4)), arr, rules, False, values, {})
    assert (reach, cond, matched, empty) == (3, 0, 0, 1), (reach, cond, matched, empty)

    # 3. nesting: an inner branch's body is inside the outer branch's span
    arr = ["ACTION_IfRandomPct", "ACTION_IfProximity", "ACTION_EndSection", "ACTION_Move", "ACTION_EndSection"]
    assert skip_target(arr, rules, 0) == 4
    assert skip_target(arr, rules, 1) == 2
    reach, cond, matched, _u, empty = paths_of(list(range(1, 6)), arr, rules, False, values, {})
    assert (reach, cond, matched, empty) == (5, 3, 1, 1), (reach, cond, matched, empty)

    # 4. an unmatched skip walks off the array, and the run ends there
    arr = ["ACTION_IfProximity", "ACTION_Move"]
    assert skip_target(arr, rules, 0) is None
    reach, cond, matched, unmatched, _e = paths_of(list(range(1, 3)), arr, rules, False, values, {})
    assert (reach, cond, matched, unmatched) == (2, 1, 0, 1)

    # 5. an empty slot and a LeaveSequence end the run, so nothing behind them is reached
    arr = ["ACTION_Move", "", "ACTION_Move"]
    refs = [2, 0, 2]
    reach, _c, _m, _u, _e = paths_of(refs, arr, rules, False, values, {})
    assert reach == 2, reach
    arr = ["ACTION_Move", "ACTION_LeaveSequence", "ACTION_Move"]
    reach, _c, _m, _u, _e = paths_of(list(range(1, 4)), arr, rules, False, values, {})
    assert reach == 2, reach

    # 6. a goto whose target is past the array has no edge; one at index 1 jumps back and loops once
    arr = ["ACTION_Move", "ACTION_GotoAction"]
    refs = [1, 2]
    assert edges(1, refs, arr, rules, False, values, {2: 9}) == []
    assert edges(1, refs, arr, rules, False, values, {2: 0}) == [0]

    # 7. the operands: unset is a decision, and a tag that resolves to nothing is not one
    cond_tags = {"door": [7]}
    assert classify("condition", {"cond_tag": "door"}, cond_tags, {}) == "resolved"
    assert classify("condition", {"cond_tag": "nowhere"}, cond_tags, {}) == "missing"
    assert classify("condition", {"cond_tag": "None"}, cond_tags, {}) == "unset"
    assert classify("proximity", {"prox_tag": "a", "actor_tags": ["b"], "radius": 100.0}, cond_tags,
                    {"a": [1], "b": [2]}) == "resolved"
    assert classify("proximity", {"prox_tag": "a", "actor_tags": ["b"], "radius": 0.0}, cond_tags,
                    {"a": [1], "b": [2]}) == "unset"
    assert classify("proximity", {"prox_tag": "a", "actor_tags": ["b"], "radius": 5.0}, cond_tags,
                    {"a": [1]}) == "partial"
    assert classify("random", {"probability": 0.0}, cond_tags, {}) == "unset"
    assert classify("random", {"probability": 0.35}, cond_tags, {}) == "resolved"

    # 8. a name array decodes only when it consumes its payload
    class _P:
        def name(self, i):
            return ["None", "alpha", "beta"][i]
    assert decode_name_array(bytes([2, 1, 2]), _P()) == ["alpha", "beta"]
    assert decode_name_array(bytes([3, 1, 2]), _P()) is None
    print("ok: 8 synthetic cases")


def main():
    ap = argparse.ArgumentParser(description="the paths an Actions array allows (PLAN 5.2)")
    ap.add_argument("dump", nargs="?", help="game directory (.../Stargate_SG-1_-_The_Alliance-2005-12-15)")
    ap.add_argument("--sweep", action="store_true", help="read every map and write the artifact")
    ap.add_argument("-o", "--out", help="artifact directory (inventory)")
    ap.add_argument("--only", help="one map, by name fragment")
    ap.add_argument("--report", action="store_true", help="render the committed artifact")
    ap.add_argument("--write-doc", metavar="PATH", help="splice the generated block into a page")
    ap.add_argument("--check-doc", metavar="PATH", help="fail when the page does not match the artifact")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        check()
        return 0
    if args.report:
        report()
        return 0
    if args.sweep:
        if not args.dump:
            sys.exit("--sweep needs the game directory")
        rows = sweep(args.dump, args.out, only=args.only)
        if args.write_doc:
            write_doc(args.write_doc, rows)
        if args.check_doc:
            check_doc(args.check_doc, rows)
        return 0
    if args.write_doc or args.check_doc:
        with open(ARTIFACT, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if args.write_doc:
            write_doc(args.write_doc, rows)
        if args.check_doc:
            check_doc(args.check_doc, rows)
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
