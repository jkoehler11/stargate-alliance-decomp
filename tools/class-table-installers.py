#!/usr/bin/env python3
"""Who installs the class tables nothing has named: the installer's own call sites.

`docs/UNKNOWNS.md` U40 counts 277 gap bodies that a dword in a class-table slot names, and 269 of them
sit in the **258 tables no registration and no class map names** (`inventory/native_vtable_reach.csv`,
`class` empty: the 232 `inventory/native_vtable_shared.csv` tables and the 26 mapped ones the class map
leaves proposed). The entry's next instrument is "the installer's own call sites are the route
`class-table-parents` uses", so this tool walks that route for the full population and reports exactly
what it earns.

For each nameless table it reads every `mov [reg], <imm32>` store site `native_vtable_reach.csv`
already censused, resolves the body that holds it in `symbols/xbox_functions.csv`, and classifies that
body:

  * `ctor` / `dtor` -- the symbol names it (`ctor_FireTexture`), and `inventory/class_constructors.csv`
    confirms it is that class's constructor or destructor;
  * `installer` -- a body `class_constructors.csv` names as a class's constructor but whose symbol is
    still `FUN_*` (the artifact resolved the class, the symbol was never renamed);
  * `plain` -- a body neither table knows.

**What the answer is.** A constructor installs its *parent's* table before its own (the class map's
`base-store-ancestor` rule), so a nameless table a single named constructor stores is one of that
class's **ancestors'** tables -- the table is at least as deep as the class, never the class itself.
The tool therefore reports a *bound* (`at-least-<base of class>`) and a `family` (the mapped table the
shared census already paired it with by prefix), and writes no class name: `class-table-parents.py`
already proposes the six names the tree can carry, and inventing the rest is the guess U8 warns about.

    tools/class-table-installers.py                # summary + the tables a class bounds
    tools/class-table-installers.py -o inventory    # write inventory/class_table_installers.csv
    tools/class-table-installers.py --write-doc     # render the page block (no dump)
    tools/class-table-installers.py --check-doc     # refuse a stale block
    tools/class-table-installers.py --check         # synthetic, no dump, writes nothing

CSV in, CSV out: the population is committed, so this needs no dump and no boot.

**The symbol map's `size` column is a byte count in decimal, not hex.** Read as hex, a 66-byte body
becomes 0x66 = 102 and a 407-byte body 0x407 = 1,031, so the previous body's span runs past its own end
and a store site in the next body is credited to it: the census counted installers that are the body
next door. The selftest's fourth table pins it -- a site past a short body's decimal end is nobody's.
"""
import argparse
import bisect
import collections
import csv
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join("inventory", "class_table_installers.csv")
REACH = os.path.join("inventory", "native_vtable_reach.csv")
SHARED = os.path.join("inventory", "native_vtable_shared.csv")
NAMES = os.path.join("inventory", "xbe_class_table_names.csv")
CTORS = os.path.join("inventory", "class_constructors.csv")
MAP = os.path.join("symbols", "xbox_functions.csv")
PAGE = os.path.join("docs", "systems", "CLASS_REGISTRATION.md")
BLOCK_MARK = ("<!-- generated:class-table-installers -->", "<!-- /generated:class-table-installers -->")
COLUMNS = ("table_va", "slots", "installers", "named_bodies", "bound", "family", "verdict", "evidence")


def read(path):
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def body_index(path):
    out = []
    for r in read(path):
        try:
            a = int(r["address"], 16)
            s = int(r["size"] or "0")                # the map's size column is a byte count in DECIMAL
        except (KeyError, ValueError):
            continue
        out.append((a, a + s if s else a, r.get("name", "")))
    out.sort()
    return out


def owner(index, starts, va):
    i = bisect.bisect_right(starts, va) - 1
    if i < 0:
        return None
    a, end, _ = index[i]
    if end and va >= end:
        return None
    return a


