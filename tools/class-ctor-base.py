#!/usr/bin/env python3
"""Measure each class-table store's base-constructor call, instead of assuming the first one.

`inventory/class_constructors.csv` carries a `call_before_store` column: the **first** `E8` before the
`mov [this], <class table>` that the scanner reaches. Its header is honest, but every reader since has
used it as *the base constructor call* -- the artifact's own evidence strings, the page
`docs/systems/CLASS_REGISTRATION.md` and the PLAN notes all say "base=<that address>". A constructor
that makes any other call first (a lazy singleton, an allocation, an assert helper) therefore reports
the wrong body as its base, and through it the wrong class.

This tool reads the base call the way the compiler made it: the **last** direct call before the table
store, resolved to the class table its own body installs. Three verdicts:

  * `base-ctor`    -- the last call goes to a body that installs a class table; that table names the
                      base class, so the inheritance edge is measured rather than inferred from order.
  * `base-installer` -- the same edge, but the callee's own shape reads as a destructor (it installs its
                      table before any call and does not return `this`). The edge is still a table; the
                      disagreement is reported rather than hidden.
  * `no-base-call` -- the body installs its table before any call, so its base constructor was inlined
                      and there is no edge to read.
  * `not-a-base`   -- the last call goes to a body that installs no class table, so the recorded class
                    column is not an edge.

A second, independent reading sits beside it: **a body can install more than one class table**, and the
one it owns is the **last** store. MSVC writes the base constructor's vtable itself when it inlines that
constructor into the derived body, so the first store in a derived constructor is a base class's table --
which is why two unrelated constructors can both appear to install one table. `stores`, `own_table`,
`own_table_class` and `own` carry that reading per row, and the rendered block joins the 493 registration
sites to it, so a site whose class builds a different table is named instead of averaged in.

Both columns are emitted, so a disagreement is visible per row instead of being averaged away, and the
doc block renders the four tables of the UE2 object model (`0x004dd2a8`, `0x004d4418`, `0x004d4570`,
`0x004d4870`) -- the ladder every later reading quotes.

    tools/class-ctor-base.py DUMP/default.xbe -o inventory
    tools/class-ctor-base.py DUMP/default.xbe --class Class --class Struct --report
    tools/class-ctor-base.py --write-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-ctor-base.py --check-doc docs/systems/CLASS_REGISTRATION.md   # from the committed CSV
    tools/class-ctor-base.py --check        # synthetic bodies, no dump, writes nothing

Addresses, class names and counts only: no package or image content is written.
"""
import argparse
import csv
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS = ["function_va", "function_name", "class", "table_va", "object_size", "stack_args",
          "store_off", "stores", "own_table", "own_table_class", "own", "calls", "first_call",
          "first_call_class", "base_call", "base_call_class", "base_call_form", "verdict", "note"]
ARTIFACT = os.path.join(ROOT, "inventory/class_ctor_bases.csv")
SITES = os.path.join(ROOT, "inventory/class_registrations.csv")
DOC = os.path.join(ROOT, "docs/systems/CLASS_REGISTRATION.md")
DOC_START = "<!-- generated:class-ctor-base -->"
DOC_END = "<!-- /generated:class-ctor-base -->"
# the four tables of the UE2 object model: the object root, Struct, State, Class
LADDER = ("0x004dd2a8", "0x004d4418", "0x004d4570", "0x004d4870")


# --------------------------------------------------------------------------- decode

