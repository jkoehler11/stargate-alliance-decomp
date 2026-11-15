#!/usr/bin/env python3
"""The third hop of the outer edge: whether the placement that fires a name is itself reached (PLAN 5.2).

`docs/MAP_ACTION_PATHS.md` closed with what the package still could not say: "whether the firing placement is
itself reached is still a runtime question". It is not a runtime question -- the firing placement is a *placed
action*, and the three readings this project already has decide whether it runs:

* `docs/MAP_ACTION_WIRING.md` -- an action runs only if an `Actions` array names it;
* `docs/MAP_ACTION_ORDER.md` / `docs/MAP_ACTION_PATHS.md` -- a slot no path reaches never runs;
* `docs/MAP_EVENT_TARGETS.md` -- a sequence starts only if its own `Tag` is a name some placed property fires.

Chaining them is a *closure*, not a count: a sequence starts either with no name at all (its own `PostBeginPlay`
or `Touch` starts the controller -- measured from the class's shipped text, not assumed) or because a fire whose
carrier is reached names its tag, and a carrier is reached only because some started sequence names it at a slot
on some path. So the answer is the fixed point that begins at the self-starting volumes and grows through the
fires: every fire site and every sequence is either in that set or behind nothing that starts it. A cycle with no
seed in it never starts, which is what a plain two-hop count cannot see.

    python3 tools/action-reach.py DUMP --sweep -o inventory
    python3 tools/action-reach.py --report
    python3 tools/action-reach.py --write-doc docs/MAP_ACTION_REACH.md
    python3 tools/action-reach.py --check-doc docs/MAP_ACTION_REACH.md
    python3 tools/action-reach.py --check          # synthetic, no dump, writes nothing

`DUMP` is the game directory (`.../Stargate_SG-1_-_The_Alliance-2005-12-15`). Rows carry counts and class names
only: no placement name, no tag value and no class text is written.
"""
import argparse
import collections
import csv
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from ue2pkg import Package  # noqa: E402

ARTIFACT = os.path.join(ROOT, "inventory/action_reach.csv")
DOC = os.path.join(ROOT, "docs/MAP_ACTION_REACH.md")
PATHS = os.path.join(ROOT, "inventory/action_paths.csv")
TARGETS = os.path.join(ROOT, "inventory/event_targets.csv")
CLASSES = os.path.join(ROOT, "inventory/classes.csv")
DOC_START = "<!-- generated:action-reach -->"
DOC_END = "<!-- /generated:action-reach -->"

FIELDS = ["kind", "name", "exports", "holders", "arrays", "arrays_all", "armed_all", "elements", "fires", "carriers", "carrier_action",
          "carrier_other", "reached", "dead", "outside", "landings", "armed", "seeded", "started", "started_fire",
          "stalled", "rounds", "wired", "wired_armed", "wired_started", "wired_started_reach",
          "wired_stalled", "note"]

# A body that starts the sequence with no name at all: `ScriptedTrigger`'s `PostBeginPlay` spawns its controller and
# hands it itself, and `TriggeredScript` does the same from `Touch`. Both are in the class's own shipped text.
START_CALLS = ("InitializeFor(", "ScriptedTriggerPostBeginPlay(")
START_BODIES = ("PostBeginPlay", "Touch", "BeginPlay")
FUNC_HEAD = re.compile(r"(?m)^\s*(?=(?:simulated\s+|final\s+|latent\s+)*function\s+\w+)")
MAX_ROUNDS = 64


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ap = _load("action_paths", "action-paths.py")
ao = ap.ao
aw = ap.aw
et = ap.et


def function_bodies(text):
    """`{name: [body text]}` for every `function` in a class's own shipped source. Structure, not a quote."""
    out = collections.defaultdict(list)
    for chunk in FUNC_HEAD.split(text or ""):
        m = re.match(r"\s*(?:simulated\s+|final\s+|latent\s+)*function\s+(?:[\w<>\[\]]+\s+)?(\w+)\s*\(", chunk)
        if m:
            out[m.group(1)].append(chunk)
    return out


def starts_without_name(text):
    """True when the class's own body starts its run from a lifecycle event rather than from a fired name."""
    bodies = function_bodies(text)
    for name in START_BODIES:
        for body in bodies.get(name, []):
            if any(call in body for call in START_CALLS):
                return True
    return False


