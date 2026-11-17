#!/usr/bin/env python3
"""Is the shorter block a type-first ranking picks complete, or only clean?

`tools/class-defaults.py`'s `choose()` sorts `(declared fraction, bad == 0, count, own, -trailer)`. Ranking the
type test *first* was measured on the dump and deliberately not shipped: it promotes **535 withheld classes to
clean blocks** (`docs/CLASS_DEFAULTS.md`, *What was deliberately not shipped*), because for those classes a
candidate that names every one of the class's properties also contradicts the class's own property record on a
tag's type. The type rule then picks a **shorter** candidate -- one that starts later and still ends on the
payload's last byte -- and that measurement never asked whether the shorter parse is the class's whole block.

This tool asks it, per class, from the package bytes and the committed inventories:

* **What the shorter candidate dropped.** Both orderings read the same candidate list, so when the type-first
  start is later than the safe start the bytes between them are the safe parse's leading tags. The tool walks
  those bytes with the grammar's own reader (`prop-stream.read_index` / `read_value`) and counts how many of the
  dropped names the class (or an ancestor) declares -- a block sets defaults for declared properties, so a
  dropped tag naming one is a default the shorter block omits.
* **Whether the shorter start has a witness of its own.** `guarded()` records where each candidate start came
  from (`candidate` for the free scan, `group-end`/`group-tail` for the record's closing group). A start on a
  committed group's end is the record boundary measured from the record's own fields, apart from the tag parse
  -- the witness that a block opens there.
* **The control the promoted population needs.** The same origin tally is taken over the classes the safe
  ordering already calls clean, so "how many block starts have a record witness" has a base rate instead of
  being read off the promoted rows alone.

Rows come in two kinds, because the two orderings can disagree in two ways:

* `promoted` -- the type-first candidate is clean where the safe one is not: the class the earlier note counted;
* `moved` -- both orderings withhold the class but pick a *different* start, which is every row the reorder can
  actually change (a clean candidate is one with fraction 1.0 and no contradiction, and the shipped tuple already
  breaks that tie on `bad == 0`, so `promoted` is empty by construction -- the measurement `docs/CLASS_DEFAULTS.md`
  quotes was a different edit, not this ordering).

Verdicts, per row:

* `truncated` -- the dropped tags name at least one property the class declares: the shorter block omits real
  defaults, and the type-first reading is a suffix of the block, not the block;
* `complete` -- no dropped tag names a declared property (or there is no safe candidate at all): what the type
  rule discarded invents package names, and the shorter candidate is the block;
* `type-first-earlier` -- the winning type-first start is *earlier* than the safe one, so the safe start sits
  inside a tag; reported rather than folded into either side.

    python3 tools/class-defaults-promoted.py DUMP/System --sweep -o inventory
    python3 tools/class-defaults-promoted.py --report
    python3 tools/class-defaults-promoted.py DUMP/System --class SGOrder

`--check` is dump-free: it re-derives every verdict from the row's own counts and reconciles the census row with
the table, so a corrupted row fails without the dump.
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
ARTIFACT = os.path.join(ROOT, "inventory", "class_defaults_promoted.csv")
COLUMNS = ["kind", "package", "class", "safe_start", "safe_count", "safe_bad", "tf_start", "tf_count",
           "tf_witness", "gap", "dropped_tags", "dropped_declared", "dropped_own", "dropped_bad", "verdict",
           "classes", "safe_clean", "tf_clean", "promoted", "moved", "moved_truncated", "moved_complete",
           "moved_earlier", "clean_witness", "divergence_witness", "tf_nocontra", "tf_nocontra_props",
           "tf_names_package"]


def load_class_defaults():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "class-defaults.py")
    spec = importlib.util.spec_from_file_location("class_defaults", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def choose_type_first(cands, declared, own=None):
    """`class-defaults.choose()` with the type test at the head of the tuple -- the measured-but-unshipped rule."""
    best = None
    for count, start, names, trailer, bad in cands:
        known = sum(1 for n in names if n in declared)
        own_known = sum(1 for n in names if n in own) if own else 0
        score = (bad == 0, known / float(count), count, own_known, -trailer)
        if best is None or score > best[0]:
            best = (score, count, start, names, bad)
    return best


def clean(cand, declared, floor):
    """The locator's verdict for a chosen candidate: every name declared, no contradiction, at least `floor`."""
    if cand is None:
        return False
    _score, count, _start, names, bad = cand
    known = sum(1 for n in names if n in declared)
    return known > 0 and known == count and bad == 0 and count >= floor


