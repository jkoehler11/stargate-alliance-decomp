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
    python3 tools/class-tree-edges.py --write-doc docs/NATIVE_CLASS_TREE.md   # render the count block
    python3 tools/class-tree-edges.py --check-doc docs/NATIVE_CLASS_TREE.md   # ... and fail if it moved

The page quotes these counts in prose, so they are generated rather than typed: `make ci` runs
`--check-doc`, and every row of the block is recomputed from the four committed inventories -- no image,
no dump, no write on the check path.
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



def site_thunks(xbe_path, root, every_site=False):
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
            if every_site or not row["class_table"]:
                sites.append((int(row["site_va"], 16), row["class"]))

    def jmp_target(va):
        b = read(va, 24)
        if not b:
            return None
        for i in range(min(len(b) - 5, 16)):
            if b[i] == 0xE9:
                return va + i + 5 + struct.unpack_from("<i", b, i + 1)[0]
        return None

    found, blank = [], 0
    for site, cls in sites:
        b = read(site - 96, 96)
        if not b:
            blank += 1
            continue
        hit = None
        for i in range(len(b) - 4):
            if b[i] != 0x68:
                continue
            v = struct.unpack_from("<I", b, i + 1)[0]
            for target in (v, jmp_target(v)):
                if target in installer:
                    hit = {"site_va": hex(site), "class": cls,
                           "pushed": hex(v), "constructor": hex(target),
                           "table_va": hex(installer[target]),
                           "table_class": names.get(installer[target], "")}
                    break
            if hit:
                break
        if hit:
            found.append(hit)
        else:
            blank += 1
    return found, blank


def site_verdicts(rows, blank):
    """Split the push-window join into own-table / other-table / no-constructor.

    A row whose pushed constructor installs the site's *own* class table is what makes the
    literal path evidence; a row that lands on a neighbouring registration (a mixed window, or a
    shared default constructor that installs one fixed table) is the caveat the page carries.
    """
    own, other, absent = [], [], []
    for row in rows:
        cls, tcls = row["class"], row["table_class"]
        if cls and tcls and cls == tcls:
            own.append(row)
        elif tcls:
            other.append(row)
        else:
            absent.append(row)
    return own, other, absent + [None] * blank


def ancestors(classes):
    """class -> the classes above it in the script tree, when each step resolves to one super."""
    supers = collections.defaultdict(set)
    for (_pkg, cls), sup in classes.items():
        if sup:
            supers[cls].add(sup)
    out = {}
    for cls in supers:
        chain, seen, cur = [], {cls}, cls
        while True:
            nxt = supers.get(cur, ())
            if len(nxt) != 1:
                break
            cur = next(iter(nxt))
            if cur in seen:
                break
            seen.add(cur)
            chain.append(cur)
        out[cls] = chain
    return out


def decompose(tables, names, classes, ctors):
    """Why a named table's measured child set disagrees with the script tree, in measured terms.

    `call_before_store` is the *first* call before the table store (`class-ctors.py` stops at the
    first `E8`), so inverting it yields the nearest ancestor whose constructor has an out-of-line
    body, not always the direct parent. Four causes come out, and only `foreign-child` is evidence
    against a name:

    * `no-script-row` -- the named class is absent from `inventory/classes.csv` (a native C++
      class), so there is no script super to compare and its child set is measured-only;
    * `descendant-edge` -- every extra child is a descendant of the class, so the edge sits one or
      more levels up: the body calls an ancestor's constructor and the intermediate level never
      appears as a call;
    * `foreign-child` -- an extra child is in `classes.csv` but *not* a descendant: its constructor
      calls this table's constructor and the script tree puts it somewhere else, so one of the two
      is wrong;
    * `native-child` -- every extra child is absent from `classes.csv` (a native C++ class), so the
      measured child set is all there is;
    * `missing-only` -- nothing extra, something missing; report where the missing child's first
      call lands instead.
    """
    children, _rows = edges(ctors)
    subs = collections.defaultdict(set)
    known = set()
    for (_pkg, cls), sup in classes.items():
        known.add(cls)
        if sup:
            subs[sup].add(cls)
    up = ancestors(classes)
    named_tables = {n: t for t, n in names.items() if n}
    fn2table = {}
    for t, rows in _rows.items():
        for row in rows:
            fn2table[int(row["function_va"], 16)] = t
    first_call = collections.defaultdict(set)
    for row in ctors:
        if row.get("class") and row.get("call_before_store"):
            first_call[row["class"]].add(int(row["call_before_store"], 16))

    out = []
    for t in sorted(tables):
        cls = names.get(t) or ""
        if not cls:
            continue
        kids = sorted(names.get(c) or ("?" + hex(c)) for c in children[t])
        named_kids = [k for k in kids if not k.startswith("?")]
        script = sorted(s for s in subs.get(cls, ()) if s in named_tables)
        if not named_kids or named_kids == script:
            continue
        extra = [k for k in named_kids if k not in script]
        missing = [s for s in script if s not in named_kids]
        if cls not in known:
            cause = "no-script-row"
        elif any(k in known and cls not in up.get(k, ()) for k in extra):
            cause = "foreign-child"
        elif any(cls in up.get(k, ()) for k in extra):
            cause = "descendant-edge"
        elif extra:
            cause = "native-child"
        else:
            cause = "missing-only"
        def where(va):
            t2 = fn2table.get(va)
            return (names.get(t2) or "?" + hex(t2)) if t2 is not None else "?fn:" + hex(va)
        lands = []
        for s in missing:
            targets = sorted(first_call.get(s, ()))
            lands.append("%s->%s" % (s, ",".join(where(x) for x in targets) or "no-call"))
        out.append({"table_va": hex(t), "class": cls, "cause": cause,
                    "extra": extra, "missing": lands})
    return out


