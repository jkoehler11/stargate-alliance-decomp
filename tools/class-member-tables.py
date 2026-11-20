#!/usr/bin/env python3
"""What the member tables hold: their slots, their shapes, and the families that repeat.

`tools/class-table-store-fields.py` settles *where* a class table's stores write and finds **49 tables no
registration names that a class constructor writes into a member field** -- `[reg+0x28]`, `[reg+0xb8]`, and
so on. Those are not a class's dispatch table: the object's field holds a pointer to an interface object,
so the table's slots are that interface's methods. This tool reads the table a member field points at and
censuses what is in it:

  * the slot bodies -- each slot is a dword pointer into `.text`, resolved to the function map's row, and
    each body is classified `pure` (`__purecall`), `stub` (a body of 8 bytes or less: `ret`, `xor eax,eax;
    ret`) or `real` (anything else);
  * the *shape* -- a digest of the whole slot vector, so two tables with the same content group;
  * the *family* -- shapes repeat, and two tables are kin when one **subsumes** the other (every slot the
    second implements, the first implements with the same body: that is an implementation ladder over one
    interface) or when they have the same slot count and agree on at least half their slots (that is one
    interface with per-class overrides). Kin tables are joined transitively and reported as one family.

The control is the class table itself. Every one of the 474 tables the registration path names has the
*same* slot-0 body (`0x00062650`, the `UObject` dispatch entry every class table starts with), so a table
whose slot 0 is something else is not a class table -- and no member table has it.

    tools/class-member-tables.py --xbe DUMP/default.xbe -o inventory
    tools/class-member-tables.py --report
    tools/class-member-tables.py --write-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-member-tables.py --check-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-member-tables.py --check          # synthetic slots, no dump, writes nothing

The dump is never committed; the artifact and the doc block are. Addresses, slot indices and counts only.
"""
import argparse
import collections
import csv
import hashlib
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

OUT = os.path.join("inventory", "class_member_tables.csv")
STORE = os.path.join(ROOT, "inventory", "class_table_store_fields.csv")
CLASS_SLOTS = os.path.join(ROOT, "inventory", "xbe_class_table_slots.csv")
SYMBOLS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
SRC = os.path.join(ROOT, "inventory", "class_member_tables.csv")
PAGE = os.path.join("docs", "systems", "CLASS_REGISTRATION.md")
DOC_START = "<!-- generated:class-member-tables -->"
DOC_END = "<!-- /generated:class-member-tables -->"
FIELDS = ("table_va", "slots", "owners", "fields", "shape", "family", "family_tables", "family_diff",
          "diff_slots", "purecall", "stubs", "in_map", "named", "outside_map", "real_names",
          "purecall_slots", "slot_bodies", "slot0", "verdict")
STUB_BYTES = 8


# --------------------------------------------------------------------------- reading
def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_table(image, va, count):
    """The `count` dwords at `va`, read one page-sized request at a time."""
    out = []
    for k in range(count):
        out.append(struct.unpack("<I", image.read_va(va + 4 * k, 4))[0])
    return out


def shape_of(slots):
    return hashlib.sha1(struct.pack("<%dI" % len(slots), *slots)).hexdigest()[:8]


def real_name(name):
    """A map row that is a name, not a placeholder: `FUN_...` is `tools/make-symbols.py`'s filler and
    `__purecall` is the compiler's abstract-slot stub, not the interface's method."""
    return bool(name) and not name.startswith("FUN_") and name != "__purecall"


def body_class(va, names, sizes):
    """`pure`, `stub`, `real` or `unknown` for one slot body."""
    if names.get(va) == "__purecall":
        return "pure"
    size = sizes.get(va)
    if size is None:
        return "unknown"
    return "stub" if size <= STUB_BYTES else "real"


# --------------------------------------------------------------------------- families
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
    """Two tables are kin when they are the same interface.

    Three ways to be sure, and each is checked rather than assumed: the slot vectors are *identical*
    (the same table emitted twice, once per translation unit); one *subsumes* the other (an
    implementation ladder -- the upper rung implements exactly the slots the lower leaves `__purecall`);
    or the tables have the same slot count, at least four slots, and agree on *more than half* of them
    (one interface with per-class overrides). The four-slot floor is deliberate: two two-slot tables that
    share one pointer share one stub, not a shape -- so a table of fewer than four slots is only kin to a
    table with its exact slot vector.
    """
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

    order = sorted(tables)
    for i, a in enumerate(order):
        for b in order[i + 1:]:
            if kin(tables[a], tables[b], pure):
                union(a, b)
    return {t: find(t) for t in tables}