def self_start_classes(system_dir, sequence_cls):
    """The sequence classes that need no name: measured from each class's shipped text."""
    pst = _load("pkg_script_text", "pkg-script-text.py")
    found = set()
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
            if cls in sequence_cls and starts_without_name(pst.script_text(pkg, exp)):
                found.add(cls)
    return found


def holder_walk(classes, exports, rules, gated, values, targets, sequence_cls, self_start_cls):
    """One map -> `{export: {refs, seen, armed, seed, holder}}` for every holder of an `Actions` array."""
    fired = {name.lower() for rec in exports.values() for name in rec.get("fires") or []}
    out = {}
    for idx in sorted(exports):
        rec = exports[idx]
        if rec.get("actions") is None:
            continue
        holder = classes[idx - 1] if 0 < idx <= len(classes) else ""
        if holder not in sequence_cls:
            continue
        _count, refs = rec["actions"]
        arr_classes = ap.slots_of(refs, classes)
        seen = ap.reachable(refs, arr_classes, rules, holder in gated, values, targets)
        tag = (rec.get("tag") or "").lower()
        out[idx] = dict(refs=refs, seen=seen, holder=holder,
                        armed=bool(tag) and tag != "none" and tag in fired,
                        seed=holder in self_start_cls)
    return out, fired


def closure(holders, exports, tags):
    """The start set as a fixed point from the sequences that need no name.

    Returns `(started, reached, rounds)`: the holders that start, the exports a started sequence names at a slot
    some path reaches (the carriers that are `reached`), and how many rounds the closure took.
    """
    started = {idx for idx, info in holders.items() if info["seed"]}
    fires = [(idx, name.lower()) for idx, rec in exports.items() for name in rec.get("fires") or []]
    reached = set()
    rounds = 0
    while rounds < MAX_ROUNDS:
        reached = set()
        for idx in started:
            info = holders[idx]
            for slot, ref in enumerate(info["refs"]):
                if slot in info["seen"] and ref > 0:
                    reached.add(ref)
        grew = False
        for idx, name in fires:
            if idx not in reached:
                continue
            for target in tags.get(name, []):
                if target in holders and target not in started:
                    started.add(target)
                    grew = True
        rounds += 1
        if not grew:
            break
    return started, reached, rounds


def measure(entries, exports, rules, gated, values, targets, sequence_cls, self_start_cls, action_cls):
    """One map -> (row, per-carrier-class, per-holder-class). No package needed, so `--check` can drive it."""
    classes = [e["class"] for e in entries]
    holders, fired = holder_walk(classes, exports, rules, gated, values, targets, sequence_cls, self_start_cls)
    tags = ap.tag_index(exports, entries)
    started, reached, rounds = closure(holders, exports, tags)
    named = {ref for info in holders.values() for ref in info["refs"] if 0 < ref <= len(classes)}
    row = dict(kind="map", name="", exports=len(entries), holders=len(holders), arrays=len(holders),
               arrays_all=0, armed_all=0,
               elements=sum(len(info["refs"]) for info in holders.values()), fires=0, carriers=0,
               carrier_action=0, carrier_other=0, reached=0, dead=0, outside=0, landings=0, armed=0,
               seeded=sum(1 for info in holders.values() if info["seed"]),
               started=len(started), started_fire=len([i for i in started if not holders[i]["seed"]]),
               stalled=len(holders) - len(started), rounds=rounds, wired=0, wired_armed=0, wired_started=0,
               wired_started_reach=0, wired_stalled=0, note="")
    per_carrier = collections.defaultdict(collections.Counter)
    per_holder = collections.defaultdict(collections.Counter)

    # the holder population `event-targets` counts: every export carrying an `Actions` array, whatever its class
    for idx in sorted(exports):
        if exports[idx].get("actions") is None:
            continue
        cls = classes[idx - 1] if 0 < idx <= len(classes) else ""
        if cls in sequence_cls:
            continue
        row["arrays_all"] += 1
        tag = (exports[idx].get("tag") or "").lower()
        if tag and tag != "none" and tag in fired:
            row["armed_all"] += 1

    for idx in sorted(exports):
        names = exports[idx].get("fires") or []
        if not names:
            continue
        row["carriers"] += 1
        cls = classes[idx - 1] if 0 < idx <= len(classes) else ""
        state = "reached" if idx in reached else ("dead" if idx in named else "outside")
        row[state] += 1
        row["carrier_action" if cls in action_cls else "carrier_other"] += 1
        per_carrier[cls][state] += 1
        per_carrier[cls]["carriers"] += 1
        for name in names:
            row["fires"] += 1
            per_carrier[cls]["fires"] += 1
            hits = tags.get(name.lower(), [])
            row["landings"] += len(hits)
            per_carrier[cls]["landings"] += len(hits)

    for idx, info in holders.items():
        per_holder[info["holder"]]["arrays"] += 1
        per_holder[info["holder"]]["elements"] += len(info["refs"])
        if info["armed"]:
            row["armed"] += 1
            per_holder[info["holder"]]["armed"] += 1
        if info["seed"]:
            per_holder[info["holder"]]["seeded"] += 1
        per_holder[info["holder"]]["started" if idx in started else "stalled"] += 1
        for slot, ref in enumerate(info["refs"]):
            if ref <= 0 or ref > len(classes) or (action_cls and classes[ref - 1] not in action_cls):
                continue
            row["wired"] += 1
            per_holder[info["holder"]]["wired"] += 1
            if info["armed"]:
                row["wired_armed"] += 1
                per_holder[info["holder"]]["wired_armed"] += 1
            if idx not in started:
                continue
            row["wired_started"] += 1
            per_holder[info["holder"]]["wired_started"] += 1
            if slot in info["seen"]:
                row["wired_started_reach"] += 1
                per_holder[info["holder"]]["wired_started_reach"] += 1
    row["wired_stalled"] = row["wired"] - row["wired_started_reach"]
    return row, per_carrier, per_holder


