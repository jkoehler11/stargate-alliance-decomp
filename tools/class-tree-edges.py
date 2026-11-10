#!/usr/bin/env python3
"""Name a class table from the constructors that call it: the subclass relation is in the image.

`inventory/xbe_class_table_names.csv` names 466 of the 492 per-class tables from the registration
literal; the 26 it leaves unnamed are the engine's own base classes, whose registration is not a
generated `ucpp` stub, so there is no store for `tools/class-table-names.py` to follow
(`docs/UNKNOWNS.md` U8).

This reads the relation the constructors themselves encode. Every class constructor installs its own
table and, before that store, calls its parent's constructor; `inventory/class_constructors.csv`
already records that call (`call_before_store`). Invert it and each table gets the set of classes
that *call* it -- its direct subclasses -- which is a nameable fact when the set is non-empty:

* for a table the registration already names, the set must equal the class's direct subclasses in
  `inventory/classes.csv`. That is the tool's self-check, and it is what makes the set evidence;
* for an unnamed table the set constrains the name to the classes whose subclass set it equals.

A table that nothing calls is a leaf class (`WaterTexture`, `FireTexture`, ...) and gets no name from
this instrument: it is reported as unconstrained, not guessed.

    python3 tools/class-tree-edges.py                 # summary + the unnamed tables
    python3 tools/class-tree-edges.py --csv out.csv   # one row per table
    python3 tools/class-tree-edges.py --check         # synthetic self-test, no dump, no tree write
"""
import argparse
import collections
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS = ["table_va", "class", "slots", "child_count", "children", "script_subs", "verdict"]


def load(root):
    """The three committed inventories this reads; no image, no dump."""
    tables, names, classes, ctors = {}, {}, {}, []
    with open(os.path.join(root, "inventory/xbe_class_tables.csv"), newline="") as f:
        for row in csv.DictReader(f):
            tables[int(row["table_va"], 16)] = int(row["slots"])
    with open(os.path.join(root, "inventory/xbe_class_table_names.csv"), newline="") as f:
        for row in csv.DictReader(f):
            names[int(row["table_va"], 16)] = row["class"]
    with open(os.path.join(root, "inventory/classes.csv"), newline="") as f:
        for row in csv.DictReader(f):
            classes[(row["package"], row["class"])] = row["super"]
    with open(os.path.join(root, "inventory/class_constructors.csv"), newline="") as f:
        for row in csv.DictReader(f):
            ctors.append(row)
    return tables, names, classes, ctors


def edges(ctors):
    """table -> {table that calls its constructor before installing its own}."""
    installers = collections.defaultdict(set)
    rows_of = collections.defaultdict(list)
    for row in ctors:
        t = int(row["table_va"], 16)
        installers[t].add(int(row["function_va"], 16))
        rows_of[t].append(row)
    fn2table = {}
    for t, fns in installers.items():
        for fn in fns:
            fn2table[fn] = t
    children = collections.defaultdict(set)
    for t, rows in rows_of.items():
        for row in rows:
            if not row.get("call_before_store"):
                continue
            parent = fn2table.get(int(row["call_before_store"], 16))
            if parent is not None and parent != t:
                children[parent].add(t)
    return children, rows_of


def analyse(tables, names, classes, ctors):
    children, _ = edges(ctors)
    subs = collections.defaultdict(set)
    for (_pkg, cls), sup in classes.items():
        if sup:
            subs[sup].add(cls)
    named_tables = {n: t for t, n in names.items() if n}
    out, agree, conflict, blank = [], 0, 0, 0
    for t in sorted(tables):
        kids = sorted(names.get(c) or ("?" + hex(c)) for c in children[t])
        kids_named = [k for k in kids if not k.startswith("?")]
        cls = names.get(t) or ""
        script = sorted(subs.get(cls, ())) if cls else []
        script_with_table = [s for s in script if s in named_tables]
        if cls:
            if kids_named == script_with_table:
                verdict, agree = "agrees", agree + 1
            elif not kids_named:
                verdict, blank = "no-subclass-in-image", blank + 1
            else:
                verdict, conflict = "conflicts", conflict + 1
        else:
            # a name for an unnamed table: the classes whose subclass set this table's exactly is
            cands = []
            if len(kids_named) != len(kids):
                verdict = "unnamed-children:" + "|".join(k for k in kids if k.startswith("?"))
                out.append({"table_va": hex(t), "class": cls, "slots": tables[t],
                            "child_count": len(kids), "children": "|".join(kids),
                            "script_subs": "", "verdict": verdict})
                continue
            if kids_named:
                kset = set(kids_named)
                for (_pkg, c) in classes:
                    if c in named_tables or len(subs.get(c, ())) != len(kset):
                        continue
                    if set(subs.get(c, ())) == kset:
                        cands.append(c)
            verdict = "candidate:" + cands[0] if len(cands) == 1 else (
                "ambiguous:" + "|".join(sorted(cands)) if cands else "unconstrained")
        out.append({"table_va": hex(t), "class": cls, "slots": tables[t],
                    "child_count": len(kids), "children": "|".join(kids),
                    "script_subs": "|".join(script_with_table), "verdict": verdict})
    return out, agree, conflict, blank



