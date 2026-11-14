#!/usr/bin/env python3
"""The 24 class tables no registration site reaches: which bodies install them, and what calls those.

The site census (`inventory/class_registrations.csv`) keys each of the 493 registration sites to the
last class-table store of the body its thunk resolves to, and the naming census
(`inventory/xbe_class_table_names.csv`) names the 468 tables such a site reaches. **24 of the 492
measured tables have no site at all**, so nothing in the registration path names them and nothing
measures their object size.

This tool reads them from the other end, the sweep `tools/class-ctors.py` runs: every
`mov dword ptr [reg(+disp)], <imm32>` in `.text` whose immediate is one of the 24 table addresses. For
each store it reports the symbol-map body it sits in, whether that body is a registration thunk target,
and every inbound edge to it (an `E8 rel32` call or an `E9 rel32` tail jump), grouped by who calls:

  * `ctor` / `dtor` -- the caller body is a class constructor or destructor, straight out of
    `inventory/class_constructors.csv`, so a class's ctor calls this one (the base-constructor shape);
  * `wrapper` -- the caller installs no table of its own and its first `call` targets this body: the
    forwarding pair the image carries for every destructor (`call <body>` then `call [reg+8]` + `ret 4`,
    or `call <body>` + `ret`), which is not a *caller* in the sense of deciding reachability but is not
    "ordinary code" either;
  * `code` -- neither of those.

What that answers, and what it does not: it says *who installs* these 24 tables and whether the
installing body is reachable. It does **not** name them -- a base class's constructor is called from
every derived class's constructor and installed nowhere, which is exactly the shape reported here, so
the caller list is the evidence a naming pass starts from, not a name.

    tools/class-orphan-tables.py --xbe DUMP/default.xbe -o inventory
    tools/class-orphan-tables.py --report
    tools/class-orphan-tables.py --write-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-orphan-tables.py --check-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-orphan-tables.py --check        # synthetic image, no dump, writes nothing

The dump is never committed; the artifact and the doc are. Addresses and counts only.
"""
import argparse
import bisect
import csv
import os
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS = ["table_va", "slots", "proposed", "store_va", "store_kind", "body_va", "body_name",
          "body_form", "registration_thunk", "callers", "caller_kinds", "stub_unmapped",
          "caller_classes", "verdict"]
ARTIFACT = os.path.join(ROOT, "inventory/class_orphan_tables.csv")
DOC = os.path.join(ROOT, "docs/systems/CLASS_REGISTRATION.md")
DOC_START = "<!-- generated:class-orphan-tables -->"
DOC_END = "<!-- /generated:class-orphan-tables -->"

KIND_ORDER = ("ctor", "dtor", "installer", "wrapper", "code", "unmapped", "stub")


# --------------------------------------------------------------------------- image
def stores(text, text_va, tables):
    """Every `mov dword ptr [reg(+disp)], imm32` whose immediate is one of `tables`.

    `C7 /0` only, no SIB (`rm == 4`), which is the form `tools/class-ctors.py` accepts. The three
    displacement widths are decoded so a `c7 40 08 imm32` store is not read as an opcode stream, and an
    absolute `[disp32]` store is kept (`kind == "abs"`) instead of being dropped: it says where a table
    is installed even when no object owns the slot.
    """
    out = []
    i, n = 0, len(text)
    while i < n - 9:
        if text[i] != 0xC7:
            i += 1
            continue
        modrm = text[i + 1]
        mod, reg, rm = modrm >> 6, (modrm >> 3) & 7, modrm & 7
        if reg != 0 or rm == 4:
            i += 1
            continue
        if mod == 0 and rm == 5:
            imm, ln, kind = struct.unpack_from("<I", text, i + 2)[0], 6, "abs"
        elif mod == 0:
            imm, ln, kind = struct.unpack_from("<I", text, i + 2)[0], 6, "reg"
        elif mod == 1:
            imm, ln = struct.unpack_from("<I", text, i + 3)[0], 7
            kind = "frame" if rm == 5 else "reg"
        elif mod == 2:
            imm, ln = struct.unpack_from("<I", text, i + 5)[0], 9
            kind = "frame" if rm == 5 else "reg"
        else:
            i += 1
            continue
        if imm in tables:
            out.append((text_va + i, imm, kind))
        i += ln
    return out