def sweep(dump, out, classes_path=CLASSES, only=None):
    maps_dir = os.path.join(dump, "Maps")
    system_dir = os.path.join(dump, "System")
    if not os.path.isdir(maps_dir):
        sys.exit("no Maps/ under %s" % dump)
    supers = ao.class_supers(classes_path)
    rules = ao.class_rules(system_dir, supers)
    sequence_cls = ao.family(supers, "ScriptedSequence") | {"ScriptedSequence"}
    action_cls = aw.family(supers, "ScriptedAction") - et.ACTION_BASES
    marks = {c for c, r in rules.items() if r["role"] == "holder" and r.get("gate")}
    gated = set(marks)
    for cls in supers:
        cur, trail = cls, set()
        while cur in supers and cur not in trail:
            trail.add(cur)
            if cur in marks:
                gated.add(cls)
                break
            cur = supers[cur]
    values = {c: r.get("gate_value") for c, r in rules.items() if r["role"] == "action"}
    self_start_cls = self_start_classes(system_dir, sequence_cls)
    print("self-starting sequence classes: %d of %d" % (len(self_start_cls), len(sequence_cls)), flush=True)
    reference = _reference()
    rows = []
    per_carrier = collections.defaultdict(collections.Counter)
    per_holder = collections.defaultdict(collections.Counter)
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
        exports = ap.read_exports(pkg)
        targets = {idx: r["target"] for idx, r in exports.items() if r.get("target") is not None}
        row, carriers, holders = measure(entries, exports, rules, gated, values, targets, sequence_cls,
                                        self_start_cls, action_cls)
        row["name"] = label
        rows.append(row)
        for cls, info in carriers.items():
            per_carrier[cls].update(info)
        for cls, info in holders.items():
            per_holder[cls].update(info)
        print("%-26s fires=%-5d reached=%-4d dead=%-4d outside=%-4d holders=%-4d started=%-4d rounds=%-2d "
              "wired=%-5d chain=%-5d" % (label, row["fires"], row["reached"], row["dead"], row["outside"],
                                         row["holders"], row["started"], row["rounds"], row["wired"],
                                         row["wired_started_reach"]), flush=True)
    for cls in sorted(per_carrier, key=lambda c: (-per_carrier[c]["fires"], c)):
        r = dict(kind="carrier", name=cls)
        r.update({k: per_carrier[cls].get(k, 0) for k in ("carriers", "fires", "reached", "dead", "outside",
                                                          "landings")})
        r["note"] = "action" if cls in action_cls else "other"
        rows.append(r)
    for cls in sorted(per_holder, key=lambda c: (-per_holder[c]["wired"], -per_holder[c]["arrays"], c)):
        r = dict(kind="holder", name=cls)
        r.update({k: per_holder[cls].get(k, 0) for k in
                  ("arrays", "elements", "armed", "seeded", "started", "stalled", "wired", "wired_armed",
                   "wired_started", "wired_started_reach")})
        r["note"] = "self_start" if cls in self_start_cls else ""
        rows.append(r)
    census(rows, control=reference, self_start_cls=self_start_cls, holders=len(per_holder))
    if out:
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, "action_reach.csv")
        write_artifact(rows, path)
        print("%s: %d rows" % (path, len(rows)))
    return rows


