#!/usr/bin/env python3
"""The 232 interface tables' identity: 18 named by a slot, 49 held by a named field, 165 anonymous.

`tools/class-plain-tables.py` splits the 250 tables no registration names into **18 class tables** (the
class-table prefix) and **232 interface vtables** (its complement, and the same 232
`inventory/native_vtable_shared.csv` lists). That census answers *what kind of object a table is*; this one
asks *which tables are the same interface*, because the vtable -- not a name -- is the identity the image
carries:

  * the **shape**: two tables with the same slot vector are one interface emitted twice (a per-translation-unit
    copy), so the 232 tables are fewer than 232 interfaces;
  * the **family**: `tools/class-member-tables.py`'s kin rule, unchanged and applied to this population --
    identical vectors, one vector *subsuming* another of the same length (the upper rung implements exactly
    the slots the lower leaves `__purecall`), or two vectors of four slots or more agreeing on more than half.
    The four-slot floor is deliberate: two short tables that share one pointer share one stub, not a shape;
  * the **role**, which is the only naming channel this population has been measured to have: 18 tables are
    named by the class scope of their own slots' masked symbols (`CUIPlugin`, `CEncoderXMO`, ...), 49 are
    held by a **named member field** of a class (`tools/class-member-tables.py` builds that name from the
    field), and the remaining 165 carry neither;
  * the **object size**, read from the installer's allocation (`inventory/class_receiver_reads.csv`'s
    `new(...)` immediate), which is the size the class registers -- so it is a per-interface number rather
    than a shape.

**The 49 member tables are 49 of these 232, not a neighbouring population.** `inventory/class_member_tables.csv`
and `inventory/class_plain_tables.csv` intersect on all 49, so "the 232 interfaces" already *are* the member
tables plus the plain ones; what differs between a member table and a plain one is the **store** (a member
field of a class's object, or the object's own slot), not the table. Those 49 are also where a member's
*type* is answerable without a class name: the field names the interface, and this census says how many
owners hold the same shape.

    tools/class-interface-shapes.py --xbe DUMP/default.xbe -o inventory
    tools/class-interface-shapes.py --report
    tools/class-interface-shapes.py --write-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-interface-shapes.py --check-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-interface-shapes.py --check        # synthetic vectors, no dump, writes nothing

The dump is never committed; the artifact and the doc block are. Addresses, slot indices and counts only.
"""
import argparse
import collections
import csv
import hashlib
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OUT = os.path.join("inventory", "class_interface_shapes.csv")
PLAIN = os.path.join(ROOT, "inventory", "class_plain_tables.csv")
MEMBERS = os.path.join(ROOT, "inventory", "class_member_tables.csv")
RECEIVERS = os.path.join(ROOT, "inventory", "class_receiver_reads.csv")
SYMBOLS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
SRC = os.path.join(ROOT, "inventory", "class_interface_shapes.csv")
DOC_START = "<!-- generated:class-interface-shapes -->"
DOC_END = "<!-- /generated:class-interface-shapes -->"
FIELDS = ("table_va", "slots", "role", "name", "shape", "family", "family_tables", "family_diff",
          "twins", "purecall", "stubs", "real", "in_map", "named_slots", "outside_map", "object_size",
          "size_source", "owners", "fields", "verdict", "evidence")
ROLES = ("named", "member", "anonymous")
INTERFACE_VERDICTS = ("interface-named", "interface-unnamed")
STUB_BYTES = 8
NEW = re.compile(r"new\((0x[0-9a-fA-F]+):")
HEX = re.compile(r"^(0x)?[0-9a-fA-F]+$")


# --------------------------------------------------------------------------- reading
def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def key(text):
    text = (text or "").strip()
    return "%#010x" % int(text, 16) if HEX.match(text) else text


def read_table(image, va, count):
    return [struct.unpack("<I", image.read_va(va + 4 * k, 4))[0] for k in range(count)]


def shape_of(slots):
    return hashlib.sha1(struct.pack("<%dI" % len(slots), *slots)).hexdigest()[:8]


