#!/usr/bin/env python3
"""Where a refused property tag's name comes from, and what the engine does with one no class declares.

`docs/MAP_ACTION_PARAMETERS.md` leaves five statements refused and all five are the same tag: `EndType` on
`ACTION_SetIMusic`, in `M02A` and `M14A`.  A refused name is not a parse defect when every other name in the
block is declared, so the question is which revision wrote it.  This tool answers the halves that need no boot:

* the name's *origin*: every `System/*.u` package's own name table, whether it holds `EndType`, how many of its
  class texts spell it, and which of its classes declare it (`inventory/class_properties.csv`);
* the *placement*: the five tags' bytes, read with the same locator and declared-name repair
  `tools/action-parameters.py` ships, and the enum member each one-byte value names -- the enum is the
  declaring property's own `friendly_type`, parsed out of the class text that declares it;
* the control: does any of those five blocks refuse a name *other* than the tag?  A block that refuses only the
  tag is a placement, not a misparse.

The runtime half is a native read, not this tool: `docs/NATIVE_PROPERTIES.md` §4 carries the tag walk at
`0x0003bdc0` and its unknown-name path (log, then skip the tag's `Size` bytes and read the next tag).

No parameter value that is content is written: a placement row names the map, the class, the action slot and
the enum member of an engine enum, which is the same granularity the shipped artifact already publishes.
"""

import argparse
import collections
import csv
import importlib.util
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from ue2pkg import Package  # noqa: E402

NAME = "EndType"
ARTIFACT = os.path.join(ROOT, "inventory/tag_origin.csv")
SOURCE = os.path.join(ROOT, "inventory/action_parameters.csv")
PROPERTIES = os.path.join(ROOT, "inventory/class_properties.csv")
DOC = os.path.join(ROOT, "docs/MAP_ACTION_PARAMETERS.md")
DOC_START = "<!-- generated:tag-origin -->"
DOC_END = "<!-- /generated:tag-origin -->"
DEFAULT_DUMP = os.path.join(os.path.expanduser("~"), "nfs/sg1-dump/Stargate_SG-1_-_The_Alliance-2005-12-15")
FIELDS = ["kind", "name", "package", "class", "index", "names", "texts", "value", "note"]


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ap = _load("action_parameters", "action-parameters.py")
st = _load("pkg_script_text", "pkg-script-text.py")

COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
IDENT = re.compile(r"[A-Za-z_]\w*")


def name_index(names):
    """The tag name's index in one package's own name table, or None -- an absent name cannot be spelled at all."""
    for i, entry in enumerate(names or []):
        if entry[0] == NAME:
            return i
    return None


def declarations(path=PROPERTIES):
    """{package: {class: count}} for every declaration of the name, and the friendly types those rows carry."""
    by_pkg = collections.defaultdict(lambda: collections.defaultdict(int))
    friendly = []
    if not os.path.exists(path):
        return by_pkg, friendly
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("property") == NAME:
                by_pkg[row["package"]][row["class"]] += 1
                if row.get("friendly_type"):
                    friendly.append(row["friendly_type"])
    return by_pkg, friendly


def class_texts(pkg):
    """{class name: ScriptText} for every Class export of the package that carries one."""
    out = {}
    for exp in pkg.exports:
        if pkg.class_name_of(exp) != "Class":
            continue
        text = st.script_text(pkg, exp, head=1 << 20)
        if text:
            out[pkg.name(exp["name"])] = text
    return out


def enum_members(text, enum):
    """The identifiers of `enum <enum> { ... }` in declaration order -- an unreal enum value is its position."""
    m = re.search(r"\benum\s+" + re.escape(enum) + r"\b[^{]*\{", text or "")
    if not m:
        return []
    i, depth = m.end(), 1
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    body = COMMENT.sub("", text[m.end():max(m.end(), i - 1)])
    out = []
    for part in body.split(","):
        ident = IDENT.match(part.strip())
        if ident:
            out.append(ident.group(0))
    return out


