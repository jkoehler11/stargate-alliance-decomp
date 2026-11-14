#!/usr/bin/env python3
"""Whether the published one-tag starts are blocks: the tag stream walked from the start, for both populations.

`inventory/class_defaults_per_class.csv` publishes a start (`defaults = 1`) for **2,181** classes, and **196** of
those starts parse a block that names **none** of the class's declared properties (`declared_fraction = 0`). A
byte test cannot settle those rows -- the payload is ordinary package bytes -- so `--sweep` walks the grammar the
locator uses, from the published start, and reports where it stops and what every tag it reads names:

* **where the grammar stops** -- a `None` name terminates a block, so a parse either runs to the payload's last
  byte (`payload-end`) or terminates early with bytes left (`none-tag`, carrying `left`), or overruns / hits the
  property limit;
* **what each tag names**, classified against the package and the corpus: a property this class declares
  (`declared`), a property *another* class declares (`elsewhere` -- a real property name, so the nearest a
  false start gets), a `function` or `class` export of the same package, a `package` name (a name-table entry
  that can never be a property of any class), or a plain `name` (a state, a variable, a label);
* **whether the payload holds a declared block anywhere at all** -- every offset of the payload is walked and
  each `>= MIN_PROPERTIES`-tag parse that ends on the payload's last byte is counted, with the ones naming a
  declared property counted separately (`n_block`, `n_declared`, `best_declared`). The locator picks the
  best-scoring candidate, so a declared candidate at another offset would have been chosen; counting it here is
  the check that no reading of these bytes names a declared property, and `n_block` is the grammar's own base
  rate on that payload -- how many offsets open a block that parses to the end.

Verdicts, per row of `--sweep`:

* `declares-nothing` -- the class and its ancestors declare no property, so no tag could ever be declared
  (measured: 0 of 196);
* `no-property-tag` -- every tag the parse reads names something that is not a property of any class;
* `property-elsewhere` -- at least one tag is a real property name, of another class.

`--gained` is the other half of the same question, and the population the two-property floor withholds. The
record's closing group is a rule in `tools/class-defaults.py`'s `guarded()`, which adds the group's **end** as a
candidate when a one-property block there names a declared property and tiles the payload. `tools/defaults-emit.py`
does not choose from that set, so those starts are blocks the locator settles and the renderer does not: 88 of
them, every one a single declared tag (`--reconcile`). This mode walks the same grammar from each of those starts,
and from the locator's *own* one-tag starts (the 288 `single-declared` misses) as its control population, and
records the four things a one-tag start needs to be a block rather than a table tail:

* what the single tag names and whether its wire type is one the declared property's kind allows (`type_bad`);
* what follows the terminator -- `left` bytes, and `after`: the tags a parse at the terminator reads. A second
  tag there means the block was truncated by a `None` in the middle of a stream, not that it holds one property;
* whether the start is a group boundary -- `group_ends` is how many committed groups end exactly on it and
  `group_cover` how many span it (a covered start is inside a record field, so its bytes cannot be the block's);
* whether any later offset of the payload opens a `>= 2`-tag parse of declared names that ends on the payload's
  last byte (`alt2`): if one did, the one-tag start is the wrong reading of the same bytes.

Verdicts, per row of `--gained`: `declared-single` (one declared tag, an agreeing type, nothing after it, no
group covering it, no two-tag alternative), `second-tag`, `no-declared-tag`, `type-contradiction`, `inside-group`,
`not-single`, `no-terminator`, `empty`, `start-past-payload`.

    python3 tools/class-defaults-silent.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-defaults-silent.py --report
    python3 tools/class-defaults-silent.py --dump $SG1_DUMP --class Engine.FileLog
    python3 tools/class-defaults-silent.py --dump $SG1_DUMP --gained -o inventory
    python3 tools/class-defaults-silent.py --report-gained
    python3 tools/class-defaults-silent.py --check        # synthetic bodies, no dump, writes nothing

Reads the packages and three committed tables; no boot, no Ghidra round, no property value decoded.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402

PER_CLASS = "class_defaults_per_class.csv"
PROPERTIES = "class_properties.csv"
MISSES = "class_defaults_misses.csv"
ARTIFACT = "class_defaults_silent.csv"
GAINED_ARTIFACT = "class_defaults_single.csv"
MIN_PROPERTIES = 2                      # the locator's own floor, so the base rate is measured at the same bar
MAX_SCAN = 20000                        # a payload longer than this is not scanned for a two-tag alternative
COLUMNS = ["package", "class", "payload", "start", "props", "length", "stop", "left", "to_end", "first", "last",
           "first_kind", "kinds", "nonproperty", "elsewhere", "declared", "n_block", "n_declared", "best_declared",
           "verdict"]
GAINED_COLUMNS = ["population", "package", "class", "payload", "start", "origin", "props", "length", "stop", "left",
                  "after", "name", "wire", "declared_type", "type_bad", "group_ends", "group_cover", "alt2",
                  "declared_total", "verdict"]


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


cdef = _load("class-defaults")
pstream = _load("prop-stream")


def read(path):
    with open(os.path.join(ROOT, "inventory", path), newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def declared_for(cls, decl, supers):
    names, cur, seen = set(), cls, set()
    while cur and cur not in seen:
        seen.add(cur)
        names |= decl.get(cur, set())
        cur = supers.get(cur)
    return names


def rows_of_interest():
    """`(package, class)` -> row with a `block_start`, for the starts that name no declared property.

    `class_defaults_per_class.csv` no longer publishes these: the label is withheld from all 196 of them (they are
    `no block` there now), so the only record of *which* starts this artifact measured is the artifact itself. The
    fallback reads it rather than writing an empty file over a 196-row measurement -- a reproduction, not a reset.
    """
    out = {}
    for r in read(PER_CLASS):
        if not r.get("block_start"):
            continue
        if float(r.get("declared_fraction") or 0) != 0.0:
            continue
        out[(r["package"], r["class"])] = r
    if not out:
        try:
            out = {(r["package"], r["class"]): dict(r, block_start=r["start"]) for r in read(ARTIFACT)}
        except OSError:
            out = {}
    return out


def vocabulary(pkgname, pkg, rows):
    """What a tag's name can be in this package, besides the class's own declared set."""
    classes, funcs = set(), set()
    for e in pkg.exports:
        nm, cn = pkg.name(e["name"]), pkg.class_name_of(e)
        if cn == "Class":
            classes.add(nm)
        elif cn == "Function":
            funcs.add(nm)
    return {"class": classes, "function": funcs, "package": {p for p, _ in rows}}


def kind_of(name, decl, props_any, vocab):
    """What a tag's name is: a property of the class, of another class, or no property at all."""
    if name in decl:
        return "declared"
    if name in props_any:
        return "elsewhere"
    for k in ("function", "class", "package"):
        if name in vocab[k]:
            return k
    return "name"


