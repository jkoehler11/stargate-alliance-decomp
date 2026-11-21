#!/usr/bin/env python3
"""The 250 tables no registration names are two populations: 18 class tables and 232 interfaces.

`tools/class-table-store-fields.py` finds **250 tables no registration names** -- 38 a class constructor
stores (the `bounded` bound), 212 only bodies no class names store (`plain-only`), 4 nothing stores.
U40's naming half has been read from the store's operand, the storing body's callers, the ctor's literals
and the destructor shape, and none of those channels names the table. This tool asks a different question
of the same population -- *what is the table?* -- because a class dispatch table and an interface object's
table are told apart by their contents:

  * the **class-table prefix**: every one of the 474 tables the registration path names starts with the
    same three bodies (`0x00062650`, `0x00062660`, `0x00062670`), and none of the 49 member tables does.
    A table that carries the same three slots is a class table by a control measured on both sides --
    and `inventory/native_vtable_shared.csv` already partitions the same 250 the same way, because the
    232 tables it lists are the ones that share no prefix with a class table at all;
  * the **slot vocabulary**: a C++ class's vtable holds its own virtual methods, and this build's map
    carries masked-symbol matches against the shipped XDK libraries (`?Pump@CVoiceMailMsg@@...`), so a
    table whose slots are one class's methods names that class even though no registration does;
  * the **installer's own symbol**: `??0<C>@@` / `??1<C>@@` is a C++ constructor or destructor and names
    the class it builds;
  * the **parent edge**: `inventory/class_table_parent_edges.csv` records which table a class's own ctor
    resets to; a table that is a named class's `parent_va` is that class's base level, and the children
    name the ladder.

    tools/class-plain-tables.py --xbe DUMP/default.xbe -o inventory
    tools/class-plain-tables.py --report
    tools/class-plain-tables.py --write-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-plain-tables.py --check-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-plain-tables.py --check          # synthetic tables, no dump, writes nothing

The dump is never committed; the artifact and the doc block are. Addresses, slot indices and counts only.
"""
import argparse
import collections
import csv
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

OUT = os.path.join("inventory", "class_plain_tables.csv")
STORE = os.path.join(ROOT, "inventory", "class_table_store_fields.csv")
REACH = os.path.join(ROOT, "inventory", "native_vtable_reach.csv")
SYMBOLS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
PARENTS = os.path.join(ROOT, "inventory", "class_table_parent_edges.csv")
CLASS_SLOTS = os.path.join(ROOT, "inventory", "xbe_class_table_slots.csv")
CLASS_TABLES = os.path.join(ROOT, "inventory", "xbe_class_tables.csv")
MEMBERS = os.path.join(ROOT, "inventory", "class_member_tables.csv")
SRC = os.path.join(ROOT, "inventory", "class_plain_tables.csv")
DOC_START = "<!-- generated:class-plain-tables -->"
DOC_END = "<!-- /generated:class-plain-tables -->"
FIELDS = ("table_va", "slots", "slots_run", "stop", "population", "store", "signature", "slot0",
          "purecall", "stubs", "named_slots", "fun_slots", "outside_map", "vocab_class",
          "vocab_slots", "vocab_classed", "vocab_share", "installer_classes", "installer_agree",
          "children", "child_named", "child_classes", "verdict", "evidence")
PREFIX = (0x00062650, 0x00062660, 0x00062670)
STUB_BYTES = 8
HEX = re.compile(r"^(0x)?[0-9a-fA-F]+$")
POPULATIONS = ("bounded", "plain-only")
# A language keyword cannot be a class name: `class_table_parent_edges.csv` carries one child row whose
# name is `new`, from a registration site whose literal that reader took from the wrong operand.
KEYWORDS = {"new", "delete", "void", "int", "char", "float", "double", "bool", "if", "else", "for",
            "while", "do", "switch", "case", "default", "return", "break", "continue", "sizeof",
            "this", "operator", "typedef", "struct", "class", "enum", "union", "unsigned", "signed"}


# --------------------------------------------------------------------------- reading
def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def key(text):
    """Every artifact spells a VA its own way; one spelling for joins (`0x004c78c0`)."""
    text = (text or "").strip()
    return "%#010x" % int(text, 16) if HEX.match(text) else text


def read_u32(image, va):
    return struct.unpack("<I", image.read_va(va, 4))[0]


