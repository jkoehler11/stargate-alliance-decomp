#!/usr/bin/env python3
"""The `Context` size byte for a **struct member**: the fields' sum, or that sum aligned to 4.

`tools/uscript-emit.py` writes a `Context`'s size byte from `STRUCT_SIZE`, whose entries are *field sums*:
`MouseCursor` is `texture, int, int, byte` = 13. Nine UWindow bodies in `inventory/emit_differ.csv` read
`Cursor` / `NormalCursor` (a `MouseCursor`) and the package writes **16**, so the byte is the compiler's *stack
slot* -- the sum rounded up to 4 -- and the table carried the fields' sum where the build writes the slot.

That is a claim about the corpus, so it is counted rather than asserted. For every `Context` (`19`) /
`ClassContext` (`12`) node in every walked body this records the member's record kind and declared word, the size
byte, and a verdict; a `Context` that a **subscript** consumes is split out as its own population, because its byte
is the *array's* own size (`dimension x element`, measured by `tools/array-read-width.py`) and not a struct's.

    python3 tools/context-size.py --dump DUMP/System --tree $WORK/scripts-all -o inventory
    python3 tools/context-size.py --report
    python3 tools/context-size.py --write-doc docs/COMPILER_FRONTEND.md
    python3 tools/context-size.py --check          # synthetic, no dump, writes nothing

The artifact is one row per `(population, record kind, declared word, dimension, size byte, verdict)` with a node
count: names, numbers and counts only -- no package bytes, no source text.
"""

import argparse
import collections
import csv
import importlib.util
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "inventory", "context_size.csv")
FIELDS = ["population", "record_kind", "declared", "dim", "size_byte", "rule", "nodes", "examples"]
DOC_A = ("<!-- BEGIN context-size -->", "<!-- END context-size -->")

POP_STRUCT = "struct member"
POP_ARRAY = "array read"
POP_OTHER = "other member"


_LOADED = {}


def _load(name, filename):
    """Import a sibling tool **once**: `struct_rule`/`array_rule` ask for one per node, and re-executing a 6,000-line
    module per `Context` node made the walk pay for it on every row. It is also what lets the self-test drive a rule
    against a synthetic table entry -- a fresh module per call saw a different dict each time."""
    if filename in _LOADED:
        return _LOADED[filename]
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _LOADED[filename] = mod
    return mod


def _emitter():
    return _load("uscript_emit", "uscript-emit.py")


def _dis():
    return _load("uscript_dis", "uscript-dis.py")


def _array_tool():
    return _load("array_read_width", "array-read-width.py")


def aligned4(n):
    return (n + 3) // 4 * 4


def struct_rule(declared, size):
    """`table` when the byte is the emitter's own entry, `aligned` when it is that entry rounded up to 4.

    The vocabulary follows what the table is *for*: `STRUCT_SIZE` carried the fields' sum (`MouseCursor` 13) until
    the corpus showed every node writing the compiler's slot (**16**), and it now carries the slot -- so a node that
    writes 16 agrees with the table and is not "a field sum". `aligned` stays because a table entry that is still a
    sum is read that way, and a struct absent from the table is not a failure: it is a width the corpus supplies and
    the emitter has never been told, reported as its own population so the table can be extended from a measurement.

    **`not joined` is not `not in table`.** A blank declared word means this census's own join missed -- the record
    kind and the owner did not reach a `(class, property)` row in `class_properties.csv` -- so nothing is *known*
    about the struct and no width can be offered. Folding the two together read 2,017 struct members as "a struct the
    table does not carry, of which 0 have one agreed width", which is a claim about the table made out of a join that
    never answered.
    """
    if not declared:
        return "not joined"
    unit = _emitter().STRUCT_SIZE.get((declared or "").lower())
    if unit is None:
        return "not in table"
    if size is None:
        return "unexplained"
    if size == unit:
        return "table"
    if unit < size <= aligned4(unit) and size == aligned4(unit):
        return "aligned"
    return "unexplained"


