#!/usr/bin/env python3
"""Every parameter a placed scripted action carries, and the ones left to its class default (PLAN 5.2).

`docs/MAP_ACTION_WIRING.md` .. `docs/MAP_ACTION_REACH.md` read the *graph* the placed actions sit in -- which
sequence names which action, in what order, on which path, and whether the sequence starts. None of them read the
actions' own parameters except the handful a branch reads. This tool does: it walks the 73 maps once, parses every
placed action's property block with the shared reader (`tools/prop-stream.py`), and counts, per class and property,
how many placements state it.

An action the block does not name takes its class default -- the class's own `var(Action)` declaration -- so a
parameter is decided by the package either way. Three of those defaults are visible in the class's shipped text and
decide a run on their own:

* `ACTION_GotoAction.ProceedToNextAction` sets `C.ActionNum = Max(0, ActionNumber)`; a placement that states no
  `ActionNumber` sends the run back to **slot 0**.
* `ACTION_WaitForEvent.InitActionFor` resolves a `TriggeredCondition` by the tag `ExternalEvent`; a placement that
  states no `ExternalEvent` looks one up under `None`, which is the engine's "no tag filter", so the first
  `TriggeredCondition` in the level decides whether the wait completes at once.
* `ACTION_TriggerEvent.InitActionFor` hands its `Event` to `C.TriggerEvent(Event, ...)`, and that function returns
  at the top when the name is `None` -- so a placement that states no `Event` fires nothing at all.
* `ACTION_WaitForTimer` hands `PauseTime` straight to `SetTimer(..., false)`, so the largest placed value is how long
  a sequence can sit still.

    python3 tools/action-parameters.py DUMP --sweep -o inventory
    python3 tools/action-parameters.py DUMP --relocate -o inventory     # the candidate census
    python3 tools/action-parameters.py --report
    python3 tools/action-parameters.py --write-doc docs/MAP_ACTION_PARAMETERS.md
    python3 tools/action-parameters.py --check-doc docs/MAP_ACTION_PARAMETERS.md
    python3 tools/action-parameters.py --check          # synthetic, no dump, writes nothing

A block is located by where it ends, and every property boundary inside it is a valid start, so the locator's
"earliest start with the most properties" is a tie-break. When the block it picks names a property the class chain
does not declare, the placement is refused rather than published; this tool then re-scores the export's every
candidate start by the *fraction* of names its class declares (`choose()`, the rule `tools/class-defaults.py`
ships) and adopts that start only when it refuses strictly fewer names. `--relocate` is the measurement behind
that rule: it runs the same chooser over every placement and crosses it against the locator, so the rows it would
move can be told apart from the rows it agrees on.

No name, string or object value is written: a name-typed parameter contributes its distinct count and how often it
is `None`, a numeric one its min/max/median. Names of properties are schema, not content.
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

ARTIFACT = os.path.join(ROOT, "inventory/action_parameters.csv")
RELOCATION = os.path.join(ROOT, "inventory/action_block_relocation.csv")
DOC = os.path.join(ROOT, "docs/MAP_ACTION_PARAMETERS.md")
CLASSES = os.path.join(ROOT, "inventory/classes.csv")
PROPERTIES = os.path.join(ROOT, "inventory/class_properties.csv")
WIRING = os.path.join(ROOT, "inventory/action_wiring.csv")
MAP_CLASSES = os.path.join(ROOT, "inventory/map_export_classes.csv")
DOC_START = "<!-- generated:action-parameters -->"
DOC_END = "<!-- /generated:action-parameters -->"

FIELDS = ["kind", "map", "name", "class", "property", "type", "placements", "stated", "distinct", "min", "max",
          "median", "note"]
RELOCATION_FIELDS = ["map", "placements", "blocked", "multi", "refused_placements", "moved", "recovered",
                     "refused_after", "clean", "agree_clean", "disagree_clean"]
ACTION_BASES = {"ScriptedAction", "LatentScriptedAction"}
NUMERIC = ("Byte", "Int", "Float")
NAMEY = ("Name", "Str", "Object", "Class")
# The class an untagged wait resolves against, and the value its own text leaves `bEnabled` at: `var() bool
# bEnabled` states no default, so an instance that does not state one is disabled.
CONDITION_CLASS = "TriggeredCondition"
DEFAULT_ENABLED = False


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ps = _load("prop_stream", "prop-stream.py")
aw = _load("action_wiring", "action-wiring.py")


def class_supers(path=CLASSES):
    supers = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            supers[row["class"]] = row.get("super", "")
    return supers


def family(supers, root_name):
    out = set()
    for cls in supers:
        seen, cur = set(), cls
        while cur in supers and cur not in seen:
            seen.add(cur)
            if supers[cur] == root_name:
                out.add(cls)
                break
            cur = supers[cur]
    return out


def load_declared(path=PROPERTIES):
    """(class, property) pairs the build declares -- the control that refuses a mis-parsed block's row."""
    out = set()
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out.add((row["class"], row["property"]))
    return out


def allowed_for(cls, declared, supers):
    """Every property name the class's chain declares."""
    names, seen, cur = set(), set(), cls
    while cur and cur not in seen:
        seen.add(cur)
        names.update(p for c, p in declared if c == cur)
        cur = supers.get(cur, "")
    return names


def read_blocks(pkg):
    """Per export (1-based): its properties with decoded values, its block start, and its `Actions` array."""
    blocks = {}
    for i, exp in enumerate(pkg.exports):
        data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
        if not data:
            continue
        found = ps.find_block(data, pkg, min_props=1)
        if not found:
            continue
        _count, start, _props = found
        props, _end, _reason = ps.read_block(data[start:], pkg, True)
        blocks[i + 1] = {"props": props, "actions": actions_of(data, start, props), "start": start}
    return blocks


def actions_of(data, start, props):
    """The `Actions` array a block states, decoded; `None` when it states none."""
    for p in props:
        if p["name"] == "Actions" and p["type"] == "Array":
            raw = data[start + p["value_offset"]:start + p["value_offset"] + p.get("size", 0)]
            return aw.decode_array_refs(raw)
    return None


WINDOW = 64  # the start window `find_block` reads, so every candidate here is one it considered


def candidates(pkg, data, min_props=1, values=True):
    """Every start in the locator's window whose property list parses cleanly and ends at the export's end."""
    out = []
    for start in range(0, min(len(data), WINDOW)):
        try:
            props, end, reason = ps.read_block(data[start:], pkg, values)
        except Exception:
            continue
        if reason != "None" or len(props) < min_props:
            continue
        if any(p["name"] == "?" for p in props):
            continue
        if start + end != len(data):
            continue
        out.append((start, props))
    return out


