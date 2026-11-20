#!/usr/bin/env python3
"""What a nameless class table's constructor carries: its literals, its callees, its parent.

`tools/class-table-installer-shape.py` settled *which* bodies install the 250 tables no
registration names (97 constructor-shaped, 29 destructor-shaped, 56 both, 26 neither) and left
the naming question to "the body, not the class map". This tool reads those bodies once and
records the three things a body can still say about its class:

  * the **literals** it pushes -- a `push imm32` whose immediate is a UTF-16 string in `.rdata` is
    a field default or a config section (`Z:\\Cache`, `ini:Engine.Engine.Canvas`), and the config
    form names a class in its own syntax;
  * the **named callees** -- the map's own `Core_*`/`Engine_*` rows, which place the table in a
    subsystem even when no name reaches the class, and whose combination is a signature that
    tables can share;
  * the **parent** -- the first call a self-installing constructor makes that lands in a body
    storing another table, which is the base constructor, followed through the null-check thunk.

The control is the 474 tables the registration path already names. It settles what a constructor
literal means: on those tables no body pushes its own class's name, and the two `ini:` literals
name the class that *declares* the config property (`DemoPlayPendingLevel` pushes
`ini:Engine.Engine.DemoRecordingDevice`), not the object's class. It also settles the parent rule:
where the named table's class has a script `super`, the edge this tool reads is that ancestor, with
no contradiction.

    tools/class-ctor-evidence.py --xbe DUMP/default.xbe -o inventory
    tools/class-ctor-evidence.py --report
    tools/class-ctor-evidence.py --write-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-ctor-evidence.py --check-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-ctor-evidence.py --check          # synthetic bodies, no dump, writes nothing

The dump is never committed; the artifact and the page block are. Addresses, class names, counts
and the decoded literals only -- no package or script content.
"""
import argparse
import bisect
import collections
import csv
import hashlib
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

OUT = os.path.join("inventory", "class_ctor_evidence.csv")
REACH = os.path.join(ROOT, "inventory", "native_vtable_reach.csv")
SHAPES = os.path.join(ROOT, "inventory", "class_table_installer_shapes.csv")
NAMES = os.path.join(ROOT, "inventory", "xbe_class_table_names.csv")
CLASSES = os.path.join(ROOT, "inventory", "classes.csv")
MAP = os.path.join(ROOT, "symbols", "xbox_functions.csv")
PAGE = os.path.join("docs", "systems", "CLASS_REGISTRATION.md")
DOC_START = "<!-- generated:class-ctor-evidence -->"
DOC_END = "<!-- /generated:class-ctor-evidence -->"
FIELDS = ("table_va", "slots", "population", "class", "script_super", "installers", "literals",
          "literal_count", "ini_sections", "ini_classes", "class_literals", "own_class_literal",
          "named_calls", "named_call_count", "signature", "family_size", "parent_va",
          "parent_class", "parent_route", "parent_vs_script", "chain_root", "chain_depth",
          "verdict", "run", "evidence")
POPS = ("named", "at-least", "plain-only", "no-store")
MAX_LIT = 4
MAX_CALL = 8
LIT_CHARS = 64
BODY_CAP = 700
THUNK_HOPS = 3


# --------------------------------------------------------------------------- inputs
def read(path):
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def body_index(path):
    """[(start, end, name)]; the map's size column is a byte count in DECIMAL."""
    out = []
    for r in read(path):
        try:
            a = int(r["address"], 16)
            s = int(r["size"] or 0)
        except (KeyError, ValueError):
            continue
        out.append((a, a + s if s else a, r.get("name", "")))
    out.sort()
    return out


def owner(index, starts, va):
    """The body holding `va`, or None -- an address past a row's end is nobody's body."""
    i = bisect.bisect_right(starts, va) - 1
    if i < 0:
        return None
    a, end, name = index[i]
    if end and va >= end:
        return None
    return a, end, name


