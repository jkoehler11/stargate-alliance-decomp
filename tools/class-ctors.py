#!/usr/bin/env python3
"""Name every class's constructor and destructor from the class table each one installs.

`inventory/xbe_class_table_names.csv` already names the 466 class tables by following each
registration -> thunk -> constructor and reading the `C7 /0` store of the table at `[this]`. That
chain reaches **one** constructor per class, and only for classes the registration table covers.

This tool inverts the direction: it scans `.text` for every `mov dword ptr [reg], <table>` whose
immediate is one of the 492 measured class tables, and reports the function each one sits in. So a
class's *whole* C++ construction set comes out -- the default constructor, the constructors that take
arguments, the scalar deleting destructor -- including for the 26 engine base classes no registration
literal names (`Object`, `Engine`, `Player`, ...).

Two independent shape signatures separate a constructor from a destructor:

  * **order** -- a constructor calls its base constructor *before* it installs the table (the object's
    vtable must not point at a half-built base); a destructor installs the table first and calls the
    base destructor after.
  * **return** -- a constructor returns `this` (`mov eax, esi` before the epilogue); a destructor
    returns nothing.

Only functions where the two agree are classified; the rest are reported `unclassified` with both
flags, so a disagreement is visible instead of being averaged away.

    python3 tools/class-ctors.py DUMP/default.xbe -o inventory        # the artifact
    python3 tools/class-ctors.py DUMP/default.xbe --apply             # + name them (needs a clean tree)

`--apply` writes `ctor_<Class>` / `dtor_<Class>` into `symbols/xbe_overrides.csv` (with the evidence)
and into `symbols/xbox_functions.csv`, which is what `make symbols` reads. A function that already
carries a non-`FUN_*` name is never renamed: an assert-derived name is better evidence than a shape
rule. A class with more than one constructor gets only the registration-reached one named; the others
are recorded in the artifact, not numbered into the symbol map (`docs/CONVENTIONS.md`).

The dump is never committed; the artifact and the docs are.
"""
import argparse
import bisect
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OVERRIDE_FIELDS = ("address", "name", "evidence", "class", "outer", "object_size", "assert_line",
                   "module", "source_file", "function_size")

CTOR_EVIDENCE = ("class table installed after a call into the base constructor; the body returns this")
DTOR_EVIDENCE = ("class table installed before any call; the body does not return this")


# --------------------------------------------------------------------------- image


def load_image(path):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from xbe import Xbe

    return Xbe(path)


def stores_to_tables(text, text_va, tables):
    """Every `mov dword ptr [reg(+disp)], imm32` whose immediate is a measured class table.

    Forms accepted: mod=00/01/10 with a register base (not esp, not ebp-relative). An absolute
    `[disp32]` store is a global initialiser, not an object's vtable, so it is reported separately.
    """
    hits = []
    i, n = 0, len(text)
    while i < n - 9:
        if text[i] != 0xC7:                                    # C7 /0 = mov r/m32, imm32
            i += 1
            continue
        modrm = text[i + 1]
        mod, reg, rm = modrm >> 6, (modrm >> 3) & 7, modrm & 7
        if reg != 0 or rm == 4:                                # /0 only, no SIB
            i += 1
            continue
        if mod == 0 and rm == 5:                               # absolute [disp32]
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
            hits.append((text_va + i, imm, kind))
        i += ln
    return hits


def shape(body, store_off, va, text_lo, text_hi):
    """The two signatures, plus the epilogue's argument count."""
    call_before = None
    for i in range(0, min(store_off, len(body)) - 4):
        if body[i] != 0xE8:
            continue
        rel = struct.unpack_from("<i", body, i + 1)[0]
        target = va + i + 5 + rel
        if text_lo <= target < text_hi:
            call_before = (va + i, target)
            break
    tail = body[-40:] if len(body) > 40 else body
    returns_this = any(tail[k] == 0x8B and 0xC0 <= tail[k + 1] <= 0xC7
                       for k in range(len(tail) - 1))
    seh = body[:3] == b"\x6a\xff\x68"
    if len(body) >= 3 and body[-3] == 0xC2:
        args = struct.unpack_from("<H", body, len(body) - 2)[0]
    elif body and body[-1] == 0xC3:
        args = 0
    else:
        args = None
    # Returning `this` is what a constructor does; a destructor never does. A constructor whose base
    # constructor was inlined (or whose base has no constructor to call) installs the table before any
    # call, so the order is the weaker of the two signatures and is only needed to separate a
    # destructor from a function that disagrees with itself.
    if returns_this:
        form = "ctor"
    elif not call_before:
        form = "dtor"
    else:
        form = "unclassified"
    return {
        "form": form,
        "call_before_store": call_before,
        "returns_this": returns_this,
        "seh": seh,
        "stack_args": args,
    }


