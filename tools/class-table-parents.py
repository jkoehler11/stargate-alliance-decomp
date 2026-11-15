#!/usr/bin/env python3
"""Name a class table from the *callers* of the function that installs it.

`inventory/xbe_class_table_names.csv` names 466 of the 492 per-class tables from the registration
literal, and `tools/class-tree-edges.py` names three more from the constructor tree. Twenty-six stay
unnamed (`docs/UNKNOWNS.md` U8), and the registration route is measured out: 25 of the 27 unnamed
sites push one shared nine-byte default constructor, so the literal at a site cannot name a table
(`tools/ctor-argument.py`).

This reads a different edge. `inventory/class_constructors.csv` already says which function installs
which table (`store_va`, `function_va`); a table's installer is that class's own constructor or
destructor, and MSVC makes every subclass's constructor and destructor **call** it before installing
their own. So the direct callers of an installer are the classes just below it:

* an installer that is itself a `ctor_X`/`dtor_X` symbol names its own table (`own-installer`);
* an unnamed installer names the *ancestor* of its callers -- the deepest class that is a proper
  ancestor of every named caller (`lca`), which is exactly the class whose constructor the stubs call.

The rule is checked against the 466 tables the registration already names, which is what makes it
evidence: an agreement is a reproduction, and a disagreement is reported as `adjacent` (the proposal
is the committed name's parent or child -- the one level a generated stub skips) rather than hidden.

    tools/class-table-parents.py DUMP/default.xbe --csv inventory/class_table_parents.csv
    tools/class-table-parents.py DUMP/default.xbe          # summary + the unnamed tables
    tools/class-table-parents.py --write-doc               # render the page's count block (no dump)
    tools/class-table-parents.py --check-doc               # recompute it and fail if the page is stale
    tools/class-table-parents.py --check                   # synthetic self-test, no dump, no write

Addresses and class names only, the same shape as `inventory/xbe_class_table_names.csv`; no package
or image content is written.
"""

import argparse
import bisect
import collections
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS = ["table_va", "class_committed", "proposed_class", "basis", "named_callers",
          "callers", "support", "verdict"]
NAMED = ("ctor_", "dtor_")


def load_inventories(root):
    """Everything this needs that is committed: names, installers, script supers, slot counts."""
    tables = {}
    with open(os.path.join(root, "inventory/xbe_class_tables.csv"), newline="") as f:
        for row in csv.DictReader(f):
            tables[int(row["table_va"], 16)] = int(row["slots"])
    names = {}
    with open(os.path.join(root, "inventory/xbe_class_table_names.csv"), newline="") as f:
        for row in csv.DictReader(f):
            names[int(row["table_va"], 16)] = (row.get("class") or "").strip()
    sup_of = {}
    with open(os.path.join(root, "inventory/classes.csv"), newline="") as f:
        for row in csv.DictReader(f):
            sup_of[row["class"]] = row.get("super") or ""
    installers = collections.defaultdict(set)
    with open(os.path.join(root, "inventory/class_constructors.csv"), newline="") as f:
        for row in csv.DictReader(f):
            installers[int(row["table_va"], 16)].add(int(row["function_va"], 16))
    return tables, names, sup_of, installers


def load_functions(path):
    """VA -> (name, size). The proof that an address is a function, and the names that carry a class."""
    out = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            va = row.get("address") or row.get("va") or ""
            if not va:
                continue
            try:
                out[int(va, 16)] = (row.get("name", ""), int(row.get("size") or 0))
            except ValueError:
                continue
    return out


def code_section(image, functions):
    """The section holding the most known function starts; section flags cannot tell code from data."""
    best, best_hits = None, -1
    for sec in image.sections:
        hits = sum(1 for va in functions if sec.contains(va))
        if hits > best_hits:
            best, best_hits = sec, hits
    return best


def call_sites(image, functions):
    """target VA -> every `E8 rel32` call site that reaches it (one pass over the code section)."""
    sec = code_section(image, functions)
    data = image.read_va(sec.va, sec.vsize)
    out = collections.defaultdict(list)
    for i in range(len(data) - 4):
        if data[i] != 0xE8:
            continue
        rel = struct.unpack_from("<i", data, i + 1)[0]
        out[sec.va + i + 5 + rel].append(sec.va + i)
    return out