# --------------------------------------------------------------------------- reading a body
def utf16(image, va, cap=LIT_CHARS):
    """The UTF-16LE printable string at `va`, or "" -- this image's name literals are exactly that."""
    try:
        raw = image.read_va(va, cap * 2)
    except Exception:
        return ""
    out = []
    for i in range(0, len(raw) - 1, 2):
        c = struct.unpack_from("<H", raw, i)[0]
        if c == 0:
            break
        if c < 0x20 or c > 0x7E:
            return ""
        out.append(chr(c))
    return "".join(out) if len(out) >= 2 else ""


def literals(image, body, va, in_text):
    """Every `push imm32` whose immediate is a printable UTF-16 string outside `.text`.

    A push's immediate is not 4-aligned (the opcode is one byte), and the text scan is linear, so
    a byte inside another instruction's immediate can also look like a push. The printable-decode
    test is what keeps that out: a code address, an object size or a small integer does not decode
    to a run of printable UTF-16 ending in a terminator.
    """
    out = []
    for i in range(len(body) - 4):
        if body[i] != 0x68:                                   # PUSH imm32
            continue
        imm = struct.unpack_from("<I", body, i + 1)[0]
        if in_text(imm):
            continue
        s = utf16(image, imm)
        if s and s not in out:
            out.append(s)
    return out


def calls(body, va, index, starts):
    """(distinct named targets, self-install parent candidate) for the `E8` calls in one body."""
    named, targets = set(), []
    for i in range(len(body) - 5):
        if body[i] != 0xE8:
            continue
        tgt = (va + i + 5 + struct.unpack_from("<i", body, i + 1)[0]) & 0xFFFFFFFF
        targets.append((va + i, tgt))
        hit = owner(index, starts, tgt)
        if hit and hit[2] and not hit[2].startswith("FUN_"):
            named.add(hit[2])
    return named, targets


def follow(image, index, starts, va, hops=THUNK_HOPS):
    """A `mov ecx,[esp+4]; test; je; jmp` null-check thunk stands between a call and the target."""
    seen = set()
    while hops and va not in seen:
        seen.add(va)
        try:
            text = image.read_va(va, 32)
        except Exception:
            return va
        if not text or any(b == 0xE8 for b in text):           # a body that calls is not a thunk
            return va
        jump = None
        for i in range(min(len(text), 40) - 4):
            if text[i] == 0xE9:
                jump = va + i + 5 + struct.unpack_from("<i", text, i + 1)[0]
                break
        if jump is None:
            return va
        va, hops = jump, hops - 1
    return va


def stores(text, va, tables):
    """Every `mov dword ptr [reg(+disp)], <table>`; an absolute store is a global initialiser."""
    out, i = [], 0
    while i < len(text) - 6:                                  # the shortest form is seven bytes
        if text[i] != 0xC7:                                   # C7 /0 = mov r/m32, imm32
            i += 1
            continue
        modrm = text[i + 1]
        mod, reg, rm = modrm >> 6, (modrm >> 3) & 7, modrm & 7
        if reg != 0 or rm == 4:                               # /0 only, no SIB byte
            i += 1
            continue
        if mod in (0, 1, 2) and not (mod == 0 and rm == 5):
            ln = 6 + (0 if mod == 0 else 1 if mod == 1 else 3)
            if i + ln > len(text):
                i += 1
                continue
            imm = struct.unpack_from("<I", text, i + ln - 4)[0]
            if imm in tables:
                out.append((va + i, imm))
            i += ln
            continue
        i += 1
    return out


def parent_edge(image, index, starts, tables, body_tables, tva, bodies):
    """(installer, call site, real target, parent table) from a body that installs one table only.

    A body installing two tables is a constructor with an inlined base, so which of its stores is
    its own is the separate last-store question; only a self-install body says "this table is
    mine" without a further read.
    """
    for b in bodies:
        if len(body_tables.get(b, ())) != 1:
            continue
        a, end, _ = owner(index, starts, b)
        try:
            body = image.read_va(a, min((end - a) if end > a else BODY_CAP, BODY_CAP))
        except Exception:
            continue
        _, targets = calls(body, a, index, starts)
        for site, tgt in targets:
            real = follow(image, index, starts, tgt)
            hit = owner(index, starts, real)
            if not hit:
                continue
            try:
                text = image.read_va(hit[0], min((hit[1] - hit[0]) if hit[1] > hit[0] else BODY_CAP,
                                                BODY_CAP))
            except Exception:
                continue
            foreign = [h for h in stores(text, hit[0], tables) if h[1] != tva]
            if foreign:
                return b, site, real, foreign[0][1]
    return None