DOC = "docs/NATIVE_CLASS_TREE.md"
DOC_START = "<!-- generated:class-tree -->"
DOC_END = "<!-- /generated:class-tree -->"


def render(out, agree, conflict, blank, causes):
    """The counts this page quotes, as one block built from the committed inventories."""
    named = [r for r in out if r["class"]]
    unnamed = [r for r in out if not r["class"]]
    tally = collections.Counter(r["verdict"].split(":")[0] for r in unnamed)
    lines = ["| measurement | value |",
             "|---|---|",
             "| tables | %d |" % len(out),
             "| named by the registration literal | %d |" % len(named),
             "| unnamed | %d |" % len(unnamed),
             "| named tables with a measured child set | %d |" % (agree + conflict),
             "| … whose child set equals the class's direct subclasses in `classes.csv` | **%d** |" % agree,
             "| … that disagree | **%d** |" % conflict,
             "| … whose class has no subclass in the image (a leaf) | %d |" % blank,
             "",
             "| conflict cause | tables |",
             "|---|---|"]
    for cause in ("no-script-row", "descendant-edge", "native-child", "foreign-child",
                  "missing-only"):
        lines.append("| `%s` | %d |" % (cause, causes.get(cause, 0)))
    lines += ["",
              "| the unnamed tables, by what the tree can say | tables |",
              "|---|---|",
              "| named exactly by their child set | %d |" % tally.get("candidate", 0),
              "| ambiguous (two classes match) | %d |" % tally.get("ambiguous", 0),
              "| partly named child set | %d |" % tally.get("unnamed-children", 0),
              "| unconstrained (no class's subclass set matches) | %d |" % tally.get("unconstrained", 0)]
    return "\n".join(lines)


def splice(path, block):
    """The page with its generated block replaced; read whole, so the check never writes."""
    with open(path) as fh:
        text = fh.read()
    if text.count(DOC_START) != 1 or text.count(DOC_END) != 1:
        raise SystemExit("%s: expected one %s / %s pair" % (path, DOC_START, DOC_END))
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + "\n" + DOC_END + tail