def site_thunks(xbe_path, root):
    """Name a table from the constructor a registration site pushes, when that pointer is a thunk.

    A site `E8`s the class constructor and pushes its arguments in the 96 bytes before, one of them a
    pointer to the class's own constructor. On a generated `ucpp` stub that pointer is a thunk whose
    `jmp` lands on the constructor that installs the table; on the engine's own base classes the same
    window can also hold a *neighbouring* registration's push, so this reports the link only when the
    pushed pointer resolves to one of the table-installing constructors.
    """
    sys.path.insert(0, os.path.join(root, "tools"))
    from xbe import Xbe  # noqa: E402

    xbe = Xbe(xbe_path)
    data = xbe.data

    def sec_of(va):
        for s in xbe.sections:
            if s.va <= va < s.va + s.vsize:
                return s
        return None

    def read(va, n):
        s = sec_of(va)
        if not s:
            return None
        off = s.foff + (va - s.va)
        return data[off:off + n]

    names = {}
    with open(os.path.join(root, "inventory/xbe_class_table_names.csv"), newline="") as f:
        for row in csv.DictReader(f):
            names[int(row["table_va"], 16)] = row["class"]
    installer = {}
    with open(os.path.join(root, "inventory/class_constructors.csv"), newline="") as f:
        for row in csv.DictReader(f):
            installer[int(row["function_va"], 16)] = int(row["table_va"], 16)
    sites = []
    with open(os.path.join(root, "inventory/registration_sites.csv"), newline="") as f:
        for row in csv.DictReader(f):
            if not row["class_table"]:
                sites.append((int(row["site_va"], 16), row["class"]))

    def jmp_target(va):
        b = read(va, 24)
        if not b:
            return None
        for i in range(min(len(b) - 5, 16)):
            if b[i] == 0xE9:
                return va + i + 5 + struct.unpack_from("<i", b, i + 1)[0]
        return None

    found = []
    for site, cls in sites:
        b = read(site - 96, 96)
        if not b:
            continue
        for i in range(len(b) - 4):
            if b[i] != 0x68:
                continue
            v = struct.unpack_from("<I", b, i + 1)[0]
            for target in (v, jmp_target(v)):
                if target in installer:
                    found.append({"site_va": hex(site), "class": cls,
                                  "pushed": hex(v), "constructor": hex(target),
                                  "table_va": hex(installer[target]),
                                  "table_class": names.get(installer[target], "")})
                    break
            else:
                continue
            break
    return found


def check():
    """Synthetic: three tables, three classes, one shared base -- asserts the direction."""
    ctors = [
        {"table_va": "0x1000", "function_va": "0x10", "call_before_store": "0x20", "form": "ctor"},
        {"table_va": "0x2000", "function_va": "0x20", "call_before_store": "0x30", "form": "ctor"},
        {"table_va": "0x3000", "function_va": "0x30", "call_before_store": "", "form": "ctor"},
    ]
    tables = {0x1000: 4, 0x2000: 4, 0x3000: 4}
    names = {0x1000: "Leaf", 0x2000: "", 0x3000: "Root"}
    classes = {("P", "Root"): "", ("P", "Mid"): "Root", ("P", "Leaf"): "Mid"}
    out, _a, _c, _b = analyse(tables, names, classes, ctors)
    by = {r["table_va"]: r for r in out}
    assert by["0x3000"]["children"] == "?0x2000", by["0x3000"]
    assert by["0x2000"]["verdict"] == "candidate:Mid", by["0x2000"]
    assert by["0x1000"]["child_count"] == 0, by["0x1000"]
    assert by["0x1000"]["verdict"] == "agrees", by["0x1000"]  # a leaf: nothing calls it, no script sub
    print("class-tree-edges: check ok (3 tables, direction and candidate match)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--csv", help="write one row per table here")
    ap.add_argument("--unnamed-only", action="store_true")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, writes nothing")
    ap.add_argument("--xbe", help="also link the unnamed registration sites through their pushed thunk")
    args = ap.parse_args()
    if args.check:
        return check()
    tables, names, classes, ctors = load(args.root)
    out, agree, conflict, blank = analyse(tables, names, classes, ctors)
    unnamed = [r for r in out if not r["class"]]
    print("tables %d, named %d, unnamed %d" % (len(out), len(out) - len(unnamed), len(unnamed)))
    print("named over a measured child set: %d agree, %d conflict, %d no subclass in the image"
          % (agree, conflict, blank))
    cands = [r for r in unnamed if r["verdict"].startswith("candidate:")]
    amb = [r for r in unnamed if r["verdict"].startswith("ambiguous:")]
    free = [r for r in unnamed if r["verdict"] == "unconstrained"]
    print("unnamed: %d named by their child set, %d ambiguous, %d unconstrained (leaf)"
          % (len(cands), len(amb), len(free)))
    for r in (unnamed if args.unnamed_only else out):
        if r["class"] and not args.unnamed_only:
            continue
        print("  %s %-34s children=%-2d %s" % (r["table_va"], r["verdict"], r["child_count"],
                                               r["children"][:70]))
    if args.xbe:
        linked = site_thunks(args.xbe, args.root)
        sites_seen = {r["site_va"] for r in linked}
        print("registration sites with no table in the class map: %d" % len(sites_seen))
        print("of those, %d link through a pushed constructor" % len(linked))
        for row in linked:
            print("  %s %-22s push %s -> %s table %s %s" % (
                row["site_va"], row["class"], row["pushed"], row["constructor"],
                row["table_va"], row["table_class"]))
    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, FIELDS)
            w.writeheader()
            w.writerows(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