def ini_class(section):
    """`ini:Engine.Engine.Canvas` -> `Engine` (the class field, not the package and not the property)."""
    parts = section.split(".")
    return parts[1] if len(parts) > 2 and section.startswith("ini:") else ""


# --------------------------------------------------------------------------- the census
def dword_run(image, va, limit, cap=256):
    """The run of address-like dwords `va` sits in, as (start, end) -- `end` exclusive."""
    def addr(v):
        return 0x10000 <= v < limit
    a, b = va, va + 4
    for _ in range(cap):
        try:
            if not addr(struct.unpack("<I", image.read_va(a - 4, 4))[0]):
                break
        except Exception:
            break
        a -= 4
    for _ in range(cap):
        try:
            if not addr(struct.unpack("<I", image.read_va(b, 4))[0]):
                break
        except Exception:
            break
        b += 4
    return a, b


def harvest(image, index, reach, shapes, names, classes, text_range):
    starts = [a for a, _, _ in index]
    tables = {}
    for r in reach:
        try:
            tables[int(r["table_va"], 16)] = int(r.get("table_slots") or 0)
        except ValueError:
            continue
    pop = {}
    for r in shapes:
        try:
            pop[int(r["table_va"], 16)] = r["population"]
        except (KeyError, ValueError):
            continue
    cls = {}
    for r in names:
        if (r.get("class") or "").strip():
            try:
                cls[int(r["table_va"], 16)] = r["class"].strip()
            except ValueError:
                continue
    supers = {r["class"]: (r.get("super") or "").strip() for r in classes if r.get("class")}
    class_names = set(supers)
    body_tables = collections.defaultdict(set)
    sites_of = {}
    for r in reach:
        try:
            tva = int(r["table_va"], 16)
        except ValueError:
            continue
        sites = [int(s, 16) for s in (r.get("install_sites") or "").split() if s]
        sites_of[tva] = sites
        for s in sites:
            hit = owner(index, starts, s)
            if hit:
                body_tables[hit[0]].add(tva)

    lo, hi = text_range

    def in_text(va):
        return lo <= va < hi                               # `tools/xbe.py`'s `.text` bounds

    # the body's own table is its LAST store: a constructor installs the base's table before its
    # own, so a descendant's body that stores this table first is not this table's installer
    own_installers = collections.defaultdict(set)
    for b, ts in body_tables.items():
        a, end, _ = owner(index, starts, b)
        try:
            text = image.read_va(a, min((end - a) if end > a else BODY_CAP, BODY_CAP))
        except Exception:
            continue
        hits = stores(text, a, tables)
        if hits:
            own_installers[hits[-1][1]].add(b)

    rows = []
    for tva in sorted(tables):
        bodies = sorted({hit[0] for hit in (owner(index, starts, s)
                                           for s in sites_of.get(tva, ())) if hit})
        lits, named = [], set()
        for b in bodies:
            a, end, _ = owner(index, starts, b)
            try:
                body = image.read_va(a, min((end - a) if end > a else BODY_CAP, BODY_CAP))
            except Exception:
                continue
            for s in literals(image, body, a, in_text):
                if s not in lits:
                    lits.append(s)
            named |= calls(body, a, index, starts)[0]
        ini = [s for s in lits if s.startswith("ini:") and ini_class(s)]
        class_hits = [s for s in lits if s in class_names]
        own = [s for s in lits if cls.get(tva) and s == cls[tva]]
        edge = parent_edge(image, index, starts, tables, body_tables, tva,
                           sorted(own_installers.get(tva, ())))
        run = ""
        if not bodies:
            start, end = dword_run(image, tva, image.image_size)
            slot_vals = []
            try:
                for k in range(min(tables[tva], 64)):
                    slot_vals.append(struct.unpack("<I", image.read_va(tva + 4 * k, 4))[0])
            except Exception:
                slot_vals = []
            odd = sum(1 for v in slot_vals if v % 4)
            tiles = sorted(t for t in tables if start <= t < end)
            installed = sum(1 for t in tiles if any(owner(index, starts, s) for s in sites_of.get(t, ())))
            site = (sites_of.get(tva) or [None])[0]
            run = ("dword run 0x%08x+%d; this address at offset %d; %d listed table(s) in the run, "
                   "%d with an installer body; the recorded store site %s lies in no body of the map%s"
                   % (start, (end - start) // 4, (tva - start) // 4, len(tiles), installed,
                      ("0x%08x" % site) if site else "none",
                      "; %d of %d slots are not 4-aligned, so they are in-function offsets"
                      % (odd, len(slot_vals)) if odd else ""))
        rows.append({
            "run": run,
            "table_va": hex(tva), "slots": tables[tva],
            "population": pop.get(tva, "named" if tva in cls else ""),
            "class": cls.get(tva, ""), "script_super": supers.get(cls.get(tva, ""), ""),
            "installers": len(bodies),
            "literals": "|".join(lits[:MAX_LIT]), "literal_count": len(lits),
            "ini_sections": "|".join(ini[:MAX_LIT]),
            "ini_classes": "|".join(sorted({ini_class(s) for s in ini})),
            "class_literals": len(class_hits),
            "own_class_literal": "yes" if own else "no",
            "named_calls": "|".join(sorted(named)[:MAX_CALL]), "named_call_count": len(named),
            "signature": hashlib.sha1("|".join(sorted(named)).encode()).hexdigest()[:8] if named else "",
            "family_size": 0,
            "parent_va": hex(edge[3]) if edge else "",
            "parent_class": cls.get(edge[3], "") if edge else "",
            "parent_route": ("named" if edge and edge[3] in cls
                             else "unnamed" if edge else "none"),
            "parent_vs_script": "", "chain_root": "", "chain_depth": 0,
            "verdict": "", "evidence": "",
        })
    # the signature families, over the whole population
    fam = collections.Counter(r["signature"] for r in rows if r["signature"])
    for r in rows:
        r["family_size"] = fam[r["signature"]] if r["signature"] else 0
    # the chain a nameless table climbs: parent by parent until a named class or no edge
    by_va = {int(r["table_va"], 16): r for r in rows}
    for r in rows:
        t, depth, seen = int(r["table_va"], 16), 0, {int(r["table_va"], 16)}
        while True:
            p = by_va.get(t, {}).get("parent_va")
            p = int(p, 16) if p else None
            if p is None or p in seen or depth >= 12:
                break
            seen.add(p)
            depth += 1
            if by_va.get(p, {}).get("class"):
                r["chain_root"], r["chain_depth"] = by_va[p]["class"], depth
                break
            t = p
            if by_va.get(p, {}).get("parent_route") in ("", "none"):
                break
    for r in rows:
        r["parent_vs_script"] = vs_script(r, supers)
        r["verdict"], r["evidence"] = judge(r)
    return rows, supers


