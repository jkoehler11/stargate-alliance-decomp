#!/usr/bin/env python3
"""The interface-held bodies read at the table: 306 a table's own methods, 58 one method under several.

`inventory/class_slot_declarers.csv` ends its declaration walk with two verdicts that carry no name:
**359 bodies only an interface table holds** (`interface`) and **5 bodies a named interface inherits**
(`inherited-interface`). Both were counts, and the question the thread has carried is whether they are
*defaults* -- one body at one index across several unrelated tables, like the five's `E_NOTIMPL` pair --
or real methods a class declares and the declarer walk missed.

The read is the table, not the body. Every one of the 232 interface tables is indexed from the image's own
slots (`inventory/native_vtable_shared.csv`'s extents), so each body's holders are the `(table, index)`
pairs that point at it, and three shapes fall out of that index:

  * `sole` -- one interface table holds it: that interface's own method, at its own index.
  * `copies` -- two or more tables hold it and their slots are **identical**: the same table emitted more
    than once, so the body is still one interface's method.
  * `shared` -- two or more tables hold it but they differ elsewhere: one implementation under several
    interfaces at one index, which is a shared base's method and not any holder's.

Two controls travel with the artifact. The holders the image gives back must be the `tables` and `indices`
the declarer walk recorded for that body -- a read that changes the population is not the same census --
and **no class table may hold any of the bodies**: a body a class table also holds is a method the walk
missed, and the point of the read is that the interface population has none. A row that fails either is
written as it was read with its evidence, and the census counts it.

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/interface-slot-bodies.py \\
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/interface-slot-bodies.py --report
    python3 tools/interface-slot-bodies.py --write-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/interface-slot-bodies.py --check-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/interface-slot-bodies.py --check        # synthetic, no dump, writes nothing

Addresses, slot indices and counts only; no image or package content is written.
"""
import argparse
import collections
import csv
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join("inventory", "interface_slot_bodies.csv")
DOC_A = ("<!-- generated:interface-slot-bodies -->", "<!-- /generated:interface-slot-bodies -->")
VERDICTS = ("interface", "inherited-interface")
FIELDS = ("row", "body_va", "size", "verdict", "sharing", "shape", "tables", "indices",
          "named_holders", "holders", "evidence")
CENSUS = ("bodies", "bytes", "interface", "inherited", "sole", "copies", "shared", "class_held",
          "tables_touched", "interface_tables", "named_tables", "shape_body", "shape_stub",
          "shape_const", "shape_notimpl", "shape_ret", "mismatched")
SHARING = ("sole", "copies", "shared")
SHAPES = ("body", "stub", "const", "notimpl", "ret")


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load(root):
    """The committed artifacts this reads: extents, names, sizes and the population itself."""
    shared = {}
    for r in read_csv(os.path.join(root, "inventory", "native_vtable_shared.csv")):
        try:
            shared[int(r["table_va"], 16)] = int(r["table_slots"] or 0)
        except (KeyError, ValueError):
            continue
    class_tables = {}
    for r in read_csv(os.path.join(root, "inventory", "xbe_class_tables.csv")):
        try:
            class_tables[int(r["table_va"], 16)] = int(r["slots"] or 0)
        except (KeyError, ValueError):
            continue
    # an interface table's own name, from the slot vocabulary's vote -- the same column the declarer
    # walk's `inherited-interface` verdict is decided on
    names = {}
    for r in read_csv(os.path.join(root, "inventory", "class_plain_tables.csv")):
        try:
            tva = int(r["table_va"], 16)
        except (KeyError, ValueError):
            continue
        if r.get("class_name") and r.get("signature") != "yes":
            names[tva] = r["class_name"]
    sizes = {}
    for r in read_csv(os.path.join(root, "inventory", "gap_heads.csv")):
        if r.get("row") != "head":
            continue
        try:
            sizes[r["head_va"]] = int(r.get("size") or 0)
        except ValueError:
            sizes[r["head_va"]] = 0
    decl = [r for r in read_csv(os.path.join(root, "inventory", "class_slot_declarers.csv"))
            if r.get("row") == "head" and r.get("verdict") in VERDICTS]
    return shared, class_tables, names, sizes, decl


