#!/usr/bin/env python3
"""The second hop of the outer edge: which actor an event name lands on, and whether it arms a sequence (PLAN 5.2).

`docs/MAP_EVENT_WIRING.md` joined the names a map's sequences wait on to the names its placed actors fire, and
`docs/MAP_ACTION_WIRING.md` joined a sequence's `Actions` array to the placed actions it names. Both pages stop one
hop short of the question 5.2 has been asking since it found no level script: **is a placed action reachable at
all?** A fired name does not stop at the name -- `TriggerEvent('X')` addresses every actor whose `Tag` is `X` and
calls its `Trigger` event, so the name's real target is an *actor*, and an actor of the scripted family that
receives it starts running its own `Actions` array.

So this walk follows both edges and multiplies them:

* a fire site's name -> the actors tagged with it (the landing), and their classes;
* a sequence that carries an `Actions` array -> whether its own `Tag` is a name some placed actor fires (armed), or
  not (never armed by placed data);
* therefore every action a `Actions` array wires is either **in an armed sequence** -- reachable, on the data's own
  terms -- or **in a sequence nothing in the map starts**.

A tag is not unique: several actors can carry the same one, and a fire lands on all of them, so landings are
counted as pairs rather than names.

    python3 tools/event-targets.py DUMP/Maps --sweep -o inventory
    python3 tools/event-targets.py --report
    python3 tools/event-targets.py --write-doc docs/MAP_EVENT_TARGETS.md
    python3 tools/event-targets.py --check-doc docs/MAP_EVENT_TARGETS.md
    python3 tools/event-targets.py --check          # synthetic, no dump, writes nothing
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

ARTIFACT = os.path.join(ROOT, "inventory/event_targets.csv")
DOC = os.path.join(ROOT, "docs/MAP_EVENT_TARGETS.md")
CLASSES = os.path.join(ROOT, "inventory/classes.csv")
DOC_START = "<!-- generated:event-targets -->"
DOC_END = "<!-- /generated:event-targets -->"

FIELDS = ["kind", "map", "exports", "blocks", "sequences", "sequences_tagged", "armed", "wired", "wired_armed",
          "wired_tag_unfired", "wired_untagged", "wired_empty", "landings", "landing_classes", "fires", "tags",
          "shared_tags", "note"]

# The abstract heads of the scripted family: a placement of one names no behaviour of its own.
ACTION_BASES = {"ScriptedAction", "LatentScriptedAction"}
WAIT_PROPS = {"ExternalEvent"}


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


def is_fire(prop):
    return "event" in prop.lower() and prop not in WAIT_PROPS


def read_blocks(pkg, skip_classes=("Class", "Function", "State", "ScriptStruct", "Const")):
    """Per export (1-based): its `Tag`, the event names it fires, and its `Actions` array if it carries one.

    The array decode is `action-wiring`'s own: an `Actions` payload is a count followed by that many compact export
    indices, and a payload that does not consume itself exactly is refused rather than read short.
    """
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
        tag, fires, array = "", [], None
        for p in props:
            value = p.get("value")
            if p["name"] == "Tag" and p["type"] == "Name" and isinstance(value, str):
                tag = value
            elif p["type"] == "Name" and isinstance(value, str) and is_fire(p["name"]):
                fires.append(value)
            elif p["name"] == "Actions" and p["type"] == "Array":
                raw = data[start + p["value_offset"]:start + p["value_offset"] + p["size"]]
                array = aw.decode_array_refs(raw)
        if tag or fires or array:
            blocks[i + 1] = {"tag": tag, "fires": fires, "actions": array}
    return blocks


def measure(entries, blocks, action_cls=frozenset()):
    """One map -> a row plus the per-target-class landings.

    `entries` is `[{"class", "name", "outer"}]` and `blocks` what `read_blocks` returned; neither needs a package,
    which is what lets a synthetic map drive this in `--check`.
    """
    classes = [e["class"] for e in entries]
    row = dict(kind="map", map="", exports=len(entries), blocks=len(blocks), sequences=0, sequences_tagged=0,
               armed=0, wired=0, wired_armed=0, wired_tag_unfired=0, wired_untagged=0, wired_empty=0, landings=0,
               landing_classes=0, fires=0, tags=0, shared_tags=0, note="")
    tag_index = collections.defaultdict(list)
    for idx in blocks:
        tag = blocks[idx]["tag"]
        if tag:
            tag_index[tag.lower()].append(idx)
    fired = set()
    for idx in blocks:
        for name in blocks[idx]["fires"]:
            fired.add(name.lower())
    row["fires"] = sum(len(blocks[idx]["fires"]) for idx in blocks)
    row["tags"] = len(tag_index)
    row["shared_tags"] = sum(1 for t in tag_index if len(tag_index[t]) > 1)
    landings = collections.Counter()
    landing_names = set()
    for idx in blocks:
        for name in blocks[idx]["fires"]:
            low = name.lower()
            targets = tag_index.get(low, [])
            for target in targets:
                landings[classes[target - 1] if 0 < target <= len(classes) else ""] += 1
                landing_names.add(low)
    row["landings"] = sum(landings.values())
    row["landing_classes"] = len(landings)
    holders = [idx for idx in blocks if blocks[idx]["actions"] is not None]
    per_class = collections.defaultdict(lambda: {"seq": 0, "wired": 0, "armed": 0})
    row["sequences"] = len(holders)
    armed_holders = set()
    for idx in holders:
        count, refs = blocks[idx]["actions"]
        tag = blocks[idx]["tag"]
        is_armed = bool(tag) and tag.lower() in fired
        if is_armed:
            armed_holders.add(idx)
        if tag:
            row["sequences_tagged"] += 1
        if count == 0:
            row["wired_empty"] += 1
        holder_cls = classes[idx - 1] if 0 < idx <= len(classes) else ""
        per_class[holder_cls]["seq"] += 1
        for ref in refs:
            if ref <= 0 or ref > len(classes):
                continue
            if action_cls and classes[ref - 1] not in action_cls:
                continue
            per_class[holder_cls]["wired"] += 1
            if is_armed:
                per_class[holder_cls]["armed"] += 1
            row["wired"] += 1
            if is_armed:
                row["wired_armed"] += 1
            elif tag:
                row["wired_tag_unfired"] += 1
            else:
                row["wired_untagged"] += 1
    row["armed"] = len(armed_holders)
    return row, landings, landing_names, per_class


def sweep(maps_dir, out, classes_path=CLASSES, only=None):
    supers = class_supers(classes_path)
    action_cls = aw.family(supers, "ScriptedAction") - ACTION_BASES
    rows = []
    per_target = collections.Counter()
    per_holder = collections.defaultdict(lambda: {"seq": 0, "wired": 0, "armed": 0})
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
        except Exception as exc:                      # an unreadable map is not a zero
            rows.append(dict(kind="map", map=label, exports="", blocks="", sequences="", armed="", wired="",
                             wired_armed="", wired_tag_unfired="", wired_untagged="", wired_empty="",
                             landings="", landing_classes="",
                             fires="", tags="", shared_tags="", note="unreadable: %s" % exc))
            continue
        entries = [{"class": pkg.class_name_of(e), "name": pkg.name(e["name"]), "outer": e["outer"]}
                   for e in pkg.exports]
        blocks = read_blocks(pkg)
        row, landings, _names, per_class = measure(entries, blocks, action_cls)
        row["map"] = label
        rows.append(row)
        per_target.update(landings)
        for cls, info in per_class.items():
            for key in ("seq", "wired", "armed"):
                per_holder[cls][key] += info[key]
        print("%-28s seq=%-4s armed=%-4s wired=%-5s armed=%-5s landings=%-5s" %
              (label, row["sequences"], row["armed"], row["wired"], row["wired_armed"], row["landings"]),
              flush=True)
    for cls in sorted(per_target, key=lambda c: (-per_target[c], c)):
        rows.append(dict(kind="target", map=cls, exports="", blocks="", sequences="", armed="", wired="",
                         wired_armed="", wired_tag_unfired="", wired_untagged="", wired_empty="",
                         landings=str(per_target[cls]),
                         landing_classes="", fires="", tags="", shared_tags="", note=""))
    for cls in sorted(per_holder, key=lambda c: (-per_holder[c]["wired"], c)):
        rows.append(dict(kind="holder", map=cls, exports="", blocks="", sequences=str(per_holder[cls]["seq"]),
                         sequences_tagged="", armed=str(per_holder[cls]["armed"]),
                         wired=str(per_holder[cls]["wired"]), wired_armed="", wired_tag_unfired="",
                         wired_untagged="", wired_empty="", landings="",
                         landing_classes="", fires="", tags="", shared_tags="", note=""))
    census(rows)
    if out:
        path = os.path.join(out, "event_targets.csv")
        write_artifact(rows, path)
        print("%s: %d rows" % (path, len(rows)))
    return rows


def census(rows):
    maps = [r for r in rows if r["kind"] == "map" and r["exports"] != ""]
    total = dict(exports=0, blocks=0, sequences=0, sequences_tagged=0, armed=0, wired=0, wired_armed=0,
                 wired_tag_unfired=0, wired_untagged=0, wired_empty=0, landings=0, fires=0, tags=0,
                 shared_tags=0)
    for r in maps:
        for key in total:
            total[key] += int(r.get(key, 0) or 0)
    rows.append(dict(kind="census", map="corpus", exports=str(total["exports"]), blocks=str(total["blocks"]),
                     sequences=str(total["sequences"]), sequences_tagged=str(total["sequences_tagged"]),
                     armed=str(total["armed"]), wired=str(total["wired"]),
                     wired_armed=str(total["wired_armed"]), wired_tag_unfired=str(total["wired_tag_unfired"]),
                     wired_untagged=str(total["wired_untagged"]),
                     wired_empty=str(total["wired_empty"]), landings=str(total["landings"]),
                     landing_classes=str(len({r["map"] for r in rows if r["kind"] == "target"})),
                     fires=str(total["fires"]), tags=str(total["tags"]),
                     shared_tags=str(total["shared_tags"]),
                     note="maps=%d;unreadable=%d;holder_classes=%d;target_classes=%d;armed_maps=%d"
                          % (len(maps), len([r for r in rows if r["kind"] == "map" and r["exports"] == ""]),
                             len([r for r in rows if r["kind"] == "holder"]),
                             len([r for r in rows if r["kind"] == "target"]),
                             sum(1 for r in maps if int(r["armed"]) > 0))))
    return rows


def note_values(row):
    out = {}
    for part in row.get("note", "").split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            out[key.strip()] = int(value)
    return out


def render(rows):
    c = [r for r in rows if r["kind"] == "census"][0]
    maps = [r for r in rows if r["kind"] == "map" and r["exports"] != ""]
    targets = [r for r in rows if r["kind"] == "target"]
    holders = [r for r in rows if r["kind"] == "holder"]
    vals = note_values(c)
    wired, armed_w = int(c["wired"]), int(c["wired_armed"])
    out = []
    out.append("**%d of the %d actions a sequence's `Actions` array wires (%s) sit in a sequence whose own `Tag` "
               "some placed actor fires; %s sit in a sequence with a tag nothing fires, and %s in a sequence with "
               "no tag at all.**"
               % (armed_w, wired, "%.1f%%" % (100.0 * armed_w / wired if wired else 0.0),
                  c["wired_tag_unfired"], c["wired_untagged"]))
    out.append("")
    out.append("| measure | answer |")
    out.append("|---|---|")
    out.append("| maps read | **%d** |" % vals["maps"])
    out.append("| exports over those maps | **%s** |" % c["exports"])
    out.append("| exports carrying a tag, a fire or an `Actions` array | **%s** |" % c["blocks"])
    out.append("| fire sites | **%s** |" % c["fires"])
    out.append("| distinct tags in those maps | **%s** |" % c["tags"])
    out.append("| tags carried by more than one actor | **%s** |" % c["shared_tags"])
    out.append("| fire-to-actor landings | **%s** over %s classes |" % (c["landings"], c["landing_classes"]))
    out.append("| sequences carrying an `Actions` array | **%s** over %d classes |"
               % (c["sequences"], vals["holder_classes"]))
    out.append("| sequences carrying a tag at all | **%s of %s** |"
               % (c["sequences_tagged"], c["sequences"]))
    out.append("| sequences whose own `Tag` is a name something fires | **%s** in %d maps |"
               % (c["armed"], vals["armed_maps"]))
    out.append("| actions wired by an array | **%d** |" % wired)
    out.append("| -- in a sequence whose tag is fired | **%d** |" % armed_w)
    out.append("| -- in a sequence with a tag nothing fires | **%s** |" % c["wired_tag_unfired"])
    out.append("| -- in a sequence that carries no tag at all | **%s** |" % c["wired_untagged"])
    out.append("| sequences whose array is empty | **%s** |" % c["wired_empty"])
    out.append("")
    out.append("A fired name resolves through `Tag`, not through a pointer: `TriggerEvent('X')` walks every actor "
               "tagged `X`, so the landing count is a count of pairs and %s tags address more than one actor. That "
               "is why the second column of this page is a **class** histogram -- what receives an event is the "
               "class of the actor that carries the tag."
               % c["shared_tags"])
    out.append("")
    out.append("**This is the chain 5.2 has been asking about, in one number.** `docs/MAP_ACTION_WIRING.md` says "
               "which sequence names which action; `docs/MAP_EVENT_WIRING.md` says which name a placed actor fires "
               "and which name a sequence waits on. Neither answers *does it run*: an action in a sequence that "
               "nothing starts is wired and still dead. Joining the sequence's own `Tag` to the fire population "
               "does, and %s of %d wired actions survive that join -- a placement is not a run, and being named by "
               "an array is not one either."
               % (armed_w, wired))
    out.append("")
    top = holders[0] if holders else None
    two = holders[1] if len(holders) > 1 else {"map": "", "sequences": "0", "wired": "0", "armed": "0"}
    out.append("**The population is the classes', not one number's.** %s carries %s of the %s arrays and %s of the "
               "%s wired actions with %s of them in a sequence a fire arms; %s carries %s and %s with %s armed. The "
               "rest of the split is the finding: %s wired actions sit in a sequence whose tag nothing fires and %s "
               "in one that carries no tag, so a name-based read decides the %s that are armed and sizes -- but "
               "does not settle -- the rest."
               % (top["map"] if top else "", top["sequences"] if top else "0", c["sequences"],
                  top["wired"] if top else "0", c["wired"], top["armed"] if top else "0",
                  two["map"], two["sequences"], two["wired"], two["armed"],
                  c["wired_tag_unfired"], c["wired_untagged"], c["wired_armed"]))
    out.append("")
    out.append("A `ScriptedTrigger` is a touch volume as well as a sequence -- a pawn walking into it starts the "
               "array with no name involved -- which is why the armed column is not a verdict on the classes that "
               "carry a volume. For the classes that are pure sequences the tag is the only handle a package gives, "
               "and there the fired-name count is the one that says whether anything in the level starts them.")
    out.append("")
    out.append("### What receives an event")
    out.append("")
    out.append("| class of the actor carrying the tag | landings |")
    out.append("|---|---:|")
    for r in targets[:15]:
        out.append("| `%s` | %s |" % (r["map"], r["landings"]))
    out.append("")
    out.append("### Which classes carry the arrays")
    out.append("")
    out.append("| class | sequences with an `Actions` array | actions wired | in a fired-tag sequence |")
    out.append("|---|---:|---:|---:|")
    for r in holders[:12]:
        out.append("| `%s` | %s | %s | %s |" % (r["map"], r["sequences"], r["wired"], r["armed"]))
    out.append("")
    out.append("### The maps with the most wired actions nothing arms")
    out.append("")
    out.append("| map | wired | in a fired-tag sequence | tagged, tag never fired | no tag |")
    out.append("|---|---:|---:|---:|---:|")
    for r in sorted(maps, key=lambda r: (-(int(r["wired_untagged"]) + int(r["wired_tag_unfired"])), r["map"]))[:10]:
        out.append("| `%s` | %s | %s | %s | %s |"
                   % (r["map"], r["wired"], r["wired_armed"], r["wired_tag_unfired"], r["wired_untagged"]))
    out.append("")
    out.append("Census: maps=%d;sequences=%s;sequences_tagged=%s;armed=%s;wired=%s;wired_armed=%s;"
               "wired_tag_unfired=%s;wired_untagged=%s;landings=%s."
               % (vals["maps"], c["sequences"], c["sequences_tagged"], c["armed"], c["wired"],
                  c["wired_armed"], c["wired_tag_unfired"], c["wired_untagged"], c["landings"]))
    return "\n".join(out)


def read_artifact(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def splice(path, block):
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return None
    if DOC_START not in text or DOC_END not in text:
        return None
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + "\n" + DOC_END + tail


def self_check():
    bad = []
    entries = [{"class": "Level", "name": "myLevel", "outer": 0},          # 1
               {"class": "ScriptedTrigger", "name": "Seq0", "outer": 0},   # 2 armed: tag EV_A is fired
               {"class": "ScriptedTrigger", "name": "Seq1", "outer": 0},   # 3 never armed
               {"class": "ACTION_WaitForTimer", "name": "Action0", "outer": 0},   # 4
               {"class": "ACTION_WaitForTimer", "name": "Action1", "outer": 0},   # 5
               {"class": "Trigger", "name": "Touch0", "outer": 0},         # 6 fires EV_A
               {"class": "StaticMeshActor", "name": "Mesh0", "outer": 0}]  # 7 carries tag EV_A too
    blocks = {2: {"tag": "EV_A", "fires": [], "actions": (2, [4, 5])},
              3: {"tag": "EV_B", "fires": [], "actions": (1, [4])},
              6: {"tag": "", "fires": ["EV_A"], "actions": None},
              7: {"tag": "EV_A", "fires": [], "actions": None}}
    action_cls = {"ACTION_WaitForTimer"}
    row, landings, _names, _pc = measure(entries, blocks, action_cls)
    if (row["sequences"], row["sequences_tagged"], row["armed"], row["wired"], row["wired_armed"],
            row["wired_tag_unfired"], row["wired_untagged"]) != (2, 2, 1, 3, 2, 1, 0):
        bad.append("synthetic map: %r" % (row,))
    if (row["landings"], row["tags"], row["shared_tags"], row["fires"]) != (2, 2, 1, 1):
        bad.append("landing arms: %r" % (row,))
    if landings != {"ScriptedTrigger": 1, "StaticMeshActor": 1}:
        bad.append("landing classes: %r" % (landings,))
    # a fire that lands on no tag is still a fire site; it just lands nowhere
    row2, land2, _n, _pc2 = measure(entries, {6: {"tag": "", "fires": ["EV_Z"], "actions": None}}, action_cls)
    if (row2["fires"], row2["landings"]) != (1, 0):
        bad.append("unlanded fire: %r" % (row2,))
    # an array whose elements are not actions (a MatAction, say) is not a wired action
    row3, _l, _n, _pc3 = measure(entries, {2: {"tag": "EV_A", "fires": [], "actions": (1, [7])}}, action_cls)
    if (row3["sequences"], row3["wired"], row3["wired_armed"]) != (1, 0, 0):
        bad.append("non-action element: %r" % (row3,))
    # a sequence with no tag can never be armed by a fire
    row4, _l, _n, _pc4 = measure(entries, {2: {"tag": "", "fires": [], "actions": (1, [4])},
                                    6: {"tag": "", "fires": ["EV_A"], "actions": None}}, action_cls)
    if (row4["armed"], row4["wired_untagged"], row4["wired_tag_unfired"]) != (0, 1, 0):
        bad.append("untagged sequence: %r" % (row4,))
    # an empty array is a sequence that runs nothing, and it is counted apart
    row5, _l, _n, _pc5 = measure(entries, {2: {"tag": "EV_A", "fires": [], "actions": (0, [])}}, action_cls)
    if (row5["sequences"], row5["wired_empty"], row5["wired"]) != (1, 1, 0):
        bad.append("empty array: %r" % (row5,))
    # the array decode refuses a payload that does not consume itself
    if aw.decode_array_refs(b"\x02\x01") is not None:
        bad.append("short payload accepted")
    rows = [dict(kind="map", map="A", exports="7", blocks="4", sequences="2", sequences_tagged="2", armed="1",
                 wired="3", wired_armed="2", wired_tag_unfired="0", wired_untagged="1", wired_empty="0",
                 landings="2", landing_classes="2", fires="1", tags="2", shared_tags="1", note=""),
            dict(kind="map", map="B", exports="1", blocks="1", sequences="1", sequences_tagged="1", armed="0",
                 wired="1", wired_armed="0", wired_tag_unfired="1", wired_untagged="0", wired_empty="0",
                 landings="0", landing_classes="0", fires="0", tags="1", shared_tags="0", note=""),
            dict(kind="target", map="ScriptedTrigger", exports="", blocks="", sequences="", sequences_tagged="",
                 armed="", wired="", wired_armed="", wired_tag_unfired="", wired_untagged="", wired_empty="",
                 landings="1", landing_classes="", fires="", tags="", shared_tags="", note=""),
            dict(kind="holder", map="ScriptedTrigger", exports="", blocks="", sequences="3",
                 sequences_tagged="", armed="", wired="", wired_armed="", wired_tag_unfired="",
                 wired_untagged="", wired_empty="", landings="", landing_classes="", fires="", tags="",
                 shared_tags="", note="")]
    census(rows)
    if (rows[-1]["wired"], rows[-1]["wired_armed"], rows[-1]["wired_tag_unfired"],
            rows[-1]["wired_untagged"], rows[-1]["armed"], rows[-1]["landings"]) != ("4", "2", "1", "1", "1", "2"):
        bad.append("census totals: %r" % (rows[-1],))
    if note_values(rows[-1]) != {"maps": 2, "unreadable": 0, "holder_classes": 1, "target_classes": 1,
                                 "armed_maps": 1}:
        bad.append("census note: %r" % (rows[-1]["note"],))
    block = render(rows)
    for want in ("2 of the 4 actions", "in a fired-tag sequence", "What receives an event"):
        if want not in block:
            bad.append("render is missing %r" % want)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "t.csv")
        write_artifact(rows, path)
        if read_artifact(path) != [{f: str(r.get(f, "")) for f in FIELDS} for r in rows]:
            bad.append("artifact round trip differs")
        if splice(path, "x") is not None:
            bad.append("splice found markers in a CSV")
    if bad:
        print("FAIL event-targets: " + "; ".join(bad))
        return 1
    print("ok   event-targets: a fired name lands on the actors tagged with it, a sequence is armed only when its "
          "own tag is fired, an unwired or non-action element never counts, and the census totals come from their "
          "own row")
    return 0


def main():
    if "--check" in sys.argv[1:]:
        return self_check()
    ap = argparse.ArgumentParser()
    ap.add_argument("maps", nargs="?", help="the dump's Maps directory")
    ap.add_argument("--sweep", action="store_true", help="every .unr -> inventory/event_targets.csv")
    ap.add_argument("--report", action="store_true", help="print the totals for a swept artifact")
    ap.add_argument("--only", default=None, help="sweep only the maps whose name contains this")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--write-doc", nargs="?", const=DOC, default=None)
    ap.add_argument("--check-doc", nargs="?", const=DOC, default=None)
    args = ap.parse_args()

    if args.write_doc or args.check_doc or args.report or not args.maps:
        if not os.path.isfile(ARTIFACT):
            print("no committed artifact: %s" % ARTIFACT)
            return 2
        rows = read_artifact(ARTIFACT)
    else:
        if not os.path.isdir(args.maps):
            print("no Maps directory given")
            return 2
        rows = sweep(args.maps, None, only=args.only)
        if args.out:
            path = os.path.join(args.out, "event_targets.csv")
            write_artifact(rows, path)
            print("%s: %d rows" % (path, len(rows)))
    block = render(rows)
    print(block)
    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        new = splice(path, block)
        if new is None:
            print("FAIL event-targets: markers not found in %s" % path)
            return 1
        old = open(path, encoding="utf-8").read()
        if args.check_doc:
            if new != old:
                print("FAIL event-targets: %s is stale against the committed artifact" % path)
                return 1
            print("ok   event-targets: %s matches the committed artifact" % path)
            return 0
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
