#!/usr/bin/env python3
"""The `Context` size byte an array read writes when a subscript consumes it.

`tools/uscript-emit.py` writes a **member** read's value size from `PROP_SIZE`, and an *array* member's size as 0
(`PROP_SIZE["array"]`) -- measured on `.Length`, where the length operator consumes the array itself. A read that a
subscript consumes is a different node: `StargatePawn.EndCrouch`'s
`StargateWeapon(Weapon).RegularMovementAnims[1]` ships `19 2e 09 01 a5 05 00 08 01 63 21` -- size **8** -- and the
emitter wrote 4, one byte at offset 173 of a 354-byte body. Three bodies in the residue pinned the same shape with
three different widths (`PlayerController.ClientUpdateFlagHolder` 8, `NavigationPoint.SetBaseVisibility` 2), so the
width is not a constant and this is a census, not a patch.

    python3 tools/array-read-width.py --dump DUMP/System --tree $WORK/scripts-all -o inventory
    python3 tools/array-read-width.py --report
    python3 tools/array-read-width.py --write-doc docs/COMPILER_FRONTEND.md
    python3 tools/array-read-width.py --check          # synthetic, no dump, writes nothing

For every `10` (`DynamicArrayElement`) / `1A` (`ArrayElement`) node in every walked body it records the form of the
array read (`19`/`12` Context, or a bare `01` when the read is on self), the array property's record, its declared
element word and dimension, and the size byte. The artifact is one row per `(record kind, declared word, dimension,
read form, size byte)` with a node count, so `--check` re-derives the census from the rows. No package bytes and no
source text are written -- names, numbers and counts only.
"""

import argparse
import collections
import csv
import importlib.util
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "inventory", "array_read_width.csv")
FIELDS = ["record_kind", "declared", "dim", "read_form", "size_byte", "rule", "nodes", "examples"]
DOC_A = ("<!-- BEGIN array-read-width -->", "<!-- END array-read-width -->")

# The forms an array read takes, in the order the render lists them.
FORMS = ["context", "class-context", "bare", "other"]


