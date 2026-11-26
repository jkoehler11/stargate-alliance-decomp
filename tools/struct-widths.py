#!/usr/bin/env python3
"""The struct widths the corpus *writes*, against the table the emitter reads.

`tools/uscript-emit.py` writes a `Context`'s size byte for a struct-typed value from `STRUCT_SIZE`, and that table
grew two ways: from a node that reads the struct (`inventory/context_size.csv`, the census this tool joins) and from a
body that refused for want of the width and came out byte-identical at exactly one byte -- the "the match is the
measurement" rule the table's own comment states. Both are measurements, but only the first can be re-derived by a
tool from the committed artifacts, so this one counts which entries rest on which and **fails when a node's byte
contradicts the table**.

    python3 tools/struct-widths.py -o inventory            # write inventory/struct_widths.csv
    python3 tools/struct-widths.py --report                # the census, printed
    python3 tools/struct-widths.py --write-doc docs/COMPILER_FRONTEND.md
    python3 tools/struct-widths.py --check                 # synthetic, no dump, writes nothing, fails on conflict

An **unwitnessed** entry is not a wrong entry: `coords` and `interpcurve` are pinned by bodies, and no `Context` node
in the corpus reads either. What this tool refuses to do is *grow* the table: a declared struct with no node witness
has no width it can state, so it is listed as `absent` and the next body that refuses for it has a population to read
instead of a number to guess.

The artifact is one row per struct the source declares (`inventory/struct_members.csv`'s first column) plus one
`(census)` row the rendered page reads its counts from -- names and numbers only, no package bytes, no source text.
"""

import argparse
import collections
import csv
import importlib.util
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMBERS = os.path.join(ROOT, "inventory", "struct_members.csv")
CONTEXT = os.path.join(ROOT, "inventory", "context_size.csv")
OUT = os.path.join(ROOT, "inventory", "struct_widths.csv")
DOC_A = ("<!-- BEGIN struct-widths -->", "<!-- END struct-widths -->")
FIELDS = ["struct", "in_table", "table_width", "witness_nodes", "witness_bytes", "verdict"]

AGREES = "agrees"
CONFLICT = "conflict"
UNWITNESSED = "unwitnessed"
ABSENT = "absent"


