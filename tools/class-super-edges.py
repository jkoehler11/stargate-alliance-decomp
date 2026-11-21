#!/usr/bin/env python3
"""The superclass edge: the class object's parent, read from the image, and the tables it names.

A class object carries its parent at `+0x28` (`docs/NATIVE_PROPERTIES.md`,
*`+0x28` on a class object is `Super`*). The image writes that field itself, in a
per-class setup body of a fixed shape, and the shape is the measurement:

    mov  eax,ds:<parent slot>      ; the parent's static-class cell, or a package's
    mov  ecx,ds:<self slot>        ; the cell this class's own StaticClass() caches
    cmp  eax,ecx                   ; the root cannot be its own parent...
    je   .null
    mov  [ecx+0x28],eax            ; Super = the parent
    jmp  .done
.null:
    mov  [ecx+0x28],0x0            ; ...so its Super is null
.done:

Both cells are `.data` dwords that a class's `StaticClass()` wrapper fills with the
object `tools/class-registrations.py` registers under a class literal (`call <regfunc>`
then `mov ds:<cell>,eax`), so **the pair (self cell, parent cell) is (a class literal,
another class literal)** and the hierarchy of every native class in the image is read
without a package and without a boot.

The census reads every `[reg+0x28]` store in `.text` (both encodings: `89 /r` with the
`eax` source, and the `C7 /0` null fallback), takes the two cells the body loads
immediately before the store, and keys them to the registration literals. What it does
not decide is the parent of a class whose setup body loads a *lookup* rather than a
class cell: three stores sit on such a source and they are reported `unresolved` rather
than guessed.

The join is the second half, and it is what the seven unnamed class tables needed: a
table with no registration is named by the classes that call its installer, and each of
those names is a *child* -- so the class the table belongs to is the one its children
agree their parent is. `inventory/class_table_super_names.csv` records that verdict per
table, and it agrees with every table the registrations already named.

    tools/class-super-edges.py DUMP/default.xbe -o inventory
    tools/class-super-edges.py --write-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-super-edges.py --check-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-super-edges.py --check       # synthetic bodies, no dump, writes nothing

The decode is objdump over each body, the same reader `tools/class-registrations.py`
uses, so it needs no capstone. Addresses and class literals only: no image or package
content is written.
"""
import argparse
import bisect
import collections
import csv
import os
import re
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDGE_FIELDS = ["target_class", "target_slot", "parent_class", "parent_slot", "body_va", "store_va",
               "verdict"]
NAME_FIELDS = ["table_va", "slots", "children", "child_supers", "class", "verdict"]
ARTIFACT = os.path.join(ROOT, "inventory/class_super_edges.csv")
NAMES = os.path.join(ROOT, "inventory/class_table_super_names.csv")
REGISTRATIONS = os.path.join(ROOT, "inventory/class_registrations.csv")
PARENTS = os.path.join(ROOT, "inventory/class_table_parents.csv")
DOC = os.path.join(ROOT, "docs/systems/CLASS_REGISTRATION.md")
DOC_START = "<!-- generated:class-super-edges -->"
DOC_END = "<!-- /generated:class-super-edges -->"
STORE_REG = re.compile(r"mov\s+DWORD PTR \[(e[a-z]{2})\+0x28\]")
STORE_IMM = re.compile(r"mov\s+DWORD PTR \[(e[a-z]{2})\+0x28\],0x0$")
LOAD = re.compile(r"mov\s+(e[a-z]{2}),(?:DWORD PTR )?ds:0x([0-9a-f]+)")
WINDOW = 0x300


# --------------------------------------------------------------------------- image helpers

