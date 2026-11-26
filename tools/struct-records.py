#!/usr/bin/env python3
"""Which record a `StructMember` names: the field a struct-typed receiver reads, counted.

A struct field's read is `36 <record> <object>` -- the record is a property whose **outer** is the struct, and the
emitter's table (`uscript-emit.py`'s `struct_props`) is keyed on the `(struct, member)` pair the source spells. That
key is not unique in one package: `Orientation` is declared inside both `SceneManager` and `MatObject` in `Engine.u`,
so `LookAtActorTag` has two records (4800 and 10624) and the table has to choose. A plain assignment kept the *last*
export and wrote 10624 in `SceneManager.ResolveActorsInActions`, where the package names 4800 -- the body was one of
`inventory/emit_differ.csv`'s rows for exactly that reason.

So the choice is counted rather than asserted. Every `StructMember` node in every walked body is read for the record
it names, the record's own `(struct, member)` key is rebuilt from the export table, and the node is classified:
**single** (the key holds one record -- no choice to make), **lowest** / **later** (the key holds several and the
package named the lowest index or a later one), or **no record**. Which side the record is on is counted too, because
an inherited struct is an import: 3,318 of the 5,128 nodes name a record this package does not export.

    python3 tools/struct-records.py --dump DUMP/System -o inventory
    python3 tools/struct-records.py --report
    python3 tools/struct-records.py --write-doc docs/COMPILER_FRONTEND.md
    python3 tools/struct-records.py --check          # synthetic, no dump, writes nothing

The artifact is one row per (verdict, record side, record kind, candidate count) with a node count: names, numbers and
counts only -- no package bytes, no source text. The duplicate rows carry the `struct.member` they are about, because
those are the nine nodes a table has to get right.
"""

import argparse
import collections
import csv
import importlib.util
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "inventory", "struct_field_records.csv")
FIELDS = ["population", "verdict", "record_side", "record_kind", "candidates", "field", "nodes", "examples"]
DOC_A = ("<!-- BEGIN struct-records -->", "<!-- END struct-records -->")

STRUCT = "struct field"
SINGLE = "single"
LOWEST = "lowest"
LATER = "later"
ABSENT = "no record"

_LOADED = {}


def _load(name, filename):
    """Import a sibling tool **once**; the walk asks for the disassembler per package, not per node."""
    if filename in _LOADED:
        return _LOADED[filename]
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _LOADED[filename] = mod
    return mod


def _dis():
    return _load("uscript_dis", "uscript-dis.py")


def classify(records, chosen):
    """`(verdict, candidates)` for one field read: the records its key holds, and which one the package named.

    `records` are the record indices under the `(struct, member)` key the read's own record belongs to. The key is
    rebuilt from the chosen record, so the chosen index is always a member of it; `no record` means the index names
    nothing in either table.
    """
    if not records:
        return ABSENT, 0
    if len(records) == 1:
        return SINGLE, 1
    return (LOWEST if chosen == min(records) else LATER), len(records)


def _norm_kind(record_kind):
    """`UNameProperty` -> `NameProperty`: an *import*'s class name carries the `U` and an export's does not."""
    k = record_kind or ""
    if k[:1] == "U" and k[1:2].isupper():
        return k[1:]
    return k


def _walk(nodes):
    for n in nodes:
        yield n
        for c in n.get("children") or []:
            yield from _walk([c])


def _owner_exports(pkg, names, ref):
    """The name of an export's `outer`, or `""` -- an import's owner is its `package` field (see `_import_owners`)."""
    if 0 < ref <= len(pkg.exports):
        return names.get(ref, "")
    return ""