def walk(data, pkg, start, decl, props_any, vocab):
    """The tag stream from the published start, and the same grammar read at every offset of the payload."""
    rec = {c: "" for c in COLUMNS}
    rec.update({"payload": len(data), "start": start})
    if start > len(data):
        rec["stop"] = "start-past-payload"
        rec["verdict"] = "start-past-payload"
        return rec
    if not decl:
        # nothing to name: reported, never called a false start
        rec.update({"stop": "declares-nothing", "verdict": "declares-nothing"})
        return rec
    try:
        props, length, reason = pstream.read_block(data[start:], pkg, False)
    except Exception:
        rec.update({"stop": "unreadable", "verdict": "unreadable"})
        return rec
    names = [p["name"] for p in props]
    kinds = [kind_of(n, decl, props_any, vocab) for n in names]
    end = start + length
    if not props:
        stop = "empty"
    elif reason != "None":
        stop = "overrun" if reason == "overrun" else "limit"
    elif end == len(data):
        stop = "payload-end"
    else:
        stop = "none-tag"
    rec.update({"props": len(props), "length": length, "stop": stop,
                "left": max(0, len(data) - end) if reason == "None" else "",
                "to_end": 1 if stop == "payload-end" else 0,
                "first": names[0] if names else "", "last": names[-1] if names else "",
                "first_kind": kinds[0] if kinds else "", "kinds": ",".join(kinds),
                "nonproperty": sum(1 for k in kinds if k not in ("declared", "elsewhere")),
                "elsewhere": sum(1 for k in kinds if k == "elsewhere"),
                "declared": sum(1 for k in kinds if k == "declared")})
    n_block = n_declared = best = 0
    for off in range(len(data)):
        try:
            cand, stop_at, why = pstream.read_block(data[off:], pkg, False)
        except Exception:
            continue
        if why != "None" or len(cand) < MIN_PROPERTIES or off + stop_at != len(data):
            continue
        n_block += 1
        known = sum(1 for p in cand if p["name"] in decl)
        if known:
            n_declared += 1
        best = max(best, known)
    rec.update({"n_block": n_block, "n_declared": n_declared, "best_declared": best})
    if rec["declared"] == len(kinds):
        rec["verdict"] = "declared"
    elif rec["declared"]:
        rec["verdict"] = "has-declared-tag"
    elif rec["elsewhere"]:
        rec["verdict"] = "property-elsewhere"
    else:
        rec["verdict"] = "no-property-tag"
    return rec


