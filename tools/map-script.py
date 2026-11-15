#!/usr/bin/env python3
"""Which maps carry compiled script, and what carries a level's logic instead (PLAN 5.2).

PLAN 5.2 ends with an open item it states plainly: "the `Level` export's script sequence is separate from
the actors' properties and **unread**". Three pages then depend on that read. `docs/systems/OBJECTIVES.md`
says "mission logic is level script"; `docs/systems/AUDIO.md`'s cue-usage question is deferred to "the maps'
level scripts (5.2's T3D dump)"; `docs/MAP_EXPORTS.md` lists "anything the level script does" as unmeasured.

So the question is asked of the packages rather than of a reader that would need a script format first: a
compiled level script is *exports*, whatever its text looks like. A UE2 package that carries script carries
`Function` / `State` / `ScriptStruct` exports, and a class that ships source carries a `TextBuffer`. The `Level`
export is the level's own object, so a level script's members hang off it as child exports. Counting those three
things over all 73 maps answers whether there is anything to read at all, without decoding a byte of it.

When the answer is no -- and on this build it is -- the census has to say what carries the logic instead, or
"no level script" reads like "no mission logic". The carriers are the placed `ACTION_*` latent scripted actions
and the scripted-sequence and trigger actors that reference them, so the same walk counts those per map, and the
class histogram it produces is checked against `inventory/map_export_classes.csv` -- an artifact measured by a
different reader -- as its control.

    python3 tools/map-script.py DUMP/Maps --sweep -o inventory
    python3 tools/map-script.py --report
    python3 tools/map-script.py --write-doc docs/MAP_SCRIPTS.md
    python3 tools/map-script.py --check-doc docs/MAP_SCRIPTS.md   # from the committed artifact
    python3 tools/map-script.py --check                           # synthetic, no dump, writes nothing

Reads the maps; no script text, no value, no boot.
"""
import argparse
import collections
import csv
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from ue2pkg import Package  # noqa: E402

ARTIFACT = os.path.join(ROOT, "inventory/map_script.csv")
DOC = os.path.join(ROOT, "docs/MAP_SCRIPTS.md")
HISTOGRAM = os.path.join(ROOT, "inventory/map_export_classes.csv")
DOC_START = "<!-- generated:map-script -->"
DOC_END = "<!-- /generated:map-script -->"

FIELDS = ["kind", "map", "exports", "script_exports", "textbuffers", "level_size", "level_children",
          "actions", "sequences", "triggers", "action_classes", "note"]

# A compiled script is these export classes. `TextBuffer` is the source text a class ships, so a map carrying
# one would carry script source the same way; both are counted rather than only the executable half.
SCRIPT_CLASSES = ("Function", "State", "ScriptStruct", "Const", "Enum")
TEXT_CLASSES = ("TextBuffer",)


def family(class_name):
    """`actions` / `sequences` / `triggers` / None, by the name the actor was placed under.

    The three families are mutually exclusive by precedence: `ACTION_TriggerEvent` is an action, not a trigger,
    because the `ACTION_` prefix is what says it is one of the latent scripted actions a sequence runs.
    """
    if class_name.startswith("ACTION_"):
        return "actions"
    if "Scripted" in class_name or class_name.endswith("Sequence"):
        return "sequences"
    if "Trigger" in class_name:
        return "triggers"
    return None


def measure(entries):
    """Reduce one map's (class name, object name, serial size, outer) tuples to its row.

    `entries` is what a package walk yields, kept as plain tuples so the arithmetic is testable from a
    synthetic map: a list built here can carry a `Function` export, which no shipped map does.
    """
    row = dict(kind="map", map="", exports=len(entries), script_exports=0, textbuffers=0,
               level_size=0, level_children=0, actions=0, sequences=0, triggers=0, action_classes=0,
               note="")
    level_index = None
    action_names = set()
    for i, (cls, _name, size, _outer) in enumerate(entries):
        if cls in SCRIPT_CLASSES:
            row["script_exports"] += 1
        elif cls in TEXT_CLASSES:
            row["textbuffers"] += 1
        if cls == "Level" and level_index is None:
            level_index = i + 1                  # exports are numbered from 1
            row["level_size"] = size
        fam = family(cls)
        if fam:
            row[fam] += 1
            if fam == "actions":
                action_names.add(cls)
    row["action_classes"] = len(action_names)
    if level_index is not None:
        row["level_children"] = sum(1 for _c, _n, _s, outer in entries if outer == level_index)
    return row


