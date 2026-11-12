#!/usr/bin/env python3
"""Which field holds the object at each slot-19/20 dispatch site, and what class owns it (U8).

`inventory/vcall_receiver_sites.csv` classified the *origin* of the object pointer at the 55 real
`call [reg+0x4c]`/`[reg+0x50]` sites: 28 of them read the object out of a field of another object.
That answered "the pair is mostly dispatched on an object another object holds" and left the next
question open -- *which* class holds it, because the field is only a byte offset until something
names its owner. This reads the member load the origin points at, so the offset stops being prose:

    mov ecx,[esi+0x4c]          offset 0x4c of the object in esi
    mov eax,[eax+0x220]         offset 0x220 of the object in eax
    mov eax,[ebp+0x8]           a stack argument, not a field at all

and then tries the three static routes that could name the owner's class, recording each as a
verdict rather than dropping the row:

    table    the containing function is a virtual in a class table -> the table names the class
    caller   every direct `E8` caller of it is a slot target of one named class
    -        neither: the function is a plain member function, so nothing in the image types it

The third case is the common one and is *why* this is a separate read: a class table only holds
virtuals, so a member function reached by a direct call carries no class at all.

    python3 tools/vcall-field-owner.py --xbe DUMP/default.xbe -o inventory/vcall_field_owners.csv
    python3 tools/vcall-field-owner.py --check     # synthetic bytes, no dump, writes nothing

The owner is only half the question. `field_offset` is what `tools/prop-stream.py` would need to
resolve it to a property name, and the property stream carries tag names, types and sizes but no
byte offsets -- so a name at an offset still needs a class layout, which is the negative this tool
records rather than guesses at.
"""
import argparse
import csv
import os
import struct
import sys

HEADER = ["site_va", "slot", "function", "field_va", "field_bytes", "field_form", "field_base",
          "field_offset", "origin_chain", "module", "own_register", "table_class", "table_slot",
          "callers", "caller_classes", "named_class", "route", "reason"]

REG = ["eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi"]


def decode_member_load(buf):
    """Decode `mov r32,[base+disp]` at the start of buf.

    Returns dict(length, form, base, disp) or None. Deliberately narrow: the 0x8b member-load form
    with a disp8/disp32 displacement is what every field origin in this image is; an indexed (SIB)
    or segment-prefixed load answers None instead of a guessed offset, so an unread row stays
    visible as unread.
    """
    if len(buf) < 3 or buf[0] != 0x8B:
        return None
    modrm = buf[1]
    mod, reg, rm = modrm >> 6, (modrm >> 3) & 7, modrm & 7
    if reg == 4:
        return None
    if mod == 0:
        if rm != 5:
            return None  # [reg] with no displacement is not a field
        if len(buf) < 6:
            return None
        disp = struct.unpack_from("<i", buf, 2)[0]
        base, length = None, 6
    elif mod in (1, 2):
        if rm == 4:
            return None  # SIB: an indexed load is not a member read of one object
        size = 1 if mod == 1 else 4
        if len(buf) < 2 + size:
            return None
        disp = struct.unpack_from("<b" if size == 1 else "<i", buf, 2)[0]
        base, length = REG[rm], 2 + size
    else:
        return None  # mod 3 is a register-to-register move
    if disp == 0:
        form = "mov %s,[%s]" % (REG[reg], base or "abs")
    elif disp > 0:
        form = "mov %s,[%s+0x%x]" % (REG[reg], base or "abs", disp)
    else:
        form = "mov %s,[%s-0x%x]" % (REG[reg], base or "abs", -disp)
    return {"length": length, "form": form, "base": base or "abs", "disp": disp}


def table_slots(xbe, tables, names):
    """Map a code address to the (class, table, slot) triples that install it."""
    out = {}
    for row in tables:
        try:
            va, n = int(row["table_va"], 16), int(row["slots"])
        except (KeyError, ValueError):
            continue
        blob = xbe.read_va(va, 4 * n)
        if not blob or len(blob) < 4 * n:
            continue
        for slot in range(n):
            ptr = struct.unpack_from("<I", blob, 4 * slot)[0]
            if ptr:
                out.setdefault(ptr, []).append((names.get(row["table_va"], ""), row["table_va"], slot))
    return out


