#!/usr/bin/env python3
"""Which voice pack a pawn speaks with, and how one line becomes a sound (PLAN 3.10, 8.3).

`docs/systems/AUDIO.md` carried two open questions: *"which voice pack a given mission uses, and how
`VoiceSelector` picks between them"*. Both are answerable from data the project already reads, and the
answers are narrower than the question.

**The pack is a default property, not a mission setting.** `StargatePawn.SGVoicePackClass` is a
`class<StargateVoicePack>` and `StargatePawn` spawns *that class* on itself (`Spawn(SGVoicePackClass, self)`,
then `SGVoicePack.Say('MSGID_Dead',, TONE_Combat, MSGP_Critical)` at its call sites), so which pack a pawn
speaks with is fixed by its own class and inherited down the tree. `tools/defaults-emit.py` already decodes
the block that carries it, so this tool is a read: for every class under `StargatePawn` it reports the pack
and *how* the class got it.

    python3 tools/voice-packs.py --system DUMP/System -o inventory/voice_packs.csv   # drive the emitter
    python3 tools/voice-packs.py --tree $WORK/defaults -o /tmp/voice.csv             # an emitted tree
    python3 tools/voice-packs.py --tree $WORK/defaults --write-doc docs/systems/AUDIO.md
    python3 tools/voice-packs.py --check                                             # synthetic, no dump

Columns: `package,class,pack,source,via`.

* `source` is `own` when the class's own block names the pack, `inherited` when a super's does, `none` when
  neither does (the base `StargatePawn` sets none, so such a pawn never spawns a pack and never speaks), and
  `unread` when the class has **no emitted block at all** — the emitter could not locate its
  `defaultproperties`, so the class's own value cannot be read and the inherited one is only a lower bound.
  That distinction is the point of the column: `Tokra_Armed` is `unread`, not `none`.
* `via` is the ancestor the inherited value came from, and is empty for `own` and `none`.
* `pack` is a class name, which this repository already carries in `inventory/classes.csv`; no property value
  is copied into the artifact.

The tool's second half is the placement question, and it answers with a negative: **no pawn class is placed in
any of the 73 maps** (`inventory/map_actors.csv`, 6,658 rows over 389 distinct actor classes), so a mission's
pack set cannot be read from its placement. What fills a mission with pawns is 66 spawner placements
(`IntelligentSpawner` 62, `ScriptedSpawner`, `FishSpawner`) whose squad is a per-instance property of the map,
so the join stops there and `docs/systems/AUDIO.md` says so instead of guessing.
"""
import argparse
import collections
import csv
import importlib.util
import io
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLUMNS = ["package", "class", "pack", "source", "via"]
DOC_MARK = ("<!-- generated:voice-packs -->", "<!-- /generated:voice-packs -->")
PACK = re.compile(r"SGVoicePackClass\s*=\s*class'([^']*)'")
BASE = "StargatePawn"
SOURCES = ("own", "inherited", "unread", "none")