def build(reach_path=REACH, shared_path=SHARED, names_path=NAMES, ctors_path=CTORS, map_path=MAP):
    """[(row dict)] -- one row per class table no name reaches."""
    named_tables = {int(r["table_va"], 16) for r in read(names_path) if r.get("class")}
    family = {int(r["table_va"], 16): (r.get("best_class") or "", r.get("family") or "")
              for r in read(shared_path) if r.get("table_va")}
    ctor_class = {}
    for r in read(ctors_path):
        try:
            a = int(r["function_va"], 16)
        except (KeyError, ValueError):
            continue
        if r.get("class") and r.get("form") in ("ctor", "dtor"):
            ctor_class.setdefault(a, (r["class"], r["form"]))
    index = body_index(map_path)
    starts = [a for a, _, _ in index]
    name_of = {a: n for a, _, n in index}

    rows = []
    for t in read(reach_path):
        try:
            tva = int(t["table_va"], 16)
        except (KeyError, ValueError):
            continue
        if not t.get("table_va") or t.get("class") or tva in named_tables:
            continue
        bodies = sorted({b for b in (owner(index, starts, int(s, 16)) for s in (t.get("install_sites") or "").split()
                                     if s) if b is not None})
        kinds, named, bound = collections.Counter(), [], ""
        for b in bodies:
            nm = name_of.get(b, "FUN_%08x" % b)
            if b in ctor_class:
                kinds["ctor" if nm.startswith(("ctor_", "dtor_")) else "installer"] += 1
                named.append(nm)
                if not bound:
                    bound = ctor_class[b][0]          # the class whose ctor stores it = its descendant
            else:
                kinds["plain"] += 1
        if not bodies:
            verdict = "no-store"
        elif bound:
            verdict = "at-least"                      # a ctor stored it: the table is an ancestor's
        else:
            verdict = "plain-only"
        fam, fam_kind = family.get(tva, ("", ""))
        rows.append({
            "table_va": t["table_va"],
            "slots": t.get("table_slots", ""),
            "installers": len(bodies),
            "named_bodies": "|".join(sorted(set(named))),
            "bound": ("at-least-" + bound) if bound else "",
            "family": fam,
            "verdict": verdict,
            "evidence": "%s; stores by %d bod%s (%s); shared family %s%s"
                        % (", ".join("%s %d" % (k, v) for k, v in sorted(kinds.items())) or "no store",
                           len(bodies), "" if len(bodies) == 1 else "ies",
                           ", ".join(sorted(name_of.get(b, "FUN_%08x" % b) for b in bodies)[:4]) or "-",
                           fam or "-", (" (%s)" % fam_kind) if fam_kind else ""),
        })
    return rows


def counts(rows):
    c = collections.Counter(r["verdict"] for r in rows)
    bounds = collections.Counter(r["bound"] for r in rows if r["bound"])
    return c, bounds


def summary(rows):
    c, bounds = counts(rows)
    print("%d class tables no name reaches: %s" % (len(rows), ", ".join("%s %d" % (k, c[k]) for k in sorted(c))))
    print("a constructor stores %d of them, which bounds each to an ancestor of one class:"
          % sum(bounds.values()))
    for k, v in bounds.most_common(12):
        print("  %-28s %d table(s)" % (k, v))
    print("the other %d are stored by bodies no class names (`plain-only`), and %d by nothing"
          % (c["plain-only"], c["no-store"]))


def render(rows):
    c, bounds = counts(rows)
    fam = collections.Counter(r["family"] for r in rows if r["family"])
    out = ["| the tables no name reaches | tables |", "| --- | --- |",
           "| stored by a class constructor (so an ancestor of that class) | %d |" % c["at-least"],
           "| stored only by bodies no class names | %d |" % c["plain-only"],
           "| stored by nothing | %d |" % c["no-store"],
           "| **total** | **%d** |" % len(rows),
           "",
           "The constructor-stored tables carry a bound, not a name: %d distinct classes, and a table a "
           "constructor stores is its base's, never its own. %d of the population share a prefix with a "
           "mapped table (`%s`), which is the family the shared census already recorded."
           % (len(bounds), sum(fam.values()), "`, `".join(k for k, _ in fam.most_common(4))),
           "",
           "| class whose constructor stores it | tables it bounds |", "| --- | --- |"]
    for k, v in bounds.most_common():
        out.append("| `%s` | %d |" % (k.replace("at-least-", ""), v))
    return out


