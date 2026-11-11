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
    if bad:
        print("%d self-test(s) failed" % len(bad))
        return 1
    print("self-test ok: %d cases" % len(checks))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("xbe", nargs="?", help="DUMP/default.xbe")
    ap.add_argument("--csv", help="write one row per class table")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump")
    args = ap.parse_args()
    if args.check:
        return self_test()
    if not args.xbe:
        ap.error("default.xbe is required unless --check")
    summarise(run(args.xbe, args.csv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
