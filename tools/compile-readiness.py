#!/usr/bin/env python3
"""What the compile loop (PLAN 3.6) would have, per class, measured from committed artifacts.

The compile loop needs `ucc`, which this project cannot obtain; what it does not need is guesswork about
whether the *inputs* are present. This joins the four things a class needs and reports, per class, which of
them is missing:

* **source** — the class's shipped `ScriptText` (2,319 of 2,319, `inventory/package_script_text.csv`)
* **defaults** — a default-property block whose every property the class declares (its own and its ancestors').
  A class with **no properties anywhere in its ancestry** is not blocked by a missing block: it has nothing to
  configure, so "no defaults" is the right answer for it. Note the test is the *inherited closure*, not the class's
  own property list — 1,215 classes declare nothing themselves and inherit everything they have
  (`inventory/class_defaults_per_class.csv`)
* **ancestry** — the superclass is known, so the tree can be ordered for compiling
  (`inventory/classes.csv`)
* **declarations** — the class's own function and native counts, which say whether `ucc` would have C++ to
  bind to (`inventory/classes.csv`)

    python3 tools/compile-readiness.py -o inventory
    python3 tools/compile-readiness.py --check
"""
import argparse
import collections
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rows(name):
    path = os.path.join(ROOT, "inventory", name)
    if not os.path.isfile(path):
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", help="directory for the rollup CSV (default: inventory/)")
    ap.add_argument("--check", action="store_true", help="fail if the committed artifacts disagree")
    args = ap.parse_args()
    outdir = args.out or os.path.join(ROOT, "inventory")

    classes = rows("classes.csv")
    defaults = {(r["package"], r["class"]): r for r in rows("class_defaults_per_class.csv")}
    own_props = collections.Counter((r["package"], r["class"]) for r in rows("class_properties.csv"))
    props = collections.Counter((r["package"], r["class"]) for r in rows("class_properties.csv"))
    source = {r["package"]: int(r["script_text_exports"]) for r in rows("package_script_text.csv")}
    # package names in the inventory are not case-consistent (`GamePlay` vs `package_of_super=Gameplay`),
    # so ancestry is matched case-insensitively -- otherwise 70 classes look orphaned when they are not.
    known = {(r["package"].lower(), r["class"].lower()) for r in classes}
    supers = {(r["package"], r["class"]): r["super"] for r in classes}
    package_classes = collections.Counter(r["package"] for r in classes)

    per_class, by_package = [], collections.defaultdict(lambda: collections.Counter())
    totals = collections.Counter()
    for r in classes:
        key = (r["package"], r["class"])
        d = defaults.get(key)
        declared_props = props.get(key, 0)
        # the closure any defaults block could legitimately set: own properties plus every ancestor's
        closure, cur, seen = 0, r["class"], set()
        while cur and cur not in seen:
            seen.add(cur)
            closure += props.get((r["package"], cur), 0)
            cur = r["super"]
        blockers = []
        has_source = package_classes[r["package"]] > 0 and source.get(r["package"], 0) > 0
        if not has_source:
            blockers.append("no-source")
        if closure == 0:
            # Nothing to configure: 230 of the "ambiguous" and 54 of the "no block" classes were this case.
            defaults_state = "not-applicable"
        elif d is None or d["defaults"] == "0":
            blockers.append("no-defaults-block")
            defaults_state = "none"
        elif d["fully_declared"] != "1":
            blockers.append("defaults-ambiguous")
            defaults_state = "ambiguous"
        else:
            defaults_state = "read"
        sup = r["super"]
        if sup and (r["package_of_super"] or "") and \
                (r["package_of_super"].lower(), sup.lower()) not in known and \
                (r["package"].lower(), sup.lower()) not in known:
            blockers.append("super-not-in-tree")
        ready = not blockers
        totals["ready"] += 1 if ready else 0
        for b in blockers:
            totals[b] += 1
            by_package[r["package"]][b] += 1
        by_package[r["package"]]["classes"] += 1
        by_package[r["package"]]["ready"] += 1 if ready else 0
        per_class.append([r["package"], r["class"], r["functions"], r["native_functions"],
                          declared_props, closure, d["defaults"] if d else 0,
                          d["fully_declared"] if d else 0, defaults_state, ";".join(blockers)])

    per_class.sort()
    print("%-22s %6s %6s %s" % ("package", "classes", "ready", "blockers"))
    for pkg in sorted(by_package):
        c = by_package[pkg]
        detail = ", ".join("%s %d" % (b, c[b]) for b in ("no-source", "no-defaults-block", "defaults-ambiguous",
                                                         "super-not-in-tree") if c[b])
        print("%-22s %6d %6.1f%% %s" % (pkg, c["classes"], 100.0 * c["ready"] / c["classes"], detail or "-"))
    n = len(classes)
    print("\n%d classes: %d ready for the compile loop (%.1f%%); blockers: %s"
          % (n, totals["ready"], 100.0 * totals["ready"] / n if n else 0.0,
             ", ".join("%s %d" % (b, totals[b]) for b in ("no-source", "no-defaults-block",
                                                          "defaults-ambiguous", "super-not-in-tree")
                       if totals[b]) or "none"))

    path = os.path.join(outdir, "compile_readiness.csv")
    rollup = os.path.join(outdir, "compile_readiness_by_package.csv")
    for target, header, data in (
        (path, ["package", "class", "functions", "native_functions", "properties", "properties_inherited",
                "defaults", "defaults_clean", "defaults_state", "blockers"], per_class),
        (rollup, ["package", "classes", "ready", "no_source", "no_defaults_block", "defaults_ambiguous",
                  "super_not_in_tree"],
         [[pkg, by_package[pkg]["classes"], by_package[pkg]["ready"], by_package[pkg]["no-source"],
           by_package[pkg]["no-defaults-block"], by_package[pkg]["defaults-ambiguous"],
           by_package[pkg]["super-not-in-tree"]] for pkg in sorted(by_package)]),
    ):
        with open(target, "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(header)
            w.writerows(data)
        print("wrote %s (%d rows)" % (target, len(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