# --------------------------------------------------------------------------- names


def naming_plan(rows, functions):
    """{address: (name, evidence)} for the installers that can carry a class name.

    `rows` are the census rows; `functions` maps address -> current name.
    """
    by_class = {}
    for r in rows:
        if not r["class"]:
            continue
        by_class.setdefault(r["class"], []).append(r)

    plan, unplaced = {}, []
    for cls, group in by_class.items():
        ctors = [r for r in group if r["form"] == "ctor"]
        dtors = [r for r in group if r["form"] == "dtor"]
        reg = group[0]["registration_ctor"]
        reg_rows = [r for r in group if reg and r["function_va"] == reg]
        chosen = None
        if reg:
            # The registration chain and the vtable store are independent evidence; they must agree.
            if len(reg_rows) == 1 and reg_rows[0]["form"] == "ctor":
                if len(ctors) == 1 and ctors[0] is not reg_rows[0]:
                    unplaced.append((cls, "ctor", [c["function_va"] for c in ctors]
                                     + ["registration names " + reg]))
                else:
                    chosen = reg_rows[0]
            else:
                unplaced.append((cls, "ctor", [reg + " is not a constructor by shape"]
                                 + [c["function_va"] for c in ctors]))
        elif len(ctors) == 1:
            chosen = ctors[0]
        elif len(ctors) > 1:
            unplaced.append((cls, "ctor", [c["function_va"] for c in ctors]))
        if chosen is not None:
            plan[int(chosen["function_va"], 16)] = ("ctor_" + cls, CTOR_EVIDENCE, chosen)
        if len(dtors) == 1:
            plan[int(dtors[0]["function_va"], 16)] = ("dtor_" + cls, DTOR_EVIDENCE, dtors[0])
        elif len(dtors) > 1:
            unplaced.append((cls, "dtor", [d["function_va"] for d in dtors]))

    kept, skipped, clashes = {}, [], set()
    for addr, (name, evidence, row) in plan.items():
        current = functions.get(addr, "")
        if not current:
            skipped.append((addr, name, "not in the function map"))
            continue
        if not current.startswith("FUN_"):
            skipped.append((addr, name, "already named " + current))
            continue
        if name in clashes:
            skipped.append((addr, name, "duplicate name"))
            continue
        clashes.add(name)
        kept[addr] = (name, evidence, row)
    return kept, skipped, unplaced


# --------------------------------------------------------------------------- writers


def write_census(rows, outdir):
    path = os.path.join(outdir, "class_constructors.csv")
    cols = ("function_va", "function_name", "class", "table_va", "object_size", "form",
            "stack_args", "returns_this", "call_before_store", "store_va", "registration_ctor",
            "evidence")
    os.makedirs(outdir, exist_ok=True)
    order = {"ctor": 0, "dtor": 1, "unclassified": 2}
    rows = sorted(rows, key=lambda r: (int(r["table_va"], 16), order.get(r["form"], 3),
                                       int(r["function_va"], 16)))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})
    return path