def wide_text(v):
    """Two printable UTF-16 characters -- a wide string's first half, not a code pointer."""
    lo, hi = v & 0xFFFF, (v >> 16) & 0xFFFF
    return 0x20 <= lo <= 0x7E and 0x20 <= hi <= 0x7E


def own_extent(read, table_va, run_slots, text_range):
    """(slots, stop) under the tables' own tail rule: the run ends at the first dword that is not
    inside `.text`, or that spells two printable UTF-16 characters.

    `inventory/native_vtable_reach.csv`'s extent is the block scan's -- every dword inside
    `.text`'s *address range* -- and `.rdata` holds these tables in front of the build's wide-string
    pool, so a string's first two characters (`0x00330044`, `D3`) are an in-range aligned dword and
    the run keeps counting. `tools/class-tables.py`'s class census already ends a table on that
    dword; this is the same rule without its body-start gate, which cannot apply here because an
    interface's bodies are the XDK libraries' and the function map covers `.rdata` 0.000.
    """
    n = 0
    while n < run_slots:
        try:
            v = read(table_va + 4 * n)
        except KeyError:
            return n, None
        if not (text_range[0] <= v < text_range[1]) or wide_text(v):
            return n, v
        n += 1
    return n, None


def slot_vector(image, table_va, count):
    return [read_u32(image, table_va + 4 * k) for k in range(count)]


def load_symbols(path):
    """({va: name}, {va: size}) from the symbol map."""
    names, sizes = {}, {}
    for row in read_csv(path):
        try:
            va = int(row["address"], 16)
        except (KeyError, ValueError):
            continue
        names[va] = row.get("name") or ""
        try:
            sizes[va] = int(row.get("size") or 0)
        except ValueError:
            sizes[va] = 0
    return names, sizes


# --------------------------------------------------------------------------- naming
def mangle_class(symbol):
    """The innermost class scope of an MSVC mangled name, or ''.

    `??0CVoiceMailPseudoFeature@UIXNamespace@@QAE@XZ` is a **constructor** and its class is the first
    token; `?Pump@CVoiceMailMsg@UIXNamespace@@QAEXXZ` is a **method** whose class is the token after it.
    Templates (`??$...`) carry no class token this reader can trust and are refused rather than guessed.
    """
    if not symbol or not symbol.startswith("?"):
        return ""
    if symbol.startswith("??$"):
        return ""
    if symbol.startswith(("??_G", "??_E")):
        parts = symbol[4:].split("@@")[0].split("@")
        return parts[0] if parts and parts[0] else ""
    if symbol.startswith(("??0", "??1")):
        parts = symbol[3:].split("@@")[0].split("@")
        return parts[0] if parts and parts[0] else ""
    parts = symbol[1:].split("@@")[0].split("@")
    return parts[1] if len(parts) > 1 and parts[1] else ""


def body_kind(va, names, sizes):
    """`pure`, `stub`, `named`, `fun` or `outside` for one slot body."""
    if names.get(va) == "__purecall":
        return "pure"
    if va not in sizes:
        return "outside"
    if sizes[va] and sizes[va] <= STUB_BYTES:
        return "stub"
    name = names.get(va, "")
    if name and not name.startswith("FUN_"):
        return "named"
    return "fun"


def vocabulary(vector, names):
    """({class: slots} over the slot bodies' mangled class scopes, slots that carry a class)."""
    counts = collections.Counter()
    for va in vector:
        cls = mangle_class(names.get(va, ""))
        if cls:
            counts[cls] += 1
    return counts, sum(counts.values())


def has_prefix(vector):
    return len(vector) >= 3 and tuple(vector[:3]) == PREFIX


def names_a_class(vocab, classed, slots):
    """Does the vocabulary name the table? Two of the class's methods, or one in a one-slot table."""
    if not vocab:
        return False
    top = max(vocab.values())
    if classed and top / float(classed) < 0.5:
        return False
    return top >= 2 or (slots == 1 and top == 1)


def verdict_of(signature, vocab, classed, slots, children):
    if signature:
        return "class-table-base" if children else "class-table"
    if names_a_class(vocab, classed, slots):
        return "interface-named"
    return "interface-unnamed"