def choose(cands, allowed, own=frozenset()):
    """The declared fraction first, then no `Invalid` tag, then the length, then the own declarations, then the start.

    `find_block` takes the most properties, which is a tie-break rather than a reading: every property boundary is
    a valid start, so a suffix of the list parses to the export's end too. On `M15A`'s display message the longest
    parse starts four bytes in, reads three names the class does not declare and drops the two it does. Scoring the
    *fraction* of declared names first is `tools/class-defaults.py`'s `choose()` applied to a placed action's block,
    and it is the same rule `docs/ACTOR_PROPERTIES.md` reaches for from the actor side: a misparse invents names,
    the class's own declaration list says which parse is real.
    """
    best = None
    for start, props in cands:
        names = [p["name"] for p in props]
        known = sum(1 for n in names if n in allowed)
        bad = sum(1 for p in props if p.get("type") == "Invalid")
        score = (known / float(len(names)), bad == 0, len(names), sum(1 for n in names if n in own), -start)
        if best is None or score > best[0]:
            best = (score, start, props)
    return (best[1], best[2]) if best else None


def refused_names(props, allowed):
    """The block's names the class chain does not declare -- the control that keeps a misparse out of the table."""
    return [p["name"] for p in props if p["name"] not in allowed]


def better_block(before, after):
    """Adopt a re-located block only when it refuses strictly fewer names."""
    return len(after) < len(before)


def relocate(pkg, blocks, classes, action_cls, declared, supers):
    """Re-locate only the blocks whose class chain refuses a name; every other block keeps `find_block`'s start.

    The rule is confined to the refused rows on purpose: the --relocate census runs the same chooser over every
    placement and finds it picks `find_block`'s start on all 15,429 that were never refused, so widening it buys
    nothing and costs a second read of every block.
    """
    moved, recovered, allowed_cache = [], 0, {}
    for i, cls in enumerate(classes):
        if cls not in action_cls:
            continue
        blk = blocks.get(i + 1)
        if not blk:
            continue
        if cls not in allowed_cache:
            allowed_cache[cls] = allowed_for(cls, declared, supers)
        allowed = allowed_cache[cls]
        bad = refused_names(blk["props"], allowed)
        if not bad:
            continue
        exp = pkg.exports[i]
        data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
        cands = candidates(pkg, data)
        if len(cands) < 2:
            continue
        chosen = choose(cands, allowed, {p for c, p in declared if c == cls})
        if not chosen:
            continue
        start, props = chosen
        still = refused_names(props, allowed)
        if not better_block(bad, still):
            continue
        blk["props"] = props
        blk["actions"] = actions_of(data, start, props)
        moved.append({"export": i + 1, "class": cls, "from": blk["start"], "to": start,
                      "before": bad, "after": still})
        recovered += len(bad) - len(still)
    return moved, recovered


def slot_index(blocks, classes, sequence_cls):
    """Object index -> (holder class, slot) for every element of a sequence's `Actions` array, and its slot count."""
    out, count = {}, collections.Counter()
    for idx in sorted(blocks):
        blk = blocks[idx]
        if not blk.get("actions"):
            continue
        holder = classes[idx - 1] if 0 < idx <= len(classes) else ""
        if sequence_cls and holder not in sequence_cls:
            continue
        _count, refs = blk["actions"]
        for slot, ref in enumerate(refs):
            if ref > 0:
                count[ref] += 1
                if ref not in out:
                    out[ref] = (holder, slot)
    return out, count


def median(values):
    vals = sorted(values)
    n = len(vals)
    if not n:
        return None
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def condition_index(entries, blocks):
    """Per map, the `TriggeredCondition` placements in export order, with the `bEnabled` each one states.

    `ACTION_WaitForEvent.InitActionFor` keeps the **first** condition its `foreach` yields -- the body is a bare
    `break` -- and then tests `t.bEnabled`, so an untagged wait's outcome is this list's first row. A condition
    that states no `bEnabled` takes the class's own `var() bool bEnabled`, which has no default, so it is false:
    `enabled` is `None` for the placement and `resolved()` supplies what the class does.
    """
    out = []
    for i, e in enumerate(entries):
        if e["class"] != CONDITION_CLASS:
            continue
        idx = i + 1
        stated = None
        for p in (blocks.get(idx) or {}).get("props") or []:
            if p["name"] == "bEnabled":
                stated = bool(p.get("value"))
        out.append({"index": idx, "enabled": stated})
    return out


def resolved(cond):
    """The `bEnabled` the engine sees: the placement's own value, or the class default it does not override."""
    return cond["enabled"] if cond["enabled"] is not None else DEFAULT_ENABLED


def wait_verdict(conds):
    """(outcome, order_free) for a wait that states no `ExternalEvent`: the `None` name filters nothing.

    `order_free` says whether the answer survives not knowing the level's actor order -- true when every
    condition the map carries resolves the same way (including a map that carries none at all).
    """
    if not conds:
        return "no_condition", True
    order_free = len({resolved(c) for c in conds}) == 1
    return ("completes" if resolved(conds[0]) else "blocks"), order_free