def sweep(maps_dir, out):
    rows, carriers = [], collections.Counter()
    for name in sorted(os.listdir(maps_dir)):
        if not name.lower().endswith(".unr"):
            continue
        try:
            pkg = Package(os.path.join(maps_dir, name))
            pkg.read_names()
            pkg.read_imports()
            pkg.read_exports()
        except Exception as exc:                  # an unreadable map is not a zero
            rows.append(dict(kind="map", map=name[:-4], exports="", script_exports="", textbuffers="",
                             level_size="", level_children="", actions="", sequences="", triggers="",
                             action_classes="", note="unreadable: %s" % exc))
            continue
        entries = [(pkg.class_name_of(e), pkg.name(e["name"]), e["size"], e["outer"])
                   for e in pkg.exports]
        row = measure(entries)
        row["map"] = name[:-4]
        rows.append(row)
        for cls, _n, _s, _o in entries:
            fam = family(cls)
            if fam == "actions":
                carriers[cls] += 1
    carrier_rows = [dict(kind="carrier", map=cls, exports="", script_exports="", textbuffers="",
                         level_size="", level_children="", actions=str(n), sequences="", triggers="",
                         action_classes="", note="")
                    for cls, n in carriers.most_common()]
    rows += carrier_rows
    census(rows)
    if out:
        path = os.path.join(out, "map_script.csv")
        write_artifact(rows, path)
        print("%s: %d rows" % (path, len(rows)))
    return rows


def census(rows):
    """Corpus totals in their own row, so every rendering of them comes from the artifact."""
    maps = [r for r in rows if r["kind"] == "map" and r["exports"] != ""]
    total = dict(exports=0, script_exports=0, textbuffers=0, level_size=0, level_children=0,
                 actions=0, sequences=0, triggers=0, action_classes=0)
    for r in maps:
        for key in total:
            total[key] += int(r[key])
    with_script = sum(1 for r in maps if int(r["script_exports"]) > 0 or int(r["textbuffers"]) > 0)
    with_level = sum(1 for r in maps if int(r["level_size"]) > 0)
    with_children = sum(1 for r in maps if int(r["level_children"]) > 0)
    rows.append(dict(kind="census", map="corpus", exports=str(total["exports"]),
                     script_exports=str(total["script_exports"]), textbuffers=str(total["textbuffers"]),
                     level_size=str(total["level_size"]), level_children=str(total["level_children"]),
                     actions=str(total["actions"]), sequences=str(total["sequences"]),
                     triggers=str(total["triggers"]), action_classes=str(total["action_classes"]),
                     note="maps=%d;script=%d;level=%d;children=%d"
                          % (len(maps), with_script, with_level, with_children)))
    return rows


def note_values(row):
    """The census row's counts, as a dict: the note is `maps=73;script=0;level=73;children=0`."""
    out = {}
    for part in row.get("note", "").split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            out[key.strip()] = int(value)
    return out


def histogram_control(rows):
    """Per-map `ACTION_*` totals, this walk against `inventory/map_export_classes.csv`.

    The histogram is a different reader over the same exports (5.2's class-level pass), so agreement is the
    control that this walk's class names are the actors' classes and not, say, the names of their templates.
    A missing histogram is not a disagreement; it leaves the control unrun.
    """
    try:
        with open(HISTOGRAM, newline="", encoding="utf-8") as fh:
            hist = collections.Counter()
            for r in csv.DictReader(fh):
                if r["class"].startswith("ACTION_"):
                    hist[r["map"]] += int(r["count"])
    except OSError:
        return None
    mine = {r["map"]: int(r["actions"]) for r in rows
            if r["kind"] == "map" and r["exports"] != "" and r["actions"] != ""}
    agree = [m for m in mine if hist.get(m, 0) == mine[m]]
    disagree = [(m, mine[m], hist.get(m, 0)) for m in sorted(mine) if hist.get(m, 0) != mine[m]]
    return len(mine), len(hist), len(agree), disagree