def verdict_of(gap, dropped_tags, dropped_declared):
    """The one place a row's verdict is decided, so `--check` can re-derive it from the row's own columns."""
    if gap < 0:
        return "type-first-earlier"
    if gap == 0 and dropped_tags == 0:
        return "complete"                       # no safe candidate: nothing was dropped
    return "truncated" if dropped_declared else "complete"


def tag_spans(mod, data, start, pkg, limit=2000):
    """`(offset, name, type, width)` for every tag the grammar reads from `start`, stopping at the `None` tag."""
    out, off, n = [], start, len(data)
    while off < n and len(out) < limit:
        at = off
        try:
            name, off = mod.read_index(data, off)
            if name == 0 or pkg.name(name) == "None":
                break
            entry, off = mod.read_value(data, off, pkg, False)
        except Exception:
            break
        out.append((at, pkg.name(name), entry.get("type"), off - at))
    return out


def bad_tag(cdef, name, typ, kinds):
    """1 when the tag's type is one the declared kind cannot carry -- `class-defaults.contradictions`, one tag."""
    allowed = cdef.TYPE_AGREEMENT.get(kinds.get(name))
    return 1 if (allowed is not None and typ not in allowed) else 0


def divergence_row(cdef, mod, pkg, pkg_name, cls, safe, tf, origins, data, declared, own, kinds):
    """One row where the two orderings disagree: a `promoted` class, or a `moved` start. None when they agree."""
    if tf is None:
        return None
    _score, count, start, _names, _bad = tf
    promoted = clean(tf, declared, cdef.MIN_PROPERTIES) and not clean(safe, declared, cdef.MIN_PROPERTIES)
    moved = safe is not None and clean(safe, declared, cdef.MIN_PROPERTIES) is False \
        and not promoted and start != safe[2]
    if not promoted and not moved:
        return None
    row = {"kind": "promoted" if promoted else "moved", "package": pkg_name, "class": cls,
           "safe_start": safe[2] if safe else "", "safe_count": safe[1] if safe else "",
           "safe_bad": safe[4] if safe else "", "tf_start": start, "tf_count": count,
           "tf_witness": origins.get(start, "candidate")}
    if safe is None:
        # No safe candidate at all: the type rule did not discard anything, it published the only parse.
        row.update({"gap": 0, "dropped_tags": 0, "dropped_declared": 0, "dropped_own": 0, "dropped_bad": 0,
                    "verdict": verdict_of(0, 0, 0)})
        return row
    s_start = safe[2]
    gap = start - s_start
    if gap < 0:
        row.update({"gap": gap, "dropped_tags": "", "dropped_declared": "", "dropped_own": "",
                    "dropped_bad": "", "verdict": verdict_of(gap, 0, 0)})
        return row
    dropped = [s for s in tag_spans(mod, data, s_start, pkg) if s[0] < start]
    row["gap"] = gap
    row["dropped_tags"] = len(dropped)
    row["dropped_declared"] = sum(1 for _o, n, _t, _w in dropped if n in declared)
    row["dropped_own"] = sum(1 for _o, n, _t, _w in dropped if n in own)
    row["dropped_bad"] = sum(bad_tag(cdef, n, t, kinds) for _o, n, t, _w in dropped)
    row["verdict"] = verdict_of(gap, len(dropped), row["dropped_declared"])
    return row


