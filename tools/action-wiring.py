#!/usr/bin/env python3
"""Which sequence runs which action: the `Actions` arrays, resolved (PLAN 5.2).

`docs/MAP_SCRIPTS.md` settled that no map carries compiled script and counted what carries a level's logic
instead -- 18,283 placed `ACTION_*` actors. It left two things open in its own words: *which sequence runs
which action* (the `Actions` array that orders a sequence is a property value) and *whether a placed action is
reachable* ("an `ACTION_*` that no sequence references never runs, and nothing here distinguishes the two").

Both are one read. A sequence actor's `Actions` property is an `Array` whose payload is a count followed by
that many **compact object references** -- 1-based export indices into the same map package. So the ordered
list a sequence runs is a name-free index list, and joining it back to the export table names every action a
sequence calls, every action no sequence calls, and every action two sequences share.

    python3 tools/action-wiring.py DUMP/Maps --sweep -o inventory
    python3 tools/action-wiring.py --report
    python3 tools/action-wiring.py --write-doc docs/MAP_ACTION_WIRING.md
    python3 tools/action-wiring.py --check-doc docs/MAP_ACTION_WIRING.md
    python3 tools/action-wiring.py --check          # synthetic, no dump, writes nothing

The action family is read from `inventory/classes.csv`'s `super` column, not from the `ACTION_` spelling: the
build places `Action_PLAYANIM`, a class name no prefix census sees. Reads the maps' property blocks; the only
things a row carries are class names and counts, and nothing game-derived is written anywhere.
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

ARTIFACT = os.path.join(ROOT, "inventory/action_wiring.csv")
DOC = os.path.join(ROOT, "docs/MAP_ACTION_WIRING.md")
CLASSES = os.path.join(ROOT, "inventory/classes.csv")
SCRIPTS = os.path.join(ROOT, "inventory/map_script.csv")
DOC_START = "<!-- generated:action-wiring -->"
DOC_END = "<!-- /generated:action-wiring -->"

FIELDS = ["kind", "map", "exports", "actions", "actions_prefixed", "action_classes", "holders",
          "holders_empty", "refs", "refs_none", "refs_imported", "refs_matinee", "refs_other",
          "refs_dangling", "referenced", "orphan", "shared", "note"]

# The two abstract heads of the family: a placement of one of them would be a bare `ScriptedAction`, which
# names no behaviour of its own. Kept out of the class list so the census counts the concrete actions.
ACTION_BASES = {"ScriptedAction", "LatentScriptedAction"}

# `Actions` is not one property: the scene manager carries an array of the same name whose elements are
# `MatAction`s (camera moves, pauses, matinee waits) -- a different family with a different root. Keeping them
# apart is the difference between "a sequence runs this" and "a cutscene camera does this".
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


def decode_array_refs(payload):
    """An `Array` payload of object references -> (count, [refs]) or None when it does not consume itself.

    The stream is a compact index (the count) followed by that many compact indices. Requiring the elements to
    consume the payload exactly is what makes a wrong guess visible instead of silently short.
    """
    off = 0
    try:
        count, off = ps.read_index(payload, off)
    except IndexError:
        return None
    if count < 0 or count > 8192:
        return None
    refs = []
    for _ in range(count):
        if off >= len(payload):
            return None
        try:
            ref, off = ps.read_index(payload, off)
        except IndexError:
            return None
        refs.append(ref)
    if off != len(payload):
        return None
    return count, refs


def read_blocks(pkg):
    """Per export (1-based): its property names, plus the decoded `Actions` array when it has one.

    `min_props` is 1 rather than `find_block`'s default 2: an action that overrides only `ExternalEvent` has a
    one-property block, and asking for two read 51 of M00's `ACTION_WaitForEvent` sites as having none.
    """
    blocks = {}
    for i, exp in enumerate(pkg.exports):
        data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
        if not data:
            continue
        found = ps.find_block(data, pkg, min_props=1, record_class=exp["class"])
        if not found:
            continue
        _count, start, _props = found
        props, _end, _reason = ps.read_block(data[start:], pkg, True)
        actions = None
        for p in props:
            if p["name"] == "Actions" and p["type"] == "Array":
                raw = data[start + p["value_offset"]:start + p["value_offset"] + p["size"]]
                actions = decode_array_refs(raw)
                break
        blocks[i + 1] = {"props": [p["name"] for p in props], "actions": actions}
    return blocks


def classify_ref(ref, classes):
    """One `Actions` element -> how it lands in the map's own export table."""
    if ref == 0:
        return "none", ""
    if ref < 0:
        return "imported", ""
    if ref > len(classes):
        return "dangling", ""
    return "export", classes[ref - 1]


