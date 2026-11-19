#!/usr/bin/env python3
"""The outer edge of the flow graph: named events, fired and awaited (PLAN 5.2).

`docs/MAP_ACTION_WIRING.md` read the inner edge of a level's logic -- which sequence's `Actions` array names which
placed action -- and closed with its own next question: *"the outer edge is named events: `ACTION_WaitForEvent`
carries an `ExternalEvent` name, so the next read is a join between the names the waiters wait on and the names the
`TriggerEvent` actions fire."* This is that join.

An event in this engine is a **name**, not an index: `TriggerEvent('X')` addresses every actor whose `Tag` is `X`
and calls its `Trigger` event, so the names live in three places and all three are property values of placed
exports:

* a **wait site** -- `ExternalEvent` on an `ACTION_WaitForEvent` (scripted family) or a `MatActionWaitForEvent`
  (the scene family), the name a sequence blocks on;
* a **fire site** -- any other name-typed property whose name says event (`Event`, `StaticEvent`, `EventName`,
  `BumpEvent`, `OpenedEvent`, `SuccessEvent`, ...): the name a trigger, a `TriggerEvent` action or a mover fires
  when its own condition is met;
* a **tag** -- the `Tag` a fired name resolves to, which is what makes a fire land on an actor at all.

Names compare case-insensitively in UnrealScript, so the join folds case; the artifact keeps the classes and the
counts and never the names, which are game content.

    python3 tools/event-wiring.py DUMP/Maps --sweep -o inventory
    python3 tools/event-wiring.py --report
    python3 tools/event-wiring.py --write-doc docs/MAP_EVENT_WIRING.md
    python3 tools/event-wiring.py --check-doc docs/MAP_EVENT_WIRING.md
    python3 tools/event-wiring.py --check          # synthetic, no dump, writes nothing
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

ARTIFACT = os.path.join(ROOT, "inventory/event_wiring.csv")
DOC = os.path.join(ROOT, "docs/MAP_EVENT_WIRING.md")
CLASSES = os.path.join(ROOT, "inventory/classes.csv")
PROPERTIES = os.path.join(ROOT, "inventory/class_properties.csv")
DOC_START = "<!-- generated:event-wiring -->"
DOC_END = "<!-- /generated:event-wiring -->"

FIELDS = ["kind", "map", "exports", "blocks", "waits", "waits_scripted", "waits_matinee", "wait_names",
          "fires", "fires_undeclared", "fire_actors", "fire_names", "fires_landing", "fires_unwaited", "matched", "matched_names",
          "orphan", "orphan_tag", "orphan_name", "orphan_none", "orphan_cross", "note"]

# The one property that *waits*. Everything else whose name says event fires: a block only carries the
# properties an actor overrides, so an `Event` in a placed actor's block is a name the mapper set on purpose.
WAIT_PROPS = {"ExternalEvent"}
# The scene family carries the same wait under the same property name; counted apart so a cutscene wait is never
# read as a mission wait (the same split `Actions` needed).
MATINEE_ROOT = "MatAction"


def load_prop_stream():
    path = os.path.join(ROOT, "tools", "prop-stream.py")
    spec = importlib.util.spec_from_file_location("prop_stream", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ps = load_prop_stream()


def class_supers(path=CLASSES):
    """class -> super, from the committed class census (nothing rewrites this file)."""
    supers = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            supers[row["class"]] = row.get("super", "")
    return supers


def family(supers, root_name):
    """Classes whose `super` chain reaches `root_name` -- the family by the build's own hierarchy."""
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


def is_wait(prop):
    return prop in WAIT_PROPS


def is_fire(prop):
    low = prop.lower()
    return "event" in low and prop not in WAIT_PROPS