def walk_packages(system_dir, only=None, show=False):
    """Every class of every package under both orderings: the promoted rows plus the run's own tallies."""
    cdef = load_class_defaults()
    mod = cdef.load_prop_stream()
    supers, declared_all = cdef.super_map(), cdef.declared_properties()
    # every package name in the corpus: a class's defaults never name one as a property, and a parse that does
    # is reading a name run (the shape `docs/CLASS_DEFAULTS.md` calls "undeclared package names").
    packages = {f[:-2].lower() for f in os.listdir(system_dir) if f.lower().endswith(".u")}
    rows, tally = [], collections.Counter()
    clean_origin, promoted_origin = collections.Counter(), collections.Counter()
    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        pkg_name = fname[:-2]
        for exp in [e for e in pkg.exports if pkg.class_name_of(e) == "Class"]:
            cls = pkg.name(exp["name"])
            if only and cls != only:
                continue
            decl, own, cur, seen = set(), set(declared_all.get(cls, set())), cls, set()
            while cur and cur not in seen:
                seen.add(cur)
                decl |= declared_all.get(cur, set())
                cur = supers.get(cur)
            kinds = cdef.kinds_for_class(cls)
            try:
                cands, data = cdef.candidates(pkg, mod, exp, kinds=kinds)
                cands, origins = cdef.guarded(cands, data, pkg, decl, kinds, len(pkg.imports or []))
            except Exception as exc:            # a payload the reader cannot walk is counted, not guessed
                tally["error"] += 1
                if show:
                    print("error %s.%s: %s" % (pkg_name, cls, exc))
                continue
            safe = cdef.choose(cands, decl, own)
            tf = choose_type_first(cands, decl, own)
            tally["classes"] += 1
            if clean(safe, decl, cdef.MIN_PROPERTIES):
                tally["safe_clean"] += 1
                tally["safe_props"] += safe[1]
                clean_origin[origins.get(safe[2], "candidate")] += 1
            if clean(tf, decl, cdef.MIN_PROPERTIES):
                tally["tf_clean"] += 1
            if tf and tf[4] == 0 and tf[1] >= cdef.MIN_PROPERTIES and any(n in decl for n in tf[3]):
                # "clean" read as "no contradiction on a declared name" -- the weaker predicate, which a parse
                # naming only undeclared names satisfies
                tally["tf_nocontra"] += 1
                tally["tf_nocontra_props"] += tf[1]
                if any(n.lower() in packages for n in tf[3]):
                    tally["tf_names_package"] += 1
            row = divergence_row(cdef, mod, pkg, pkg_name, cls, safe, tf, origins, data, decl, own, kinds)
            if row is None:
                continue
            tally[row["kind"]] += 1
            tally[row["kind"] + "_" + row["verdict"]] += 1
            promoted_origin[row["tf_witness"]] += 1
            rows.append(row)
            if show:
                print("%s.%s %s -> %s (gap %s, %s of %s dropped tags declared)" % (
                    pkg_name, cls, row["safe_start"], row["tf_start"], row["gap"],
                    row["dropped_declared"], row["dropped_tags"]))
    return rows, tally, clean_origin, promoted_origin


def mix(counter):
    return ";".join("%s %d" % kv for kv in sorted(counter.items()))


def write_sweep(system_dir, outdir, show=False):
    rows, tally, clean_origin, promoted_origin = walk_packages(system_dir, show=show)
    census = {"kind": "census", "classes": tally["classes"], "safe_clean": tally["safe_clean"],
              "tf_clean": tally["tf_clean"], "promoted": tally["promoted"], "moved": tally["moved"],
              "moved_truncated": tally["moved_truncated"], "moved_complete": tally["moved_complete"],
              "moved_earlier": tally["moved_type-first-earlier"],
              "clean_witness": mix(clean_origin), "divergence_witness": mix(promoted_origin),
              "tf_nocontra": tally["tf_nocontra"], "tf_nocontra_props": tally["tf_nocontra_props"],
              "tf_names_package": tally["tf_names_package"]}
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "class_defaults_promoted.csv")
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerow(census)
        for r in rows:
            w.writerow({k: r.get(k, "") for k in COLUMNS})
    print("%d classes; %d clean under the shipped ordering, %d under the type-first; promoted %d, moved %d "
          "(truncated %d, complete %d, earlier %d); start origin clean %s, divergent %s"
          % (tally["classes"], tally["safe_clean"], tally["tf_clean"], tally["promoted"], tally["moved"],
             tally["moved_truncated"], tally["moved_complete"], tally["moved_type-first-earlier"],
             mix(clean_origin), mix(promoted_origin)))
    print("control: the shipped ordering's %d clean classes carry %d properties; the weaker predicate (no "
          "contradiction on a declared name, at least one declared name) selects %d classes and %d properties, %d "
          "naming a package name as a property"
          % (tally["safe_clean"], tally["safe_props"], tally["tf_nocontra"], tally["tf_nocontra_props"],
             tally["tf_names_package"]))
    if tally["error"]:
        print("%d class payload(s) raised" % tally["error"])
    print("wrote %s" % path)
    return 0


def read_rows(path=ARTIFACT):
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    census = [r for r in rows if r["kind"] == "census"]
    return [r for r in rows if r["kind"] != "census"], (census[0] if census else None)


def parse_mix(text):
    out = {}
    for part in (text or "").split(";"):
        if not part.strip():
            continue
        key, _, num = part.strip().rpartition(" ")
        out[key] = int(num)
    return out