def sizes_of(text):
    """The allocation sizes one receiver row spells, in its own order.

    `temporary_sizes` holds bare hex (`0x30`), and the `receivers` column spells the same immediate as
    `new(0x30:DWORD PTR [eax])`; `new(-:...)` is an allocation whose size operand was not an immediate.
    """
    out = []
    for m in NEW.finditer(text or ""):
        va = int(m.group(1), 16)
        if va not in out:
            out.append(va)
    for tok in (text or "").split():
        if HEX.match(tok) and tok.lower().startswith("0x"):
            va = int(tok, 16)
            if va not in out:
                out.append(va)
    return out


# --------------------------------------------------------------------------- identity
def subsumes(a, b, pure):
    """`a` implements every slot `b` does, with the same body -- `b` is a rung below `a`."""
    if len(a) != len(b):
        return False
    seen = False
    for x, y in zip(a, b):
        if x == y and not pure(x):
            seen = True
            continue
        if pure(y):
            continue
        return False
    return seen


def agreement(a, b):
    if len(a) != len(b):
        return 0
    return sum(1 for x, y in zip(a, b) if x and x == y)


def kin(a, b, pure):
    """Two tables are the same interface: identical, a ladder of one, or more than half alike."""
    if len(a) != len(b):
        return False
    if a == b:
        return True
    if len(a) < 4:
        return False
    if subsumes(a, b, pure) or subsumes(b, a, pure):
        return True
    return agreement(a, b) * 2 > len(a)


def families(tables, pure):
    """{table_va: family_id}; a family id is its lowest table address. Union-find over `kin`."""
    parent = {t: t for t in tables}

    def find(t):
        while parent[t] != t:
            parent[t] = parent[parent[t]]
            t = parent[t]
        return t

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    ordered = sorted(tables)
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if kin(tables[a], tables[b], pure):
                union(a, b)
    return {t: find(t) for t in tables}


# --------------------------------------------------------------------------- harvesting
def member_map():
    """{table_va: (owners, fields, member_name)} for the tables a class holds in a field."""
    out = {}
    for row in read_csv(MEMBERS):
        owners = (row.get("owners") or "").strip()
        fields = (row.get("fields") or "").strip()
        if owners or fields:
            out[key(row["table_va"])] = (owners, fields, (row.get("member_name") or "").strip())
    return out


def receiver_map():
    """{table_va: (sizes, registry_size)} from the receiver census -- the installer's allocation."""
    out = {}
    for row in read_csv(RECEIVERS):
        sizes = sizes_of(row.get("temporary_sizes") or "")
        reg = (row.get("registry_size") or "").strip()
        if not sizes:
            sizes = sizes_of(row.get("receivers") or "")
        out[key(row["table_va"])] = (sizes, reg)
    return out


def load_symbols(path):
    """{va: name} from the committed map -- `__purecall` is the one name this tool needs from it."""
    names = {}
    for row in read_csv(path):
        try:
            names[int(row["address"], 16)] = row.get("name") or ""
        except (KeyError, ValueError):
            continue
    return names