def vs_script(r, supers):
    """How the edge sits against the script tree: the parent, an ancestor of it, or neither.

    A script-only class whose nearest native base is above its script parent installs its table
    from a body that calls that native ancestor's constructor, so the edge is an ancestor and not
    the immediate `super`; the walk up `super` is what tells the two apart.
    """
    if r["parent_route"] == "none" or r["population"] != "named":
        return ""
    parent = r["parent_class"]
    if not parent:
        return "parent-unnamed"
    seen = set()
    walk = r["class"]
    while walk and walk not in seen:
        seen.add(walk)
        walk = supers.get(walk, "")
        if not walk:
            return "no-script-chain"
        if walk == parent:
            return "agree" if walk == r["script_super"] else "ancestor"
    return "differ"


def judge(r):
    """One label per table: the strongest channel that names or places it."""
    bits = []
    if r["installers"] == 0:
        if r.get("run"):
            return ("window" if "at offset 0;" not in r["run"] else "run-start"), r["run"]
        return "no-body", "no body stores this table"
    if r["own_class_literal"] == "yes":
        return "names-its-class", "a literal the body pushes is the class's own name"
    if r["ini_sections"]:
        bits.append("an `ini:` literal names the declaring class (%s)" % r["ini_classes"])
    if r["class_literals"]:
        bits.append("%d literal(s) are class names" % r["class_literals"])
    if r["literals"]:
        bits.append("%d field literal(s)" % r["literal_count"])
    if r["named_calls"]:
        bits.append("%d named callee(s)" % r["named_call_count"])
    if r["parent_route"] != "none":
        bits.append("%s parent" % r["parent_route"])
    if r["chain_root"]:
        bits.append("chains to %s at depth %d" % (r["chain_root"], r["chain_depth"]))
    if r["ini_sections"]:
        verdict = "config-class"
    elif r["class_literals"]:
        verdict = "class-literal"
    elif r["named_calls"]:
        verdict = "subsystem"
    elif r["literals"]:
        verdict = "field-literal"
    else:
        verdict = "nothing"
    return verdict, "; ".join(bits) or "no literal and no named callee"