def report(path=ARTIFACT):
    rows, census = read_rows(path)
    if census is None:
        print("FAIL class-defaults-promoted: %s has no census row" % path)
        return 1
    kinds = collections.Counter(r["kind"] for r in rows)
    verdicts = collections.Counter(r["verdict"] for r in rows)
    witness = collections.Counter(r["tf_witness"] for r in rows)
    dropped = sum(int(r["dropped_declared"] or 0) for r in rows)
    gap = sum(max(0, int(r["gap"] or 0)) for r in rows)
    print("%d divergent classes of %s (safe clean %s, type-first clean %s): %s"
          % (len(rows), census["classes"], census["safe_clean"], census["tf_clean"],
             ", ".join("%s %d" % kv for kv in sorted(kinds.items()))))
    print("verdicts: " + ", ".join("%s %d" % kv for kv in sorted(verdicts.items())))
    print("type-first start witness on divergent rows: " + ", ".join("%s %d" % kv for kv in sorted(witness.items())))
    print("clean-row start witness (control): " + (census["clean_witness"] or "none"))
    print("the weaker predicate (no contradiction, at least one declared name): %s classes, %s properties, "
          "%s naming a package name" % (census["tf_nocontra"], census["tf_nocontra_props"],
                                        census["tf_names_package"]))
    print("dropped tags naming a declared property: %d, over %d bytes dropped in all" % (dropped, gap))
    return 0


def check(path=ARTIFACT):
    """Dump-free: every verdict re-derived from its own row, and the census reconciled with the table."""
    rows, census = read_rows(path)
    if census is None:
        print("FAIL class-defaults-promoted: no census row in %s" % path)
        return 1
    problems = []
    for r in rows:
        name = "%s.%s" % (r["package"], r["class"])
        try:
            s_start = int(r["safe_start"]) if r["safe_start"] else None
            t_start, t_count = int(r["tf_start"]), int(r["tf_count"])
            gap, tags = int(r["gap"]), int(r["dropped_tags"] or 0)
            decl, own = int(r["dropped_declared"] or 0), int(r["dropped_own"] or 0)
        except ValueError:
            problems.append("%s carries a non-numeric field" % name)
            continue
        if t_count < 2:
            problems.append("%s is promoted on %d tag(s)" % (name, t_count))
        if r["verdict"] != verdict_of(gap, tags, decl):
            problems.append("%s: verdict %r is not what gap %d / %d tag(s) / %d declared name(s) say"
                            % (name, r["verdict"], gap, tags, decl))
        if own > decl:
            problems.append("%s: %d own names among %d declared" % (name, own, decl))
        if decl > tags:
            problems.append("%s: %d declared names among %d dropped tags" % (name, decl, tags))
        if gap > 0 and s_start is not None and tags > gap:
            problems.append("%s drops %d tags in %d bytes" % (name, tags, gap))
    counts = collections.Counter(r["kind"] for r in rows)
    if (len(rows) != int(census["promoted"] or 0) + int(census["moved"] or 0)
            or counts.get("promoted", 0) != int(census["promoted"] or 0)
            or counts.get("moved", 0) != int(census["moved"] or 0)):
        problems.append("%d rows (%s) but the census says %s promoted and %s moved"
                        % (len(rows), dict(counts), census["promoted"], census["moved"]))
    truncated = sum(1 for r in rows if r["kind"] == "moved" and r["verdict"] == "truncated")
    if str(truncated) != str(census["moved_truncated"] or ""):
        problems.append("%d moved rows are truncated but the census says %s"
                        % (truncated, census["moved_truncated"]))
    want = collections.Counter(r["tf_witness"] for r in rows)
    if census["divergence_witness"] and parse_mix(census["divergence_witness"]) != dict(want):
        problems.append("the census witness tally %s is not the table's %s"
                        % (census["divergence_witness"], dict(want)))
    if problems:
        print("FAIL class-defaults-promoted: " + "; ".join(problems[:6]))
        return 1
    verdicts = collections.Counter(r["verdict"] for r in rows)
    kinds = collections.Counter(r["kind"] for r in rows)
    print("ok   class-defaults-promoted: %d promoted, %d moved (%d truncated, %d complete); "
          "safe clean %s, type-first clean %s"
          % (kinds.get("promoted", 0), kinds.get("moved", 0), verdicts.get("truncated", 0),
             verdicts.get("complete", 0), census["safe_clean"], census["tf_clean"]))
    return 0


class _Pkg:
    """A names table and an import table, enough for the tag grammar: the self-test never opens a package."""
    exports = []

    def __init__(self, names):
        self.names = names
        self.imports = [{"class": 1, "class_package": 1, "name": 1}]

    def name(self, i):
        return self.names[i] if 0 <= i < len(self.names) else "?"