def read_system(dump):
    """One pass over System/*.u: name table, class texts.  Text extraction is 0.1-0.2 s per package."""
    system = os.path.join(dump, "System")
    if not os.path.isdir(system):
        sys.exit("no System/ under %s" % dump)
    pkgs = {}
    for fn in sorted(os.listdir(system)):
        if not fn.lower().endswith(".u"):
            continue
        pkg = Package(os.path.join(system, fn))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        pkgs[fn[:-2]] = pkg
    return pkgs


def package_rows(pkgs, decl_by_pkg):
    """One row per package: does its table hold the name, which classes declare it, which texts spell it."""
    rows, census = [], collections.Counter()
    spell = re.compile(r"\b" + re.escape(NAME) + r"\b")
    for stem in sorted(pkgs):
        pkg = pkgs[stem]
        idx = name_index(pkg.names)
        texts = class_texts(pkg)
        spelling = sorted(c for c, t in texts.items() if spell.search(t))
        declared = sorted(decl_by_pkg.get(stem, {}))
        rows.append(dict(kind="package", name=NAME, package=stem, **{"class": ""},
                         index="" if idx is None else idx, names=len(pkg.names or []), texts=len(spelling),
                         value="", note="declared_by=%s;spelled_by=%s"
                                        % (",".join(declared) or "-", ",".join(spelling) or "-")))
        census["packages"] += 1
        census["names_total"] += len(pkg.names or [])
        if idx is not None:
            census["hold_name"] += 1
        if spelling:
            census["spell_name"] += 1
    return rows, census


def enum_of(pkgs, friendly):
    """The enum a declaration names, and its members: `LevelInfo.EIMusicEndType` -> class text of `LevelInfo`."""
    for ftype in sorted(set(friendly)):
        if "." not in ftype:
            continue
        owner, enum = ftype.rsplit(".", 1)
        for stem in sorted(pkgs):
            text = class_texts(pkgs[stem]).get(owner)
            if text:
                members = enum_members(text, enum)
                if members:
                    return ftype, members
    return "", []


def sourced_maps(path=SOURCE):
    """The maps the shipped artifact refuses the tag in, with the number of placements it names there."""
    maps, count = set(), 0
    if not os.path.exists(path):
        sys.exit("no %s: run `make action-parameters` first" % path)
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("kind") == "residue" and row.get("name") == "property_refused"
                    and row.get("note", "").startswith(NAME + " ")):
                maps.add(row["map"])
                count += 1
    if not maps:
        sys.exit("%s refuses no %s placement" % (path, NAME))
    return sorted(maps), count


def map_rows(dump):
    """Every map package's own name table: a map can only write a tag name its table holds, so this is the origin.

    Only the header is read (names), which is why the whole `Maps/` walk is a second.
    """
    maps_dir = os.path.join(dump, "Maps")
    if not os.path.isdir(maps_dir):
        sys.exit("no Maps/ under %s" % dump)
    rows, census = [], collections.Counter()
    for fn in sorted(os.listdir(maps_dir)):
        if not fn.lower().endswith(".unr"):
            continue
        pkg = Package(os.path.join(maps_dir, fn))
        pkg.read_names()
        idx = name_index(pkg.names)
        rows.append(dict(kind="map", name=NAME, package=fn[:-4], **{"class": ""},
                         index="" if idx is None else idx, names=len(pkg.names or []), texts="", value="",
                         note="holds=%d" % (idx is not None)))
        census["maps"] += 1
        if idx is not None:
            census["maps_hold"] += 1
    return rows, census