def _emitter():
    spec = importlib.util.spec_from_file_location("uscript_emit", os.path.join(ROOT, "tools", "uscript-emit.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dis():
    spec = importlib.util.spec_from_file_location("uscript_dis", os.path.join(ROOT, "tools", "uscript-dis.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rule_of(record_kind, declared, dim, size):
    """What a node's size byte *is*, as one of four verdicts -- the whole question this census asks.

    `static` is the claim under test: a static array's read writes its own byte size, `dimension x element`. `dynamic`
    is a `UArrayProperty`, whose own size is 0 (there is no fixed block). Anything else is a row the rule does not
    explain and is named (`unexplained`) rather than averaged in.
    """
    if record_kind == "ArrayProperty":
        return "dynamic" if size in (0, None) else "unexplained"
    if size is None:
        return "no-context" if record_kind else "untyped"
    return "static" if size == expected_size(declared, dim, record_kind) else "unexplained"


def expected_size(declared, dim, record_kind, prop_types=None, struct_size=None):
    """`dimension x element`, with the element's width from the emitter's own `PROP_SIZE`/`STRUCT_SIZE` tables."""
    emit = _emitter()
    prop_types = prop_types or emit.PROP_TYPE
    struct_size = struct_size or emit.STRUCT_SIZE
    word = (declared or "").lower()
    if record_kind == "StructProperty":
        unit = struct_size.get(word)
    else:
        unit = emit.PROP_SIZE.get(prop_types.get(record_kind, word) or word)
        if unit is None:
            unit = emit.PROP_SIZE.get(word)
    if unit is None or not dim:
        return None
    return unit * dim


def _element_word(friendly):
    """`array<name>` -> `name`; a static array's word is the record's own declared type."""
    f = (friendly or "").strip()
    if f.lower().startswith("array<") and f.endswith(">"):
        return f[6:-1].strip().lower()
    return f.lower()


def census(dump, tree, only=None):
    """Walk every body's element nodes and count `(record kind, declared, dim, form, size)`."""
    dis = _dis()
    emit = _emitter()
    from ue2pkg import Package                                  # noqa: PLC0415 - only with a real dump

    dims = {}
    if tree:
        _index, _files, _params, _enums, _enames, _rets, _lt, _pt, _w, dims_all, _c, _sp, _cr, _ltf, _crf, _mods = \
            emit._source_index(tree)
        for (cls, nm), sizes in dims_all.items():
            if len(sizes) == 1:
                dims[(cls, nm)] = next(iter(sizes))
    props = {}
    with open(os.path.join(ROOT, "inventory", "class_properties.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            props[(r["class"].lower(), r["property"].lower())] = (r["kind"], r["friendly_type"])
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
            for node in _walk(nodes):
                if node["opcode"] not in (0x10, 0x1A) or len(node["children"]) != 2:
                    continue
                tally["nodes"] += 1
                read = node["children"][1]
                form, size, member = "bare", None, read
                if read["opcode"] in (0x19, 0x12) and len(read["children"]) == 2:
                    form = "context" if read["opcode"] == 0x19 else "class-context"
                    size = read["operands"][2] if len(read["operands"]) >= 3 else None
                    member = read["children"][1]
                owner = declared = record_kind = ""
                if member["opcode"] == 0x01 and member["operands"]:
                    idx, _q = dis.read_index(member["operands"], 0)
                    if idx > 0 and idx - 1 < len(pkg.exports):
                        prop = pkg.exports[idx - 1]
                        prop_name = pkg.name(prop["name"])
                        record_kind = pkg.class_name_of(prop)
                        if prop["outer"] > 0 and prop["outer"] - 1 < len(pkg.exports):
                            owner = pkg.name(pkg.exports[prop["outer"] - 1]["name"]).lower()
                        row = props.get((owner, prop_name.lower()))
                        declared = row[1] if row else ""
                    elif idx < 0 and -idx - 1 < len(pkg.imports):
                        imp = pkg.imports[-idx - 1]
                        prop_name = pkg.name(imp["name"])
                        record_kind = pkg.name(imp["class"])
                        row = props.get((cls, prop_name.lower()))
                        declared = row[1] if row else ""
                    else:
                        prop_name = "?"
                elif member["opcode"] in (0x10, 0x1A):
                    prop_name = "(nested element)"
                    form = "other"
                elif member["opcode"] == 0x1C or member["opcode"] == 0x1B:
                    prop_name = "(call)"
                    form = "other"
                else:
                    prop_name = "(read opcode %#04x)" % member["opcode"]
                    form = "other"
                word = _element_word(declared)
                dim = dims.get((owner, prop_name.lower())) if owner else None
                verdict = rule_of(record_kind, word, dim, size)
                key = (record_kind, word, str(dim if dim is not None else ""), form,
                       "" if size is None else str(size))
                counts[key] += 1
                if len(examples[key]) < 3:
                    fname = pkg.name(e["name"])
                    examples[key].append("%s.%s" % (dis.owner_class(pkg.exports, kinds, names, e["outer"]), fname))
                tally[verdict] += 1
    rows = []
    for key, n in sorted(counts.items()):
        record_kind, word, dim, form, size = key
        rows.append({"record_kind": record_kind, "declared": word, "dim": dim, "read_form": form,
                     "size_byte": size, "rule": rule_of(record_kind, word, int(dim) if dim else 0,
                                                        int(size) if size else None),
                     "nodes": str(n), "examples": " ".join(examples[key])})
    return rows, tally


def _walk(nodes):
    for n in nodes:
        yield n
        for c in n.get("children") or []:
            for x in _walk([c]):
                yield x


def read_artifact(path):
    rows, census_row = [], {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["record_kind"] == "(census)":
                census_row = r
            else:
                rows.append(r)
    return rows, census_row


def render(rows, tally):
    total = sum(int(r["nodes"]) for r in rows)
    static = sum(int(r["nodes"]) for r in rows if r["rule"] == "static")
    dynamic = sum(int(r["nodes"]) for r in rows if r["rule"] == "dynamic")
    hot = [r for r in rows if r["rule"] == "unexplained"]
    lines = []
    lines.append("**{:,} array-element nodes over {} shapes** (`inventory/array_read_width.csv`): **{:,}** write "
                 "`dimension x element` for a static array and **{:,}** write 0 for a `UArrayProperty`; "
                 "**{:,}** are the rows the rule does not explain, listed below by name.".format(
                     total, len(rows), static, dynamic, sum(int(r["nodes"]) for r in hot)))
    lines.append("")
    lines.append("| record kind | declared | dim | read form | size byte | rule | nodes | examples |")
    lines.append("|---|---|---|---|---|---|---|---|")
    order = {"static": 0, "dynamic": 1, "no-context": 2, "untyped": 3, "unexplained": 4}
    for r in sorted(rows, key=lambda r: (order.get(r["rule"], 9), -int(r["nodes"]), r["record_kind"])):
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["record_kind"] or "-", r["declared"] or "-", r["dim"] or "-", r["read_form"],
            r["size_byte"] or "-", r["rule"], r["nodes"],
            ", ".join("`%s`" % e for e in (r["examples"] or "").split()[:2]) or "-"))
    if hot:
        lines.append("")
        lines.append("A row the rule does not explain is named, never folded in: " + "; ".join(
            "`%s %s[%s]` writes %s (%s nodes)" % (r["record_kind"], r["declared"] or "?", r["dim"] or "?",
                                                  r["size_byte"] or "nothing", r["nodes"])
            for r in hot[:6]) + ".")
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
    """Synthetic: the four verdicts, the expected sizes, the render and its round trip."""
    bad = []
    cases = [("ByteProperty", "byte", 2, 2, "static"),
             ("NameProperty", "name", 2, 8, "static"),
             ("ObjectProperty", "object", 2, 8, "static"),
             ("StructProperty", "vector", 24, 288, "static"),
             ("ArrayProperty", "array<name>", 0, 0, "dynamic"),
             ("ArrayProperty", "array<name>", 0, 8, "unexplained"),
             ("NameProperty", "name", 7, 8, "unexplained"),
             ("ByteProperty", "byte", 2, 1, "unexplained"),
             ("ByteProperty", "byte", 2, None, "no-context")]
    for kind, word, dim, size, want in cases:
        got = rule_of(kind, word, dim, size)
        if got != want:
            bad.append("rule_of(%s, %s, %s, %s) = %s, wanted %s" % (kind, word, dim, size, got, want))
    # the expected size is the emitter's own table, not a second copy of it
    emit = _emitter()
    if expected_size("name", 2, "NameProperty") != 2 * emit.PROP_SIZE["name"]:
        bad.append("expected_size does not read PROP_SIZE for a name")
    if expected_size("vector", 24, "StructProperty") != 24 * emit.STRUCT_SIZE["vector"]:
        bad.append("expected_size does not read STRUCT_SIZE for a vector")
    if expected_size("name", 0, "NameProperty") is not None:
        bad.append("expected_size invented a width for a dimension the text does not spell")
    rows = [{"record_kind": "NameProperty", "declared": "name", "dim": "2", "read_form": "context",
             "size_byte": "8", "rule": "static", "nodes": "3", "examples": "Probe.A Probe.B"},
            {"record_kind": "ArrayProperty", "declared": "name", "dim": "", "read_form": "context",
             "size_byte": "0", "rule": "dynamic", "nodes": "1", "examples": "Probe.C"}]
    block = render(rows, {})
    if "**4 array-element nodes over 2 shapes**" not in block:
        bad.append("the render does not carry the node total it was handed")
    if "| NameProperty | name | 2 | context | 8 | static | 3 |" not in block:
        bad.append("the render does not carry the static row")
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "array_read_width.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
            w.writeheader()
            w.writerow({"record_kind": "(census)", "nodes": "4", "rule": "modelled"})
            for r in rows:
                w.writerow(r)
        back, _census = read_artifact(path)
        if render(back, {}) != block:
            bad.append("the render does not reproduce from the written artifact")
    finally:
        for name in os.listdir(tmp):
            os.unlink(os.path.join(tmp, name))
        os.rmdir(tmp)
    for line in bad:
        print("FAIL array-read-width: %s" % line)
    if not bad:
        print("ok   array-read-width: the four verdicts, the size tables it reads, the render and its round trip")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", help="the dump's System directory")
    ap.add_argument("--tree", help="the extracted source corpus ($WORK/scripts-all), for declared dimensions")
    ap.add_argument("-o", "--out-dir")
    ap.add_argument("--only", metavar="PKG", help="score only these packages (debug aid)")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.check_doc:
        rows, _census = read_artifact(OUT)
        if not rows:
            print("FAIL array-read-width: %s is missing" % OUT)
            return 1
        block = render(rows, {})
        with open(args.check_doc, encoding="utf-8") as fh:
            text = fh.read()
        i, j = text.find(DOC_A[0]), text.find(DOC_A[1])
        if i < 0 or j < 0:
            print("FAIL array-read-width: %s has no %s block" % (args.check_doc, DOC_A[0]))
            return 1
        if text[i:j + len(DOC_A[1])] != DOC_A[0] + "\n" + block + "\n" + DOC_A[1]:
            print("FAIL array-read-width: %s's block does not reproduce from the committed CSV" % args.check_doc)
            return 1
        print("ok   array-read-width: %s reproduces from the committed CSV" % args.check_doc)
        return 0
    if args.report or args.write_doc:
        rows, _census = read_artifact(OUT)
        if not rows:
            print("FAIL array-read-width: %s is missing" % OUT)
            return 1
        block = render(rows, {})
        if args.report:
            print(block)
            return 0
        if splice_doc(args.write_doc, block, DOC_A) is None:
            print("FAIL array-read-width: %s has no %s markers" % (args.write_doc, DOC_A[0]))
            return 1
        print("wrote %s (%s)" % (args.write_doc, DOC_A[0]))
        return 0
    if not args.dump:
        ap.error("--dump is required to measure (or use --report/--check-doc/--check)")
    only = {p.strip().lower() for p in args.only.split(",")} if args.only else None
    rows, tally = census(args.dump, args.tree, only)
    out_dir = args.out_dir or os.path.join(ROOT, "inventory")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "array_read_width.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerow({"record_kind": "(census)", "nodes": str(sum(int(r["nodes"]) for r in rows)),
                    "rule": "%d static, %d dynamic, %d unexplained" % (
                        tally["static"], tally["dynamic"], tally["unexplained"] + tally["untyped"]
                        + tally["no-context"])})
        for r in rows:
            w.writerow(r)
    print("walked %d nodes: %s" % (tally["nodes"],
                                   ", ".join("%s %d" % (k, v) for k, v in sorted(tally.items()) if k != "nodes")))
    for r in rows:
        print("  %-16s %-18s dim %-3s %-13s size %-3s %-11s %5s  %s" % (
            r["record_kind"], r["declared"], r["dim"], r["read_form"], r["size_byte"], r["rule"], r["nodes"],
            r["examples"]))
    print("wrote %s" % os.path.relpath(path, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