def _emitter():
    """The width table under test, imported once (it is the claim, not a copy of it)."""
    spec = importlib.util.spec_from_file_location("uscript_emit", os.path.join(ROOT, "tools", "uscript-emit.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def declared(path=MEMBERS):
    """Every struct name the source census declares, in the census's own order."""
    names = []
    seen = set()
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            nm = (r.get("struct") or "").strip().lower()
            if nm and nm not in seen:
                seen.add(nm)
                names.append(nm)
    return names


def witnesses(path=CONTEXT):
    """`{struct: {byte: nodes}}` from the `struct member` population of the `Context` census.

    `declared` is the *member's* declared type word, which is the struct whose value the node reads; an empty word is
    the census's own "the artifact does not name this" bucket and is skipped rather than folded into any struct.
    """
    out = collections.defaultdict(lambda: collections.Counter())
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("population") != "struct member" or not r.get("size_byte"):
                continue
            nm = (r.get("declared") or "").strip().lower()
            if not nm:
                continue
            out[nm][r["size_byte"].strip()] += int(r.get("nodes") or 0)
    return out


def verdict(name, table, seen):
    """One struct's row: `(in_table, table_width, nodes, bytes, verdict)`."""
    tw = table.get(name)
    nmap = seen.get(name) or {}
    nodes = sum(nmap.values())
    bs = ";".join("%s x%d" % (b, n) for b, n in sorted(nmap.items(), key=lambda kv: -kv[1]))
    if tw is not None:
        if not nmap:
            return ("yes", str(tw), 0, "", UNWITNESSED)
        if len(nmap) == 1 and str(tw) in nmap:
            return ("yes", str(tw), nodes, bs, AGREES)
        return ("yes", str(tw), nodes, bs, CONFLICT)
    return ("no", "", nodes, bs, ABSENT)


def rows(names=None, table=None, seen=None):
    names = list(names if names is not None else declared())
    table = {k.lower(): v for k, v in (table if table is not None else _emitter().STRUCT_SIZE).items()}
    seen = seen if seen is not None else witnesses()
    out = []
    for nm in names:
        it, tw, nodes, bs, v = verdict(nm, table, seen)
        out.append({"struct": nm, "in_table": it, "table_width": tw,
                    "witness_nodes": nodes, "witness_bytes": bs, "verdict": v})
    # every entry in the table is a row, even one no declared struct spells (`vector` is declared, a native struct is
    # not) -- an entry the census cannot see is exactly what a gate has to notice
    for nm in table:
        if nm not in names:
            it, tw, nodes, bs, v = verdict(nm, table, seen)
            out.append({"struct": nm, "in_table": it, "table_width": tw,
                        "witness_nodes": nodes, "witness_bytes": bs, "verdict": v})
    agreed = sum(1 for r in out if r["verdict"] == AGREES)
    out.append({"struct": "(census)",
                "in_table": str(len(table)),
                "table_width": str(agreed),
                "witness_nodes": str(sum(int(r["witness_nodes"]) for r in out)),
                "witness_bytes": str(sum(1 for r in out if r["verdict"] == CONFLICT)),
                "verdict": "entries / node-witnessed / conflicts"})
    return out


def census(rs):
    """The `(census)` row's numbers, read from the rows the page also renders from."""
    for r in rs:
        if r["struct"] == "(census)":
            return r
    raise SystemExit("FAIL struct-widths: no census row")


def render(rs):
    c = census(rs)
    witnessed = [r for r in rs if r["verdict"] in (AGREES, CONFLICT)]
    blind = [r for r in rs if r["verdict"] == UNWITNESSED]
    absent = sum(1 for r in rs if r["verdict"] == ABSENT)
    lines = [
        "`STRUCT_SIZE` carries **%s** widths. **%s** of them are node-witnessed -- a `Context`'s size byte in a body"
        " that reads the struct -- and those rest on the census below, which also holds the table's **%s**"
        " conflict(s): a node whose byte disagrees with the entry fails `tools/struct-widths.py --check`, so a width"
        " cannot drift from the corpus it claims."
        % (c["in_table"], c["table_width"], c["witness_bytes"]),
        "",
        "The entries with no node are **%s**: each rests on a body that refused for want of the width and came out"
        " byte-identical at exactly one byte, and no instrument re-derives one from the corpus -- which is why they are"
        " listed rather than removed. The table is **not** grown from this census: %d declared structs have no node"
        " witness at all, and a width the corpus never writes is a guess with a number's shape." %
        (", ".join("`%s`" % r["struct"] for r in blind) or "none", absent),
        "",
        "| struct | width | node reads | byte(s) written |",
        "|---|---|---|---|",
    ]
    for r in sorted(witnessed, key=lambda r: -int(r["witness_nodes"])):
        lines.append("| `%s` | %s | %d | %s |" % (r["struct"], r["table_width"], int(r["witness_nodes"]),
                                                  r["witness_bytes"]))
    return "\n".join(lines)


def splice(path, text):
    body = open(path, encoding="utf-8").read()
    a, b = DOC_A
    if a not in body or b not in body:
        raise SystemExit("FAIL struct-widths: %s carries no %s marker" % (path, a))
    head, rest = body.split(a, 1)
    _mid, tail = rest.split(b, 1)
    return head + a + "\n" + text + "\n" + b + tail


def write_csv(rs, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "struct_widths.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rs)
    print("wrote %s (%d rows)" % (path, len(rs)))
    return path


def check(page=None):
    """Synthetic: every verdict, the census row against its own rows, and a conflict that has to fail.

    No dump and nothing written: the witness map and the width table are handed in, so this drives `verdict()` and
    `rows()` against a fixture whose answers are known -- including the shapes the real corpus does not currently
    have (a witnessed struct absent from the table, and a two-byte witness).
    """
    table = {"vector": 12, "coords": 12, "mousecursor": 16, "htmlstyle": 28}
    seen = {"vector": {"12": 400}, "mousecursor": {"16": 30}, "htmlstyle": {"28": 30, "32": 2},
            "extra": {"20": 3}}
    names = ["vector", "coords", "mousecursor", "htmlstyle", "extra", "unseen"]
    rs = rows(names, table, seen)
    got = {r["struct"]: r["verdict"] for r in rs if not r["struct"].startswith("(")}
    want = {"vector": AGREES, "coords": UNWITNESSED, "mousecursor": AGREES, "htmlstyle": CONFLICT,
            "extra": ABSENT, "unseen": ABSENT}
    if got != want:
        raise SystemExit("FAIL struct-widths: verdicts %r, expected %r" % (got, want))
    c = census(rs)
    if int(c["witness_nodes"]) != 400 + 30 + 32 + 3:
        raise SystemExit("FAIL struct-widths: census nodes %s" % c["witness_nodes"])
    if int(c["witness_bytes"]) != 1:
        raise SystemExit("FAIL struct-widths: census conflicts %s" % c["witness_bytes"])
    if int(c["table_width"]) != 2:
        raise SystemExit("FAIL struct-widths: census node-witnessed %s" % c["table_width"])
    # a conflict has to *name* the row it came from, and a clean fixture has to report none -- both halves, because
    # a gate that fails on every fixture and one that fails on none read the same from the outside
    if [r["struct"] for r in rs if r["verdict"] == CONFLICT] != ["htmlstyle"]:
        raise SystemExit("FAIL struct-widths: a two-byte witness did not name its own row")
    clean = rows(["vector"], {"vector": 12}, {"vector": {"12": 5}})
    if any(r["verdict"] == CONFLICT for r in clean):
        raise SystemExit("FAIL struct-widths: an agreeing row read as a conflict")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", metavar="DIR", help="write struct_widths.csv here")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc", metavar="PAGE")
    ap.add_argument("--check-doc", metavar="PAGE")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if args.check:
        rc = check()
        # the committed table has to clear the same gate the fixture does, from committed artifacts only
        rs = rows()
        bad = [r for r in rs if r["verdict"] == CONFLICT]
        if bad:
            print("\n".join("FAIL struct-widths: %s is declared %s and the corpus writes %s"
                            % (r["struct"], r["table_width"], r["witness_bytes"]) for r in bad))
            return 1
        print("ok struct-widths: %s entries, %s node-witnessed, %s with no node, 0 conflicts"
              % (census(rs)["in_table"], census(rs)["table_width"],
                 sum(1 for r in rs if r["verdict"] == UNWITNESSED)))
        return rc
    if args.write_doc:
        rs = rows()
        new = splice(args.write_doc, render(rs))
        open(args.write_doc, "w", encoding="utf-8").write(new)
        print("wrote the block in %s" % args.write_doc)
        return 0
    if args.check_doc:
        want = splice(args.check_doc, render(rows()))
        have = open(args.check_doc, encoding="utf-8").read()
        if want != have:
            print("FAIL struct-widths: %s's block is stale" % args.check_doc)
            return 1
        print("ok struct-widths: %s's block is current" % args.check_doc)
        return 0
    if args.report:
        for r in rows():
            print("%-22s %-4s %-3s %6s  %s" % (r["struct"], r["in_table"], r["table_width"],
                                               r["witness_nodes"], r["verdict"]))
        return 0
    if args.out:
        write_csv(rows(), args.out)
        return 0
    ap.error("one of -o, --report, --write-doc, --check-doc, --check")


if __name__ == "__main__":
    sys.exit(main())