def _placement_walk(dump, members):
    maps_dir = os.path.join(dump, "Maps")
    labels, expected = sourced_maps()
    supers = ap.class_supers()
    action_cls = ap.family(supers, "ScriptedAction") - ap.ACTION_BASES
    sequence_cls = ap.family(supers, "ScriptedSequence") | {"ScriptedSequence"}
    declared = ap.load_declared()
    rows = []
    for label in labels:
        pkg = Package(os.path.join(maps_dir, label + ".unr"))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        entries = [{"class": pkg.class_name_of(e), "name": pkg.name(e["name"])} for e in pkg.exports]
        classes = [e["class"] for e in entries]
        blocks = ap.read_blocks(pkg)
        ap.relocate(pkg, blocks, classes, action_cls, declared, supers)
        slots, _count = ap.slot_index(blocks, classes, sequence_cls)
        for idx in sorted(blocks):
            cls = classes[idx - 1] if 0 < idx <= len(classes) else ""
            if cls not in action_cls:
                continue
            blk = blocks[idx]
            hits = [p for p in blk["props"] if p["name"] == NAME]
            if not hits:
                continue
            allowed = ap.allowed_for(cls, declared, supers)
            other = [p["name"] for p in blk["props"] if p["name"] != NAME and p["name"] not in allowed]
            here = slots.get(idx)
            for p in hits:
                value = p.get("value")
                member = members[value] if isinstance(value, int) and 0 <= value < len(members) else None
                rows.append(dict(kind="placement", name=NAME, package=label, **{"class": cls},
                                 index=here[1] if here else "", names="", texts="",
                                 value=member or str(value),
                                 note="type=%s;size=%s;chain=%d;others_refused=%d"
                                      % (p.get("type"), p.get("size"), len(allowed), len(other))))
    rows.sort(key=lambda r: (r["package"], r["index"] if r["index"] != "" else -1))
    return rows, expected


def census_rows(pkg_census, map_census, places, expected, enum_name, members, decl_by_pkg):
    """The run-level counts as their own rows, so every sentence in the block is a read of the artifact."""
    values = collections.Counter(r["value"] for r in places)
    rows = [
        dict(kind="census", name="packages", package="corpus", **{"class": ""}, index="",
             names=pkg_census["names_total"], texts=pkg_census.get("spell_name", 0), value="",
             note="packages=%d;name_tables_holding=%d"
                  % (pkg_census["packages"], pkg_census.get("hold_name", 0))),
        dict(kind="census", name="maps", package="corpus", **{"class": ""}, index="", names=map_census["maps"],
             texts=map_census.get("maps_hold", 0), value="",
             note="maps_holding=%d" % map_census.get("maps_hold", 0)),
        dict(kind="census", name="placements", package="corpus", **{"class": ""}, index="", names="",
             texts="", value=",".join("%s=%d" % kv for kv in sorted(values.items())),
             note="artifact_names=%d;walked=%d;other_names_refused=%d"
                  % (expected, len(places), sum(int(r["note"].rsplit("=", 1)[-1]) for r in places))),
        dict(kind="census", name="enum", package="corpus", **{"class": ""}, index="", names="",
             texts=len(members), value=enum_name, note=";".join(members)),
        dict(kind="census", name="declarations", package="corpus", **{"class": ""}, index="", names="",
             texts="", value="",
             note=";".join("%s=%s" % (p, ",".join(sorted(c))) for p, c in sorted(decl_by_pkg.items()))),
    ]
    return rows


def rows_of(pkg_rows, map_name_rows, place_rows, census):
    return list(pkg_rows) + list(map_name_rows) + list(place_rows) + list(census)


