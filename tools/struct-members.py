#!/usr/bin/env python3
"""The corpus's struct members and their declared types -- the table beside `inventory/class_properties.csv`.

A struct's member has **no class**. In the package it is a property record whose Outer is a `Struct` export
(`FilePath.FullPath` is Engine export 3007, `Directory` 3006) or, when the struct lives in another package, an
import whose owner is that struct (`Vector.Z` is import 21 of AmbientCreatures, written `36 95`). The record's own
class gives the member its *size* -- `FloatProperty` is four bytes, `IntProperty` four -- but never a declared type
word, and the struct a nested member is, or an array's element, is in the source only: `Coords.Origin` is a
`vector`, `FilePath.DirectoryParts` an `array<string>`. So the `var` line inside the `struct` block is the
authority, read exactly as `tools/uscript-dis.py`'s `source_structs()` reads it for the call census.

    python3 tools/struct-members.py --system DUMP/System -o inventory/struct_members.csv
    python3 tools/struct-members.py --check          # no dump: the committed artifact's own shape
    python3 tools/struct-members.py --report         # the counts `--check` prints, plus every untyped member

Rows are `struct,member,type,declarers,via`, ordered by struct then member, and a member that two class texts declare
with different types is written with an **empty** type rather than guessed. This corpus has none (0 of 800), so the
rule is a gate and not a repair; `--report` names the members it would blank.

A struct that **extends** another inherits its members: `Object.uc`'s `struct Plane extends Vector` declares only `W`,
and `Plane.X` is Vector's record. Those rows are written for the deriving struct with `via` naming the struct that
declares the member (empty for the struct's own `var` lines), because the record a receiver reads is the base's and
the `(struct, member)` key the emitter joins on is the deriving one. This corpus has two such structs
(`Plane extends Vector`, `BoundingVolume extends Box`).

Read from the packages, not from `$WORK/scripts-all`: that tree is a reconstruction, and a declaration census over
it can be short of the build (the reconstructed `UnrealScript` tree was six `native function` declarations short of
the dump). The extraction is cheap -- `pkg-script-text.script_text` is 0.1-0.2 s per class.
"""
import argparse
import collections
import csv
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("uscript_dis", os.path.join(ROOT, "tools", "uscript-dis.py"))
dis = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dis)

CSV_PATH = os.path.join(ROOT, "inventory", "struct_members.csv")
FIELDS = ["struct", "member", "type", "declarers", "via"]
TYPE_SHAPE = re.compile(r"^[A-Za-z_]\w*(?:\.\w+)?(?:<[A-Za-z_]\w*(?:\.\w+)?>)?$")


def die(msg):
    sys.exit("struct-members: " + msg)


def harvest(system):
    """`{(struct, member): Counter(type)}` over every class text, plus the `{struct: Counter(base)}` map, files, texts."""
    from ue2pkg import Package  # noqa: PLC0415 - only with a real dump

    _spec2 = importlib.util.spec_from_file_location("pkg_script_text", os.path.join(ROOT, "tools",
                                                                                  "pkg-script-text.py"))
    pst = importlib.util.module_from_spec(_spec2)
    _spec2.loader.exec_module(pst)
    script_text = pst.script_text

    texts, files = 0, 0
    out = collections.defaultdict(collections.Counter)
    bases = collections.defaultdict(collections.Counter)
    for fn in sorted(os.listdir(system)):
        if not fn.endswith(".u"):
            continue
        pkg = Package(os.path.join(system, fn))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        files += 1
        for i, e in enumerate(pkg.exports):
            if pkg.class_name_of(e) != "Class":
                continue
            text = script_text(pkg, e)
            if not text:
                continue
            texts += 1
            for struct, members in dis.source_structs(text).items():
                for member, t in members.items():
                    out[(struct.lower(), member.lower())][t] += 1
            for struct, base in dis.source_struct_bases(text).items():
                bases[struct.lower()][base.lower()] += 1
    return out, bases, files, texts