def containing(va, starts, functions):
    """(name, offset) of the function holding `va`, or None."""
    i = bisect.bisect_right(starts, va) - 1
    if i < 0:
        return None
    start = starts[i]
    name, size = functions[start]
    if size and va < start + size:
        return name, va - start
    return None


def symbol_class(name):
    """`ctor_Foo`/`dtor_Foo` -> Foo; anything else -> ''."""
    for prefix in NAMED:
        if name.startswith(prefix):
            return name[len(prefix):]
    return ""


def chain(cls, sup_of):
    """[cls, parent, grandparent, ...] as far as `classes.csv` knows the names."""
    out = [cls]
    while True:
        parent = sup_of.get(out[-1])
        if not parent or parent in out:
            return out
        out.append(parent)


def decide(installer_names, caller_classes, sup_of):
    """(proposed_class, basis, support) for one table. Pure: the self-test drives this alone."""
    own = sorted({symbol_class(n) for n in installer_names if symbol_class(n)})
    if len(own) == 1:
        return own[0], "own-installer", len([n for n in installer_names if symbol_class(n)])
    callers = sorted(set(caller_classes))
    if not callers:
        return "", "no-named-call", 0
    # a caller with no row in classes.csv has no super to read: it bounds the table from below and
    # nothing more, so it is left out of the intersection rather than allowed to empty it.
    known = [c for c in callers if c in sup_of]
    if not known:
        return "", "parent-of-callers", len(callers)
    common = None
    for cls in known:
        anc = set(chain(cls, sup_of))
        common = anc if common is None else (common & anc)
    cands = [c for c in (common or ()) if c not in known]
    if not cands:
        return "", "parent-of-callers", len(known)
    # deepest proper ancestor of every caller: the class whose constructor the stubs call
    best = max(cands, key=lambda c: (len(chain(c, sup_of)), c))
    support = sum(1 for c in callers if best in chain(c, sup_of))
    return best, "lca", support


def verdict_for(committed, proposed, basis, sup_of):
    if not proposed:
        return "constrained" if basis == "parent-of-callers" else "no-caller"
    if not committed:
        return "unnamed->proposed"
    if committed == proposed:
        return "agrees"
    if proposed in chain(committed, sup_of) or committed in chain(proposed, sup_of):
        return "adjacent"
    return "differs"