def read_blocks(pkg, skip_classes=("Class", "Function", "State", "ScriptStruct", "Const")):
    """Per export (1-based): its name-typed property values, `Tag` among them.

    Only name-typed values are kept: the join is between names, and an actor's `Event`/`ExternalEvent`/`Tag` are
    the three that carry this graph. `min_props` is 1 for the same reason `action-wiring` needs it: an action that
    overrides only `ExternalEvent` has a one-property block.
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
        vals = {}
        for p in props:
            if p["type"] == "Name" and isinstance(p.get("value"), str):
                vals.setdefault(p["name"], p["value"])
        if vals:
            blocks[i + 1] = vals
    return blocks


def measure(entries, blocks, matinee_cls=frozenset(), accepted=None, tag_arm=True):
    """One map -> (row, waits, fires) where the two are lists of (class, property, name).

    `entries` is `[{"class", "name", "outer"}]` and `blocks` what `read_blocks` returned; neither needs a package,
    which is what lets a synthetic map drive this in `--check`.
    """
    classes = [e["class"] for e in entries]
    objects = {e["name"].lower() for e in entries if e.get("name")}
    row = dict(kind="map", map="", exports=len(entries), blocks=len(blocks), waits=0, waits_scripted=0,
               waits_matinee=0, wait_names=0, fires=0, fires_undeclared=0, fire_actors=0, fire_names=0,
               fires_landing=0,
               fires_unwaited=0, matched=0, matched_names=0, orphan=0, orphan_tag=0, orphan_name=0,
               orphan_none=0, orphan_cross=0, note="")
    tags = set()
    for idx in blocks:
        tag = blocks[idx].get("Tag")
        if isinstance(tag, str) and tag:
            tags.add(tag.lower())
    waits, fires, undeclared = [], [], 0
    for idx in sorted(blocks):
        blk = blocks[idx]
        cls = classes[idx - 1] if 0 < idx <= len(classes) else ""
        for prop, value in blk.items():
            if not isinstance(value, str) or not value:
                continue
            if is_wait(prop):
                waits.append((cls, prop, value))
            elif is_fire(prop):
                # A property name that says event but that the class census does not declare as a name
                # property is the property reader having landed on an object name, not a fire site.
                if accepted is not None and not accepted(cls, prop):
                    undeclared += 1
                    continue
                fires.append((cls, prop, value))
    fired = {name.lower() for _c, _p, name in fires}
    waited = {name.lower() for _c, _p, name in waits}
    row["waits"] = len(waits)
    row["waits_matinee"] = sum(1 for c, _p, _n in waits if c in matinee_cls)
    row["waits_scripted"] = row["waits"] - row["waits_matinee"]
    row["wait_names"] = len(waited)
    row["fires"] = len(fires)
    row["fires_undeclared"] = undeclared
    row["fire_names"] = len(fired)
    row["fire_actors"] = len({idx for idx in blocks
                              if any(is_fire(p) and isinstance(v, str) and v for p, v in blocks[idx].items())})
    row["fires_landing"] = sum(1 for _c, _p, n in fires if n.lower() in tags)
    row["fires_unwaited"] = sum(1 for _c, _p, n in fires if n.lower() not in waited)
    matched_names = set()
    for _cls, _prop, name in waits:
        low = name.lower()
        if low in fired:
            row["matched"] += 1
            matched_names.add(low)
        else:
            row["orphan"] += 1
            if tag_arm and low in tags:
                row["orphan_tag"] += 1
            elif low in objects:
                row["orphan_name"] += 1
            else:
                row["orphan_none"] += 1
    row["matched_names"] = len(matched_names)
    # private: the cross-map pass reads these, the artifact never carries them
    row["_fired"] = fired
    row["_orphan_names"] = [name.lower() for _c, _p, name in waits
                            if name.lower() not in fired and name.lower() not in tags
                            and name.lower() not in objects]
    return row, waits, fires


def sweep(maps_dir, out, classes_path=CLASSES, only=None):
    supers = class_supers(classes_path)
    matinee_cls = family(supers, MATINEE_ROOT)
    declared = load_declared()
    rows = []
    per_wait_class = collections.Counter()
    per_fire_class = collections.Counter()
    per_fire_prop = collections.Counter()
    per_map = {}
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
            rows.append(dict(kind="map", map=label, exports="", blocks="", waits="", waits_scripted="",
                             waits_matinee="", wait_names="", fires="", fire_actors="", fire_names="",
                             fires_landing="", fires_unwaited="", matched="", matched_names="", orphan="",
                             orphan_tag="", orphan_name="", orphan_none="", orphan_cross="",
                             note="unreadable: %s" % exc))
            continue
        entries = [{"class": pkg.class_name_of(e), "name": pkg.name(e["name"]), "outer": e["outer"]}
                   for e in pkg.exports]
        def accepted(cls, prop, _declared=declared, _supers=supers):
            return declared_type(_declared, _supers, cls, prop) == "UNameProperty"

        row, waits, fires = measure(entries, read_blocks(pkg), matinee_cls, accepted)
        row["map"] = label
        rows.append(row)
        for cls, _prop, _name in waits:
            per_wait_class[cls] += 1
        for cls, prop, _name in fires:
            per_fire_class[cls] += 1
            per_fire_prop[prop] += 1
        per_map[label] = row
        print("%-28s waits=%-5s fires=%-5s matched=%-5s orphan=%-5s" %
              (label, row["waits"], row["fires"], row["matched"], row["orphan"]), flush=True)
    # Cross-map control: a level's event names are its own. A wait that nothing in its own map fires is not
    # rescued by a fire in another map unless this count says so.
    fired_by_map = {}
    for r in rows:
        if r["kind"] != "map" or r["exports"] == "":
            continue
        fired_by_map[r["map"]] = set(r.get("_fired", ()))
    cross = 0
    for r in rows:
        if r["kind"] != "map" or r["exports"] == "":
            continue
        others = set()
        for other, names in fired_by_map.items():
            if other != r["map"]:
                others |= names
        hit = sum(1 for low in r.get("_orphan_names", ()) if low in others)
        r["orphan_cross"] = hit
        cross += hit
    for cls in sorted(per_wait_class, key=lambda c: (-per_wait_class[c], c)):
        rows.append(dict(kind="waitclass", map=cls, exports="", blocks="", waits=str(per_wait_class[cls]),
                         waits_scripted="", waits_matinee="", wait_names="", fires="", fire_actors="",
                         fire_names="", fires_landing="", fires_unwaited="", matched="", matched_names="",
                         orphan="", orphan_tag="", orphan_name="", orphan_none="", orphan_cross="", note=""))
    for cls in sorted(per_fire_class, key=lambda c: (-per_fire_class[c], c)):
        rows.append(dict(kind="fireclass", map=cls, exports="", blocks="", waits="", waits_scripted="",
                         waits_matinee="", wait_names="", fires=str(per_fire_class[cls]), fire_actors="",
                         fire_names="", fires_landing="", fires_unwaited="", matched="", matched_names="",
                         orphan="", orphan_tag="", orphan_name="", orphan_none="", orphan_cross="", note=""))
    for prop in sorted(per_fire_prop, key=lambda c: (-per_fire_prop[c], c)):
        rows.append(dict(kind="fireprop", map=prop, exports="", blocks="", waits="", waits_scripted="",
                         waits_matinee="", wait_names="", fires=str(per_fire_prop[prop]), fire_actors="",
                         fire_names="", fires_landing="", fires_unwaited="", matched="", matched_names="",
                         orphan="", orphan_tag="", orphan_name="", orphan_none="", orphan_cross="", note=""))
    census(rows, cross)
    if out:
        path = os.path.join(out, "event_wiring.csv")
        write_artifact(_strip_private(rows), path)
        print("%s: %d rows" % (path, len(rows)))
    return rows


def census(rows, cross=0):
    """Corpus totals in their own row, so every rendering reads one set of numbers."""
    maps = [r for r in rows if r["kind"] == "map" and r["exports"] != ""]
    total = dict(exports=0, blocks=0, waits=0, waits_scripted=0, waits_matinee=0, fires=0,
                 fires_undeclared=0, fire_actors=0,
                 fires_landing=0, fires_unwaited=0, matched=0, orphan=0, orphan_tag=0, orphan_name=0,
                 orphan_none=0)
    for r in maps:
        for key in total:
            total[key] += int(r.get(key, 0) or 0)
    rows.append(dict(kind="census", map="corpus", exports=str(total["exports"]), blocks=str(total["blocks"]),
                     waits=str(total["waits"]), waits_scripted=str(total["waits_scripted"]),
                     waits_matinee=str(total["waits_matinee"]),
                     wait_names=str(sum(int(r["wait_names"]) for r in maps)), fires=str(total["fires"]),
                     fires_undeclared=str(total["fires_undeclared"]),
                     fire_actors=str(total["fire_actors"]), fire_names=str(sum(int(r["fire_names"]) for r in maps)),
                     fires_landing=str(total["fires_landing"]), fires_unwaited=str(total["fires_unwaited"]),
                     matched=str(total["matched"]),
                     matched_names=str(sum(int(r["matched_names"]) for r in maps)),
                     orphan=str(total["orphan"]), orphan_tag=str(total["orphan_tag"]),
                     orphan_name=str(total["orphan_name"]), orphan_none=str(total["orphan_none"]),
                     orphan_cross=str(cross),
                     note="maps=%d;unreadable=%d;wait_classes=%d;fire_classes=%d;fire_props=%d;"
                          "orphan_maps=%d"
                          % (len(maps), len([r for r in rows if r["kind"] == "map" and r["exports"] == ""]),
                             len([r for r in rows if r["kind"] == "waitclass"]),
                             len([r for r in rows if r["kind"] == "fireclass"]),
                             len([r for r in rows if r["kind"] == "fireprop"]),
                             sum(1 for r in maps if int(r["orphan"]) > 0))))
    return rows


def note_values(row):
    out = {}
    for part in row.get("note", "").split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            out[key.strip()] = int(value)
    return out


def load_declared(path=PROPERTIES):
    """class -> {property: friendly type}, from the committed class census."""
    declared = {}
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                declared.setdefault(row["class"], {})[row["property"]] = row.get("kind", "")
    except OSError:
        return {}
    return declared


def declared_type(declared, supers, cls, prop):
    """A property's declared type on `cls` or the nearest ancestor that declares it."""
    cur, hops = cls, 0
    while cur and hops < 40:
        want = declared.get(cur, {}).get(prop)
        if want:
            return want
        cur = supers.get(cur, "")
        hops += 1
    return ""