def render(rows):
    census_row = [r for r in rows if r["kind"] == "census"][0]
    maps = [r for r in rows if r["kind"] == "map" and r["exports"] != "" and r["actions"] != ""]
    carriers = [r for r in rows if r["kind"] == "carrier"]
    script = int(census_row["script_exports"])
    text = int(census_row["textbuffers"])
    vals = note_values(census_row)
    nmaps, with_level, with_children = vals["maps"], vals["level"], vals["children"]
    distinct = len(carriers)
    out = []
    if script == 0 and text == 0:
        out.append("**No map in the build carries compiled script.** Across %d maps -- %s exports in all -- "
                   "**none** is a `Function`, `State`, `ScriptStruct`, `Const` or `Enum`, and not one carries a "
                   "`TextBuffer`: there is no level script to read in any of them, by export class or by source "
                   "text. %d of them carry a `Level` export and %d of those give it a child export, so the absence "
                   "is the build's and not a truncated table."
                   % (nmaps, census_row["exports"], with_level, with_children))
    else:
        out.append("**%d of the %d maps carry compiled script** (%d `Function`/`State`/`ScriptStruct` exports and "
                   "%d `TextBuffer`s over the corpus), so a level script exists and its text is the next read."
                   % (vals["script"], nmaps, script, text))
    out.append("")
    out.append("| measure | answer |")
    out.append("|---|---|")
    out.append("| maps read | **%d** |" % nmaps)
    out.append("| exports over those maps | **%s** |" % census_row["exports"])
    out.append("| `Function`/`State`/`ScriptStruct`/`Const`/`Enum` exports | **%s** |" % census_row["script_exports"])
    out.append("| `TextBuffer` exports | **%s** |" % census_row["textbuffers"])
    out.append("| maps with a `Level` export | **%d** |" % with_level)
    out.append("| maps whose `Level` export has a child export | **%d** |" % with_children)
    out.append("| placed `ACTION_*` actors | **%s** over %d distinct classes |" % (census_row["actions"], distinct))
    out.append("| placed scripted-sequence actors | **%s** |" % census_row["sequences"])
    out.append("| placed trigger actors (non-`ACTION_`) | **%s** |" % census_row["triggers"])
    out.append("")
    maps_zero = [r["map"] for r in maps if int(r["actions"]) == 0]
    if maps_zero:
        out.append("**%d of the %d maps place no `ACTION_*` actor at all** (`%s`), so scripted actions are not "
                   "what every level's logic is made of; those are the maps to read first for whatever they use "
                   "instead."
                   % (len(maps_zero), len(maps), "`, `".join(maps_zero)))
    else:
        out.append("Every one of the %d maps places at least one `ACTION_*` actor." % len(maps))
    out.append("")
    control = histogram_control(rows)
    if control is None:
        out.append("The histogram control did not run: `inventory/map_export_classes.csv` is not in the tree.")
    else:
        n_mine, n_hist, n_agree, disagree = control
        line = ("**Against `inventory/map_export_classes.csv` -- a second reader over the same exports -- this "
                "walk agrees on %d of %d maps' `ACTION_*` totals**" % (n_agree, n_mine))
        if disagree:
            line += (", and disagrees on %d: %s" % (len(disagree),
                                                    ", ".join("`%s` %d vs %d" % d for d in disagree[:8])))
        else:
            line += " (the histogram holds %d maps, so the two populations agree wherever both have a row)" % n_hist
        out.append(line + ".")
    out.append("")
    out.append("The carriers, by class, over the whole corpus:")
    out.append("")
    out.append("| `ACTION_*` class | placements |")
    out.append("|---|---:|")
    for r in carriers[:15]:
        out.append("| `%s` | %s |" % (r["map"], r["actions"]))
    out.append("")
    out.append("Per map: `script` counts the export classes a compiled script would be made of, `tb` the "
               "`TextBuffer`s, `kd` the `Level` export's children, and the last three the carrier families.")
    out.append("")
    out.append("| map | exports | script | tb | kd | actions | sequences | triggers |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in maps:
        out.append("| `%s` | %s | %s | %s | %s | %s | %s | %s |"
                   % (r["map"], r["exports"], r["script_exports"], r["textbuffers"], r["level_children"],
                      r["actions"], r["sequences"], r["triggers"]))
    out.append("")
    out.append("Census: %s." % census_row["note"])
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
    # 1. a map with script is told from one without; the Level's children are the Level's
    with_script = [("Level", "myLevel", 400, 0), ("Function", "BeginPlay", 90, 1),
                   ("State", "Running", 40, 1), ("TextBuffer", "myLevel", 200, 1),
                   ("StaticMeshActor", "Actor0", 30, 0)]
    row = measure(with_script)
    if (row["script_exports"], row["textbuffers"], row["level_children"], row["level_size"]) != (2, 1, 3, 400):
        bad.append("scripted map: %r" % (row,))
    plain = [("Level", "myLevel", 400, 0), ("ACTION_Talk", "Action0", 30, 0),
             ("ACTION_Talk", "Action1", 30, 0), ("ACTION_WaitForTimer", "Action2", 30, 0),
             ("TeamScriptedSequence", "Seq0", 30, 0), ("Trigger", "Trig0", 30, 0)]
    row = measure(plain)
    if (row["script_exports"], row["textbuffers"], row["actions"], row["action_classes"],
            row["sequences"], row["triggers"]) != (0, 0, 3, 2, 1, 1):
        bad.append("plain map: %r" % (row,))
    # 2. the families are exclusive by precedence, which is the one judgement in the classifier
    for cls, want in (("ACTION_TriggerEvent", "actions"), ("ScriptedTrigger", "sequences"),
                      ("SGUseTrigger", "triggers"), ("Level", None), ("ACTION_SpawnScriptedActor", "actions")):
        if family(cls) != want:
            bad.append("family(%s) = %r, wanted %r" % (cls, family(cls), want))
    # 3. the census adds up and the render reads its totals from the census row, not from the map rows
    rows = [dict(kind="map", map="A", exports="5", script_exports="0", textbuffers="0", level_size="9",
                 level_children="0", actions="2", sequences="1", triggers="0", action_classes="1", note=""),
            dict(kind="map", map="B", exports="7", script_exports="0", textbuffers="0", level_size="9",
                 level_children="0", actions="1", sequences="0", triggers="3", action_classes="1", note=""),
            dict(kind="carrier", map="ACTION_Talk", exports="", script_exports="", textbuffers="",
                 level_size="", level_children="", actions="2", sequences="", triggers="",
                 action_classes="", note="")]
    census(rows)
    if rows[-1]["actions"] != "3" or rows[-1]["exports"] != "12":
        bad.append("census totals: %r" % (rows[-1],))
    if note_values(rows[-1]) != {"maps": 2, "script": 0, "level": 2, "children": 0}:
        bad.append("census note: %r" % (rows[-1]["note"],))
    block = render(rows)
    for want in ("no map in the build carries compiled script", "**12**", "**3**", "ACTION_Talk"):
        if want not in block.lower() and want not in block:
            bad.append("render is missing %r" % want)
    # 4. the artifact round-trips and a CSV is not a doc
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.csv")
        write_artifact(rows, path)
        if read_artifact(path) != rows:
            bad.append("artifact round trip differs")
        if splice(path, "x") is not None:
            bad.append("splice found markers in a CSV")
    if bad:
        print("FAIL map-script: " + "; ".join(bad))
        return 1
    print("ok   map-script: a scripted map is told from a plain one by its export classes, the three carrier "
          "families are exclusive, and the census totals come from their own row")
    return 0


def main():
    if "--check" in sys.argv[1:]:
        return self_check()
    ap = argparse.ArgumentParser()
    ap.add_argument("maps", nargs="?", help="the dump's Maps directory")
    ap.add_argument("--sweep", action="store_true", help="every .unr -> inventory/map_script.csv")
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
            write_artifact(rows, os.path.join(args.out, "map_script.csv"))
            print("%s: %d rows" % (os.path.join(args.out, "map_script.csv"), len(rows)))
    block = render(rows)
    print(block)
    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        new = splice(path, block)
        if new is None:
            print("FAIL map-script: markers not found in %s" % path)
            return 1
        old = open(path, encoding="utf-8").read()
        if args.check_doc:
            if new != old:
                print("FAIL map-script: %s is stale against the committed artifact" % path)
                return 1
            print("ok   map-script: %s matches the committed artifact" % path)
            return 0
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