def single_walk(data, pkg, start, decl, kinds, population, pkgname, cls, origin):
    """One-tag start: what the tag names, what follows it, and the group structure on both sides of it."""
    rec = {c: "" for c in GAINED_COLUMNS}
    rec.update({"population": population, "package": pkgname, "class": cls, "payload": len(data), "start": start,
                "origin": origin, "declared_total": len(decl)})
    if not (0 <= start < len(data)):
        rec["verdict"] = "start-past-payload"
        return rec
    intervals = cdef.group_intervals(data, len(pkg.names or []), len(pkg.imports or []))
    rec["group_ends"] = sum(1 for q, e in intervals if e == start)
    rec["group_cover"] = sum(1 for q, e in intervals if q < start < e)
    try:
        props, length, reason = pstream.read_block(data[start:], pkg, False)
    except Exception:
        rec["verdict"] = "unreadable"
        return rec
    end = start + length
    rec.update({"props": len(props), "length": length, "stop": reason})
    if not props:
        rec["verdict"] = "empty"
        return rec
    if len(props) > 1:
        rec["verdict"] = "not-single"
        return rec
    tag = props[0]
    rec.update({"name": tag["name"], "wire": tag["type"],
                "declared_type": (kinds or {}).get(tag["name"], ""),
                "type_bad": cdef.contradictions(props, kinds) if kinds else ""})
    if reason != "None":
        rec["verdict"] = "no-terminator"
        return rec
    left = max(0, len(data) - end)
    rec["left"] = left
    if left:
        # Does a second tag follow the terminator? A `None` inside a tag stream is a block end, so a tag here
        # means this parse stopped in the middle of something longer.
        try:
            nxt, stop2, why2 = pstream.read_block(data[end:], pkg, False)
        except Exception:
            nxt, stop2, why2 = [], 0, "unreadable"
        rec["after"] = "%d:%s:%s" % (len(nxt), why2, ",".join(p["name"] for p in nxt[:3]))
        if nxt:
            rec["verdict"] = "second-tag"
            return rec
    alt2 = 0
    if len(data) <= MAX_SCAN:
        for off in range(start + 1, len(data)):
            try:
                cand, stop_at, why = pstream.read_block(data[off:], pkg, False)
            except Exception:
                continue
            if why != "None" or len(cand) < 2 or off + stop_at != len(data):
                continue
            if all(p["name"] in decl for p in cand):
                alt2 += 1
    rec["alt2"] = alt2
    if tag["name"] not in decl:
        rec["verdict"] = "no-declared-tag"
    elif rec["type_bad"]:
        rec["verdict"] = "type-contradiction"
    elif rec["group_cover"]:
        rec["verdict"] = "inside-group"
    elif left:
        rec["verdict"] = "trailing-bytes"
    elif alt2:
        rec["verdict"] = "two-tag-alternative"
    elif not rec["group_ends"]:
        rec["verdict"] = "no-group-boundary"
    else:
        rec["verdict"] = "declared-single"
    return rec


def choose_scored(cands, decl):
    """`cdef.choose` under the named-property floor `tools/defaults-emit.py` applies (its `choose_scored`)."""
    best = cdef.choose(cands, decl)
    if best is None:
        return None, "no-block"
    if not any(n in decl for n in best[3]):
        return None, "no-declared-name"
    return best, None


