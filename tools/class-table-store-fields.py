#!/usr/bin/env python3
"""Where a class table is stored: the object's own slot, or a member field of the object.

`docs/UNKNOWNS.md` U40's class-side entry counts the **250 tables no registration and no class map
names** and reports who installs them (`tools/class-table-installers.py`). That tool calls every
`mov [reg(+disp)], <imm32>` an *install*, which is true of the store and says nothing about the field
it writes. This tool reads the store's operand instead and partitions the tables by it:

  * `vtable-slot` -- every store writes `[reg]`, the object's own table slot, which is what a class's
    dispatch table lives in;
  * `member-field` -- every store writes `[reg+disp]` with a displacement, so the object holds a
    pointer to the table in one of its *own* fields and the table is a member's table, not the class's;
  * `vtable-slot+member` -- both forms, which is a body that installs its own table and a member's;
  * `frame-only` -- every store writes `[esp+disp]`/`[ebp+disp]`, a stack slot: the table address is
    passed or spilled and installed nowhere, so the table has no installer at all;
  * `global` -- every store writes an absolute `[disp32]`, a global variable rather than an object;
  * `no-store` -- no `C7 /0` store carries the address.

The named population is the control: **all 474 tables the registration path names are stored in the
object slot and none of them only into a member**, so the reading is the store's own form rather than a
guess about which pointer is `this`.

    tools/class-table-store-fields.py --xbe DUMP/default.xbe -o inventory
    tools/class-table-store-fields.py --report
    tools/class-table-store-fields.py --write-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-table-store-fields.py --check-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-table-store-fields.py --check          # synthetic image, no dump, writes nothing

The dump is never committed; the artifact and the doc are. Addresses, field offsets and counts only.
"""
import argparse
import bisect
import collections
import csv
import os
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

OUT = os.path.join("inventory", "class_table_store_fields.csv")
REACH = os.path.join("inventory", "native_vtable_reach.csv")
NAMES = os.path.join(ROOT, "inventory", "xbe_class_table_names.csv")
INST = os.path.join(ROOT, "inventory", "class_table_installers.csv")
CTORS = os.path.join(ROOT, "inventory", "class_constructors.csv")
SRC = os.path.join(ROOT, "inventory", "class_table_store_fields.csv")
PAGE = os.path.join("docs", "systems", "CLASS_REGISTRATION.md")
DOC_START = "<!-- generated:class-table-store-fields -->"
DOC_END = "<!-- /generated:class-table-store-fields -->"
FIELDS = ("table_va", "slots", "class", "population", "stores", "vtable_slots", "member_fields",
          "frame_sites", "global_sites", "indexed_sites", "member_of", "verdict", "evidence")
VERDICTS = ("vtable-slot", "vtable-slot+member", "member-field", "frame-only", "global", "indexed",
            "no-store")
POPULATIONS = ("named", "bounded", "plain-only", "no-installer", "unnamed-other")
REGISTERS = ("eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi")


# --------------------------------------------------------------------------- image
def sweep_stores(text, text_va):
    """[(site_va, imm, base, disp, form)] for every `C7 /0` store in one code blob.

    `form` is `slot` (base register, no displacement), `member` (base register, displacement),
    `frame` (base esp/ebp), `global` (absolute `[disp32]`) or `indexed` (a SIB with a scaled index,
    whose effective address is not a field of one object). The immediate's own address is the site,
    because that is what `tools/native-vtable.py` records as an install site.
    """
    out = []
    i, n = 0, len(text)
    while i < n - 9:
        if text[i] != 0xC7:
            i += 1
            continue
        modrm = text[i + 1]
        mod, reg, rm = modrm >> 6, (modrm >> 3) & 7, modrm & 7
        if reg != 0:
            i += 1
            continue
        kind, base = "member", ""
        if rm == 4:
            sib = text[i + 2] if i + 2 < n else 0
            base_no, index = sib & 7, (sib >> 3) & 7
            # the immediate's offset is the displacement's width plus three: a `disp32` SIB store
            # (`C7 84 24 <disp32> <imm32>`) is eleven bytes, so a ten-byte walk reads the
            # displacement's last byte instead of the table
            if mod == 0:
                ln = 11 if index == 4 and base_no == 5 else 7
            elif mod == 1:
                ln = 8
            elif mod == 2:
                ln = 11
            else:
                i += 1
                continue
            disp = 0
            if mod == 1:
                disp = struct.unpack_from("<b", text, i + 3)[0]
            elif mod == 2:
                disp = struct.unpack_from("<i", text, i + 3)[0]
            base = REGISTERS[base_no]
            if index != 4:
                # a scaled index: the effective address is an element of an array, not a field
                kind = "indexed"
            elif mod == 0 and base_no == 5:
                kind, disp = "global", 0
            else:
                kind = "slot" if disp == 0 else "member"
        elif mod == 0 and rm == 5:
            # `C7 05 <disp32> <imm32>` is ten bytes: a six-byte read takes the *displacement* for
            # the table's address, so a store into a global is lost and its target is never a key
            kind, base, disp, ln = "global", "", 0, 10
        elif mod == 0:
            kind, base, disp, ln = "slot", REGISTERS[rm], 0, 6
        elif mod == 1:
            kind, base, disp, ln = "member", REGISTERS[rm], struct.unpack_from("<b", text, i + 2)[0], 7
        elif mod == 2:
            # C7 /0 + disp32 + imm32 is ten bytes: an immediate read five bytes in lands on the
            # displacement's last byte and the store is lost (or read as another table).
            kind, base, disp, ln = "member", REGISTERS[rm], struct.unpack_from("<i", text, i + 2)[0], 10
        else:
            i += 1
            continue
        if kind in ("slot", "member") and base in ("esp", "ebp"):
            kind = "frame"
        imm = struct.unpack_from("<I", text, i + ln - 4)[0]
        out.append((text_va + i, imm, base, disp, kind))
        i += ln
    return out