# --------------------------------------------------------------------------- the doc block
NUMERIC = ("slots", "installers", "literal_count", "class_literals", "named_call_count",
           "family_size", "chain_depth")


def numeric(rows):
    """The CSV round trip returns strings; the renderer arithmetic needs the numbers back."""
    return [dict(r, **{k: int(r[k] or 0) for k in NUMERIC if k in r}) for r in rows]


def counts(rows):
    rows = numeric(rows)
    by_pop = collections.defaultdict(collections.Counter)
    for r in rows:
        d = by_pop[r["population"]]
        d["tables"] += 1
        d["installers"] += int(r["installers"] or 0)
        for key, col in (("literal", "literal_count"), ("ini", "ini_classes"),
                         ("classlit", "class_literals"), ("callee", "named_call_count")):
            d[key] += int(bool(r[col]))
        d["own"] += int(r["own_class_literal"] == "yes")
        d["edge"] += int(r["parent_route"] != "none")
        d["edge_named"] += int(r["parent_route"] == "named")
        d["root"] += int(bool(r["chain_root"]))
    return by_pop


def render(rows):
    rows = numeric(rows)
    c = counts(rows)
    named, plain, least = c["named"], c["plain-only"], c["at-least"]
    nameless = [r for r in rows if r["population"] in ("plain-only", "at-least")]
    out = ["| population | tables | installer bodies | a printable literal | an `ini:` literal "
           "| a literal that is a class name | a literal that is its own class | a named callee "
           "| a base-caller edge |",
           "|---|---|---|---|---|---|---|---|---|"]
    for pop in POPS:
        d = c[pop]
        if not d["tables"]:
            continue
        out.append("| %s | %d | %d | %d | %d | %d | %d | %d | %d |"
                   % (pop, d["tables"], d["installers"], d["literal"], d["ini"], d["classlit"],
                      d["own"], d["callee"], d["edge"]))
    out.append("")
    tally = collections.Counter(r["parent_vs_script"] for r in rows if r["parent_vs_script"])
    out.append("**The control answers what a constructor literal is, and it is not a name.** %d of "
               "the %d installer bodies behind the %d named tables push a printable UTF-16 literal; "
               "%d of those tables push a literal that is a class name, and **none of the %d pushes "
               "its own class**. The %d `ini:` literals name the class that *declares* the config "
               "property -- `DemoPlayPendingLevel` pushes `ini:Engine.Engine.DemoRecordingDevice` "
               "and `NetPendingLevel` `ini:Engine.Engine.NetworkDevice` -- so a literal is a field "
               "default or a config section, and the class it can name is the declaring one."
               % (named["literal"], named["installers"], named["tables"], named["classlit"],
                  named["tables"], named["ini"]))
    out.append("")
    ini_rows = [r for r in nameless if r["ini_sections"]]
    top = collections.Counter()
    for r in nameless:
        for lit in (r["literals"] or "").split("|"):
            if lit and not lit.startswith("ini:"):
                top[lit] += 1
    out.append("**Of the %d nameless tables -- %d plain-only and %d whose table a constructor "
               "bounds -- %d push a printable literal and %d call a named function.** %d push an "
               "`ini:` section, and that form does name a class: %s. The other %d are field "
               "defaults and data addresses -- the commonest are %s -- and %d of the %d push a "
               "class name (a package, or a class they register), never their own. So the literal "
               "channel places %d of the %d by config class and describes the fields of the rest, "
               "and names none of them."
               % (len(nameless), plain["tables"], least["tables"],
                  plain["literal"] + least["literal"], plain["callee"] + least["callee"],
                  len(ini_rows),
                  "; ".join("`%s` (`%s`)" % (r["table_va"],
                                             (r["ini_sections"] or "").replace("|", "`, `"))
                            for r in ini_rows) or "none",
                  plain["literal"] + least["literal"] - len(ini_rows),
                  ", ".join("`%s` (%d)" % (k, v) for k, v in top.most_common(6)) or "none",
                  sum(1 for r in nameless if int(r["class_literals"] or 0)), len(nameless),
                  len(ini_rows), len(nameless)))
    out.append("")
    fams = collections.defaultdict(list)
    for r in nameless:
        if r["signature"] and int(r["family_size"] or 0) > 1:
            fams[r["signature"]].append(r)
    big = sorted(fams.values(), key=lambda g: (-len(g), g[0]["table_va"]))
    covered = sum(len(g) for g in big)
    out.append("**The named callees place a table even when nothing names it.** %d of the %d "
               "nameless tables call at least one of the map's own `Core_*`/`Engine_*` rows; %d "
               "signatures are shared by two or more of them and cover %d tables, the largest being "
               "%d tables that call the same set. A signature is not a name -- two classes in one "
               "subsystem share it -- which is why it is reported as a family and not as a label."
               % (plain["callee"] + least["callee"], len(nameless), len(big), covered,
                  len(big[0]) if big else 0))
    out.append("")
    out.append("**The base-caller edge is the parent's constructor, and the named population holds it "
               "to the script tree.** A table's own installer is the body whose *last* store is that "
               "table, and its first call that reaches another table's installer is its parent "
               "constructor, followed through the null-check thunk. On the named tables the edge is "
               "the class's script `super` on **%d**, a further ancestor on **%d** (a script-only "
               "class installs its table from a body that calls its nearest native base), and on **%d** "
               "the class has no script chain; **%d contradict**. Of the %d nameless tables %d get an "
               "edge -- %d to a named class, %d to another nameless table -- and %d climb a chain to "
               "a named class, the deepest %d levels."
               % (tally["agree"], tally["ancestor"], tally["no-script-chain"], tally["differ"],
                  len(nameless), plain["edge"] + least["edge"],
                  plain["edge_named"] + least["edge_named"],
                  plain["edge"] + least["edge"] - plain["edge_named"] - least["edge_named"],
                  plain["root"] + least["root"],
                  max([r["chain_depth"] for r in rows] or [0])))
    out.append("")
    nostore = [r for r in rows if r["population"] == "no-store"]
    out.append("**The %d tables with no installer body are not tables: each is a window into a "
               "dword run, or a run the census cut short.** The store site each records lies in no "
               "body of the map, and the run of address-like dwords around it is tiled by tables the "
               "census already lists: %s."
               % (len(nostore), "; ".join("`%s` (%s slots) %s" % (r["table_va"], r["slots"],
                                                                  r["evidence"])
                                          for r in nostore)))
    return out


