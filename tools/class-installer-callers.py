#!/usr/bin/env python3
"""Who calls the body that installs a class table: the fourth channel, read from the callers.

`docs/systems/CLASS_REGISTRATION.md` reads a nameless table from the body that stores it -- its
literals, the map's names among its callees, and the parent constructor its first call reaches --
and all three place a table in a family without naming it. This tool asks the other half of the
call edge: **which bodies call the installer**. It resolves every installer body of the 724-table
census (`inventory/native_vtable_reach.csv`) through `symbols/xbox_functions.csv`, scans `.text` for
the `E8` calls that target one, and names the enclosing body from the same map.

Two populations answer the question from opposite sides. On the 474 tables the registration path
names, the callers are the derived classes' constructors and destructors, and the deepest common
ancestor of those classes is the table's class -- that is the control. On the 250 tables no
registration names, the same join either names the class whose C++ constructor builds the table
(a *second* table of a class that already owns one, or a base whose own class owns none), or finds
callers that are not constructors at all, which says the store is an inlined construction inside an
engine body rather than an install.

    tools/class-installer-callers.py --xbe DUMP/default.xbe -o inventory
    tools/class-installer-callers.py --report
    tools/class-installer-callers.py --write-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-installer-callers.py --check-doc docs/systems/CLASS_REGISTRATION.md  # committed CSV, no dump
    tools/class-installer-callers.py --check        # synthetic callers, no dump, writes nothing

The dump is never committed; the artifact and the page block are. Addresses and symbol names only.
"""
import argparse
import bisect
import collections
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

REACH = os.path.join(ROOT, "inventory", "native_vtable_reach.csv")
EDGES = os.path.join(ROOT, "inventory", "class_table_parent_edges.csv")
SHAPES = os.path.join(ROOT, "inventory", "class_table_installer_shapes.csv")
NAMES = os.path.join(ROOT, "inventory", "xbe_class_table_names.csv")
CLASSES = os.path.join(ROOT, "inventory", "classes.csv")
MAP = os.path.join(ROOT, "symbols", "xbox_functions.csv")
OUT = os.path.join(ROOT, "inventory", "class_installer_callers.csv")
PAGE = os.path.join(ROOT, "docs", "systems", "CLASS_REGISTRATION.md")
DOC_START = "<!-- generated:class-installer-callers -->"
DOC_END = "<!-- /generated:class-installer-callers -->"
FIELDS = ["table_va", "population", "shape", "slots", "class", "installers", "callers", "caller_classes",
          "caller_kinds", "owner_candidate", "owner_table", "owner_shared", "relation", "verdict", "evidence"]
POPS = ("named", "at-least", "plain-only", "no-store")


def read(path):
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def symbol_map(rows):
    """(sorted starts, addr -> (name, size), name -> addr); the size column is bytes in DECIMAL."""
    starts, info, byname = [], {}, {}
    for r in rows:
        try:
            a = int(r["address"], 16)
            s = int(r["size"] or 0)
        except (KeyError, ValueError):
            continue
        starts.append(a)
        info[a] = (r.get("name", ""), s)
        byname.setdefault(r.get("name", ""), a)
    starts.sort()
    return starts, info, byname


def owner(starts, info, va):
    i = bisect.bisect_right(starts, va) - 1
    if i < 0:
        return ""
    a = starts[i]
    name, size = info[a]
    return name if (size == 0 or va < a + size) else ""


def symbol_class(name):
    """`ctor_Texture` -> `Texture`, `dtor_Engine_UnObj` -> `` (its class is the more specific part)."""
    for pre in ("ctor_", "dtor_"):
        if name.startswith(pre):
            return name[len(pre):]
    return ""


def super_map(rows):
    sup = {}
    for r in rows:
        cls = (r.get("class") or "").strip()
        if not cls:
            continue
        s = (r.get("super") or "").strip()
        if cls not in sup or not sup[cls]:
            sup[cls] = s.split(".")[-1] if s else ""
    return sup


def chain(sup, cls, depth=40):
    out, seen, cur = [], set(), cls
    while cur and cur not in seen and len(out) < depth:
        seen.add(cur)
        cur = sup.get(cur, "")
        if cur:
            out.append(cur)
    return out


def dca(sup, classes):
    """The deepest class every caller class descends from, over the script chain."""
    cs = [c for c in classes if c]
    if not cs:
        return ""
    sets = [set([c] + chain(sup, c)) for c in cs]
    for c in [cs[0]] + chain(sup, cs[0]):
        if all(c in s for s in sets):
            return c
    return ""


def scan(xbe, bodies):
    """Every `E8` site in `.text` whose target is one of `bodies`, as (target -> [caller names])."""
    code = next(s for s in xbe.sections if s.name == ".text")
    text = xbe.data[code.foff:code.foff + min(code.fsize, code.vsize)]
    callers = collections.defaultdict(list)
    for i in range(len(text) - 5):
        if text[i] != 0xE8:
            continue
        site = code.va + i
        t = site + 5 + struct.unpack_from("<i", text, i + 1)[0]
        if t in bodies:
            callers[t].append(site)
    return callers