def apply_names(kept, overrides_path, functions_path):
    """Fold the names into the override file and the function map, both deterministically."""
    address_name = {("0x%08x" % a): (n, e) for a, (n, e, _r) in kept.items()}
    # A committed override is a name the build itself supplies, so it outranks anything derived from a
    # module prefix. One row can lag behind: an override added after the last import never reached
    # `symbols/xbox_functions.csv` (measured 2026-11-10 on #331's `ucpp_Register_GoauldDrone`), so the
    # override file is applied to the map in its own right, and the plan only adds new rows to it.
    committed = {}
    with open(overrides_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["name"]:
                committed[row["address"]] = row["name"]

    # 1. symbols/xbox_functions.csv -- the file `make symbols` reads.
    with open(functions_path, newline="", encoding="utf-8") as fh:
        text = fh.read()
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    header = lines[0].split(",")
    addr_col, name_col = header.index("address"), header.index("name")
    renamed = resynced = 0
    out = [lines[0]]
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) > name_col and parts[addr_col] in committed \
                and parts[name_col] != committed[parts[addr_col]]:
            parts[name_col] = committed[parts[addr_col]]
            resynced += 1
        elif len(parts) > name_col and parts[addr_col] in address_name \
                and parts[name_col].startswith("FUN_"):
            parts[name_col] = address_name[parts[addr_col]][0]
            renamed += 1
        out.append(",".join(parts))
    with open(functions_path, "w", newline="", encoding="utf-8") as fh:
        fh.write(nl.join(out) + nl)

    # 2. symbols/xbe_overrides.csv -- so a future Ghidra import reproduces the same names.
    with open(overrides_path, newline="", encoding="utf-8") as fh:
        text = fh.read()
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = [l for l in text.splitlines() if l.strip()]
    header = next(csv.reader([lines[0]]))
    have = {next(csv.reader([l]))[0] for l in lines[1:]}
    added = []
    for addr, (name, evidence) in sorted(address_name.items()):
        if addr in have:
            continue
        row = dict.fromkeys(header, "")
        row.update({"address": addr, "name": name, "evidence": evidence})
        added.append(",".join(_quote(row[k]) for k in header))
    if added:
        body = [lines[0]] + sorted(lines[1:] + added,
                                   key=lambda l: int(next(csv.reader([l]))[0], 16))
        with open(overrides_path, "w", newline="", encoding="utf-8") as fh:
            fh.write(nl.join(body) + nl)
    return renamed, len(added), resynced


def _quote(value):
    s = str(value)
    return '"' + s.replace('"', '""') + '"' if ("," in s or '"' in s) else s


def self_check():
    """A synthetic body passes the two signatures, a synthetic destructor fails the second."""
    ctor = (b"\x56\x8b\xf1\xe8\x00\x00\x00\x00"        # push esi; mov esi,ecx; call +0
            b"\xc7\x06\x00\x00\x00\x00"                 # mov [esi], table
            b"\x8b\xc6\x5e\xc3")                        # mov eax,esi; pop esi; ret
    dtor = (b"\x6a\xff\x68\x00\x00\x00\x00"             # SEH prologue
            b"\x56\x8b\xf1\xc7\x06\x00\x00\x00\x00"     # push esi; mov esi,ecx; mov [esi],table
            b"\x5e\xc3")                                # pop esi; ret
    c = shape(ctor, 8, 0x1000, 0x1000, 0x2000)
    d = shape(dtor, 12, 0x2000, 0x1000, 0x3000)
    bad = []
    if c["form"] != "ctor" or not c["returns_this"] or c["call_before_store"] is None:
        bad.append("constructor shape: %r" % (c,))
    if d["form"] != "dtor" or d["returns_this"] or d["call_before_store"] is not None:
        bad.append("destructor shape: %r" % (d,))
    text = b"\x00" * 4 + b"\xc7\x06\x78\x56\x34\x12" + b"\x00" * 8
    hits = stores_to_tables(text, 0x4000, {0x12345678: {}})
    if hits != [(0x4004, 0x12345678, "reg")]:
        bad.append("store scan: %r" % (hits,))
    if bad:
        print("FAIL class-ctors: " + "; ".join(bad))
        return 1
    print("ok   class-ctors: constructor/destructor shapes split, and a table store is found at its VA")
    return 0


# --------------------------------------------------------------------------- main


