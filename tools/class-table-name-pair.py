#!/usr/bin/env python3
"""Pair a class name to a class table from the tree, for the tables no registration literal names.

`inventory/xbe_class_table_names.csv` names 467 of the image's 492 packed class tables from the literal
a registration site pushes. Twenty-five sites push the shared default constructor `0x00034650`, so their
literals (`Subsystem`, `Field`, `Property`, `Channel`, ...) never reach a table, and the 24 tables those
classes own stay addresses. `docs/UNKNOWNS.md` U8 leaves the next read as "pair the literal to the
position", and this tool is that read.

Two rules do the pairing, and both are measured on the tables that are *already* named before they are
applied to the ones that are not:

* `child-super` -- a named child table's class declares a script super in `inventory/classes.csv`; a table
  whose children all declare the same super is that super's table. **85 of 85** on the named population,
  no counter-example.
* `parent-super` -- the candidate whose own script super is the name of the table's measured parent, when
  exactly one candidate has that super. **29 of 29** where it leaves a single candidate; it leaves one on
  29 of 389 parented tables, so it is precise and rare.

A third rule is measured and **rejected as a namer**: every named child ending in the same candidate
(`...Property`, `...Texture`) identifies the family *ancestor*, not the table -- it answers `Info` for
`ZoneInfo`, `Emitter` for `ParticleEmitter`, `Texture` for `FractalTexture` and `Material` for
`RenderedMaterial`, so it is 10 of 16 as a name. Its answer is recorded as `family` on the row and used
only to order the candidates a table still has.

What the rules cannot reach is recorded as a hint or as open, with the measured reason -- not guessed:

    python3 tools/class-table-name-pair.py --report
    python3 tools/class-table-name-pair.py -o inventory
    python3 tools/class-table-name-pair.py --check     # in-memory fixtures, no dump, writes nothing

Committed CSVs only: no dump, no decompiled output, addresses and class names only.
"""
import argparse
import collections
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS = ["table_va", "class", "basis", "support", "family", "candidates", "site_va", "site_size",
          "children", "verdict"]
CENSUS = ("census", "candidates", "tables", "paired", "hint", "open", "child_super_precision",
          "parent_super_precision", "family_precision", "unassigned_by_rule")
ARTIFACT = os.path.join(ROOT, "inventory/class_table_name_pairs.csv")


def va(text):
    try:
        return int(text, 16)
    except (TypeError, ValueError):
        return None


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def load(root=ROOT):
    """The four committed artifacts, as tables keyed by integer VA."""
    names = {}
    for row in read_csv(os.path.join(root, "inventory/xbe_class_table_names.csv")):
        key = va(row["table_va"])
        if key is not None:
            names[key] = row["class"]
    parent, children = {}, collections.defaultdict(list)
    for row in read_csv(os.path.join(root, "inventory/class_table_parent_edges.csv")):
        key = va(row["table_va"])
        if key is None:
            continue
        up = va(row["parent_va"])
        parent[key] = up
        if up is not None:
            children[up].append(key)
    supers = {}
    for row in read_csv(os.path.join(root, "inventory/classes.csv")):
        if row.get("super"):
            supers[row["class"]] = row["super"]
    sites = {}
    for row in read_csv(os.path.join(root, "inventory/class_registrations.csv")):
        if row["verdict"] == "shared-thunk":
            sites.setdefault(row["class_literal"], []).append(row)
    return names, parent, children, supers, sites


def candidates(names, sites):
    """The site literals no table carries yet: the names still to pair."""
    taken = {name for name in names.values() if name}
    return {literal: rows for literal, rows in sites.items() if literal not in taken}


def child_super(table, names, children, supers):
    votes = collections.Counter()
    for child in children.get(table, ()):
        name = names.get(child)
        if name and supers.get(name):
            votes[supers[name]] += 1
    return votes


def parent_super(table, names, parent, supers, pool):
    up = names.get(parent.get(table) or 0)
    if not up:
        return []
    return [name for name in pool if supers.get(name) == up]


def family(table, names, children, pool):
    """The candidate every named child contains: the family ancestor, not the table's own name."""
    kids = [names[c] for c in children.get(table, ()) if names.get(c)]
    if len(kids) < 2:
        return "", kids
    fit = [name for name in pool
           if len(name) >= 4 and all(name != kid and name in kid for kid in kids)]
    return (sorted(fit, key=len)[-1] if fit else ""), kids