def _reference():
    """What the two committed artifacts already say about the same populations, for the controls."""
    out = {}
    for path, keys in ((PATHS, ("arrays", "elements", "reach")),
                       (TARGETS, ("fires", "armed", "wired", "wired_armed"))):
        try:
            with open(path, newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    if r.get("kind") == "census":
                        for key in keys:
                            out[key] = int(r.get(key) or 0)
        except OSError:
            pass
    return out


def census(rows, control=None, self_start_cls=frozenset(), holders=0):
    maps = [r for r in rows if r["kind"] == "map"]
    row = dict(kind="census", name="corpus")
    keys = ("exports", "holders", "arrays", "arrays_all", "armed_all", "elements", "fires", "carriers", "carrier_action", "carrier_other",
            "reached", "dead", "outside", "landings", "armed", "seeded", "started", "started_fire", "stalled",
            "wired", "wired_armed", "wired_started", "wired_started_reach", "wired_stalled")
    for key in keys:
        row[key] = sum(int(r.get(key, 0) or 0) for r in maps)
    row["rounds"] = max([int(r.get("rounds", 0) or 0) for r in maps] or [0])
    control = control or {}
    note = ["maps=%d" % len(maps), "holder_classes=%d" % holders, "self_start_classes=%d" % len(self_start_cls)]
    for key in ("arrays", "elements", "reach"):
        if key in control:
            note.append("ref_%s=%d" % (key, control[key]))
    for key in ("fires", "armed", "wired", "wired_armed"):
        if key in control:
            note.append("targets_%s=%d" % (key, control[key]))
    row["note"] = ";".join(note)
    rows.append(row)
    return row


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
    carriers = [r for r in rows if r["kind"] == "carrier"]
    holders = [r for r in rows if r["kind"] == "holder"]
    out = []
    out.append("**Of the %d actions the maps wire, %d sit in a sequence that starts and at a slot some path "
               "reaches; the closure that says so begins at %d sequences that need no name at all.**"
               % (num(c, "wired"), num(c, "wired_started_reach"), num(c, "seeded")))
    out.append("")
    out.append("`docs/MAP_ACTION_PATHS.md` left one question open -- \"whether the firing placement is itself "
               "reached is still a runtime question\". The firing placement is a placed action, so the three "
               "readings this project already has decide it: an action runs only if an `Actions` array names it "
               "(`docs/MAP_ACTION_WIRING.md`), only if its slot is on some path (`docs/MAP_ACTION_PATHS.md`), and "
               "only if the sequence holding that slot starts (`docs/MAP_EVENT_TARGETS.md`). Chaining them is a "
               "closure, not a count -- a sequence starts because a reached carrier fires its tag, and a carrier "
               "is reached because a started sequence names it -- so this walk grows the start set outward from "
               "the sequences that start by themselves and stops when nothing new starts.")
    out.append("")
    out.append("| measure | answer |")
    out.append("|---|---|")
    out.append("| maps read | **%s** |" % vals.get("maps", "?"))
    out.append("| sequence classes carrying an `Actions` array | **%s** |" % vals.get("holder_classes", "?"))
    out.append("| of those, classes that start with no name | **%s** |" % vals.get("self_start_classes", "?"))
    out.append("| sequences carrying an array (the six scripted classes) | **%s** |" % c["arrays"])
    out.append("| exports carrying an array at all (every class: `event-targets`' population) | **%s** |"
               % (num(c, "arrays") + num(c, "arrays_all")))
    out.append("| -- of those, a `Tag` some placed property fires | **%s** (%s scene-manager camera arrays) |"
               % (num(c, "armed") + num(c, "armed_all"), num(c, "armed_all")))
    out.append("| slots wired | **%s** |" % c["elements"])
    out.append("| fire sites | **%s** over %s carrier actors |" % (c["fires"], c["carriers"]))
    out.append("| -- carrier is a placed scripted action | **%s** (%s carriers of other classes) |"
               % (c["carrier_action"], c["carrier_other"]))
    out.append("| -- carrier reached (a started sequence names it, on a path) | **%s** |" % c["reached"])
    out.append("| -- carrier wired but on no path a started sequence reaches | **%s** |" % c["dead"])
    out.append("| -- carrier no array names (volume, mover, native start) | **%s** |" % c["outside"])
    out.append("| fire-to-actor landings | **%s** |" % c["landings"])
    out.append("| sequences whose tag some placed property fires (armed) | **%s** |" % c["armed"])
    out.append("| -- in the start closure by their own class | **%s** |" % c["seeded"])
    out.append("| -- in the start closure through a chain of fires | **%s** |" % c["started_fire"])
    out.append("| sequences in the closure altogether | **%s** |" % c["started"])
    out.append("| sequences no chain reaches (stalled) | **%s** |" % c["stalled"])
    out.append("| rounds the closure took (deepest chain) | **%s** |" % c["rounds"])
    out.append("| actions wired by an array | **%s** |" % c["wired"])
    out.append("| -- in a sequence whose tag a fire names (armed) | **%s** |" % c["wired_armed"])
    out.append("| -- in a started sequence | **%s** |" % c["wired_started"])
    out.append("| -- there and on a reached slot (the full chain) | **%s** |" % c["wired_started_reach"])
    out.append("| -- behind nothing that starts, or off the paths | **%s** |" % c["wired_stalled"])
    out.append("")
    out.append("A fire site's carrier is classified by where placed data puts *it*. `reached` means a started "
               "sequence's `Actions` array names it at a slot on some path; `dead` means an array names it but only "
               "at slots the paths do not reach, or inside a sequence nothing starts; `outside` means no array "
               "names it at all -- a door `Mover`, an `Emitter`, a spawner, which the engine starts and no property "
               "in the map records. The self-starting classes are read from their own shipped text, not assumed: "
               "`ScriptedTrigger`'s `PostBeginPlay` spawns its controller and calls `InitializeFor(self)`, and "
               "`TriggeredScript` does the same from `Touch`.")
    out.append("")
    out.append("**What the closure adds over the two-hop count.** `docs/MAP_EVENT_TARGETS.md` reported %s wired "
               "actions in a sequence whose tag is fired -- an arm, not a run, because the fire that arms it may "
               "itself sit in a sequence nothing starts. The closure answers both directions at once: %s wired "
               "actions are in a started sequence, %s of those on a reached slot, and %s lie behind no start at "
               "all. A cycle of sequences that fire each other and take no seed never enters the set, which is why "
               "the count is a fixed point rather than two hops."
               % (vals.get("targets_wired_armed", c["armed"]), c["wired_started"], c["wired_started_reach"],
                  c["wired_stalled"]))
    out.append("")
    out.append("### Which classes fire, and from where")
    out.append("")
    out.append("| class of the actor carrying the fire property | fire sites | carrier reached | carrier dead | "
               "carrier outside | landings |")
    out.append("|---|---:|---:|---:|---:|---:|")
    for r in carriers[:15]:
        out.append("| `%s` | %s | %s | %s | %s | %s |"
                   % (r["name"], r["fires"], r["reached"], r["dead"], r["outside"], r["landings"]))
    out.append("")
    out.append("### Which classes carry the arrays, and how much of each is in the closure")
    out.append("")
    out.append("| sequence class | arrays | armed | started | stalled | wired | in a started sequence | "
               "there and on a path |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in holders[:12]:
        out.append("| `%s`%s | %s | %s | %s | %s | %s | %s | %s |"
                   % (r["name"], " (starts with no name)" if r.get("note") == "self_start" else "",
                      r["arrays"], r["armed"], r["started"], r["stalled"], r["wired"], r["wired_started"],
                      r["wired_started_reach"]))
    out.append("")
    out.append("Census: maps=%s;fires=%s;reached=%s;dead=%s;outside=%s;holders=%s;started=%s;stalled=%s;"
               "rounds=%s;wired=%s;wired_started=%s;wired_started_reach=%s."
               % (vals.get("maps", "?"), c["fires"], c["reached"], c["dead"], c["outside"], c["holders"],
                  c["started"], c["stalled"], c["rounds"], c["wired"], c["wired_started"],
                  c["wired_started_reach"]))
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
    """Synthetic maps only: the closure, the three carrier states, the chain, and the start rule."""
    rules = {
        "ACTION_Move": dict(moves=False),
        "ACTION_IfCondition": dict(opens=True, branches=True),
        "ACTION_EndSection": dict(closes=True),
        "ACTION_TriggerEvent": dict(moves=False),
    }
    sequence_cls = {"ScriptedTrigger", "TeamScriptedSequence"}
    action_cls = {"ACTION_Move", "ACTION_IfCondition", "ACTION_EndSection", "ACTION_TriggerEvent"}
    entries = [{"class": "Level", "name": "L"},                       # 1
               {"class": "ScriptedTrigger", "name": "SeqA"},          # 2 seed, wires 4 5 6
               {"class": "TeamScriptedSequence", "name": "SeqB"},     # 3 tag EV_B, wires 4
               {"class": "ACTION_Move", "name": "A0"},                # 4
               {"class": "ACTION_TriggerEvent", "name": "A1"},        # 5 fires EV_B
               {"class": "ACTION_Move", "name": "A2"},                # 6
               {"class": "Trigger", "name": "T0"}]                    # 7 fires EV_A
    exports = {2: dict(actions=(3, [4, 5, 6]), tag="EV_A", fires=[]),
               3: dict(actions=(1, [4]), tag="EV_B", fires=[]),
               5: dict(actions=None, tag="", fires=["EV_B"]),
               7: dict(actions=None, tag="", fires=["EV_A"])}

    def run(exp, seeds, rules_=rules, seq=sequence_cls, ents=entries):
        return measure(ents, exp, rules_, frozenset(), {}, {}, seq, seeds, action_cls)

    # 1 the seed starts by itself, its carrier arms SeqB, and SeqB's own action is then reached
    row, per_carrier, _ph = run(exports, {"ScriptedTrigger"})
    want = dict(fires=2, reached=1, dead=0, outside=1, landings=2, armed=2, seeded=1, started=2, started_fire=1,
                stalled=0, wired=4, wired_started=4, wired_started_reach=4, wired_stalled=0)
    for key, value in want.items():
        if row[key] != value:
            print("FAIL action-reach: synthetic closure %s=%r, want %r" % (key, row[key], value))
            return 1
    if row["rounds"] < 2:
        print("FAIL action-reach: the chain should take two rounds, took %r" % (row["rounds"],))
        return 1
    if per_carrier["Trigger"]["outside"] != 1 or per_carrier["ACTION_TriggerEvent"]["reached"] != 1:
        print("FAIL action-reach: per-carrier classes %r" % (dict(per_carrier),))
        return 1
    # 2 with no seed the same map starts nothing: a cycle of fires is not a start
    row2, _c2, _h2 = run(exports, set())
    if (row2["started"], row2["reached"], row2["dead"], row2["armed"], row2["wired_started_reach"],
            row2["wired_stalled"], row2["outside"]) != (0, 0, 1, 2, 0, 4, 1):
        print("FAIL action-reach: unseeded closure %r" % (row2,))
        return 1
    # 3 a carrier at a slot no path reaches is dead, and the sequence it would arm stays out of the closure
    exports3 = {2: dict(actions=(2, [4, 5]), tag="EV_A", fires=[]),
                5: dict(actions=None, tag="", fires=["EV_B"]),
                3: dict(actions=(1, [4]), tag="EV_B", fires=[])}
    rules3 = dict(rules)
    rules3["ACTION_Move"] = dict(leaves=True)            # the run ends at slot 0, so slot 1 is off every path
    row3, _c3, _h3 = run(exports3, {"ScriptedTrigger"}, rules3)
    if (row3["dead"], row3["reached"], row3["started"], row3["wired_started"], row3["wired_started_reach"],
            row3["wired_stalled"]) != (1, 0, 1, 2, 1, 2):
        print("FAIL action-reach: unreached carrier %r" % (row3,))
        return 1
    # 4 a branch's skip edge reaches the EndSection the fall-through never gets to: slot 1 ends the run and
    #   slot 2 is the body the skip jumps over, so only the skip edge reaches slot 3
    entries4 = [{"class": "Level", "name": "L"}, {"class": "ScriptedTrigger", "name": "Seq"},
                {"class": "TeamScriptedSequence", "name": "B"}, {"class": "ACTION_IfCondition", "name": "C"},
                {"class": "ACTION_LeaveSequence", "name": "X"}, {"class": "ACTION_Move", "name": "M"},
                {"class": "ACTION_EndSection", "name": "E"}]
    exports4 = {2: dict(actions=(4, [4, 5, 6, 7]), tag="EV_A", fires=[])}
    rules4 = dict(rules)
    rules4["ACTION_LeaveSequence"] = dict(leaves=True)
    action4 = action_cls | {"ACTION_LeaveSequence"}
    row4, _c4, _h4 = measure(entries4, exports4, rules4, frozenset(), {}, {}, sequence_cls, {"ScriptedTrigger"},
                             action4)
    if (row4["wired"], row4["wired_started"], row4["wired_started_reach"]) != (4, 4, 3):
        print("FAIL action-reach: branch skip edge %r" % (row4,))
        return 1
    # 5 a non-action element is not a wired action even when a path contains it
    exports5 = {2: dict(actions=(1, [7]), tag="EV_A", fires=[]),
                7: dict(actions=None, tag="", fires=["EV_A"])}
    row5, _c5, _h5 = run(exports5, {"ScriptedTrigger"})
    if (row5["elements"], row5["wired"], row5["wired_started_reach"]) != (1, 0, 0):
        print("FAIL action-reach: non-action element %r" % (row5,))
        return 1
    # 6 the start rule reads the class's own body
    trig = ("function PostBeginPlay()\n{\n\tSuper.PostBeginPlay();\n"
            "\tTriggerController = Spawn(class'ScriptedTriggerController', Self, Self.Name);\n"
            "\tTriggerController.InitializeFor(self);\n}\n")
    tscript = "function Touch( actor Other )\n{\n\tScriptedTriggerPostBeginPlay();\n}\n"
    plain = ("function PostBeginPlay()\n{\n\tSuper.PostBeginPlay();\n}\n"
             "function Trigger( actor Other, pawn EventInstigator )\n{\n\tTakeOver(EventInstigator);\n}\n")
    if not starts_without_name(trig) or not starts_without_name(tscript) or starts_without_name(plain):
        print("FAIL action-reach: start rule %r %r %r" % (starts_without_name(trig),
                                                          starts_without_name(tscript),
                                                          starts_without_name(plain)))
        return 1
    if starts_without_name("var int InitializeFor_Count;\nfunction Other()\n{\n\tx = 1;\n}\n"):
        print("FAIL action-reach: a declaration is not a call")
        return 1
    # 7 the census totals come from their own row, and the artifact round-trips
    rows = [dict(row, name="A"), dict(row2, name="B")]
    total = census(list(rows), control=_reference(), self_start_cls={"ScriptedTrigger"}, holders=2)
    if (total["fires"], total["started"], total["wired_armed"], total["wired_started_reach"],
            total["wired_stalled"]) != (4, 2, 8, 4, 4):
        print("FAIL action-reach: census totals %r" % (total,))
        return 1
    block = render(list(rows) + [total])
    for want_text in ("Of the", "carrier reached", "Census: maps=2"):
        if want_text not in block:
            print("FAIL action-reach: render is missing %r" % want_text)
            return 1
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "t.csv")
        write_artifact(rows, path)
        with open(path, newline="", encoding="utf-8") as fh:
            back = list(csv.DictReader(fh))
        if len(back) != 2 or back[0]["name"] != "A" or set(back[0]) != set(FIELDS):
            print("FAIL action-reach: artifact round trip %r" % (back[:1],))
            return 1
    print("ok   action-reach: the start set is a closure from the classes that need no name, a carrier is reached "
          "only through a started sequence's on-path slot, and the start rule reads the class body")
    return 0


def main():
    cli = argparse.ArgumentParser(description="whether the placement that fires a name is itself reached (PLAN 5.2)")
    cli.add_argument("dump", nargs="?", help="game directory (.../Stargate_SG-1_-_The_Alliance-2005-12-15)")
    cli.add_argument("--sweep", action="store_true", help="read every map and write the artifact")
    cli.add_argument("-o", "--out", help="artifact directory (inventory)")
    cli.add_argument("--only", help="one map, by name fragment")
    cli.add_argument("--report", action="store_true", help="render the committed artifact")
    cli.add_argument("--write-doc", metavar="PATH", help="splice the generated block into a page")
    cli.add_argument("--check-doc", metavar="PATH", help="fail when the page does not match the artifact")
    cli.add_argument("--check", action="store_true", help="synthetic self-test, no dump, writes nothing")
    args = cli.parse_args()
    if args.check:
        return check()
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