def read_csv(path):
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def classify(sites):
    """(verdict, distinct member offsets, counts per form) from one table's store list."""
    forms = collections.Counter(f for _, _, _, _, f in sites)
    offs = sorted({d for _, _, _, d, f in sites if f in ("member", "frame")})
    member = sorted({d for _, _, _, d, f in sites if f == "member"})
    if not sites:
        return "no-store", [], forms
    if forms["slot"] and forms["member"]:
        return "vtable-slot+member", member, forms
    if forms["slot"]:
        return "vtable-slot", [], forms
    if forms["member"]:
        return "member-field", [d for d in member], forms
    if forms["indexed"]:
        return "indexed", [], forms
    if forms["frame"]:
        return "frame-only", [], forms
    if forms["global"]:
        return "global", [], forms
    return "no-store", [], forms


def harvest(tables, names, bounded, plain, reach_sites, ctor_classes, sites_by_table, body_of):
    """One row per table: which population it is in, where its stores write, and who owns a member."""
    rows = []
    for tva in sorted(tables):
        slots = tables[tva]
        cls = names.get(tva, "")
        if cls:
            pop = "named"
        elif tva in bounded:
            pop = "bounded"
        elif tva in plain:
            pop = "plain-only"
        elif tva in reach_sites:
            pop = "no-installer"
        else:
            pop = "unnamed-other"
        sites = sites_by_table.get(tva, [])
        verdict, offs, forms = classify(sites)
        owners = []
        for site, _, base, disp, form in sites:
            if form != "member":
                continue
            body = body_of(site)
            who = ctor_classes.get(body, "")
            if who and who not in owners:
                owners.append(who)
        rows.append({
            "table_va": "%#010x" % tva,
            "slots": str(slots),
            "class": cls,
            "population": pop,
            "stores": str(len(sites)),
            "vtable_slots": str(forms.get("slot", 0)),
            "member_fields": " ".join("%#x" % d for d in offs),
            "frame_sites": str(forms.get("frame", 0)),
            "global_sites": str(forms.get("global", 0)),
            "indexed_sites": str(forms.get("indexed", 0)),
            "member_of": ";".join(sorted(owners)),
            "verdict": verdict,
            "evidence": "%d store site(s): %s" % (
                len(sites), ", ".join("%s=%d" % (f, forms[f]) for f in sorted(forms)) or "none"),
        })
    return rows


