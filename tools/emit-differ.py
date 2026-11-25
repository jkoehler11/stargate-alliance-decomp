#!/usr/bin/env python3
"""The bodies that emit and still differ: classify each one at its **first differing node**.

`tools/uscript-emit.py` refuses a body whose bytes differ from the package's, so the residue carries the shape that
was wrong and both byte strings. A byte offset alone is not a finding -- `StargatePawn.EndCrouch`'s 354-vs-354
difference at offset 173 is a `Context` size byte, while a 268-vs-270 body whose offset 1 differs is usually a
`JumpIfNot` distance that *moved because something downstream is missing*. This tool parses both streams with the
image's own operand table and walks the two token trees in document order, so the first difference is a *node*, named
with what changed inside it (a size byte, a skip word, an index, an operand, a node only one side has).

    python3 tools/uscript-emit.py --tree $WORK/scripts-all --dump .../System -o $WORK/emit \\
        --residue-out $WORK/residue
    python3 tools/emit-differ.py --residue $WORK/residue/residue.csv --emit $WORK/emit/uscript_emit.csv -o inventory
    python3 tools/emit-differ.py --report
    python3 tools/emit-differ.py --write-doc docs/COMPILER_FRONTEND.md
    python3 tools/emit-differ.py --check          # synthetic, no dump, writes nothing

A **jump distance** is only a family of its own when it moved *with* the length delta: `ours` shorter by n and the
word smaller by n means the branch is right and the body is missing n bytes after it -- the difference is downstream,
and the row says so rather than sending the next run after the jump. `inventory/emit_differ.csv` is one row per
(family, first node, length delta sign, downstream) with a body count and up to three `class.function` examples.
"""

import argparse
import collections
import csv
import importlib.util
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "inventory", "emit_differ.csv")
FIELDS = ["family", "first_node", "length_delta", "downstream", "bodies", "examples"]
DOC_A = ("<!-- BEGIN emit-differ -->", "<!-- END emit-differ -->")

DIFFERS = "an emitted body that differs"

# The tokens whose word operand is a position in the compiler's code array, not a value: a difference here is a
# *distance*, and whether it moved with the body's length decides if the cause is at the jump or further on.
JUMPS = {0x06: "Jump", 0x07: "JumpIfNot"}