# --------------------------------------------------------------------------- rows
def harvest(tables, names, sizes, installers, pop_of, form_of, parents, runs=None):
    """`tables` is {table_va: slots}; `installers` is {table_va: [symbol names]}; `parents` is
    {parent_va: [child class]}; `runs` is {table_va: (block-scan slots, the dword that ended it)},
    so every row carries the extent it was read at beside the run the scan declared."""
    runs = runs or {}
    rows = []
    for tva in sorted(tables):
        vector = tables[tva]
        run_slots, stop = runs.get(tva, (len(vector), None))
        kinds = collections.Counter(body_kind(va, names, sizes) for va in vector)
        vocab, classed = vocabulary(vector, names)
        top, top_n = (max(vocab.items(), key=lambda kv: kv[1]) if vocab else ("", 0))
        share = top_n / float(classed) if classed else 0.0
        inst = sorted({mangle_class(s) for s in installers.get(tva, []) if mangle_class(s)})
        signature = has_prefix(vector)
        kids = parents.get(tva, [])
        agree = ""
        if inst and top:
            agree = "yes" if top in inst else "no"
        elif inst or top:
            agree = "one-side"
        row = {
            "table_va": "%#010x" % tva,
            "slots": str(len(vector)),
            "slots_run": str(run_slots),
            "stop": "" if stop is None else "%#010x" % stop,
            "population": pop_of.get(tva, ""),
            "store": form_of.get(tva, ""),
            "signature": "yes" if signature else "no",
            "slot0": "%#010x" % vector[0] if vector else "",
            "purecall": str(kinds["pure"]),
            "stubs": str(kinds["stub"]),
            "named_slots": str(kinds["named"]),
            "fun_slots": str(kinds["fun"]),
            "outside_map": str(kinds["outside"]),
            "vocab_class": top,
            "vocab_slots": str(top_n),
            "vocab_classed": str(classed),
            "vocab_share": "%.2f" % share,
            "installer_classes": " ".join(inst),
            "installer_agree": agree,
            "children": str(len(kids)),
            "child_named": str(len([k for k in kids if not HEX.match(k)])),
            "child_classes": " ".join(kids[:8]),
            "verdict": verdict_of(signature, vocab, classed, len(vector), kids),
            "evidence": "%d slot(s); %d outside the map; %d named; top class %s on %d of %d classed; "
                        "installer %s; %d child class(es)%s"
                        % (len(vector), kinds["outside"], kinds["named"], top or "-", top_n, classed,
                           ", ".join(inst) or "-", len(kids),
                           "" if run_slots == len(vector) else
                           "; the block scan read %d, ended by %s (%s)"
                           % (run_slots, "" if stop is None else "%#010x" % stop,
                              "wide text" if stop is not None and wide_text(stop) else "a non-code dword")),
        }
        rows.append(row)
    return rows


def installer_names():
    """{table_va: [installer symbols]} from the reach census."""
    return {key(r["table_va"]): [s for s in (r.get("install_bodies") or "").split() if s != "-"]
            for r in read_csv(REACH)}


def parent_children():
    """{parent_va: [class]} from the class tables' parent edges, minus names that are language keywords."""
    out = collections.defaultdict(list)
    for row in read_csv(PARENTS):
        p = key(row.get("parent_va"))
        if HEX.match(p):
            cls = (row.get("class") or "").strip()
            if cls.lower() in KEYWORDS:
                continue
            out[p].append(cls or key(row.get("table_va")))
    return {k: sorted(set(v)) for k, v in out.items()}


def store_forms():
    """{table_va: (population, store verdict)} from the store-field census."""
    return {key(r["table_va"]): (r.get("population") or "", r.get("verdict") or "")
            for r in read_csv(STORE)}


def control_of():
    """The class-table prefix and the populations, from the committed censuses (no dump)."""
    out = {"tables": 0, "slot0": "", "prefix": [], "named": 0}
    rows = {r["slot"]: r for r in read_csv(CLASS_SLOTS) if (r.get("slot") or "").isdigit()}
    for i in ("0", "1", "2"):
        if i in rows:
            out["prefix"].append("%#010x" % int(rows[i]["most_common"], 16))
    if "0" in rows:
        out["tables"] = int(rows["0"]["tables"])
        out["slot0"] = "%#010x" % int(rows["0"]["most_common"], 16)
    forms = store_forms()
    out["named"] = sum(1 for f in forms.values() if f[0] == "named")
    classrows = read_csv(CLASS_TABLES)
    out["class_tables"] = len(classrows)
    surplus = [int(r.get("surplus") or 0) for r in classrows]
    out["class_moved"] = sum(1 for s in surplus if s)
    out["class_surplus"] = sum(surplus)
    names, _ = load_symbols(SYMBOLS)
    members = read_csv(MEMBERS)
    out["member_tables"] = len(members)
    out["member_named"] = 0
    for r in members:
        vec = [int(b, 16) for b in (r.get("slot_bodies") or "").split()]
        vocab, classed = vocabulary(vec, names)
        if names_a_class(vocab, classed, len(vec)):
            out["member_named"] += 1
    return out