def measure(entries, blocks, action_cls, sequence_cls=frozenset(), slots=None, label="", declared=None,
            supers=None, slot_count=None, conds=None):
    """One map -> (per-class, per-property, census, residues). No package needed, so `--check` drives it."""
    classes = [e["class"] for e in entries]
    per_class = collections.defaultdict(collections.Counter)
    per_prop = collections.defaultdict(lambda: {"type": "", "stated": 0, "values": [], "distinct": set(),
                                                "none": 0})
    census = collections.Counter()
    residues = []
    slots = slots or {}
    slot_count = slot_count or {}
    allowed = {}
    for i, cls in enumerate(classes):
        if cls not in action_cls:
            continue
        idx = i + 1
        census["placements"] += 1
        per_class[cls]["placements"] += 1
        blk = blocks.get(idx)
        if not blk:
            census["unblocked"] += 1
            per_class[cls]["unblocked"] += 1
        else:
            census["blocked"] += 1
            per_class[cls]["blocked"] += 1
        props = blk["props"] if blk else []
        if declared is not None:
            if cls not in allowed:
                allowed[cls] = allowed_for(cls, declared, supers or {})
            kept, refused_here = [], []
            for p in props:
                if p["name"] in allowed[cls]:
                    kept.append(p)
                else:
                    census["props_refused"] += 1
                    refused_here.append(p["name"])
                    here = slots.get(idx)
                    residues.append(dict(kind="residue", name="property_refused", **{"class": cls}, map=label,
                                         note="%s slot %s" % (p["name"], here[1] if here else "")))
            if refused_here:
                census["placements_refused"] += 1
            props = kept
        for p in props:
            name, typ = p["name"], p["type"]
            rec = per_prop[(cls, name, typ)]
            rec["type"] = typ
            rec["stated"] += 1
            census["props_stated"] += 1
            value = p.get("value")
            if typ in NUMERIC and isinstance(value, (int, float)) and not isinstance(value, bool):
                rec["values"].append(float(value))
            elif typ == "Bool":
                rec["distinct"].add(bool(value))
            elif typ in NAMEY:
                if isinstance(value, str) and value.lower() == "none":
                    rec["none"] += 1
                elif value is not None:
                    rec["distinct"].add(str(value))
                else:
                    rec["none"] += 1
        stated = {p["name"] for p in props}
        cls_short = cls.split("_")[-1].lower()
        if cls == "ACTION_GotoAction":
            census["goto_placements"] += 1
            if "ActionNumber" in stated:
                census["goto_stated"] += 1
                target = next(p.get("value") for p in blk["props"] if p["name"] == "ActionNumber")
                here = slots.get(idx)
                if not here:
                    census["goto_unwired"] += 1
                elif isinstance(target, int):
                    slot = here[1]
                    if target < slot:
                        census["goto_back"] += 1
                    elif target > slot:
                        census["goto_forward"] += 1
                    else:
                        census["goto_same"] += 1
            else:
                census["goto_unset"] += 1
                here = slots.get(idx)
                if here:
                    census["goto_unset_wired"] += 1
                else:
                    census["goto_unset_unwired"] += 1
                if here and here[1] == 0:
                    census["goto_unset_slot0"] += 1
                    residues.append(dict(kind="residue", name="goto_unset_slot0", **{"class": cls},
                                         map=label, note="slot %d" % here[1]))
        elif cls == "ACTION_WaitForTimer":
            census["timer_placements"] += 1
            if "PauseTime" in stated:
                census["timer_stated"] += 1
                value = next((p.get("value") for p in blk["props"] if p["name"] == "PauseTime"), None)
                if isinstance(value, float):
                    if value >= 99999.0:
                        census["timer_sentinel"] += 1
                    elif value <= 0.0:
                        census["timer_nonpositive"] += 1
            else:
                census["timer_unstated"] += 1
                here = slots.get(idx)
                residues.append(dict(kind="residue", name="timer_unstated", **{"class": cls},
                                     map=label, note="slot %s" % (here[1] if here else "")))
        elif cls == "ACTION_TriggerEvent":
            census["trigger_placements"] += 1
            if "Event" in stated:
                census["trigger_stated"] += 1
            else:
                # `Actor.TriggerEvent` returns at the top on a `None` name, so this placement fires nothing.
                census["trigger_unstated"] += 1
                here = slots.get(idx)
                residues.append(dict(kind="residue", name="trigger_unstated", **{"class": cls},
                                     map=label, note="slot %s" % (here[1] if here else "")))
        elif cls == "ACTION_WaitForEvent":
            census["wait_placements"] += 1
            if "ExternalEvent" in stated:
                census["wait_stated"] += 1
            else:
                census["wait_unstated"] += 1
                here = slots.get(idx)
                outcome, order_free = wait_verdict(conds or [])
                census["wait_unstated_" + outcome] += 1
                if order_free:
                    census["wait_unstated_order_free"] += 1
                first = (conds or [None])[0]
                if not first:
                    which = "none"
                elif first["enabled"] is None:
                    which = "default (%s)" % ("true" if DEFAULT_ENABLED else "false")
                else:
                    which = "stated %s" % ("true" if first["enabled"] else "false")
                residues.append(dict(kind="residue", name="wait_unstated", **{"class": cls}, map=label,
                                     note="slot=%s;conditions=%d;first=%s;outcome=%s" % (
                                         here[1] if here else "", len(conds or []), which, outcome)))
            if any(p["name"] == "bCheckRecentEvents" and p.get("value") for p in props):
                census["wait_recent"] += 1
        elif cls == "ACTION_Talk":
            census["talk_placements"] += 1
            if "SubTitleText" in stated:
                census["talk_subtitle"] += 1
            if "DialogueFinishedEvent" in stated:
                census["talk_finished"] += 1
        elif cls == "Action_PLAYANIM":
            census["anim_placements"] += 1
            if "BaseAnim" in stated:
                census["anim_stated"] += 1
        if not stated:
            census["empty_block"] += 1
    return per_class, per_prop, census, residues


def rows_of(maps, per_class, per_prop, census, residues, classes_total):
    """Artifact rows: map rows, class rows, property rows, residues, then the corpus census."""
    rows = []
    for label, info in sorted(maps.items()):
        c = info["census"]
        rows.append(dict(kind="map", map=label, name="", **{"class": ""}, property="", type="",
                         placements=c["placements"], stated=c["props_stated"],
                         note="blocked=%d;unblocked=%d;classes=%d;conditions=%d"
                              % (c["blocked"], c["unblocked"], len(info["class"]), c.get("conditions", 0))))
    totals = collections.Counter()
    for info in maps.values():
        for key, value in info["census"].items():
            totals[key] += value
    for cls in sorted(per_class, key=lambda c: (-per_class[c]["placements"], c)):
        props = sorted({k[1] for k in per_prop if k[0] == cls})
        rows.append(dict(kind="class", map="corpus", name=cls, **{"class": cls}, property="", type="",
                         placements=per_class[cls]["placements"], stated=len(props),
                         note="blocked=%d;unblocked=%d;%s" % (per_class[cls]["blocked"],
                                                             per_class[cls]["unblocked"], ",".join(props))))
    for key in sorted(per_prop):
        cls, name, typ = key
        rec = per_prop[key]
        values = rec["values"]
        distinct = len(rec["distinct"]) if typ in NAMEY or typ == "Bool" else len(set(values))
        rows.append(dict(kind="property", map="corpus", name="", **{"class": cls}, property=name, type=typ,
                         placements=per_class.get(cls, {}).get("placements", 0), stated=rec["stated"],
                         distinct=distinct,
                         min=("%.4g" % min(values)) if values else "",
                         max=("%.4g" % max(values)) if values else "",
                         median=("%.4g" % median(values)) if values else "",
                         note="none=%d" % rec["none"] if typ in NAMEY else ""))
    for r in residues:
        rows.append(r)
    for key in sorted(totals):
        rows.append(dict(kind="census", map="corpus", name=key, **{"class": ""}, property="", type="",
                         placements=totals[key], note=""))
    rows.append(dict(kind="census", map="corpus", name="maps", **{"class": ""}, property="", type="",
                     placements=len(maps), note=""))
    rows.append(dict(kind="census", map="corpus", name="classes", **{"class": ""}, property="", type="",
                     placements=len(per_class), note="family=%d" % classes_total))
    return rows