def harvest(tables, named_of, pop_of, members, receivers, names, counters):
    pure = lambda va: names.get(va) == "__purecall"
    fam = families(tables, pure)
    by_family = collections.defaultdict(list)
    for t in tables:
        by_family[fam[t]].append(t)
    twins_of = collections.defaultdict(list)
    for t in tables:
        for u in tables:
            if u != t and tables[u] == tables[t]:
                twins_of[t].append(u)
    rows = []
    for tva in sorted(tables):
        slots = tables[tva]
        members_of = sorted(x for x in by_family[fam[tva]])
        rep = min(members_of)
        diff = [k for k in range(len(slots)) if slots[k] != tables[rep][k]]
        owners, fields, mname = members.get(key("%#010x" % tva), ("", "", ""))
        sizes, reg = receivers.get(key("%#010x" % tva), ([], ""))
        if tva in named_of:
            role = "named"
            name = named_of[tva]
        elif owners or fields:
            role = "member"
            name = (owners.replace(";", ",") or "unowned") + (("@%s" % fields.replace(" ", "/")) if fields else "")
        else:
            role = "anonymous"
            name = ""
        size = " ".join("%#x" % s for s in sizes) if sizes else ("%#x" % int(reg, 16) if reg else "")
        source = "allocation" if sizes else ("registry" if reg else "")
        if sizes and reg:
            source = "allocation=registry" if int(reg, 16) in sizes else "allocation!=registry"
        rows.append({
            "table_va": "%#010x" % tva,
            "slots": str(len(slots)),
            "role": role,
            "name": name,
            "shape": shape_of(slots),
            "family": "%#010x" % fam[tva],
            "family_tables": str(len(members_of)),
            "family_diff": str(len(diff)),
            "twins": " ".join("%#010x" % u for u in sorted(twins_of[tva])),
            "purecall": pop_of[tva]["purecall"],
            "stubs": pop_of[tva]["stubs"],
            "real": pop_of[tva]["fun_slots"],
            "in_map": str(int(pop_of[tva]["fun_slots"]) + int(pop_of[tva]["named_slots"])),
            "named_slots": pop_of[tva]["named_slots"],
            "outside_map": pop_of[tva]["outside_map"],
            "object_size": size,
            "size_source": source,
            "owners": owners,
            "fields": fields,
            "verdict": pop_of[tva]["verdict"],
            "evidence": "%s; %s slot(s); %d in a family of %d; %d twin(s); size %s"
                        % (name or "no name", len(slots), len(members_of), len(members_of),
                           len(twins_of[tva]), size or "unread"),
        })
        counters[role] += 1
    return rows


# --------------------------------------------------------------------------- rendering
def render(rows):
    counts = collections.Counter(r["role"] for r in rows)
    slots = collections.Counter()
    twins = collections.Counter()
    fams = collections.defaultdict(list)
    sizes = collections.defaultdict(list)
    for r in rows:
        slots[r["role"]] += int(r["slots"])
        if r["twins"]:
            twins[r["role"]] += 1
        fams[r["family"]].append(r)
        if r["object_size"]:
            sizes[r["object_size"]].append(r)
    multi = collections.Counter()
    single = collections.Counter()
    for fam, rs in fams.items():
        for r in rs:
            (multi if len(rs) > 1 else single)[r["role"]] += 1
    lines = []
    lines.append("| role | tables | slots | with an exact twin | in a family of two or more | alone |")
    lines.append("|---|---|---|---|---|---|")
    for role in ROLES:
        if not counts[role]:
            continue
        lines.append("| %s | %d | %d | %d | %d | %d |" % (
            role, counts[role], slots[role], twins[role], multi[role], single[role]))
    lines.append("| **all** | **%d** | **%d** | **%d** | **%d** | **%d** |" % (
        sum(counts.values()), sum(slots.values()), sum(twins.values()),
        sum(multi.values()), sum(single.values())))
    lines.append("")
    order = sorted(fams.items(), key=lambda kv: (-len(kv[1]), int(kv[0], 16)))
    biggest = order[0][1] if order else []
    lines.append("**%d table(s) over %d slot(s) are %d family(ies); %d table(s) have an exact twin and %d stand "
                 "alone.** The largest family holds **%d** tables of **%d** slots." % (
                     sum(counts.values()), sum(slots.values()), len(fams), sum(twins.values()),
                     sum(single.values()), len(biggest), int(biggest[0]["slots"]) if biggest else 0))
    lines.append("")
    lines.append("| family | tables | slots | roles | names | members (up to 12) |")
    lines.append("|---|---|---|---|---|---|")
    for fam, rs in order[:12]:
        roles = ",".join(sorted({r["role"] for r in rs}))
        names = ",".join(sorted({r["name"].split("@")[0] for r in rs if r["name"]})) or "-"
        members = " ".join(r["table_va"] for r in rs[:12])
        lines.append("| `%s` | %d | %s | %s | %s | %s |" % (
            fam, len(rs), rs[0]["slots"], roles, names, members))
    lines.append("")
    named_sizes = sorted(((s, rs) for s, rs in sizes.items()), key=lambda kv: (-len(kv[1]), kv[0]))
    lines.append("| object size (the installer's allocation) | tables | roles |")
    lines.append("|---|---|---|")
    shown = 0
    for size, rs in named_sizes:
        if shown >= 8:
            break
        shown += 1
        lines.append("| %s | %d | %s |" % (
            size, len(rs), ",".join(sorted({r["role"] for r in rs}))))
    unread = sum(1 for r in rows if not r["object_size"])
    if unread:
        lines.append("| no size read | %d | %s |" % (
            unread, ",".join(sorted({r["role"] for r in rows if not r["object_size"]}))))
    return "\n".join(lines)


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def splice_doc(text, block):
    i, j = text.find(DOC_START), text.find(DOC_END)
    if i < 0 or j < 0:
        return None
    return text[:i + len(DOC_START)] + "\n" + block + "\n" + text[j:]