# --------------------------------------------------------------------------- rendering
def render(rows, control):
    if not rows:
        return "| no unnamed table was read | |\n"
    lines = []
    n = len(rows)
    prefix = [r for r in rows if r["signature"] == "yes"]
    bases = [r for r in rows if r["verdict"] == "class-table-base"]
    named = [r for r in rows if r["verdict"] == "interface-named"]
    inst_named = [r for r in rows if r["installer_classes"]]
    agree = [r for r in rows if r["installer_agree"] == "yes"]
    slots = sum(int(r["slots"]) for r in rows)
    outside = sum(int(r["outside_map"]) for r in rows)
    pure = sum(int(r["purecall"]) for r in rows)
    stubs = sum(int(r["stubs"]) for r in rows)

    lines.append("| population | store | tables | slots | the class-table prefix | named by their slots | named by the installer | neither |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for pop in POPULATIONS:
        for form in ("vtable-slot", "vtable-slot+member", "member-field", "global", "frame-only"):
            group = [r for r in rows if r["population"] == pop and r["store"] == form]
            if not group:
                continue
            lines.append("| %s | `%s` | %d | %d | %d | %d | %d | %d |" % (
                pop, form, len(group), sum(int(r["slots"]) for r in group),
                sum(1 for r in group if r["signature"] == "yes"),
                sum(1 for r in group if r["verdict"] == "interface-named"),
                sum(1 for r in group if r["installer_classes"]),
                sum(1 for r in group if r["verdict"] == "interface-unnamed")))
    lines.append("")

    run_slots = sum(int(r.get("slots_run") or r["slots"]) for r in rows)
    moved = [r for r in rows if int(r.get("slots_run") or r["slots"]) != int(r["slots"])]
    if moved:
        lines.append("**The block scan's extent is %d dword(s) too long on %d of the %d tables: this "
                     "population holds %d slots, not the %d `inventory/native_vtable_reach.csv` "
                     "counts.** These tables sit in `.rdata` in front of the build's wide-string "
                     "pool, so a string's first two characters (`0x00330044` is `D3`) are an aligned "
                     "dword inside `.text`'s *address range* and the scan keeps counting -- the class "
                     "census ends a table on exactly that dword and %d of its %d tables end on one, "
                     "%d dwords in all. Each row above is read at the shorter extent, and `stop` "
                     "names the dword that ended it: %s."
                     % (run_slots - slots, len(moved), n, slots, run_slots,
                        control.get("class_moved", 0), control.get("class_tables", 0),
                        control.get("class_surplus", 0),
                        ", ".join("`%s` %d -> %d (%s)" % (
                            r["table_va"], int(r["slots_run"]), int(r["slots"]),
                            "`%s`" % r["stop"] if r["stop"] else "a non-code dword")
                            for r in moved)))
        lines.append("")
    else:
        lines.append("**The block scan's extent and the tables' own extent agree on all %d rows: %d "
                     "slots.** A string's first two characters would be an aligned dword inside "
                     "`.text`'s address range, so this is a measurement and not a formality -- the "
                     "class census ends %d of its %d tables on that dword, %d dwords in all."
                     % (n, slots, control.get("class_moved", 0), control.get("class_tables", 0),
                        control.get("class_surplus", 0)))
        lines.append("")

    lines.append("**%d of the %d tables no registration names carry the class-table prefix, and the control "
                 "is measured on both sides.** All %d tables the registration path names start with the same "
                 "three bodies (%s) and none of the %d member tables does, so the prefix separates a class's "
                 "dispatch table from an interface object's table without a name. The %d that carry it are "
                 "class tables whose class no registration names; the other %d are interfaces."
                 % (len(prefix), n, control["named"], ", ".join("`%s`" % s for s in control["prefix"]),
                    len(read_csv(MEMBERS)), len(prefix), n - len(prefix)))
    lines.append("")

    if bases:
        withnamed = [r for r in bases if int(r["child_named"])]
        big = sorted(withnamed, key=lambda r: (-int(r["child_named"]), -int(r["slots"])))
        named_kids = lambda r: [c for c in r["child_classes"].split() if not c.startswith("0x")]
        lines.append("**All %d are recorded as a parent edge, and %d are a named class's.** %s. Those are "
                     "the tables `inventory/class_table_parent_edges.csv` records as the classes' "
                     "`parent_va`, read from the destructor thunk rather than from a registration, and no "
                     "shipped package declares the class of any of them -- `inventory/classes.csv` has no "
                     "row -- which is why no registration site can carry its name. Where the child the "
                     "edge names is itself one of the %d (`0x004c78c0` -> `0x004d0318` -> "
                     "`TcpNetDriver`), the ladder is read rung by rung; the five base levels U40 left open "
                     "are five of these %d, and their parent is unnamed because the class is not in any `.u`."
                     % (len(prefix), len(withnamed),
                        "; ".join("`%s` (%d slots) is the table behind %s" % (
                            r["table_va"], int(r["slots"]),
                            ", ".join("`%s`" % c for c in named_kids(r)[:5]) +
                            ("" if int(r["child_named"]) <= 5
                             else " and %d more" % (int(r["child_named"]) - 5)))
                            for r in big[:5]), len(prefix), len(prefix)))
        lines.append("")

    if named:
        lines.append("**%d of the %d interfaces are named by their own slots.** A C++ vtable holds its "
                     "class's virtual methods, and the map carries masked-symbol matches against the "
                     "shipped XDK libraries (`uix.lib`, `xvoice.lib`, `xonline.lib`), so a table whose slots "
                     "are one class's methods carries that class: %s."
                     % (len(named), n - len(prefix),
                        ", ".join("`%s` = `%s` (%s of %s slots that carry a class)" % (
                            r["table_va"], r["vocab_class"], r["vocab_slots"], r["vocab_classed"])
                            for r in sorted(named, key=lambda r: -int(r["vocab_slots"])))))
        lines.append("")
        lines.append("**The same reading names %d of the %d member tables.** Their slots carry no mangled "
                     "class scope at all, so the interface object a member field holds stays unnamed by "
                     "this channel -- the member's *own* name comes from the field it is stored in, which "
                     "`tools/class-member-tables.py` already builds."
                     % (control.get("member_named", 0), control.get("member_tables", 0)))
        lines.append("")

    lines.append("**The installer's own symbol names a class on %d table%s, and the two channels agree on "
                 "%d.** %sA `??0<C>@@`/`??1<C>@@` symbol is a C++ constructor or destructor and names the "
                 "class it builds outright; it is the narrow channel of the two, because only the XDK "
                 "library bodies carry a mangled symbol, and the slot vocabulary does not need one."
                 % (len(inst_named), "" if len(inst_named) == 1 else "s", len(agree),
                    ("It is " + ", ".join("`%s` = `%s`" % (r["table_va"], r["installer_classes"])
                                          for r in inst_named) + ", a CRT class and not one of the "
                     "interfaces. ") if inst_named else ""))
    lines.append("")

    lines.append("**%d slots resolve, and %d of them sit outside the function map.** %d slots are "
                 "`__purecall` and %d are stubs of %d bytes or less -- an interface's default "
                 "implementation -- so the interface half repeats the shape the 49 member tables already "
                 "showed, at a receiver that is the object's own slot rather than a member field."
                 % (slots, outside, pure, stubs, STUB_BYTES))
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


def block_from_csv(path):
    rows = read_csv(path)
    if not rows:
        return None
    return render(rows, control_of())


# --------------------------------------------------------------------------- self-test
def self_check():
    bad = []
    if mangle_class("??0CVoiceMailPseudoFeature@UIXNamespace@@QAE@XZ") != "CVoiceMailPseudoFeature":
        bad.append("a constructor names its class")
    if mangle_class("?Pump@CVoiceMailMsg@UIXNamespace@@QAEXXZ") != "CVoiceMailMsg":
        bad.append("a method names its class and not its namespace")
    if mangle_class("??_GCVoiceMailMsg@UIXNamespace@@QAEPAXI@Z") != "CVoiceMailMsg":
        bad.append("a scalar deleting destructor names its class")
    if mangle_class("??$assign@V?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@std@@") != "":
        bad.append("a template name is refused rather than guessed")
    if mangle_class("Engine_UnObjBas") != "" or mangle_class("") != "":
        bad.append("a plain C symbol and an empty name carry no class")

    names = {0x9001: "__purecall", 0x9002: "FUN_00009002", 0x9003: "Some_Real",
             0x9004: "?A@CFoo@@QAEXXZ", 0x9005: "?B@CFoo@@QAEXXZ", 0x9006: "?C@CBar@@QAEXXZ"}
    sizes = {0x9001: 20, 0x9002: 3, 0x9003: 40, 0x9004: 30, 0x9005: 30, 0x9006: 30}
    if body_kind(0x9001, names, sizes) != "pure" or body_kind(0x9002, names, sizes) != "stub":
        bad.append("a purecall body and a 3-byte stub are classified")
    if body_kind(0x9003, names, sizes) != "named" or body_kind(0x9099, names, sizes) != "outside":
        bad.append("a named body and an unmapped one are classified")

    class_table = list(PREFIX) + [0x9003, 0x9004]
    interface = [0x9004, 0x9005, 0x9006]
    if not has_prefix(class_table) or has_prefix(interface):
        bad.append("only a table with all three prefix slots is a class table")
    vocab, classed = vocabulary(interface, names)
    if vocab.get("CFoo") != 2 or classed != 3:
        bad.append("the slot vocabulary counts the class each body is a method of")
    if not names_a_class(vocab, classed, 3) or names_a_class({}, 0, 3):
        bad.append("a vocabulary over half the classed slots names the table and an empty one does not")
    if names_a_class({"CFoo": 1}, 4, 5):
        bad.append("one method of a class out of four does not name a table")
    if verdict_of(True, {"CFoo": 3}, 3, 5, []) != "class-table":
        bad.append("a prefixed table is a class table whatever its slots say")
    if verdict_of(False, {"CFoo": 3}, 3, 3, []) != "interface-named":
        bad.append("an interface whose classed slots are all one class is named")
    if verdict_of(True, {}, 0, 5, ["XboxViewport"]) != "class-table-base":
        bad.append("a prefixed table that is a named class's parent edge is a base level")

    # the tables' own extent: a wide-string half and a non-code dword each end the run, and a clean
    # run is not shortened -- the block scan's wider rule is the control the row carries
    text_range = (0x10000, 0x30000)
    body = {0x5000: 0x11000, 0x5004: 0x11000, 0x5008: 0x00330044, 0x500C: 0x11000,
            0x6000: 0x11000, 0x6004: 0x00000000}
    if own_extent(lambda va: body[va], 0x5000, 4, text_range) != (2, 0x00330044):
        bad.append("a wide-string half ends the table at the slot before it")
    if own_extent(lambda va: body[va], 0x5000, 2, text_range) != (2, None):
        bad.append("a run that ends on its last slot reads its full extent")
    if own_extent(lambda va: body[va], 0x6000, 2, text_range) != (1, 0):
        bad.append("a null slot ends the table at the slot before it")
    if own_extent(lambda va: 0x11000, 0x7000, 3, text_range) != (3, None):
        bad.append("a clean run is not shortened")
    if not wide_text(0x00330044) or wide_text(0x003e5501) or wide_text(0x00016ed0):
        bad.append("only two printable UTF-16 characters read as wide text")

    tables = {0x1000: class_table, 0x2000: interface, 0x3000: [0x9004, 0x9006, 0x9002]}
    rows = harvest(tables, names, sizes,
                   {0x2000: ["??0CFoo@@QAE@XZ"], 0x1000: ["FUN_00001000"]},
                   {0x1000: "plain-only", 0x2000: "bounded", 0x3000: "plain-only"},
                   {0x1000: "vtable-slot", 0x2000: "member-field", 0x3000: "frame-only"},
                   {0x1000: ["XboxViewport"]}, {0x2000: (5, 0x00330044)})
    by = {int(r["table_va"], 16): r for r in rows}
    if by[0x1000]["verdict"] != "class-table-base" or by[0x1000]["child_classes"] != "XboxViewport":
        bad.append("the parent edge is carried on the row: %r" % (by[0x1000],))
    if by[0x2000]["verdict"] != "interface-named" or by[0x2000]["installer_agree"] != "yes":
        bad.append("the installer's ctor and the slot vocabulary agree: %r" % (by[0x2000],))
    if by[0x3000]["verdict"] != "interface-unnamed":
        bad.append("two classes in three slots name nothing: %r" % (by[0x3000],))
    if by[0x1000]["signature"] != "yes" or by[0x2000]["signature"] != "no":
        bad.append("the prefix column follows the vector")
    if by[0x2000]["slots"] != "3" or by[0x2000]["slots_run"] != "5" or \
            by[0x2000]["stop"] != "0x00330044":
        bad.append("the row carries the run and the dword that ended it: %r" % (by[0x2000],))
    if by[0x1000]["slots_run"] != by[0x1000]["slots"] or by[0x1000]["stop"]:
        bad.append("a row with no run reads its own extent: %r" % (by[0x1000],))

    out = render(rows, {"tables": 3, "slot0": "0x00062650", "prefix": ["0x00062650"], "named": 3,
                        "member_named": 0, "member_tables": 1, "class_tables": 492,
                        "class_moved": 12, "class_surplus": 14})
    for want in ("class-table prefix", "parent edge", "interfaces", "too long on 1 of the 3"):
        if want not in out:
            bad.append("render lacks %r" % (want,))

    import tempfile
    path = os.path.join(tempfile.mkdtemp(), "a.csv")
    write_artifact(rows, path)
    if read_csv(path) != rows:
        bad.append("artifact round trip differs")
    if bad:
        print("FAIL class-plain-tables: " + "; ".join(bad))
        return 1
    print("ok   class-plain-tables: the prefix, the mangled class, the vocabulary share and the parent "
          "edge all agree with the synthetic tables")
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
        block = block_from_csv(SRC)
        if block is None:
            print("FAIL class-plain-tables: %s is missing" % SRC)
            return 1
        text = open(args.check_doc, encoding="utf-8").read()
        i, j = text.find(DOC_START), text.find(DOC_END)
        if i < 0 or j < 0:
            print("FAIL class-plain-tables: %s has no generated block" % args.check_doc)
            return 1
        want = DOC_START + "\n" + block + "\n" + DOC_END
        if text[i:j + len(DOC_END)] != want:
            print("FAIL class-plain-tables: %s's block does not reproduce from %s" % (args.check_doc, SRC))
            return 1
        print("ok   class-plain-tables: %s's block reproduces from %s" % (args.check_doc, SRC))
        return 0

    if args.report or args.write_doc:
        block = block_from_csv(SRC)
        if block is None:
            print("FAIL class-plain-tables: %s is missing" % SRC)
            return 1
        if args.report:
            print(block)
            return 0
        text = open(args.write_doc, encoding="utf-8").read()
        new = splice_doc(text, block)
        if new is None:
            print("FAIL class-plain-tables: %s has no generated block markers" % args.write_doc)
            return 1
        if new != text:
            open(args.write_doc, "w", encoding="utf-8").write(new)
            print("wrote %s" % args.write_doc)
        return 0

    if not args.xbe:
        ap.error("--xbe is required to read the tables (or use --report/--check-doc/--check)")
    import xbe as xbemod
    image = xbemod.Xbe(args.xbe)
    names, sizes = load_symbols(SYMBOLS)
    forms = store_forms()
    slots_of = {}
    for row in read_csv(REACH):
        try:
            slots_of[key(row["table_va"])] = int(row["table_slots"] or 0)
        except ValueError:
            slots_of[key(row["table_va"])] = 0
    tables, pop_of, form_of, runs = {}, {}, {}, {}
    text = next((s for s in image.sections if s.name == ".text"), None)
    if text is None:
        raise SystemExit(".text: section not found")
    text_range = (text.va, text.va + text.vsize)
    read = lambda va: read_u32(image, va)
    for tva, (pop, verdict) in forms.items():
        if pop not in POPULATIONS:
            continue
        n = slots_of.get(tva, 0)
        if not n:
            continue
        va = int(tva, 16)
        own, stop = own_extent(read, va, n, text_range)
        if not own:
            continue
        tables[va] = slot_vector(image, va, own)
        runs[va] = (n, stop)
        pop_of[va] = pop
        form_of[va] = verdict
    rows = harvest(tables, names, sizes, {int(k, 16): v for k, v in installer_names().items()},
                   pop_of, form_of, {int(k, 16): v for k, v in parent_children().items()}, runs)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, os.path.basename(OUT))
        write_artifact(rows, path)
        print("wrote %s (%d rows)" % (path, len(rows)))
    else:
        print(render(rows, control_of()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