def doc_diff(page, new):
    """First differing line pair, so a stale block names itself."""
    with open(page) as fh:
        old_lines = fh.read().splitlines()
    new_lines = new.splitlines()
    for i, (a, b) in enumerate(zip(old_lines, new_lines)):
        if a != b:
            return "line %d: page %r, recomputed %r" % (i + 1, a, b)
    if len(old_lines) != len(new_lines):
        return "the page has %d lines, the recomputation %d" % (len(old_lines), len(new_lines))
    return ""


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
    # A second world: one table that wins an extra descendant, one that wins a stranger, and one
    # whose class the script tree does not have at all.
    ctors2 = [
        {"table_va": "0x1000", "function_va": "0x10", "call_before_store": "", "form": "ctor"},
        {"table_va": "0x2000", "function_va": "0x20", "call_before_store": "0x10", "form": "ctor"},
        {"table_va": "0x3000", "function_va": "0x30", "call_before_store": "0x10", "form": "ctor"},
        {"table_va": "0x4000", "function_va": "0x40", "call_before_store": "0x30", "form": "ctor"},
        {"table_va": "0x4100", "function_va": "0x50", "call_before_store": "0x40", "form": "ctor"},
    ]
    tables2 = {0x1000: 4, 0x2000: 4, 0x3000: 4, 0x4000: 4, 0x4100: 4}
    names2 = {0x1000: "Base", 0x2000: "Mid", 0x3000: "Leaf", 0x4000: "Odd", 0x4100: "Sub"}
    classes2 = {("P", "Base"): "", ("P", "Mid"): "Base", ("P", "Leaf"): "Mid",
                ("P", "Odd"): "Base"}
    causes = {r["class"]: r["cause"] for r in decompose(tables2, names2, classes2, ctors2)}
    assert causes == {"Base": "descendant-edge", "Leaf": "foreign-child",
                      "Odd": "native-child"}, causes
    block = render(out, _a, _c, _b, collections.Counter(
        r["cause"] for r in decompose(tables2, names2, classes2, ctors2)))
    assert "| tables | 3 |" in block and "| unnamed | 1 |" in block, block
    assert "| `descendant-edge` | 1 |" in block, block
    print("class-tree-edges: check ok (direction, candidate, three conflict causes, the block)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--csv", help="write one row per table here")
    ap.add_argument("--unnamed-only", action="store_true")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, writes nothing")
    ap.add_argument("--conflicts", action="store_true",
                    help="with the summary, say why each conflicting table's child set disagrees")
    ap.add_argument("--xbe", help="also link the unnamed registration sites through their pushed thunk")
    ap.add_argument("--write-doc", nargs="?", const=DOC, default=None,
                    help="render the generated count block into this page (%s)" % DOC)
    ap.add_argument("--check-doc", nargs="?", const=DOC, default=None,
                    help="recompute the block and fail if the page disagrees")
    ap.add_argument("--all-sites", action="store_true",
                    help="with --xbe, run the join over every registration site, not only the unnamed ones")
    args = ap.parse_args()
    if args.check:
        return check()
    tables, names, classes, ctors = load(args.root)
    out, agree, conflict, blank = analyse(tables, names, classes, ctors)
    if args.write_doc or args.check_doc:
        page = args.write_doc or args.check_doc
        causes = collections.Counter(r["cause"] for r in decompose(tables, names, classes, ctors))
        new = splice(page, render(out, agree, conflict, blank, causes))  # whole read, then write
        if args.check_doc:
            with open(page) as fh:
                same = fh.read() == new
            if not same:
                bad = doc_diff(page, new)
                print("FAIL class-tree-edges: %s is stale -- %s" % (page, bad))
                return 1
            print("class-tree-edges: %s reproduces (%d tables, %d unnamed)"
                  % (page, len(out), len(out) - len([r for r in out if r["class"]])))
            return 0
        with open(page, "w") as fh:
            fh.write(new)
        print("class-tree-edges: wrote %s" % page)
        return 0
    unnamed = [r for r in out if not r["class"]]
    print("tables %d, named %d, unnamed %d" % (len(out), len(out) - len(unnamed), len(unnamed)))
    print("named over a measured child set: %d agree, %d conflict, %d no subclass in the image"
          % (agree, conflict, blank))
    if args.conflicts:
        rows = decompose(tables, names, classes, ctors)
        counts = collections.Counter(r["cause"] for r in rows)
        print("conflicts by cause: " + ", ".join(
            "%s %d" % (c, counts[c]) for c in
            ("no-script-row", "descendant-edge", "foreign-child", "native-child",
             "missing-only") if counts[c]))
        for r in rows:
            print("  %s %-22s %-15s extra=%d %s" % (
                r["table_va"], r["class"], r["cause"], len(r["extra"]),
                "|".join(r["extra"])[:90] if r["extra"] else "-"))
            if r["missing"]:
                print("       missing: %s" % " ".join(r["missing"]))
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
        linked, blank = site_thunks(args.xbe, args.root, args.all_sites)
        own, other, missing = site_verdicts(linked, blank)
        where = "every registration site" if args.all_sites else \
            "registration sites with no table in the class map"
        print("%s: %d" % (where, len(linked) + blank))
        print("  the pushed constructor installs the site's own class table: %d" % len(own))
        print("  it installs another table (a shared default or a mixed window): %d" % len(other))
        print("  no constructor in the 96-byte window: %d" % len(missing))
        for row in linked:
            print("  %s %-22s push %s -> %s table %s %s%s" % (
                row["site_va"], row["class"], row["pushed"], row["constructor"],
                row["table_va"], row["table_class"],
                "" if row["class"] == row["table_class"] else "  <- not this site's class"))
    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, FIELDS)
            w.writeheader()
            w.writerows(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