def array_rule(record_kind, declared, dim, size):
    """`array size` when the byte is `dimension x element`, `element size` when it is the element alone.

    The element rule needs no dimension: `TransportRings` declares `MyRings[MAX_RINGS]`, a `const` dimension this
    census cannot resolve, and its node still measures the element.
    """
    unit = _array_tool().element_unit(_norm_kind(record_kind), declared)
    if unit is None:
        return "not joined"
    if size is None:
        return "unexplained"
    if size == unit:
        return "element size"
    if isinstance(dim, int) and dim > 0:
        return "array size" if size == dim * unit else "unexplained"
    return "not joined"


def _norm_kind(record_kind):
    """`UObjectProperty` -> `ObjectProperty`: an *import*'s class name carries the `U` and an export's does not."""
    emit = _emitter()
    k = record_kind or ""
    if k[:1] == "U" and k[1:] in emit.PROP_TYPE:
        return k[1:]
    return k


def _walk(nodes, parent=None):
    for n in nodes:
        yield n, parent
        for c in n.get("children") or []:
            for x in _walk([c], n):
                yield x


def _read_member(pkg, read):
    """`(record kind, property name, owner class)` for an `InstanceVariable`/`DefaultVariable`."""
    dis = _dis()
    if read["opcode"] not in (0x01, 0x02) or not read["operands"]:
        return "", "", ""
    idx, _q = dis.read_index(read["operands"], 0)
    if idx > 0 and idx - 1 < len(pkg.exports):
        prop = pkg.exports[idx - 1]
        owner = ""
        if prop["outer"] > 0 and prop["outer"] - 1 < len(pkg.exports):
            owner = pkg.name(pkg.exports[prop["outer"] - 1]["name"]).lower()
        return pkg.class_name_of(prop), pkg.name(prop["name"]).lower(), owner
    if idx < 0 and -idx - 1 < len(pkg.imports):
        imp = pkg.imports[-idx - 1]
        return pkg.name(imp["class"]), pkg.name(imp["name"]).lower(), ""
    return "", "", ""