def harvest(reach, shapes, names, class_rows, map_rows, xbe):
    starts, info, byname = symbol_map(map_rows)
    sup = super_map(class_rows)
    known = {r["class"] for r in class_rows if r.get("class")} | \
            {r["class"] for r in names if r.get("class")}
    for r in read(EDGES):                     # the native parent edge, for classes with no script row
        c, p = (r.get("class") or "").strip(), (r.get("parent_class") or "").strip()
        if c and p:
            sup.setdefault(c, p)
    named = {}
    for r in names:
        if (r.get("class") or "").strip():
            named[int(r["table_va"], 16)] = r["class"].strip()
    byclass = {}
    for va, cls in sorted(named.items()):
        byclass.setdefault(cls, va)
    shapes_by = {int(r["table_va"], 16): r for r in shapes}
    targets, bodies = {}, set()
    for r in reach:
        va = int(r["table_va"], 16)
        addrs = []
        for t in (r.get("install_bodies") or "").replace(",", " ").split():
            a = int(t, 16) if t.startswith("0x") else byname.get(t)
            if a:
                addrs.append(a)
                bodies.add(a)
        targets[va] = addrs
    callers = scan(xbe, bodies)
    rows = []
    for va in sorted(targets):
        shape = shapes_by.get(va, {})
        cls = named.get(va, "")
        cnames = []                      # names of the bodies that call an installer
        for b in targets[va]:
            for site in callers.get(b, []):
                cnames.append(owner(starts, info, site))
        cnames = sorted(set(c for c in cnames if c))
        cclasses = sorted({symbol_class(c) for c in cnames if symbol_class(c)})
        kinds = sorted({("ctor" if c.startswith("ctor_") else "dtor") for c in cnames
                        if c.startswith(("ctor_", "dtor_"))})
        cand = dca(sup, cclasses)
        own = byclass.get(cand, 0)
        row = {"table_va": "0x%08x" % va, "population": shape.get("population", ""),
               "shape": shape.get("verdict", ""), "slots": shape.get("slots", ""), "class": cls,
               "installers": str(len(targets[va])), "callers": str(len(cnames)),
               "caller_classes": "|".join(cclasses[:6]), "caller_kinds": "+".join(kinds),
               "owner_candidate": cand, "owner_table": "0x%08x" % own if own else "",
               "owner_shared": "", "relation": "", "verdict": "", "evidence": ""}
        if not targets[va]:
            row["verdict"] = "no-installer"
            row["evidence"] = "no installer body resolves through the symbol map"
        elif not cnames:
            row["verdict"] = "no-direct-call"
            row["evidence"] = "%d installer body(ies), no direct `E8` caller in .text" % len(targets[va])
        elif not cclasses:
            row["verdict"] = "callers-unnamed"
            row["evidence"] = "callers %s" % ", ".join(cnames[:3])
        elif cls:
            rel = "class" if cand == cls else ("ancestor" if cand in chain(sup, cls) else
                                               ("descendant" if cls in chain(sup, cand) else "other"))
            row["relation"] = rel
            row["verdict"] = "control-" + rel
            row["evidence"] = "%d caller class(es), dca %s" % (len(cclasses), cand or "-")
        elif not kinds:
            row["verdict"] = "inline-in-body"
            row["evidence"] = ("%d named caller(s), none a `ctor_`/`dtor_`: %s"
                               % (len(cnames), ", ".join(cnames[:3])))
        elif len(cclasses) > 1:
            row["verdict"] = "base-of-group"
            row["evidence"] = ("%d caller classes (%s), deepest common ancestor %s"
                               % (len(cclasses), ", ".join(cclasses[:4]), cand or "-"))
        elif cclasses and byclass.get(cclasses[0], 0) not in (0, va):
            row["verdict"] = "second-table"
            row["owner_table"] = "0x%08x" % byclass[cclasses[0]]
            row["evidence"] = ("its only caller class is %s, whose own table is %s"
                               % (cclasses[0], row["owner_table"]))
        else:
            row["verdict"] = "owner-unnamed"
            row["evidence"] = "caller classes %s, none with a table of its own" % ", ".join(cclasses[:4])
        rows.append(row)
    return rows


def counts(rows):
    by_pop = collections.defaultdict(collections.Counter)
    for r in rows:
        d = by_pop[r["population"]]
        d["tables"] += 1
        d["v:" + r["verdict"]] += 1
        d["called"] += 1 if r["verdict"] not in ("no-direct-call", "no-installer") else 0
        d["with-class"] += 1 if r["owner_candidate"] else 0
    return by_pop