def main():
    if "--check" in sys.argv[1:]:
        return self_check()

    ap = argparse.ArgumentParser()
    ap.add_argument("xbe", nargs="?", help="DUMP/default.xbe")
    ap.add_argument("--tables", default=os.path.join(ROOT, "inventory/xbe_class_table_names.csv"))
    ap.add_argument("--symbols", default=os.path.join(ROOT, "symbols/xbox_functions.csv"))
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--apply", action="store_true",
                    help="fold the names into symbols/xbe_overrides.csv and symbols/xbox_functions.csv")
    args = ap.parse_args()
    if not args.xbe:
        ap.error("the image is required unless --check is given")

    xbe = load_image(args.xbe)
    text = next(s for s in xbe.sections if s.name == ".text")
    tb = xbe.data[text.foff:text.foff + text.fsize]

    tables = {}
    with open(args.tables, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            tables[int(row["table_va"], 16)] = row

    functions = {}
    order = []
    with open(args.symbols, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            functions[int(row["address"], 16)] = row["name"]
            order.append((int(row["address"], 16), int(row["size"]), row["name"]))
    order.sort()
    starts = [f[0] for f in order]

    def containing(va):
        i = bisect.bisect_right(starts, va) - 1
        if i >= 0 and order[i][0] <= va < order[i][0] + order[i][1]:
            return order[i]
        return None

    rows, orphans = [], []
    for store_va, table_va, kind in stores_to_tables(tb, text.va, tables):
        fn = containing(store_va)
        if fn is None or kind == "frame":
            orphans.append((store_va, table_va, kind))
            continue
        start, size, name = fn
        body = tb[start - text.va:start - text.va + size]
        s = shape(body, store_va - start, start, text.va, text.va + len(tb))
        trow = tables[table_va]
        rows.append({
            "function_va": "0x%08x" % start,
            "function_name": name,
            "class": trow["class"],
            "table_va": "0x%08x" % table_va,
            "object_size": trow["object_size"],
            "form": s["form"],
            "stack_args": "" if s["stack_args"] is None else s["stack_args"],
            "returns_this": "yes" if s["returns_this"] else "no",
            "call_before_store": "0x%08x" % s["call_before_store"][1] if s["call_before_store"] else "",
            "store_va": "0x%08x" % store_va,
            "registration_ctor": trow["constructor_va"],
            "evidence": {"ctor": CTOR_EVIDENCE, "dtor": DTOR_EVIDENCE,
                         "unclassified": "shape signatures disagree; left unclassified"}[s["form"]],
        })

    kept, skipped, unplaced = naming_plan(rows, functions)
    path = write_census(rows, args.out)

    by_form = {f: sum(1 for r in rows if r["form"] == f) for f in ("ctor", "dtor", "unclassified")}
    classes = {r["class"] for r in rows if r["class"]}
    agree = sum(1 for r in rows
                if r["registration_ctor"] and r["registration_ctor"] == r["function_va"]
                and r["form"] == "ctor")
    reg_disagree = sum(1 for r in rows
                       if r["registration_ctor"] and r["registration_ctor"] == r["function_va"]
                       and r["form"] != "ctor")
    print("registration chain: %d of 466 agree with the classified constructor, %d disagree"
          % (agree, reg_disagree))
    print("%s: %d rows" % (path, len(rows)))
    print("stores=%d functions=%d classes=%d ctor=%d dtor=%d unclassified=%d orphans=%d"
          % (len(rows), len({r["function_va"] for r in rows}), len(classes), by_form["ctor"],
             by_form["dtor"], by_form["unclassified"], len(orphans)))
    print("named=%d skipped=%d multi-ctor/dtor classes=%d"
          % (len(kept), len(skipped), len(unplaced)))
    for addr, name, why in skipped[:5]:
        print("  skip %#010x %s (%s)" % (addr, name, why))
    for cls, what, addrs in unplaced[:6]:
        print("  more than one %s for %s: %s" % (what, cls, " ".join(addrs)))

    if args.apply:
        renamed, added, resynced = apply_names(kept, os.path.join(ROOT, "symbols/xbe_overrides.csv"),
                                               os.path.join(ROOT, "symbols/xbox_functions.csv"))
        print("applied: %d rows renamed in symbols/xbox_functions.csv, %d override rows added, "
              "%d lagging override(s) re-applied" % (renamed, added, resynced))
        with open(os.path.join(ROOT, "symbols/xbox_functions.csv"), newline="",
                  encoding="utf-8") as fh:
            after = {row["address"]: row["name"] for row in csv.DictReader(fh)}
        missing = [a for a in kept if after.get("0x%08x" % a) != kept[a][0]]
        if missing:
            print("FAIL class-ctors: %d planned names are not in the map, first %#010x"
                  % (len(missing), missing[0]))
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