def measure(entries, blocks, actions_cls, matinee_cls=frozenset()):
    """One map -> (row, placed, referenced, named_indexes, holder_classes).

    `entries` is `[{"class", "name", "outer"}]` and `blocks` what `read_blocks` returned; neither needs a
    package, which is what lets a synthetic map drive this in `--check`.
    """
    classes = [e["class"] for e in entries]
    row = dict(kind="map", map="", exports=len(entries), actions=0, actions_prefixed=0,
               action_classes=0, holders=0, holders_empty=0, refs=0, refs_none=0, refs_imported=0,
               refs_matinee=0, refs_other=0, refs_dangling=0, referenced=0, orphan=0, shared=0, note="")
    placed, prefixed = set(), 0
    holders = collections.Counter()
    named = collections.Counter()
    for i, cls in enumerate(classes):
        if cls in actions_cls:
            placed.add(i + 1)
            if cls.startswith("ACTION_"):
                prefixed += 1
    for idx in sorted(blocks):
        blk = blocks[idx]
        if not blk.get("actions"):
            continue
        count, refs = blk["actions"]
        holders[classes[idx - 1]] += 1
        if count == 0:
            row["holders_empty"] += 1
        for ref in refs:
            row["refs"] += 1
            kind, target = classify_ref(ref, classes)
            if kind == "none":
                row["refs_none"] += 1
            elif kind == "imported":
                row["refs_imported"] += 1
            elif kind == "dangling":
                row["refs_dangling"] += 1
            elif target in actions_cls:
                named[ref] += 1
            elif target in matinee_cls:
                row["refs_matinee"] += 1
            else:
                row["refs_other"] += 1
    referenced = set(named) & placed
    row["actions"] = len(placed)
    row["actions_prefixed"] = prefixed
    row["action_classes"] = len({classes[i - 1] for i in placed})
    row["holders"] = sum(holders.values())
    row["referenced"] = len(referenced)
    row["orphan"] = len(placed) - len(referenced)
    row["shared"] = sum(1 for idx in referenced if named[idx] > 1)
    return row, placed, referenced, named, holders


def sweep(maps_dir, out, classes_path=CLASSES):
    supers = class_supers(classes_path)
    actions_cls = family(supers, "ScriptedAction") - ACTION_BASES
    matinee_cls = family(supers, MATINEE_ROOT)
    rows = []
    per_class = collections.defaultdict(lambda: dict(placed=0, referenced=0, shared=0))
    per_holder = collections.Counter()
    for name in sorted(os.listdir(maps_dir)):
        if not name.lower().endswith(".unr"):
            continue
        label = name[:-4]
        try:
            pkg = Package(os.path.join(maps_dir, name))
            pkg.read_names()
            pkg.read_imports()
            pkg.read_exports()
        except Exception as exc:                      # an unreadable map is not a zero
            rows.append(dict(kind="map", map=label, exports="", actions="", actions_prefixed="",
                             action_classes="", holders="", holders_empty="", refs="", refs_none="",
                             refs_imported="", refs_matinee="", refs_other="", refs_dangling="",
                             referenced="", orphan="", shared="", note="unreadable: %s" % exc))
            continue
        entries = [{"class": pkg.class_name_of(e), "name": pkg.name(e["name"]), "outer": e["outer"]}
                   for e in pkg.exports]
        row, placed, referenced, named, holders = measure(entries, read_blocks(pkg), actions_cls,
                                                          matinee_cls)
        row["map"] = label
        rows.append(row)
        for idx in placed:
            per_class[entries[idx - 1]["class"]]["placed"] += 1
        for idx in referenced:
            info = per_class[entries[idx - 1]["class"]]
            info["referenced"] += 1
            if named[idx] > 1:
                info["shared"] += 1
        per_holder.update(holders)
    for cls in sorted(per_class, key=lambda c: (-per_class[c]["placed"], c)):
        info = per_class[cls]
        rows.append(dict(kind="action", map=cls, exports="", actions=str(info["placed"]),
                         actions_prefixed="", action_classes="", holders=str(per_holder.get(cls, 0)),
                         holders_empty="", refs="", refs_none="", refs_imported="", refs_matinee="",
                         refs_other="",
                         refs_dangling="", referenced=str(info["referenced"]),
                         orphan=str(info["placed"] - info["referenced"]), shared=str(info["shared"]),
                         note=""))
    for cls in sorted(per_holder):
        rows.append(dict(kind="holder", map=cls, exports="", actions="", actions_prefixed="",
                         action_classes="", holders=str(per_holder[cls]), holders_empty="", refs="",
                         refs_none="", refs_imported="", refs_matinee="", refs_other="",
                         refs_dangling="", referenced="",
                         orphan="", shared="", note=""))
    census(rows)
    if out:
        path = os.path.join(out, "action_wiring.csv")
        write_artifact(rows, path)
        print("%s: %d rows" % (path, len(rows)))
    return rows