PLACEMENTS = os.path.join(ROOT, "inventory/map_export_classes.csv")


def placement_control(rows, path=PLACEMENTS, wait_class="ACTION_WaitForEvent"):
    """Scripted wait sites against `inventory/map_export_classes.csv`'s placement count for the same class.

    That file is 5.2's export-class walk, a different reader over the same maps: an actor cannot wait on more
    events than it has placements, so a map with more wait sites than placements is this reader's error, and a
    placement with no `ExternalEvent` override is an action that waits on nothing.
    """
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            placed = {r["map"]: int(r["count"]) for r in csv.DictReader(fh) if r["class"] == wait_class}
    except OSError:
        return None
    mine = {r["map"]: int(r["waits_scripted"]) for r in rows if r["kind"] == "map" and r["exports"] != ""}
    over = [(m, mine[m], placed.get(m, 0)) for m in sorted(mine) if mine[m] > placed.get(m, 0)]
    return (sum(mine.values()), sum(placed.values()), len(mine) - len(over), over)


def render(rows):
    c = [r for r in rows if r["kind"] == "census"][0]
    maps = [r for r in rows if r["kind"] == "map" and r["exports"] != ""]
    wait_rows = [r for r in rows if r["kind"] == "waitclass"]
    fire_rows = [r for r in rows if r["kind"] == "fireclass"]
    prop_rows = [r for r in rows if r["kind"] == "fireprop"]
    vals = note_values(c)
    waits, fires, matched = int(c["waits"]), int(c["fires"]), int(c["matched"])
    orphan, orphan_tag = int(c["orphan"]), int(c["orphan_tag"])
    out = []
    out.append("**%d of the %d events a map's sequences wait on (%s) are fired by a placed actor in the same map; "
               "%d are not.** The map's event space is a name space with two ends: %d wait sites and %d fire "
               "sites over %d classes, and the join between them needs no index, no name table lookup and no "
               "other package."
               % (matched, waits, "%.1f%%" % (100.0 * matched / waits if waits else 0.0), orphan, waits, fires,
                  vals["fire_classes"]))
    out.append("")
    out.append("| measure | answer |")
    out.append("|---|---|")
    out.append("| maps read | **%d** |" % vals["maps"])
    out.append("| exports over those maps | **%s** |" % c["exports"])
    out.append("| exports with a name-valued property block | **%s** |" % c["blocks"])
    out.append("| wait sites (`ExternalEvent`) | **%d** over %d classes |" % (waits, vals["wait_classes"]))
    out.append("| wait sites in the scripted family | **%s** |" % c["waits_scripted"])
    out.append("| wait sites in the scene (`MatAction`) family | **%s** |" % c["waits_matinee"])
    out.append("| distinct names waited on | **%s** |" % c["wait_names"])
    out.append("| fire sites | **%d** over %d classes and %d properties |"
               % (fires, vals["fire_classes"], vals["fire_props"]))
    out.append("| actors carrying a fire property | **%s** |" % c["fire_actors"])
    out.append("| distinct names fired | **%s** |" % c["fire_names"])
    out.append("| fire sites whose name is a placed actor's `Tag` | **%s** |" % c["fires_landing"])
    out.append("| fire sites no waiter in the map waits on | **%s** |" % c["fires_unwaited"])
    out.append("| wait sites a fire in the same map satisfies | **%d** |" % matched)
    out.append("| distinct names that connect the two ends | **%s** |" % c["matched_names"])
    out.append("| wait sites nothing in the map fires | **%d** in %d maps |"
               % (orphan, vals["orphan_maps"]))
    out.append("| -- of those, the name is a placed actor's `Tag` | **%s** |" % c["orphan_tag"])
    out.append("| -- the name is a placed actor's object name | **%s** |" % c["orphan_name"])
    out.append("| -- the name is neither | **%s** |" % c["orphan_none"])
    out.append("| -- the name is fired in some *other* map | **%s** |" % c["orphan_cross"])
    out.append("")
    out.append("**A fire site's property is a declared `UNameProperty` on its class chain** in "
               "`inventory/class_properties.csv`, a different reader of a different artifact: **%s** name-typed "
               "properties whose spelling says event and that census does not declare were refused rather than "
               "counted, because the property reader landing on an object name is a mis-parse and not a fire. "
               "The wait side needs no such guard -- `ExternalEvent` is the only property name it accepts."
               % c["fires_undeclared"])
    out.append("")
    out.append("Names compare case-insensitively in UnrealScript, so the join folds case; a name that differs "
               "only in case is a match, not a miss. Every count here is a class name or a number -- the names "
               "themselves are game content and none is written to the artifact.")
    out.append("")
    control = placement_control(rows)
    if control:
        sites, placed, clean, over = control
        out.append("**The wait side is a subset of the placements.** `inventory/map_export_classes.csv` counts "
                   "%d placed `ACTION_WaitForEvent` actors over these maps, so the %d scripted wait sites leave "
                   "%d placements that state no `ExternalEvent`; no map has more wait "
                   "sites than placements (%d of %d maps agree, %s). Those placements do not wait on "
                   "nothing: the class looks its condition up with the default name, `None`, which the actor "
                   "iterator does not filter on, so the first `TriggeredCondition` in the level decides "
                   "whether the wait completes at once -- `docs/MAP_ACTION_PARAMETERS.md` names them by map, "
                   "class and slot and counts the placements that state `bCheckRecentEvents` beside them."
                   % (placed, sites, placed - sites, clean, clean + len(over),
                      "no exception" if not over else "exceptions: %s"
                      % ", ".join("`%s` (%d vs %d)" % o for o in over[:6])))
        out.append("")
    out.append("**A wait site is not a wait for an event the level fires from its data alone.** %d of the %d "
               "waits name something no placed actor fires, and %s of those name a placed actor's `Tag` -- a "
               "name a `TriggerEvent` could address, but one nothing in the map addresses. Of the %s that name "
               "nothing in their own map, %s are fired by a *different* map -- a handful of names are conventions "
               "shared across levels rather than edges inside one -- and the rest "
               "are fired by nothing in any package, which is where native code and runtime-spawned actors take "
               "over from placed data."
               % (orphan, waits, c["orphan_tag"], c["orphan_none"], c["orphan_cross"]))
    out.append("")
    out.append("### By waiter class")
    out.append("")
    out.append("| class | wait sites |")
    out.append("|---|---:|")
    for r in wait_rows[:12]:
        out.append("| `%s` | %s |" % (r["map"], r["waits"]))
    out.append("")
    out.append("### By fire class")
    out.append("")
    out.append("| class | fire sites |")
    out.append("|---|---:|")
    for r in fire_rows[:15]:
        out.append("| `%s` | %s |" % (r["map"], r["fires"]))
    out.append("")
    out.append("### By fire property")
    out.append("")
    out.append("| property | fire sites |")
    out.append("|---|---:|")
    for r in prop_rows[:15]:
        out.append("| `%s` | %s |" % (r["map"], r["fires"]))
    out.append("")
    out.append("### The maps with the most unsatisfied waits")
    out.append("")
    out.append("| map | waits | fires | matched | nothing fires it |")
    out.append("|---|---:|---:|---:|---:|")
    for r in sorted(maps, key=lambda r: (-int(r["orphan"]), r["map"]))[:10]:
        out.append("| `%s` | %s | %s | %s | %s |" % (r["map"], r["waits"], r["fires"], r["matched"],
                                                     r["orphan"]))
    out.append("")
    out.append("Census: maps=%d;waits=%s;fires=%s;matched=%s;orphan=%s;orphan_tag=%s;orphan_none=%s;"
               "orphan_cross=%s."
               % (vals["maps"], c["waits"], c["fires"], c["matched"], c["orphan"], c["orphan_tag"],
                  c["orphan_none"], c["orphan_cross"]))
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