def edges(text, text_va):
    """target -> [(site, kind)] for every relative `E8` call and `E9` jump in a code blob."""
    out = {}
    for op, kind in ((b"\xe8", "call"), (b"\xe9", "jmp")):
        pos = text.find(op)
        while pos != -1:
            if pos + 5 <= len(text):
                rel = struct.unpack_from("<i", text, pos + 1)[0]
                target = (text_va + pos + 5 + rel) & 0xFFFFFFFF
                out.setdefault(target, []).append((text_va + pos, kind))
            pos = text.find(op, pos + 1)
    return out


def containing(funcs, starts, va):
    """The map entry that contains `va`: the greatest start at or below it whose size covers it."""
    i = bisect.bisect_right(starts, va) - 1
    if i >= 0:
        addr, size, name = funcs[i]
        if addr <= va < addr + max(size, 1):
            return (addr, size, name)
    return None


def first_call(body, body_va):
    """The target of a body's first `E8`/`E9`, or None -- what makes a forwarder a wrapper."""
    for op in (b"\xe8", b"\xe9"):
        pos = body.find(op)
        if pos != -1 and pos + 5 <= len(body):
            rel = struct.unpack_from("<i", body, pos + 1)[0]
            return (body_va + pos + 5 + rel) & 0xFFFFFFFF
    return None


def caller_bodies(inbound, funcs):
    """Every body that holds an inbound edge site, so `first_call` can be read for each of them."""
    starts = [f[0] for f in funcs]
    out = set()
    for sites in inbound.values():
        for site, _ in sites:
            fn = containing(funcs, starts, site)
            if fn:
                out.add(fn[0])
    return out


# --------------------------------------------------------------------------- the measurement
def harvest(tables, slots, ctors, names, funcs, inbound, store_hits, first_calls, thunks,
            proposed=None):
    """One row per (table, store site), with the caller roles of the body the store sits in.

    `proposed` is `inventory/class_table_parents.csv`'s answer for the same table, copied so a reader has
    the caller rule's name beside the evidence; it is a join, not a second measurement.
    """
    """One row per (table, store site), with the caller roles of the body the store sits in."""
    starts = [f[0] for f in funcs]
    class_of_table = {int(r["table_va"], 16): r.get("class") or "" for r in names}
    body_form, body_table = {}, {}
    for r in ctors:
        va = int(r["function_va"], 16)
        body_form.setdefault(va, r.get("form") or "")
        body_table.setdefault(va, int(r["table_va"], 16) if r.get("table_va") else None)

    rows = []
    for site, table, kind in sorted(store_hits, key=lambda h: (h[1], h[0])):
        fn = containing(funcs, starts, site)
        row = {"table_va": "0x%08x" % table, "slots": slots.get(table, ""),
               "proposed": (proposed or {}).get(table, ""),
               "store_va": "0x%08x" % site, "store_kind": kind,
               "body_va": "", "body_name": "", "body_form": "", "registration_thunk": "no",
               "callers": "0", "caller_kinds": "", "stub_unmapped": "0", "caller_classes": ""}
        if fn is None:
            row["verdict"] = "no-body"
            rows.append(row)
            continue
        body = fn[0]
        row["body_va"] = "0x%08x" % body
        row["body_name"] = fn[2]
        row["body_form"] = body_form.get(body, "")
        row["registration_thunk"] = "yes" if body in thunks else "no"
        hits = inbound.get(body, [])
        kinds, classes, unmapped_stubs = {}, [], 0
        for call_site, edge in hits:
            caller = containing(funcs, starts, call_site)
            if caller is None and edge == "jmp":
                unmapped_stubs += 1
            if edge == "jmp":
                kinds["stub"] = kinds.get("stub", 0) + 1
                continue
            if caller is None:
                kinds["unmapped"] = kinds.get("unmapped", 0) + 1
                continue
            if caller[0] in body_form and body_form[caller[0]]:
                k = body_form[caller[0]]
                kinds[k] = kinds.get(k, 0) + 1
                cname = class_of_table.get(body_table.get(caller[0]) or 0, "")
                classes.append(cname or "table:%s" % (
                    "0x%08x" % body_table[caller[0]] if body_table.get(caller[0]) else "?"))
            elif first_calls.get(caller[0]) == body:
                kinds["wrapper"] = kinds.get("wrapper", 0) + 1
            elif caller[0] in body_form:
                kinds["installer"] = kinds.get("installer", 0) + 1
            else:
                kinds["code"] = kinds.get("code", 0) + 1
        row["callers"] = str(len(hits))
        row["caller_kinds"] = ",".join("%s=%d" % (k, kinds[k]) for k in KIND_ORDER if k in kinds)
        row["stub_unmapped"] = str(unmapped_stubs)
        seen, ordered = set(), []
        for c in classes:
            if c not in seen:
                seen.add(c)
                ordered.append(c)
        row["caller_classes"] = ";".join(ordered)
        if not hits:
            row["verdict"] = "no-caller"
        else:
            row["verdict"] = "+".join(k for k in KIND_ORDER if k in kinds)
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- rendering
def _has(rows, kind):
    return any(kind in r["caller_kinds"] for r in rows)