def census(dump, only=None):
    """Walk every `StructMember` node and count `(verdict, side, kind, candidates)`."""
    dis = _dis()
    from ue2pkg import Package                                  # noqa: PLC0415 - only with a real dump

    table = dis.Table()
    counts = collections.Counter()
    examples = collections.defaultdict(list)
    tally = collections.Counter()
    for fn in sorted(os.listdir(dump)):
        if not fn.endswith(".u"):
            continue
        if only and fn.lower() not in only:
            continue
        pkg = Package(os.path.join(dump, fn))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        kinds = {i + 1: pkg.class_name_of(e) for i, e in enumerate(pkg.exports)}
        names = {i + 1: pkg.name(e["name"]).lower() for i, e in enumerate(pkg.exports)}
        import_owners = _import_owners(pkg)
        # The key the emitter's table uses, rebuilt from the records themselves: `(struct name, member)` -> the
        # records that answer it. An export's owner is its `outer`; an import's is the class its `package` field
        # names, because an import has no place in this package's export table.
        key = collections.defaultdict(list)
        for i, e in enumerate(pkg.exports):
            key[(_owner_exports(pkg, names, e["outer"]), names[i + 1])].append(i + 1)
        for j, im in enumerate(pkg.imports):
            key[(import_owners.get(-(j + 1), ""), str(pkg.name(im["name"])).lower())].append(-(j + 1))
        for ei, e in enumerate(pkg.exports):
            if kinds.get(ei + 1) != "Function" or e["outer"] <= 0:
                continue
            status, body, _s, _e, _ne, _front = dis.locate(e, pkg.data)
            if status != "ok":
                continue
            try:
                nodes = dis.parse(body, table)
            except dis.Refused:
                continue
            if sum(x["end"] - x["offset"] for x in nodes) != len(body):
                continue
            fname = "%s.%s" % (dis.owner_class(pkg.exports, kinds, names, e["outer"]), pkg.name(e["name"]))
            for node in _walk(nodes):
                if node["opcode"] != 0x36 or not node["operands"]:
                    continue
                idx, _q = dis.read_index(node["operands"], 0)
                if idx > 0 and idx - 1 < len(pkg.exports):
                    member = names.get(idx, "")
                    owner = _owner_exports(pkg, names, pkg.exports[idx - 1]["outer"])
                    side, kind = "export", kinds.get(idx, "")
                elif idx < 0 and -idx - 1 < len(pkg.imports):
                    member = str(pkg.name(pkg.imports[-idx - 1]["name"])).lower()
                    owner = import_owners.get(idx, "")
                    side, kind = "import", pkg.name(pkg.imports[-idx - 1]["class"])
                else:
                    member = owner = ""
                    side, kind = "?", ""
                verdict, cands = classify(key.get((owner, member), []), idx)
                kind = _norm_kind(kind)
                field = "%s.%s" % (owner, member) if verdict in (LOWEST, LATER) else ""
                k = (verdict, side, kind, str(cands), field)
                counts[k] += 1
                if fname not in examples[k] and len(examples[k]) < 3:
                    examples[k].append(fname)
                tally[verdict] += 1
                tally["nodes"] += 1
    rows = []
    for k, n in sorted(counts.items()):
        verdict, side, kind, cands, field = k
        rows.append({"population": STRUCT, "verdict": verdict, "record_side": side, "record_kind": kind,
                     "candidates": cands, "field": field, "nodes": str(n), "examples": " ".join(examples[k])})
    return rows, tally


def _import_owners(pkg):
    """{negative import index: the **class** the import's `package` field names} -- the join `struct_props` uses."""
    out = {}
    for j, im in enumerate(pkg.imports):
        ref = im.get("package", 0)
        ref = ref - 2 ** 32 if ref > 2 ** 31 else ref
        if ref < 0 and -ref <= len(pkg.imports):
            out[-(j + 1)] = str(pkg.name(pkg.imports[-ref - 1]["name"])).lower()
        elif ref > 0 and ref <= len(pkg.exports):
            out[-(j + 1)] = str(pkg.name(pkg.exports[ref - 1]["name"])).lower()
    return out