# --------------------------------------------------------------------------- rows
def harvest(tables, owners, fields, names, sizes):
    """`tables` is {table_va: slots}, `owners`/`fields` are the store-field row's columns."""
    pure = lambda va: names.get(va) == "__purecall"
    fam = families(tables, pure)
    rows = []
    for tva in sorted(tables):
        slots = tables[tva]
        members = sorted(t for t in tables if fam[t] == fam[tva])
        rep = min(members)
        diff = [k for k in range(len(slots)) if slots[k] != tables[rep][k]]
        rows.append({
            "table_va": f"{tva:#010x}",
            "slots": str(len(slots)),
            "owners": owners.get(tva, ""),
            "fields": fields.get(tva, ""),
            "shape": shape_of(slots),
            "family": f"{fam[tva]:#010x}",
            "family_tables": str(len(members)),
            "family_diff": str(len(diff)),
            "diff_slots": " ".join(str(k) for k in diff),
            "purecall": str(sum(1 for s in slots if pure(s))),
            "stubs": str(sum(1 for s in slots if body_class(s, names, sizes) == "stub")),
            "in_map": str(sum(1 for s in slots if s in sizes)),
            "named": str(sum(1 for s in slots if s in names and real_name(names[s]))),
            "outside_map": str(sum(1 for s in slots if s not in sizes)),
            "real_names": " ".join("%d=%s" % (k, names[s]) for k, s in enumerate(slots)
                                   if s in names and real_name(names[s])),
            "purecall_slots": " ".join(str(k) for k, s in enumerate(slots) if pure(s)),
            "slot_bodies": " ".join("%#010x" % s for s in slots),
            "slot0": f"{slots[0]:#010x}" if slots else "",
            "verdict": "abstract" if slots and all(pure(s) for s in slots) else "interface",
        })
    return rows