def direct_callers(xbe, targets):
    """Scan the executable sections for `E8 rel32` calls landing on any address in targets."""
    out = {va: [] for va in targets}
    for sec in xbe.sections:
        if not sec.executable:
            continue
        size = sec.vsize or sec.fsize
        if not size:
            continue
        blob = xbe.read_va(sec.va, size)
        for i in range(len(blob) - 4):
            if blob[i] != 0xE8:
                continue
            target = sec.va + i + 5 + struct.unpack_from("<i", blob, i + 1)[0]
            if target in out:
                out[target].append(sec.va + i)
    return out


def functions_of(value):
    """`FUN_0001db50+0xa3` -> 0x0001db50."""
    text = value.split("+")[0]
    if "_" in text:
        text = text.rsplit("_", 1)[-1]  # FUN_0001db50 / thunk_FUN_003e67ed -> the address
    try:
        return int(text, 16)
    except ValueError:
        return None


def load_module_map(here):
    """function/code address -> module label, from the two attribution artifacts."""
    out = {}
    for name, key in (("inventory/function_modules.csv", "module"),
                      ("inventory/body_names.csv", "module")):
        path = os.path.join(here, name)
        if not os.path.exists(path):
            continue
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                try:
                    va = int(row["address"], 16)
                except (KeyError, ValueError):
                    continue
                label = (row.get(key) or "").strip()
                if label and label != "(unnamed)":
                    out.setdefault(va, label)
    return out


