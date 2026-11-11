#!/usr/bin/env python3
"""Score the live class-table read against the registration-literal names (U8).

Two instruments name the same 492 tables in `.rdata`, and they answer different questions:

* `inventory/xbe_class_table_names.csv` names a table from the **literal** a registration site pushes
  before the store that installs it (`tools/registration-sites.py`). It names the class whose
  *constructor* installs the table.
* `inventory/live_class_tables.csv` names a table from the **objects** that carry it in a booted guest
  (`tools/xemu/class-tables-live.py`): every object's `+0x00` is its class's table and `+0x24` its own
  `UClass`. It names the class of each *instance*.

So a disagreement is only an error when the two readings cannot both be true. For a table whose objects
are all of class `C` and whose literal named `S`:

    agree         S == C
    installer     S is an ancestor of C -- C's constructor installs nothing, so its objects carry the
                  table S's constructor installed. Both readings are right; the literal is the better
                  name for the table, the read is the better name for the object.
    inverted      C is an ancestor of S -- an object of class C cannot carry a table only its descendant
                  installs, so the literal is on the wrong table (`0x004cfba0`, `WaterTexture`/`Texture`).
    unrelated     neither is an ancestor of the other: the literal and the objects name two families.
    shared        more than one class carries the table (reported, never scored: the table is inherited).
    static-unnamed  the literal names no class for this table (26 rows); the read may name it.
    unknown-class one of the two names has no row in `inventory/classes.csv`, so ancestry cannot be
                  tested -- `System` is a native class with no script row, `new` is not a class at all.

The artifact keeps the literal's row even where this tool calls it wrong: it is that pass's output and
`registration-sites.py --check` reproduces it. This tool is the tiebreaker, not an editor.

    python3 tools/class-table-reconcile.py                      # defaults to the three committed CSVs
    python3 tools/class-table-reconcile.py --all                # every table, not only disagreements
    python3 tools/class-table-reconcile.py --check              # synthetic case, no dump, writes nothing
"""
import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LIVE = os.path.join(REPO, "inventory", "live_class_tables.csv")
STATIC = os.path.join(REPO, "inventory", "xbe_class_table_names.csv")
CLASSES = os.path.join(REPO, "inventory", "classes.csv")


def read_live(path):
    """{table_va: [(class, objects), ...]} from `table_va,class,objects`."""
    live = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            table = int(row["table_va"], 16)
            live.setdefault(table, []).append((row["class"].strip(), int(row["objects"])))
    return live


def read_static(path):
    """{table_va: class name or ''} from `inventory/xbe_class_table_names.csv`."""
    static = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            static[int(row["table_va"], 16)] = (row.get("class") or "").strip()
    return static


def read_supers(path):
    """{class: super} from `inventory/classes.csv`; a native class with no script row is absent."""
    supers = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("class") or "").strip()
            if name and name not in supers:
                supers[name] = (row.get("super") or "").strip()
    return supers


def ancestors(name, supers, limit=64):
    """Every script ancestor of `name`, nearest first."""
    out, seen = [], {name}
    cur = supers.get(name, "")
    while cur and cur not in seen and len(out) < limit:
        out.append(cur)
        seen.add(cur)
        cur = supers.get(cur, "")
    return out


def verdict_of(names, static_name, supers):
    """(verdict, detail) for one table, given the class names its objects carry."""
    if static_name is None:
        return "not-in-static", "the literal artifact has no row for this table"
    if len(names) > 1:
        return "shared", "%d classes carry it" % len(names)
    live_name = names[0]
    if not static_name:
        return "static-unnamed", "the read names it %s" % live_name
    if static_name == live_name:
        return "agree", ""
    if static_name not in supers or live_name not in supers:
        return "unknown-class", "%s has no script row in inventory/classes.csv" % (
            static_name if static_name not in supers else live_name)
    up = ancestors(live_name, supers)
    if static_name in up:
        return "installer", "%s < %s" % (live_name, " < ".join(up[:up.index(static_name) + 1]))
    down = ancestors(static_name, supers)
    if live_name in down:
        return "inverted", "the literal names a child of the objects' class: %s < %s" % (
            static_name, " < ".join(down[:down.index(live_name) + 1]))
    return "unrelated", "no ancestor relation either way"


def reconcile(live, static, supers):
    """Rows `(table, verdict, static_name, [(class, objects)], detail)`, sorted by table."""
    rows = []
    for table in sorted(live):
        entries = sorted(live[table], key=lambda e: (-e[1], e[0]))
        verdict, detail = verdict_of([e[0] for e in entries], static.get(table), supers)
        rows.append((table, verdict, static.get(table) or "", entries, detail))
    return rows