def index_holds(slot_of, class_tables, shared):
    """body_va -> [(table, index, kind)] over the 492 class tables and the 232 interface tables."""
    holds = collections.defaultdict(list)
    for t in sorted(class_tables):
        try:
            slots = slot_of(t, class_tables[t])
        except Exception:  # noqa: BLE001 -- an extent that runs off a section is not a body
            continue
        for i, v in enumerate(slots):
            holds[v].append((t, i, "class"))
    for t in sorted(shared):
        if t in class_tables:
            continue
        try:
            slots = slot_of(t, shared[t])
        except Exception:  # noqa: BLE001
            continue
        for i, v in enumerate(slots):
            holds[v].append((t, i, "interface"))
    return holds


def shape_of(read, va, size):
    """A body's first instruction is its shape: a constant return and a stub are compiler defaults."""
    try:
        b = read(va, min(max(size, 1), 8))
    except Exception:  # noqa: BLE001 -- an unmapped body has no shape to read
        return "body"
    if b[:1] == b"\xc3":
        return "ret"
    if b[:5] == b"\xb8\x01\x40\x00\x80":          # mov eax,E_NOTIMPL (0x80004001)
        return "notimpl"
    if b[:1] == b"\xb8" and len(b) > 5 and b[5:6] == b"\xc3":
        return "const"
    if b[:3] in (b"\x33\xc0\xc3", b"\x31\xc0\xc3") or b[:4] == b"\x6a\xff\x58\xc3":
        return "const"
    if size <= 8:
        return "stub"
    return "body"


def indices_of(row):
    return sorted({int(x) for x in re.findall(r"\d+", row.get("indices") or "")})


def measure(image, data):
    shared, class_tables, names, sizes, decl = data
    sections = list(image.sections)

    def read(va, n):
        if not any(sec.contains(va) for sec in sections):
            raise KeyError(hex(va))
        return image.read_va(va, n)

    def slot_of(table, count):
        return [int.from_bytes(read(table + 4 * i, 4), "little") for i in range(count)]

    holds = index_holds(slot_of, class_tables, shared)
    slots_of = {}
    rows, counts = [], collections.Counter()
    touched = set()
    for r in decl:
        va = int(r["head_va"], 16)
        hs = holds.get(va, [])
        iface = [h for h in hs if h[2] == "interface"]
        klass = [h for h in hs if h[2] == "class"]
        tables = sorted({t for t, _, _ in iface})
        indices = sorted({i for _, i, _ in iface})
        try:
            claimed = int(r.get("tables") or 0)
        except ValueError:
            claimed = 0
        agree = (len(tables) == claimed and indices == indices_of(r)) if iface else False
        if not agree or klass:
            counts["mismatched"] += 1
        # identical slot tuples mean one table emitted more than once; a difference elsewhere means the
        # body is one implementation the several interfaces share
        tuples = set()
        for t in tables:
            if t not in slots_of:
                try:
                    slots_of[t] = tuple(slot_of(t, shared[t]))
                except Exception:  # noqa: BLE001
                    slots_of[t] = None
            tuples.add(slots_of[t])
        if len(tables) <= 1:
            sharing = "sole"
        elif len(tuples) == 1 and None not in tuples:
            sharing = "copies"
        else:
            sharing = "shared"
        try:
            size = int(r.get("size") or 0)
        except ValueError:
            size = 0
        if not size:
            size = sizes.get(r["head_va"], 0)
        shape = shape_of(read, va, size)
        named = [names[t] for t in tables if t in names]
        slot_txt = ",".join(str(i) for i in indices) or "-"
        if sharing == "sole":
            evidence = "1 interface table holds it at slot %s; no class table does" % slot_txt
        elif sharing == "copies":
            evidence = ("%d interface tables with the same slots hold it at slot %s -- one table emitted "
                        "more than once; no class table does" % (len(tables), slot_txt))
        else:
            evidence = ("%d interface tables that differ elsewhere hold it at slot %s; no class table "
                        "does" % (len(tables), slot_txt))
        if klass:
            evidence += "; %d class table(s) hold it too" % len(klass)
        if not agree and iface:
            evidence += "; the census says %d table(s) at %s" % (
                claimed, ",".join(str(i) for i in indices_of(r)) or "-")
        rows.append({
            "row": "head", "body_va": r["head_va"], "size": str(size), "verdict": r["verdict"],
            "sharing": sharing, "shape": shape, "tables": str(len(tables)),
            "indices": " ".join(str(i) for i in indices),
            "named_holders": "|".join(named),
            "holders": "|".join("0x%08x:%d" % (t, i) for t, i, _ in iface),
            "evidence": evidence,
        })
        counts["bodies"] += 1
        counts["bytes"] += size
        counts["interface" if r["verdict"] == "interface" else "inherited"] += 1
        counts[sharing] += 1
        counts["shape_" + shape] += 1
        if klass:
            counts["class_held"] += 1
        if named:
            counts["named_tables"] += 1
        touched.update(tables)
    counts["tables_touched"] = len(touched)
    counts["interface_tables"] = len(shared)
    census = dict((k, counts.get(k, 0)) for k in CENSUS)
    return rows, census