def census(dump, tree, only=None):
    """Walk every `Context` node and count `(population, kind, declared, dim, size, verdict)`."""
    dis = _dis()
    emit = _emitter()
    arr = _array_tool()
    from ue2pkg import Package                                  # noqa: PLC0415 - only with a real dump

    props = {}
    with open(os.path.join(ROOT, "inventory", "class_properties.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            props[(r["class"].lower(), r["property"].lower())] = (r["kind"], r["friendly_type"])
    dims = {}
    if tree:
        # a **starred** tail: the emitter's index grows an element per corpus-wide table it learns (the iterators
        # are the most recent), and an exact-length unpack turns that into an exception in this census, not a
        # failure of its own rule.
        _idx, _files, _params, _enums, _enames, _rets, _lt, _pt, _w, dims_all, *_rest = \
            emit._source_index(tree)
        for (cls, nm), sizes in dims_all.items():
            if len(sizes) == 1:
                dims[(cls, nm)] = next(iter(sizes))
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
        names = {i + 1: pkg.name(e["name"]) for i, e in enumerate(pkg.exports)}
        import_owners = emit._class_tables(pkg, kinds, names)[4]
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
            cls = dis.owner_class(pkg.exports, kinds, names, e["outer"]).lower()
            fname = "%s.%s" % (dis.owner_class(pkg.exports, kinds, names, e["outer"]), pkg.name(e["name"]))
            for node, parent in _walk(nodes):
                if node["opcode"] not in (0x19, 0x12) or len(node["children"]) != 2:
                    continue
                if len(node["operands"]) < 3:
                    continue
                size = node["operands"][2]
                member = node["children"][1]
                record_kind, name, owner = _read_member(pkg, member)
                declared = ""
                if name:
                    # a property **import** carries no owner in the export table, so the join is: the record's own
                    # outer, then the body's class -- the same fallback `tools/array-read-width.py` measures with.
                    row = props.get((owner, name)) if owner else None
                    if row is None:
                        row = props.get((cls, name))
                    if row is None:
                        row = props.get((import_owners.get(name) or "", name))
                    declared = row[1] if row else ""
                dim = None
                if parent is not None and parent["opcode"] in (0x10, 0x1A):
                    # a **subscript** consumes this read, so its byte is the array's own size
                    population = POP_ARRAY
                    if owner:
                        declared = arr._element_word(declared)
                        dim = dims.get((owner, name))
                    rule = array_rule(record_kind, declared, dim, size)
                elif "structproperty" in (record_kind or "").lower() or \
                        (declared or "").lower() in emit.STRUCT_SIZE:
                    population = POP_STRUCT
                    rule = struct_rule(declared, size)
                else:
                    population = POP_OTHER
                    rule = "not tested"
                key = (population, record_kind, (declared or "").lower(),
                       "" if dim is None else str(dim), str(size), rule)
                counts[key] += 1
                if fname not in examples[key] and len(examples[key]) < 3:
                    examples[key].append(fname)
                tally[rule] += 1
    rows = []
    for key, n in sorted(counts.items()):
        population, record_kind, declared, dim, size, rule = key
        rows.append({"population": population, "record_kind": record_kind, "declared": declared, "dim": dim,
                     "size_byte": size, "rule": rule, "nodes": str(n), "examples": " ".join(examples[key])})
    return rows, tally


def read_artifact(path):
    rows, census_row = [], {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["population"] == "(census)":
                census_row = r
            else:
                rows.append(r)
    return rows, census_row


def measured_widths(rows):
    """`[(declared, size, nodes)]` for the structs the emitter's table does not carry, where every node agrees.

    A struct whose nodes disagree is *not* a width: it is either two structs of one name or a shape this census has
    mis-joined, and it is printed as a disagreement rather than offered to the table.
    """
    seen = collections.defaultdict(set)
    nodes = collections.Counter()
    for r in rows:
        if r["population"] != POP_STRUCT or r["rule"] != "not in table":
            continue
        seen[r["declared"]].add(r["size_byte"])
        nodes[r["declared"]] += int(r["nodes"])
    out = []
    for declared, sizes in sorted(seen.items()):
        if len(sizes) == 1 and declared:
            out.append((declared, next(iter(sizes)), nodes[declared]))
    return out


def disputed_widths(rows):
    """The structs whose nodes do *not* agree on one byte -- reported, and never offered to the table."""
    seen = collections.defaultdict(set)
    for r in rows:
        if r["population"] == POP_STRUCT and r["rule"] == "not in table":
            seen[r["declared"]].add(r["size_byte"])
    return sorted(d for d, sizes in seen.items() if len(sizes) > 1)


def render(rows, census=None):
    """The page's block: the struct population in full, one line for the array read, one for the measured widths."""
    def total(pop, rule=None):
        return sum(int(r["nodes"]) for r in rows
                   if r["population"] == pop and (rule is None or r["rule"] == rule))

    def shapes(pop, rule=None):
        return sum(1 for r in rows if r["population"] == pop and (rule is None or r["rule"] == rule))

    widths = measured_widths(rows)
    disputed = disputed_widths(rows)
    hot = [r for r in rows if r["rule"] == "unexplained"
           and r["population"] in (POP_STRUCT, POP_ARRAY)]
    lines = []
    lines.append("**{:,} `Context` nodes over {} shapes** (`inventory/context_size.csv`). A **struct** member's "
                 "byte is the compiler's stack slot, and `STRUCT_SIZE` carries that slot: **{:,}** nodes over {} "
                 "shapes write the table's own entry -- `MouseCursor` is **16** where its four fields (`texture, "
                 "int, int, byte`) sum to 13 -- **{:,}** write an entry that is still a fields' sum rounded up to 4, "
                 "**{:,}** a struct the table does not carry, of which **{}** have one agreed width the table can "
                 "take, and **{:,}** a member whose **declared word the join does not name** (`not joined` -- no "
                 "width is claimed for those). A **subscripted** read's byte is the array's own size, not a "
                 "struct's: **{:,}** nodes over "
                 "{} shapes write `dimension x element` and **{:,}** the element alone. **{}**".format(
                     sum(int(r["nodes"]) for r in rows), len(rows),
                     total(POP_STRUCT, "table"), shapes(POP_STRUCT, "table"),
                     total(POP_STRUCT, "aligned"), total(POP_STRUCT, "not in table"), len(widths),
                     total(POP_STRUCT, "not joined"),
                     total(POP_ARRAY, "array size"), shapes(POP_ARRAY, "array size"),
                     total(POP_ARRAY, "element size"),
                     ("%d node(s) contradict the rule, named below." % len(hot)) if hot
                     else "No node contradicts it."))
    lines.append("")
    lines.append("| member | declared | dim | size byte | rule | nodes | examples |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in sorted([r for r in rows if r["population"] in (POP_STRUCT, POP_ARRAY)],
                    key=lambda r: (-int(r["nodes"]), r["population"], r["declared"] or "", r["size_byte"])):
        seen = []
        for e in (r["examples"] or "").split():
            if e not in seen:
                seen.append(e)
        lines.append("| %s `%s` | %s | %s | %s | %s | %s | %s |" % (
            r["population"], r["record_kind"] or "?", r["declared"] or "-", r["dim"] or "-", r["size_byte"],
            r["rule"], r["nodes"], ", ".join("`%s`" % e for e in seen) or "-"))
    lines.append("")
    lines.append("The widths the corpus supplies and `STRUCT_SIZE` does not carry, each agreed by every node that "
                 "reads it: %s.%s" % (
                     ", ".join("`%s` %s (%d nodes)" % (d, s, n) for d, s, n in widths) or "none",
                     (" **Disputed** (two structs of one name, or a shape this census mis-joined): %s."
                      % ", ".join("`%s`" % d for d in disputed)) if disputed else ""))
    return "\n".join(lines)


def splice_doc(path, block, markers):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    i, j = text.find(markers[0]), text.find(markers[1])
    if i < 0 or j < 0:
        return None
    new = text[:i] + markers[0] + "\n" + block + "\n" + markers[1] + text[j + len(markers[1]):]
    if new != text:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new)
    return new


def self_check():
    """Synthetic: both struct verdicts, the measured-width rule, and the render's round trip."""
    bad = []
    if struct_rule("mousecursor", 16) != "table":
        bad.append("MouseCursor's own slot (16) is not read as the table's entry")
    if struct_rule("mousecursor", 13) != "unexplained":
        bad.append("the fields' sum (13) reads as the table's width")
    if struct_rule("vector", 12) != "table":
        bad.append("vector's 12 is no longer the table's entry")
    if struct_rule("", 12) != "not joined":
        bad.append("a member with no declared word reads as a struct the table does not carry")
    if struct_rule("testslot", 12) != "not in table":
        bad.append("a declared struct the table does not carry is not reported as such")
    # `aligned` is the rule for a table entry that is a fields' sum rather than a slot -- no shipped entry is one
    # any more, so the branch is driven by a synthetic entry and the table is restored afterwards.
    _tbl = _emitter().STRUCT_SIZE
    _tbl["testslot"] = 13
    try:
        if struct_rule("testslot", 16) != "aligned":
            bad.append("a table entry that is a fields' sum is not read as aligned")
        if struct_rule("testslot", 13) != "table":
            bad.append("a table entry that is a fields' sum does not read as the table's own width")
    finally:
        del _tbl["testslot"]
    if struct_rule("htmlstyle", 31) != "unexplained":
        bad.append("a width neither the sum nor its alignment is read as a rule")
    if struct_rule("interpcurvepoint", 8) != "not in table":
        bad.append("a struct the table does not carry is not reported as one")
    if aligned4(13) != 16 or aligned4(9) != 12:
        bad.append("the aligner is wrong")
    rows = [{"population": POP_STRUCT, "record_kind": "StructProperty", "declared": "mousecursor", "dim": "",
             "size_byte": "16", "rule": "aligned", "nodes": "2", "examples": "A.B"},
            {"population": POP_STRUCT, "record_kind": "StructProperty", "declared": "interpcurve", "dim": "",
             "size_byte": "112", "rule": "not in table", "nodes": "3", "examples": "C.D"},
            {"population": POP_ARRAY, "record_kind": "StructProperty", "declared": "vector", "dim": "16",
             "size_byte": "192", "rule": "array size", "nodes": "1", "examples": "StoryModeGame.AddSquadFromSpawner"}]
    block = render(rows, {"nodes": "6"})
    if "**6 `Context` nodes" not in block:
        bad.append("the render does not carry the node total it was handed")
    if "| struct member `StructProperty` | mousecursor | - | 16 | aligned | 2 |" not in block:
        bad.append("the render does not carry the struct row")
    if "`interpcurve` 112 (3 nodes)" not in block:
        bad.append("the render does not name a width the table can take")
    if measured_widths(rows) != [("interpcurve", "112", 3)]:
        bad.append("measured_widths does not answer the agreed width")
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "context_size.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
            w.writeheader()
            w.writerow({"population": "(census)", "nodes": "6", "examples": "census"})
            for r in rows:
                w.writerow(r)
        back, _census = read_artifact(path)
        if render(back, _census) != render(rows, {"nodes": "6"}):
            bad.append("the render does not reproduce from the written artifact")
    finally:
        for name in os.listdir(tmp):
            os.unlink(os.path.join(tmp, name))
        os.rmdir(tmp)
    for line in bad:
        print("FAIL context-size: %s" % line)
    if not bad:
        print("ok   context-size: the table's own width (and a fields' sum entry read as aligned), the array read "
              "split, the measured widths, the render and its round trip")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump")
    ap.add_argument("--tree")
    ap.add_argument("-o", "--out-dir")
    ap.add_argument("--only")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.check_doc:
        rows, census_row = read_artifact(OUT)
        if not rows:
            print("FAIL context-size: %s is missing" % OUT)
            return 1
        block = render(rows, census_row)
        with open(args.check_doc, encoding="utf-8") as fh:
            text = fh.read()
        i, j = text.find(DOC_A[0]), text.find(DOC_A[1])
        if i < 0 or j < 0:
            print("FAIL context-size: %s has no %s block" % (args.check_doc, DOC_A[0]))
            return 1
        if text[i:j + len(DOC_A[1])] != DOC_A[0] + "\n" + block + "\n" + DOC_A[1]:
            print("FAIL context-size: %s's block does not reproduce from the committed CSV" % args.check_doc)
            return 1
        print("ok   context-size: %s reproduces from the committed CSV" % args.check_doc)
        return 0
    if args.report or args.write_doc:
        rows, census_row = read_artifact(OUT)
        if not rows:
            print("FAIL context-size: %s is missing" % OUT)
            return 1
        block = render(rows, census_row)
        if args.report:
            print(block)
            return 0
        if splice_doc(args.write_doc, block, DOC_A) is None:
            print("FAIL context-size: %s has no %s markers" % (args.write_doc, DOC_A[0]))
            return 1
        print("wrote %s (%s)" % (args.write_doc, DOC_A[0]))
        return 0
    if not args.dump:
        ap.error("--dump is required to measure (or use --report/--check-doc/--check)")
    only = {p.strip().lower() for p in (args.only or "").split(",") if p.strip()} or None
    if only:
        only = {p if p.endswith(".u") else p + ".u" for p in only}
    rows, tally = census(args.dump, args.tree, only)
    out_dir = args.out_dir or os.path.join(ROOT, "inventory")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "context_size.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerow({"population": "(census)", "nodes": str(sum(tally.values())), "examples": "census"})
        for r in rows:
            w.writerow(r)
    print("%d nodes over %d shapes" % (sum(tally.values()), len(rows)))
    for r in rows:
        if r["population"] == POP_OTHER:
            continue
        print("  %-14s %-16s %-18s dim=%-4s size=%-3s %-14s %5s  %s" % (
            r["population"], r["record_kind"], r["declared"] or "-", r["dim"] or "-", r["size_byte"], r["rule"],
            r["nodes"], r["examples"]))
    print("verdicts: %s" % dict(tally))
    print("widths the table does not carry: %s" % measured_widths(rows))
    print("wrote %s" % os.path.relpath(path, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
