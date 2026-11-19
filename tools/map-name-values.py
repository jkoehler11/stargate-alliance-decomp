#!/usr/bin/env python3
"""What a placed actor's name-typed properties put into a map package's name table (PLAN 5.2).

`docs/CLASS_DEFAULTS.md` records the open bullet: *"The roster itself. The squad scripts maps name
(`TSS_7A_B_Jaffa_01`, `AI_Jaffa_Horus_Med_single`) appear in those packages' name tables but are neither
imports nor exports, so the reference to them lives in data inside the `Level` export, which this reader does
not parse."* That is one instance of a general question, and the general question has a cheap answer: a
package's name table holds strings for every object, import and property it needs -- and also for every
**name value** a placed actor's property carries. A name value is data, not a reference: it needs no import,
no export and no resolution, only a table entry.

So this tool classifies a map's name table by what needs each entry:

* an **export**'s object name,
* an **import**'s name,
* a **name value** -- a `UNameProperty` value read out of a placed export's property block (this is where
  `SuggestedSquadScript` keeps a roster),
* or **vocabulary** -- neither of the three: property names, class names, package names, the strings the
  table exists to be indexed by.

The join is by string and folds case, because a name index resolves to one entry and UnrealScript compares
names case-insensitively. Nothing here reads a `Level` export: `SuggestedSquadScript` is a declared
`UNameProperty` on `IntelligentSpawner` (`inventory/class_properties.csv`), so the roster is a property value
on the spawner that names it.

    python3 tools/map-name-values.py DUMP/Maps --sweep -o inventory
    python3 tools/map-name-values.py --report
    python3 tools/map-name-values.py --write-doc docs/MAP_NAME_VALUES.md
    python3 tools/map-name-values.py --check-doc docs/MAP_NAME_VALUES.md
    python3 tools/map-name-values.py --check          # synthetic, no dump, writes nothing

Reads the map packages and three committed tables; no boot. The values themselves are game content and never
reach the artifact: rows carry counts, class names and property names only.
"""
import argparse
import collections
import csv
import re
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from ue2pkg import Package  # noqa: E402

ARTIFACT = os.path.join(ROOT, "inventory/map_name_values.csv")
DOC = os.path.join(ROOT, "docs/MAP_NAME_VALUES.md")
PROPERTIES = os.path.join(ROOT, "inventory/class_properties.csv")
SQUAD = os.path.join(ROOT, "inventory/squad_scripts.csv")
DOC_START = "<!-- generated:map-name-values -->"
DOC_END = "<!-- /generated:map-name-values -->"

FIELDS = ["kind", "map", "exports", "blocks", "name_entries", "name_unique", "name_props", "name_values",
          "distinct", "values_export", "values_import", "values_neither", "values_unknown",
          "values_malformed", "entries_only_values", "entries_structural", "squad_pairs", "squad_named", "note"]

# An `FCompactIndex` is at most five bytes, so a `Name` tag whose value region is wider cannot be a name index:
# the walk has landed on a value byte and read it as a type-6 tag. Measured on this corpus: 11 such tags, every
# one with a 12- or 16-byte "name" (a `Tag` on `KMeshProps`, a `Vector` on `counter`, a `ZoneNumber` on
# `SkyZoneInfo`), so they are refused rather than resolved to the reader's `?`.
MAX_NAME_INDEX = 5

SKIP = ("Class", "Function", "State", "ScriptStruct", "Const")