def rows_of(out, bases=None):
    own = {}
    for (struct, member) in sorted(out):
        counts = out[(struct, member)]
        declarers = sum(counts.values())
        kinds = {t for t in counts if t}
        kind = kinds.pop() if len(kinds) == 1 else ""
        own[(struct, member)] = {"struct": struct, "member": member, "type": (kind or "").lower(),
                                 "declarers": str(declarers), "via": ""}
    rows = dict(own)
    # A struct that extends another inherits its members, one row per base member with `via` naming the struct that
    # declares it. The walk follows the base chain (nothing here is deeper than one link) and is bounded, because a
    # declaration cycle would otherwise write rows forever.
    base = {s: next(iter(c)) for s, c in (bases or {}).items() if len(c) == 1}
    for struct in sorted(s for s in base if s in {k[0] for k in own}):
        link, chain = base.get(struct), []
        while link and link not in chain and len(chain) < 8:
            chain.append(link)
            link = base.get(link)
        for name in chain:
            for (s, m), r in own.items():
                if s == name and (struct, m) not in rows:
                    rows[(struct, m)] = {"struct": struct, "member": m, "type": r["type"],
                                         "declarers": r["declarers"], "via": name}
    return [rows[k] for k in sorted(rows)]


def read_csv(path=CSV_PATH):
    with open(path, newline="", encoding="utf-8") as fh:
        return [{k: r[k] for k in FIELDS} for r in csv.DictReader(fh)]


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def check(rows):
    """The committed artifact's own shape. Three claims it can re-derive: uniqueness, a type word, and the base link."""
    seen = {}
    for r in rows:
        key = (r["struct"], r["member"])
        if key in seen:
            die("%s.%s is declared twice" % key)
        seen[key] = r["type"]
        if not int(r["declarers"]):
            die("%s.%s has no declarer" % key)
        if r["type"] and not TYPE_SHAPE.match(r["type"]):
            die("%s.%s: `%s` is not a type word" % (key[0], key[1], r["type"]))
        if r["struct"] != r["struct"].lower() or r["member"] != r["member"].lower():
            die("%s.%s is not written lower-cased" % key)
        via = (r.get("via") or "").lower()
        if via:
            if via == r["struct"]:
                die("%s.%s extends itself" % key)
            if not any(x["struct"] == via and x["member"] == r["member"] for x in rows):
                die("%s.%s names `via %s`, which declares no such member" % (key[0], key[1], via))
            if any(x["struct"] == via and x["member"] == r["member"] and x["type"] != r["type"] for x in rows):
                die("%s.%s inherits `%s` with a different type" % (key[0], key[1], r["type"]))
    if rows != sorted(rows, key=lambda r: (r["struct"], r["member"])):
        die("the rows are not in struct/member order")
    structs = len({r["struct"] for r in rows})
    typed = sum(1 for r in rows if r["type"])
    inherited = sum(1 for r in rows if (r.get("via") or ""))
    print("ok struct-members: %d struct(s), %d member(s), %d typed, %d untyped, %d inherited"
          % (structs, len(rows), typed, len(rows) - typed, inherited))
    return True


def report(rows):
    check(rows)
    by_struct = collections.Counter(r["struct"] for r in rows)
    print("largest structs: %s" % ", ".join("%s %d" % kv for kv in by_struct.most_common(8)))
    untyped = ["%s.%s" % (r["struct"], r["member"]) for r in rows if not r["type"]]
    print("untyped: %s" % (", ".join(untyped) or "none"))
    inh = ["%s.%s <- %s" % (r["struct"], r["member"], r["via"]) for r in rows if (r.get("via") or "")]
    print("inherited: %s" % (", ".join(inh) or "none"))
    for name in ("vector", "rotator", "region", "coords", "color", "filepath"):
        got = ["%s:%s" % (r["member"], r["type"]) for r in rows if r["struct"] == name]
        print("  %s: %s" % (name, ", ".join(got) or "absent"))
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--system", help="the dump's System directory (the packages, not $WORK/scripts-all)")
    ap.add_argument("-o", "--out", default=CSV_PATH)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.check:
        return 0 if check(read_csv()) else 1
    if args.report:
        return 0 if report(read_csv()) else 1
    if not args.system:
        die("--system is required unless --check/--report (the corpus is game-derived)")
    out, bases, files, texts = harvest(args.system)
    rows = rows_of(out, bases)
    write_csv(args.out, rows)
    check(rows)
    print("struct-members: %d package(s), %d class text(s), %d row(s) -> %s"
          % (files, texts, len(rows), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