def render(rows):
    tables = {}
    for r in rows:
        tables.setdefault(r["table_va"], []).append(r)
    sites = len(rows)
    bodies = {r["body_va"] for r in rows if r["body_va"]}
    no_body = [r for r in rows if r["verdict"] == "no-body"]
    thunked = [r for r in rows if r["registration_thunk"] == "yes"]
    no_caller = [r for r in rows if r["verdict"] == "no-caller"]
    classes = set()
    for r in rows:
        for c in r["caller_classes"].split(";"):
            if c and not c.startswith("table:") and c != "?":
                classes.add(c)

    lines = ["| table | slots | proposed class | store sites | installing bodies | registration thunk | "
             "callers | verdict | named classes whose constructor calls it |",
             "|---|---|---|---|---|---|---|---|---|"]
    for table in sorted(tables):
        mine = tables[table]
        bs = {r["body_va"] for r in mine if r["body_va"]}
        cs = []
        for r in mine:
            cs.extend(c for c in r["caller_classes"].split(";") if c)
        seen, ordered = set(), []
        for c in cs:
            if c not in seen:
                seen.add(c)
                ordered.append(c)
        shown = ", ".join(ordered[:4]) + (" +%d" % (len(ordered) - 4) if len(ordered) > 4 else "")
        lines.append("| `%s` | %s | %s | %d | %d | %s | %d | %s | %s |" % (
            table, mine[0]["slots"], mine[0]["proposed"] or "--", len(mine), len(bs),
            "yes" if any(r["registration_thunk"] == "yes" for r in mine) else "no",
            sum(int(r["callers"]) for r in mine),
            ", ".join(sorted({r["verdict"] for r in mine})), shown or "--"))
    lines.append("")

    multi = [t for t, mine in tables.items() if len(mine) > len({r["body_va"] for r in mine if r["body_va"]})]
    lines.append("**%d tables, %d store sites in %d bodies, %d of the sites in no symbol-map body.** %s "
                 "of the bodies that install one of these tables is a registration thunk target%s."
                 % (len(tables), sites, len(bodies), len(no_body),
                    "None" if not thunked else "%d" % len(thunked),
                    "" if not multi else ", and one body stores %d table%s more than once"
                    % (len(multi), "" if len(multi) == 1 else "s")))
    proposed = [t for t in tables if tables[t][0]["proposed"]]
    if proposed:
        lines.append("")
        lines.append("**%d of the %d tables carry a name from the caller rule**, the join in the "
                     "`proposed class` column and not this sweep's measurement; the rest are that tool's "
                     "`constrained` verdict (a caller set whose common ancestor is not one class), its "
                     "name-clash case, or a table with no named caller at all."
                     % (len(proposed), len(tables)))
    lines.append("")

    def bodies_with(kind):
        return [r for r in rows if kind in r["caller_kinds"]]

    def tables_with(kind):
        return [t for t in tables if any(kind in r["caller_kinds"] for r in tables[t])]

    stubs = sum(sum(int(n.split("=")[1]) for n in r["caller_kinds"].split(",")
                    if n.startswith("stub=")) for r in rows)
    stubs_unmapped = sum(int(r["stub_unmapped"]) for r in rows)
    unmapped = sum(sum(int(n.split("=")[1]) for n in r["caller_kinds"].split(",")
                       if n.startswith("unmapped=")) for r in rows)
    lines.append("**%d of the %d installing bodies are reached, and the shape is a base class, not "
                 "isolated code: %d are called from a class constructor (%d of the %d tables have at "
                 "least one) and %d from a class destructor.** %d named classes call an installer body "
                 "from their own constructor. The other edges are %d forwarder wrappers (a body whose "
                 "first call targets the installer), %d tail jumps of which %d come from code the symbol "
                 "map does not cover, and %d call%s from no map body at all; %d bodies have no inbound "
                 "edge."
                 % (len(bodies) - len(no_caller), len(bodies), len(bodies_with("ctor")),
                    len(tables_with("ctor")), len(tables),
                    len(bodies_with("dtor")), len(classes), len(bodies_with("wrapper")), stubs,
                    stubs_unmapped, unmapped, "" if unmapped == 1 else "s", len(no_caller)))
    return "\n".join(lines)


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def read_artifact(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_csv(path):
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def splice_doc(path, block):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(DOC_START), text.find(DOC_END)
    if i < 0 or j < 0:
        return None
    return text[:i] + DOC_START + "\n" + block + "\n" + text[j:]


# --------------------------------------------------------------------------- self-test
def write_rows(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def self_check():
    bad = []
    text_va = 0x1000
    text = bytearray(b"\x90" * 0x600)
    # 0x1010: c7 00 <table 0x9000>       mov [eax], 0x9000
    text[0x10:0x16] = b"\xc7\x00" + struct.pack("<I", 0x9000)
    # 0x1020: c7 41 08 <table 0x9100>    mov [ecx+8], 0x9100
    text[0x20:0x27] = b"\xc7\x41\x08" + struct.pack("<I", 0x9100)
    # 0x1030: c7 04 24 <table>           SIB form, must be skipped
    text[0x30:0x36] = b"\xc7\x04\x24" + struct.pack("<I", 0x9000)
    # 0x1040: c7 00 <not a table>
    text[0x40:0x46] = b"\xc7\x00" + struct.pack("<I", 0x1234)
    # 0x1e0: a store outside every map entry
    text[0x1E0:0x1E6] = b"\xc7\x00" + struct.pack("<I", 0x9000)
    # 0x1100 and 0x1200 call the installing body at 0x1000; 0x1300 tail-jumps to 0x1050
    text[0x100:0x105] = b"\xe8" + struct.pack("<i", 0x1000 - 0x1105)
    text[0x200:0x205] = b"\xe8" + struct.pack("<i", 0x1000 - 0x1205)
    text[0x300:0x305] = b"\xe9" + struct.pack("<i", 0x1050 - 0x1305)

    hits = stores(bytes(text), text_va, {0x9000, 0x9100})
    if sorted(h[0] for h in hits) != [0x1010, 0x1020, 0x11e0]:
        bad.append("stores: expected the three non-SIB sites, got %r" % (hits,))
    if [h[2] for h in hits] != ["reg", "reg", "reg"]:
        bad.append("stores: displacement form misread %r" % (hits,))

    inbound = edges(bytes(text), text_va)
    if [k for _, k in inbound.get(0x1000, [])] != ["call", "call"]:
        bad.append("edges: two callers expected for 0x1000, got %r" % (inbound.get(0x1000),))
    if [k for _, k in inbound.get(0x1050, [])] != ["jmp"]:
        bad.append("edges: a tail jump is an edge, got %r" % (inbound.get(0x1050),))
    if first_call(bytes(text[0x100:0x120]), 0x1100) != 0x1000:
        bad.append("first_call: the wrapper shape is not read")

    funcs = [(0x1000, 0x60, "FUN_0001000"), (0x1100, 0x20, "FUN_0001100"),
             (0x1200, 0x40, "FUN_0001200"), (0x1300, 0x20, "FUN_0001300")]
    names = [{"table_va": "0x00009000", "class": ""}, {"table_va": "0x00009100", "class": ""},
             {"table_va": "0x00009200", "class": "Named"}]
    ctors = [{"function_va": "0x00001200", "form": "ctor", "table_va": "0x00009200"}]
    stores_rows = [(0x1010, 0x9000, "reg"), (0x1020, 0x9100, "reg"), (0x11E0, 0x9000, "reg")]
    # 0x1200 is a class ctor, 0x1100's first call targets 0x1000 (a wrapper), 0x1300 is ordinary code
    rows = harvest({0x9000: 1, 0x9100: 1}, {0x9000: "4", 0x9100: "4"}, ctors, names, funcs,
                   {0x1000: [(0x1100, "call"), (0x1205, "call"), (0x1305, "jmp")]},
                   stores_rows, {0x1100: 0x1000}, set())
    by_store = {r["store_va"]: r for r in rows}
    if by_store["0x00001010"]["verdict"] != "ctor+wrapper+stub":
        bad.append("a ctor, a wrapper and a tail jump are three kinds: %r" % (by_store["0x00001010"],))
    if by_store["0x000011e0"]["verdict"] != "no-body":
        bad.append("a store outside every body is no-body: %r" % (by_store["0x000011e0"],))
    if "Named" not in by_store["0x00001010"]["caller_classes"].split(";"):
        bad.append("the caller class comes from the caller ctor's table: %r" % (by_store["0x00001010"],))

    rows2 = harvest({0x9100: 1}, {0x9100: "4"}, ctors, names, funcs, {},
                    [(0x1020, 0x9100, "reg")], {}, set())
    if rows2[0]["verdict"] != "no-caller":
        bad.append("an uncalled body is no-caller: %r" % (rows2[0],))

    rows3 = harvest({0x9100: 1}, {0x9100: "4"}, ctors, names, funcs,
                    {0x1000: [(0x1205, "call")]}, [(0x1020, 0x9100, "reg")], {}, set())
    if rows3[0]["verdict"] != "ctor":
        bad.append("a body called only from a ctor is ctor: %r" % (rows3[0],))

    rows4 = harvest({0x9100: 1}, {0x9100: "4"}, ctors, names, funcs,
                    {0x1000: [(0x1100, "call")]}, [(0x1020, 0x9100, "reg")], {0x1100: 0x1000}, set())
    if rows4[0]["verdict"] != "wrapper":
        bad.append("a forwarder that calls the body first is a wrapper: %r" % (rows4[0],))

    rows6 = harvest({0x9100: 1}, {0x9100: "4"}, ctors, names,
                    funcs + [(0x1400, 0x20, "FUN_0001400")],
                    {0x1000: [(0x1405, "call")]}, [(0x1020, 0x9100, "reg")], {}, set())
    if rows6[0]["verdict"] != "code":
        bad.append("a call from ordinary code is code: %r" % (rows6[0],))

    rows5 = harvest({0x9100: 1}, {0x9100: "4"}, ctors, names, funcs,
                    {0x1000: [(0x1205, "call")]}, [(0x1020, 0x9100, "reg")], {}, {0x1000})
    if rows5[0]["registration_thunk"] != "yes":
        bad.append("a thunk target is marked: %r" % (rows5[0],))

    out = render(rows)
    for want in ("2 tables, 3 store sites", "no-body", "None of"):
        if want not in out:
            bad.append("render lacks %r: %r" % (want, out))

    path = os.path.join(tempfile.mkdtemp(), "a.csv")
    write_artifact(rows, path)
    if read_artifact(path) != rows:
        bad.append("artifact round trip differs")
    if bad:
        print("FAIL class-orphan-tables: " + "; ".join(bad))
        return 1
    print("ok   class-orphan-tables: the store sweep, the caller kinds and the verdicts all agree with "
          "the synthetic image")
    return 0


# --------------------------------------------------------------------------- main
def main():
    if "--check" in sys.argv[1:]:
        return self_check()
    ap = argparse.ArgumentParser()
    ap.add_argument("--xbe", default=None)
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc", nargs="?", const=DOC, default=None)
    ap.add_argument("--check-doc", nargs="?", const=DOC, default=None)
    args = ap.parse_args()

    inv = os.path.join(ROOT, "inventory")
    if args.write_doc or args.check_doc or args.report:
        rows = read_artifact(ARTIFACT)
    else:
        if not args.xbe:
            ap.error("--xbe is required to measure (or use --report / --write-doc)")
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        from xbe import Xbe

        xbe = Xbe(args.xbe)
        dat = open(args.xbe, "rb").read()
        code = [s for s in xbe.sections if s.name == ".text"]
        text = b"".join(dat[s.foff:s.foff + s.fsize] for s in code)
        text_va = code[0].va if code else 0

        names = read_csv(os.path.join(inv, "xbe_class_table_names.csv"))
        tables = {int(r["table_va"], 16) for r in names if not r.get("registration_va")}
        slots = {int(r["table_va"], 16): r["slots"]
                 for r in read_csv(os.path.join(inv, "xbe_class_tables.csv"))}
        ctors = read_csv(os.path.join(inv, "class_constructors.csv"))
        funcs = []
        for r in read_csv(os.path.join(ROOT, "symbols/xbox_functions.csv")):
            funcs.append((int(r["address"], 16), int(r.get("size") or 0), r.get("name") or ""))
        funcs.sort()
        inbound = edges(text, text_va)
        thunks = set()
        for r in read_csv(os.path.join(inv, "class_registrations.csv")):
            for col in ("thunk_target", "thunk_va", "stub_va"):
                if r.get(col):
                    thunks.add(int(r[col], 16))
        size_of = {f[0]: f[1] for f in funcs}
        first_calls = {}
        for body in caller_bodies(inbound, funcs):
            n = min(size_of.get(body) or 64, 512)
            first_calls[body] = first_call(xbe.read_va(body, n), body)
        proposed = {}
        for r in read_csv(os.path.join(inv, "class_table_parents.csv")):
            if r.get("proposed_class"):
                proposed[int(r["table_va"], 16)] = r["proposed_class"]
        rows = harvest(tables, {k: slots.get(k, "") for k in tables}, ctors, names, funcs,
                       inbound, stores(text, text_va, tables), first_calls, thunks, proposed)
        if args.out:
            path = os.path.join(args.out, "class_orphan_tables.csv")
            write_artifact(rows, path)
            print("%s: %d rows" % (path, len(rows)))
    print(render(rows))
    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        new = splice_doc(path, render(rows))
        if new is None:
            print("FAIL class-orphan-tables: markers not found in %s" % path)
            return 1
        old = open(path, encoding="utf-8").read()
        if args.check_doc:
            if new != old:
                print("FAIL class-orphan-tables: %s is stale against the committed artifact" % path)
                return 1
            print("ok   class-orphan-tables: %s matches the committed artifact" % path)
            return 0
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