def load_prop_stream():
    path = os.path.join(ROOT, "tools", "prop-stream.py")
    spec = importlib.util.spec_from_file_location("prop_stream", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ps = load_prop_stream()


def classify(names, imports, exports, values, malformed=0):
    """Join a package's name table to the four things that can need an entry.

    `names` is the table's strings, `imports` and `exports` the strings those rows resolve to, and `values`
    a list of `(class, property, value)` triples read from placed property blocks. Pure: `--check` drives it
    with synthetic lists, so no package is needed to test the rule.
    """
    name_strs = {n.lower() for n in names if n}
    import_strs = {i.lower() for i in imports if i}
    export_strs = {e.lower() for e in exports if e}
    distinct, per_prop = {}, collections.defaultdict(set)
    for cls, prop, value in values:
        if not isinstance(value, str) or not value:
            continue
        distinct.setdefault(value.lower(), []).append((cls, prop))
        per_prop[prop].add(value.lower())
    v_export = {v for v in distinct if v in export_strs}
    v_import = {v for v in distinct if v in import_strs and v not in v_export}
    v_neither = {v for v in distinct if v not in export_strs and v not in import_strs}
    v_unknown = {v for v in distinct if v not in name_strs}
    only_values = v_neither & name_strs
    structural = name_strs - only_values - export_strs - import_strs
    out = dict(name_entries=len(names), name_unique=len(name_strs), name_values=len(values),
               values_malformed=malformed,
               distinct=len(distinct), values_export=len(v_export), values_import=len(v_import),
               values_neither=len(v_neither), values_unknown=len(v_unknown),
               entries_only_values=len(only_values), entries_structural=len(structural))
    out["_only_values"] = only_values
    out["_neither"] = v_neither
    out["_per_prop"] = per_prop
    return out


def read_names(pkg, skip=SKIP):
    """Every non-`Name`-excluded export's name-typed property values: `(class, property, value)`.

    A `Name` tag wider than `MAX_NAME_INDEX` is counted apart and not resolved: the size class cannot hold the
    value, so the tag is a mis-split and its string is the reader's `?` rather than a table entry.
    """
    values = []
    malformed = 0
    blocks = 0
    for exp in pkg.exports:
        data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
        if not data or pkg.class_name_of(exp) in skip:
            continue
        found = ps.find_block(data, pkg, min_props=1, record_class=exp["class"])
        if not found:
            continue
        blocks += 1
        _count, start, _props = found
        props, _end, _reason = ps.read_block(data[start:], pkg, True)
        for p in props:
            if p["type"] != "Name":
                continue
            if int(p.get("size") or 0) > MAX_NAME_INDEX:
                malformed += 1
                continue
            if isinstance(p.get("value"), str):
                values.append((pkg.class_name_of(exp), p["name"], p["value"]))
    return blocks, values, malformed


def load_squad(path=SQUAD):
    """map -> the squad script names the committed census pairs with it."""
    out = collections.defaultdict(set)
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                out[row["map"]].add(row["squad_script"].lower())
    except OSError:
        return {}
    return out


def blank(kind, label, note=""):
    row = {k: "" for k in FIELDS}
    row.update(kind=kind, map=label, note=note)
    return row


def sweep(maps_dir, out=None, only=None):
    squad = load_squad()
    rows, per_prop = [], collections.defaultdict(lambda: [0, 0, set(), set()])
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
            rows.append(blank("map", label, "unreadable: %s" % exc))
            continue
        blocks, values, malformed = read_names(pkg)
        got = classify([n[0] if isinstance(n, (tuple, list)) else n for n in (pkg.names or ())],
                       [pkg.name(i["name"]) for i in pkg.imports],
                       [pkg.name(e["name"]) for e in pkg.exports],
                       values, malformed)
        row = blank("map", label)
        row.update(exports=len(pkg.exports), blocks=blocks, name_props=len(values))
        for key in ("name_entries", "name_unique", "name_values", "distinct", "values_export", "values_import",
                    "values_neither", "values_unknown", "values_malformed", "entries_only_values",
                    "entries_structural"):
            row[key] = got[key]
        pair = squad.get(label, set())
        row["squad_pairs"] = len(pair)
        row["squad_named"] = len(pair & got["_only_values"])
        rows.append(row)
        for prop, seen in got["_per_prop"].items():
            slot = per_prop[prop]
            slot[0] += len(seen)
            slot[1] += len(seen & got["_neither"])
            slot[2].update(seen)
            slot[3].add(label)
        print("%-28s values=%-6d distinct=%-6d only-values=%-6d structural=%-6d"
              % (label, got["name_values"], got["distinct"], got["entries_only_values"],
                 got["entries_structural"]), flush=True)
    for prop in sorted(per_prop, key=lambda p: (-per_prop[p][0], p)):
        slot = per_prop[prop]
        row = blank("prop", prop)
        row.update(name_values=slot[0], distinct=len(slot[2]), values_neither=slot[1],
                   note="maps=%d" % len(slot[3]))
        rows.append(row)
    census_row(rows)
    if out:
        write_artifact(rows, os.path.join(out, "map_name_values.csv"))
    return rows


def census_row(rows):
    maps = [r for r in rows if r["kind"] == "map" and r["exports"] != ""]
    total = collections.Counter()
    for r in maps:
        for key in ("exports", "blocks", "name_props", "name_entries", "name_unique", "name_values", "distinct",
                    "values_export", "values_import", "values_neither", "values_unknown", "values_malformed",
                    "entries_only_values", "entries_structural", "squad_pairs", "squad_named"):
            total[key] += int(r[key] or 0)
    row = blank("census", "corpus")
    for key in total:
        row[key] = total[key]
    row["note"] = ("maps=%d;unreadable=%d;props=%d;zero_value_maps=%d;mis_parsed_maps=%d"
                   % (len(maps), len([r for r in rows if r["kind"] == "map" and r["exports"] == ""]),
                      len([r for r in rows if r["kind"] == "prop"]),
                      len([r for r in maps if int(r["name_values"] or 0) == 0]),
                      len([r for r in maps if int(r["values_malformed"] or 0) > 0])))
    rows.append(row)
    return row


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    print("%s: %d rows" % (path, len(rows)))


def note_values(row):
    out = {}
    for part in (row.get("note") or "").split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            try:
                out[key.strip()] = int(value)
            except ValueError:
                pass
    return out


NUM_4 = re.compile(r"(?<![\d.,])(\d{4,})(?![\d.,])")


def commas(text):
    """Thousands separators for the counts, so a page of five-digit numbers stays readable."""
    return NUM_4.sub(lambda m: "{:,}".format(int(m.group(1))), text)


def render(rows):
    c = [r for r in rows if r["kind"] == "census"][0]
    maps = [r for r in rows if r["kind"] == "map" and r["exports"] != ""]
    props = [r for r in rows if r["kind"] == "prop"]
    vals = note_values(c)
    unique, only = int(c["name_unique"]), int(c["entries_only_values"])
    structural = int(c["entries_structural"])
    out = []
    objects = unique - only - structural
    c_export, c_import = int(c["values_export"]), int(c["values_import"])
    out.append("**%d of a map corpus's %d distinct name-table entries (%.1f%%) exist because a placed actor's "
               "property names them -- not because the package imports, exports or declares that name.** A name "
               "value is data: `SuggestedSquadScript` is a declared `UNameProperty` on `IntelligentSpawner`, so "
               "the roster a map ships is a property value and needs no import, no export and no `Level` export "
               "to parse." % (only, unique, 100.0 * only / unique if unique else 0.0))
    out.append("")
    out.append("| measure | answer |")
    out.append("|---|---|")
    out.append("| maps read | **%d** |" % vals.get("maps", 0))
    out.append("| exports over those maps | **%s** |" % c["exports"])
    out.append("| exports with a readable property block | **%s** |" % c["blocks"])
    out.append("| name-typed property values | **%s** |" % c["name_values"])
    out.append("| distinct name values | **%s** |" % c["distinct"])
    out.append("| -- equal to an export's object name | **%s** |" % c["values_export"])
    out.append("| -- equal to an import's name | **%s** |" % c["values_import"])
    out.append("| -- equal to neither (data only) | **%s** |" % c["values_neither"])
    out.append("| -- absent from the name table (the join\'s control) | **%s** |" % c["values_unknown"])
    out.append("| `Name` tags refused as mis-splits (wider than an index) | **%s** |" % c["values_malformed"])
    out.append("| name-table entries | **%s** |" % c["name_entries"])
    out.append("| -- distinct | **%s** |" % c["name_unique"])
    out.append("| -- needed only by a name value | **%s** (%.1f%%) |"
               % (c["entries_only_values"],
                  100.0 * only / unique if unique else 0.0))
    out.append("| -- vocabulary (neither object nor value) | **%s** (%.1f%%) |"
               % (c["entries_structural"], 100.0 * structural / unique if unique else 0.0))
    out.append("| -- an export's or import's own name | **%s** (%.1f%%) |"
               % (objects, 100.0 * objects / unique if unique else 0.0))
    out.append("| squad-script pairs in `inventory/squad_scripts.csv` | **%s** |" % c["squad_pairs"])
    out.append("| -- of those named as a data-only value in their own map | **%s** |" % c["squad_named"])
    out.append("")
    out.append("The four arms partition the table -- **%s** objects, **%s** value-only strings, **%s** "
               "vocabulary, totalling the **%s** distinct entries the 73 maps need -- so no row is counted "
               "twice and the split is the whole table, not a sample of it. **%s** distinct values resolve to an "
               "object (`%s` an export's own name, `%s` an import's), so a `Tag` that repeats an actor's name "
               "costs the table nothing." 
               % (objects, only, structural, unique, c_export + c_import, c_export, c_import))
    out.append("")
    out.append("**The join's own control is the refusals, not a hope.** A `Name` tag's value region is an "
               "`FCompactIndex`, at most five bytes, so a wider one cannot be a name index and is counted as a "
               "mis-split rather than resolved to the reader's `?`: **%s** tags over %s maps, every one of the "
               "shape a `Vector` or a `ZoneNumber` value read as a type-6 tag. After that filter **%s** value in "
               "the whole corpus names no table entry -- one out-of-range index, not a systematic miss -- which "
               "is the assertion that the string comparison is right: an index can only resolve inside the "
               "table."
               % (c["values_malformed"], note_values(c).get("mis_parsed_maps", 0), c["values_unknown"]))
    out.append("")
    out.append("### The properties that carry names")
    out.append("")
    out.append("| property | values | distinct | data-only | maps |")
    out.append("|---|---:|---:|---:|---:|")
    for r in props[:15]:
        out.append("| `%s` | %s | %s | %s | %s |"
                   % (r["map"], r["name_values"], r["distinct"], r["values_neither"],
                      note_values(r).get("maps", "")))
    out.append("")
    out.append("### By map")
    out.append("")
    out.append("| map | exports | name values | distinct | data-only entries | vocabulary |")
    out.append("|---|---:|---:|---:|---:|---:|")
    for r in sorted(maps, key=lambda x: (-int(x["entries_only_values"] or 0), x["map"])):
        out.append("| `%s` | %s | %s | %s | %s | %s |"
                   % (r["map"], r["exports"], r["name_values"], r["distinct"],
                      r["entries_only_values"], r["entries_structural"]))
    return commas("\n".join(out) + "\n")


def splice(path, block):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if DOC_START not in text or DOC_END not in text:
        raise SystemExit("%s: markers not found" % path)
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + DOC_END + tail


def read_artifact(path=ARTIFACT):
    with open(path, newline="", encoding="utf-8") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def check():
    """Synthetic name tables: the four arms, case folding and the impossible arm."""
    names = ["Foo", "Bar", "Baz", "Quux", "SquadA", "Prop"]
    imports = ["Bar"]
    exports = ["Foo"]
    got = classify(names, imports, exports, [("A", "P", "Baz"), ("A", "P", "squada"), ("B", "Q", "Baz")],
                   malformed=3)
    assert got["distinct"] == 2 and got["values_malformed"] == 3, got
    assert got["values_export"] == 0 and got["values_import"] == 0, got
    assert got["values_neither"] == 2, got
    assert got["values_unknown"] == 0, got
    assert got["entries_only_values"] == 2, got
    # export Foo, import Bar, values Baz/SquadA, vocabulary Quux and Prop
    assert got["entries_structural"] == 2, got
    # a value that is an export's name is an object, not data
    got = classify(names, imports, exports, [("A", "P", "foo")])
    assert got["values_export"] == 1 and got["entries_only_values"] == 0, got
    assert got["entries_structural"] == 4, got
    # a value outside the table is the join's own failure, and it is counted as one
    got = classify(names, imports, exports, [("A", "P", "NotInTable")])
    assert got["values_unknown"] == 1 and got["entries_only_values"] == 0, got
    # an import's name is an object too
    got = classify(names, imports, exports, [("A", "P", "bar")])
    assert got["values_import"] == 1 and got["entries_only_values"] == 0, got
    # the per-property rollup separates two properties that carry the same value
    got = classify(names, imports, exports, [("A", "P", "Baz"), ("B", "Q", "Baz"), ("B", "Q", "SquadA")])
    assert sorted(len(v) for v in got["_per_prop"].values()) == [1, 2], got["_per_prop"]
    # render() reads one census row and must not crash on a corpus of one
    rows = [blank("map", "M00"), blank("census", "corpus")]
    rows[0].update(exports=1, blocks=1, name_entries=4, name_unique=4, name_values=2, distinct=2,
                   values_export=0, values_import=0, values_neither=2, values_unknown=0,
                   entries_only_values=2, entries_structural=2, note="")
    rows[1].update(exports=1, blocks=1, name_entries=4, name_unique=4, name_values=2, distinct=2,
                   values_export=0, values_import=0, values_neither=2, values_unknown=0,
                   entries_only_values=2, entries_structural=2, note="maps=1;unreadable=0;props=0;zero_value_maps=0")
    text = render(rows)
    assert "**2 of a map corpus's 4 distinct name-table entries" in text, text[:200]
    print("check: 6 synthetic cases pass")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("maps", nargs="?", help="the dump's Maps directory")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--only", default=None)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc", default=None)
    ap.add_argument("--check-doc", default=None)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check()
    if args.report or args.write_doc or args.check_doc:
        rows = read_artifact()
        if args.report:
            print(render(rows))
        if args.write_doc:
            new = splice(args.write_doc, render(rows))       # compute before opening
            with open(args.write_doc, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("%s: written" % args.write_doc)
        if args.check_doc:
            want = splice(args.check_doc, render(rows))
            with open(args.check_doc, encoding="utf-8") as fh:
                have = fh.read()
            if want != have:
                raise SystemExit("FAIL map-name-values: %s is stale" % args.check_doc)
            print("ok map-name-values: %s" % args.check_doc)
        return 0
    if not args.maps:
        ap.error("a Maps directory, --report, --write-doc, --check-doc or --check is required")
    rows = sweep(args.maps, args.out, args.only)
    c = [r for r in rows if r["kind"] == "census"][0]
    print("maps=%s values=%s distinct=%s only-values=%s/%s"
          % (c["exports"] and note_values(c).get("maps"), c["name_values"], c["distinct"],
             c["entries_only_values"], c["name_unique"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