def render(rows):
    c = counts(rows)
    out = ["| population | tables | installer body called directly (`E8`) | a caller class names the owner | no direct caller | no installer body |",
           "|---|---|---|---|---|---|"]
    for pop in POPS:
        d = c[pop]
        if not d["tables"]:
            continue
        out.append("| %s | %d | %d | %d | %d | %d |"
                   % (pop, d["tables"], d["called"], d["with-class"],
                      d["v:no-direct-call"], d["v:no-installer"]))
    named = c["named"]
    out.append("")
    out.append("**The control: on the %d named tables the callers are the derived classes, and their deepest "
               "common ancestor is the table's class on %d, one of its descendants on %d and an unrelated "
               "class on %d.** %d named tables are called only from bodies with no `ctor_`/`dtor_` symbol, "
               "and %d have no direct caller at all."
               % (named["tables"], named["v:control-class"], named["v:control-descendant"],
                  named["v:control-other"], named["v:callers-unnamed"], named["v:no-direct-call"]))
    plain, at = c["plain-only"], c["at-least"]
    out.append("")
    out.append("**Of the %d nameless tables, %d are built inside a class's own constructor -- %d by a group of "
               "sibling classes, so the table is the base their constructors and destructors reset to, and %d by a "
               "single class whose own table is a different address, a second table that class builds. %d are "
               "called only from bodies with no class symbol and %d are never called directly.**"
               % (plain["tables"] + at["tables"],
                  plain["v:base-of-group"] + at["v:base-of-group"] + plain["v:second-table"] + at["v:second-table"],
                  plain["v:base-of-group"] + at["v:base-of-group"],
                  plain["v:second-table"] + at["v:second-table"],
                  plain["v:callers-unnamed"] + at["v:callers-unnamed"],
                  plain["v:no-direct-call"] + at["v:no-direct-call"]))
    d55 = [r for r in rows if r["shape"] in ("dtor-only", "unclassified")]
    d55c = sum(1 for r in d55 if r["caller_classes"])
    d55n = collections.Counter(r["verdict"] for r in d55)
    out.append("")
    out.append("**Of the %d tables whose installer is destructor-shaped or neither, %d have callers that carry a "
               "`ctor_`/`dtor_` class symbol, and there the callers are the sibling destructors that reset the "
               "pointer to this table; %d are called only from bodies with no class symbol, %d are never called "
               "directly and %d have no installer body.**"
               % (len(d55), d55c, d55n["callers-unnamed"], d55n["no-direct-call"], d55n["no-installer"]))
    return out


def write_doc(path, rows, check=False):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(DOC_START), text.find(DOC_END)
    if i < 0 or j < 0:
        raise SystemExit("%s: no %s block" % (path, DOC_START))
    new = text[:i + len(DOC_START)] + "\n" + "\n".join(render(rows)) + "\n" + text[j:]
    if check:
        if new != text:
            raise SystemExit("%s: the class-installer-callers block is stale" % path)
        print("ok   class-installer-callers: the page block reproduces")
        return
    open(path, "w", encoding="utf-8").write(new)
    print("wrote %s" % path)


def write_csv(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in FIELDS})
    os.replace(tmp, path)
    print("wrote %s (%d rows)" % (path, len(rows)))


def self_check():
    """Synthetic callers: the symbol class, the chain join, and the three verdict inputs."""
    cases = [
        ("a ctor symbol names its class", symbol_class("ctor_Texture") == "Texture"),
        ("a dtor symbol names its class", symbol_class("dtor_Engine_UnObj") == "Engine_UnObj"),
        ("a plain body names no class", symbol_class("FUN_00012340") == ""),
        ("a chain reads nearest first", chain({"B": "A"}, "B") == ["A"]),
        ("one class is its own deepest ancestor", dca({}, ["A"]) == "A"),
        ("siblings share their parent", dca({"B": "A", "C": "A"}, ["B", "C"]) == "A"),
        ("a class and its child share the child", dca({"B": "A"}, ["A", "B"]) == "A"),
        ("unrelated classes share nothing", dca({"B": "A", "D": "C"}, ["B", "D"]) == ""),
        ("an unnamed caller is not a class", symbol_class("___security_init_cookie") == ""),
    ]
    bad = [n for n, ok in cases if not ok]
    for n, ok in cases:
        print("%s %s" % ("ok  " if ok else "FAIL", n))
    if bad:
        raise SystemExit("class-installer-callers: %d selftest case(s) failed" % len(bad))
    print("class-installer-callers: selftest passed (%d cases)" % len(cases))


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return self_check()
    src = read(OUT)
    if args.check_doc or args.write_doc:
        if not src:
            print("FAIL class-installer-callers: %s is missing" % OUT)
            return 1
        try:
            write_doc(args.check_doc or args.write_doc, src, check=bool(args.check_doc))
        except SystemExit as exc:
            print("FAIL class-installer-callers: %s" % exc)
            return 1
        return 0
    if args.report:
        print("\n".join(render(src)))
        return 0
    if not args.xbe:
        print("FAIL class-installer-callers: --xbe is required to read the image")
        return 1
    from xbe import Xbe
    xbe = Xbe(args.xbe)
    rows = harvest(read(REACH), read(SHAPES), read(NAMES), read(CLASSES), read(MAP), xbe)
    print("%d tables: %s" % (len(rows), ", ".join("%s %d" % (k, v["tables"])
                                                  for k, v in sorted(counts(rows).items()))))
    print("\n".join(render(rows)))
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        write_csv(os.path.join(args.out, os.path.basename(OUT)), rows)


if __name__ == "__main__":
    sys.exit(main() or 0)