def child_super_precision(names, children, supers, pool):
    """Every table the rule fires on, and whether it answered that table's committed name."""
    fired = agreed = 0
    for table, name in names.items():
        if not name:
            continue
        votes = collections.Counter({k: v for k, v in child_super(table, names, children, supers).items()
                                     if k in pool or pool is None})
        if not votes:
            continue
        top = votes.most_common()
        if len(top) > 1 and top[0][1] == top[1][1]:
            continue
        fired += 1
        agreed += top[0][0] == name
    return agreed, fired


def parent_super_precision(names, parent, supers):
    fired = agreed = 0
    for table, name in names.items():
        if not name:
            continue
        up = names.get(parent.get(table) or 0)
        if not up:
            continue
        fit = [other for other in names.values() if other and supers.get(other) == up]
        if len(fit) != 1:
            continue
        fired += 1
        agreed += fit[0] == name
    return agreed, fired


def family_precision(names, children):
    fired = agreed = 0
    for table, name in names.items():
        if not name:
            continue
        anchor, kids = family(table, names, children, set(names.values()) - {None})
        if not anchor:
            continue
        fired += 1
        agreed += anchor == name
    return agreed, fired


def run(root=ROOT):
    names, parent, children, supers, sites = load(root)
    pool = candidates(names, sites)
    labels = {table: name for table, name in names.items() if name}
    basis = {}
    pending = [table for table, name in names.items() if not name]

    # Fixpoint: child-super first (85/85), then parent-super on what the new labels make answerable.
    changed = True
    while changed:
        changed = False
        for table in pending:
            if table in labels:
                continue
            votes = collections.Counter({k: v for k, v in child_super(table, names, children, supers).items()
                                         if k in pool})
            top = votes.most_common()
            if top and (len(top) == 1 or top[0][1] > top[1][1]):
                labels[table] = top[0][0]
                basis[table] = ("child-super", "%d child table(s) declare super %s"
                                % (top[0][1], top[0][0]))
                changed = True
                continue
            fit = parent_super(table, names, parent, supers, pool)
            if len(fit) == 1:
                labels[table] = fit[0]
                basis[table] = ("parent-super", "script super of %s is its parent table %s"
                                % (fit[0], names.get(parent.get(table) or 0, ())))
                changed = True

    rows = []
    paired = hint = 0
    for table in sorted(pending):
        name = labels.get(table, "")
        anchor, kids = family(table, names, children, set(pool) | set(labels.values()))
        votes, why = [], []
        for other, count in collections.Counter(
                {k: v for k, v in child_super(table, names, children, supers).items()}).most_common():
            votes.append("%s:%d" % (other, count))
        for other in parent_super(table, names, parent, supers, pool):
            votes.append("%s:parent" % other)
        if name:
            verdict = "paired"
            paired += 1
        elif anchor and anchor in pool:
            verdict = "hint"
            hint += 1
        else:
            verdict = "open"
        if verdict == "hint" and anchor not in votes:
            votes.insert(0, "%s:family" % anchor)
        site_rows = sites.get(name) or ([sites[anchor][0]] if verdict == "hint" and anchor in sites else [])
        row_basis, row_support = basis.get(table, ("", ""))
        if not row_basis and verdict == "hint":
            row_basis, row_support = "family", "every named child contains %s" % anchor
        row = {
            "table_va": "0x%08x" % table,
            "class": name,
            "basis": row_basis,
            "support": row_support,
            "family": anchor,
            "candidates": ";".join(votes),
            "site_va": site_rows[0]["site_va"] if site_rows else "",
            "site_size": site_rows[0]["object_size"] if site_rows else "",
            "children": "|".join(kids),
            "verdict": verdict,
        }
        rows.append(row)

    a_ok, a_fired = child_super_precision(names, children, supers, set(names.values()) - {None})
    b_ok, b_fired = parent_super_precision(names, parent, supers)
    f_ok, f_fired = family_precision(names, children)
    census = {
        "table_va": "census", "class": "",
        "basis": "candidates=%d tables=%d paired=%d hint=%d open=%d" % (len(pool), len(pending), paired,
                                                                       hint, len(pending) - paired - hint),
        "support": "child-super %d/%d parent-super %d/%d family %d/%d" % (a_ok, a_fired, b_ok, b_fired,
                                                                         f_ok, f_fired),
        "family": "", "candidates": "", "site_va": "", "site_size": "", "children": "",
        "verdict": "census",
    }
    return rows, census, dict(pool=pool, pending=pending, paired=paired, hint=hint,
                              a=(a_ok, a_fired), b=(b_ok, b_fired), f=(f_ok, f_fired),
                              labels=labels, sites=sites)


