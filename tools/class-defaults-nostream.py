#!/usr/bin/env python3
"""What a class payload holds when no property tag stream is in it: the names, by kind.

`inventory/class_defaults_misses.csv` withholds the defaults verdict on **334** classes as `empty-block`, and that
reason is a conjunction, not a reading: `diagnose()` reports it when the payload parses a valid *empty* block
somewhere (true of every class payload -- each ends in `00`) and **no offset anywhere yields a parse holding two of
the class's declared properties**. The second clause is the content, and it says only what the locator did *not*
find. This tool measures what the payload does hold:

* **`max_declared`** -- the most declared-property names any single parse contains, published per class. It is 0 or 1
  by construction (2 would make the class `incomplete`), so the number that carries information is *which*: a class at
  0 holds not one property name anywhere in its payload, a class at 1 holds exactly one and `single_declared_block()`
  refused it as a block (it does not tile the payload's tail, or its type contradicts the declared kind).
* **the name kinds of the best parse** -- every name the payload yields read as a tag is classified against the
  corpus: a property of any class (`class_properties.csv`), a class, a function or state (the package's own exports),
  a package, or a name that is none of those. A class record carries references to functions, classes and packages;
  a property block carries property names. The mix is what separates "the reader is standing in the record" from
  "the reader is missing a block".

Controls travel with the census, because a scan that finds a block only where the answer is already known proves
nothing:

* the **located control** runs the same sweep over classes whose block start is published, and requires that start to
  be among the parses holding two declared names -- the fraction is the instrument's recall, and a low one would
  mean the 334's zero is the reader's blindness rather than the payload's shape;
* the **one-tag control** is the `single-declared` population: 285 classes whose whole block is one tag, which the
  sweep must resolve at `max_declared = 1` (the tag names one declared property).

    python3 tools/class-defaults-nostream.py DUMP/System --sweep -o inventory
    python3 tools/class-defaults-nostream.py --report
    python3 tools/class-defaults-nostream.py DUMP/System --class AmbientCreature.HerdAnimal

No value is decoded and no byte offset is guessed: names, indices and counts only.
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
ARTIFACT = os.path.join(ROOT, "inventory", "class_defaults_nostream.csv")
COLUMNS = ["kind", "package", "class", "payload", "declared", "offsets", "parses", "tags", "max_declared",
           "best_start", "best_props", "best_to_end", "best_names", "kinds", "one_declared_at", "one_declared_end",
           "shape", "classes", "payload_bytes", "max_declared_0", "max_declared_1", "window", "long",
           "control", "control_found", "control_scanned", "control_large", "one_tag_control", "one_tag_scanned",
           "one_tag_at_one", "tag_limit", "names_yielded", "kind_property", "kind_class", "kind_function",
           "kind_state", "kind_package", "kind_other", "table_names", "table_properties", "one_names_yielded",
           "one_kind_property", "one_kind_other", "base_property", "base_class", "base_function",
           "base_package", "base_state", "base_other"]

# `class-record.py`'s window: a payload at or below this is short enough for the class record alone to account for
# every byte (the record-length p5 is 87 and the median 96 over the located population). It is a window, not a
# rule -- it is quoted here so the shape column says the same thing the other artifact does.
RECORD_WINDOW = 100

# A parse is stopped after this many tags. Every claim here is about a *count* of names, and a sweep that reads a
# 13 KB record as 2,000 tags spends its time on the tail of a desynchronised stream; 64 is past every real block
# this dump's located classes hold except the largest, and the limit is published in the census row.
TAG_LIMIT = 64

# The located control sweeps whole payloads and some class records run to 13 KB, where an all-offsets parse costs more
# than the reading is worth. Control classes above this size are skipped and the census publishes how many were read,
# so a low recall number cannot be an artifact of a truncated sample.
CONTROL_MAX = 1500


def load(name):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    spec = importlib.util.spec_from_file_location(name.replace("-", "_").replace(".py", ""), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def corpus_paths():
    return {"properties": os.path.join(ROOT, "inventory", "class_properties.csv"),
            "classes": os.path.join(ROOT, "inventory", "classes.csv"),
            "per_class": os.path.join(ROOT, "inventory", "class_defaults_per_class.csv"),
            "misses": os.path.join(ROOT, "inventory", "class_defaults_misses.csv")}


def corpus():
    """The three name sets a payload's names are classified against, and the populations to sweep."""
    paths = corpus_paths()
    props = set()
    with open(paths["properties"], newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            props.add(r["property"])
    classes, packages = set(), set()
    with open(paths["classes"], newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            classes.add(r["class"])
            packages.add(r["package"])
    starts, clean, misses, one_tag = {}, set(), {}, set()
    with open(paths["per_class"], newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["block_start"]:
                starts[(r["package"], r["class"])] = int(r["block_start"])
            if r["fully_declared"] == "1":
                clean.add((r["package"], r["class"]))
    with open(paths["misses"], newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            misses[(r["package"], r["class"])] = r["reason"]
            if r["reason"] == "single-declared":
                one_tag.add((r["package"], r["class"]))
    return props, classes, packages, starts, clean, misses, one_tag


def package_names(pkg):
    """The names a package's exports declare as functions, states and classes.

    A class record references the class's own function and state table, so a payload read as a tag stream yields
    those names; a property block yields property names. The distinction is what the census measures, so the two
    sets are kept apart rather than merged into "a known name".
    """
    out = {"Function": set(), "State": set(), "Class": set()}
    for e in pkg.exports:
        kind = pkg.class_name_of(e)
        if kind in out:
            out[kind].add(pkg.name(e["name"]))
    return out


def classify(names, props, classes, packages, local):
    """[(kind, name)] for every tag name, in the order the payload yields them."""
    out = []
    for n in names:
        if n in props:
            kind = "property"
        elif n in local["Function"]:
            kind = "function"
        elif n in local["State"]:
            kind = "state"
        elif n in classes:
            kind = "class"
        elif n in packages:
            kind = "package"
        else:
            kind = "other"
        out.append((kind, n))
    return out


def scan(pkg, mod, data, declared, props, classes, packages, local):
    """Every offset whose parse is a valid tag stream: (declared names, tags, start, end, names)."""
    out = []
    for start in range(len(data)):
        try:
            tags, end, reason = mod.read_block(data[start:], pkg, False, limit=TAG_LIMIT)
        except Exception:
            continue
        if reason not in ("None", "limit"):
            continue
        if not tags or any(t["name"] == "?" for t in tags):
            continue
        declared_n = sum(1 for t in tags if t["name"] in declared)
        out.append((declared_n, len(tags), start, start + end, [t["name"] for t in tags]))
    return out


def best_of(parses):
    """The parse with the most declared names, then the most tags, then the earliest start."""
    if not parses:
        return None
    return sorted(parses, key=lambda p: (-p[0], -p[1], p[2]))[0]


def measure_class(pkg, mod, cls, data, declared, props, classes, packages, local):
    parses = scan(pkg, mod, data, declared, props, classes, packages, local)
    best = best_of(parses)
    max_declared = max((p[0] for p in parses), default=0)
    one = [p for p in parses if p[0] == 1]
    kinds = collections.Counter()
    row = {"kind": "class", "payload": len(data), "declared": len(declared), "offsets": len(data),
           "parses": len(parses), "tags": sum(p[1] for p in parses), "max_declared": max_declared,
           "best_start": "", "best_props": "", "best_to_end": "", "best_names": "", "kinds": "",
           "one_declared_at": "", "one_declared_end": "",
           "shape": "record-window" if len(data) <= RECORD_WINDOW else "long"}
    if best is not None:
        classified = classify(best[4], props, classes, packages, local)
        for kind, _n in classified:
            kinds[kind] += 1
        row.update({"best_start": best[2], "best_props": best[1], "best_to_end": len(data) - best[3],
                    "best_names": ";".join(n for _k, n in classified[:6]),
                    "kinds": ";".join("%s %d" % kv for kv in sorted(kinds.items()))})
    if one:
        # The single declared name, at its earliest parse: where it sits and how many bytes the parse leaves after it.
        first = sorted(one, key=lambda p: p[2])[0]
        row["one_declared_at"] = "%d:%s" % (first[2], first[4][:1][0] if first[4] else "")
        row["one_declared_end"] = len(data) - first[3]
    return row, parses


def sweep(system_dir, outdir, show=False, only=None, control_step=0):
    if not os.path.isdir(system_dir):
        print("FAIL class-defaults-nostream: %s is not a directory (pass DUMP/System)" % system_dir)
        return 1
    cdef = load("class-defaults.py")
    mod = cdef.load_prop_stream()
    supers, declared_all = cdef.super_map(), cdef.declared_properties()
    props, classes, packages, starts, clean, misses, one_tag = corpus()
    want = {k for k, v in misses.items() if v == "empty-block"}
    if only:
        want = {k for k in want if k[1] == only}
    control = set()
    if control_step:
        ordered = sorted(clean)
        control = {k for i, k in enumerate(ordered) if i % control_step == 0}
    by_pkg = collections.defaultdict(list)
    for pkgname, cls in sorted(want | control | one_tag):
        by_pkg[pkgname].append(cls)
    rows, tally = [], collections.Counter()
    control_found, control_scanned, one_tag_at_one = 0, 0, 0
    for pkgname in sorted(by_pkg):
        pkg = Package(os.path.join(system_dir, pkgname + ".u"))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        local = package_names(pkg)
        # `pkg.names` is a list of (name, flags) pairs, so the table's entries are read through `pkg.name(i)`.
        table = [pkg.name(i) for i in range(len(pkg.names))] if pkg.names else []
        tally["table_names"] += len(table)
        for n in table:
            tally["base_" + classify([n], props, classes, packages, local)[0][0]] += 1
        classes_here = {}
        for exp in pkg.exports:
            if pkg.class_name_of(exp) == "Class":
                classes_here.setdefault(pkg.name(exp["name"]), exp)
        for cls in sorted(by_pkg[pkgname]):
            exp = classes_here.get(cls)
            if exp is None:
                tally["no-class-export"] += 1
                continue
            declared, cur, seen = set(), cls, set()
            while cur and cur not in seen:
                seen.add(cur)
                declared |= declared_all.get(cur, set())
                cur = supers.get(cur)
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            row, parses = measure_class(pkg, mod, cls, data, declared, props, classes, packages, local)
            key = (pkgname, cls)
            if key in control:
                # the located control: the published start has to be one of the two-declared parses
                if len(data) > CONTROL_MAX:
                    tally["control-large"] += 1
                    continue
                control_scanned += 1
                hit = any(p[2] == starts[key] and p[0] >= 2 for p in parses)
                control_found += 1 if hit else 0
                tally["control-miss"] += 0 if hit else 1
                if show and not hit:
                    print("control miss %s.%s start %d max_declared %d" % (pkgname, cls, starts[key],
                                                                          row["max_declared"]))
                continue
            if key in one_tag:
                # the one-tag control: this class's whole block is one tag, so the sweep must resolve it at 1
                one_tag_at_one += 1 if row["max_declared"] == 1 else 0
                tally["one_tag_scanned"] += 1
                tally["one_names"] += int(row["best_props"] or 0)
                for part in row["kinds"].split(";"):
                    if part:
                        k, v = part.rsplit(" ", 1)
                        tally["one_kind_" + k] += int(v)
                if show and row["max_declared"] != 1:
                    print("one-tag miss %s.%s max_declared %s" % (pkgname, cls, row["max_declared"]))
                continue
            if key not in want:
                continue
            row.update({"package": pkgname, "class": cls})
            rows.append(row)
            tally["classes"] += 1
            tally["payload_bytes"] += len(data)
            tally["offsets"] += row["offsets"]
            tally["parses"] += row["parses"]
            tally["tags"] += row["tags"]
            tally["max_declared_%d" % row["max_declared"]] += 1
            tally["names_yielded"] += int(row["best_props"] or 0)
            for part in row["kinds"].split(";"):
                if part:
                    k, v = part.rsplit(" ", 1)
                    tally["kind_" + k] += int(v)
            tally["window" if row["shape"] == "record-window" else "long"] += 1
            if show:
                print("%s.%s payload %d declared %d: %d parses, max_declared %d%s"
                      % (pkgname, cls, len(data), len(declared), row["parses"], row["max_declared"],
                         " one-declared at %s (%s after)" % (row["one_declared_at"], row["one_declared_end"])
                         if row["one_declared_at"] else ""))
    rows.sort(key=lambda r: (r["package"], r["class"]))
    census = {"kind": "census", "classes": tally["classes"], "payload_bytes": tally["payload_bytes"],
              "offsets": tally["offsets"], "parses": tally["parses"], "tags": tally["tags"],
              "max_declared_0": tally["max_declared_0"], "max_declared_1": tally["max_declared_1"],
              "window": tally["window"], "long": tally["long"], "control": len(control),
              "control_found": control_found, "control_scanned": control_scanned,
              "control_large": tally["control-large"],
              "one_tag_control": len(one_tag), "one_tag_scanned": tally["one_tag_scanned"],
              "one_tag_at_one": one_tag_at_one, "tag_limit": TAG_LIMIT,
              "names_yielded": tally["names_yielded"],
              "kind_property": tally["kind_property"], "kind_class": tally["kind_class"],
              "kind_function": tally["kind_function"], "kind_state": tally["kind_state"],
              "kind_package": tally["kind_package"], "kind_other": tally["kind_other"],
              "table_names": tally["table_names"], "table_properties": tally["base_property"],
              "base_property": tally["base_property"], "base_class": tally["base_class"],
              "base_function": tally["base_function"], "base_package": tally["base_package"],
              "base_state": tally["base_state"], "base_other": tally["base_other"],
              "one_names_yielded": tally["one_names"],
              "one_kind_property": tally["one_kind_property"], "one_kind_other": tally["one_kind_other"]}
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "class_defaults_nostream.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerow(census)
        for r in rows:
            w.writerow(r)
    print("%d classes with no start read: %d parses over %d payload bytes; max_declared 0 on %d, 1 on %d; "
          "%d within the record window, %d longer"
          % (census["classes"], census["parses"], census["payload_bytes"], census["max_declared_0"],
             census["max_declared_1"], census["window"], census["long"]))
    print("controls: published start found at two declared names on %d of %d located classes; %d of %d one-tag "
          "classes resolve at max_declared 1" % (control_found, control_scanned, one_tag_at_one, len(one_tag)))
    kinds = collections.Counter()
    for r in rows:
        for part in r["kinds"].split(";"):
            if part:
                k, v = part.rsplit(" ", 1)
                kinds[k] += int(v)
    print("names the best parse yields, by kind: %s"
          % ", ".join("%s %d" % kv for kv in kinds.most_common()))
    if census["names_yielded"]:
        print("shares against the name table: %s"
              % ", ".join("%s %.1f%%/%.1f%%" % (k, 100.0 * int(census["kind_" + k]) / int(census["names_yielded"]),
                                                100.0 * int(census["base_" + k]) / max(1, int(census["table_names"])))
                          for k in ("property", "class", "function", "package", "state", "other")))
    print("wrote %s" % path)
    return 0


def read_rows(path=ARTIFACT):
    if not os.path.isfile(path):
        return [], None
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    census = [r for r in rows if r["kind"] == "census"]
    return [r for r in rows if r["kind"] != "census"], (census[0] if census else None)


def report(path=ARTIFACT):
    rows, census = read_rows(path)
    if census is None:
        print("FAIL class-defaults-nostream: %s has no census row" % path)
        return 1
    print("%s classes, %s payload bytes, %s parses: max_declared 0 on %s, 1 on %s; %s record-window / %s long"
          % (census["classes"], census["payload_bytes"], census["parses"], census["max_declared_0"],
             census["max_declared_1"], census["window"], census["long"]))
    print("controls: published start at two declared names on %s of %s clean classes (%s skipped as too large); "
          "one-tag classes resolved at max_declared 1: %s of %s"
          % (census["control_found"], census["control_scanned"], census.get("control_large", "0"),
             census["one_tag_at_one"], census["one_tag_scanned"]))
    if int(census.get("names_yielded") or 0):
        print("property share of the names the best parses yield: %.1f%% against %.1f%% of the name table"
              % (100.0 * int(census["kind_property"]) / int(census["names_yielded"]),
                 100.0 * int(census["table_properties"]) / max(1, int(census["table_names"]))))
    kinds = collections.Counter()
    for r in rows:
        for part in r["kinds"].split(";"):
            if part:
                k, v = part.rsplit(" ", 1)
                kinds[k] += int(v)
    print("best-parse names by kind: %s" % ", ".join("%s %d" % kv for kv in kinds.most_common()))
    one = [r for r in rows if r["one_declared_at"]]
    print("%d of %s hold exactly one declared name somewhere; %d of those tile the payload's last byte"
          % (len(one), census["classes"], sum(1 for r in one if r["one_declared_end"] == "0")))
    for r in sorted(rows, key=lambda r: -int(r["payload"]))[:6]:
        print("  %-22s %-26s payload %5s declared %4s max %s best %s names %s"
              % (r["package"], r["class"], r["payload"], r["declared"], r["max_declared"], r["kinds"],
                 r["best_names"]))
    return 0


def self_check():
    """The classification and the best-parse rule, on names whose kinds are known."""
    props, classes, packages = {"Speed", "Tag"}, {"Actor", "Pawn"}, {"Engine", "GamePlay"}
    local = {"Function": {"Tick", "BeginState"}, "State": {"Dying"}, "Class": {"Pawn"}}
    got = classify(["Speed", "Tick", "Dying", "Pawn", "Engine", "Nothing"], props, classes, packages, local)
    assert [k for k, _n in got] == ["property", "function", "state", "class", "package", "other"], got
    # `Pawn` is both a class and a declared local class: the class set wins, and only a property name is a property
    assert got[3][0] == "class", got[3]
    # the best parse is by declared names, then tags, then the earliest start -- not by length alone
    parses = [(1, 6, 40, 90, ["a"]), (0, 9, 10, 90, ["b"]), (1, 6, 12, 60, ["c"]), (1, 8, 70, 90, ["d"])]
    assert best_of(parses)[2] == 70, best_of(parses)
    assert best_of([]) is None
    print("ok   class-defaults-nostream: name kinds, the class-before-package order and the best-parse rule")
    return 0


def check(path=ARTIFACT):
    rc = self_check()
    rows, census = read_rows(path)
    problems = []
    if census is None:
        print("FAIL class-defaults-nostream: no census row in %s" % path)
        return 1
    classes = int(census["classes"])
    if len(rows) != classes:
        problems.append("%d class rows against a census of %s" % (len(rows), census["classes"]))
    if sum(int(r["payload"]) for r in rows) != int(census["payload_bytes"]):
        problems.append("the payload column sums to %d against a census of %s"
                        % (sum(int(r["payload"]) for r in rows), census["payload_bytes"]))
    zero = sum(1 for r in rows if r["max_declared"] == "0")
    one = sum(1 for r in rows if r["max_declared"] == "1")
    if (zero, one) != (int(census["max_declared_0"]), int(census["max_declared_1"])):
        problems.append("max_declared splits %d/%d against a census of %s/%s"
                        % (zero, one, census["max_declared_0"], census["max_declared_1"]))
    window = sum(1 for r in rows if r["shape"] == "record-window")
    if window != int(census["window"]) or len(rows) - window != int(census["long"]):
        problems.append("shapes %d/%d against a census of %s/%s"
                        % (window, len(rows) - window, census["window"], census["long"]))
    for r in rows:
        if int(r["max_declared"]) > 1:
            # `empty-block` is withheld exactly when no parse holds two declared names; a row above one would mean
            # the artifact and the reason it explains disagree.
            problems.append("%s.%s holds %s declared names in one parse"
                            % (r["package"], r["class"], r["max_declared"]))
        if int(r["best_props"] or 0) > int(census["tag_limit"]):
            problems.append("%s.%s parses past the tag limit" % (r["package"], r["class"]))
    if problems:
        print("FAIL class-defaults-nostream: " + "; ".join(problems[:6]))
        return 1
    print("ok   class-defaults-nostream: %d classes, %s parses, max_declared 0 on %s and 1 on %s; control %s of %s"
          % (len(rows), census["parses"], census["max_declared_0"], census["max_declared_1"],
             census["control_found"], census["control_scanned"]))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("system", nargs="?", help="the game directory holding System/*.u")
    ap.add_argument("--class", dest="cls", help="one class name")
    ap.add_argument("--sweep", action="store_true", help="read every class the misses table has no start for")
    ap.add_argument("-o", "--out", help="artifact directory")
    ap.add_argument("--control", type=int, default=40, metavar="N",
                    help="sweep every Nth class with a published start as the recall control (0 = none)")
    ap.add_argument("--report", action="store_true", help="summarise the committed artifact")
    ap.add_argument("--check", action="store_true", help="dump-free self-test and artifact reconciliation")
    ap.add_argument("--show", action="store_true", help="print one line per class while sweeping")
    args = ap.parse_args()
    if args.check:
        return check()
    if args.report:
        return report()
    if not args.system:
        ap.error("a System directory, or --report/--check")
    if not args.sweep or not args.out:
        ap.error("--sweep needs -o")
    return sweep(args.system, args.out, show=args.show, only=args.cls, control_step=args.control)


if __name__ == "__main__":
    sys.exit(main())