def table(path=os.path.join(ROOT, "inventory", "classes.csv")):
    """{class: (package, super)} from the committed class table (no dump)."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out[row["class"]] = (row["package"], row["super"])
    return out


def chain(cls, tree):
    """The class and its ancestors, nearest first, stopping at a cycle."""
    out, seen = [], set()
    while cls and cls not in seen:
        seen.add(cls)
        out.append(cls)
        cls = tree.get(cls, ("", ""))[1]
    return out


def under_pawn(cls, tree):
    return BASE in chain(cls, tree)


def walk(tree):
    """({class: pack}, {classes with an emitted block}) from an emitted defaults tree."""
    packs, blocks = {}, set()
    if not os.path.isdir(tree):
        return packs, blocks
    for pkg in sorted(os.listdir(tree)):
        directory = os.path.join(tree, pkg)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".defaultproperties.uc"):
                continue
            cls = name[: -len(".defaultproperties.uc")]
            blocks.add(cls)
            with open(os.path.join(directory, name), encoding="utf-8", errors="replace") as fh:
                hit = PACK.search(fh.read())
            if hit:
                packs[cls] = hit.group(1).split(".")[-1]
    return packs, blocks


def resolve(classes, packs, blocks):
    """One row per class under `StargatePawn`, sorted by class."""
    rows = []
    for cls in sorted(classes):
        if not under_pawn(cls, classes):
            continue
        package = classes[cls][0]
        pack, source, via = "None", "none", ""
        for anc in chain(cls, classes):
            if anc in packs:
                pack = packs[anc]
                source = "own" if anc == cls else "inherited"
                via = "" if anc == cls else anc
                break
        if cls not in blocks:
            source = "unread"
        rows.append((package, cls, pack, source, via))
    return rows


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return [(r["package"], r["class"], r["pack"], r["source"], r.get("via", ""))
                for r in csv.DictReader(fh)]


def render(rows):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def placement(rows, actors_path=os.path.join(ROOT, "inventory", "map_actors.csv")):
    """(pawn rows placed in maps, maps, spawner placements, spawner classes) from the map actor table."""
    pawns = {r[1] for r in rows}
    maps, placed, spawners = set(), 0, collections.Counter()
    if not os.path.exists(actors_path):
        return None
    with open(actors_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            maps.add(row["map"])
            if row["class"] in pawns:
                placed += int(row["count"])
            if "Spawner" in row["class"]:
                spawners[row["class"]] += int(row["count"])
    return placed, len(maps), sum(spawners.values()), dict(spawners)


def summary(rows):
    counts = collections.Counter(r[3] for r in rows)
    packs = collections.Counter(r[2] for r in rows if r[2] != "None")
    return counts, packs


def doc_block(rows, actors):
    counts, packs = summary(rows)
    by_pack = collections.defaultdict(list)
    for _pkg, cls, pack, source, _via in rows:
        if pack != "None":
            by_pack[pack].append((cls, source))
    lines = ["",
             "**%d classes under `StargatePawn` — the pack is `own` in %d, `inherited` in %d, `unread` in %d "
             "and `none` in %d, over %d distinct pack%s.**"
             % (len(rows), counts["own"], counts["inherited"], counts["unread"], counts["none"], len(packs),
                "" if len(packs) == 1 else "s"),
             "",
             "| pack | pawns | how |",
             "|---|---:|---|"]
    for pack in sorted(by_pack, key=lambda p: (-len(by_pack[p]), p)):
        members = by_pack[pack]
        how = ", ".join("`%s`%s" % (cls, "" if source == "own" else " (inherited)")
                        for cls, source in sorted(members))
        lines.append("| `%s` | %d | %s |" % (pack, len(members), how))
    silent = [cls for _pkg, cls, pack, _s, _v in rows if pack == "None"]
    if silent:
        lines += ["", "The **%d** classes that set no pack and inherit none are silent by construction — the "
                       "base class sets no `SGVoicePackClass`, so nothing is spawned: %s."
                  % (len(silent), ", ".join("`%s`" % c for c in silent))]
    if actors:
        placed, maps, spawns, classes = actors
        lines += ["", "**Placement answers nothing.** %d pawns are placed across the %d maps in "
                       "`inventory/map_actors.csv`; the %d spawner placements (%s) are what fills a mission, and "
                       "each spawner's squad is a per-instance property of the map, so a mission's pack set is "
                       "not readable from its actor table."
                  % (placed, maps, spawns,
                     ", ".join("%s %d" % (k, classes[k]) for k in sorted(classes)))]
    return "\n".join(lines)


def replace_block(text, block):
    begin, end = DOC_MARK
    i, j = text.find(begin), text.find(end)
    if i < 0 or j <= i:
        sys.exit("document has no %s ... %s block" % DOC_MARK)
    return text[: i + len(begin)] + block + "\n" + text[j:]


def emit(rows, args):
    counts, packs = summary(rows)
    where = placement(rows)
    path = args.write_doc or args.check_doc
    if path:
        with open(path, encoding="utf-8") as fh:
            current = fh.read()
        fresh = replace_block(current, doc_block(rows, where))
        if args.check_doc:
            if fresh != current:
                print("FAIL %s: the generated block is stale (run --write-doc)" % path)
                return 1
            print("ok   voice-packs-doc: %s matches its rows" % path)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(fresh)
            print("ok   voice-packs-doc: wrote %s" % path)
    if args.out:
        with open(args.out, "w", newline="") as fh:
            fh.write(render(rows))
    elif not path:
        sys.stdout.write(render(rows))
    print("ok   voice-packs: %d class(es) under %s; %s; %d pack(s)"
          % (len(rows), BASE, ", ".join("%s %d" % (s, counts[s]) for s in SOURCES), len(packs)))
    if where:
        print("ok   voice-packs: %d pawn(s) placed over %d map(s); the %d spawner placement(s) are the source"
              % (where[0], where[1], where[2]))
    return 0


def check():
    """Synthetic: no dump, nothing written to the tree."""
    classes = {
        "StargatePawn": ("StargateGame", "Pawn"),
        "Pawn": ("Engine", "Info"),
        "TestOwner": ("StargateContent", "StargatePawn"),
        "TestChild": ("StargateContent", "TestOwner"),
        "TestSilent": ("StargateContent", "StargatePawn"),
        "TestUnread": ("StargateContent", "TestOwner"),
        "NotAPawn": ("StargateContent", "Actor"),
    }
    packs = {"TestOwner": "TestVoicePack", "NotAPawn": "OtherVoicePack"}
    blocks = {"StargatePawn", "TestOwner", "TestChild", "TestSilent", "NotAPawn"}
    rows = resolve(classes, packs, blocks)
    want = [
        ("StargateGame", "StargatePawn", "None", "none", ""),
        ("StargateContent", "TestChild", "TestVoicePack", "inherited", "TestOwner"),
        ("StargateContent", "TestOwner", "TestVoicePack", "own", ""),
        ("StargateContent", "TestSilent", "None", "none", ""),
        ("StargateContent", "TestUnread", "TestVoicePack", "unread", "TestOwner"),
    ]
    if rows != want:
        print("FAIL resolve:\n got  %r\n want %r" % (rows, want))
        return 1
    if any(r[1] == "NotAPawn" for r in rows):
        print("FAIL a class outside the StargatePawn tree was reported")
        return 1
    doc = doc_block(rows, (0, 73, 66, {"IntelligentSpawner": 62, "ScriptedSpawner": 3}))
    for needle in ("| `TestVoicePack` | 3 |",
                   "`TestOwner`", "`TestChild` (inherited)",
                   "`TestUnread`",
                   "**Placement answers nothing.** 0 pawns",
                   "spawner placements (IntelligentSpawner 62, ScriptedSpawner 3)"):
        if needle not in doc:
            print("FAIL doc_block is missing %r:\n%s" % (needle, doc))
            return 1
    stale = "a\n<!-- generated:voice-packs -->\nold\n<!-- /generated:voice-packs -->\nb\n"
    fresh = replace_block(stale, "\nnew")
    if "old" in fresh or "new" not in fresh:
        print("FAIL replace_block did not replace the marked block")
        return 1
    again = resolve(classes, packs, blocks)
    if again != rows:
        print("FAIL resolve is not deterministic")
        return 1
    print("ok   voice-packs: an own value beats an inherited one, an unread block is told from none, a class "
          "outside the tree is ignored, and the doc block renders both halves")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--system", help="the dump's System directory (drives defaults-emit into a temp tree)")
    ap.add_argument("--tree", help="an already emitted tree (defaults-emit --emit DIR)")
    ap.add_argument("-o", "--out", help="write the CSV here (default: stdout)")
    ap.add_argument("--write-doc", metavar="PATH", help="regenerate the page's table between its markers")
    ap.add_argument("--check-doc", metavar="PATH", help="fail if the page's table is stale (in `make ci`)")
    ap.add_argument("--from-csv", metavar="PATH", default=os.path.join(ROOT, "inventory", "voice_packs.csv"),
                    help="with --check-doc/--write-doc and no tree: read this artifact instead")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; needs no dump")
    args = ap.parse_args()

    if args.check:
        return check()
    classes = table()
    if not classes:
        sys.exit("voice-packs: inventory/classes.csv is missing or empty")
    if (args.write_doc or args.check_doc) and not (args.tree or args.system):
        if not os.path.exists(args.from_csv):
            sys.exit("voice-packs: %s does not exist" % args.from_csv)
        rows = read_csv(args.from_csv)
        if not rows:
            sys.exit("voice-packs: %s holds no rows" % args.from_csv)
        return emit(rows, args)
    tree = args.tree
    if tree is None:
        if not args.system:
            sys.exit("voice-packs: pass --system <DUMP/System> or --tree <emitted dir>")
        spec = importlib.util.spec_from_file_location(
            "sg1_defaults_emit", os.path.join(ROOT, "tools", "defaults-emit.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tree = tempfile.mkdtemp(prefix="voice-packs-")
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            module.sweep(args.system, None, emit_dir=tree)
    packs, blocks = walk(tree)
    if not packs:
        sys.exit("voice-packs: no SGVoicePackClass value under %s" % tree)
    rows = resolve(classes, packs, blocks)
    if not rows:
        sys.exit("voice-packs: no class under %s" % BASE)
    return emit(rows, args)


if __name__ == "__main__":
    sys.exit(main())