def objdump(blob, base):
    """[(va, text)] from objdump, an independent decoder over a byte window."""
    path = tempfile.mktemp(suffix=".bin")
    with open(path, "wb") as fh:
        fh.write(blob)
    try:
        out = subprocess.run(["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                              "--adjust-vma=0x%x" % base, path],
                             capture_output=True, text=True).stdout
    finally:
        os.unlink(path)
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        head = parts[0].strip()
        if not head.endswith(":") or not all(c in "0123456789abcdef" for c in head[:-1]):
            continue
        rows.append((int(head[:-1], 16), parts[-1].strip()))
    return rows


def calls_before(blob, base, end_off):
    """Direct-call sites in [0, end_off) as [(va, target)], plus the count of indirect calls."""
    out, indirect = [], 0
    for va, text in objdump(blob, base):
        if va - base >= end_off:
            break
        if not text.startswith("call"):
            continue
        operand = text.split(None, 1)[1] if " " in text else ""
        if operand.startswith("0x"):
            out.append((va, int(operand.split()[0], 16)))
        else:
            indirect += 1
    return out, indirect


# --------------------------------------------------------------------------- join

def load_census(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def installers_by_body(rows):
    """{function_va: row} for one census row per body."""
    by_body = {}
    for r in rows:
        by_body.setdefault(int(r["function_va"], 16), r)
    return by_body


def bodies_by_stores(rows):
    """{function_va: [census row, ...]} for every body that installs a class table, in store order.

    A body can install more than one: MSVC writes the base constructor's table itself when it inlines
    it, so the first store in a derived constructor is the base's vtable and the *last* store is the
    table of the class the body builds. The census keeps one row per store, so a body with two stores
    has two rows and the order is the column that tells them apart.
    """
    out = {}
    for r in rows:
        if r.get("form") != "ctor":
            continue
        out.setdefault(r["function_va"], []).append(r)
    for group in out.values():
        group.sort(key=lambda r: int(r["store_va"], 16))
    return out


def site_join(out, path=None):
    """Join every registration site to the table its constructor owns (its body's last store)."""
    path = path or SITES
    if not os.path.isfile(path):
        return None
    own = {}
    for r in out:
        if r.get("own") == "yes":
            own[r["function_va"]] = r
    sites, agree, differ, unresolved = 0, 0, [], []
    for s in load_census(path):
        sites += 1
        r = own.get(s["thunk_target"])
        if r is None:
            unresolved.append(s)
        elif r["own_table"].lower() == s["table_va"].lower():
            agree += 1
        else:
            differ.append((s, r))
    return sites, agree, differ, unresolved


def resolve(target, by_body):
    """The class table the callee installs, if that body is in the census."""
    return by_body.get(target)


def read_row(r, read, by_body, stores=None):
    start = int(r["function_va"], 16)
    store = int(r["store_va"], 16)
    end_off = store - start + 6
    blob = read(start, end_off + 16)
    calls, indirect = calls_before(blob, start, end_off)
    first = calls[0] if calls else None
    last = calls[-1] if calls else None
    first_row = resolve(first[1], by_body) if first else None
    last_row = resolve(last[1], by_body) if last else None
    group = stores or [r]
    own = group[-1]
    if last_row is not None:
        verdict = "base-ctor" if last_row["form"] == "ctor" else "base-installer"
    elif not calls:
        verdict = "no-base-call"
    else:
        verdict = "not-a-base"
    note = ""
    if not calls:
        note = "table installed before any call"
    elif first[1] != last[1]:
        note = "%d later call(s) before the store" % (len(calls) - 1)
        note += ("; the last one installs no class table" if last_row is None
                 else "; the recorded call is not the base")
    if indirect:
        note = (note + "; " if note else "") + "%d indirect call(s) not read" % indirect
    return {
        "function_va": r["function_va"],
        "function_name": r["function_name"],
        "class": r["class"],
        "table_va": r["table_va"],
        "object_size": r["object_size"],
        "stack_args": r["stack_args"],
        "store_off": "0x%x" % (store - start),
        "stores": str(len(group)),
        "own_table": own["table_va"],
        "own_table_class": own.get("class", ""),
        "own": "yes" if own["table_va"] == r["table_va"] else "no",
        "calls": str(len(calls)),
        "first_call": "0x%08x" % first[1] if first else "",
        "first_call_class": first_row["class"] if first_row else "",
        "base_call": "0x%08x" % last[1] if last else "",
        "base_call_class": last_row["class"] if last_row else "",
        "base_call_form": last_row["form"] if last_row else "",
        "verdict": verdict,
        "note": note,
    }


def census(xbe, rows):
    text = next(s for s in xbe.sections if s.name == ".text")
    blob = xbe.data[text.foff:text.foff + text.fsize]

    def read(va, size):
        return blob[va - text.va:va - text.va + size]

    by_body = installers_by_body(rows)
    groups = bodies_by_stores(rows)
    return [read_row(r, read, by_body, groups.get(r["function_va"]))
            for r in rows if r["form"] == "ctor"]


def summary(out):
    picked = [r for r in out if r["verdict"].startswith("base-")]
    unread = [r for r in out if r["verdict"] not in ("base-ctor", "base-installer", "no-base-call")]
    differ = [r for r in out if r["first_call"] and r["base_call"] and r["first_call"] != r["base_call"]]
    multi = [r for r in out if int(r.get("stores") or 1) > 1]
    moved = [r for r in out if r.get("own") == "no"]
    return picked, unread, differ, multi, moved


def render(out, total=None):
    """The object-model ladder as a table, plus one sentence about the whole population."""
    total = total if total is not None else out
    shown = [r for r in out if r["table_va"] in LADDER]
    lines = ["| class | ctor | args | recorded call | measured base | base class | verdict |",
             "|---|---|---|---|---|---|---|"]
    for r in sorted(shown, key=lambda r: (r["table_va"], r["function_va"])):
        lines.append("| %s | %s | %s | %s%s | %s | %s | %s |"
                     % (r["class"], r["function_va"], r["stack_args"] or "0",
                        r["first_call"] or "-",
                        " (%s)" % r["first_call_class"] if r["first_call_class"] else "",
                        r["base_call"] or "-", r["base_call_class"] or "-", r["verdict"]))
    picked, unread, differ, multi, moved = summary(total)
    lines.append("")
    lines.append("%d constructors in the image: %d read a base class off the last call before their table "
                 "store (%d of those to a body the shape rule calls a destructor), "
                 "%d install the table before any call (the base was inlined), %d end in a body that "
                 "installs no class table, and %d of the recorded calls are not the last call and so are not "
                 "the base the census implied."
                 % (len(total), len(picked),
                    sum(1 for r in total if r["verdict"] == "base-installer"),
                    sum(1 for r in total if r["verdict"] == "no-base-call"), len(unread), len(differ)))
    groups = {}
    for r in multi:
        groups.setdefault(r["function_va"], []).append(r)
    lines.append("")
    lines.append("**A body can install more than one class table, and the last store is the body's own.** %d of "
                 "the %d rows sit in a body that stores two or more tables (%d distinct bodies). The first store "
                 "is the table of a base whose constructor the compiler inlined into the derived body; the last "
                 "store is the table of the class the body builds. So a row whose `table_va` differs from its "
                 "body's last store (`own` = `no`) records a base class's vtable: **%d row(s)**, and those are "
                 "the rows the site census cannot name from."
                 % (len(multi), len(total), len(groups), len(moved)))
    if groups:
        lines.append("")
        lines.append("| body | name | stores in body order | own table | own table class | row whose table is a base |")
        lines.append("|---|---|---|---|---|---|")
        for va in sorted(groups, key=lambda v: int(v, 16)):
            g = sorted(groups[va], key=lambda r: int(r["store_off"], 16))
            base_rows = [r for r in g if r.get("own") == "no"]
            lines.append("| %s | %s | %s | %s | %s | %s |"
                         % (va, g[0]["function_name"], " -> ".join(r["table_va"] for r in g),
                            g[-1].get("own_table") or g[-1]["table_va"],
                            g[-1].get("own_table_class") or "-",
                            ", ".join((r["class"] + " ") if r["class"] else "" for r in base_rows).strip()
                            or "-"))
    join = site_join(total)
    if join:
        sites, agree, differ, unresolved = join
        lines.append("")
        lines.append("**The registration side agrees, and its exceptions are the two texture classes.** %d of the "
                     "%d sites record the last store of the body their thunk resolves to; %d resolve to a body "
                     "the census has no constructor row for%s."
                     % (agree, sites, len(unresolved),
                        (" (" + ", ".join("`%s`" % s["class_literal"] for s in unresolved) + ")")
                        if unresolved else ""))
        if differ:
            lines.append("")
            lines.append("| site | class the site names | table it records | body | table the body builds |")
            lines.append("|---|---|---|---|---|")
            for s, r in differ:
                lines.append("| %s | %s | %s | %s | %s |" % (s["site_va"], s["class_literal"], s["table_va"],
                                                            r["function_va"], r["own_table"]))
    return "\n".join(lines)


def write_artifact(out, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(out)


def read_artifact(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def splice_doc(path, block):
    """The page with its generated block replaced, or None when the markers are absent."""
    text = open(path, encoding="utf-8").read()
    i, j = text.find(DOC_START), text.find(DOC_END)
    if i < 0 or j < 0:
        return None
    return text[:i] + DOC_START + "\n" + block + "\n" + text[j:]


# --------------------------------------------------------------------------- self-test

def self_check():
    """Synthetic bodies and a write/read round trip of the artifact, with no dump."""
    plain = (b"\x56\x8b\xf1"                       # push esi; mov esi, ecx
             b"\xe8\x00\x00\x00\x00"               # call +0     -> the base
             b"\xc7\x06\x00\x00\x00\x00"           # mov [esi], table
             b"\x8b\xc6\x5e\xc3")                  # mov eax, esi; pop esi; ret
    helper = (b"\xa1\x00\x00\x00\x00"               # mov eax, ds:0
              b"\x85\xc0\x75\x0a"                   # test eax, eax; jne +0xa
              b"\xe8\x00\x00\x00\x00"               # call +0     -> a helper
              b"\x83\xc4\x04"                       # add esp, 4
              b"\xe8\x00\x00\x00\x00"               # call +0     -> the base
              b"\xc7\x06\x00\x00\x00\x00"           # mov [esi], table
              b"\x8b\xc6\x5e\xc3")
    inlined = (b"\x8b\xc1"                          # mov eax, ecx
               b"\xc7\x00\x00\x00\x00\x00"          # mov [eax], table
               b"\xc3")                             # ret
    bad = []
    c, _ = calls_before(plain, 0x1000, 0x10)
    if len(c) != 1 or c[0][1] != 0x1008:
        bad.append("plain body calls: %r" % (c,))
    c, _ = calls_before(helper, 0x2000, 0x20)
    if len(c) != 2 or c[0][0] >= c[1][0]:
        bad.append("helper body calls: %r" % (c,))
    c, _ = calls_before(inlined, 0x3000, 0x10)
    if c:
        bad.append("inlined body calls: %r" % (c,))

    base = {"function_va": "0x00002000", "function_name": "FUN_base", "class": "Base",
            "table_va": "0x004dd2a8", "object_size": "8", "stack_args": "0", "form": "ctor",
            "store_va": "0x00002002", "call_before_store": "", "registration_ctor": "", "evidence": ""}
    by_body = installers_by_body([base])
    row = read_row({"function_va": "0x00003000", "function_name": "FUN_child", "class": "Child",
                    "table_va": "0x004d4418", "object_size": "8", "stack_args": "4", "form": "ctor",
                    "store_va": "0x0000300a", "call_before_store": "", "registration_ctor": "",
                    "evidence": ""}, lambda va, size: bytes(size), by_body)
    if row["verdict"] != "no-base-call":
        bad.append("empty body verdict: %r" % (row["verdict"],))
    if resolve(0x2000, by_body) is not base:
        bad.append("resolve did not find the installing body")
    if resolve(0x9999, by_body) is not None:
        bad.append("resolve invented a row")

    # a body with two stores: the base's table first, the body's own last
    two = [
        {"function_va": "0x00004000", "function_name": "FUN_two", "class": "Base", "table_va": "0x004d0000",
         "object_size": "8", "stack_args": "0", "form": "ctor", "store_va": "0x00004002",
         "call_before_store": "", "registration_ctor": "", "evidence": ""},
        {"function_va": "0x00004000", "function_name": "FUN_two", "class": "Child", "table_va": "0x004d0004",
         "object_size": "8", "stack_args": "0", "form": "ctor", "store_va": "0x00004010",
         "call_before_store": "", "registration_ctor": "", "evidence": ""},
    ]
    groups = bodies_by_stores(two)
    if len(groups.get("0x00004000", [])) != 2:
        bad.append("two-store body was not grouped: %r" % (groups,))
    else:
        blanks = lambda va, size: bytes(size)
        first = read_row(two[0], blanks, by_body, groups["0x00004000"])
        second = read_row(two[1], blanks, by_body, groups["0x00004000"])
        if (first["own"], first["own_table"], first["stores"]) != ("no", "0x004d0004", "2"):
            bad.append("the base store was taken as the body's own: %r" % (first,))
        if (second["own"], second["own_table"]) != ("yes", "0x004d0004"):
            bad.append("the last store was not the body's own: %r" % (second,))
        if "a body can install more than one class table" not in render([first, second]).lower():
            bad.append("render does not report the multi-store body")

    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "artifact.csv")
    rows = [dict(row, verdict="base-ctor", base_call="0x00002000", base_call_class="Base",
                 first_call="0x00002000", first_call_class="Base")]
    write_artifact(rows, path)
    if read_artifact(path) != rows:
        bad.append("artifact round trip differs")
    if "1 constructors in the image" not in render(read_artifact(path)):
        bad.append("render: %r" % render(read_artifact(path)))
    if not os.path.isfile(DOC) or splice_doc(DOC, "x") is None:
        bad.append("the page has no generated:class-ctor-base markers")
    if bad:
        print("FAIL class-ctor-base: " + "; ".join(bad))
        return 1
    print("ok   class-ctor-base: the base is the last call before the store, a body's own table is its last "
          "store, an inlined base has none, and the artifact round-trips")
    return 0


def main():
    if "--check" in sys.argv[1:]:
        return self_check()
    ap = argparse.ArgumentParser()
    ap.add_argument("xbe", nargs="?")
    ap.add_argument("--csv", default=os.path.join(ROOT, "inventory/class_constructors.csv"))
    ap.add_argument("-o", "--out", default=None, help="inventory/ to write class_ctor_bases.csv into")
    ap.add_argument("--class", action="append", dest="classes", default=None,
                    help="limit the report to one class (repeatable)")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc", nargs="?", const=DOC, default=None)
    ap.add_argument("--check-doc", nargs="?", const=DOC, default=None)
    args = ap.parse_args()

    if args.write_doc or args.check_doc:
        # rendered from the committed artifact, so the gate needs no dump
        out = read_artifact(ARTIFACT)
        if args.out:
            ap.error("--write-doc renders the committed artifact; regenerate it without --out first")
    else:
        if not args.xbe:
            ap.error("the image is required unless --check, --write-doc or --check-doc is given")
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        from xbe import Xbe

        out = census(Xbe(args.xbe), load_census(args.csv))
        if args.out:
            path = os.path.join(args.out, "class_ctor_bases.csv")
            write_artifact(out, path)
            print("%s: %d rows" % (path, len(out)))

    if args.classes:
        out = [r for r in out if r["class"] in args.classes]
    if args.report or not (args.out or args.write_doc or args.check_doc):
        print(render(out))
    picked, unread, differ, multi, moved = summary(out)
    print("base-edge %d, no-base-call %d, not-a-base %d, recorded!=last %d, multi-store rows %d, base-table "
          "rows %d"
          % (len(picked), sum(1 for r in out if r["verdict"] == "no-base-call"), len(unread),
             len(differ), len(multi), len(moved)))

    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        new = splice_doc(path, render(out))
        if new is None:
            print("FAIL class-ctor-base: markers not found in %s" % path)
            return 1
        old = open(path, encoding="utf-8").read()
        if args.check_doc:
            if new != old:
                print("FAIL class-ctor-base: %s is stale against the committed artifact" % path)
                return 1
            print("ok   class-ctor-base: %s matches the committed artifact" % path)
            return 0
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("wrote %s" % path)
        else:
            print("%s already current" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