def write_doc(path, rows, check=False):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(DOC_START), text.find(DOC_END)
    if i < 0 or j < 0:
        raise SystemExit("%s: no %s block" % (path, DOC_START))
    new = text[:i + len(DOC_START)] + "\n" + "\n".join(render(rows)) + "\n" + text[j:]
    if check:
        if new != text:
            raise SystemExit("%s: the class-ctor-evidence block is stale" % path)
        print("ok   %s: class-ctor-evidence block reproduces" % path)
        return
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(new)
    print("wrote %s" % path)


def write_csv(path, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


# --------------------------------------------------------------------------- the self-test
class Fake:
    """A synthetic image: `va -> bytes`, and nothing outside it."""

    def __init__(self, blobs):
        self.blobs = blobs

    def read_va(self, va, n):
        for start, blob in self.blobs.items():
            if start <= va < start + len(blob):
                off = va - start
                return blob[off:off + n]
        raise KeyError(hex(va))


def self_check():
    fails = []

    def want(label, cond):
        if not cond:
            fails.append(label)

    wide = "Z:\\Cache".encode("utf-16-le") + b"\x00\x00"
    ini = "ini:Engine.Engine.Canvas".encode("utf-16-le") + b"\x00\x00"
    asc = b"Cache\x00"
    # 0x90000: a data blob holding the two literals; 0x10000: code
    data = bytearray(b"\x00" * 0x200)
    data[0:len(wide)] = wide
    data[0x80:0x80 + len(ini)] = ini
    data[0x100:0x100 + len(asc)] = asc
    image = Fake({0x90000: bytes(data)})
    body = (b"\x68" + struct.pack("<I", 0x90000) +            # push Z:\Cache
            b"\x68" + struct.pack("<I", 0x90080) +            # push ini:Engine.Engine.Canvas
            b"\x68" + struct.pack("<I", 0x90100) +            # push the ASCII blob
            b"\x68" + struct.pack("<I", 0x00000003) +         # push 3: no string
            b"\xC3")
    # the synthetic text section is 0x10000..0x1FFFF, so the data blob at 0x90000 is outside it
    got = literals(image, body, 0x10000, lambda v: 0x10000 <= v < 0x20000)
    want("a UTF-16 literal decodes", "Z:\\Cache" in got)
    want("the ini section decodes", "ini:Engine.Engine.Canvas" in got)
    want("an ASCII blob is not a literal", all("Cache" != s for s in got))
    want("a small integer is not a literal", len(got) == 2)
    want("the ini class is the middle field", ini_class("ini:Engine.Engine.Canvas") == "Engine")
    want("a non-ini section has no class", ini_class("Z:\\Cache") == "")
    # a code image: a named callee, a thunk, and the parent's table store
    code = bytearray(b"\xCC" * 0x2400)
    code[0x000:0x003] = b"\xC3\x90\x90"
    code[0x010:0x010 + 5] = b"\xE8" + struct.pack("<i", 0x1000 - 0x015)   # call thunk
    code[0x010 + 5:0x010 + 11] = b"\xC7\x06" + struct.pack("<I", 0x5100)  # own table
    code[0x010 + 11] = 0xC3
    code[0x1000:0x1000 + 19] = b"\x8B\x4C\x24\x04\x85\xC9\x74\x02\xE9" + \
        struct.pack("<i", 0x2000 - 0x100D)
    code[0x2000:0x2000 + 6] = b"\xC7\x06" + struct.pack("<I", 0x5000)     # parent table
    code[0x2000 + 6] = 0xC3
    img = Fake({0x11000: bytes(code)})
    index = [(0x11000, 0x11003, "ctor_Named"), (0x11010, 0x1101C, "FUN_00011010"),
             (0x12000, 0x12013, "FUN_00012000"), (0x13000, 0x13007, "ctor_Parent")]
    starts = [a for a, _, _ in index]
    tables = {0x5000: 30, 0x5100: 31}
    body_tables = {0x11010: {0x5100}, 0x13000: {0x5000}}
    edge = parent_edge(img, index, starts, tables, body_tables, 0x5100, [0x11010])
    want("the thunk is followed to the parent's store", edge is not None and edge[3] == 0x5000)
    want("the call site is the installer's own instruction", edge is not None and edge[1] == 0x11010)
    want("an absolute store is not an install", stores(b"\xC7\x05" + struct.pack("<I", 0x400000) +
                                                      struct.pack("<I", 0x5000), 0x20000, tables) == [])
    both = parent_edge(img, index, starts, tables, {0x11010: {0x5000, 0x5100}}, 0x5100, [0x11010])
    want("a body installing two tables yields no edge", both is None)
    want("named calls resolve through the map",
         calls(img.read_va(0x11010, 12), 0x11010, index, starts)[0] == set())
    row = {"installers": 0, "own_class_literal": "no", "ini_classes": "", "ini_sections": "",
           "class_literals": 0, "literals": "", "literal_count": 0, "named_calls": "",
           "named_call_count": 0, "parent_route": "none", "chain_root": "", "chain_depth": 0}
    want("a table no body stores is judged no-body", judge(row)[0] == "no-body")
    row.update({"installers": 1, "named_calls": "Engine_UnCanvas", "named_call_count": 1})
    want("a named callee alone places a table in a subsystem", judge(row)[0] == "subsystem")
    row.update({"ini_sections": "ini:Engine.Engine.Canvas", "ini_classes": "Engine"})
    want("an ini literal outranks a callee", judge(row)[0] == "config-class")
    if fails:
        print("FAIL class-ctor-evidence: %s" % "; ".join(fails))
        return 1
    print("ok   class-ctor-evidence: 18 assertions")
    return 0


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe", help="the dump's default.xbe")
    ap.add_argument("-o", "--out", help="artifact directory")
    ap.add_argument("--report", action="store_true", help="print the census")
    ap.add_argument("--write-doc", metavar="PAGE")
    ap.add_argument("--check-doc", metavar="PAGE")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump, no write")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.check_doc:
        rows = read(OUT)
        if not rows:
            raise SystemExit("%s: missing -- regenerate it with --xbe" % OUT)
        write_doc(args.check_doc, rows, check=True)
        return 0
    if args.write_doc:
        rows = read(OUT)
        if not rows:
            raise SystemExit("%s: missing -- regenerate it with --xbe" % OUT)
        write_doc(args.write_doc, rows)
        return 0
    if not args.xbe:
        if args.report:
            for line in render(read(OUT)):
                print(line)
            return 0
        ap.error("--xbe, --report, --check or a doc pass is required")
    from xbe import Xbe

    image = Xbe(args.xbe)
    index = body_index(MAP)
    reach = read(REACH)
    text = [sec for sec in image.sections if sec.name.lower().startswith(".text")]
    text_range = (text[0].va, text[0].va + text[0].fsize) if text else (0, 0)
    rows, supers = harvest(image, index, reach, read(SHAPES), read(NAMES), read(CLASSES),
                           text_range)
    # the control: where the named table's class has a script super, the edge must be that class
    tally = collections.Counter(r["parent_vs_script"] for r in rows if r["parent_vs_script"])
    agree, contradict = tally["agree"], tally["differ"]
    no_super = tally["no-script-chain"]
    if os.environ.get("CTOR_DEBUG") and contradict:
        shown = 0
        for r in rows:
            if r["population"] != "named" or r["parent_route"] == "none":
                continue
            want = supers.get(r["class"], "")
            if r["parent_vs_script"] == "differ" and shown < 12:
                print("X %s %s: parent %s %s, script super %s"
                      % (r["table_va"], r["class"], r["parent_va"], r["parent_class"], want))
                shown += 1
    if contradict:
        raise SystemExit("the parent rule contradicts the script tree on %d named tables"
                         % contradict)
    print("control  %s" % ", ".join("%s %d" % (k, v) for k, v in sorted(tally.items())))
    if args.out:
        write_csv(os.path.join(args.out, "class_ctor_evidence.csv"), rows)
        print("wrote %s" % os.path.join(args.out, "class_ctor_evidence.csv"))
    if args.report or not args.out:
        for r in render(rows):
            print(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