# --------------------------------------------------------------------------- self-test
def self_check():
    bad = []
    pure = lambda va: va == 0x9000
    base = [0x9000, 0x9000, 0x1000, 0x2000]
    ladder = [0x9000, 0x3000, 0x1000, 0x2000]
    half = [0x4000, 0x4001, 0x1000, 0x2000]
    short_a = [0x9000, 0x5000]
    short_b = [0x9000, 0x6000]
    stranger = [0x7000, 0x7001, 0x7002]
    if not kin(base, ladder, pure):
        bad.append("a rung that implements the slot below leaves pure is kin")
    if not subsumes(ladder, base, pure) or subsumes(base, ladder, pure):
        bad.append("subsumption is one-way: the upper rung covers the lower, not the reverse")
    if not kin(base, half, pure):
        bad.append("more than half the slots alike is kin")
    if kin(short_a, short_b, pure):
        bad.append("two two-slot tables sharing one stub are not kin")
    if kin(base, stranger, pure):
        bad.append("a different slot count is never kin")
    tables = {0x1000: base, 0x2000: ladder, 0x3000: half, 0x4000: short_a, 0x5000: short_b,
              0x6000: stranger}
    fam = families(tables, pure)
    if not (fam[0x1000] == fam[0x2000] == fam[0x3000] == 0x1000):
        bad.append("the family is joined transitively, id the lowest address: %r" % (fam,))
    if fam[0x6000] != 0x6000:
        bad.append("a stranger is its own family")
    if fam[0x4000] == fam[0x5000]:
        bad.append("the short pair is two families")
    if shape_of(base) != shape_of(list(base)):
        bad.append("the shape is a function of the vector")
    if shape_of(base) == shape_of(ladder):
        bad.append("one slot apart is one shape apart")
    if sizes_of("new(0x30:DWORD PTR [eax]) this") != [0x30]:
        bad.append("the allocation immediate is read")
    if sizes_of("0x58") != [0x58]:
        bad.append("a bare hex size column is read")
    if sizes_of("new(-:DWORD PTR [eax]) this") != []:
        bad.append("an allocation with no immediate operand has no size")
    # role assignment and twin counting through harvest()
    pop = {t: {"purecall": "0", "stubs": "0", "fun_slots": "0", "named_slots": "0",
               "outside_map": "0", "verdict": "interface-unnamed"} for t in tables}
    rows = harvest(tables, {0x3000: "CUIThing"}, pop,
                   {"0x00002000": ("LinkerLoad", "0xb8", "")},
                   {"0x00001000": ([0x30], "0x30")},
                   {0x9000: "__purecall"}, collections.Counter())
    by = {int(r["table_va"], 16): r for r in rows}
    if by[0x3000]["role"] != "named" or by[0x3000]["name"] != "CUIThing":
        bad.append("a slot-named table's role is named: %r" % (by[0x3000],))
    if by[0x2000]["role"] != "member" or by[0x2000]["name"] != "LinkerLoad@0xb8":
        bad.append("a field-held table's role is member and its name is its owner and offset: %r"
                   % (by[0x2000],))
    if by[0x6000]["role"] != "anonymous" or by[0x6000]["name"]:
        bad.append("a table with neither channel is anonymous: %r" % (by[0x6000],))
    if by[0x2000]["twins"]:
        bad.append("a ladder rung has no exact twin")
    if by[0x1000]["object_size"] != "0x30" or by[0x1000]["size_source"] != "allocation=registry":
        bad.append("the size and its provenance are read: %r" % (by[0x1000],))
    if by[0x1000]["family_tables"] != "3":
        bad.append("the family count is the family's size: %r" % (by[0x1000],))
    if bad:
        for b in bad:
            print("FAIL class-interface-shapes: %s" % b)
        return 1
    print("ok   class-interface-shapes: the kin rule, the family join, the two roles, the size read and the "
          "twin count all agree with synthetic vectors")
    return 0


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out", help="write the artifact under this directory")
    ap.add_argument("--report", action="store_true", help="render the block to stdout")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return self_check()

    if args.check_doc:
        rows = read_csv(SRC)
        if not rows:
            print("FAIL class-interface-shapes: %s is missing" % SRC)
            return 1
        text = open(args.check_doc, encoding="utf-8").read()
        i, j = text.find(DOC_START), text.find(DOC_END)
        if i < 0 or j < 0:
            print("FAIL class-interface-shapes: %s has no generated block" % args.check_doc)
            return 1
        if text[i + len(DOC_START):j].strip() != render(rows).strip():
            print("FAIL class-interface-shapes: %s's block does not reproduce from %s" % (args.check_doc, SRC))
            return 1
        print("ok   class-interface-shapes: %s's block reproduces from %s" % (args.check_doc, SRC))
        return 0

    if args.report and not args.xbe:
        rows = read_csv(SRC)
        if not rows:
            print("FAIL class-interface-shapes: %s is missing" % SRC)
            return 1
        print(render(rows))
        return 0

    if args.write_doc and not args.xbe:
        rows = read_csv(SRC)
        if not rows:
            print("FAIL class-interface-shapes: %s is missing" % SRC)
            return 1
        text = open(args.write_doc, encoding="utf-8").read()
        new = splice_doc(text, render(rows))
        if new is None:
            print("FAIL class-interface-shapes: %s has no generated block markers" % args.write_doc)
            return 1
        if new != text:
            open(args.write_doc, "w", encoding="utf-8").write(new)
            print("wrote %s" % args.write_doc)
        return 0

    if not args.xbe:
        ap.error("--xbe is required to read the tables (or use --report/--check-doc/--check)")

    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import xbe as xbemod
    image = xbemod.Xbe(args.xbe)

    tables, pop_of, named_of, counters = {}, {}, {}, collections.Counter()
    for row in read_csv(PLAIN):
        if row.get("verdict") not in INTERFACE_VERDICTS:
            continue
        tva = int(row["table_va"], 16)
        count = int(row["slots"] or 0)
        if count <= 0 or count > 512:
            continue
        tables[tva] = read_table(image, tva, count)
        pop_of[tva] = row
        if row["verdict"] == "interface-named" and (row.get("vocab_class") or "").strip():
            named_of[tva] = row["vocab_class"].strip()
    if not tables:
        print("FAIL class-interface-shapes: %s has no interface row" % PLAIN)
        return 1
    names = load_symbols(SYMBOLS)
    rows = harvest(tables, named_of, pop_of, member_map(), receiver_map(), names, counters)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, os.path.basename(OUT))
        write_artifact(rows, path)
        print("wrote %s (%d rows)" % (path, len(rows)))
    if args.write_doc:
        block = render(rows)
        text = open(args.write_doc, encoding="utf-8").read()
        new = splice_doc(text, block)
        if new is None:
            print("FAIL class-interface-shapes: %s has no generated block markers" % args.write_doc)
            return 1
        open(args.write_doc, "w", encoding="utf-8").write(new)
        print("wrote %s" % args.write_doc)
    if args.report:
        print(render(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())