def read_artifact(path):
    rows, census_row = [], {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["population"] == "(census)":
                census_row = r
            else:
                rows.append(r)
    return rows, census_row


def totals(rows):
    """`{verdict: nodes}` and `{side: nodes}` -- what the page's sentence quotes."""
    by_verdict = collections.Counter()
    by_side = collections.Counter()
    for r in rows:
        by_verdict[r["verdict"]] += int(r["nodes"])
        by_side[r["record_side"]] += int(r["nodes"])
    return by_verdict, by_side


def render(rows, census=None):
    by_verdict, by_side = totals(rows)
    nodes = int(census["nodes"]) if census else sum(by_verdict.values())
    dup = by_verdict[LOWEST] + by_verdict[LATER]
    lines = []
    lines.append("**{:,} `StructMember` nodes over {} shapes** (`inventory/struct_field_records.csv`): **{:,}** name a field "
                 "whose `(struct, member)` key holds exactly one record, and **{}** name one of a key that holds "
                 "more -- **{}** the lowest index and **{}** a later one. **{:,}** of the {:,} are records this "
                 "package **imports** (the struct is declared in another package), so an inherited field read is an "
                 "import and no export table can answer it.".format(
                     nodes, len(rows), by_verdict[SINGLE], dup, by_verdict[LOWEST], by_verdict[LATER],
                     by_side["import"], nodes))
    if dup:
        lines.append("")
        lines.append("| struct.field | verdict | candidates | record kind | nodes | examples |")
        lines.append("|---|---|---|---|---|---|")
        for r in sorted(rows, key=lambda r: (r["field"], r["verdict"])):
            if r["verdict"] not in (LOWEST, LATER):
                continue
            lines.append("| `%s` | %s | %s | `%s` | %s | %s |"
                         % (r["field"], r["verdict"], r["candidates"], r["record_kind"], r["nodes"],
                            ", ".join("`%s`" % x for x in r["examples"].split())))
    return "\n".join(lines)


def splice(path, text):
    with open(path, encoding="utf-8") as fh:
        page = fh.read()
    a, b = DOC_A
    if a not in page or b not in page:
        raise SystemExit("FAIL struct-records: %s has no %s marker" % (path, a))
    head, rest = page.split(a, 1)
    _old, tail = rest.split(b, 1)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(head + a + "\n" + text + "\n" + b + tail)


def _self_test():
    """The four classifications, and a rendering that counts them: `--check` runs this, not a dump."""
    class Fake(list):
        pass
    assert classify([], 7) == (ABSENT, 0)
    assert classify([31], 31) == (SINGLE, 1)
    assert classify([4800, 10624], 4800) == (LOWEST, 2)
    assert classify([5974, 6597], 6597) == (LATER, 2)
    assert _norm_kind("UNameProperty") == "NameProperty"
    assert _norm_kind("NameProperty") == "NameProperty"
    assert _norm_kind("") == ""
    rows = [
        {"population": STRUCT, "verdict": SINGLE, "record_side": "export", "record_kind": "NameProperty",
         "candidates": "1", "field": "", "nodes": "3", "examples": "Engine.u.a"},
        {"population": STRUCT, "verdict": LOWEST, "record_side": "export", "record_kind": "NameProperty",
         "candidates": "2", "field": "orientation.lookatactortag", "nodes": "2", "examples": "Engine.u.i"},
        {"population": STRUCT, "verdict": LATER, "record_side": "export", "record_kind": "ClassProperty",
         "candidates": "2", "field": "driverweaponstruct.weaponclass", "nodes": "1", "examples": "Stargame.u.x"},
        {"population": STRUCT, "verdict": SINGLE, "record_side": "import", "record_kind": "IntProperty",
         "candidates": "1", "field": "", "nodes": "4", "examples": "Engine.u.b"},
    ]
    text = render(rows, {"nodes": "10"})
    for want in ("**10 `StructMember` nodes over 4 shapes**", "**7** name a field", "**3** name one of a key",
                 "**2** the lowest index and **1** a later one", "**4** of the 10 are records this package "
                 "**imports**", "| `orientation.lookatactortag` | lowest | 2 |"):
        if want not in text:
            print("FAIL struct-records: self-test render lacks %r" % want)
            return 1
    # a census row round-trips through the artifact: `--check` holds `--report` to the same totals
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "struct_field_records.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)
        w.writerow({"population": "(census)", "verdict": "", "record_side": "", "record_kind": "",
                    "candidates": "", "field": "", "nodes": "10", "examples": "census"})
    back, cen = read_artifact(path)
    if sum(int(r["nodes"]) for r in back) != int(cen["nodes"]):
        print("FAIL struct-records: the rows do not add up to the census row")
        return 1
    if render(back, cen) != text:
        print("FAIL struct-records: the artifact's rows do not render the same block")
        return 1
    print("ok struct-records: 4 classifications, %d synthetic rows, the census row agrees" % len(back))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dump", help="the dump's System directory")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--only", metavar="PKG", help="count only these packages (comma separated); a debug aid -- the "
                                                  "artifact it writes is partial and must not be committed")
    ap.add_argument("--report", action="store_true", help="print the page's block from the committed artifact")
    ap.add_argument("--write-doc", metavar="PATH", help="splice the block into a page between its markers")
    ap.add_argument("--check-doc", metavar="PATH", help="fail if the page's block is stale (in `make ci`)")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        return _self_test()
    if args.report or args.write_doc or args.check_doc:
        rows, cen = read_artifact(OUT)
        text = render(rows, cen)
        if args.report:
            print(text)
            return 0
        path = args.write_doc or args.check_doc
        if sum(int(r["nodes"]) for r in rows) != int(cen["nodes"]):
            print("FAIL struct-records: the rows do not add up to the census row (%d vs %s)"
                  % (sum(int(r["nodes"]) for r in rows), cen["nodes"]))
            return 1
        if args.write_doc:
            splice(args.write_doc, text)
            print("wrote %s" % args.write_doc)
            return 0
        with open(path, encoding="utf-8") as fh:
            page = fh.read()
        a, b = DOC_A
        if a not in page or b not in page:
            print("FAIL struct-records: %s has no %s marker" % (path, a))
            return 1
        if page.split(a, 1)[1].split(b, 1)[0].strip() != text.strip():
            print("FAIL struct-records: %s's block is stale -- run `make struct-records`" % path)
            return 1
        print("ok struct-records: %s's block is current" % path)
        return 0
    if not args.dump:
        print("FAIL struct-records: --dump is required (the dump stays outside the repo)")
        return 2
    only = {p.strip().lower() for p in args.only.split(",") if p.strip()} if args.only else None
    rows, tally = census(args.dump, only)
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "struct_field_records.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)
        w.writerow({"population": "(census)", "verdict": "", "record_side": "", "record_kind": "",
                    "candidates": "", "field": "", "nodes": str(tally["nodes"]), "examples": "census"})
    print("wrote %s (%d rows, %d nodes: %d single, %d lowest, %d later, %d absent)"
          % (path, len(rows), tally["nodes"], tally[SINGLE], tally[LOWEST], tally[LATER], tally[ABSENT]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