def render(rows, census):
    def pct(n):
        return "%d%%" % (round(100.0 * n / census["bodies"]) if census["bodies"] else 0)

    def cell(v):
        return "-" if not v else str(v)

    lines = []
    lines.append("**%d bodies, %d bytes: %d are one table's own method, %d the same table emitted twice, "
                 "%d one method under several interfaces, and 0 a class's.**" % (
                     census["bodies"], census["bytes"], census["sole"], census["copies"], census["shared"]))
    lines.append("")
    lines.append("The population is the declarer walk's two interface verdicts (`inventory/class_slot_declarers.csv`): "
                 "**%d** only an interface table holds and **%d** a named interface inherits. Read at the table "
                 "instead of at the body, each body's holders are the `(table, index)` pairs the image's own slots "
                 "state, and whether those tables hold the same slots tells a copy from a shared implementation:"
                 % (census["interface"], census["inherited"]))
    lines.append("")
    lines.append("| what holds the body | bodies | share | what it is |")
    lines.append("|---|---:|---:|---|")
    lines.append("| one interface table | %d | %s | that interface's own method, at its own index |"
                 % (census["sole"], pct(census["sole"])))
    lines.append("| two or more tables with the same slots | %d | %s | one table emitted more than once, so "
                 "still one interface's method |" % (census["copies"], pct(census["copies"])))
    lines.append("| two or more tables that differ elsewhere | %d | %s | one implementation under several "
                 "interfaces: a shared base's method |" % (census["shared"], pct(census["shared"])))
    lines.append("| a class table | %d | %s | a method the declarer walk missed |"
                 % (census["class_held"], pct(census["class_held"])))
    lines.append("")
    lines.append("**%d of the %d interface tables hold one of the %d, and %d hold none of them.** A body's shape "
                 "is its first instruction: a `ret`, an `E_NOTIMPL` return, a constant return or an eight-byte "
                 "stub is a compiler default, and everything else is a body." % (
                     census["tables_touched"], census["interface_tables"], census["bodies"],
                     census["interface_tables"] - census["tables_touched"]))
    lines.append("")
    lines.append("| shape | sole | copies | shared | bodies |")
    lines.append("|---|---:|---:|---:|---:|")
    for sh in SHAPES:
        lines.append("| `%s` | %d | %d | %d | %d |" % (
            sh, sum(1 for r in rows if r["shape"] == sh and r["sharing"] == "sole"),
            sum(1 for r in rows if r["shape"] == sh and r["sharing"] == "copies"),
            sum(1 for r in rows if r["shape"] == sh and r["sharing"] == "shared"),
            sum(1 for r in rows if r["shape"] == sh)))
    lines.append("")
    five = [r for r in rows if r["verdict"] == "inherited-interface"]
    if five:
        lines.append("| the %d a named interface inherits | body | slot | the named tables that hold it | shape |"
                     % len(five))
        lines.append("|---|---|---:|---|---|")
        for r in five:
            lines.append("| `%s` | %s | %s | %s | `%s` |" % (
                r["body_va"], r["size"], cell(r["indices"]),
                cell(str(r["named_holders"]).replace("|", ", ")), r["shape"]))
        lines.append("")
    lines.append("**%d bod%s fail%s the holder control** (the image's holders against the `tables`/`indices` "
                 "the declarer census recorded, and no class table holding the body)." % (
                     census["mismatched"], "y" if census["mismatched"] == 1 else "ies",
                     "s" if census["mismatched"] == 1 else ""))
    return "\n".join(lines)


