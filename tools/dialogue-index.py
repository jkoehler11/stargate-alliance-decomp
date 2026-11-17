#!/usr/bin/env python3
"""Index the recorded dialogue by mission, scene and speaker.

`Speech/<Folder>_Final/<name>.wav` is a naming convention, and it is metadata the project can publish:
the index says how many scenes and which characters each mission was recorded for, without reproducing a
second of audio.

Six shapes are in use, not the one the first version of this tool read. Under that narrow rule 545 of the
1,364 lines carried no mission, and two mission ids that are otherwise unrepresented in the corpus -- M01
and M05, which have no map -- read as "no dialogue recorded at all", which `docs/CUT_CONTENT.md` turned
into a claim about when the voice work was cut. The interlude and briefing lines are exactly what the
narrow rule missed.

    shape           template                              example
    scene           <mission>_<scene>_<line>[_<speaker>]  m02_01_a_oneill.wav
    interlude       <mission><word>_<scene>_<line>[_<sp>] m02int_01_a_oneill.wav
    word-scene      <mission>_<word>_<line>_<speaker>     m01_int_d_thor.wav
    named-scene     <mission>_<name>_<name>               m13_jacob_follow1.wav
    pseudo-mission  <segment>_<scene>_<line>[_<speaker>]  tut_01_a_dr.wav
    named-cue       <segment>_<cue...>                    Ca_CardWontWork.wav

Two details the shapes table cannot show, both measured from the corpus:

* `segment` keeps the token the mission was written as (`m02int`, `m01_int`, `tut`), because an interlude or
  briefing numbers its scenes from 01 again -- within one mission id the same scene number occurs in more
  than one segment, so the per-mission scene count is taken over (segment, scene) pairs, never over scene
  numbers alone.
* a `named-cue` sets no speaker: `Ca_`/`On_`/`Te_`/`Hammond_` are a character shorthand (`ca` -> `carter`,
  `on` -> `oneill`, and so on), but `Ja_` fits both `jacob` and Daniel Jackson, `Tokra_` is a faction and
  `Sc1_` is a scene, so the prefix is a cue namespace rather than a speaker column. Read it as a lead.

A row with no mission id is not a failure: 139 of the lines are menu, system and one-off cues, and 88 of
the 1,364 carry no speaker at all (barks, radio traffic, crowd).

Usage: dialogue-index.py DUMP_ROOT [-o inventory]
       dialogue-index.py --check
"""
import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict

# The narrow rule the first version used, kept as a control: `--check` asserts it reads strictly less than
# the shapes below, so a future edit that silently narrows the tagger fails rather than shipping.
NARROW = re.compile(r"^(m\d+[a-z]?)_(\d+)_([a-z])(?:_([a-z0-9]+))?\.wav$", re.IGNORECASE)

# (shape, pattern, groups in order, template). First match wins, so the specific shapes precede the
# catch-all; `groups` names each capture, and `mission`/`suffix`/`cue1`/`cue2` are folded afterwards.
SHAPES = [
    ("scene", r"^(m\d+)_(\d+[a-z]?)_(\d?[a-z]\d?)(?:_([a-z0-9]+))?\.wav$",
     ("mission", "scene", "line", "speaker"), "<mission>_<scene>_<line>[_<speaker>]"),
    ("interlude", r"^(m\d+)([a-z]+)_(\d+[a-z]?)_(\d?[a-z]\d?)(?:_([a-z0-9]+))?\.wav$",
     ("mission", "suffix", "scene", "line", "speaker"), "<mission><word>_<scene>_<line>[_<speaker>]"),
    ("word-scene", r"^(m\d+)_([a-z]+)_(\d?[a-z]\d?)_([a-z0-9]+)\.wav$",
     ("mission", "scene", "line", "speaker"), "<mission>_<word>_<line>_<speaker>"),
    ("named-scene", r"^(m\d+)_([a-z]+)_([a-z]+\d*)\.wav$",
     ("mission", "cue1", "cue2"), "<mission>_<name>_<name>"),
    ("pseudo-mission", r"^(tut|tutorial|prol|coda)_(\d+[a-z]?)_(\d?[a-z]\d?)(?:_([a-z0-9]+))?\.wav$",
     ("segment", "scene", "line", "speaker"), "<segment>_<scene>_<line>[_<speaker>]"),
    ("named-cue", r"^([a-z][a-z0-9]*)_(.+)\.wav$", ("segment", "cue1"), "<segment>_<cue...>"),
]
COMPILED = [(shape, re.compile(rx, re.IGNORECASE), groups, tpl) for shape, rx, groups, tpl in SHAPES]