def self_check():
    """Synthetic tag bodies: the orderings disagree on the same list, and the verdict follows the dropped names."""
    cdef = load_class_defaults()
    mod = cdef.load_prop_stream()
    # tags: A float (class 2 = four bytes), B bool (one value byte), C bool -- then the `None` name
    body = b"\x01\x24" + b"\x00" * 4 + b"\x02\x03\x01" + b"\x03\x83\x00" + b"\x00"
    pkg = _Pkg(["None", "A", "B", "C"])
    spans = tag_spans(mod, body, 0, pkg)
    assert [s[1] for s in spans] == ["A", "B", "C"], spans
    assert [s[3] for s in spans] == [6, 3, 3], spans
    assert [s[0] for s in spans] == [0, 6, 9], spans
    assert [s[1] for s in tag_spans(mod, body, 6, pkg)] == ["B", "C"]
    kinds = {"A": "UFloatProperty", "B": "UBoolProperty", "C": "UBoolProperty"}
    assert bad_tag(cdef, "A", "Bool", kinds) == 1 and bad_tag(cdef, "A", "Float", kinds) == 0
    assert bad_tag(cdef, "B", "Bool", kinds) == 0 and bad_tag(cdef, "B", "Int", kinds) == 1
    # the two orderings do not always agree: a full-fraction candidate with a contradiction outranks a
    # half-declared one under the shipped tuple, and the type test picks the half-declared one instead
    full_bad = (3, 0, ["A", "B", "C"], 0, 1)
    half_ok = (4, 9, ["A", "X", "Y", "Z"], 0, 0)
    assert cdef.choose([full_bad, half_ok], {"A", "B", "C"})[2] == 0
    assert choose_type_first([full_bad, half_ok], {"A", "B", "C"})[2] == 9
    assert not clean(choose_type_first([full_bad, half_ok], {"A", "B", "C"}), {"A", "B", "C"}, 2)
    # and the structural claim: a *clean* winner under the type-first ordering is one the shipped ordering
    # also calls clean, because clean pins the fraction at 1.0 and the shipped tuple breaks that tie on bad == 0
    for cands in ([full_bad, half_ok], [full_bad, (3, 6, ["A", "B", "C"], 0, 0)],
                  [(2, 0, ["A", "B"], 0, 0), (3, 4, ["A", "B", "C"], 0, 2)],
                  [(1, 0, ["A"], 0, 0), (2, 3, ["A", "B"], 0, 0)]):
        tf_c, safe_c = choose_type_first(cands, {"A", "B", "C"}), cdef.choose(cands, {"A", "B", "C"})
        if clean(tf_c, {"A", "B", "C"}, 2):
            assert clean(safe_c, {"A", "B", "C"}, 2), cands
    assert not clean((None, 3, 0, ["A", "X", "Y"], 0), {"A", "B", "C"}, 2)
    assert verdict_of(6, 1, 1) == "truncated" and verdict_of(6, 2, 0) == "complete"
    assert verdict_of(0, 0, 0) == "complete" and verdict_of(-2, 0, 0) == "type-first-earlier"
    # the census's witness tally round-trips, so `--check` compares counts and not strings
    assert parse_mix(mix(collections.Counter({"group-end": 3, "candidate": 2}))) == {"candidate": 2,
                                                                                    "group-end": 3}
    print("ok   class-defaults-promoted: synthetic tag spans, both orderings and every verdict")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("system", nargs="?", help="the System directory holding the .u packages")
    ap.add_argument("--class", dest="cls", help="one class name")
    ap.add_argument("--sweep", action="store_true", help="walk every class of every package")
    ap.add_argument("-o", "--out", help="artifact directory")
    ap.add_argument("--report", action="store_true", help="summarise the committed artifact")
    ap.add_argument("--check", action="store_true", help="dump-free self-test and artifact reconciliation")
    ap.add_argument("--self-test", action="store_true", help="the synthetic half of --check only")
    ap.add_argument("--show", action="store_true", help="print one line per promoted class while sweeping")
    args = ap.parse_args()
    if args.self_test:
        return self_check()
    if args.check:
        return self_check() or check()
    if args.report:
        return report()
    if not args.system:
        ap.error("a System directory, or --report/--check")
    if args.cls:
        rows, tally, _cw, _pw = walk_packages(args.system, only=args.cls, show=True)
        print("%d promoted row(s) for %s" % (len(rows), args.cls))
        return 0
    if not args.sweep or not args.out:
        ap.error("--sweep needs -o")
    return write_sweep(args.system, args.out, show=args.show)


if __name__ == "__main__":
    sys.exit(main())