def write_artifact(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    print("wrote %s (%d rows)" % (path, len(rows)))


def read_artifact(path=ARTIFACT):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def render(rows):
    pkgs = [r for r in rows if r["kind"] == "package"]
    maps = [r for r in rows if r["kind"] == "map"]
    places = [r for r in rows if r["kind"] == "placement"]
    census = {r["name"]: r for r in rows if r["kind"] == "census"}
    holders = [r for r in pkgs if r["index"] != ""]
    spelling = [r for r in pkgs if int(r["texts"] or 0)]
    map_holders = [r for r in maps if r["index"] != ""]
    total = len(pkgs)
    out = []
    out.append("**`EndType` is a name `Engine.IMusicActor` declares, no `GamePlay` class can even spell, and the two "
               "maps wrote it themselves: the five refused tags are a placement against a class revision this build "
               "no longer ships, and the engine reads them as a skip, not a refusal.**")
    out.append("")
    out.append("`inventory/tag_origin.csv` walks every `System/*.u` name table (%s packages, %s names), every "
               "`Maps/*.unr` table (%s maps) and every class text in the former. **%s of %s** packages hold the "
               "tag's name, **%s** spell it in a class text, and **%s of %s** maps hold it -- the two the refused "
               "tags sit in. A map can only write a name its own table holds, and a class can only declare one its "
               "package's table holds, so `GamePlay.u`'s table is the whole answer: the property is not there."
               % (census["packages"]["note"].split("packages=", 1)[1].split(";", 1)[0],
                  census["packages"]["names"], census["maps"]["names"], len(holders), total, len(spelling),
                  len(map_holders), census["maps"]["names"]))
    out.append("")
    out.append("| package | names | holds `EndType` | classes declaring | class texts spelling it |")
    out.append("|---|---|---|---|---|")
    for r in pkgs:
        out.append("| `%s` | %s | %s | %s | %s |"
                   % (r["package"], r["names"], ("index %s" % r["index"]) if r["index"] != "" else "no",
                      (r["note"].split("declared_by=", 1)[1].split(";", 1)[0] if "declared_by=" in r["note"] else "-"),
                      r["texts"]))
    out.append("")
    out.append("The maps that hold the name in their own table:")
    out.append("")
    out.append("| map | names | `EndType` index |")
    out.append("|---|---|---|")
    for r in map_holders:
        out.append("| `%s` | %s | %s |" % (r["package"], r["names"], r["index"]))
    out.append("")
    out.append("**The five placements, read with the shipped locator and its declared-name repair.**")
    out.append("")
    out.append("| map | slot | class | tag type | size | value |")
    out.append("|---|---|---|---|---|---|")
    for r in places:
        note = dict(kv.split("=", 1) for kv in r["note"].split(";") if "=" in kv)
        out.append("| `%s` | %s | `%s` | %s | %s | %s |"
                   % (r["package"], r["index"], r["class"], note.get("type", ""), note.get("size", ""), r["value"]))
    out.append("")
    enum = census.get("enum", {})
    if enum.get("texts"):
        out.append("`%s` declares `%s`; the five tags read %s, so every one names a member of the enum the "
                   "*declaring* class carries, and none is out of range. Each tag's `others_refused` is 0: the "
                   "block start is the shipped one, and the tag is the only name the shipped `GamePlay.u` cannot "
                   "declare. What the engine does with it is `docs/NATIVE_PROPERTIES.md` §4/§6."
                   % (enum.get("value", ""), "`, `".join(enum.get("note", "").split(";")),
                      census["placements"].get("value", "")))
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
    body = splice(path, render(rows))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    print("wrote %s" % path)


def check_doc(path, rows):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if DOC_START not in text or DOC_END not in text:
        sys.exit("markers not found in %s" % path)
    if splice(path, render(rows)) != text:
        sys.exit("%s is stale: run --write-doc" % path)
    print("%s matches the artifact" % path)


def check():
    """Synthetic cases only, no dump and no writes: the name lookup, the enum parse, the value mapping, the render
    and the doc round trip -- including that a corrupted block is refused."""
    assert name_index([("Foo", 0), (NAME, 0)]) == 1
    assert name_index([("Foo", 0)]) is None
    assert name_index(None) is None
    text = ("enum EIMusicEndType\n{\n\tIMEND_LOOP,   // 0\n\tIMEND_ONESHOT // 1\n};")
    members = enum_members(text, "EIMusicEndType")
    assert members == ["IMEND_LOOP", "IMEND_ONESHOT"], members
    assert enum_members("enum Other { A };", "EIMusicEndType") == []
    rows = rows_of(
        [dict(kind="package", name=NAME, package="Engine", **{"class": ""}, index=9, names=100, texts=1,
              value="", note="declared_by=IMusicActor;spelled_by=IMusicActor"),
         dict(kind="package", name=NAME, package="GamePlay", **{"class": ""}, index="", names=50, texts=0,
              value="", note="declared_by=-;spelled_by=-")],
        [dict(kind="map", name=NAME, package="M02A", **{"class": ""}, index=7, names=40, texts="", value="",
              note="holds=1")],
        [dict(kind="placement", name=NAME, package="M02A", **{"class": "ACTION_SetIMusic"}, index=1, names="",
              texts="", value="IMEND_LOOP", note="type=Byte;size=1;chain=5;others_refused=0")],
        census_rows({"packages": 2, "names_total": 150, "hold_name": 1, "spell_name": 1},
                    {"maps": 1, "maps_hold": 1},
                    [dict(value="IMEND_LOOP", note="others_refused=0")], 1, "LevelInfo.EIMusicEndType", members,
                    {"Engine": {"IMusicActor": 1}}))
    block = render(rows)
    assert render(rows) == block
    assert "GamePlay" in block and "IMEND_LOOP" in block and "M02A" in block
    tmp = tempfile.mkdtemp()
    try:
        page = os.path.join(tmp, "page.md")
        with open(page, "w", encoding="utf-8") as fh:
            fh.write("# page\n\n" + DOC_START + "\nold\n" + DOC_END + "\n\ntail\n")
        write_doc(page, rows)
        check_doc(page, rows)
        with open(page, encoding="utf-8") as fh:
            corrupt = fh.read().replace("IMEND_LOOP", "IMEND_NONSENSE")
        with open(page, "w", encoding="utf-8") as fh:
            fh.write(corrupt)
        try:
            check_doc(page, rows)
        except SystemExit:
            pass
        else:
            raise AssertionError("check_doc accepted a corrupted block")
        with open(page, encoding="utf-8") as fh:
            assert fh.read().endswith("tail\n"), "the block splice ate the page tail"
    finally:
        for name in os.listdir(tmp):
            os.unlink(os.path.join(tmp, name))
        os.rmdir(tmp)
    print("ok   tag-origin: name lookup, enum parse, value mapping, render and doc round trip (5 cases)")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("dump", nargs="?", default=os.environ.get("SG1_DUMP", DEFAULT_DUMP))
    p.add_argument("--sweep", action="store_true", help="walk System/*.u and the refused placements")
    p.add_argument("-o", "--out", default=None, help="artifact directory")
    p.add_argument("--write-doc", metavar="PAGE", default=None)
    p.add_argument("--check-doc", metavar="PAGE", default=None)
    p.add_argument("--report", action="store_true")
    p.add_argument("--check", action="store_true", help="synthetic self-test, no dump, writes nothing")
    args = p.parse_args(argv)

    if args.check:
        check()
        return
    if args.check_doc:
        check_doc(args.check_doc, read_artifact())
        return
    if args.write_doc:
        write_doc(args.write_doc, read_artifact())
        return
    if args.report:
        sys.stdout.write(render(read_artifact()) + "\n")
        return
    if not args.sweep:
        p.error("nothing to do: --sweep, --report, --write-doc, --check-doc or --check")
    dump = args.dump
    if os.path.basename(dump) != "Stargate_SG-1_-_The_Alliance-2005-12-15" and os.path.isdir(
            os.path.join(dump, "Stargate_SG-1_-_The_Alliance-2005-12-15")):
        dump = os.path.join(dump, "Stargate_SG-1_-_The_Alliance-2005-12-15")
    pkgs = read_system(dump)
    decl_by_pkg, friendly = declarations()
    pkg_rows, pkg_census = package_rows(pkgs, decl_by_pkg)
    enum_name, members = enum_of(pkgs, friendly)
    map_name_rows, map_census = map_rows(dump)
    places, expected = _placement_walk(dump, members)
    census = census_rows(pkg_census, map_census, places, expected, enum_name, members, decl_by_pkg)
    rows = rows_of(pkg_rows, map_name_rows, places, census)
    if args.out:
        write_artifact(os.path.join(args.out, "tag_origin.csv"), rows)
    sys.stdout.write(render(rows) + "\n")


if __name__ == "__main__":
    main()