COLUMNS = ["folder", "file", "mission", "segment", "scene", "line", "speaker", "shape", "cue", "bytes"]


def parse(name):
    """One filename -> the fields its shape names, with everything else empty."""
    row = {"file": name, "shape": "unparsed", "mission": "", "segment": "", "scene": "", "line": "",
           "speaker": "", "cue": ""}
    for shape, rx, groups, _tpl in COMPILED:
        m = rx.match(name)
        if not m:
            continue
        row["shape"] = shape
        for key, value in zip(groups, m.groups()):
            row[key] = (value or "").lower()
        if row["mission"]:
            row["segment"] = row["mission"] + row.pop("suffix", "")
        row.pop("suffix", None)
        if row.get("cue1") is not None:
            cue1, cue2 = row.pop("cue1", ""), row.pop("cue2", "")
            row["cue"] = "_".join(x for x in (cue1, cue2) if x)
        return row
    return row


# (name, expected fields) -- every shape must appear here, and the old rule's misses must parse.
CHECK_NAMES = [
    ("m02_01_a_oneill.wav", {"shape": "scene", "mission": "m02", "segment": "m02",
                             "scene": "01", "line": "a", "speaker": "oneill"}),
    ("M21_13B_A_Daniel.wav", {"shape": "scene", "mission": "m21", "scene": "13b", "line": "a",
                              "speaker": "daniel"}),
    ("m16_17_j2_colonelganesford.wav", {"shape": "scene", "mission": "m16", "scene": "17",
                                        "line": "j2", "speaker": "colonelganesford"}),
    ("m02_01_a.wav", {"shape": "scene", "mission": "m02", "line": "a", "speaker": ""}),
    ("m02int_01_a_oneill.wav", {"shape": "interlude", "mission": "m02", "segment": "m02int",
                                "scene": "01", "speaker": "oneill"}),
    ("m02brief_01_a_oneill.wav", {"shape": "interlude", "mission": "m02", "segment": "m02brief"}),
    ("m01_int_d_thor.wav", {"shape": "word-scene", "mission": "m01", "segment": "m01",
                            "scene": "int", "line": "d", "speaker": "thor"}),
    ("m13_jacob_follow1.wav", {"shape": "named-scene", "mission": "m13",
                               "cue": "jacob_follow1", "speaker": "", "scene": ""}),
    ("tut_01_a_dr.wav", {"shape": "pseudo-mission", "mission": "", "segment": "tut", "scene": "01",
                         "line": "a", "speaker": "dr"}),
    ("tut_01_2e_dr.wav", {"shape": "pseudo-mission", "segment": "tut", "line": "2e"}),
    ("Tutorial_01_a_oneill.wav", {"shape": "pseudo-mission", "segment": "tutorial"}),
    ("prol_02_a_anubis.wav", {"shape": "pseudo-mission", "segment": "prol", "speaker": "anubis"}),
    ("coda_01_a_hammond.wav", {"shape": "pseudo-mission", "segment": "coda"}),
    ("Ca_CardWontWork.wav", {"shape": "named-cue", "segment": "ca", "cue": "cardwontwork",
                             "speaker": "", "mission": ""}),
    ("Sc1_Funny10_Ca.wav", {"shape": "named-cue", "segment": "sc1", "cue": "funny10_ca"}),
    ("tut_chatter1.wav", {"shape": "named-cue", "segment": "tut", "cue": "chatter1"}),
    ("readme.txt", {"shape": "unparsed"}),
    ("m02_01_a_oneill.wav.bak", {"shape": "unparsed"}),
]


