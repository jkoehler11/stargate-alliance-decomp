#!/usr/bin/env python3
"""Who calls the body that installs a class table: the fourth channel, read from the callers.

`docs/systems/CLASS_REGISTRATION.md` reads a nameless table from the body that stores it -- its
literals, the map's names among its callees, and the parent constructor its first call reaches --
and all three place a table in a family without naming it. This tool asks the other half of the
call edge: **which bodies call the installer**. It resolves every installer body of the 724-table
census (`inventory/native_vtable_reach.csv`) through `symbols/xbox_functions.csv`, scans `.text` for
the `E8` calls that target one, and names the enclosing body from the same map.

A call site the map does not cover is **not** a missing caller: the symbol map leaves 11,070 gaps, and
`inventory/gap_heads.csv` finds 1,451 decodable bodies in them. The first version of this census dropped
such a site with the name it could not build, so a table called only from a gap body read *no direct
caller* -- which is a report about the map, not about the image. `site_split()` keeps the two apart and
the verdict is `caller-gap`; `no-call` now means no `E8` in `.text` targets the installer at all.

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
GAPS = os.path.join(ROOT, "inventory", "gap_heads.csv")
UNREACHED = os.path.join(ROOT, "inventory", "native_unreachable.csv")
OUT = os.path.join(ROOT, "inventory", "class_installer_callers.csv")
PAGE = os.path.join(ROOT, "docs", "systems", "CLASS_REGISTRATION.md")
DOC_START = "<!-- generated:class-installer-callers -->"
DOC_END = "<!-- /generated:class-installer-callers -->"
FIELDS = ["table_va", "population", "shape", "slots", "class", "installers", "callers", "e8_sites",
          "gap_sites", "gap_bodies", "unreached", "caller_classes",
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


def site_split(sites, starts, info):
    """The `E8` sites that land inside a mapped body, and those the symbol map does not cover.

    A gap site is a real caller whose enclosing body the map has no row for: counting it as *no caller*
    reports the map's coverage as the image's, and the two populations are what the verdict has to keep
    apart.
    """
    named, gap = [], []
    for site in sites:
        (named if owner(starts, info, site) else gap).append(site)
    return named, gap


def unreached_split(bodies, unreached):
    """How many of a table's installer bodies the fixpoint also finds unreferenced."""
    return sum(1 for b in bodies if b in unreached), len(bodies)


def gap_bodies(rows):
    """[(head, end)] for the gap rows whose body the pad-and-prologue rule decoded."""
    out = []
    for r in rows:
        try:
            head, size = int(r["head_va"], 16), int(r["size"] or 0)
        except (KeyError, ValueError):
            continue
        if size > 0 and r.get("verdict") in ("body", "island"):
            out.append((head, head + size))
    out.sort()
    return out


def gap_owner(site, spans):
    """The decoded gap body a call site sits in, or 0: the map's gaps are only partly decoded."""
    for head, end in spans:
        if head <= site < end:
            return head
        if head > site:
            break
    return 0


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
    spans = gap_bodies(read(GAPS))
    unreached = set()
    for r in read(UNREACHED):
        if r.get("kind") == "body":
            try:
                unreached.add(int(r["at"], 16))
            except (KeyError, ValueError):
                pass
    rows = []
    for va in sorted(targets):
        shape = shapes_by.get(va, {})
        cls = named.get(va, "")
        sites = [s for b in targets[va] for s in callers.get(b, [])]
        named_sites, gap_sites = site_split(sites, starts, info)
        cnames = sorted(set(owner(starts, info, s) for s in named_sites))
        cclasses = sorted({symbol_class(c) for c in cnames if symbol_class(c)})
        kinds = sorted({("ctor" if c.startswith("ctor_") else "dtor") for c in cnames
                        if c.startswith(("ctor_", "dtor_"))})
        cand = dca(sup, cclasses)
        own = byclass.get(cand, 0)
        row = {"table_va": "0x%08x" % va, "population": shape.get("population", ""),
               "shape": shape.get("verdict", ""), "slots": shape.get("slots", ""), "class": cls,
               "installers": str(len(targets[va])), "callers": str(len(cnames)),
               "e8_sites": str(len(sites)), "gap_sites": str(len(gap_sites)),
               "gap_bodies": " ".join("0x%08x" % h for h in
                                      sorted({gap_owner(s, spans) for s in gap_sites} - {0})),
               "unreached": "%d/%d" % unreached_split(targets[va], unreached),
               "caller_classes": "|".join(cclasses[:6]), "caller_kinds": "+".join(kinds),
               "owner_candidate": cand, "owner_table": "0x%08x" % own if own else "",
               "owner_shared": "", "relation": "", "verdict": "", "evidence": ""}
        if not targets[va]:
            row["verdict"] = "no-installer"
            row["evidence"] = "no installer body resolves through the symbol map"
        elif not sites:
            row["verdict"] = "no-call"
            row["evidence"] = "no `E8` in .text targets its %d installer body(ies)" % len(targets[va])
        elif not cnames:
            row["verdict"] = "caller-gap"
            row["evidence"] = ("%d direct `E8` caller(s) at %s, every one inside a body the symbol map "
                               "does not cover%s"
                               % (len(gap_sites), ", ".join("0x%08x" % s for s in gap_sites[:3]),
                                  ("; %s is a body the gap census decoded" % row["gap_bodies"])
                                  if row["gap_bodies"] else ""))
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
        d["called"] += 1 if r["e8_sites"] not in ("0", "") else 0
        d["gap-only"] += 1 if r["verdict"] == "caller-gap" else 0
        d["with-class"] += 1 if r["owner_candidate"] else 0
    return by_pop