def census(rows):
    """Corpus totals in their own row, so every rendering reads one set of numbers."""
    maps = [r for r in rows if r["kind"] == "map" and r["exports"] != ""]
    total = dict(exports=0, actions=0, actions_prefixed=0, holders=0, holders_empty=0, refs=0,
                 refs_none=0, refs_imported=0, refs_matinee=0, refs_other=0, refs_dangling=0,
                 referenced=0, orphan=0, shared=0)
    for r in maps:
        for key in total:
            total[key] += int(r[key])
    rows.append(dict(kind="census", map="corpus", exports=str(total["exports"]),
                     actions=str(total["actions"]), actions_prefixed=str(total["actions_prefixed"]),
                     action_classes=str(len([r for r in rows if r["kind"] == "action"])),
                     holders=str(total["holders"]), holders_empty=str(total["holders_empty"]),
                     refs=str(total["refs"]), refs_none=str(total["refs_none"]),
                     refs_imported=str(total["refs_imported"]), refs_matinee=str(total["refs_matinee"]),
                     refs_other=str(total["refs_other"]),
                     refs_dangling=str(total["refs_dangling"]), referenced=str(total["referenced"]),
                     orphan=str(total["orphan"]), shared=str(total["shared"]),
                     note="maps=%d;wired=%d;orphan_maps=%d;holder_classes=%d"
                          % (len(maps), sum(1 for r in maps if int(r["referenced"]) > 0),
                             sum(1 for r in maps if int(r["orphan"]) > 0),
                             len([r for r in rows if r["kind"] == "holder"]))))
    return rows


def note_values(row):
    out = {}
    for part in row.get("note", "").split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            out[key.strip()] = int(value)
    return out


def script_control(rows, path=SCRIPTS):
    """`inventory/map_script.csv`'s `actions` column against this walk's `ACTION_`-prefixed count.

    That column was measured by 5.2's export-class walk, a different reader over the same table. It counts the
    `ACTION_` spelling only, so agreement is a check on this walk's placement census, and the gap between this
    walk's two counts is exactly the placements that spelling cannot see.
    """
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            other = {r["map"]: int(r["actions"]) for r in csv.DictReader(fh)
                     if r["kind"] == "map" and r["actions"]}
    except OSError:
        return None
    mine = {r["map"]: int(r["actions_prefixed"]) for r in rows
            if r["kind"] == "map" and r["exports"] != ""}
    agree = [m for m in mine if other.get(m, 0) == mine[m]]
    disagree = [(m, mine[m], other.get(m, 0)) for m in sorted(mine) if other.get(m, 0) != mine[m]]
    return len(mine), len(agree), disagree