def control_rows():
    """The locator's own one-tag starts: every `single-declared` row of the misses table."""
    return {(r["package"], r["class"]): r for r in read(MISSES) if r.get("reason") == "single-declared"}


def gained(args):
    """Walk the guard's 88 added starts and the locator's own one-tag starts; write `class_defaults_single.csv`."""
    control = control_rows()
    starts = {(r["package"], r["class"]): r["block_start"] for r in read(PER_CLASS) if r.get("block_start")}
    decl_all, supers = cdef.declared_properties(), cdef.super_map()
    out = []
    system = os.path.join(args.dump, "System")
    sysdir = system if os.path.isdir(system) else args.dump      # --dump is the game directory, like --sweep
    for fname in sorted(f for f in os.listdir(sysdir) if f.lower().endswith(".u")):
        pkgname = fname[:-2]
        pkg = Package(os.path.join(sysdir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            cls = pkg.name(exp["name"])
            decl = declared_for(cls, decl_all, supers)
            kinds = cdef.kinds_for_class(cls)
            cands, data = cdef.candidates(pkg, pstream, exp, kinds=kinds)
            best_u, _reason = choose_scored(cands, decl)
            guarded, origins = cdef.guarded(cands, data, pkg, decl, kinds, len(pkg.imports or []))
            best_g = cdef.choose(guarded, decl)
            here = "block" if best_u else "no-block"
            there = "no-block" if best_g is None else (
                "block" if any(n in decl for n in best_g[3]) else "no-declared-name")
            if there == "block" and here != "block":
                out.append(single_walk(data, pkg, best_g[2], decl, kinds, "guard-adds", pkgname, cls,
                                       origins.get(best_g[2], "?")))
            if (pkgname, cls) in control and starts.get((pkgname, cls)):
                out.append(single_walk(data, pkg, int(starts[(pkgname, cls)]), decl, kinds, "locator-single",
                                       pkgname, cls, "published"))
        print("done %s: %d rows" % (pkgname, len(out)), flush=True)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, GAINED_ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=GAINED_COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(out)
        print("wrote %s: %d rows" % (path, len(out)))
    return report_gained(out)


def sweep(args):
    rows = rows_of_interest()
    if not rows:
        print("no rows: neither class_defaults_per_class.csv nor class_defaults_silent.csv names a start to walk",
              file=sys.stderr)
        return 2
    decl_all = cdef.declared_properties()
    supers = cdef.super_map()
    props_any = collections.defaultdict(set)
    for r in read(PROPERTIES):
        props_any[r["property"]].add(r["class"])

    by_pkg = collections.defaultdict(list)
    for (pkgname, cls), row in rows.items():
        by_pkg[pkgname].append((cls, row))
    out = []
    for pkgname in sorted(by_pkg):
        pkg = Package(os.path.join(args.dump, "System", pkgname + ".u"))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        vocab = vocabulary(pkgname, pkg, rows)
        for cls, row in sorted(by_pkg[pkgname]):
            exp = next((e for e in pkg.exports
                        if pkg.class_name_of(e) == "Class" and pkg.name(e["name"]) == cls), None)
            rec = {"package": pkgname, "class": cls}
            if exp is None:
                rec["verdict"] = "no-class-export"
                out.append(rec)
                continue
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            rec.update(walk(data, pkg, int(row["block_start"]), declared_for(cls, decl_all, supers),
                            props_any, vocab))
            rec.setdefault("package", pkgname)
            rec["package"], rec["class"] = pkgname, cls
            out.append(rec)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(out)
        print("wrote %s: %d rows" % (path, len(out)))
    report(out)
    return 0


def _pct(n, d):
    return "%.1f%%" % (100.0 * n / d) if d else "-"


def _int(v):
    return int(v or 0)


def report(rows=None):
    if rows is None:
        rows = read(ARTIFACT)
    print("rows: %d over %d packages" % (len(rows), len({r["package"] for r in rows})))
    print("verdicts: %s" % collections.Counter(r["verdict"] for r in rows).most_common())
    print("where the grammar stops: %s" % collections.Counter(r["stop"] for r in rows).most_common())
    early = [r for r in rows if r["stop"] == "none-tag"]
    if early:
        left = sorted(int(r["left"]) for r in early)
        print("  the %d early stops leave %d..%d bytes after the None tag (median %d)"
              % (len(early), left[0], left[-1], left[len(left) // 2]))
    print("tags the parse reads: %s"
          % collections.Counter(k for r in rows for k in r["kinds"].split(",") if k).most_common())
    print("first tag: %s" % collections.Counter(r["first_kind"] for r in rows).most_common())
    nonproperty = sum(int(r["nonproperty"]) for r in rows)
    tags = sum(int(r["props"]) for r in rows)
    print("tags that name no property of any class: %d of %d (%s)"
          % (nonproperty, tags, _pct(nonproperty, tags)))
    withpkg = sum(1 for r in rows if "package" in str(r["kinds"]).split(","))
    print("rows whose parse reads a package name (never a property of any class): %d of %d (%s)"
          % (withpkg, len(rows), _pct(withpkg, len(rows))))
    n_decl = sum(1 for r in rows if int(r["n_declared"] or 0))
    print("payloads holding a declared block at ANY offset: %d of %d" % (n_decl, len(rows)))
    print("offsets opening a >=%d-tag block to the payload end (the grammar's base rate): %s"
          % (MIN_PROPERTIES, collections.Counter(int(r["n_block"] or 0) for r in rows).most_common(8)))
    share = [int(r["n_block"] or 0) / float(r["payload"]) for r in rows if r["payload"]]
    if share:
        print("  as a share of the payload: median %.2f, largest %.2f"
              % (sorted(share)[len(share) // 2], max(share)))
    else_rows = [r for r in rows if r["verdict"] == "property-elsewhere"]
    if else_rows:
        print("the %d rows whose parse reads a real property name of another class:" % len(else_rows))
        for r in else_rows:
            print("  %-40s start %-5s %-9s props %-2s %s" % ("%s.%s" % (r["package"], r["class"]), r["start"],
                                                            r["stop"], r["props"], r["kinds"]))
    return 0


def _population_report(label, rows):
    print("%s: %d rows over %d packages" % (label, len(rows), len({r["package"] for r in rows})))
    if not rows:
        return
    print("  verdicts: %s" % collections.Counter(r["verdict"] for r in rows).most_common())
    print("  properties per block: %s" % collections.Counter(r["props"] for r in rows).most_common(5))
    print("  bytes left after the terminator: %s" % collections.Counter(r["left"] for r in rows).most_common(5))
    print("  tags naming a declared property: %d of %d, type-contradicting %d"
          % (sum(1 for r in rows if r["name"] and r["verdict"] != "no-declared-tag"), len(rows),
             sum(1 for r in rows if _int(r["type_bad"]))))
    print("  the start is a committed group's end on %d, covered by one on %d, no group end on %d"
          % (sum(1 for r in rows if _int(r["group_ends"])), sum(1 for r in rows if _int(r["group_cover"])),
             sum(1 for r in rows if not _int(r["group_ends"]))))
    print("  group parses ending exactly on the start: %s"
          % collections.Counter(r["group_ends"] for r in rows).most_common(5))
    print("  a two-declared-tag parse at a later offset, to the payload end: %d rows"
          % sum(1 for r in rows if _int(r["alt2"])))
    print("  payload bytes: min %d, median %d, max %d"
          % (min(_int(r["payload"]) for r in rows),
             sorted(_int(r["payload"]) for r in rows)[len(rows) // 2], max(_int(r["payload"]) for r in rows)))
    print("  classes declare: median %d properties, min %d, max %d"
          % (sorted(_int(r["declared_total"]) for r in rows)[len(rows) // 2],
             min(_int(r["declared_total"]) for r in rows), max(_int(r["declared_total"]) for r in rows)))
    print("  wire types: %s" % collections.Counter(r["wire"] for r in rows).most_common(12))
    print("  declared kinds: %s" % collections.Counter(r["declared_type"] for r in rows).most_common(12))


def report_gained(rows=None):
    if rows is None:
        rows = read(GAINED_ARTIFACT)
    pops = collections.defaultdict(list)
    for r in rows:
        pops[r["population"]].append(r)
    for label in ("guard-adds", "locator-single"):
        _population_report(label, pops.get(label, []))
    adds = {("%s|%s" % (r["package"], r["class"])): r for r in pops.get("guard-adds", [])}
    ctrl = {("%s|%s" % (r["package"], r["class"])): r for r in pops.get("locator-single", [])}
    if adds and ctrl:
        both = set(adds) & set(ctrl)
        same = [k for k in both if adds[k]["start"] == ctrl[k]["start"]]
        print("classes in both populations: %d, the two paths choose the same start on %d" % (len(both), len(same)))
        print("guard-adds classes with no published one-tag row: %d" % len(set(adds) - set(ctrl)))
        same_keys = ("verdict", "props", "left", "group_ends", "alt2", "type_bad")
        for k in same_keys:
            a = collections.Counter(r[k] for r in adds.values())
            c = collections.Counter(r[k] for r in ctrl.values())
            print("  %-11s guard-adds %-28s locator-single %s" % (k, a.most_common(3), c.most_common(3)))
    return 0


def one(args):
    if not args.dump or not args.cls:
        print("--class needs --dump", file=sys.stderr)
        return 2
    pkgname, cls = args.cls.split(".", 1)
    rows = rows_of_interest()
    row = rows.get((pkgname, cls))
    if row is None:
        print("not a published zero-fraction class: %s" % args.cls, file=sys.stderr)
        return 2
    decl_all, supers = cdef.declared_properties(), cdef.super_map()
    props_any = collections.defaultdict(set)
    for r in read(PROPERTIES):
        props_any[r["property"]].add(r["class"])
    pkg = Package(os.path.join(args.dump, "System", pkgname + ".u"))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    exp = next((e for e in pkg.exports
                if pkg.class_name_of(e) == "Class" and pkg.name(e["name"]) == cls), None)
    data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    rec = walk(data, pkg, int(row["block_start"]), declared_for(cls, decl_all, supers), props_any,
               vocabulary(pkgname, pkg, rows))
    print("%s.%s: payload %d, published start %s, declared properties %d"
          % (pkgname, cls, len(data), row["block_start"], len(declared_for(cls, decl_all, supers))))
    for k in ("props", "length", "stop", "left", "first", "last", "kinds", "n_block", "n_declared",
              "best_declared", "verdict"):
        print("  %-14s %s" % (k, rec.get(k)))
    return 0


def self_check():
    """Synthetic bodies: a function tag, an early None, a declared block deeper in, and an empty vocabulary."""

    class _Pkg:
        exports = []
        imports = [{"class": 1, "class_package": 2, "name": 3}, {"class": 1, "class_package": 2, "name": 4}]
        names = ["None"] + ["N%d" % i for i in range(1, 40)]

        def name(self, i):
            return self.names[i] if 0 <= i < len(self.names) else "?"

    pkg = _Pkg()
    vocab = {"class": set(), "function": {"N5"}, "package": {"Engine"}}
    props_any = {"N7": {"Other"}}
    # two tags, the first naming a function export: no property at all, and the block ends on the payload's last byte
    body = b"\x01\x02\x03\x04" + b"\x05\x01\x07" + b"\x05\x01\x07" + b"\x00"
    rec = walk(body, pkg, 4, {"N3", "N4"}, props_any, vocab)
    assert rec["verdict"] == "no-property-tag" and rec["stop"] == "payload-end", rec
    assert rec["first_kind"] == "function" and rec["nonproperty"] == int(rec["props"]), rec
    # the same block with a None tag and five bytes left: reported as stopping early, with the left count
    early = b"\x01\x02\x03\x04" + b"\x05\x01\x07" + b"\x05\x01\x07" + b"\x00" + b"\xaa" * 5
    rec = walk(early, pkg, 4, {"N3", "N4"}, props_any, vocab)
    assert rec["stop"] == "none-tag" and int(rec["left"]) == 5 and not int(rec["to_end"]), rec
    # a declared block sits at offset 0 of the payload, so the payload is not silent at all
    good = b"\x03\x01\x07" + b"\x04\x01\x09" + b"\x00"
    rec = walk(good, pkg, 0, {"N3", "N4"}, props_any, vocab)
    assert int(rec["n_declared"]) >= 1 and int(rec["best_declared"]) == 2, rec
    assert rec["verdict"] == "declared" and rec["first_kind"] == "declared" and int(rec["to_end"]) == 1, rec
    # one declared tag among two is `has-declared-tag`, the shape the population must not contain
    rec = walk(good[:-1] + b"\x05\x01\x07\x00", pkg, 0, {"N3", "N4"}, props_any, vocab)
    assert rec["verdict"] == "has-declared-tag", rec
    # a tag naming another class's property is `elsewhere`, not a property of this class
    rec = walk(b"\x07\x01\x09" + b"\x05\x01\x07" + b"\x00", pkg, 0, {"N3"}, props_any, vocab)
    assert rec["first_kind"] == "elsewhere" and rec["declared"] == 0, rec
    # a class that declares nothing is never called a false start
    rec = walk(body, pkg, 4, set(), props_any, vocab)
    assert rec["verdict"] == "declares-nothing", rec

    # --gained: a group (names array, negative reference, config name, list array) then one declared tag
    # `[01 05]` names array {N5}, `81` is compact -1 (a reference the import table holds), `[01]` config name,
    # `[00]` empty list array -- then one declared tag `N3` bool true, then the None.
    group = b"\x01\x05" + b"\x81" + b"\x01" + b"\x00"
    tag = b"\x03\x03\x00"                 # name N3, a Bool tag whose value rides in the info byte
    one_tag = group + tag + b"\x00"
    kinds = {"N3": "UBoolProperty"}
    ivs = cdef.group_intervals(one_tag, len(pkg.names), len(pkg.imports))
    assert any(e == len(group) for _q, e in ivs), ivs
    rec = single_walk(one_tag, pkg, len(group), {"N3"}, kinds, "guard-adds", "P", "C", "group-end")
    assert rec["verdict"] == "declared-single" and rec["props"] == 1 and rec["group_ends"] >= 1, rec
    assert int(rec["left"]) == 0 and _int(rec["alt2"]) == 0 and not _int(rec["group_cover"]), rec
    # a tag with a same-width wire type the declared kind forbids is a contradiction, not a block
    kinds_bad = {"N3": "UFloatProperty"}
    rec = single_walk(one_tag, pkg, len(group), {"N3"}, kinds_bad, "guard-adds", "P", "C", "group-end")
    assert rec["verdict"] == "type-contradiction", rec
    # a second tag after the terminator: the parse stopped in the middle of a stream, so the block is not one tag
    second = group + tag + b"\x00" + tag + b"\x00"
    rec = single_walk(second, pkg, len(group), {"N3"}, kinds, "guard-adds", "P", "C", "group-end")
    assert rec["verdict"] == "second-tag" and rec["after"].startswith("1:"), rec
    # two declared tags from the start is not this population at all
    rec = single_walk(group + tag + tag + b"\x00", pkg, len(group), {"N3"}, kinds, "guard-adds", "P", "C", "x")
    assert rec["verdict"] == "not-single", rec
    # a start that no committed group ends on has no boundary witness
    rec = single_walk(b"\x05" + tag + b"\x00", pkg, 1, {"N3"}, kinds, "guard-adds", "P", "C", "candidate")
    assert rec["verdict"] == "no-group-boundary", rec
    print("self-check ok: a function tag is not a property, an early None keeps its byte count, a declared block "
          "deeper in is counted, another class's property is `elsewhere`, a class with no properties is reported "
          "as such, and a one-tag start is a block only with an agreeing type, nothing after it, no covering "
          "group and a group ending on it -- with a second tag and a two-tag block refused")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", help="dump root (the directory holding System/)")
    ap.add_argument("--class", dest="cls", help="one class, package.Class")
    ap.add_argument("--sweep", action="store_true", help="walk every published zero-fraction payload")
    ap.add_argument("--gained", action="store_true",
                    help="walk the guard's added one-tag starts and the locator's own, into class_defaults_single.csv")
    ap.add_argument("-o", "--out", help="artifact directory")
    ap.add_argument("--report", action="store_true", help="summarise the committed artifact")
    ap.add_argument("--report-gained", action="store_true", help="summarise the committed one-tag artifact")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.report:
        return report()
    if args.report_gained:
        return report_gained()
    if args.cls:
        return one(args)
    if args.gained:
        if not args.dump:
            ap.error("--gained needs --dump")
        return gained(args)
    if not args.sweep or not args.dump:
        ap.error("--sweep needs --dump")
    return sweep(args)


if __name__ == "__main__":
    sys.exit(main())