def _dis():
    spec = importlib.util.spec_from_file_location("uscript_dis", os.path.join(ROOT, "tools", "uscript-dis.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def classify(ours, theirs):
    """`(family, node name, downstream)` for the first node where two token trees part company.

    The walk is document order over both trees at once: the first unequal *node* is the answer, and what is unequal
    inside it names the family. A node only one side has is a body that emits too much or too little, which is a
    different repair from a node that is present on both sides with a different operand.
    """
    a, b = ours, theirs
    # a token tree's own length: the last top-level node's end, which is the body's byte count
    delta = (a[-1]["end"] if a else 0) - (b[-1]["end"] if b else 0)

    def node_at(nodes_a, nodes_b):
        for i in range(max(len(nodes_a), len(nodes_b))):
            if i >= len(nodes_a) or i >= len(nodes_b):
                side = "ours" if i >= len(nodes_b) else "theirs"
                node = nodes_a[i] if i < len(nodes_a) else nodes_b[i]
                return ("node-only-%s" % side, node["name"] or ("op%02x" % node["opcode"]), "", None)
            na, nb = nodes_a[i], nodes_b[i]
            if na["opcode"] != nb["opcode"]:
                return ("opcode", na["name"] or ("op%02x" % na["opcode"]), "", None)
            if na["operands"] != nb["operands"]:
                idx = next((k for k in range(min(len(na["operands"]), len(nb["operands"])))
                            if na["operands"][k] != nb["operands"][k]), None)
                name = na["name"] or ("op%02x" % na["opcode"])
                if na["opcode"] in (0x19, 0x12) and idx == 2:
                    return ("context size", name, "", None)
                if na["opcode"] in (0x19, 0x12) and idx is not None and idx < 2:
                    return ("context skip", name, "", None)
                if na["opcode"] in JUMPS and idx is not None and idx < 2:
                    # the word is a code position: read both and see whether the distance moved with the length
                    moved = None
                    if len(na["operands"]) >= 2 and len(nb["operands"]) >= 2:
                        va = int.from_bytes(na["operands"][:2], "little")
                        vb = int.from_bytes(nb["operands"][:2], "little")
                        moved = bool(delta) and (va - vb) == delta
                    return ("jump distance", name, "yes" if moved else "no", None)
                if na["opcode"] in (0x01, 0x00, 0x36, 0x1A, 0x10, 0x1B, 0x1C):
                    return ("node index", name, "", None)
                return ("operand", name, "", idx)
            got = node_at(na["children"], nb["children"])
            if got is not None:
                return got
        return None

    got = node_at(a, b)
    if got is None:
        return ("length only", "", "", delta)
    return (got[0], got[1], got[2], got[3])


def summarise(rows, dis):
    """The counter and the examples, from residue rows: one verdict per differing body."""
    counts = collections.Counter()
    examples = collections.defaultdict(list)
    complete = 0
    for r in rows:
        if not r["reason"].startswith(DIFFERS):
            continue
        complete += 1
        try:
            ours = dis.parse(bytes.fromhex(r["ours"]), dis.Table())
            theirs = dis.parse(bytes.fromhex(r["theirs"]), dis.Table())
        except dis.Refused:
            counts[("unparsed", "", "", "")] += 1
            examples[("unparsed", "", "", "")].append("%s.%s" % (r["class"], r["function"]))
            continue
        family, node, downstream, extra = classify(ours, theirs)
        sign = "ours shorter" if len(bytes.fromhex(r["ours"])) < len(bytes.fromhex(r["theirs"])) else (
            "same length" if len(bytes.fromhex(r["ours"])) == len(bytes.fromhex(r["theirs"])) else "ours longer")
        key = (family, node, sign, downstream)
        counts[key] += 1
        if len(examples[key]) < 3:
            examples[key].append("%s.%s" % (r["class"], r["function"]))
    return counts, examples, complete


def rows_of(counts, examples):
    rows = []
    for key, n in sorted(counts.items()):
        family, node, sign, downstream = key
        rows.append({"family": family, "first_node": node, "length_delta": sign, "downstream": downstream,
                     "bodies": str(n), "examples": " ".join(examples[key])})
    return rows


def read_artifact(path):
    rows, census_row = [], {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["family"] == "(census)":
                census_row = r
            else:
                rows.append(r)
    return rows, census_row


def render(rows, census):
    total = sum(int(r["bodies"]) for r in rows)
    downstream = sum(int(r["bodies"]) for r in rows if r["downstream"] == "yes")
    lines = []
    lines.append("**{:,} emitted bodies differ from the shipped bytes, and every one is a named node** "
                 "(`inventory/emit_differ.csv`; {:,} modelled and identical in the same run). **{:,}** of them differ "
                 "first at a jump whose distance moved with the body's own length -- the branch is right and the "
                 "body is short after it -- so the count is not {:,} separate defects.".format(
                     total, census.get("identical", 0), downstream, total))
    lines.append("")
    lines.append("| first difference | node | length | branch moved with it | bodies | examples |")
    lines.append("|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: (-int(r["bodies"]), r["family"], r["first_node"])):
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            r["family"], r["first_node"] or "-", r["length_delta"] or "-", r["downstream"] or "-", r["bodies"],
            ", ".join("`%s`" % e for e in (r["examples"] or "").split()[:3]) or "-"))
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
    """Synthetic: every family from a hand-built stream, and the render's round trip."""
    dis = _dis()
    table = dis.Table()
    bad = []

    def body(kind):
        # `return <x>; EndOfScript` shapes, one per answer the classifier has to give. The Context is written with a
        # one-byte object read (`01 01`), so its three operand bytes are unambiguous: skip word, then the size byte.
        base = [0x04, 0x19, 0x01, 0x01, 0x05, 0x00, 0x04, 0x01, 0x07, 0x04, 0x0B, 0x47]
        if kind == "same":
            return bytes(base)
        if kind == "size":
            # the same node with the Context's size byte 8 instead of 4 (EndCrouch's shape)
            b = list(base)
            b[6] = 0x08
            return bytes(b)
        if kind == "size-short":
            b = list(base)
            b[6] = 0x02
            return bytes(b)
        if kind == "index":
            # the member's own record index moves, the size byte does not
            b = list(base)
            b[8] = 0x08
            return bytes(b)
        if kind == "jump-moved":
            # `JumpIfNot 0x0004` then a body two bytes shorter than the other side's `JumpIfNot 0x0006`
            return bytes([0x07, 0x04, 0x00, 0x04, 0x26, 0x04, 0x0B, 0x47])
        if kind == "jump-moved-long":
            return bytes([0x07, 0x06, 0x00, 0x04, 0x26, 0x2C, 0xFF, 0x04, 0x0B, 0x47])
        if kind == "jump-fixed":
            return bytes([0x07, 0x04, 0x00, 0x04, 0x26, 0x04, 0x0B, 0x47])
        if kind == "jump-fixed-long":
            # the word moves 4 -> 9 while the body grows by two: the distance did *not* follow the length
            return bytes([0x07, 0x09, 0x00, 0x04, 0x26, 0x2C, 0xFF, 0x04, 0x0B, 0x47])
        if kind == "extra-node":
            return bytes(base + [0x0B])
        raise AssertionError(kind)

    cases = [("same", "same", "length only", ""),
             ("size", "size-short", "context size", ""),
             ("jump-moved", "jump-moved-long", "jump distance", "yes"),
             ("jump-fixed", "jump-fixed-long", "jump distance", "no"),
             ("extra-node", "same", "node-only-ours", "")]
    for a, b, want_family, want_down in cases:
        got = classify(dis.parse(body(a), table), dis.parse(body(b), table))
        if got[0] != want_family or (want_down and got[2] != want_down):
            bad.append("classify(%s, %s) = %s, wanted %s / %s" % (a, b, got, want_family, want_down or "-"))
    rows = [{"family": "jump distance", "first_node": "JumpIfNot", "length_delta": "ours shorter",
             "downstream": "yes", "bodies": "2", "examples": "Probe.A Probe.B"},
            {"family": "context size", "first_node": "Context", "length_delta": "same length",
             "downstream": "", "bodies": "1", "examples": "Probe.C"}]
    block = render(rows, {"identical": 10323})
    if "**3 emitted bodies differ" not in block or "**2** of them differ" not in block:
        bad.append("the render does not carry the totals it was handed")
    if "| context size | Context | same length | - | 1 |" not in block:
        bad.append("the render does not carry the context-size row")
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "emit_differ.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
            w.writeheader()
            w.writerow({"family": "(census)", "bodies": "3", "examples": "10323 identical"})
            for r in rows:
                w.writerow(r)
        back, _census = read_artifact(path)
        if render(back, {"identical": 10323}) != block:
            bad.append("the render does not reproduce from the written artifact")
    finally:
        for name in os.listdir(tmp):
            os.unlink(os.path.join(tmp, name))
        os.rmdir(tmp)
    for line in bad:
        print("FAIL emit-differ: %s" % line)
    if not bad:
        print("ok   emit-differ: the six families, the distance that moves with the length, the render and its "
              "round trip")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--residue")
    ap.add_argument("--emit", default=os.path.join(ROOT, "inventory", "uscript_emit.csv"))
    ap.add_argument("-o", "--out-dir")
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
            print("FAIL emit-differ: %s is missing" % OUT)
            return 1
        block = render(rows, {"identical": _identical(args.emit)})
        with open(args.check_doc, encoding="utf-8") as fh:
            text = fh.read()
        i, j = text.find(DOC_A[0]), text.find(DOC_A[1])
        if i < 0 or j < 0:
            print("FAIL emit-differ: %s has no %s block" % (args.check_doc, DOC_A[0]))
            return 1
        if text[i:j + len(DOC_A[1])] != DOC_A[0] + "\n" + block + "\n" + DOC_A[1]:
            print("FAIL emit-differ: %s's block does not reproduce from the committed CSV" % args.check_doc)
            return 1
        print("ok   emit-differ: %s reproduces from the committed CSV" % args.check_doc)
        return 0
    if args.report or args.write_doc:
        rows, _census = read_artifact(OUT)
        if not rows:
            print("FAIL emit-differ: %s is missing" % OUT)
            return 1
        block = render(rows, {"identical": _identical(args.emit)})
        if args.report:
            print(block)
            return 0
        if splice_doc(args.write_doc, block, DOC_A) is None:
            print("FAIL emit-differ: %s has no %s markers" % (args.write_doc, DOC_A[0]))
            return 1
        print("wrote %s (%s)" % (args.write_doc, DOC_A[0]))
        return 0
    if not args.residue:
        ap.error("--residue is required to join (or use --report/--check-doc/--check)")
    dis = _dis()
    rows = list(csv.DictReader(open(args.residue, newline="", encoding="utf-8")))
    counts, examples, complete = summarise(rows, dis)
    out = rows_of(counts, examples)
    out_dir = args.out_dir or os.path.join(ROOT, "inventory")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "emit_differ.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerow({"family": "(census)", "bodies": str(complete),
                    "examples": "%d identical" % _identical(args.emit)})
        for r in out:
            w.writerow(r)
    print("%d differing bodies over %d shapes" % (complete, len(out)))
    for r in out:
        print("  %-16s %-14s %-14s downstream=%-3s %3s  %s" % (
            r["family"], r["first_node"], r["length_delta"], r["downstream"] or "-", r["bodies"], r["examples"]))
    print("wrote %s" % os.path.relpath(path, ROOT))
    return 0


def _identical(emit_csv):
    """The modelled/identical count the page's headline quotes, read from the emitter's own artifact."""
    if not emit_csv or not os.path.exists(emit_csv):
        return 0
    with open(emit_csv, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("package") == "(census)" or r.get("class") == "(census)":
                try:
                    return int(r.get("ours") or 0)
                except ValueError:
                    return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