def selftest():
    """`--check`: the rules that name a mission still name it, and the shapes cannot narrow silently."""
    problems = []
    seen = set()
    for name, want in CHECK_NAMES:
        got = parse(name)
        seen.add(got["shape"])
        for key, value in want.items():
            if got[key] != value:
                problems.append("%s: %s = %r, expected %r" % (name, key, got[key], value))
    missing = [shape for shape, _rx, _g, _t in COMPILED if shape not in seen]
    if missing:
        problems.append("shape(s) with no self-test case: %s" % ", ".join(missing))
    if parse("m02int_01_a_oneill.wav")["mission"] == "":
        problems.append("the interlude shape stopped naming a mission")
    # The control: the narrow rule must still miss the shapes below, or the table is describing history
    # rather than this corpus.
    narrow = [n for n, _w in CHECK_NAMES if NARROW.match(n)]
    wide = [n for n, w in CHECK_NAMES if w.get("shape") not in (None, "unparsed")]
    if len(narrow) >= len(wide):
        problems.append("the narrow rule reads every self-test name; the shapes claim it does not")
    if problems:
        for p in problems:
            print("FAIL dialogue-index: " + p)
        return 1
    print("ok   dialogue-index: %d shapes, %d synthetic names, narrow rule reads %d of %d"
          % (len(COMPILED), len(CHECK_NAMES), len(narrow), len(wide)))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump_root", nargs="?")
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--check", action="store_true",
                    help="run the synthetic filename self-test and exit (no dump needed)")
    args = ap.parse_args()
    if args.check:
        return selftest()
    if not args.dump_root:
        ap.error("a dump root is required unless --check is given")

    game = os.path.join(args.dump_root, "Stargate_SG-1_-_The_Alliance-2005-12-15")
    game = game if os.path.isdir(game) else args.dump_root
    root = os.path.join(game, "Speech")
    if not os.path.isdir(root):
        sys.exit(f"no Speech/ directory under {args.dump_root}")

    rows = []
    for dirpath, _dirs, files in os.walk(root):
        folder = os.path.basename(dirpath)
        for name in sorted(files):
            if not name.lower().endswith(".wav"):
                continue
            row = parse(name)
            row["folder"] = folder
            row["bytes"] = os.path.getsize(os.path.join(dirpath, name))
            rows.append(row)

    shapes = Counter(r["shape"] for r in rows)
    narrow = sum(1 for r in rows if NARROW.match(r["file"]))

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "speech_index.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    # Per mission: lines, scenes (distinct (segment, scene)), speakers, bytes.
    per_mission = defaultdict(lambda: {"lines": 0, "scenes": set(), "speakers": Counter(), "bytes": 0,
                                       "segments": set()})
    segments = defaultdict(lambda: {"lines": 0, "scenes": set(), "speakers": set(), "bytes": 0})
    for r in rows:
        if r["mission"]:
            d = per_mission[r["mission"]]
            d["lines"] += 1
            d["bytes"] += r["bytes"]
            d["segments"].add(r["segment"])
            if r["scene"]:
                d["scenes"].add((r["segment"], r["scene"]))
            d["speakers"][r["speaker"] or "?"] += 1
        else:
            s = segments[r["segment"] or "(none)"]
            s["lines"] += 1
            s["bytes"] += r["bytes"]
            if r["scene"]:
                s["scenes"].add(r["scene"])
            if r["speaker"]:
                s["speakers"].add(r["speaker"])

    missions = sorted(per_mission, key=lambda m: (len(m), m))
    with open(os.path.join(args.out, "dialogue_by_mission.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["mission", "lines", "scenes", "speakers", "bytes"])
        for m in missions:
            d = per_mission[m]
            w.writerow([m, d["lines"], len(d["scenes"]), len([k for k in d["speakers"] if k != "?"]),
                        d["bytes"]])

    # Which dialogue missions have no map of the same id in this build?
    map_ids = set()
    maps_dir = os.path.join(game, "Maps")
    if os.path.isdir(maps_dir):
        map_ids = {os.path.splitext(f)[0].lower() for f in os.listdir(maps_dir)}

    def has_map(mission):
        return any(mid.startswith(mission) for mid in map_ids)

    speakers = Counter(r["speaker"] for r in rows if r["speaker"])
    untagged = sum(1 for r in rows if not r["speaker"])
    cues = {r["segment"] for r in rows if r["shape"] == "named-cue"}

    # A scene number is only unique inside its segment: count the collisions the widening exposed.
    by_seg_scene = defaultdict(set)
    for r in rows:
        if r["mission"] and r["scene"]:
            by_seg_scene[(r["mission"], r["scene"])].add(r["segment"])
    reused = {k: v for k, v in by_seg_scene.items() if len(v) > 1}

    examples = {}
    for r in rows:
        examples.setdefault(r["shape"], r["file"])

    lines = ["# Dialogue index", "",
             "Generated by `tools/dialogue-index.py` from the `Speech/` WAV names. The names are a",
             "convention, not a schema, and this report reads all of them: it publishes counts, scene and",
             "character names, never audio.", "",
             f"- {len(rows):,} WAV files, {sum(r['bytes'] for r in rows) / 2**20:.0f} MiB",
             f"- {len([r for r in rows if r['mission']]):,} name a campaign mission id, "
             f"{len(rows) - len([r for r in rows if r['mission']]):,} do not (menu/system/one-off cues)",
             f"- the first version's rule (`<mission>_<scene>_<line>_<speaker>`) reads {narrow:,} of them;",
             f"  the six shapes below read {len([r for r in rows if r['shape'] != 'unparsed']):,}",
             f"- {len(missions)} mission ids recorded, {len(speakers)} speakers, and {untagged:,} lines carry no",
             "  speaker tag (`named-cue` and `named-scene` have none by shape)", "",
             "## Filename shapes", "",
             "| shape | lines | template | example |", "|---|---:|---|---|"]
    for shape, _rx, _groups, tpl in SHAPES:
        lines.append(f"| `{shape}` | {shapes.get(shape, 0)} | `{tpl}` | `{examples.get(shape, '')}` |")
    if shapes.get("unparsed"):
        lines.append(f"| `unparsed` | {shapes['unparsed']} | -- | `{examples.get('unparsed', '')}` |")
    lines += ["",
              "`interlude` and `word-scene` are the same idea written two ways: an interlude or briefing",
              "scene glued to the mission number (`m02int`), or its scene token spelled as a word",
              "(`m01_int_...`). `named-scene` and `named-cue` are the residual: a scene the recording",
              "session named instead of numbering (`m13_jacob_follow1`), and the free-form cues that carry",
              "no scene at all.", "",
              "The `named-cue` segments are a cue namespace, not a speaker column: "
              + ", ".join("`%s` (%d)" % (k, d["lines"]) for k, d in
                          sorted(segments.items(), key=lambda kv: (-kv[1]["lines"], kv[0]))
                          if k in cues) + ".", "",
              "`ca`/`on`/`te`/`hammond` are the leading letters of `carter`/`oneill`/`tealc`/`hammond`, which",
              "is a lead rather than a field: `ja_` fits both `jacob` and Daniel Jackson, `tokra_` is a",
              "faction, and `sc1_` is a scene number. So the prefix stays in `segment` and `speaker` stays",
              "empty on those rows.", "",
              "## By mission", "",
              "`scenes` counts distinct *(segment, scene)* pairs: an interlude numbers from 01 again, and",
              "**%d** scene ids occur in more than one segment of the same mission (e.g. %s), so a scene"
              % (len(reused), ", ".join("`%s`/%s in %s" % (m, sc, "+".join(sorted(v)))
                                         for (m, sc), v in sorted(reused.items())[:3]) or "none"),
              "number alone would undercount them.", "",
              "| mission | lines | scenes | speakers | segments | map of that id ships |",
              "|---|---:|---:|---:|---|---|"]
    for m in missions:
        d = per_mission[m]
        segs = " ".join("`%s`" % s for s in sorted(d["segments"]))
        lines.append(f"| `{m}` | {d['lines']} | {len(d['scenes'])} | "
                     f"{len([k for k in d['speakers'] if k != '?'])} | {segs} | "
                     f"{'yes' if has_map(m) else '**no**'} |")

    lines += ["", "## Segments with no campaign id", "",
              "These are recorded, named, and not a mission id the campaign or the `Maps/` tree uses. The",
              "`named-cue` segments are the free-form set; the others are the prologue, the tutorial and",
              "the coda.", "",
              "| segment | lines | scenes | speakers |", "|---|---:|---:|---|"]
    for s, d in sorted(segments.items(), key=lambda kv: (-kv[1]["lines"], kv[0])):
        lines.append(f"| `{s}` | {d['lines']} | {len(d['scenes'])} | {len(d['speakers'])} |")

    lines += ["", "## Speakers", "", "| speaker | lines |", "|---|---:|"]
    for speaker, count in sorted(speakers.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| `{speaker}` | {count} |")
    if untagged:
        lines.append(f"| (no speaker tag) | {untagged} |")

    no_map = [m for m in missions if not has_map(m)]
    if no_map:
        lines += ["", "## Recorded but not playable in this build", "",
                  f"{len(no_map)} recorded mission ids have no map of the same id in `Maps/`: "
                  + ", ".join(f"`{m}` ({per_mission[m]['lines']} lines)"
                              for m in no_map) + ".", "",
                  "Weapons, levels and dialogue were produced for a longer campaign than this",
                  "prototype ships — consistent with the 37-chapter `ChapterList.ini` naming maps",
                  "(`m01a`, `m12ax`, …) that are absent (see [CONTENT_CENSUS.md](CONTENT_CENSUS.md)).",
                  "`M01` and `M05` carry speech *and* music and no map at all, so the voice work had",
                  "started before those two missions were cut: `docs/CUT_CONTENT.md` records the",
                  "correction, and it is the interlude shape above that the older index could not see.",
                  "Promoting this from a lead to a claim needs the scene-to-quest mapping in the maps",
                  "or the `.sgs` checkpoints (PLAN 5.4).", ""]
    with open(os.path.join("docs", "DIALOGUE.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))
    print(f"{len(rows)} WAVs, {len(missions)} missions, "
          f"{len(speakers)} speakers, {shapes.get('unparsed', 0)} unparsed, "
          f"{len(no_map)} recorded missions without a map -> docs/DIALOGUE.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