def sweep(dump, out, classes_path=CLASSES, only=None):
    maps_dir = os.path.join(dump, "Maps")
    if not os.path.isdir(maps_dir):
        sys.exit("no Maps/ under %s" % dump)
    supers = class_supers(classes_path)
    action_cls = family(supers, "ScriptedAction") - ACTION_BASES
    declared = load_declared()
    sequence_cls = family(supers, "ScriptedSequence") | {"ScriptedSequence"}
    maps, all_class, all_prop = {}, collections.defaultdict(collections.Counter), {}
    combined = collections.Counter()
    prop_acc = collections.defaultdict(lambda: {"type": "", "stated": 0, "values": [], "distinct": set(),
                                                "none": 0, "placements": 0})
    residues = []
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
        blocks = read_blocks(pkg)
        classes = [e["class"] for e in entries]
        moved, recovered = relocate(pkg, blocks, classes, action_cls, declared, supers)
        slots, slot_count = slot_index(blocks, classes, sequence_cls)
        conds = condition_index(entries, blocks)
        per_class, per_prop, census, res = measure(entries, blocks, action_cls, sequence_cls, slots, label,
                                                  declared, supers, slot_count, conds)
        census["relocated"] = len(moved)
        census["relocated_props"] = recovered
        census["refused_before"] = census["props_refused"] + recovered
        census["refused_placements_before"] = census["placements_refused"] + sum(1 for mv in moved if not mv["after"])
        for mv in moved:
            here = slots.get(mv["export"])
            gained = [n for n in mv["before"] if n not in mv["after"]]
            res.append(dict(kind="residue", name="block_moved", **{"class": mv["class"]}, map=label,
                            note="slot=%s;from=%d;to=%d;recovered=%s;still=%s"
                                 % (here[1] if here else "", mv["from"], mv["to"],
                                    ",".join(gained) or "-", ",".join(mv["after"]) or "-")))
        census["conditions"] = len(conds)
        maps[label] = {"class": per_class, "census": census}
        for cls, info in per_class.items():
            all_class[cls].update(info)
        for key, rec in per_prop.items():
            acc = prop_acc[key]
            acc["type"] = rec["type"]
            acc["stated"] += rec["stated"]
            acc["values"].extend(rec["values"])
            acc["distinct"] |= rec["distinct"]
            acc["none"] += rec["none"]
        residues.extend(res)
        combined.update(census)
        print("%-24s actions=%-5d blocked=%-5d unblocked=%-4d props=%d" %
              (label, census["placements"], census["blocked"], census["unblocked"], census["props_stated"]),
              flush=True)
    rows = rows_of(maps, all_class, prop_acc, combined, residues, len(action_cls))
    if out:
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, "action_parameters.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in FIELDS})
        print("wrote %s (%d rows)" % (path, len(rows)))
    condition_control({label: info["census"].get("conditions", 0) for label, info in maps.items()})
    control(rows)
    return rows


def relocation_census(dump, out, classes_path=CLASSES, only=None):
    """Does the declared-name chooser pick `find_block`'s start? One row per map, and the refusal counted both ways.

    `find_block` is the shipped locator -- earliest start with the most properties that ends at the export's end.
    This walks every placed action in every map, enumerates the same window's candidates, and asks two questions:
    on a block the class chain never refused, does the chooser agree with `find_block` (the control -- if it moved
    those, the rule would be a rewrite of the locator rather than a repair of the refused rows), and on a block it
    did refuse, how many statements does the chooser recover and how many does it leave. Counts only, no values.
    """
    maps_dir = os.path.join(dump, "Maps")
    if not os.path.isdir(maps_dir):
        sys.exit("no Maps/ under %s" % dump)
    supers = class_supers(classes_path)
    action_cls = family(supers, "ScriptedAction") - ACTION_BASES
    declared = load_declared()
    own = collections.defaultdict(set)
    for c, p in declared:
        own[c].add(p)
    rows, totals = [], collections.Counter()
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
        row = dict(map=label, placements=0, blocked=0, multi=0, refused_placements=0, moved=0, recovered=0,
                   refused_after=0, clean=0, agree_clean=0, disagree_clean=0)
        allowed_cache = {}
        for i, exp in enumerate(pkg.exports):
            cls = pkg.class_name_of(exp)
            if cls not in action_cls:
                continue
            row["placements"] += 1
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            if not data:
                continue
            found = ps.find_block(data, pkg, min_props=1)
            if not found:
                continue
            row["blocked"] += 1
            cands = candidates(pkg, data, values=False)
            if len(cands) > 1:
                row["multi"] += 1
            if cls not in allowed_cache:
                allowed_cache[cls] = allowed_for(cls, declared, supers)
            allowed = allowed_cache[cls]
            bad = refused_names(found[2], allowed)
            chosen = choose(cands, allowed, own.get(cls, set())) if cands else None
            if not chosen:
                continue
            after = refused_names(chosen[1], allowed)
            if bad:
                row["refused_placements"] += 1
                row["refused_after"] += len(after)
                if len(after) < len(bad):
                    row["moved"] += 1
                    row["recovered"] += len(bad) - len(after)
            elif chosen[0] == found[1]:
                row["clean"] += 1
                row["agree_clean"] += 1
            else:
                row["clean"] += 1
                row["disagree_clean"] += 1
        rows.append(row)
        totals.update({k: v for k, v in row.items() if k != "map"})
        print("%-24s placements=%-5d blocked=%-5d multi=%-5d refused=%d moved=%d" %
              (label, row["placements"], row["blocked"], row["multi"], row["refused_placements"], row["moved"]),
              flush=True)
    rows.append(dict(map="corpus", **{k: totals[k] for k in RELOCATION_FIELDS if k != "map"}))
    if out:
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, "action_block_relocation.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=RELOCATION_FIELDS, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print("wrote %s (%d rows)" % (path, len(rows)))
    print("relocation: %d refused placements, %d moved, %d statements recovered, %d left; the chooser picks "
          "find_block's start on %d of %d clean placements" %
          (totals["refused_placements"], totals["moved"], totals["recovered"], totals["refused_after"],
           totals["agree_clean"], totals["clean"]))
    return rows