def render(rows):
    c = counts(rows)
    out = ["| population | tables | installer body called directly (`E8`) | of those, only from a body the map does not cover | a caller class names the owner | no `E8` caller | no installer body |",
           "|---|---|---|---|---|---|---|"]
    for pop in POPS:
        d = c[pop]
        if not d["tables"]:
            continue
        out.append("| %s | %d | %d | %d | %d | %d | %d |"
                   % (pop, d["tables"], d["called"], d["gap-only"], d["with-class"],
                      d["v:no-call"], d["v:no-installer"]))
    named = c["named"]
    out.append("")
    out.append("**The control: on the %d named tables the callers are the derived classes, and their deepest "
               "common ancestor is the table's class on %d, one of its descendants on %d and an unrelated "
               "class on %d.** %d named tables are called only from bodies with no `ctor_`/`dtor_` symbol, "
               "and %d have no `E8` caller at all."
               % (named["tables"], named["v:control-class"], named["v:control-descendant"],
                  named["v:control-other"], named["v:callers-unnamed"], named["v:no-call"]))
    plain, at = c["plain-only"], c["at-least"]
    out.append("")
    out.append("**Of the %d nameless tables, %d are built inside a class's own constructor -- %d by a group of "
               "sibling classes, so the table is the base their constructors and destructors reset to, and %d by a "
               "single class whose own table is a different address, a second table that class builds. %d are "
               "called only from bodies with no class symbol, %d are called from a body inside a map gap and %d "
               "have no `E8` caller at all.**"
               % (plain["tables"] + at["tables"],
                  plain["v:base-of-group"] + at["v:base-of-group"] + plain["v:second-table"] + at["v:second-table"],
                  plain["v:base-of-group"] + at["v:base-of-group"],
                  plain["v:second-table"] + at["v:second-table"],
                  plain["v:callers-unnamed"] + at["v:callers-unnamed"],
                  plain["v:caller-gap"] + at["v:caller-gap"],
                  plain["v:no-call"] + at["v:no-call"]))
    nocall = [r for r in rows if r["verdict"] == "no-call"]
    dead = [r for r in nocall if r["unreached"].split("/")[0] == r["unreached"].split("/")[1]]
    out.append("")
    out.append("**%d tables have no `E8` caller. On %d of them the installer body is also absent from the "
               "reachability fixpoint (`inventory/native_unreachable.csv`: no `E8`, `E9` or dword in any section "
               "names it), so the table and its constructor are an isolated pair; on the other %d a dword the "
               "`E8` scan cannot see still names the body.**"
               % (len(nocall), len(dead), len(nocall) - len(dead)))
    gaps = [r for r in rows if r["gap_sites"] not in ("0", "")]
    gonly = [r for r in rows if r["verdict"] == "caller-gap"]
    placed = [r for r in gonly if r["gap_bodies"]]
    heads = sorted({h for r in placed for h in r["gap_bodies"].split()})
    out.append("")
    out.append("**%d tables have a direct `E8` caller inside a body the symbol map does not cover, and for %d "
               "of them that is the only caller. The gap census names the enclosing body of %d of those %d "
               "(%d distinct heads: %s), and the other %d sites land in gaps the pad-and-prologue rule leaves "
               "open.**"
               % (len(gaps), len(gonly), len(placed), len(gonly), len(heads),
                  ", ".join("`%s`" % h for h in heads) or "-",
                  sum(len(r["gap_sites"].split()) for r in gonly if not r["gap_bodies"])))
    d55 = [r for r in rows if r["shape"] in ("dtor-only", "unclassified")]
    d55c = sum(1 for r in d55 if r["caller_classes"])
    d55n = collections.Counter(r["verdict"] for r in d55)
    out.append("")
    out.append("**Of the %d tables whose installer is destructor-shaped or neither, %d have callers that carry a "
               "`ctor_`/`dtor_` class symbol, and there the callers are the sibling destructors that reset the "
               "pointer to this table; %d are called only from bodies with no class symbol, %d from a body inside "
               "a map gap, %d have no `E8` caller and %d have no installer body.**"
               % (len(d55), d55c, d55n["callers-unnamed"], d55n["caller-gap"], d55n["no-call"],
                  d55n["no-installer"]))
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
        ("a site inside a mapped body is named", site_split([0x1010], [0x1000], {0x1000: ("a", 0x40)})[0] == [0x1010]),
        ("a decoded gap body owns its site", gap_owner(0x2953a6, [(0x2952d0, 0x2952d0 + 462)]) == 0x2952d0),
        ("a site past a gap body is owned by nothing", gap_owner(0x296000, [(0x2952d0, 0x2952ce)]) == 0),
        ("a body the fixpoint names counts", unreached_split([0x1000, 0x2000], {0x1000}) == (1, 2)),
        ("no body is zero of nothing", unreached_split([], {0x1000}) == (0, 0)),
        ("the gaps read as spans", gap_bodies([{"head_va": "0x1000", "size": "10", "verdict": "body"},
                                               {"head_va": "0x2000", "size": "0", "verdict": "no-close"}])
         == [(0x1000, 0x100a)]),
        ("a site past a body's size is a gap",
         site_split([0x1050], [0x1000], {0x1000: ("a", 0x40)})[1] == [0x1050]),
        ("a site in no body at all is a gap", site_split([0x9000], [0x1000], {0x1000: ("a", 0x40)})[1] == [0x9000]),
        ("the split keeps both sides apart",
         site_split([0x1010, 0x1050, 0x2008], [0x1000, 0x2000],
                    {0x1000: ("a", 0x40), 0x2000: ("b", 0x10)}) == ([0x1010, 0x2008], [0x1050])),
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