def run(dump, csv_out):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import xbe

    tables, names, sup_of, installers = load_inventories(ROOT)
    functions = load_functions(os.path.join(ROOT, "symbols/xbox_functions.csv"))
    starts = sorted(functions)
    image = xbe.Xbe(dump)
    calls = call_sites(image, functions)

    committed_of = {name: table for table, name in names.items() if name}
    rows = []
    for table in sorted(tables):
        fns = sorted(installers.get(table, ()))
        installer_names = [functions[f][0] for f in fns if f in functions]
        caller_classes, caller_names = [], []
        for fn in fns:
            for site in calls.get(fn, ()):
                hit = containing(site, starts, functions)
                if not hit:
                    continue
                caller_names.append(hit[0])
                cls = symbol_class(hit[0])
                if cls:
                    caller_classes.append(cls)
        proposed, basis, support = decide(installer_names, caller_classes, sup_of)
        verdict = verdict_for(names.get(table, ""), proposed, basis, sup_of)
        other = committed_of.get(proposed) if proposed else None
        if verdict == "unnamed->proposed" and other is not None and other != table:
            # the registration already gives this name to another table: report the clash, do not
            # silently name two tables the same class
            verdict = "name-clash:%s" % hex(other)
        rows.append({
            "table_va": hex(table),
            "class_committed": names.get(table, ""),
            "proposed_class": proposed,
            "basis": basis,
            "named_callers": "|".join(sorted(set(caller_classes))),
            "callers": len(caller_names),
            "support": support,
            "verdict": verdict,
        })

    if csv_out:
        with open(csv_out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
    return rows


def summarise(rows):
    count = collections.Counter(r["verdict"] for r in rows)
    named = [r for r in rows if r["class_committed"]]
    print("tables %d, named %d, unnamed %d" % (len(rows), len(named), len(rows) - len(named)))
    print("verdicts: " + ", ".join("%s %d" % (k, v) for k, v in sorted(count.items())))
    unnamed = [r for r in rows if not r["class_committed"]]
    print("\nunnamed tables (%d):" % len(unnamed))
    for r in unnamed:
        print("  %s -> %-22s %-18s support %d/%d callers  %s"
              % (r["table_va"], r["proposed_class"] or "-", r["basis"], r["support"],
                 r["callers"], r["named_callers"][:70]))
    odd = [r for r in rows if r["verdict"] in ("differs", "adjacent")]
    print("\nnamed tables the rule does not reproduce (%d):" % len(odd))
    for r in odd[:40]:
        print("  %s committed %-26s rule %-26s %s"
              % (r["table_va"], r["class_committed"], r["proposed_class"] or "-", r["verdict"]))


def self_test():
    """Synthetic worlds: the four decide() outcomes and the adjacent verdict. No dump, no write."""
    sup = {"Actor": "Object", "Pawn": "Actor", "PlayerPawn": "Pawn", "Vehicle": "Pawn",
           "Info": "Actor", "Material": "Object", "Shader": "Material", "Texture": "Object",
           "Surface": "Frame"}
    checks = []
    # an installer that is itself a named constructor names its own table
    checks.append(("own", decide(["dtor_Shader"], [], sup), ("Shader", "own-installer")))
    # two sibling ctors call one unnamed installer: the table is their parent
    got = decide([], ["PlayerPawn", "Vehicle"], sup)
    checks.append(("lca-two", got, ("Pawn", "lca")))
    # one caller: the deepest proper ancestor, not the caller itself
    checks.append(("lca-one", decide([], ["Vehicle"], sup), ("Pawn", "lca")))
    # unrelated callers share no ancestor below the roots -- reported, not guessed
    got = decide([], ["Vehicle", "Surface"], sup)
    checks.append(("split", got, ("", "parent-of-callers")))
    # nothing named reaches it
    checks.append(("silent", decide([], [], sup), ("", "no-named-call")))
    # an unnamed table that the rule names, and one level of stub skip on a named table
    checks.append(("unnamed", verdict_for("", "Pawn", "lca", sup), "unnamed->proposed"))
    checks.append(("agrees", verdict_for("Pawn", "Pawn", "lca", sup), "agrees"))
    checks.append(("adjacent", verdict_for("PlayerPawn", "Pawn", "lca", sup), "adjacent"))
    checks.append(("differs", verdict_for("Texture", "Pawn", "lca", sup), "differs"))
    checks.append(("constrained", verdict_for("", "", "parent-of-callers", sup), "constrained"))
    def shape(got):
        return got[:2] if isinstance(got, tuple) else got

    bad = [(n, g, w) for n, g, w in checks if shape(g) != w]
    for n, g, w in checks:
        print("  %-10s %-34s %s" % (n, str(g), "ok" if shape(g) == w else "FAIL want %s" % (w,)))
    stale = committed_names_join(ROOT)
    if stale:
        bad.append(("committed-names", stale[:1], "no stale rows"))
    # the generated block is the page's counts: a row that moves the tally must move it here
    block = render([
        {"table_va": "0x1000", "class_committed": "A", "proposed_class": "A", "basis": "own-installer",
         "named_callers": "", "callers": "1", "support": "1", "verdict": "agrees"},
        {"table_va": "0x2000", "class_committed": "B", "proposed_class": "C", "basis": "own-installer",
         "named_callers": "", "callers": "2", "support": "1", "verdict": "adjacent"},
        {"table_va": "0x3000", "class_committed": "", "proposed_class": "", "basis": "no-named-call",
         "named_callers": "", "callers": "0", "support": "0", "verdict": "no-caller"},
    ])
    if "| `agrees` -- the installer's symbol names the committed class | **1** |" not in block \
            or "| `0x2000` | B | C | `own-installer` | `adjacent` |" not in block:
        bad.append(("render", block, "one agree, one adjacent row"))
    if bad:
        print("%d self-test(s) failed" % len(bad))
        return 1
    print("self-test ok: %d cases" % len(checks))
    return 0


def committed_names_join(root):
    """The committed artifact's name column against the class map it was generated from.

    `class_committed` is a copy of `inventory/xbe_class_table_names.csv`, and nothing compares the two
    after the fact: when that map is regenerated behind a better naming rule, this artifact keeps the old
    names and its verdicts grade the new rule against them. Measured 2026-11-15: five rows were one
    revision stale, and the two `name-clash` verdicts they produced were the stale column, not the image.
    """
    names, committed = {}, {}
    with open(os.path.join(root, "inventory/xbe_class_table_names.csv"), newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                names[int(row["table_va"], 16)] = (row.get("class") or "").strip()
            except ValueError:
                continue
    with open(os.path.join(root, "inventory/class_table_parents.csv"), newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                committed[int(row["table_va"], 16)] = (row.get("class_committed") or "").strip()
            except ValueError:
                continue
    differ = sorted((va, committed.get(va, ""), name) for va, name in names.items()
                    if committed.get(va, "") != name)
    print("  %-10s %-34s %s" % ("names-join", "%d of %d rows" % (len(names) - len(differ), len(names)),
                                "ok" if not differ else "FAIL %d stale" % len(differ)))
    for va, was, now in differ[:5]:
        print("      0x%08x committed %-20s map %s" % (va, was or "-", now or "-"))
    return differ


DOC = "docs/NATIVE_CLASS_TREE.md"
DOC_START = "<!-- generated:class-table-parents -->"
DOC_END = "<!-- /generated:class-table-parents -->"


def render(rows):
    """The verdict counts this page quotes, and every named row the rule does not reproduce."""
    count = collections.Counter(r["verdict"].split(":")[0] for r in rows)
    named = [r for r in rows if r["class_committed"]]
    lines = ["| measurement | value |",
             "|---|---|",
             "| tables | %d |" % len(rows),
             "| named in `inventory/xbe_class_table_names.csv` | %d |" % len(named),
             "| unnamed | %d |" % (len(rows) - len(named)),
             "| `agrees` -- the installer's symbol names the committed class | **%d** |" % count.get("agrees", 0),
             "| `adjacent` -- the symbol names a parent or child | %d |" % count.get("adjacent", 0),
             "| `differs` -- the symbol names an unrelated class | %d |" % count.get("differs", 0),
             "| `unnamed->proposed` | %d |" % count.get("unnamed->proposed", 0),
             "| `constrained` -- the callers share no named ancestor | %d |" % count.get("constrained", 0),
             "| `name-clash` -- the proposal is another table's name | %d |" % count.get("name-clash", 0),
             "| `no-caller` -- no `E8` site reaches the installer | %d |" % count.get("no-caller", 0)]
    odd = [r for r in named if r["verdict"] != "agrees"]
    if odd:
        lines += ["",
                  "| table | committed | the rule's answer | basis | verdict |",
                  "|---|---|---|---|---|"]
        for r in odd:
            lines.append("| `%s` | %s | %s | `%s` | `%s` |"
                         % (r["table_va"], r["class_committed"], r["proposed_class"] or "-",
                            r["basis"], r["verdict"]))
    return "\n".join(lines)


def splice(page, block):
    """The page with its generated block replaced; read whole, so the check never writes."""
    with open(page) as fh:
        text = fh.read()
    if text.count(DOC_START) != 1 or text.count(DOC_END) != 1:
        raise SystemExit("%s: expected one %s / %s pair" % (page, DOC_START, DOC_END))
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + "\n" + DOC_END + tail


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("xbe", nargs="?", help="DUMP/default.xbe")
    ap.add_argument("--csv", help="write one row per class table")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump")
    ap.add_argument("--write-doc", nargs="?", const=DOC, default=None,
                    help="render the generated verdict block into this page (%s)" % DOC)
    ap.add_argument("--check-doc", nargs="?", const=DOC, default=None,
                    help="recompute the block from the committed artifact and fail if the page disagrees")
    args = ap.parse_args()
    if args.check:
        return self_test()
    if args.write_doc or args.check_doc:
        page = args.write_doc or args.check_doc
        path = args.csv or os.path.join(ROOT, "inventory/class_table_parents.csv")
        rows = read_csv(path)
        new = splice(page, render(rows))          # whole read, then write
        if args.check_doc:
            with open(page) as fh:
                if fh.read() != new:
                    print("FAIL class-table-parents: %s is stale against %s" % (page, path))
                    return 1
            print("class-table-parents: %s reproduces (%d rows)" % (page, len(rows)))
            return 0
        with open(page, "w") as fh:
            fh.write(new)
        print("class-table-parents: wrote %s" % page)
        return 0
    if not args.xbe:
        ap.error("default.xbe is required unless --check")
    summarise(run(args.xbe, args.csv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