def render(rows):
    c = [r for r in rows if r["kind"] == "census"][0]
    maps = [r for r in rows if r["kind"] == "map" and r["exports"] != ""]
    actions_rows = [r for r in rows if r["kind"] == "action"]
    holder_rows = [r for r in rows if r["kind"] == "holder"]
    vals = note_values(c)
    actions, referenced = int(c["actions"]), int(c["referenced"])
    orphan, refs = int(c["orphan"]), int(c["refs"])
    resolved = refs - int(c["refs_none"]) - int(c["refs_imported"]) - int(c["refs_matinee"]) \
        - int(c["refs_other"]) - int(c["refs_dangling"])
    out = []
    out.append("**%d of the %d actions a map places are named by a sequence's `Actions` array, and %d "
               "(%s) are named by none** -- so a placement is not a run. The arrays are the only thing in the "
               "package that orders a level's logic: %d arrays over %d classes hold %d references, %d of which "
               "resolve to a placed action."
               % (referenced, actions, orphan,
                  "%.1f%%" % (100.0 * orphan / actions if actions else 0.0),
                  int(c["holders"]), vals["holder_classes"], refs, resolved))
    out.append("")
    out.append("| measure | answer |")
    out.append("|---|---|")
    out.append("| maps read | **%d** |" % vals["maps"])
    out.append("| exports over those maps | **%s** |" % c["exports"])
    out.append("| placed actions, by class hierarchy | **%d** over %s classes |"
               % (actions, c["action_classes"]))
    out.append("| placed actions, by the `ACTION_` spelling | **%s** |" % c["actions_prefixed"])
    out.append("| `Actions` arrays | **%s** on %d classes |" % (c["holders"], vals["holder_classes"]))
    out.append("| holders with an empty array | **%s** |" % c["holders_empty"])
    out.append("| references in those arrays | **%d** |" % refs)
    out.append("| references that resolve to a placed action | **%d** |" % resolved)
    out.append("| references that name `None` -- an empty slot in an order | **%s** |" % c["refs_none"])
    out.append("| references to a `MatAction` (the scene family) | **%s** |" % c["refs_matinee"])
    out.append("| references to anything else placed | **%s** |" % c["refs_other"])
    out.append("| references to an import (never a placement) | **%s** |" % c["refs_imported"])
    out.append("| references past the export table (dangling) | **%s** |" % c["refs_dangling"])
    out.append("| actions named by more than one array | **%s** |" % c["shared"])
    out.append("| actions no array names | **%d** in %d maps |" % (orphan, vals["orphan_maps"]))
    out.append("")
    out.append("Every reference resolves inside its own map: the array holds 1-based export indices, so the "
               "wiring needs no name resolution and no cross-package join. `Actions` sits on %d different "
               "classes, so the ordered lists are not a property of one actor class -- `ScriptedSequence` and "
               "its `ScriptedTrigger`/`TeamScriptedSequence` subclasses carry most of them, and the census row "
               "names how many holders are empty (a sequence that exists and runs nothing)."
               % vals["holder_classes"])
    out.append("")
    if int(c["refs_matinee"]):
        out.append("**`Actions` is two properties, not one.** %s of the %d references leave the scripted-action "
                   "family and land on `%s` subclasses -- the scene manager's own `Actions` array, which orders a "
                   "level's cutscene camera and animation steps. A join on the property name alone would read a "
                   "camera move as a mission action. Both families are listed apart for that reason, and the "
                   "holder table names which classes carry each."
                   % (c["refs_matinee"], refs, MATINEE_ROOT))
    out.append("")
    out.append("### By action class")
    out.append("")
    out.append("| class | placed | named by an array | never named | shared |")
    out.append("|---|---:|---:|---:|---:|")
    for r in sorted(actions_rows, key=lambda r: (-int(r["actions"]), r["map"]))[:22]:
        out.append("| `%s` | %s | %s | %s | %s |" % (r["map"], r["actions"], r["referenced"],
                                                     r["orphan"], r["shared"]))
    out.append("")
    blind = [r for r in actions_rows if not r["map"].startswith("ACTION_")]
    gap = actions - int(c["actions_prefixed"])
    if gap:
        out.append("**The `ACTION_` spelling is not the family.** %d of the %d placements carry a class the "
                   "prefix cannot see (%s), so a census that counts by name reads them as nothing; the family "
                   "here is `inventory/classes.csv`'s `super` chain reaching `ScriptedAction`, which is %s "
                   "concrete classes." % (gap, actions, ", ".join("`%s`" % r["map"] for r in blind[:5]),
                                          c["action_classes"]))
        out.append("")
    control = script_control(rows)
    if control:
        nmaps, agree, disagree = control
        out.append("**Against `inventory/map_script.csv`'s `actions` column -- 5.2's other reader over the same "
                   "exports -- this walk agrees on %d of %d maps' `ACTION_`-prefixed placements**%s."
                   % (agree, nmaps, "" if not disagree else ", and the rows that differ are %s"
                      % ", ".join("`%s` (%d vs %d)" % d for d in disagree[:6])))
        out.append("")
    out.append("### The holder classes")
    out.append("")
    out.append("| class carrying `Actions` | arrays |")
    out.append("|---|---:|")
    for r in sorted(holder_rows, key=lambda r: (-int(r["holders"]), r["map"]))[:12]:
        out.append("| `%s` | %s |" % (r["map"], r["holders"]))
    out.append("")
    out.append("### The maps with the most unwired actions")
    out.append("")
    out.append("| map | actions | named | never named | arrays |")
    out.append("|---|---:|---:|---:|---:|")
    for r in sorted(maps, key=lambda r: (-int(r["orphan"]), r["map"]))[:10]:
        out.append("| `%s` | %s | %s | %s | %s |" % (r["map"], r["actions"], r["referenced"],
                                                     r["orphan"], r["holders"]))
    out.append("")
    out.append("Census: maps=%d;actions=%s;referenced=%s;orphan=%s;holder_classes=%d;arrays=%s;refs=%s."
               % (vals["maps"], c["actions"], c["referenced"], c["orphan"], vals["holder_classes"],
                  c["holders"], c["refs"]))
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
    actions_cls = {"ACTION_Talk", "ACTION_WaitForTimer", "Action_PLAYANIM"}
    entries = [{"class": "Level", "name": "myLevel", "outer": 0},
               {"class": "TeamScriptedSequence", "name": "Seq0", "outer": 0},
               {"class": "ACTION_Talk", "name": "Action0", "outer": 0},
               {"class": "ACTION_Talk", "name": "Action1", "outer": 0},
               {"class": "ACTION_WaitForTimer", "name": "Action2", "outer": 0},
               {"class": "Action_PLAYANIM", "name": "Action3", "outer": 0},
               {"class": "StaticMeshActor", "name": "Mesh0", "outer": 0}]
    blocks = {2: {"props": ["Actions", "Level"], "actions": (3, [3, 4, 0])},
              7: {"props": ["Actions"], "actions": (1, [3])}}
    entries.append({"class": "ActionMoveCamera", "name": "Cam0", "outer": 0})
    matinee_cls = {"ActionMoveCamera"}
    row, placed, referenced, named, holders = measure(entries, blocks, actions_cls, matinee_cls)
    if (row["actions"], row["actions_prefixed"], row["action_classes"], row["holders"], row["refs"],
            row["refs_none"], row["referenced"], row["orphan"], row["shared"]) != (4, 3, 3, 2, 4, 1, 2, 2, 1):
        bad.append("synthetic map: %r" % (row,))
    if named != {3: 2, 4: 1} or holders != {"TeamScriptedSequence": 1, "StaticMeshActor": 1}:
        bad.append("named/holders: %r %r" % (named, holders))
    # a reference to a placed non-action is `other`, one past the table is dangling, a negative one is an import
    row2, _p, _r, _n, _h = measure(entries, {2: {"props": ["Actions"], "actions": (3, [7, 99, -5])}},
                                   actions_cls, matinee_cls)
    if (row2["refs_other"], row2["refs_dangling"], row2["refs_imported"], row2["referenced"]) != (1, 1, 1, 0):
        bad.append("misresolving refs: %r" % (row2,))
    # an element of the scene family is counted apart from a scripted action, not as `other`
    row2b, _p, _r, _n, _h = measure(entries, {2: {"props": ["Actions"], "actions": (1, [8])}},
                                    actions_cls, matinee_cls)
    if (row2b["refs_matinee"], row2b["refs_other"], row2b["referenced"]) != (1, 0, 0):
        bad.append("matinee split: %r" % (row2b,))
    # an empty array is a sequence that runs nothing, which is not a sequence with no array
    row3, _p, _r, _n, _h = measure(entries, {2: {"props": ["Actions"], "actions": (0, [])}}, actions_cls,
                                   matinee_cls)
    if (row3["holders"], row3["holders_empty"], row3["refs"]) != (1, 1, 0):
        bad.append("empty array: %r" % (row3,))
    # a payload that does not consume itself is refused rather than read short
    if decode_array_refs(b"\x02\x01") is not None:
        bad.append("short payload accepted")
    if decode_array_refs(b"\x01\x02\x00") is not None:
        bad.append("trailing byte accepted")
    if decode_array_refs(b"\x00") != (0, []):
        bad.append("empty payload mis-decoded")
    # the family is the hierarchy's: the unmatched spelling is in it, the abstract head is not
    supers = {"ScriptedAction": "Object", "LatentScriptedAction": "ScriptedAction",
              "Action_PLAYANIM": "LatentScriptedAction", "ACTION_Talk": "LatentScriptedAction",
              "StaticMeshActor": "Actor"}
    if family(supers, "ScriptedAction") - ACTION_BASES != {"Action_PLAYANIM", "ACTION_Talk"}:
        bad.append("family: %r" % sorted(family(supers, "ScriptedAction")))
    # census totals come from their own row, and the render reads them from there
    rows = [dict(kind="map", map="A", exports="7", actions="4", actions_prefixed="3", action_classes="3",
                 holders="1", holders_empty="0", refs="3", refs_none="1", refs_imported="0", refs_matinee="0", refs_other="0",
                 refs_dangling="0", referenced="2", orphan="2", shared="0", note=""),
            dict(kind="map", map="C", exports="1", actions="0", actions_prefixed="0", action_classes="0",
                 holders="1", holders_empty="0", refs="1", refs_none="0", refs_imported="0", refs_matinee="1",
                 refs_other="0", refs_dangling="0", referenced="0", orphan="0", shared="0", note=""),
            dict(kind="map", map="B", exports="2", actions="1", actions_prefixed="1", action_classes="1",
                 holders="0", holders_empty="0", refs="0", refs_none="0", refs_imported="0", refs_matinee="0", refs_other="0",
                 refs_dangling="0", referenced="0", orphan="1", shared="0", note=""),
            dict(kind="action", map="ACTION_Talk", exports="", actions="2", actions_prefixed="",
                 action_classes="", holders="1", holders_empty="", refs="", refs_none="", refs_imported="", refs_matinee="",
                 refs_other="", refs_dangling="", referenced="2", orphan="0", shared="0", note=""),
            dict(kind="holder", map="TeamScriptedSequence", exports="", actions="", actions_prefixed="",
                 action_classes="", holders="1", holders_empty="", refs="", refs_none="", refs_imported="", refs_matinee="",
                 refs_other="", refs_dangling="", referenced="", orphan="", shared="", note="")]
    census(rows)
    if (rows[-1]["actions"], rows[-1]["referenced"], rows[-1]["orphan"], rows[-1]["exports"],
            rows[-1]["refs_matinee"]) != ("5", "2", "3", "10", "1"):
        bad.append("census totals: %r" % (rows[-1],))
    if note_values(rows[-1]) != {"maps": 3, "wired": 1, "orphan_maps": 2, "holder_classes": 1}:
        bad.append("census note: %r" % (rows[-1]["note"],))
    block = render(rows)
    for want in ("2 of the 5 actions", "never named", "ACTION_Talk", "TeamScriptedSequence",
                 "two properties"):
        if want not in block:
            bad.append("render is missing %r" % want)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.csv")
        write_artifact(rows, path)
        if read_artifact(path) != rows:
            bad.append("artifact round trip differs")
        if splice(path, "x") is not None:
            bad.append("splice found markers in a CSV")
    if bad:
        print("FAIL action-wiring: " + "; ".join(bad))
        return 1
    print("ok   action-wiring: an Actions array resolves to the placed actions it names, an unwired action is "
          "told from a wired one, an out-of-table index is dangling, the family comes from the hierarchy, and "
          "the census totals come from their own row")
    return 0


def main():
    if "--check" in sys.argv[1:]:
        return self_check()
    ap = argparse.ArgumentParser()
    ap.add_argument("maps", nargs="?", help="the dump's Maps directory")
    ap.add_argument("--sweep", action="store_true", help="every .unr -> inventory/action_wiring.csv")
    ap.add_argument("--report", action="store_true", help="print the totals for a swept artifact")
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
        rows = sweep(args.maps, None)
        if args.out:
            write_artifact(rows, os.path.join(args.out, "action_wiring.csv"))
            print("%s: %d rows" % (os.path.join(args.out, "action_wiring.csv"), len(rows)))
    block = render(rows)
    print(block)
    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        new = splice(path, block)
        if new is None:
            print("FAIL action-wiring: markers not found in %s" % path)
            return 1
        old = open(path, encoding="utf-8").read()
        if args.check_doc:
            if new != old:
                print("FAIL action-wiring: %s is stale against the committed artifact" % path)
                return 1
            print("ok   action-wiring: %s matches the committed artifact" % path)
            return 0
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