# --------------------------------------------------------------------------- rendering
def family_rows(rows):
    by_fam = collections.defaultdict(list)
    for r in rows:
        by_fam[r["family"]].append(r)
    out = []
    for fam, rs in sorted(by_fam.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(rs) < 2:
            continue
        rep = min(rs, key=lambda r: int(r["table_va"], 16))
        diffs = sorted({int(k) for r in rs for k in r["diff_slots"].split()})
        owners = sorted({o for r in rs for o in r["owners"].split(";") if o})
        out.append({"family": fam, "tables": rs, "rep": rep, "diff": diffs, "owners": owners,
                    "slots": int(rep["slots"])})
    return out


def ladder_of(rows):
    """(`rungs`, `chain`) for one family: `rungs` is every table ordered from least to most abstract,
    `chain` the subsumption chain read out of it. A chain of one means the family has no implementation
    ladder in it -- the tables only agree on slots."""
    def vector(r):
        return [int(b, 16) for b in r["slot_bodies"].split()]

    def pure_idx(r):
        return {int(k) for k in r["purecall_slots"].split()}

    def covers(up, down):
        if len(up["slot_bodies"].split()) != len(down["slot_bodies"].split()):
            return False
        vd, pd, vu = vector(down), pure_idx(down), vector(up)
        if len(pure_idx(up)) >= len(pd):
            return False
        return all(x == y for k, (x, y) in enumerate(zip(vu, vd)) if k not in pd)

    rungs = sorted(rows, key=lambda r: (len(pure_idx(r)), int(r["table_va"], 16)))
    best = []
    for start in rungs:
        chain = [start]
        while True:
            # climb one rung at a time: of the tables that cover the top of the chain, take the least
            # abstract one above it, so a chain of four rungs is read as four and not as its ends.
            nexts = [r for r in rungs if r not in chain and covers(r, chain[-1])]
            if not nexts:
                break
            chain.append(max(nexts, key=lambda r: len(pure_idx(r))))
        if len(chain) > len(best):
            best = chain
    return rungs, best


def render(rows, control):
    if not rows:
        return "| no member table was read | |\n"
    lines = []
    n = len(rows)
    slots = sum(int(r["slots"]) for r in rows)
    inmap = sum(int(r["in_map"]) for r in rows)
    outside = sum(int(r["outside_map"]) for r in rows)
    named = sum(int(r["named"]) for r in rows)
    stubs = sum(int(r["stubs"]) for r in rows)
    pure = sum(int(r["purecall"]) for r in rows)
    shapes = len({r["shape"] for r in rows})
    fams = family_rows(rows)
    slot0 = {r["slot0"] for r in rows}
    control_hit = control["slot0"] in slot0

    lines.append("| family | tables | slots | owners | slots the family varies |")
    lines.append("|---|---|---|---|---|")
    for f in fams:
        names = ", ".join(f["owners"][:4]) or "--"
        lines.append("| `%s` | %d | %d | %s | %s |" % (
            f["family"], len(f["tables"]), f["slots"], names,
            ", ".join(str(k) for k in f["diff"]) or "--"))
    singles = sum(1 for r in rows if int(r["family_tables"]) == 1)
    lines.append("| %d table(s) in no family | %d | -- | -- | -- |" % (singles, singles))
    lines.append("")

    lines.append("**The %d member tables are interface vtables, and their slot 0 is not a class slot.** All "
                 "%d class tables the class-map census covers (%d of them named by the registration path) "
                 "share one slot-0 body, `%s`; %s of the %d member tables carry it, and their slot-0 values "
                 "are %d distinct bodies. A class dispatch table starts with the entry every class table "
                 "starts with; an interface object's table starts with its own first method, which is why no "
                 "slot here can be named `<Class>_vt<slot>`."
                 % (n, control["tables"], control["named"], control["slot0"],
                    "none" if not control_hit else "%d" % sum(1 for r in rows if r["slot0"] == control["slot0"]),
                    n, len(slot0)))
    lines.append("")

    bodies = {b for r in rows for b in r["slot_bodies"].split()}
    lines.append("**%d slots resolve to %d distinct bodies, and %d slots land outside the symbol map.** %d "
                 "slots are `__purecall`, %d are stubs of %d bytes or less (`ret`, `xor eax,eax; ret` -- the "
                 "default implementations of an interface whose real work is elsewhere), %d carry a real name "
                 "in the map rather than a `FUN_` row, and %d are `FUN_` rows the map covers."
                 % (slots, len(bodies), outside, pure, stubs, STUB_BYTES, named, inmap - named))
    lines.append("")

    if fams:
        big = max(fams, key=lambda f: (f["slots"], len(f["tables"])))
        lines.append("**%d of the %d tables fall into %d families, and the widest is the %d-slot one.** %s"
                     % (sum(len(f["tables"]) for f in fams), n, len(fams), big["slots"],
                        " ".join("`%s`: %d table%s at %d slot%s, varying %s." % (
                            f["family"], len(f["tables"]), "" if len(f["tables"]) == 1 else "s",
                            f["slots"], "" if f["slots"] == 1 else "s",
                            ", ".join(str(k) for k in f["diff"]) or "nothing")
                                 for f in fams)))
        lines.append("")
        for f in fams:
            rungs, chain = ladder_of(f["tables"])
            purecounts = sorted({int(r["purecall"]) for r in f["tables"]}, reverse=True)
            ladder = ""
            if len(chain) > 1:
                ladder = (" %d of them form a subsumption chain -- each rung implements exactly the slots "
                          "the one below leaves `__purecall`%s." % (
                              len(chain),
                              (", and the slots it implements resolve to " +
                               "; ".join(sorted({rv for r in chain for rv in r["real_names"].split()})))
                              if any(r["real_names"] for r in chain) else ""))
            lines.append("**In the `%s` family the %d table%s %s %s.** They carry %s `__purecall` slots "
                         "over %d slot%s.%s"
                         % (f["family"], len(f["tables"]), "" if len(f["tables"]) == 1 else "s",
                            "is" if len(f["tables"]) == 1 else "are",
                            ", ".join("`%s`" % r["table_va"] for r in
                                      sorted(f["tables"], key=lambda r: int(r["table_va"], 16))),
                            ", ".join(str(c) for c in purecounts),
                            f["slots"], "" if f["slots"] == 1 else "s", ladder))
            lines.append("")
    return "\n".join(lines)


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def splice_doc(text, block):
    i, j = text.find(DOC_START), text.find(DOC_END)
    if i < 0 or j < 0:
        return None
    return text[:i] + DOC_START + "\n" + block + "\n" + text[j:]


def control_of():
    """The class-table control: how many class tables the census covers, how many a name reaches, and
    the one slot-0 body they all share."""
    named = sum(1 for r in read_csv(STORE) if r.get("population") == "named")
    for row in read_csv(CLASS_SLOTS):
        if row.get("slot") == "0":
            return {"tables": int(row["tables"]), "named": named,
                    "slot0": "0x%08x" % int(row["most_common"], 16)}
    return {"tables": 0, "named": named, "slot0": ""}


# --------------------------------------------------------------------------- self-test
def self_check():
    bad = []
    names = {0x9001: "__purecall", 0x9002: "FUN_00007002", 0x9003: "FUN_00009003",
             0x9004: "Some_Real", 0x9005: "FUN_00009005"}
    sizes = {0x9001: 20, 0x9002: 3, 0x9003: 40, 0x9004: 69, 0x9005: 4}
    pure = lambda va: names.get(va) == "__purecall"

    if body_class(0x9001, names, sizes) != "pure" or body_class(0x9002, names, sizes) != "stub":
        bad.append("a purecall body and a 3-byte stub are classified")
    if body_class(0x9003, names, sizes) != "real" or body_class(0x9099, names, sizes) != "unknown":
        bad.append("a 40-byte body is real and an unmapped one is unknown")

    base = [0x9001, 0x9001, 0x9002, 0x9003]
    rung = [0x9001, 0x9004, 0x9002, 0x9003]
    if not subsumes(rung, base, pure) or subsumes(base, rung, pure):
        bad.append("a rung subsumes the abstract table and not the other way")
    if not kin(base, rung, pure):
        bad.append("a ladder is one family")
    other = [0x9005, 0x9005, 0x9005, 0x9005]
    if kin(base, other, pure):
        bad.append("an unrelated table is not kin")
    near = [0x9001, 0x9001, 0x9002, 0x9004]
    if not kin(base, near, pure):
        bad.append("two tables that agree on half their slots are kin")

    tables = {0x1000: base, 0x2000: rung, 0x3000: other}
    rows = harvest(tables, {0x1000: "Owner", 0x2000: "Owner2"}, {0x1000: "0x28", 0x2000: "0x28"},
                   names, sizes)
    by = {int(r["table_va"], 16): r for r in rows}
    if by[0x1000]["family"] != by[0x2000]["family"] or by[0x3000]["family_tables"] != "1":
        bad.append("the ladder is one family and the stranger is alone: %r" % (rows,))
    if by[0x1000]["verdict"] != "interface" or by[0x2000]["purecall"] != "1":
        bad.append("the rung's purecall count is read: %r" % (by[0x2000],))
    if by[0x2000]["real_names"] != "1=Some_Real":
        bad.append("only real names are listed: %r" % (by[0x2000]["real_names"],))
    if by[0x1000]["family_diff"] != "0" or by[0x2000]["family_diff"] != "1":
        bad.append("the family's representative and its diff are read: %r" % (rows,))
    if shape_of(base) == shape_of(rung):
        bad.append("a different slot vector is a different shape")

    out = render(rows, {"tables": 3, "named": 3, "slot0": "0x00062650"})
    for want in ("member tables", "subsumption chain", "outside the symbol map"):
        if want not in out:
            bad.append("render lacks %r" % (want,))

    import tempfile
    path = os.path.join(tempfile.mkdtemp(), "a.csv")
    write_artifact(rows, path)
    if read_csv(path) != rows:
        bad.append("artifact round trip differs")
    if bad:
        print("FAIL class-member-tables: " + "; ".join(bad))
        return 1
    print("ok   class-member-tables: the slot classes, the ladder rule and the family join all agree with "
          "the synthetic tables")
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
            print("FAIL class-member-tables: %s is missing" % SRC)
            return 1
        text = open(args.check_doc, encoding="utf-8").read()
        i, j = text.find(DOC_START), text.find(DOC_END)
        if i < 0 or j < 0:
            print("FAIL class-member-tables: %s has no generated block" % args.check_doc)
            return 1
        if text[i + len(DOC_START):j].strip() != render(rows, control_of()).strip():
            print("FAIL class-member-tables: %s is stale; re-run --write-doc" % args.check_doc)
            return 1
        print("ok   class-member-tables: the page block matches the committed artifact")
        return 0

    if args.report and not args.xbe:
        print(render(read_csv(SRC), control_of()))
        return 0

    if args.write_doc and not args.xbe:
        rows = read_csv(SRC)
        if not rows:
            print("FAIL class-member-tables: %s is missing" % SRC)
            return 1
        spliced = splice_doc(open(args.write_doc, encoding="utf-8").read(), render(rows, control_of()))
        if spliced is None:
            print("FAIL class-member-tables: %s has no generated block" % args.write_doc)
            return 1
        open(args.write_doc, "w", encoding="utf-8").write(spliced)
        return 0

    if not args.xbe:
        print("FAIL class-member-tables: --xbe is required to read the image")
        return 1
    import xbe as xbemod
    image = xbemod.Xbe(args.xbe)
    names, sizes = {}, {}
    for row in read_csv(SYMBOLS):
        a = int(row["address"], 16)
        names[a] = row["name"]
        sizes[a] = int(row["size"] or 0)

    tables, owners, fields = {}, {}, {}
    for row in read_csv(STORE):
        cls = (row.get("class") or "").strip()
        if cls or not row.get("member_fields"):
            continue
        tva = int(row["table_va"], 16)
        count = int(row["slots"] or 0)
        if count <= 0 or count > 512:
            continue
        tables[tva] = read_table(image, tva, count)
        owners[tva] = row["member_of"]
        fields[tva] = row["member_fields"]

    rows = harvest(tables, owners, fields, names, sizes)
    if args.out:
        write_artifact(rows, os.path.join(args.out, os.path.basename(OUT)))
    if args.write_doc:
        block = render(rows, control_of())
        text = open(args.write_doc, encoding="utf-8").read()
        spliced = splice_doc(text, block)
        if spliced is None:
            print("FAIL class-member-tables: %s has no generated block" % args.write_doc)
            return 1
        open(args.write_doc, "w", encoding="utf-8").write(spliced)
    if args.report:
        print(render(rows, control_of()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