def _strip_private(rows):
    """The cross-map pass stashes name sets on the row dicts; the artifact must not carry them."""
    out = []
    for r in rows:
        out.append({k: v for k, v in r.items() if not k.startswith("_")})
    return out


def self_check():
    bad = []
    matinee = {"MatActionWaitForEvent"}
    entries = [{"class": "Level", "name": "myLevel", "outer": 0},
               {"class": "ACTION_WaitForEvent", "name": "Wait0", "outer": 0},
               {"class": "ACTION_WaitForEvent", "name": "Wait1", "outer": 0},
               {"class": "ACTION_TriggerEvent", "name": "Trigger0", "outer": 0},
               {"class": "Trigger", "name": "Touch0", "outer": 0},
               {"class": "MatActionWaitForEvent", "name": "Wait2", "outer": 0}]
    blocks = {2: {"ExternalEvent": "EV_A", "Tag": "SeqA"},
              3: {"ExternalEvent": "seqa"},                       # case fold: matches Trigger0's EV_A? no, EV_A
              4: {"Event": "EV_A"},
              5: {"Event": "EV_B", "Tag": "EV_C"},
              6: {"ExternalEvent": "EV_C"}}
    row, waits, fires = measure(entries, blocks, matinee)
    if (row["waits"], row["waits_matinee"], row["fires"], row["matched"], row["orphan"]) != (3, 1, 2, 1, 2):
        bad.append("synthetic map: %r" % (row,))
    if row["fire_actors"] != 2 or row["fires_landing"] != 0 or row["fires_unwaited"] != 1:
        bad.append("fire arms: %r" % (row,))
    # the tag arm: neither `seqa` nor `EV_C` is a *fired* name, but both are tags, so both are `orphan_tag`
    if (row["orphan_tag"], row["orphan_name"], row["orphan_none"]) != (2, 0, 0):
        bad.append("orphan split: %r" % (row,))
    # case folding is the join: `seqa` vs `SeqA` in the tag space is not a fire, so nothing rescues it
    blocks_case = {2: {"ExternalEvent": "EV_A"}, 3: {"Event": "ev_a"}}
    row_c, _w, _f = measure(entries[:3], blocks_case, matinee)
    if row_c["matched"] != 1 or row_c["orphan"] != 0:
        bad.append("case fold: %r" % (row_c,))
    # an object name rescues neither a tag nor a fire, it is its own column
    row_n, _w, _f = measure(entries, {2: {"ExternalEvent": "Trigger0"}}, matinee)
    if (row_n["orphan_name"], row_n["orphan_tag"], row_n["orphan_none"]) != (1, 0, 0):
        bad.append("object-name arm: %r" % (row_n,))
    # a block that carries no name value is not an actor for either end
    row_e, _w, _f = measure(entries, {2: {"bHidden": True}}, matinee)
    if row_e["waits"] or row_e["fires"]:
        bad.append("non-name values counted: %r" % (row_e,))
    # `OwnerEvent` is a fire (it is what the matinee wait hands back), `ExternalEvent` never is
    if is_fire("ExternalEvent") or is_wait("Event") or not is_fire("OwnerEvent"):
        bad.append("property classification")
    # a `Name` property that does not say event is neither end: `StaticEvent` yes, `Tag` no
    if is_fire("Tag") or not is_fire("StaticEvent") or not is_fire("DialogueFinishedEvent"):
        bad.append("event spelling")
    # census totals come from their own row, and the render reads them from there
    rows = [dict(kind="map", map="A", exports="6", blocks="5", waits="3", waits_scripted="2",
                 waits_matinee="1", wait_names="3", fires="2", fire_actors="2", fire_names="2",
                 fires_landing="1", fires_unwaited="1", matched="1", matched_names="1", orphan="2",
                 orphan_tag="0", orphan_name="0", orphan_none="2", orphan_cross="0", note=""),
            dict(kind="map", map="B", exports="2", blocks="0", waits="1", waits_scripted="1",
                 waits_matinee="0", wait_names="1", fires="0", fire_actors="0", fire_names="0",
                 fires_landing="0", fires_unwaited="0", matched="0", matched_names="0", orphan="1",
                 orphan_tag="1", orphan_name="0", orphan_none="0", orphan_cross="0", note=""),
            dict(kind="waitclass", map="ACTION_WaitForEvent", exports="", blocks="", waits="4",
                 waits_scripted="", waits_matinee="", wait_names="", fires="", fire_actors="", fire_names="",
                 fires_landing="", fires_unwaited="", matched="", matched_names="", orphan="", orphan_tag="",
                 orphan_name="", orphan_none="", orphan_cross="", note=""),
            dict(kind="fireclass", map="Trigger", exports="", blocks="", waits="", waits_scripted="",
                 waits_matinee="", wait_names="", fires="2", fire_actors="", fire_names="",
                 fires_landing="", fires_unwaited="", matched="", matched_names="", orphan="", orphan_tag="",
                 orphan_name="", orphan_none="", orphan_cross="", note=""),
            dict(kind="fireprop", map="Event", exports="", blocks="", waits="", waits_scripted="",
                 waits_matinee="", wait_names="", fires="2", fire_actors="", fire_names="",
                 fires_landing="", fires_unwaited="", matched="", matched_names="", orphan="", orphan_tag="",
                 orphan_name="", orphan_none="", orphan_cross="", note="")]
    census(rows, cross=0)
    if (rows[-1]["waits"], rows[-1]["fires"], rows[-1]["matched"], rows[-1]["orphan"],
            rows[-1]["orphan_tag"]) != ("4", "2", "1", "3", "1"):
        bad.append("census totals: %r" % (rows[-1],))
    if note_values(rows[-1]) != {"maps": 2, "unreadable": 0, "wait_classes": 1, "fire_classes": 1,
                                 "fire_props": 1, "orphan_maps": 2}:
        bad.append("census note: %r" % (rows[-1]["note"],))
    block = render(rows)
    for want in ("1 of the 4 events", "nothing in the map fires", "ACTION_WaitForEvent", "By fire property"):
        if want not in block:
            bad.append("render is missing %r" % want)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "e.csv")
        write_artifact(rows, path)
        if read_artifact(path) != [{f: str(r.get(f, "")) for f in FIELDS} for r in rows]:
            bad.append("artifact round trip differs")
        if splice(path, "x") is not None:
            bad.append("splice found markers in a CSV")
    if bad:
        print("FAIL event-wiring: " + "; ".join(bad))
        return 1
    print("ok   event-wiring: a wait site joins the fire site that names it, a fire that lands on no tag is told "
          "apart, case folds, a non-event name property is neither end, and the census totals come from their "
          "own row")
    return 0


def main():
    if "--check" in sys.argv[1:]:
        return self_check()
    ap = argparse.ArgumentParser()
    ap.add_argument("maps", nargs="?", help="the dump's Maps directory")
    ap.add_argument("--sweep", action="store_true", help="every .unr -> inventory/event_wiring.csv")
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
            path = os.path.join(args.out, "event_wiring.csv")
            write_artifact(_strip_private(rows), path)
            print("%s: %d rows" % (path, len(rows)))
    block = render(rows)
    print(block)
    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        new = splice(path, block)
        if new is None:
            print("FAIL event-wiring: markers not found in %s" % path)
            return 1
        old = open(path, encoding="utf-8").read()
        if args.check_doc:
            if new != old:
                print("FAIL event-wiring: %s is stale against the committed artifact" % path)
                return 1
            print("ok   event-wiring: %s matches the committed artifact" % path)
            return 0
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