def report(result):
    rows, census, meta = result
    print("%d candidates for %d tables" % (len(meta["pool"]), len(meta["pending"])))
    print("rule precision: child-super %d/%d, parent-super %d/%d, family %d/%d"
          % (meta["a"] + meta["b"] + meta["f"]))
    for row in rows:
        print("%s %-20s %-8s %-13s %s" % (row["table_va"], row["class"] or "-", row["verdict"],
                                          row["family"] or "-", row["candidates"][:90]))


def write(out_dir, result):
    rows, census, _meta = result
    path = os.path.join(out_dir, "class_table_name_pairs.csv")
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        writer.writerow(census)
    return path


def check():
    """Fixtures with no dump: the two rules, the family ancestor, and the ties that forbid a pairing."""
    names = {0x10: "Object", 0x20: "Material", 0x21: "RenderedMaterial", 0x22: "BitmapMaterial",
             0x30: "", 0x40: "", 0x41: ""}
    parent = {0x20: 0x10, 0x21: 0x20, 0x22: 0x21, 0x30: 0x20, 0x40: 0x20, 0x41: 0x10}
    children = collections.defaultdict(list)
    for table, up in parent.items():
        children[up].append(table)
    supers = {"Object": "", "Material": "Object", "RenderedMaterial": "Material",
              "BitmapMaterial": "RenderedMaterial", "Shader": "RenderedMaterial",
              "Combiner": "Material", "CameraEffect": "Object", "Field": "Object"}
    pool = {"RenderedMaterial", "Field", "CameraEffect", "Shader", "Combiner"}

    ok = True

    def want(label, cond):
        nonlocal ok
        print("%-4s %s" % ("ok" if cond else "FAIL", label))
        ok = ok and cond

    # a child whose class declares a script super names its parent table
    named_children = {0x30: "Shader", 0x40: "Combiner"}
    votes = collections.Counter(child_super(0x20, named_children, children, supers))
    want("child-super votes RenderedMaterial for Material's table",
         votes["RenderedMaterial"] == 1 and votes["Material"] == 1)
    # a child with no script super votes for nothing rather than for a guess
    want("child-super is silent on an undeclared child",
         not child_super(0x21, {0x22: "BitmapMaterial"}, children, {}))
    # parent-super: the unique candidate whose own super is the parent table's name
    want("parent-super finds the one candidate under Object",
         parent_super(0x41, {0x10: "Object"}, parent, {"CameraEffect": "Object"}, {"CameraEffect"})
         == ["CameraEffect"])
    want("parent-super refuses a tie",
         len(parent_super(0x41, {0x10: "Object"}, parent,
                          {"CameraEffect": "Object", "Field": "Object"},
                          {"CameraEffect", "Field"})) == 2)
    want("parent-super needs a named parent",
         parent_super(0x30, {}, parent, {"Shader": "RenderedMaterial"}, {"Shader"}) == [])
    # family answers the ancestor, which is why it is not a name
    anchor, kids = family(0x20, {0x21: "RenderedTexture", 0x40: "BitmapTexture"},
                          {0x20: [0x21, 0x40]}, {"Texture", "Object"})
    want("family answers the ancestor, not the table", anchor == "Texture" and len(kids) == 2)
    want("family needs two named children",
         family(0x21, {0x22: "BitmapMaterial"}, children, {"Material"}) == ("", ["BitmapMaterial"]))
    # the tie rule itself: two equal votes must not pair
    votes = collections.Counter({"CameraEffect": 2, "Field": 2})
    top = votes.most_common()
    want("an equal top vote does not pair", not (top and (len(top) == 1 or top[0][1] > top[1][1])))
    print("check: %s" % ("pass" if ok else "fail"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out-dir", help="write inventory/class_table_name_pairs.csv here")
    ap.add_argument("--report", action="store_true", help="print the pairing to stdout")
    ap.add_argument("--check", action="store_true", help="synthetic fixtures, no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        return check()
    result = run()
    if args.report or not args.out_dir:
        report(result)
    if args.out_dir:
        print("wrote %s" % write(args.out_dir, result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