def summarise(rows, log=print):
    """{verdict: count} over tables, printed."""
    counts = {}
    for _, verdict, _, _, _ in rows:
        counts[verdict] = counts.get(verdict, 0) + 1
    log("%d table(s), %d row(s): %s" % (
        len(rows), sum(len(e) for _, _, _, e, _ in rows),
        ", ".join("%s %d" % (k, counts[k]) for k in sorted(counts))))
    return counts


def report(rows, show_all, log=print):
    """Print the rows worth a reader's attention; `agree` only with `--all`."""
    quiet = ("agree",)
    for table, verdict, static_name, entries, detail in rows:
        if not show_all and verdict in quiet:
            continue
        log("  %#010x  %-14s literal=%-24s %s%s" % (
            table, verdict, static_name or "-",
            ", ".join("%s(%d)" % e for e in entries),
            "  -- " + detail if detail else ""))


def table_lookups():
    """The three committed artifacts, or a message naming the one that is missing."""
    for path in (LIVE, STATIC, CLASSES):
        if not os.path.exists(path):
            raise SystemExit("%s is missing: this tool reads the committed artifacts, not the dump"
                             % os.path.relpath(path, REPO))
    return read_live(LIVE), read_static(STATIC), read_supers(CLASSES)


def check():
    """A synthetic world: one table per verdict, no dump and no file written."""
    supers = {"Base": "Object", "Child": "Base", "Grandchild": "Child", "Other": "Object"}
    live = {
        0x004cfba0: [("Texture", 454)],          # inverted: the literal named its child
        0x004cb0a0: [("SGC_SecurityReader", 1)],  # installer: literal names an ancestor
        0x004c9090: [("ACTION_Walk", 4), ("ACTION_SetStance", 4)],   # shared
        0x004e1ce8: [("System", 1)],             # unknown-class: `System` has no script row
        0x004f0000: [("Child", 2)],              # agree
        0x004f0100: [("Grandchild", 1)],         # static-unnamed
    }
    static = {0x004cfba0: "Texture", 0x004cb0a0: "Triggers", 0x004c9090: "ACTION_WaitForTimer",
              0x004e1ce8: "new", 0x004f0000: "Child", 0x004f0100: ""}
    # `Texture` is deliberately absent from `supers`: the live class of the inverted row in the real world
    # has a script row, so add the chain the real dump has.
    supers.update({"Texture": "BitmapMaterial", "FractalTexture": "Texture", "WaterTexture": "FractalTexture",
                   "Triggers": "Actor", "SGC_SecurityReader": "Triggers"})
    static[0x004cfba0] = "WaterTexture"
    want = {0x004cfba0: "inverted", 0x004cb0a0: "installer", 0x004c9090: "shared",
            0x004e1ce8: "unknown-class", 0x004f0000: "agree", 0x004f0100: "static-unnamed"}
    rows = reconcile(live, static, supers)
    got = {table: verdict for table, verdict, _, _, _ in rows}
    if got != want:
        print("check FAILED: %r" % (sorted(got.items()),))
        return 1
    counts = summarise(rows, log=lambda *a: None)
    if counts != {"inverted": 1, "installer": 1, "shared": 1, "unknown-class": 1, "agree": 1,
                  "static-unnamed": 1}:
        print("check FAILED: counts %r" % (counts,))
        return 1
    report(rows, show_all=False, log=lambda *a: None)
    print("check ok: %d verdict(s) over %d table(s)" % (len(counts), len(rows)))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--live", default=None, help="the booted read (%s)" % os.path.relpath(LIVE, REPO))
    ap.add_argument("--static", default=None, help="the literal names (%s)" % os.path.relpath(STATIC, REPO))
    ap.add_argument("--classes", default=None, help="the script tree (%s)" % os.path.relpath(CLASSES, REPO))
    ap.add_argument("--all", action="store_true", help="print the agreeing tables too")
    ap.add_argument("--check", action="store_true", help="synthetic self-test: no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        return check()
    if args.live or args.static or args.classes:
        live = read_live(args.live or LIVE)
        static = read_static(args.static or STATIC)
        supers = read_supers(args.classes or CLASSES)
    else:
        live, static, supers = table_lookups()
    rows = reconcile(live, static, supers)
    summarise(rows)
    report(rows, args.all)
    return 0


if __name__ == "__main__":
    sys.exit(main())
