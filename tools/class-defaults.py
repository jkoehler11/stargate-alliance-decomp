#!/usr/bin/env python3
"""Resolve a class's default-property block by what the class declares, not just by where the block ends.

The previous attempt (`docs/CLASS_DEFAULTS.md`) located the block by the boundary rule alone and hit an
ambiguity: two candidate starts two bytes apart both end at the export end, so `Description` decoded as
`Str BeginState`. The class table already knows which properties a class declares
(`inventory/class_properties.csv`), so the ambiguity can be settled semantically: a candidate whose property
names are all declared by the class (or inherited from its supers) is the block, and one that invents a name
like `BeginState` is not.

    python3 tools/class-defaults.py DUMP/System --class SGOrder
    python3 tools/class-defaults.py DUMP/System --sweep -o inventory
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_prop_stream():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prop-stream.py")
    spec = importlib.util.spec_from_file_location("prop_stream", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def super_map(path=os.path.join(ROOT, "inventory", "classes.csv")):
    """class -> superclass, taken from the whole tree rather than one package.

    Following supers within the package being read stops at the first class that extends a class from
    another package (StargateGame classes extend Engine ones), which truncated the inherited property set and
    made correct blocks look undeclared.
    """
    out = {}
    if os.path.isfile(path):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                out[r["class"]] = r["super"]
    return out


def declared_properties(path=os.path.join(ROOT, "inventory", "class_properties.csv")):
    """class -> set of property names it declares, plus the same set for all its ancestors."""
    by_class = collections.defaultdict(set)
    if not os.path.isfile(path):
        return by_class
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            by_class[r["class"]].add(r["property"])
    return by_class


def candidates(pkg, mod, exp, limit=None):
    """Every offset where a property block parses, bounded by the export's own size.

    The bound used to be a fixed 512 bytes, and that is what made the classes with the most defaults look
    like classes with none: a class export carries its record (script tables, super, dependencies) *before*
    the defaults block, so `Actor` (record 2,451 bytes), `Pawn` (3,992), `PlayerController` (13,665) and
    `GameInfo` (5,682) all began their block past the window and were reported as "no block". Scanning the
    whole payload recovers 114 of the 532 withheld classes, and those are precisely the base classes.
    """
    data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    out = []
    if limit is None:
        limit = len(data)
    for start in range(0, min(len(data), limit)):
        try:
            props, end, reason = mod.read_block(data[start:], pkg, False)
        except Exception:
            continue
        if reason != "None" or len(props) < 2:
            continue
        if any(p["name"] == "?" for p in props):
            continue
        if start + end > len(data):
            continue
        trailer = len(data) - (start + end)
        if trailer > 64:
            # A class export can carry data after its defaults (a component table, for one). Requiring an
            # exact ending threw away every such class; a bounded trailer keeps them.
            continue
        out.append((len(props), start, [p["name"] for p in props], trailer))
    return out, data


def diagnose(pkg, mod, exp):
    """Why a class has no usable block: the question `docs/CLASS_DEFAULTS.md` left as "not measured".

    `candidates()` returns only survivors, so a miss told us nothing. This walks the same offsets and classifies the
    closest attempt: no block parses at all, blocks parse but all end past the trailer bound, only single-property
    blocks parse, or the parsed block carries an unnamed property.
    """
    data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    parsed = single = unnamed = 0
    best = None                                  # (props, trailer) among blocks rejected only by the bound
    for start in range(len(data)):
        try:
            props, end, reason = mod.read_block(data[start:], pkg, False)
        except Exception:
            continue
        if reason != "None":
            continue
        parsed += 1
        if len(props) < 2:
            single += 1
            continue
        if any(p["name"] == "?" for p in props):
            unnamed += 1
            continue
        if start + end > len(data):
            continue
        trailer = len(data) - (start + end)
        if trailer > 64 and (best is None or len(props) > best[0]):
            best = (len(props), trailer)
    if best is not None:
        return "trailer-over-64", parsed, best[0], best[1]
    if single:
        return "single-property", parsed, 0, -1
    if unnamed:
        return "unnamed-property", parsed, 0, -1
    if parsed:
        return "no-clean-block", parsed, 0, -1
    return "no-block-parses", 0, 0, -1


def check_misses(outdir):
    """The two artifacts must agree: every not-fully-declared class explained once, no invented class."""
    per_class = os.path.join(outdir, "class_defaults_per_class.csv")
    misses_path = os.path.join(outdir, "class_defaults_misses.csv")
    try:
        wanted = {(r["package"], r["class"]) for r in csv.DictReader(open(per_class, newline="", encoding="utf-8"))
                  if r["fully_declared"] != "1"}
        rows = list(csv.DictReader(open(misses_path, newline="", encoding="utf-8")))
    except OSError as exc:
        print("FAIL class-defaults: %s" % exc)
        return 1
    got = {(r["package"], r["class"]) for r in rows}
    reasons = collections.Counter(r["reason"] for r in rows)
    blockless = reasons.get("single-property", 0) + reasons.get("trailer-over-64", 0)
    problems = []
    if got != wanted:
        problems.append("%d classes explained but %d not fully declared (%d missing, %d invented)"
                        % (len(got), len(wanted), len(wanted - got), len(got - wanted)))
    if reasons.get("no-block-parses"):
        problems.append("%d classes have no parseable block at all -- that contradicts the locator"
                        % reasons["no-block-parses"])
    if problems:
        print("FAIL class-defaults: " + "; ".join(problems))
        return 1
    print("ok   class-defaults: %d classes without a fully declared block (%d with no block: %d single-property, "
          "%d past the trailer bound)" % (len(rows), blockless, reasons.get("single-property", 0),
                                          reasons.get("trailer-over-64", 0)))
    return 0


def misses(system_dir, outdir):
    """Explain every class whose block is missing or incomplete, in the committed artifact's own terms.

    The selection is not re-invented here: `inventory/class_defaults_per_class.csv` already says which classes have no
    block (`defaults == 0`) and which have one that is not every declared property (`fully_declared == 0`). This walks
    the same packages only to say *why* the first group has none, so the two artifacts reconcile by construction.
    """
    mod = load_prop_stream()
    committed = {}
    for r in csv.DictReader(open(os.path.join(ROOT, "inventory", "class_defaults_per_class.csv"),
                                 newline="", encoding="utf-8")):
        committed[(r["package"], r["class"])] = (int(r["defaults"]), r["fully_declared"])
    supers = super_map()
    declared_all = declared_properties()
    rows = []
    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        pkg_name = fname[:-2]
        for exp in [e for e in pkg.exports if pkg.class_name_of(e) == "Class"]:
            cls = pkg.name(exp["name"])
            state = committed.get((pkg_name, cls))
            if state is None:
                continue
            defaults, fully = state
            if defaults and fully == "1":
                continue                                    # nothing to explain
            decl, cur, seen = set(), cls, set()
            while cur and cur not in seen:
                seen.add(cur)
                decl |= declared_all.get(cur, set())
                cur = supers.get(cur)
            if defaults:
                cands, _data = candidates(pkg, mod, exp)
                rows.append(["%s" % pkg_name, cls, len(decl), "incomplete", len(cands), defaults, fully])
                continue
            reason, parsed, props, trailer = diagnose(pkg, mod, exp)
            rows.append([pkg_name, cls, len(decl), reason, parsed, props, trailer])
    path = os.path.join(outdir, "class_defaults_misses.csv")
    os.makedirs(outdir, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["package", "class", "declared_properties", "reason", "candidates", "best_properties",
                    "detail"])
        w.writerows(rows)
    counts = collections.Counter(r[3] for r in rows)
    print("%d classes explained against class_defaults_per_class.csv: %s"
          % (len(rows), ", ".join("%s %d" % (k, v) for k, v in counts.most_common())))
    print("wrote %s" % path)
    return 0


def choose(cands, declared):
    """Prefer the candidate whose names the class declares, then the longest, then the smallest trailer."""
    best = None
    for count, start, names, trailer in cands:
        known = sum(1 for n in names if n in declared)
        score = (known / float(count), count, -trailer)
        if best is None or score > best[0]:
            best = (score, count, start, names)
    return best


def all_packages(system_dir, outdir):
    """Sweep every package into one CSV: package, class, defaults, declared fraction, block start.

    Written as its own mode because `--sweep -o` writes `class_defaults.csv` per invocation and a loop over
    packages would leave only the last one -- the flag-shared artifact trap.
    """
    mod = load_prop_stream()
    rows = []
    supers = super_map()
    declared_all = declared_properties()
    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        pkg_name = fname[:-2]
        for exp in [e for e in pkg.exports if pkg.class_name_of(e) == "Class"]:
            cls = pkg.name(exp["name"])
            decl, cur, seen = set(), cls, set()
            while cur and cur not in seen:
                seen.add(cur)
                decl |= declared_all.get(cur, set())
                cur = supers.get(cur)
            cands, _data = candidates(pkg, mod, exp)
            best = choose(cands, decl)
            if best is None:
                rows.append([pkg_name, cls, 0, 0.0, "", 0])
                continue
            _score, count, start, names = best
            known = sum(1 for n in names if n in decl)
            rows.append([pkg_name, cls, count, round(known / float(count), 4), start, 1 if known == count else 0])
    path = os.path.join(outdir, "class_defaults_per_class.csv")
    os.makedirs(outdir, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["package", "class", "defaults", "declared_fraction", "block_start", "fully_declared"])
        w.writerows(rows)
    total = len(rows)
    clean = sum(r[5] for r in rows)
    print("%d classes across %d packages; %d fully declared (%.1f%%)"
          % (total, len({r[0] for r in rows}), clean, 100.0 * clean / total if total else 0.0))
    print("wrote %s" % path)
    return 0


def coverage(system_dir, outdir):
    """Per-package rollup of the locator: classes, blocks found, fully declared, ambiguous, no block.

    This is the artifact `inventory/class_defaults_coverage.csv`, which until now had no generator: it was
    produced once by hand from `--sweep` output. Same definitions, one file per package.
    """
    mod = load_prop_stream()
    supers = super_map()
    declared = declared_properties()
    rows = []

    def known_for(cls):
        out, seen, cur = set(), set(), cls
        while cur and cur not in seen:
            seen.add(cur)
            out |= declared.get(cur, set())
            cur = supers.get(cur)
        return out

    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        classes = blocks = clean = ambiguous = none_found = 0
        for exp in [e for e in pkg.exports if pkg.class_name_of(e) == "Class"]:
            classes += 1
            cands, _data = candidates(pkg, mod, exp)
            if not cands:
                none_found += 1
                continue
            blocks += 1
            score = choose(cands, known_for(pkg.name(exp["name"])))
            if score[0][0] == 1.0:
                clean += 1
            elif len(cands) > 1:
                ambiguous += 1
        rows.append([fname[:-2], classes, blocks, clean, ambiguous, none_found])
    path = os.path.join(outdir, "class_defaults_coverage.csv")
    os.makedirs(outdir, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["package", "classes", "blocks_found", "fully_declared", "ambiguous", "no_block"])
        w.writerows(rows)
    total = [sum(r[i] for r in rows) for i in range(1, 6)]
    print("%d packages: %d classes, %d blocks found, %d fully declared, %d ambiguous, %d no block"
          % (len(rows), *total))
    print("wrote %s" % path)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="a package file (not needed with --packages)")
    ap.add_argument("--class", dest="cls")
    ap.add_argument("--sweep", action="store_true", help="every class in the package")
    ap.add_argument("--packages", metavar="DIR",
                    help="sweep every .u in DIR and write one per-class CSV (this is the mode that produces "
                         "inventory/class_defaults_per_class.csv; the per-package --sweep -o overwrites itself)")
    ap.add_argument("--misses", metavar="DIR",
                    help="classify every class without a fully declared block")
    ap.add_argument("--coverage", metavar="DIR",
                    help="roll every package up into inventory/class_defaults_coverage.csv")
    ap.add_argument("--walk", action="store_true",
                    help="print the raw byte walk of the chosen block (type byte, size, consumed bytes)")
    ap.add_argument("--check-misses", action="store_true",
                    help="verify class_defaults_misses.csv reconciles with class_defaults_per_class.csv")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()

    if args.packages:
        return all_packages(args.packages, args.out or "inventory")
    if args.check_misses:
        return check_misses(args.out or os.path.join(ROOT, "inventory"))
    if args.misses:
        return misses(args.misses, args.out or "inventory")
    if args.coverage:
        return coverage(args.coverage, args.out or "inventory")

    mod = load_prop_stream()
    declared = declared_properties()
    pkg = Package(args.path)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    # ancestor closure from the committed tree, so cross-package inheritance is followed
    supers = super_map()

    def known_for(cls):
        out, seen, cur = set(), set(), cls
        while cur and cur not in seen:
            seen.add(cur)
            out |= declared.get(cur, set())
            cur = supers.get(cur)
        return out

    targets = [e for e in pkg.exports
               if pkg.class_name_of(e) == "Class" and (args.sweep or pkg.name(e["name"]) == args.cls)]
    if not targets:
        sys.exit("no matching class")

    rows, clean, ambiguous, none_found = [], 0, 0, 0
    for exp in targets:
        cls = pkg.name(exp["name"])
        known = known_for(cls)
        cands, data = candidates(pkg, mod, exp)
        if not cands:
            none_found += 1
            continue
        score, count, start, names = choose(cands, known)
        if score[0] == 1.0:
            clean += 1
        elif len(cands) > 1:
            ambiguous += 1
        rows.append({"class": cls, "properties": count, "start": "0x%x" % start,
                     "declared_fraction": "%.2f" % score[0]})
        if args.walk:
            # Byte-level walk: every property with its info byte and how many bytes its value consumed.
            # This is what showed that a Bool writes no size field: the byte after its info byte is the next
            # property's name index. Kept as a mode because it is the only way to settle an alignment question.
            off = start
            while off < len(data):
                name, after_name = mod.read_index(data, off)
                if name == 0:
                    break
                info = data[after_name]
                _entry, after_value = mod.read_value(data, after_name, pkg, False)
                print("   %-24s nameIdx=%-5d info=%#04x type=%-8s consumed=%d"
                      % (pkg.name(name), name, info, mod.TYPES.get(info & 0x0F),
                         after_value - after_name))
                off = after_value
        if args.cls:
            props, _end, _reason = mod.read_block(data[start:], pkg, True)
            print("%s: %d defaults at +0x%x, %.0f%% declared by the class"
                  % (cls, len(props), start, 100 * score[0]))
            for p in props:
                if p.get("value") is not None:
                    print("   %-24s %-8s %s" % (p["name"], p["type"], p["value"]))
            print("   candidates considered: %d" % len(cands))

    if args.sweep:
        print("classes: %d, blocks found: %d, none: %d" % (len(targets), len(rows), none_found))
        print("  every property declared by the class: %d" % clean)
        print("  partial match, more than one candidate: %d" % ambiguous)
    if args.out and rows:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "class_defaults.csv")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["class", "properties", "start", "declared_fraction"])
            w.writeheader()
            w.writerows(rows)
        print("wrote %s (%d rows)" % (path, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