def splice_doc(path, block, marks):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(marks[0]), text.find(marks[1])
    if i < 0 or j < 0:
        return None
    return text[:i] + marks[0] + "\n" + block + "\n" + text[j:]


def read_artifact(path):
    rows, census = [], None
    for r in read_csv(path):
        if (r.get("row") or "") == "census":
            census = {}
            for k in CENSUS:
                try:
                    census[k] = int(r.get(k) or 0)
                except ValueError:
                    census[k] = 0
        else:
            rows.append(r)
    return rows, census


def write_artifact(rows, census, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(FIELDS) + list(CENSUS), lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(dict((k, r.get(k, "")) for k in FIELDS))
        w.writerow(dict([("row", "census")] + [(k, census.get(k, 0)) for k in CENSUS]))


def self_check():
    """Synthetic tables only: the sharing rule, the shapes, the render and both round trips."""
    bad = []
    long_body = b"\x8b\x01\x83\xc0\x02\x89\x41\x04\xc3\x90\x90\x90"      # nine instructions, not a stub
    bodies = {0x9000: long_body, 0x9100: long_body, 0x9200: long_body,
              0x9300: b"\xb8\x01\x40\x00\x80\xc2\x2c\x00", 0x9400: long_body, 0x9500: long_body}
    # 0x1000 and 0x2000 are the same table emitted twice; 0x3000 differs from them at slots 0 and 1
    tables = {
        0x1000: [0x9000, 0x9100, 0x9200],
        0x2000: [0x9000, 0x9100, 0x9200],
        0x3000: [0x9300, 0x9400, 0x9200],
        0x4000: [0x9500, 0, 0],
    }
    decl = [
        {"row": "head", "head_va": "0x00009000", "size": "12", "verdict": "interface", "tables": "2",
         "indices": "0"},
        {"row": "head", "head_va": "0x00009200", "size": "12", "verdict": "interface", "tables": "3",
         "indices": "2"},
        {"row": "head", "head_va": "0x00009300", "size": "8", "verdict": "inherited-interface",
         "tables": "1", "indices": "0"},
        {"row": "head", "head_va": "0x00009400", "size": "12", "verdict": "interface", "tables": "1",
         "indices": "1"},
    ]

    class Sec:  # noqa: N801 -- a stand-in for an image section, not the section
        def contains(self, va):
            return True

    class Xbe:  # noqa: N801 -- a stand-in for the image object, not the image
        sections = [Sec()]

        def read_va(self, va, n):
            if va in bodies:
                return bodies[va][:n]
            for t, slots in tables.items():          # a table's dwords
                if t <= va < t + 4 * len(slots) and (va - t) % 4 == 0:
                    return int(slots[(va - t) // 4]).to_bytes(4, "little")[:n]
            return b"\x90" * n

    rows, census = measure(Xbe(), ({0x1000: 3, 0x2000: 3, 0x3000: 3, 0x4000: 3}, {}, {}, {}, decl))
    got = dict((r["body_va"], (r["sharing"], r["shape"])) for r in rows)
    if got.get("0x00009000") != ("copies", "body"):
        bad.append("two identical tables should be copies/body, got %r" % (got.get("0x00009000"),))
    if got.get("0x00009200") != ("shared", "body"):
        bad.append("three tables, one of them different, should be shared, got %r" % (got.get("0x00009200"),))
    if got.get("0x00009300") != ("sole", "notimpl"):
        bad.append("one table with E_NOTIMPL should be sole/notimpl, got %r" % (got.get("0x00009300"),))
    if got.get("0x00009400") != ("sole", "body"):
        bad.append("one table should be sole/body, got %r" % (got.get("0x00009400"),))
    if census["sole"] != 2 or census["copies"] != 1 or census["shared"] != 1:
        bad.append("the sharing split should be 2 sole / 1 copies / 1 shared, got %r" % (census,))
    if census["bodies"] != 4 or census["class_held"] != 0:
        bad.append("four bodies and no class holder, got %r" % (census,))
    block = render(rows, census)
    if not block.startswith("**4 bodies") or block.endswith("\n"):
        bad.append("the render should open with the count and end without a newline")
    import tempfile
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "iface.csv")
    write_artifact(rows, census, path)
    rows2, census2 = read_artifact(path)
    if len(rows2) != len(rows) or census2 != census:
        bad.append("the artifact round trip lost rows or the census")
    doc = os.path.join(tmp, "doc.md")
    open(doc, "w", encoding="utf-8").write("# x\n\n%s\n%s\n\ntail\n" % DOC_A)
    new = splice_doc(doc, block, DOC_A)
    if new is None or DOC_A[0] + "\n" + block + "\n" + DOC_A[1] not in new:
        bad.append("the doc splice did not place the block between its markers")
    for b in bad:
        print("FAIL interface-slot-bodies: %s" % b)
    print("%s interface-slot-bodies: synthetic tables, the sharing rule, the shapes, the render, both round "
          "trips" % ("FAIL" if bad else "ok  "))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.check_doc:
        rows, census = read_artifact(OUT)
        if not rows or not census:
            print("FAIL interface-slot-bodies: %s is missing or has no census row" % OUT)
            return 1
        block = render(rows, census)
        text = open(args.check_doc, encoding="utf-8").read()
        i, j = text.find(DOC_A[0]), text.find(DOC_A[1])
        if i < 0 or j < 0:
            print("FAIL interface-slot-bodies: %s has no %s block" % (args.check_doc, DOC_A[0]))
            return 1
        if text[i:j + len(DOC_A[1])] != DOC_A[0] + "\n" + block + "\n" + DOC_A[1]:
            print("FAIL interface-slot-bodies: %s's block does not reproduce from the committed CSV"
                  % args.check_doc)
            return 1
        print("ok   interface-slot-bodies: %s reproduces from the committed CSV" % args.check_doc)
        return 0
    if args.report or args.write_doc:
        rows, census = read_artifact(OUT)
        if not rows or not census:
            print("FAIL interface-slot-bodies: %s is missing or has no census row" % OUT)
            return 1
        block = render(rows, census)
        if args.report:
            print(block)
            return 0
        new = splice_doc(args.write_doc, block, DOC_A)
        if new is None:
            print("FAIL interface-slot-bodies: %s has no %s markers" % (args.write_doc, DOC_A[0]))
            return 1
        if new != open(args.write_doc, encoding="utf-8").read():
            open(args.write_doc, "w", encoding="utf-8").write(new)
            print("wrote %s (%s)" % (args.write_doc, DOC_A[0]))
        return 0
    if not args.xbe:
        ap.error("--xbe is required to read the image (or use --report/--check-doc/--check)")
    spec = importlib.util.spec_from_file_location("xbe", os.path.join(ROOT, "tools", "xbe.py"))
    xbemod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(xbemod)
    image = xbemod.Xbe(args.xbe)
    data = load(ROOT)
    if not data[4]:
        print("FAIL interface-slot-bodies: the declarer census holds no interface verdict to read")
        return 1
    rows, census = measure(image, data)
    out = os.path.join(args.out, os.path.basename(OUT)) if args.out else OUT
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    write_artifact(rows, census, out)
    print("wrote %s (%d rows: %d sole, %d copies, %d shared; %d table(s) touched of %d)"
          % (out, len(rows), census["sole"], census["copies"], census["shared"],
             census["tables_touched"], census["interface_tables"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