# --------------------------------------------------------------------------- rendering
def render(rows):
    by_pop = collections.defaultdict(lambda: collections.Counter())
    for r in rows:
        by_pop[r["population"]][r["verdict"]] += 1
    total = {p: sum(c.values()) for p, c in by_pop.items()}

    def cell(pop, verdict):
        n = by_pop[pop][verdict]
        return str(n) if n else "--"

    lines = ["| population | tables | object slot | slot and member | member only | frame only | "
             "global only | no `C7` store |", "|---|---|---|---|---|---|---|---|"]
    label = {"named": "named by the registration path (control)", "bounded": "nameless: a class ctor stores it",
             "plain-only": "nameless: only bodies no class names store it",
             "no-installer": "nameless: no installer body", "unnamed-other": "unnamed, outside the 250"}
    for pop in POPULATIONS:
        if pop not in by_pop:
            continue
        lines.append("| %s | %d | %s | %s | %s | %s | %s | %s |" % (
            label[pop], total[pop], cell(pop, "vtable-slot"), cell(pop, "vtable-slot+member"),
            cell(pop, "member-field"), cell(pop, "frame-only"), cell(pop, "global"), cell(pop, "no-store")))
    lines.append("")

    named = [r for r in rows if r["population"] == "named"]
    member = [r for r in rows if r["verdict"] == "member-field"]
    mixed = [r for r in rows if r["verdict"] == "vtable-slot+member"]
    frame = [r for r in rows if r["verdict"] == "frame-only"]
    glob = [r for r in rows if r["verdict"] == "global"]
    frozen = [r for r in rows if r["verdict"] == "no-store"]

    nameless = [r for r in rows if r["population"] in ("bounded", "plain-only", "no-installer")]
    slot = [r for r in nameless if r["verdict"] in ("vtable-slot", "vtable-slot+member")]
    mem = [r for r in nameless if r["verdict"] in ("member-field", "vtable-slot+member")]
    dead = [r for r in nameless if r["verdict"] in ("frame-only", "no-store", "global")]
    dead_frame = [r for r in dead if r["verdict"] == "frame-only"]
    dead_global = [r for r in dead if r["verdict"] == "global"]
    dead_none = [r for r in dead if r["verdict"] == "no-store"]
    tail = ""
    if dead_global:
        tail += " and %d only into a global variable, where the object is a singleton in `.data`" % len(dead_global)
    if dead_none:
        tail += " and %d into nothing a `C7 /0` store carries" % len(dead_none)
    lines.append("**The %d named tables are the control: %d of them are written into the object's own slot "
                 "and none of them into a member.** A class's dispatch table is the pointer its installer "
                 "writes at `[reg]`. Of the %d tables no registration names, **%d are written into an "
                 "object slot at least once, %d into a member field, and %d have no object-field store of "
                 "their own at all** -- %d of those only into a stack slot, where the table's address is "
                 "passed or spilled rather than written into a field%s."
                 % (len(named), sum(1 for r in named if "member" not in r["verdict"]), len(nameless),
                    len(slot), len(mem), len(dead), len(dead_frame), tail))
    lines.append("")

    offsets = collections.defaultdict(set)
    for r in member + mixed:
        for f in r["member_fields"].split():
            offsets[int(f, 16)].add(r["table_va"])
    if offsets:
        lines.append("**The member offsets are a class's own layout, not a shared constant**: %s.** A "
                     "field that holds a table pointer is a member of the class whose constructor stores "
                     "it, so the table is that member's dispatch table and its slots are methods of the "
                     "member, not of the class -- naming it `<Class>_vt<slot>` would claim a class slot "
                     "the image does not have."
                     % ", ".join("`%#x` in %d table%s" % (d, len(v), "" if len(v) == 1 else "s")
                                 for d, v in sorted(offsets.items())))
        lines.append("")

    owners = collections.Counter()
    for r in member + mixed:
        for o in r["member_of"].split(";"):
            if o:
                owners[o] += 1
    if owners:
        top = ", ".join("%s (%d)" % (c, n) for c, n in owners.most_common(6))
        lines.append("**%d of the %d member tables have a class whose own constructor stores the field** "
                     "(%s), so the member's owner is known even though the table's class is not: these are "
                     "`<Class>`'s field, and the name the map lacks is the member's, not a class's."
                     % (sum(owners.values()), len(member) + len(mixed), top))
        lines.append("")

    bounded = [r for r in rows if r["population"] == "bounded"]
    b_member = [r for r in bounded if r["verdict"] == "member-field"]
    plain = [r for r in rows if r["population"] == "plain-only"]
    p_slot = [r for r in plain if r["verdict"] == "vtable-slot"]
    b_both = [r for r in bounded if r["verdict"] == "vtable-slot+member"]
    lines.append("**What this settles for U40.** %d of the %d tables a class constructor stores are member "
                 "tables (%d only a member, %d the object slot as well), so the `at-least-<base>` bound those "
                 "tables carry is a bound on the *owner* of a member, not on a class's dispatch table; the "
                 "reading that a ctor's table is its own or its base's holds only for the %d that write `[reg]` "
                 "at all. And %d of the %d plain-only tables are "
                 "written into the object slot by a body no class names, which is the shape of a "
                 "constructor the map has not named rather than of a table nothing installs."
                 % (len(b_member) + len(b_both), len(bounded), len(b_member), len(b_both),
                    len(bounded) - len(b_member) - len(b_both), len(p_slot), len(plain)))
    return "\n".join(lines)


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def splice_doc(path, block):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(DOC_START), text.find(DOC_END)
    if i < 0 or j < 0:
        return None
    return text[:i] + DOC_START + "\n" + block + "\n" + text[j:]