def condition_control(per_map):
    """`TriggeredCondition` placements per map against `map_export_classes.csv` -- a second reader, same packages."""
    if not os.path.exists(MAP_CLASSES):
        return
    want = {}
    with open(MAP_CLASSES, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("class") == CONDITION_CLASS:
                want[r["map"]] = int(r["count"])
    agree = sum(1 for m, n in per_map.items() if want.get(m, 0) == n)
    print("control: %d of %d maps carry the same %s count as map_export_classes.csv"
          % (agree, len(per_map), CONDITION_CLASS))


def control(rows):
    """A placement census that disagrees with `action_wiring.csv` is not a census -- print the join."""
    if not os.path.exists(WIRING):
        return
    want = 0
    with open(WIRING, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["kind"] == "map":
                try:
                    want += int(r["actions"])
                except (TypeError, ValueError):
                    pass
    got = census_of(rows).get("placements", 0)
    print("control: %d placements here, %d in action_wiring.csv -- %s" % (got, want, "agree" if got == want else "DIFFER"))


def census_of(rows):
    return {r["name"]: int(r.get("placements") or 0) for r in rows if r["kind"] == "census"}


def wiring_total(path=WIRING):
    """`action_wiring.csv`'s placed-action total, the control this census has to agree with."""
    if not os.path.exists(path):
        return 0
    total = 0
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["kind"] == "map":
                try:
                    total += int(r["actions"])
                except (TypeError, ValueError):
                    pass
    return total


def relocation_totals(path=RELOCATION):
    """The `--relocate` corpus row: how many placements the chooser moved, and how many it agreed on."""
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("map") == "corpus":
                return {k: int(v) for k, v in r.items() if k != "map" and str(v).lstrip("-").isdigit()}
    return {}


def fmt(value):
    """A count with thousands separators, the way the other action pages print them."""
    try:
        return "{:,}".format(int(value))
    except (TypeError, ValueError):
        return str(value)


def prop_rows(rows):
    return [r for r in rows if r["kind"] == "property"]


def render(rows):
    c = census_of(rows)
    c["wiring"] = wiring_total()
    props = prop_rows(rows)
    classes = [r for r in rows if r["kind"] == "class"]
    out = []
    out.append("**%d placed actions over %d classes carry %d stated properties in %d of the 73 maps' packages; "
               "every other parameter is the class default, and three of those defaults decide a run on their own.**"
               % (c.get("placements", 0), len(classes), c.get("props_stated", 0), c.get("maps", 0)))
    out.append("")
    out.append("The wiring, order, path, reach and defect pages read the graph the actions sit in. This page reads "
               "what each placement says about itself: its property block, parsed with the shared reader "
               "(`tools/prop-stream.py`, the same one `actor-properties` and the checkpoint work use). A property "
               "the block does not state is the class's own `var(Action)` default, so a parameter is decided by the "
               "package either way -- and for three classes the default is enough to change what a play does.")
    out.append("")
    out.append("| population | count |")
    out.append("|---|---:|")
    out.append("| placed actions (the `ScriptedAction` family, minus its two bases) | **%s** |" % fmt(c.get("placements", 0)))
    out.append("| with a property block that terminates exactly at the export's end | **%s** |" % fmt(c.get("blocked", 0)))
    out.append("| with no block at all (the class default is the whole placement) | **%s** |" % fmt(c.get("unblocked", 0)))
    out.append("| property statements read | **%s** |" % fmt(c.get("props_stated", 0)))
    out.append("| placement classes | **%d** |" % len(classes))
    out.append("")
    out.append("| control | this walk | committed artifact |")
    out.append("|---|---:|---:|")
    out.append("| placed actions | %s | `action_wiring.csv` totals %s |" % (fmt(c.get("placements", 0)), fmt(c.get("wiring", 0))))
    out.append("| property statements refused because the class chain does not declare them | %s (was %s before the "
               "re-location) | `class_properties.csv` |"
               % (fmt(c.get("props_refused", 0)), fmt(c.get("refused_before", 0))))
    out.append("")
    out.append("### The three defaults that decide a run")
    out.append("")
    out.append("| parameter | placements | stated | default decides | what the class's own text does with it |")
    out.append("|---|---:|---:|---:|---|")
    gotos = c.get("goto_placements", 0)
    out.append("| `ACTION_GotoAction.ActionNumber` | %d | %d | **%d** | `C.ActionNum = Max(0, ActionNumber)`: a "
               "placement that states none sends the run back to slot 0 |" % (gotos, c.get("goto_stated", 0),
                                                                              c.get("goto_unset", 0)))
    waits = c.get("wait_placements", 0)
    out.append("| `ACTION_WaitForEvent.ExternalEvent` | %d | %d | **%d** | the tag is looked up under `None`, which "
               "the engine treats as no filter, so the level's first `TriggeredCondition` decides whether the wait "
               "completes at once |" % (waits, c.get("wait_stated", 0), c.get("wait_unstated", 0)))
    timers = c.get("timer_placements", 0)
    out.append("| `ACTION_WaitForTimer.PauseTime` | %d | %d | %d | handed straight to `SetTimer(PauseTime, false)`, "
               "so the largest placed value is how long a sequence can sit still |"
               % (timers, c.get("timer_stated", 0), timers - c.get("timer_stated", 0)))
    out.append("")
    out.append("**A goto with no `ActionNumber` is a jump to the first element, and none of the %d sits at the "
               "first element already%s. `ProceedToNextAction` clamps the value (`Max(0, ActionNumber)`), so the "
               "default is slot 0 rather than a no-op. %d of them sit on a slot of some sequence's array -- the "
               "population `MAP_ACTION_ORDER` counts as gotos with no target (its 377 is by slot on its own walk) "
               "-- and %d in none. Of the %d that state "
               "one, %d target a slot behind themselves, %d a slot ahead, %d their own slot, and %d sit in no "
               "sequence's array at all."
               % (c.get("goto_unset", 0),
                  (" -- each one re-runs its own sequence from the top" if c.get("goto_unset", 0)
                   and not c.get("goto_unset_slot0", 0) else ""),
                  c.get("goto_unset_wired", 0), c.get("goto_unset_unwired", 0),
                  c.get("goto_stated", 0), c.get("goto_back", 0), c.get("goto_forward", 0), c.get("goto_same", 0),
                  c.get("goto_unwired", 0)))
    out.append("")
    timer_row = next((r for r in props if r["class"] == "ACTION_WaitForTimer" and r["property"] == "PauseTime"), None)
    if timer_row:
        out.append("**`PauseTime` is the sequence's clock, and the top of its range is a park.** The class hands the "
                   "value straight to `SetTimer(..., false)`, so %d placements state a pause and %d state none at all "
                   "-- a zero-second timer that fires as soon as the action starts, and both are named in the residue "
                   "rows. The stated values run from %s to "
                   "%s seconds (median %s); the top is 27.8 hours, the corpus's way of holding a sequence where it is "
                   "for a whole play, and %d placements sit at or below zero."
                   % (c.get("timer_stated", 0), c.get("timer_placements", 0) - c.get("timer_stated", 0),
                      timer_row["min"], timer_row["max"], timer_row["median"], c.get("timer_nonpositive", 0)))
        out.append("")
    out.append("**The wait with no `ExternalEvent` is a resolved lookup, not a caveat.** `InitActionFor` runs "
               "`foreach C.DynamicActors(Class'Gameplay.TriggeredCondition', t, ExternalEvent)` with the name the "
               "placement states; a placement that states none passes `None`, which the iterator does not filter on, "
               "and the loop body is a bare `break`, so the **first** condition the level yields is the one kept. "
               "The test that follows is `if(t != none && t.bEnabled) return false` -- an enabled condition completes "
               "the wait at once, a disabled one lets the action block, and a map with no condition leaves `t` at "
               "`None` and blocks the same way. %d of the %d `ACTION_WaitForEvent` placements state the name and "
               "**%d do not** -- the same 19 the `MAP_EVENT_WIRING` wait count was short of this placement total. "
               "Of those 19, **%d complete at once**; the other **%d block** -- %d behind a disabled first condition "
               "and %d in a map that places no `TriggeredCondition` at all. The class's "
               "`bCheckRecentEvents` arm cannot rescue any of them (it is guarded by `ExternalEvent != 'None'`), and "
               "**%d of the 19 do not depend on the actor order at all**, because every condition their map carries "
               "resolves the same way. The one row that does depend on it is `M15A`'s: five conditions, one enabled "
               "and four not, and the package's own order puts the enabled one first."
               % (c.get("wait_stated", 0), c.get("wait_placements", 0), c.get("wait_unstated", 0),
                  c.get("wait_unstated_completes", 0),
                  c.get("wait_unstated_blocks", 0) + c.get("wait_unstated_no_condition", 0),
                  c.get("wait_unstated_blocks", 0), c.get("wait_unstated_no_condition", 0),
                  c.get("wait_unstated_order_free", 0)))
    out.append("")
    unstated = [r for r in rows if r["kind"] == "residue" and r["name"] == "wait_unstated"]
    if unstated:
        out.append("| map | sequence slot | `TriggeredCondition`s | the first one's `bEnabled` | what the wait does |")
        out.append("|---|---:|---:|---|---|")
        for r in sorted(unstated, key=lambda r: (r["map"], int(dict(
                p.split("=", 1) for p in r["note"].split(";") if "=" in p).get("slot") or -1))):
            parts = dict(p.split("=", 1) for p in r["note"].split(";") if "=" in p)
            out.append("| `%s` | %s | %s | %s | %s |" % (r["map"], parts.get("slot", ""),
                                                        parts.get("conditions", ""), parts.get("first", ""),
                                                        parts.get("outcome", "")))
        out.append("")
    out.append("**The fire with no name is a no-op, not a broadcast.** `ACTION_TriggerEvent.InitActionFor` hands its "
               "`Event` to `C.TriggerEvent(Event, ...)`, and `Actor.TriggerEvent` returns at the top on "
               "`EventName == 'None'` -- before the actor iterator and before `Level.AddEventToCache`. So the **%d** "
               "`ACTION_TriggerEvent` placement that states no `Event` fires nothing at all, which is the opposite "
               "of what the same name means on the wait side: there `None` is an iterator tag that filters nothing, "
               "here it is an early return. The class states its event on %d of its %d placements."
               % (c.get("trigger_unstated", 0), c.get("trigger_stated", 0), c.get("trigger_placements", 0)))
    out.append("")
    out.append("**`ACTION_Talk` carries its subtitle in the package.** %d of its %d placements state `SubTitleText` "
               "(a localized `string`, so the text ships in the map, not only in a voice package), %d state "
               "`DialogueFinishedEvent`, and the parameter surface is otherwise `TalkerTag`/`DialogueName`/"
               "`myDirectory`. `Action_PLAYANIM` -- the class whose name the `ACTION_` prefix cannot see -- states "
               "`BaseAnim` on %d of its %d placements, which is what makes it a real action rather than a marker."
               % (c.get("talk_subtitle", 0), c.get("talk_placements", 0), c.get("talk_finished", 0),
                  c.get("anim_stated", 0), c.get("anim_placements", 0)))
    out.append("")
    out.append("### The re-located blocks, and the five tags the class does not declare")
    out.append("")
    out.append("A block is located by where it *ends*: every property boundary in the export's first %d bytes is a "
               "valid start, so \"earliest start with the most properties\" is a tie-break rather than a reading, "
               "and on a minority of placements it lands inside the block. The control that catches that is the "
               "class declaration (`class_properties.csv`), which refuses a block naming a property its chain does "
               "not declare. **%d placements carried %d refused statements, and %d of the %d are a start the locator "
               "picked too early.** Re-scoring every candidate start by the *fraction* of declared names -- "
               "`tools/class-defaults.py`'s chooser, the rule the class-defaults work already ships -- moves %d "
               "placements and recovers **%d statements**; the five that remain are not a locator defect at all."
               % (WINDOW, c.get("refused_placements_before", 0), c.get("refused_before", 0),
                  c.get("relocated", 0), c.get("refused_placements_before", 0), c.get("relocated", 0),
                  c.get("relocated_props", 0)))
    out.append("")
    moved = [r for r in rows if r["kind"] == "residue" and r["name"] == "block_moved"]
    if moved:
        out.append("| map | class | sequence slot | start | refused names it recovered | still refused |")
        out.append("|---|---|---:|---:|---|---|")
        for r in moved:
            parts = dict(p.split("=", 1) for p in r["note"].split(";") if "=" in p)
            out.append("| `%s` | `%s` | %s | %s -> %s | %s | %s |"
                       % (r["map"], r["class"], parts.get("slot", ""), parts.get("from", ""), parts.get("to", ""),
                          ", ".join("`%s`" % n for n in parts.get("recovered", "-").split(",")
                                    if n and n != "-") or "none",
                          "none" if parts.get("still") == "-" else ", ".join(
                              "`%s`" % n for n in parts.get("still", "").split(",") if n)))
        out.append("")
    refused = [r for r in rows if r["kind"] == "residue" and r["name"] == "property_refused"]
    if refused:
        out.append("**The five left are one name on one class.** Every remaining refusal is `EndType` on "
                   "`ACTION_SetIMusic`, in two maps; the name is a declared property of `Engine.IMusicActor` "
                   "(`UByteProperty`, enum `LevelInfo.EIMusicEndType`), and `ACTION_SetIMusic`'s own chain declares "
                   "four names, none of them `EndType`. The tag sits *last* in a block whose other names are all "
                   "declared, its value is a one-byte enum index, and the same class's other placements in the "
                   "same maps read back as exactly its four names -- so the block is where it belongs and the tag "
                   "is the residue: a placement authored against a class revision that declared one more property "
                   "than the shipped `GamePlay.u` does. The engine's property reader skips a tag it cannot resolve "
                   "by the size in its info byte, which is what makes the leftover harmless at run time; what this "
                   "build's `Serialize` does with it is not measured here.")
        out.append("")
        out.append("| map | class | sequence slot | refused name |")
        out.append("|---|---|---:|---|")
        for r in sorted(refused, key=lambda r: (r["map"], r["class"], r["note"])):
            name, _sep, slot = r["note"].partition(" slot ")
            out.append("| `%s` | `%s` | %s | `%s` |" % (r["map"], r["class"], slot, name))
        out.append("")
    reloc = relocation_totals()
    if reloc:
        out.append("**The chooser is a repair, not a rewrite.** Run over every placement in all %d maps "
                   "(`--relocate`, `inventory/action_block_relocation.csv`), the declared-name chooser picks "
                   "exactly the start `find_block` does on **all %s placements the refusal never fired on** "
                   "(%s moved) -- so the rule changes no published block, and the %d it does change are precisely "
                   "the rows that were refused."
                   % (c.get("maps", 0), fmt(reloc.get("agree_clean", 0)),
                      fmt(reloc.get("disagree_clean", 0)), c.get("relocated", 0)))
        out.append("")
    out.append("### Each class's parameter surface")
    out.append("")
    out.append("| class | placements | properties it states | block / no block |")
    out.append("|---|---:|---|---|")
    for r in classes[:25]:
        names = r["note"].split(";")[-1]
        extra = r["note"].split(";")[:-1]
        out.append("| `%s` | %s | %s | %s |" % (r["name"], fmt(r["placements"]),
                                                ", ".join("`%s`" % n for n in names.split(",")) if names else "none",
                                                "; ".join(extra)))
    out.append("")
    out.append("### The widest parameters")
    out.append("")
    out.append("| class | property | type | stated | distinct | min | max | median |")
    out.append("|---|---|---|---:|---:|---:|---:|---:|")
    for r in sorted(props, key=lambda r: -int(r["stated"]))[:15]:
        out.append("| `%s` | `%s` | %s | %s | %s | %s | %s | %s |"
                   % (r["class"], r["property"], r["type"], r["stated"], r["distinct"] or "",
                      r["min"] or "", r["max"] or "", r["median"] or ""))
    out.append("")
    out.append("Only counts and ranges are written: a name-typed parameter contributes its distinct count and how "
               "often it is `None`, never the names themselves, and a string parameter only its presence. The "
               "residue rows name a map, a class and a slot -- enough to find the placement in the dump, and no "
               "content. A statement the class chain does not declare (`class_properties.csv`) is refused and "
               "counted as a residue rather than published as a parameter: the property reader locates a block by "
               "where it ends, so a mis-parsed start can invent a name, and that control is what keeps it out of the "
               "class table.")
    out.append("")
    out.append("Census: placements=%d;blocked=%d;unblocked=%d;props_stated=%d;classes=%d;maps=%d;"
               "goto_stated=%d;goto_unset=%d;props_refused=%d;wait_stated=%d;wait_unstated=%d;"
               "timer_stated=%d;timer_sentinel=%d;trigger_stated=%d;trigger_unstated=%d;conditions=%d;"
               "wait_completes=%d;wait_blocks=%d;wait_no_condition=%d;wait_order_free=%d;"
               "refused_before=%d;relocated=%d;relocated_props=%d."
               % (c.get("placements", 0), c.get("blocked", 0), c.get("unblocked", 0), c.get("props_stated", 0),
                  len(classes), c.get("maps", 0), c.get("goto_stated", 0), c.get("goto_unset", 0),
                  c.get("props_refused", 0), c.get("wait_stated", 0), c.get("wait_unstated", 0),
                  c.get("timer_stated", 0), c.get("timer_sentinel", 0), c.get("trigger_stated", 0),
                  c.get("trigger_unstated", 0), c.get("conditions", 0), c.get("wait_unstated_completes", 0),
                  c.get("wait_unstated_blocks", 0), c.get("wait_unstated_no_condition", 0),
                  c.get("wait_unstated_order_free", 0), c.get("refused_before", 0), c.get("relocated", 0),
                  c.get("relocated_props", 0)))
    return "\n".join(out)


def splice(path, block):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if DOC_START not in text or DOC_END not in text:
        sys.exit("markers not found in %s" % path)
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + "\n" + DOC_END + tail


def write_doc(path, rows):
    """Render first, then open: a missing marker must not leave the page truncated."""
    body = splice(path, render(rows)) if os.path.exists(path) else render(rows)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
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
        sys.stdout.write(render(list(csv.DictReader(fh))))


def check():
    """Synthetic blocks only: stated and unstated parameters, the slot-0 goto, the timer sentinel, the residues,
    and the condition a wait with no event resolves against."""
    action_cls = {"ACTION_GotoAction", "ACTION_WaitForTimer", "ACTION_WaitForEvent", "ACTION_Talk",
                  "ACTION_TriggerEvent", "Action_PLAYANIM", "ACTION_EndSection"}
    sequence_cls = {"ScriptedTrigger"}
    entries = [{"class": "Level", "name": "L"}, {"class": "ScriptedTrigger", "name": "S"},
               {"class": "ACTION_GotoAction", "name": "G0"}, {"class": "ACTION_GotoAction", "name": "G1"},
               {"class": "ACTION_WaitForTimer", "name": "T0"}, {"class": "ACTION_WaitForTimer", "name": "T1"},
               {"class": "ACTION_WaitForEvent", "name": "W0"}, {"class": "ACTION_WaitForEvent", "name": "W1"},
               {"class": "ACTION_EndSection", "name": "E"},
               {"class": "TriggeredCondition", "name": "C0"}, {"class": "TriggeredCondition", "name": "C1"},
               {"class": "ACTION_TriggerEvent", "name": "X0"}]
    blocks = {2: {"props": [], "actions": (7, [3, 4, 5, 6, 7, 9, 12])},
              3: {"props": [], "actions": None},                       # goto at slot 0, states nothing
              4: {"props": [{"name": "ActionNumber", "type": "Int", "value": 5}], "actions": None},
              5: {"props": [{"name": "PauseTime", "type": "Float", "value": 99999.0}], "actions": None},
              6: {"props": [{"name": "PauseTime", "type": "Float", "value": 0.5}], "actions": None},
              7: {"props": [], "actions": None},                       # wait with no ExternalEvent
              8: {"props": [{"name": "ExternalEvent", "type": "Name", "value": "EV"},
                            {"name": "bCheckRecentEvents", "type": "Bool", "value": True}], "actions": None},
              10: {"props": [{"name": "bEnabled", "type": "Bool", "value": True}], "actions": None},
              11: {"props": [], "actions": None},                      # condition that states no bEnabled
              12: {"props": [], "actions": None}}                      # fire with no Event
    slots, slot_count = slot_index(blocks, [e["class"] for e in entries], sequence_cls)
    conds = condition_index(entries, blocks)
    if [c["index"] for c in conds] != [10, 11] or conds[0]["enabled"] is not True or conds[1]["enabled"] is not None:
        print("FAIL action-parameters: condition index %r" % (conds,))
        return 1
    per_class, per_prop, census, residues = measure(entries, blocks, action_cls, sequence_cls, slots, "",
                                                   None, None, slot_count, conds)
    want = dict(placements=8, blocked=7, unblocked=1, goto_placements=2, goto_stated=1, goto_unset=1,
                goto_unset_slot0=1, goto_forward=1, wait_stated=1, wait_unstated=1, wait_recent=1,
                timer_placements=2, timer_stated=2, timer_sentinel=1, trigger_placements=1, trigger_unstated=1,
                wait_unstated_completes=1, wait_unstated_order_free=0)
    for key, value in want.items():
        if census[key] != value:
            print("FAIL action-parameters: synthetic %s=%r, want %r" % (key, census[key], value))
            return 1
    if len(residues) != 3 or {r["name"] for r in residues} != {"goto_unset_slot0", "wait_unstated",
                                                              "trigger_unstated"}:
        print("FAIL action-parameters: residues %r" % (residues,))
        return 1
    notes = [r["note"] for r in residues if r["name"] == "wait_unstated"]
    if not all("outcome=completes" in n and "first=stated true" in n for n in notes):
        print("FAIL action-parameters: wait notes %r" % (notes,))
        return 1
    # A disabled first condition blocks, and a map that places none does too -- the other two arms.
    if wait_verdict([{"index": 1, "enabled": None}]) != ("blocks", True):
        print("FAIL action-parameters: an unstated bEnabled does not block")
        return 1
    if wait_verdict([]) != ("no_condition", True):
        print("FAIL action-parameters: an empty condition list does not report no_condition")
        return 1
    timer = per_prop[("ACTION_WaitForTimer", "PauseTime", "Float")]
    if timer["stated"] != 2 or sorted(round(v) for v in timer["values"]) != [0, 99999]:
        print("FAIL action-parameters: timer values %r" % (timer,))
        return 1
    rows = rows_of({"A": {"class": per_class, "census": census}}, per_class, per_prop, census, residues, 6)
    got = census_of(rows)
    if (got["placements"], got["goto_unset"], got["maps"]) != (8, 1, 1):
        print("FAIL action-parameters: census rows %r" % (got,))
        return 1
    block = render(rows)
    for want_text in ("slot 0", "PauseTime", "distinct", "Census: placements=8", "sequence slot",
                      "no-op, not a broadcast", "re-located"):
        if want_text not in block:
            print("FAIL action-parameters: render is missing %r" % want_text)
            return 1
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "t.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in FIELDS})
        with open(path, newline="", encoding="utf-8") as fh:
            back = list(csv.DictReader(fh))
        if len(back) != len(rows) or set(back[0]) != set(FIELDS):
            print("FAIL action-parameters: artifact round trip %d/%d" % (len(back), len(rows)))
            return 1
    # The refused-row re-location: the declared fraction beats the tag count, a type-0 tag breaks the tie, and a
    # block is adopted only when it refuses strictly fewer names.
    allowed = {"Message", "bBroadcast"}
    got = choose([(0, [{"name": "Message", "type": "Str"}, {"name": "bBroadcast", "type": "Bool"}]),
                  (4, [{"name": "Max", "type": "Bool"}, {"name": "Location", "type": "Bool"},
                       {"name": "Region", "type": "Byte"}])], allowed)
    if got[0] != 0 or refused_names(got[1], allowed):
        print("FAIL action-parameters: the declared fraction does not beat the tag count")
        return 1
    got = choose([(0, [{"name": "Message", "type": "Invalid"}, {"name": "Max", "type": "Byte"}]),
                  (6, [{"name": "Message", "type": "Str"}, {"name": "Max", "type": "Byte"}])], allowed)
    if got[0] != 6:
        print("FAIL action-parameters: an Invalid tag does not break the tie")
        return 1
    if not better_block(["EndType"], []) or better_block(["EndType"], ["EndType"]):
        print("FAIL action-parameters: a re-located block is adopted without refusing fewer names")
        return 1
    print("ok   action-parameters: stated and unstated parameters, the slot-0 goto, the 99999 timer, the "
          "trigger with no name, and the condition an untagged wait resolves against are told apart")
    return 0


def main():
    cli = argparse.ArgumentParser(description="every parameter a placed scripted action carries (PLAN 5.2)")
    cli.add_argument("dump", nargs="?", help="game directory (.../Stargate_SG-1_-_The_Alliance-2005-12-15)")
    cli.add_argument("--sweep", action="store_true", help="read every map and write the artifact")
    cli.add_argument("-o", "--out", help="artifact directory (inventory)")
    cli.add_argument("--only", help="one map, by name fragment")
    cli.add_argument("--report", action="store_true", help="render the committed artifact")
    cli.add_argument("--write-doc", metavar="PATH", help="splice the generated block into a page")
    cli.add_argument("--check-doc", metavar="PATH", help="fail when the page does not match the artifact")
    cli.add_argument("--relocate", action="store_true",
                     help="candidate census: does the declared-name chooser pick the locator's start?")
    cli.add_argument("--check", action="store_true", help="synthetic self-test, no dump, writes nothing")
    args = cli.parse_args()
    if args.check:
        return check()
    if args.relocate:
        if not args.dump:
            sys.exit("--relocate needs the game directory")
        relocation_census(args.dump, args.out, only=args.only)
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
    cli.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