def write_doc(path, rows, check=False):
    lines = render(rows)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    a, b = BLOCK_MARK
    if a not in text or b not in text:
        raise SystemExit("%s: no generated block" % path)
    head, rest = text.split(a, 1)
    _, tail = rest.split(b, 1)
    new = head + a + "\n" + "\n".join(lines) + "\n" + b + tail
    if check:
        if new != text:
            raise SystemExit("%s: the class-table-installers block is stale" % path)
        print("ok   class-table-installers: %s reproduces" % path)
        return
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new)
    print("wrote %s" % path)


def write_csv(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in COLUMNS})
    os.replace(tmp, path)
    print("wrote %s (%d rows)" % (path, len(rows)))


def selftest():
    d = tempfile.mkdtemp(prefix="ctinst-")
    def wr(name, header, rows):
        with open(os.path.join(d, name), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)
    wr("reach.csv", ["table_va", "table_slots", "class", "install_sites"], [
        ["0x5000", "3", "", "0x1010 0x2010"],     # stored by ctor_Foo and a plain body
        ["0x6000", "2", "", "0x3010"],            # stored only by a plain body
        ["0x7000", "2", "Known", "0x1010"],       # already named: not this population
        ["0x8000", "1", "", ""],                  # no store at all
        ["0x9000", "1", "", "0x400e"],            # past the decimal end of the body at 0x4000
    ])
    wr("shared.csv", ["table_va", "best_class", "family"], [["0x5000", "Object", "prefix"]])
    wr("names.csv", ["table_va", "class"], [["0x7000", "Known"]])
    wr("ctors.csv", ["function_va", "function_name", "class", "form", "table_va"], [
        ["0x1000", "ctor_Foo", "Foo", "ctor", "0x9fff"],])
    wr("map.csv", ["address", "name", "size"], [
        ["0x1000", "ctor_Foo", "256"], ["0x2000", "FUN_00002000", "256"],
        ["0x3000", "FUN_00003000", "256"], ["0x9000", "FUN_00009000", "4096"],
        ["0x4000", "FUN_00004000", "10"], ["0x4010", "FUN_00004010", "100"]])
    rows = build(os.path.join(d, "reach.csv"), os.path.join(d, "shared.csv"),
                 os.path.join(d, "names.csv"), os.path.join(d, "ctors.csv"), os.path.join(d, "map.csv"))
    by = {r["table_va"]: r for r in rows}
    cases = [
        ("a named table is not in the population", "0x7000" not in by and len(rows) == 4),
        ("a constructor's store bounds the table to an ancestor, and does not name it",
         by["0x5000"]["verdict"] == "at-least" and by["0x5000"]["bound"] == "at-least-Foo"
         and by["0x5000"]["family"] == "Object"),
        ("a body no class names leaves the table unbounded", by["0x6000"]["verdict"] == "plain-only"
         and by["0x6000"]["bound"] == ""),
        ("a table with no store is its own verdict", by["0x8000"]["verdict"] == "no-store"),
        ("a site past a short body's decimal end belongs to no body",
         by["0x9000"]["verdict"] == "no-store" and int(by["0x9000"]["installers"]) == 0),
    ]
    bad = [n for n, ok in cases if not ok]
    for n, ok in cases:
        print("%s %s" % ("ok  " if ok else "FAIL", n))
    if bad:
        raise SystemExit("class-table-installers: %d selftest case(s) failed" % len(bad))
    print("class-table-installers: selftest passed (%d cases)" % len(cases))


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("-o", "--out-dir", default=None)
    ap.add_argument("--write-doc", action="store_true")
    ap.add_argument("--check-doc", nargs="?", const="", default=None)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        selftest()
        return 0
    rows = build()
    if args.write_doc or args.check_doc is not None:
        target = args.check_doc or os.path.join(ROOT, PAGE)
        if not os.path.isabs(target):
            target = os.path.join(ROOT, target)
        write_doc(target, rows, check=args.check_doc is not None)
        return 0
    summary(rows)
    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        write_csv(os.path.join(args.out_dir, os.path.basename(OUT)), rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