def selftest():
    cases = [
        (b"\x8b\x4e\x4c", "mov ecx,[esi+0x4c]", "esi", 0x4C),
        (b"\x8b\x80\x20\x02\x00\x00", "mov eax,[eax+0x220]", "eax", 0x220),
        (b"\x8b\x8e\x10\x03\x00\x00", "mov ecx,[esi+0x310]", "esi", 0x310),
        (b"\x8b\x4b\x4c", "mov ecx,[ebx+0x4c]", "ebx", 0x4C),
        (b"\x8b\x48\x48", "mov ecx,[eax+0x48]", "eax", 0x48),
        (b"\x8b\x4d\x08", "mov ecx,[ebp+0x8]", "ebp", 0x8),
        (b"\x8b\x4d\xc0", "mov ecx,[ebp-0x40]", "ebp", -0x40),
        (b"\x8b\x0d\x14\xf5\x5b\x00", "mov ecx,[abs+0x5bf514]", "abs", 0x5BF514),
    ]
    bad = 0
    for blob, form, base, disp in cases:
        got = decode_member_load(blob)
        ok = got and got["form"] == form and got["base"] == base and got["disp"] == disp
        if not ok:
            bad += 1
            print("FAIL decode %s -> %r" % (blob.hex(" "), got))
    negatives = [b"", b"\x8b", b"\x8b\x04\x88", b"\x8b\xc1", b"\x90\x90\x90", b"\x8b\x0d"]
    for blob in negatives:
        if decode_member_load(blob) is not None:
            bad += 1
            print("FAIL expected None for %r" % (blob.hex(" "),))
    if functions_of("FUN_0001db50+0xa3") != 0x1DB50:
        bad += 1
        print("FAIL functions_of")
    print("selftest: %d case(s), %d failure(s)" % (len(cases) + len(negatives) + 1, bad))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--xbe", help="DUMP/default.xbe")
    ap.add_argument("--sites", default=os.path.join(here, "inventory/vcall_receiver_sites.csv"))
    ap.add_argument("--tables", default=os.path.join(here, "inventory/xbe_class_tables.csv"))
    ap.add_argument("--names", default=os.path.join(here, "inventory/xbe_class_table_names.csv"))
    ap.add_argument("-o", "--out", default=os.path.join(here, "inventory/vcall_field_owners.csv"))
    ap.add_argument("--summary", action="store_true", help="print the census, write nothing")
    ap.add_argument("--check", action="store_true",
                    help="synthetic self-test; no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        return selftest()
    if not args.xbe:
        ap.error("--xbe is required unless --check")

    sys.path.insert(0, os.path.join(here, "tools"))
    from xbe import Xbe  # imported late so --check needs no dump

    xbe = Xbe(args.xbe)
    with open(args.tables, newline="", encoding="utf-8") as fh:
        tables = list(csv.DictReader(fh))
    with open(args.names, newline="", encoding="utf-8") as fh:
        names = {r["table_va"]: (r.get("class") or "").strip() for r in csv.DictReader(fh)}
    slots = table_slots(xbe, tables, names)
    modules = load_module_map(here)

    with open(args.sites, newline="", encoding="utf-8") as fh:
        field_rows = [r for r in csv.DictReader(fh)
                      if r.get("origin") == "field" and r.get("origin_va")]
    targets = {functions_of(r["function"]) for r in field_rows}
    targets.discard(None)
    callers = direct_callers(xbe, targets)

    rows = []
    for src in sorted(field_rows, key=lambda r: (int(r["slot"]), r["site_va"])):
        fn = functions_of(src["function"])
        field_va = int(src["origin_va"], 16)
        blob = xbe.read_va(field_va, 8)
        dec = decode_member_load(blob)
        row = {k: "" for k in HEADER}
        row.update(site_va=src["site_va"], slot=src["slot"], function=src["function"],
                   field_va=src["origin_va"], field_bytes=blob[:dec["length"]].hex(" ") if dec else blob[:6].hex(" "),
                   origin_chain=src.get("origin_chain", ""),
                   module=modules.get(fn, ""))
        if not dec:
            row["reason"] = "field instruction does not decode"
            rows.append(row)
            continue
        row.update(field_form=dec["form"], field_base=dec["base"], field_offset="0x%x" % dec["disp"])
        # A base register the chain roots at ecx with no write is the function's own `this`.
        chain = src.get("origin_chain", "")
        row["own_register"] = "yes" if chain and chain.split("<-")[-1] == "ecx" else ""
        members = slots.get(fn) or []
        if members:
            named = [m for m in members if m[0]]
            if named:
                row["table_class"], row["table_slot"] = named[0][0], str(named[0][2])
                row["named_class"], row["route"] = named[0][0], "table"
        found = callers.get(fn) or []
        row["callers"] = ";".join("0x%06x" % c for c in found)
        classes = []
        for c in found:
            for cls, _table, slot in slots.get(c, []):
                if cls:
                    classes.append("%s@%d" % (cls, slot))
        row["caller_classes"] = ";".join(classes)
        if not row["named_class"] and classes:
            distinct = {c.split("@")[0] for c in classes}
            row["caller_classes"] = ";".join(classes)
            if len(distinct) == 1:
                row["named_class"], row["route"] = distinct.pop(), "caller"
        if not row["named_class"]:
            why = []
            if not members:
                why.append("function is no table slot")
            if not found:
                why.append("no direct caller")
            elif not classes:
                why.append("no caller is a table slot")
            row["reason"] = "; ".join(why)
        rows.append(row)

    if not args.summary:
        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=HEADER, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    def counts(subset, key):
        out = {}
        for row in subset:
            k = row[key] or "-"
            out[k] = out.get(k, 0) + 1
        return ", ".join("%s %d" % (k, v) for k, v in sorted(out.items(),
                                                             key=lambda kv: (-kv[1], kv[0])))

    print("%d field site(s)" % len(rows))
    print("  offset: " + counts(rows, "field_offset"))
    print("  base: " + counts(rows, "field_base"))
    print("  slot: " + counts(rows, "slot"))
    print("  own register (this): " + counts([r for r in rows if r["own_register"]], "own_register"))
    print("  route: " + counts(rows, "route"))
    print("  named: " + counts([r for r in rows if r["named_class"]], "named_class"))
    print("  undecoded: " + str(sum(1 for r in rows if not r["field_offset"])))
    for slot in sorted({r["slot"] for r in rows}):
        print("  slot %s offsets: %s" % (slot, counts([r for r in rows if r["slot"] == slot], "field_offset")))
    if not args.summary:
        print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