# --------------------------------------------------------------------------- self-test
def self_check():
    bad = []
    text_va = 0x1000
    text = bytearray(b"\x90" * 0x200)
    text[0x10:0x16] = b"\xc7\x00" + struct.pack("<I", 0x9000)              # mov [eax],0x9000
    text[0x20:0x27] = b"\xc7\x41\x28" + struct.pack("<I", 0x9100)          # mov [ecx+0x28],0x9100
    text[0x30:0x37] = b"\xc7\x41\x28" + struct.pack("<I", 0x9200)          # same field, second table
    text[0x90:0x9A] = b"\xc7\x86\xb8\x00\x00\x00" + struct.pack("<I", 0x9800)  # mov [esi+0xb8],0x9800
    text[0x40:0x46] = b"\xc7\x04\x24" + struct.pack("<I", 0x9300)          # mov [esp],0x9300
    text[0x50:0x57] = b"\xc7\x04\x24" + struct.pack("<I", 0x9400)          # frame only
    text[0x58:0x62] = b"\xc7\x05" + struct.pack("<I", 0x5fe168) + struct.pack("<I", 0x9500)  # mov ds:0x5fe168,0x9500
    text[0x70:0x77] = b"\xc7\x44\x24\x08" + struct.pack("<I", 0x9600)      # SIB, [esp+8]
    text[0x80:0x87] = b"\xc7\x04\xc8" + struct.pack("<I", 0x9700)          # SIB with index: indexed
    text[0xa0:0xab] = b"\xc7\x84\x24" + struct.pack("<I", 0x310) + struct.pack("<I", 0x9900)  # SIB, [esp+0x310]
    sites = sweep_stores(bytes(text), text_va)
    got = {imm: (form, disp) for _, imm, _, disp, form in sites}
    want = {0x9000: ("slot", 0), 0x9100: ("member", 0x28), 0x9200: ("member", 0x28),
            0x9800: ("member", 0xb8),
            0x9300: ("frame", 0), 0x9400: ("frame", 0), 0x9500: ("global", 0),
            0x9600: ("frame", 8), 0x9700: ("indexed", 0), 0x9900: ("frame", 0x310)}
    if got != want:
        bad.append("the store forms are misread: %r" % (got,))
    if 0x5fe168 in got:
        bad.append("a global store read its displacement as the table's address")
    if classify([s for s in sites if s[1] == 0x9000])[0] != "vtable-slot":
        bad.append("a slot store is vtable-slot")
    if classify([s for s in sites if s[1] == 0x9200])[0] != "member-field":
        bad.append("a member store is member-field")
    both = [s for s in sites if s[1] in (0x9000, 0x9200)]
    if classify(both)[0] != "vtable-slot+member":
        bad.append("a slot and a member store are both: %r" % (classify(both),))
    if classify([s for s in sites if s[1] == 0x9400])[0] != "frame-only":
        bad.append("a stack store installs nothing")
    if classify([s for s in sites if s[1] == 0x9800])[0] != "member-field":
        bad.append("a disp32 store is a member store: %r" % (classify([s for s in sites if s[1] == 0x9800]),))
    if classify([])[0] != "no-store":
        bad.append("an unstored table is no-store")

    tables = {0x9000: 4, 0x9100: 2, 0x9200: 2, 0x9400: 1}
    names = {0x9000: "Named"}
    rows = harvest(tables, names, {0x9100, 0x9200}, set(), {0x9100, 0x9200, 0x9400},
                   {0x1000: "Owner"}, {0x9000: [s for s in sites if s[1] == 0x9000],
                                       0x9100: [s for s in sites if s[1] == 0x9100],
                                       0x9200: [s for s in sites if s[1] == 0x9200],
                                       0x9400: [s for s in sites if s[1] == 0x9400]},
                   lambda site: 0x1000 if site < 0x1060 else 0x1200)
    by = {int(r["table_va"], 16): r for r in rows}
    if by[0x9000]["population"] != "named" or by[0x9000]["member_of"]:
        bad.append("a named table is the control, not a member: %r" % (by[0x9000],))
    if by[0x9100]["population"] != "bounded" or by[0x9100]["member_of"] != "Owner":
        bad.append("a bounded member table names its owner: %r" % (by[0x9100],))
    if by[0x9200]["member_fields"] != "0x28":
        bad.append("the member offset is read: %r" % (by[0x9200],))
    if by[0x9400]["verdict"] != "frame-only":
        bad.append("a frame-only table is not installed: %r" % (by[0x9400],))

    out = render(rows)
    for want_text in ("control", "member tables", "U40"):
        if want_text not in out:
            bad.append("render lacks %r" % (want_text,))

    path = os.path.join(tempfile.mkdtemp(), "a.csv")
    write_artifact(rows, path)
    with open(path, newline="", encoding="utf-8") as fh:
        if list(csv.DictReader(fh)) != rows:
            bad.append("artifact round trip differs")
    if bad:
        print("FAIL class-table-store-fields: " + "; ".join(bad))
        return 1
    print("ok   class-table-store-fields: the store forms, the partition and the owner join all agree "
          "with the synthetic image")
    return 0


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out", help="write the artifact under this directory")
    ap.add_argument("--report", action="store_true", help="render the page block to stdout")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return self_check()

    if args.check_doc:
        rows = read_csv(SRC)
        if not rows:
            print("FAIL class-table-store-fields: %s is missing" % SRC)
            return 1
        text = open(args.check_doc, encoding="utf-8").read()
        i, j = text.find(DOC_START), text.find(DOC_END)
        if i < 0 or j < 0:
            print("FAIL class-table-store-fields: %s has no generated block" % args.check_doc)
            return 1
        if text[i + len(DOC_START):j].strip() != render(rows).strip():
            print("FAIL class-table-store-fields: %s is stale; re-run --write-doc" % args.check_doc)
            return 1
        print("ok   class-table-store-fields: the page block matches the committed artifact")
        return 0

    if args.report and not args.xbe:
        rows = read_csv(SRC)
        print(render(rows))
        return 0

    if not args.xbe:
        print("FAIL class-table-store-fields: --xbe is required to read the image")
        return 1
    import xbe as xbemod
    image = xbemod.Xbe(args.xbe)
    code = next(s for s in image.sections if s.name == ".text")
    blob = image.data[code.foff:code.foff + min(code.fsize, code.vsize)]
    sites = sweep_stores(blob, code.va)
    by_table = collections.defaultdict(list)
    tables = {}
    for row in read_csv(REACH):
        tables[int(row["table_va"], 16)] = int(row["table_slots"] or 0)
    for site, imm, base, disp, form in sites:
        if imm in tables:
            by_table[imm].append((site, imm, base, disp, form))
    names = {}
    for row in read_csv(NAMES):
        cls = (row.get("class") or "").strip()
        if cls:
            names[int(row["table_va"], 16)] = cls
    bounded, plain, reach = set(), set(), set()
    for row in read_csv(INST):
        tva = int(row["table_va"], 16)
        reach.add(tva)
        if row["bound"]:
            bounded.add(tva)
        else:
            plain.add(tva)
    ctor_classes = {}
    for row in read_csv(CTORS):
        cls = (row.get("class") or "").strip()
        if cls and row.get("function_va"):
            ctor_classes[int(row["function_va"], 16)] = cls
    sizes = {}
    for row in read_csv(os.path.join(ROOT, "symbols", "xbox_functions.csv")):
        sizes[int(row["address"], 16)] = int(row["size"] or 0)
    starts = sorted(a for a, n in sizes.items() if n)

    def body_of(site):
        """The map body containing `site`: the nearest start at or below it that its size covers."""
        i = bisect.bisect_right(starts, site) - 1
        while i >= 0 and site - starts[i] <= 0x4000:
            a = starts[i]
            if a <= site < a + sizes[a]:
                return a
            i -= 1
        return 0

    rows = harvest(tables, names, bounded, plain, reach, ctor_classes, by_table, body_of)
    if args.out:
        write_artifact(rows, os.path.join(args.out, os.path.basename(OUT)))
    if args.write_doc:
        block = render(rows)
        spliced = splice_doc(args.write_doc, block)
        if spliced is None:
            print("FAIL class-table-store-fields: %s has no generated block" % args.write_doc)
            return 1
        open(args.write_doc, "w", encoding="utf-8").write(spliced)
        print("ok   class-table-store-fields: wrote %s" % args.write_doc)
    if args.report or not (args.out or args.write_doc):
        print(render(rows))
    counts = collections.Counter(r["verdict"] for r in rows)
    print("tables=%d %s" % (len(rows), " ".join("%s=%d" % (k, counts[k]) for k in VERDICTS if counts[k])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