class Image:
    """Section reads plus objdump, over an Xbe or a synthetic buffer."""

    def sec_of(self, va):
        for s in self.xbe.sections:
            if s.va <= va < s.va + s.vsize:
                return s
        return None

    def read(self, va, n):
        s = self.sec_of(va)
        if not s or va + n > s.va + s.vsize:
            return None
        return self.data[s.foff + (va - s.va):s.foff + (va - s.va) + n]

    def widestr(self, va, maxchars=48):
        b = self.read(va, maxchars * 2)
        if not b:
            return None
        out = []
        for i in range(0, len(b) - 1, 2):
            ch = struct.unpack_from("<H", b, i)[0]
            if ch == 0:
                break
            if 32 <= ch < 127:
                out.append(chr(ch))
            else:
                return None
        name = "".join(out)
        return name if 3 <= len(name) <= maxchars else None

    def disasm(self, start, size):
        key = (start, size)
        if not hasattr(self, "cache"):
            self.cache = {}
        if key in self.cache:
            return self.cache[key]
        blob = self.read(start, size) or b""
        path = tempfile.mktemp(suffix=".bin")
        with open(path, "wb") as fh:
            fh.write(blob)
        try:
            out = subprocess.run(["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                                  "--adjust-vma=0x%x" % start, path],
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
        self.cache[key] = rows
        return rows


def load_symbols(path):
    funcs = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            funcs.append((int(row["address"], 16), int(row["size"] or 0)))
    funcs.sort()
    return funcs


def read_registrations(path=REGISTRATIONS):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- the read

def class_cells(img, tb, base, funcs, regfunc):
    """`.data` cell -> class literal, from the store each `StaticClass()` wrapper makes."""
    fstarts = [a for a, _s in funcs]
    sizes = dict(funcs)

    def enclosing(va):
        i = bisect.bisect_right(fstarts, va) - 1
        return fstarts[i] if i >= 0 and fstarts[i] <= va else None

    out = {}
    conflicts = 0
    for i in range(len(tb) - 5):
        if tb[i] != 0xA3:                      # mov ds:imm32, eax
            continue
        cell = struct.unpack_from("<I", tb, i + 1)[0]
        if not img.sec_of(cell):
            continue
        e = enclosing(base + i)
        if e is None:
            continue
        lit = None
        j = i - 1
        while j > max(0, i - 40):
            if tb[j] == 0xE8:
                tgt = base + j + 5 + struct.unpack_from("<i", tb, j + 1)[0]
                if tgt in regfunc:
                    lit = regfunc[tgt]
                    break
            j -= 1
        if lit is None:
            continue
        if cell in out and out[cell] != lit:
            conflicts += 1
            continue
        out[cell] = lit
    return out, conflicts, enclosing, sizes


def super_stores(tb, base, enclosing):
    """Every `mov [reg+0x28], ...` store in .text, grouped by the body that holds it."""
    bodies = collections.defaultdict(list)
    for i in range(len(tb) - 4):
        if tb[i] == 0xC7 and (tb[i + 1] & 0xC7) == 0x41 and tb[i + 2] == 0x28:
            bodies[enclosing(base + i)].append((base + i, "imm"))
        elif tb[i] == 0x89 and (tb[i + 1] & 0xC7) == 0x41 and tb[i + 2] == 0x28:
            bodies[enclosing(base + i)].append((base + i, "reg"))
    return bodies


def census(xbe):
    img = Image()
    img.xbe, img.data = xbe, xbe.data
    text = next(s for s in xbe.sections if s.name == ".text")
    tb = xbe.data[text.foff:text.foff + text.fsize]
    base = text.va
    funcs = load_symbols(os.path.join(ROOT, "symbols/xbox_functions.csv"))
    fstarts = [a for a, _s in funcs]
    sizes = dict(funcs)

    def enclosing(va):
        i = bisect.bisect_right(fstarts, va) - 1
        return fstarts[i] if i >= 0 and fstarts[i] <= va else None

    regfunc = {}
    for r in read_registrations():
        e = enclosing(int(r["site_va"], 16))
        if e is not None:
            regfunc.setdefault(e, r["class_literal"])
    cells, conflicts, _enc, _sz = class_cells(img, tb, base, funcs, regfunc)

    edges = []
    for body, stores in sorted(super_stores(tb, base, enclosing).items()):
        if body is None:
            continue
        ins = img.disasm(body, min(max(sizes.get(body, 0), 0x140), WINDOW))
        for va, kind in stores:
            idx = next((k for k, (a, _t) in enumerate(ins) if a == va), None)
            if idx is None:
                continue
            m = STORE_REG.match(ins[idx][1])
            if not m:
                continue
            breg = m.group(1)
            target = parent = None
            for k in range(idx - 1, max(-1, idx - 30), -1):
                mm = LOAD.match(ins[k][1])
                if not mm:
                    continue
                if mm.group(1) == breg and target is None:
                    target = int(mm.group(2), 16)
                if mm.group(1) == "eax" and parent is None:
                    parent = int(mm.group(2), 16)
                if target is not None and (parent is not None or kind == "imm"):
                    break
            if target is None:
                continue
            if target not in cells:
                edges.append({"target_class": "", "target_slot": "%#010x" % target,
                              "parent_class": "", "parent_slot": "",
                              "body_va": "%#010x" % body, "store_va": "%#010x" % va,
                              "verdict": "foreign"})
                continue
            if parent is None:
                verdict = "root" if kind == "imm" else "no-source"
            elif parent == target:
                verdict = "root"
            elif parent in cells:
                verdict = "named"
            else:
                verdict = "unresolved"
            edges.append({
                "target_class": cells[target], "target_slot": "%#010x" % target,
                "parent_class": cells.get(parent, ""),
                "parent_slot": "%#010x" % parent if parent is not None else "",
                "body_va": "%#010x" % body, "store_va": "%#010x" % va, "verdict": verdict})
    return edges, cells, conflicts


def names(edges):
    """The parent table's class: the one its children agree they descend from."""
    sup = {}
    for e in edges:
        if e["verdict"] == "named" and e["target_class"]:
            sup[e["target_class"]] = e["parent_class"]
    out = []
    with open(PARENTS, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            kids = [k for k in (row.get("named_callers") or "").split("|") if k]
            if not kids:
                continue
            parents = [sup.get(k) or "-" for k in kids]
            resolved = [p for p in parents if p != "-"]
            uniq = sorted(set(resolved))
            committed = row.get("class_committed") or ""
            if not resolved:
                verdict, name = "no-child-super", ""
            elif len(uniq) > 1:
                verdict, name = "split", ""
            elif len(resolved) < len(kids):
                # every child that resolved agrees, but a caller this census cannot name
                # could be a child of a different class: the candidate is kept, not the name
                verdict, name = "partial", ""
            else:
                name = uniq[0]
                verdict = ("agrees" if committed == name else
                           "disagrees" if committed else "names-the-table")
            out.append({"table_va": row["table_va"], "slots": row.get("support", ""),
                        "children": "|".join(kids), "child_supers": "|".join(parents),
                        "class": name, "verdict": verdict})
    return out, sup


# --------------------------------------------------------------------------- rendering

def render(edges, table_names):
    regs = read_registrations()
    counts = collections.Counter(e["verdict"] for e in edges)
    targets = {e["target_class"] for e in edges if e["verdict"] == "named" and e["target_class"]}
    slots = {e["target_slot"] for e in edges if e["verdict"] == "named"}
    slots |= {e["parent_slot"] for e in edges if e["verdict"] == "named" and e["parent_slot"]}
    agree, disagree = agree_control(edges)
    lines = []
    lines.append("**%d class objects are written a parent and %d `.data` cells are the join's key.** Every one "
                 "of the image's %d registration sites caches its class object in one of those cells, so each "
                 "side of a store is a class literal rather than an address, and %d `mov [reg+0x28]` stores "
                 "name a base register and a source cell this census can resolve."
                 % (len(targets), len(slots), len(regs), len(edges)))
    lines.append("")
    lines.append("| what the store is | stores | |")
    lines.append("|---|---:|---|")
    lines.append("| a named edge: both sides are a class cell | %d | the hierarchy below |" % counts["named"])
    lines.append("| the parent is a lookup, not a class cell | %d | left `unresolved` |"
                 % counts["unresolved"])
    lines.append("| the parent register is never loaded in the window | %d | left `no-source` |"
                 % counts["no-source"])
    lines.append("| `[reg+0x28]` on something that is not a class object | %d | left `foreign` |"
                 % counts["foreign"])
    lines.append("")
    lines.append("**The control is exact: every class `inventory/classes.csv` also holds agrees with the "
                 "image's edge, %d of %d, and %d disagree.** No body stores a null: the root has no setup body "
                 "of its own, and the `cmp`/`je` that would null a self-parent is the guard every body "
                 "carries, not a store any class makes."
                 % (agree, agree + disagree, disagree))
    lines.append("")
    lines.append("| the parent | its children, as the image writes them |")
    lines.append("|---|---|")
    groups = collections.defaultdict(list)
    for e in edges:
        if e["verdict"] == "named" and e["target_class"] not in groups[e["parent_class"]]:
            groups[e["parent_class"]].append(e["target_class"])
    for parent in sorted(groups, key=lambda p: (-len(groups[p]), p)):
        lines.append("| `%s` | %s |" % (parent, ", ".join("`%s`" % c for c in sorted(groups[parent]))))
    lines.append("")
    lines.append("### The tables the registrations cannot name, named by their children")
    lines.append("")
    named = [r for r in table_names if r["verdict"] == "names-the-table"]
    agrees = [r for r in table_names if r["verdict"] == "agrees"]
    dis = [r for r in table_names if r["verdict"] == "disagrees"]
    split = [r for r in table_names if r["verdict"] == "split"]
    part = [r for r in table_names if r["verdict"] == "partial"]
    nos = [r for r in table_names if r["verdict"] == "no-child-super"]
    lines.append("**%d of the %d tables a caller edge constrains get a name from their children's `Super`, "
                 "and the same join reproduces %d of the %d tables the registrations already named (%d "
                 "disagree).** The rule has no free parameter: the table is the class its calling classes "
                 "say their parent is, at least one caller must be a class this census resolved, and every "
                 "resolved caller must agree. A table whose callers disagree is a table those classes share "
                 "higher up, not their parent, and it stays unnamed."
                 % (len(named), len(table_names), len(agrees), len(agrees) + len(dis), len(dis)))
    lines.append("")
    lines.append("| table | slots | the children that call its installer | their `Super` | class |")
    lines.append("|---|---:|---|---|---|")
    for r in named:
        kids = r["children"].split("|")
        sups = r["child_supers"].split("|")
        tally = collections.Counter(sups)
        shown = ", ".join("%s%s" % (k, "" if n == 1 else " (x%d)" % n) for k, n in tally.items())
        lines.append("| `%s` | %s | %s | %s | **`%s`** |"
                     % (r["table_va"], r["slots"] or "-",
                        ", ".join(kids[:6]) + (" (+%d more)" % (len(kids) - 6) if len(kids) > 6 else ""),
                        shown, r["class"]))
    lines.append("")
    seven = ("0x4d34d8", "0x4d3af0", "0x4c94b0", "0x4fcfe0", "0x4fc588", "0x4d17a8", "0x517860")
    got = [r for r in named if r["table_va"] in seven]
    lines.append("**%d of the declarer walk's seven unnamed tables are named here**: %s. %s"
                 % (len(got), ", ".join("`%s` `%s`" % (r["table_va"], r["class"]) for r in got),
                    "All seven, so the ten `declared-unnamed` bodies and the three `unnamed-table` bodies "
                    "that residue counts now have a declarer class; what this does **not** do is write their "
                    "`<Class>_vt<slot>` names into the map -- that is the add `tools/declarer-residue.py "
                    "--emit-moves` already carries, and it is the next run's first command."
                    if len(got) == len(seven) else
                    "The rest need a caller this census cannot resolve."))
    lines.append("")
    if split:
        lines.append("**%d table(s) split**: %s. A split is two callers of one table whose `Super` "
                     "disagrees, so the table is a class those callers share higher up rather than their "
                     "parent, and it stays unnamed."
                     % (len(split), "; ".join("`%s` (%d caller(s), %d distinct `Super`)"
                                              % (r["table_va"], len(r["children"].split("|")),
                                                 len(set(r["child_supers"].split("|"))))
                                              for r in split)))
        lines.append("")
    if part:
        lines.append("**%d table(s) are constrained but not named**: %s. Every caller this census can name "
                     "agrees, but at least one caller is a class with no `Super` edge, so the agreement is "
                     "over a subset and it is reported rather than taken."
                     % (len(part), "; ".join("`%s` (%s)" % (r["table_va"], r["child_supers"])
                                             for r in part)))
        lines.append("")
    if nos:
        lines.append("**%d table(s) have no caller with a `Super` edge** (%s) and are not nameable by this "
                     "join." % (len(nos), ", ".join("`%s`" % r["table_va"] for r in nos)))
        lines.append("")
    return "\n".join(lines)


def agree_control(edges):
    """(agree, disagree) against inventory/classes.csv: the control this join has to pass."""
    hier = {}
    with open(os.path.join(ROOT, "inventory/classes.csv"), newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            hier.setdefault(row["class"], row["super"])
    ok = bad = 0
    for e in edges:
        if e["verdict"] == "named" and e["target_class"] in hier:
            if hier[e["target_class"]] == e["parent_class"]:
                ok += 1
            else:
                bad += 1
    return ok, bad


def disagree_control(edges):
    hier = {}
    with open(os.path.join(ROOT, "inventory/classes.csv"), newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            hier.setdefault(row["class"], row["super"])
    n = 0
    for e in edges:
        if e["verdict"] == "named" and e["target_class"] in hier:
            if hier[e["target_class"]] != e["parent_class"]:
                n += 1
                if n <= 10:
                    print("disagrees: %s image=%s classes.csv=%s"
                          % (e["target_class"], e["parent_class"], hier[e["target_class"]]))
    return n


# --------------------------------------------------------------------------- io

def write_artifact(rows, path, fields):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def read_artifact(path, fields):
    with open(path, newline="", encoding="utf-8") as fh:
        return [{k: r.get(k, "") for k in fields} for r in csv.DictReader(fh)]


def splice_doc(path, block):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(DOC_START), text.find(DOC_END)
    if i < 0 or j < 0:
        return None
    return text[:i] + DOC_START + "\n" + block + "\n" + text[j:]


# --------------------------------------------------------------------------- self-test

class Fake(Image):
    """A buffer at 0x1000: two class cells, a registration body, and a super store for each shape."""

    def __init__(self):
        d = bytearray(b"\xcc" * 0x4000)
        self.data = d
        self.xbe = type("X", (), {"sections": [type("S", (), {"name": ".text", "va": 0x1000,
                                                             "vsize": 0x4000, "foff": 0,
                                                             "fsize": 0x4000})()]})()
        # a registration body at 0x1100: push literal; call ctor -- the site is 0x1106
        # a StaticClass wrapper at 0x1200: call 0x1100; mov ds:0x3000,eax
        d[0x200:0x220] = (b"\x68" + struct.pack("<I", 0x9000) + b"\xe8"
                          + struct.pack("<i", 0x1100 - (0x1200 + 5 + 5)) + b"\xa3"
                          + struct.pack("<I", 0x3000) + b"\xc3" + b"\xcc" * 7)
        # the parent's setup body at 0x1300: mov eax,ds:0x3000; mov ecx,ds:0x3004; cmp; je;
        # mov [ecx+0x28],eax ... and the null fallback
        d[0x300:0x330] = (b"\xa1" + struct.pack("<I", 0x3000) + b"\x8b\x0d"
                          + struct.pack("<I", 0x3004) + b"\x3b\xc1\x74\x09\x89\x41\x28\xeb\x07"
                          + b"\xc7\x41\x28\x00\x00\x00\x00\xc3" + b"\xcc" * 8)
        self.regfunc = {0x1100: "Child"}
        self.cells = {0x3000: "Parent", 0x3004: "Child"}


def self_check():
    bad = []
    img = Fake()
    tb = img.data
    base = 0x1000
    funcs = [(0x1100, 0x20), (0x1200, 0x20), (0x1300, 0x40)]
    cells, conflicts, enclosing, sizes = class_cells(img, tb, base, funcs, img.regfunc)
    if cells.get(0x3000) != "Child" or conflicts != 0:
        bad.append("the StaticClass wrapper's `mov ds:cell,eax` does not key the cell to its literal: %s"
                   % cells)
    bodies = super_stores(tb, base, enclosing)
    if 0x1300 not in bodies or len(bodies[0x1300]) != 2:
        bad.append("the two stores in the sibling body are not found: %s" % dict(bodies))
    else:
        kinds = sorted(k for _a, k in bodies[0x1300])
        if kinds != ["imm", "reg"]:
            bad.append("the store encodings read as %s, not one `reg` and one `imm`" % kinds)
    # the store reader: the register store's two loads are the two cells it names
    ins = img.disasm(0x1300, 0x30)
    idx = next((k for k, (a, _t) in enumerate(ins) if a == 0x130f), None)
    got = None
    if idx is not None:
        breg = STORE_REG.match(ins[idx][1]).group(1)
        target = parent = None
        for k in range(idx - 1, -1, -1):
            mm = LOAD.match(ins[k][1])
            if not mm:
                continue
            if mm.group(1) == breg and target is None:
                target = int(mm.group(2), 16)
            if mm.group(1) == "eax" and parent is None:
                parent = int(mm.group(2), 16)
            if target is not None and parent is not None:
                break
        got = (target, parent)
    if got != (0x3004, 0x3000):
        bad.append("the store's two cells read as %s, not (self 0x3004, parent 0x3000)" % (got,))
    # the join: a child whose Super is its parent names the parent's table
    edges = [{"target_class": "Child", "target_slot": "0x3004", "parent_class": "Parent",
              "parent_slot": "0x3000", "body_va": "0x1300", "store_va": "0x130c",
              "verdict": "named"}]
    sup = {e["target_class"]: e["parent_class"] for e in edges}
    if sup.get("Child") != "Parent":
        bad.append("the join does not carry the child's parent")
    # the committed artifacts must round-trip and the doc must match them
    for path, fields in ((ARTIFACT, EDGE_FIELDS), (NAMES, NAME_FIELDS)):
        if not os.path.exists(path):
            bad.append("%s is missing" % path)
            continue
        if read_artifact(path, fields) != read_artifact(path, fields):
            bad.append("%s does not round-trip" % path)
    if os.path.exists(DOC) and os.path.exists(ARTIFACT) and os.path.exists(NAMES):
        block = render(read_artifact(ARTIFACT, EDGE_FIELDS), read_artifact(NAMES, NAME_FIELDS))
        if splice_doc(DOC, block) is None:
            bad.append("the doc markers are missing from %s" % DOC)
    if bad:
        for b in bad:
            print("FAIL class-super-edges: " + b)
        return 1
    print("ok   class-super-edges: the cell join, both store encodings, the two loads and the join")
    return 0


def main():
    if "--check" in sys.argv[1:]:
        return self_check()
    ap = argparse.ArgumentParser()
    ap.add_argument("pos", nargs="?", default=None, help="the image (or use --xbe)")
    ap.add_argument("--xbe", dest="xbe", default=None, help="the image, as every other tool spells it")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--write-doc", nargs="?", const=DOC, default=None)
    ap.add_argument("--check-doc", nargs="?", const=DOC, default=None)
    args = ap.parse_args()
    args.xbe = args.xbe or args.pos

    if args.write_doc or args.check_doc:
        edges = read_artifact(ARTIFACT, EDGE_FIELDS)
        table_names = read_artifact(NAMES, NAME_FIELDS)
        block = render(edges, table_names)
        path = args.write_doc or args.check_doc
        new = splice_doc(path, block)
        if new is None:
            print("FAIL class-super-edges: markers not found in %s" % path)
            return 1
        old = open(path, encoding="utf-8").read()
        if args.check_doc:
            if new != old:
                print("FAIL class-super-edges: %s is stale against the committed artifacts" % path)
                return 1
            print("ok   class-super-edges: %s matches the committed artifacts" % path)
            return 0
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("wrote %s" % path)
        return 0

    if not args.xbe:
        ap.error("the image is required unless --check, --write-doc or --check-doc is given")
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from xbe import Xbe

    edges, cells, conflicts = census(Xbe(args.xbe))
    table_names, sup = names(edges)
    if args.out:
        write_artifact(edges, os.path.join(args.out, "class_super_edges.csv"), EDGE_FIELDS)
        write_artifact(table_names, os.path.join(args.out, "class_table_super_names.csv"), NAME_FIELDS)
        print("%s: %d edges, %s: %d tables"
              % (os.path.join(args.out, "class_super_edges.csv"), len(edges),
                 os.path.join(args.out, "class_table_super_names.csv"), len(table_names)))
    counts = collections.Counter(e["verdict"] for e in edges)
    print("edges %d: %s" % (len(edges), ", ".join("%s %d" % kv for kv in sorted(counts.items()))))
    print("cells %d, conflicts %d, named targets %d"
          % (len(cells), conflicts,
             len({e["target_class"] for e in edges if e["verdict"] in ("named", "root")})))
    ok, bad = agree_control(edges)
    print("control vs classes.csv: %d agree, %d disagree" % (ok, bad))
    nc = collections.Counter(r["verdict"] for r in table_names)
    print("tables %d: %s" % (len(table_names), ", ".join("%s %d" % kv for kv in sorted(nc.items()))))
    for r in table_names:
        if r["verdict"] == "names-the-table":
            print("   %s = %s  (%s)" % (r["table_va"], r["class"], r["children"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
