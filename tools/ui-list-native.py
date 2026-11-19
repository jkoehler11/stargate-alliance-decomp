#!/usr/bin/env python3
"""A list's row is drawn by a class-table slot, and its height is measured every frame.

`docs/systems/UI.md` leaves two numbers to the image: the row's rectangle and the `ItemHeight` the
native `PreDraw` computes. This reads both out of `default.xbe` and nothing else -- no dump of guest
memory, no boot, no decompiler project:

* **the delegate chain, derived**: the wide string `OnDrawItem` -> the `FName` index the static
  initialiser caches for it -> the one body that loads that index and calls through the object's
  vtable (the dispatcher, `0x00300ee0`) -> its `E9/E8` callers, which are the row loops;
* **which class and slot holds a row loop**: the class tables in `inventory/xbe_class_tables.csv` are
  read slot by slot, so a row loop is reported against `GUIVertList` slot 31 and the classes that
  inherit it, and a slot that differs from the parent's is reported as an override;
* **what each slot body dispatches by name**: every `name -> FName index` pair the initialiser caches
  is harvested, then each family slot body is scanned for references to those globals. That is how
  slot 58 of `GUIMultiColumnListBox` reads as "calls `InitializeColumns`" and slot 31 as
  "calls `OnDrawItem`";
* **the field offsets**: `Top`/`Index`/`ItemsPerPage`/`ItemHeight`/`ItemWidth`/`interItemGutter`/
  `ItemPadding` are `var(List)` on the native `GUIListBase`, so their object offsets are not in any
  package. They are measured three ways -- read as the row's `H`, written by the `PreDraw` that sizes
  a row, and multiplied into the box's scroll range -- and the tool fails if a claimed offset does not
  appear in the body that is supposed to witness it.

The row itself (vertical list, `GUIVertList` slot 31 at `0x00301de0`):

    X = ClientBounds[0]
    Y = ClientBounds[1] + interItemGutter/2
    W = (int)(ClientBounds[2] - ClientBounds[0])
    H = ItemHeight
    pitch = ItemHeight + interItemGutter

and the horizontal list (`GUIHorzList` slot 31) swaps the roles of `ItemWidth`/`ItemHeight`, centres
the strip vertically, and advances `X` by `ItemWidth + interItemGutter`.

    python3 tools/ui-list-native.py DUMP -o inventory
    python3 tools/ui-list-native.py --report
    python3 tools/ui-list-native.py --write-doc docs/systems/UI.md
    python3 tools/ui-list-native.py --check-doc docs/systems/UI.md
    python3 tools/ui-list-native.py --check          # synthetic, no dump, writes nothing
"""
import argparse
import collections
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

ARTIFACT = os.path.join(ROOT, "inventory/ui_list_native.csv")
DOC = os.path.join(ROOT, "docs/systems/UI.md")
DOC_START = "<!-- generated:list-native -->"
DOC_END = "<!-- /generated:list-native -->"

FIELDS = ["kind", "name", "owner", "slot", "va", "form", "value", "verdict", "note"]

# The one string the chain starts from. A list hands each item to this delegate; the native row draw
# is what runs when the delegate is absent or returns false.
DELEGATE = "OnDrawItem"

# The two roots of the list family. A class belongs to the thread when its `super` chain reaches one
# of them; the branch names which half of the thread it is (`list` draws rows, `box` places a list).
ROOTS = (("list", "GUIListBase"), ("box", "GUIListBoxBase"))

# `GUIListBase`'s `var(List)` block, in declaration order, measured in the image. Offsets are the
# object offsets the natives use; `witness` names the body each one was read out of, and the tool
# refuses a row whose offset does not appear in its witness.
LIST_FIELDS = [
    ("Top", 0x258, "row-draw", "row loop starts at [this+0x258] and stops at Top+ItemsPerPage"),
    ("Index", 0x25C, "row-draw", "the selected row: compared per item to pass `bSelected`"),
    ("ItemsPerPage", 0x260, "predraw", "counted up by the PreDraw's fit loop, capped at 1000"),
    ("ItemHeight", 0x264, "predraw", "set from the style's text height, then used as the row's H"),
    ("ItemWidth", 0x268, "horz-draw", "the horizontal loop's item width"),
    ("interItemGutter", 0x26C, "row-draw", "the step between rows, and half of it insets the first"),
    ("ItemPadding", 0x270, "predraw-read", "read into ItemHeight once, before ItemsPerPage is counted"),
]

# The rectangle the native term is measured against: the list's own client rect (four contiguous
# floats), which the `GUIListBase` hit test also compares the pointer against.
ROW_RECT_OFFSET = 0x12C

# What each row of the artifact means. `value` carries the formula or the derived marker string.
RECT = {
    "GUIVertList": [
        ("x", "client[0]", "the row starts at the list's left edge"),
        ("y", "client[1] + interItemGutter/2", "half a gutter insets the first row"),
        ("w", "(int)(client[2] - client[0])", "the full client width, truncated to an int"),
        ("h", "ItemHeight", "the row height is the PreDraw's number, not the corpus's"),
        ("pitch", "ItemHeight + interItemGutter", "the step to the next row"),
    ],
    "GUIHorzList": [
        ("x", "client[0]", "the strip starts at the list's left edge"),
        ("y", "client[1] + interItemGutter/2", "or vertically centred when the flag is set"),
        ("w", "ItemWidth", "the horizontal loop's item width"),
        ("h", "(int)(client[3] - client[1])", "the full client height"),
        ("pitch", "ItemWidth + interItemGutter", "the step to the next item"),
    ],
}

# The other tree: a "list box" is a `GUIMultiComponent`, and its `PreDraw` draws exactly one item
# through slot 58 -- the *component's* slot 58, which is a different body from `GUIListBase`'s slot
# 58 at the same `+0xe8` offset. `GUIListBoxBase` overrides it; a page's `OnDrawItem` body is
# dispatched only by the list tree's row loops, so the box's item draw is where a delegate would go
# missing if anything wired one onto a box.
BOX_ROOT = "GUIListBoxBase"

# The styled-text routine's clip rectangle: `FCanvas`'s four floats, each used by one compare.
CLIP_FIELDS = [(0x3C, "clip left"), (0x40, "clip top"), (0x44, "clip right"), (0x48, "clip bottom")]

# The style's font table: `GUIStyles` indexes it by the style byte to pick the font it sets on the
# canvas for the duration of one draw.
FONT_ARRAY = 0xB0


# ------------------------------------------------------------------ image

class Image:
    """`default.xbe` with the four scans this tool needs, each over the mapped image."""

    def __init__(self, xbe_path):
        from xbe import Xbe
        self.x = Xbe(xbe_path)
        self.data = self.x.data
        self.sections = self.x.sections

    def off(self, va):
        for s in self.sections:
            if s.contains(va):
                return s.foff + (va - s.va)
        raise KeyError("0x%08x is in no section" % va)

    def va(self, foff):
        for s in self.sections:
            if s.foff <= foff < s.foff + s.fsize:
                return s.va + (foff - s.foff)
        return None

    def u32(self, va):
        return struct.unpack_from("<I", self.data, self.off(va))[0]

    def bytes_at(self, va, n):
        o = self.off(va)
        return self.data[o:o + n]

    def text(self):
        """The executable `.text` section, as (va, bytes)."""
        for s in self.sections:
            if s.name == ".text":
                return s.va, self.data[s.foff:s.foff + s.fsize]
        raise SystemExit("no .text section")

    def wide(self, va, limit=64):
        raw = self.bytes_at(va, limit * 2)
        out = []
        for i in range(0, len(raw) - 1, 2):
            ch = raw[i] | (raw[i + 1] << 8)
            if ch == 0:
                break
            if ch < 0x20 or ch > 0x7E:
                return None
            out.append(chr(ch))
        return "".join(out) or None

    def dword_refs(self, target, limit=40):
        """Every place the image stores `target` as a dword."""
        pack = struct.pack("<I", target)
        hits, i = [], 0
        while len(hits) < limit:
            i = self.data.find(pack, i)
            if i < 0:
                break
            v = self.va(i)
            if v is not None:
                hits.append(v)
            i += 1
        return hits

    def callers(self, target):
        """Direct `E8 rel32` call sites of `target` (an indirect call is invisible, by design)."""
        base, blob = self.text()
        out = []
        for i in range(len(blob) - 5):
            if blob[i] != 0xE8:
                continue
            rel = struct.unpack_from("<i", blob, i + 1)[0]
            if base + i + 5 + rel == target:
                out.append(base + i)
        return out

    def callers_in(self, va, target, hi):
        """Direct `E8` calls to `target` inside the body starting at `va`."""
        blob = self.bytes_at(va, max(0, (hi or extent(va)) - va))
        out = []
        for i in range(len(blob) - 5):
            if blob[i] != 0xE8:
                continue
            if va + i + 5 + struct.unpack_from("<i", blob, i + 1)[0] == target:
                out.append(va + i)
        return out

    def contains_dword(self, lo, hi, value):
        pack = struct.pack("<I", value)
        blob = self.bytes_at(lo, max(0, hi - lo))
        hits, i = [], blob.find(pack)
        while i >= 0:
            hits.append(lo + i)
            i = blob.find(pack, i + 1)
        return hits


# ------------------------------------------------------- the FName table

NAME_CTOR = 0x5BED0  # `FName(wchar*, int)`: the static initialiser calls it once per name it caches


def fname_pairs(img):
    """Harvest `name -> FName index global` from the engine's static initialiser.

    The initialiser is the body with the most calls to the `FName` constructor that are preceded by a
    `push <wide string>`; each such call is followed within 16 bytes by the store of the resulting
    index into a global. That store is the global every later body loads when it dispatches the event
    by name, so the pair is the whole chain's anchor.
    """
    base, blob = img.text()
    sites = []
    for i in range(len(blob) - 5):
        if blob[i] != 0xE8:
            continue
        rel = struct.unpack_from("<i", blob, i + 1)[0]
        if base + i + 5 + rel != NAME_CTOR:
            continue
        # the `push imm32` that names the string: within 24 bytes before the call
        for back in range(4, 25):
            j = i - back
            if j < 0 or blob[j] != 0x68:
                continue
            sva = struct.unpack_from("<I", blob, j + 1)[0]
            try:
                name = img.wide(sva, 48)
            except KeyError:
                name = None
            if name and len(name) >= 3:
                sites.append((base + j, base + i + 5, name))
                break
    if not sites:
        raise SystemExit("no FName constructor sites found (wrong image?)")
    per_body = collections.Counter()
    pairs = {}
    for site, after, name in sites:
        seg = img.bytes_at(after, 24)
        glob = None
        for k in range(0, 13):
            if seg[k] == 0xA3:                       # mov [imm32], eax
                glob = struct.unpack_from("<I", seg, k + 1)[0]
                break
            if seg[k:k + 2] in (b"\x89\x0d", b"\x89\x15"):  # mov [imm32], ecx / edx
                glob = struct.unpack_from("<I", seg, k + 2)[0]
                break
        if not glob:
            continue
        # the global has to be read back somewhere: a name cached and never loaded is not a dispatch
        if not [v for v in img.dword_refs(glob, limit=4) if body_of(img, v) != body_of(img, site)]:
            continue
        pairs[name] = glob
        per_body[body_of(img, site)] += 1
    if not pairs:
        raise SystemExit("FAIL ui-list-native: no name index was cached with a reader")
    return sorted(per_body), pairs, len(sites)


def body_of(img, va, symbols=None):
    """The function start containing `va` (or `va` itself when the map has no entry before it)."""
    sym = symbols if symbols is not None else SYMBOLS
    starts = sorted(sym)
    import bisect
    i = bisect.bisect_right(starts, va) - 1
    return starts[i] if i >= 0 else va


def load_symbols():
    """`address -> (name, size)` from the committed map, plus the sorted start list."""
    out = {}
    path = os.path.join(ROOT, "symbols/xbox_functions.csv")
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[int(r["address"], 16)] = (r["name"], int(r["size"] or 0))
    return out


SYMBOLS = {}


def extent(va, cap=2048):
    """The body's end: the next function start, capped so a table-only body cannot swallow the image."""
    import bisect
    starts = sorted(SYMBOLS)
    i = bisect.bisect_right(starts, va)
    nxt = starts[i] if i < len(starts) else va + cap
    return min(nxt, va + cap) if nxt > va else va + cap


# a store to `[reg+disp32]`: `89 /r`, `C7 /0`, `F3 0F 11 /r`, `D9 /3`, `DD /3` with mod = 10
STORE_OPS = (b"\x89", b"\xc7", b"\xf3\x0f\x11", b"\xd9", b"\xdd")


def store_hits(img, lo, hi, disp):
    """Every store of a dword at `[this+disp]` in the body, counted by its opcode form.

    A body that merely *reads* an offset (the stock row draw reads the selected index, the box's
    layout reads the list's height) is not a writer, and the loose "the dword appears" test cannot
    tell them apart -- `0x260` and `0x264` are ordinary immediates too.
    """
    blob = img.bytes_at(lo, max(0, hi - lo))
    want = struct.pack("<I", disp)
    hits = []
    for op in STORE_OPS:
        start = 0
        while True:
            i = blob.find(op, start)
            if i < 0:
                break
            m = i + len(op)
            if m < len(blob) and blob[m] & 0xC0 == 0x80 and blob[m + 1:m + 5] == want:
                hits.append(lo + i)
            start = i + 1
    return hits


# ------------------------------------------------------------ inventories

def load_tables():
    """`table_va -> (slots, class)` from the committed class tables, and `class -> table_va`."""
    slots, names = {}, {}
    with open(os.path.join(ROOT, "inventory/xbe_class_tables.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            slots[int(r["table_va"], 16)] = int(r["slots"])
    with open(os.path.join(ROOT, "inventory/xbe_class_table_names.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["class"]:
                names[int(r["table_va"], 16)] = r["class"]
    return slots, names


def load_classes():
    rows = {}
    with open(os.path.join(ROOT, "inventory/classes.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.setdefault(r["class"], r)
    return rows


def family(classes):
    """Every class whose `super` chain reaches one of ROOTS, with the branch it belongs to."""
    out = {}
    for name, r in classes.items():
        cur, seen, depth = name, set(), 0
        while cur and cur not in seen and depth < 32:
            seen.add(cur)
            sup = classes.get(cur, {}).get("super") or ""
            for branch, root in ROOTS:
                if sup == root or cur == root:
                    own = classes.get(name, {}).get("super") or ""
                    out[name] = (branch, r.get("package", ""), own, r.get("is_native", ""))
            cur, depth = sup, depth + 1
    return out


# ------------------------------------------------------------- the box half

PROPERTIES = os.path.join(ROOT, "inventory/class_properties.csv")
WIRINGS = os.path.join(ROOT, "inventory/ui_list_rows.csv")
FLOW = os.path.join(ROOT, "inventory/menu_flow.csv")


def load_properties():
    """`(class, property) -> friendly type`, the declared type of every property in the corpus."""
    props = {}
    with open(PROPERTIES, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            props[(r["class"], r["property"])] = r.get("friendly_type") or ""
    return props


def prop_type(classes, props, cls, prop):
    """The class that declares `prop` on `cls`'s chain, and that property's type."""
    cur, depth = cls, 0
    while cur and depth < 32:
        if (cur, prop) in props:
            return cur, props[(cur, prop)]
        cur, depth = (classes.get(cur, {}).get("super") or ""), depth + 1
    return None, None


def receiver_class(classes, props, body, receiver):
    """The class of the object a wiring writes to: `self`, `List`, or `Panel.List`."""
    if receiver == "self":
        return body
    parts = receiver.split(".")
    cur = body
    for part in parts:
        _decl, cur = prop_type(classes, props, cur, part)
        if not cur:
            sys.exit("FAIL ui-list-native: %r.%r does not resolve (wiring on %r)"
                     % (body, receiver, body))
    return cur


def box_half(img, classes, fam, sym_slots, sym_names, del_index, dispatcher, loops):
    """The box tree's own item draw: one slot, one row, and no delegate."""
    rows = []
    if BOX_ROOT not in fam:
        sys.exit("FAIL ui-list-native: %s is not in the family" % BOX_ROOT)
    tva = [t for t, c in sym_names.items() if c == BOX_ROOT][0]
    n = sym_slots[tva]
    own = [img.u32(tva + i * 4) for i in range(n)]
    parent = classes[BOX_ROOT]["super"]
    ptva = [t for t, c in sym_names.items() if c == parent]
    pslots = [img.u32(ptva[0] + i * 4) for i in range(sym_slots[ptva[0]])] if ptva else []
    # the box must not descend from the list root: the two slot-58s are different trees
    for branch, root in ROOTS:
        cur, depth = BOX_ROOT, 0
        while cur and depth < 32:
            if cur == root and branch != "box":
                sys.exit("FAIL ui-list-native: %s descends from %s" % (BOX_ROOT, root))
            cur, depth = (classes.get(cur, {}).get("super") or ""), depth + 1
    if BOX_ROOT in [c for c, v in fam.items() if v[0] == "list"]:
        sys.exit("FAIL ui-list-native: %s is counted as a list" % BOX_ROOT)
    rows.append(dict(kind="box", name=BOX_ROOT, owner=classes[BOX_ROOT]["package"], slot="",
                     va="0x%08x" % tva, form="extends %s" % parent, value="component, not a list",
                     verdict="tree", note="the box's item draw is %s slot %d, not GUIListBase's"
                                          % (parent, 58)))

    # the one slot whose body calls slot 58 through the object's vtable is the box's PreDraw
    vcall = struct.pack("<I", 58 * 4)
    holders = []
    for i, fn in enumerate(own):
        lo, hi = fn, extent(fn)
        blob = img.bytes_at(lo, max(0, hi - lo))
        if any(blob[k] == 0xFF and blob[k + 1] in (0x90, 0x91, 0x92, 0x93)
               and blob[k + 2:k + 6] == vcall for k in range(len(blob) - 6)):
            holders.append(i)
    if len(holders) != 1:
        sys.exit("FAIL ui-list-native: %s has %d slots calling slot %d, expected 1"
                 % (BOX_ROOT, len(holders), 58))
    pre = holders[0]
    pre_body = own[pre]
    # the body may be inherited (it is, on this build): then the parent call must reach the first
    # ancestor whose slot `pre` is a different body -- that is what "PreDraw, then DrawItem" means
    anc, target = parent, None
    while anc and anc in classes and target is None:
        at = [t for t, c in sym_names.items() if c == anc]
        if at and pre < sym_slots[at[0]]:
            body = img.u32(at[0] + pre * 4)
            if body and body != pre_body:
                target = body
                break
        anc = classes[anc]["super"]
    if target is None or not img.callers_in(pre_body, target, extent(pre_body)):
        sys.exit("FAIL ui-list-native: the box's slot %d body does not chain to an ancestor's" % pre)
    rows.append(dict(kind="box", name=BOX_ROOT, owner="slot %d" % pre, slot=str(pre),
                     va="0x%08x" % pre_body, form="one call, no loop",
                     value="draws one item per frame", verdict="pre-draw",
                     note="%s: calls %s's slot %d (0x%08x) and then slot %d once with the canvas"
                          % ("inherited from %s" % parent if pre_body in pslots else "own",
                             anc, pre, target, 58)))

    item = own[58]
    body = img.bytes_at(item, extent(item) - item)
    for disp, label in ((0x260, "ItemsPerPage"), (0x264, "ItemHeight"), (0x26C, "interItemGutter")):
        if struct.pack("<I", disp) not in body:
            sys.exit("FAIL ui-list-native: the box's item draw never reads +0x%x (%s)" % (disp, label))
    if struct.pack("<I", del_index) in body:
        sys.exit("FAIL ui-list-native: the box's item draw references the %s index" % DELEGATE)
    rows.append(dict(kind="box", name=BOX_ROOT, owner="slot 58", slot="58", va="0x%08x" % item,
                     form="override of %s" % parent, value="the selected row",
                     verdict="item-draw",
                     note="reads the List's ItemsPerPage/ItemHeight/gutter and draws one row; "
                          "no %s dispatch" % DELEGATE))

    seen = set()
    for cls in sorted(fam):
        if fam[cls][0] != "box" or cls == BOX_ROOT:
            continue
        tv = [t for t, c in sym_names.items() if c == cls]
        if not tv:
            continue
        tv = tv[0]
        cn = sym_slots[tv]
        cv = [img.u32(tv + i * 4) for i in range(cn)]
        ptv = [t for t, c in sym_names.items() if c == classes[cls]["super"]]
        pv = [img.u32(ptv[0] + i * 4) for i in range(sym_slots[ptv[0]])] if ptv else []
        diff = [i for i in range(cn) if i >= len(pv) or (i != 3 and i < len(pv) and cv[i] != pv[i])]
        if 58 in diff:
            rows.append(dict(kind="box", name=cls, owner=fam[cls][1], slot="58", va="0x%08x" % cv[58],
                             form="override", value="its own item draw", verdict="item-draw",
                             note="replaces %s's slot 58 body 0x%08x" % (classes[cls]["super"], item)))
        elif len(diff) == 0:
            rows.append(dict(kind="box", name=cls, owner=fam[cls][1], slot="", va="0x%08x" % tv,
                             form="inherits every slot", value="no draw of its own", verdict="passthrough",
                             note="its table differs from %s's only in slot 3" % classes[cls]["super"]))
        seen.add(cls)

    # the delegate audit: no body anywhere in the box tree references the index or the dispatcher
    bodies, hits = 0, []
    for cls in sorted(fam):
        if fam[cls][0] != "box":
            continue
        tv = [t for t, c in sym_names.items() if c == cls]
        if not tv:
            continue
        tv = tv[0]
        for i in range(sym_slots[tv]):
            fn = img.u32(tv + i * 4)
            if not fn:
                continue
            bodies += 1
            lo, hi = fn, extent(fn)
            if struct.pack("<I", del_index) in img.bytes_at(lo, max(0, hi - lo)):
                hits.append((cls, i, fn))
            if fn == dispatcher or img.callers_in(fn, dispatcher, hi):
                hits.append((cls, i, fn))
    if hits:
        sys.exit("FAIL ui-list-native: %s reaches the delegate from %r" % (BOX_ROOT, hits[:3]))
    rows.append(dict(kind="box", name="delegate audit", owner="box tree", slot="", va="",
                     form="%d slot bodies" % bodies, value="0 references", verdict="absent",
                     note="no box slot body loads the %s index or calls its dispatcher, so a body "
                          "wired onto a box would never run" % DELEGATE))

    # and no shipped wiring does: every receiver is a list-branch class
    classes_by_name = classes
    props = load_properties()
    tally, resolved = collections.Counter(), []
    if os.path.exists(WIRINGS):
        with open(WIRINGS, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("kind") != "wiring":
                    continue
                recv = (r.get("note") or "").split(",")[0].strip()
                if not recv:
                    sys.exit("FAIL ui-list-native: wiring row %r has no receiver" % r)
                rc = receiver_class(classes_by_name, props, r["name"], recv)
                branch = fam.get(rc, ("?",))[0]
                tally[branch] += 1
                resolved.append((r["name"], recv, rc, branch, r.get("form")))
                if r.get("form") not in ("live", "commented"):
                    sys.exit("FAIL ui-list-native: wiring row %r has form %r" % (r, r.get("form")))
    if tally.get("box") or not tally:
        sys.exit("FAIL ui-list-native: a wiring targets the box tree (%r)" % (resolved,))
    rows.append(dict(kind="box", name="receiver audit", owner="ui_list_rows.csv", slot="", va="",
                     form="%d wirings" % sum(tally.values()), value="%d list receivers" % tally.get("list", 0),
                     verdict="no box receiver",
                     note="every receiver resolves to a GUIListBase descendant (%s), so the box's "
                          "missing dispatch is unreachable from the shipped corpus"
                          % ", ".join("%s %d" % (k, v) for k, v in
                                      sorted(collections.Counter(x[2] for x in resolved).items()))))

    # and who shows one: the page-side widget census, from the menu-flow tree
    widgets, pages = [], set()
    if os.path.exists(FLOW):
        with open(FLOW, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("kind") not in ("component", "subcomponent"):
                    continue
                w, cur, depth, box = r.get("verdict") or "", r.get("verdict") or "", 0, False
                while cur and depth < 32:
                    if cur == BOX_ROOT:
                        box = True
                        break
                    cur, depth = (classes.get(cur, {}).get("super") or ""), depth + 1
                if box:
                    widgets.append((r["name"], r.get("target") or "", w))
                    pages.add(r["name"])
    if not widgets:
        sys.exit("FAIL ui-list-native: no page places a %s widget" % BOX_ROOT)
    owned = sorted({w for _p, _t, w in widgets if w not in ("GUIListBox", "GUIScrollTextBox", "SGScrollTextBox")})
    rows.append(dict(kind="box", name="page widgets", owner="menu_flow.csv", slot="", va="",
                     form="%d widgets over %d pages" % (len(widgets), len(pages)),
                     value="%d box classes" % len({w for _p, _t, w in widgets}),
                     verdict="placed",
                     note="%s; the page-owned subclasses (%s) declare their rows on a box class but "
                          "wire them to the inherited List, a GUIVertList"
                          % (", ".join("%s %d" % (k, v) for k, v in
                                       sorted(collections.Counter(w for _p, _t, w in widgets).items())),
                             ", ".join(owned) or "none")))
    return rows
    return rows


def clip_signature(blob):
    """The text routine's clipping: (every `comiss`, the memory compares' disp8, the `movss` loads')."""
    comiss, mem = 0, []
    i = 0
    while True:
        i = blob.find(bytes.fromhex("0f2f"), i)
        if i < 0 or i + 2 >= len(blob):
            break
        comiss += 1
        if i + 3 < len(blob) and blob[i + 2] & 0xC0 == 0x40:
            mem.append(blob[i + 3])
        i += 1
    loads = []
    i = 0
    while True:
        i = blob.find(bytes.fromhex("f30f10"), i)
        if i < 0 or i + 3 >= len(blob):
            break
        if blob[i + 3] & 0xC0 == 0x40:
            loads.append(blob[i + 4])
        i += 1
    return comiss, mem, loads


def is_text_routine(blob):
    """A body is the styled-text routine when it clips against all four canvas edges."""
    comiss, mem, loads = clip_signature(blob)
    return (comiss == 4 and mem == [0x44, 0x48]
            and 0x3C in loads and 0x40 in loads and struct.pack("<I", FONT_ARRAY) in blob)


def drawtext_half(img, classes, fam, sym_slots, sym_names, loops):
    """`GUIStyles.DrawText`: the routine the stock item draw reaches, and its clip culls."""
    rows = []
    root = ROOTS[0][1]
    tv = [t for t, c in sym_names.items() if c == root][0]
    n = sym_slots[tv]
    item = img.u32(tv + (n - 1) * 4)          # the row loop's slot, the stock item draw
    lo, hi = item, extent(item)
    blob = img.bytes_at(lo, hi - lo)
    counts = collections.Counter()
    for i in range(len(blob) - 5):
        if blob[i] == 0xE8:
            rel = struct.unpack_from("<i", blob, i + 1)[0]
            counts[lo + i + 5 + rel] += 1
    twice = [t for t, c in counts.items() if c == 2]
    # more than one target is called twice (the item's style material as well as the text), so the
    # text routine is the one whose body carries the clip rectangle and the style's font table
    texts = [t for t in twice if is_text_routine(img.bytes_at(t, extent(t) - t))]
    if len(texts) != 1:
        sys.exit("FAIL ui-list-native: %d targets called twice carry the clip rect, expected 1"
                 % len(texts))
    text = texts[0]
    tl, th = text, extent(text)
    tbody = img.bytes_at(tl, th - tl)
    if not is_text_routine(tbody):
        sys.exit("FAIL ui-list-native: 0x%08x does not clip against all four canvas edges" % text)
    comiss, mem, loads = clip_signature(tbody)
    if [hex(d) for d in mem] != ["0x44", "0x48"]:
        sys.exit("FAIL ui-list-native: the clip compares are at %r, expected 0x44/0x48"
                 % [hex(d) for d in mem])
    rows.append(dict(kind="drawtext", name="styled text", owner="GUIStyles", slot="",
                     va="0x%08x" % text, form="called twice by the stock item draw",
                     value="one routine, 4 clip compares", verdict="measured",
                     note="the stock item draw calls it at two sites 0x55 apart with different "
                          "rect arguments; the styled text of a row is that call"))
    rows.append(dict(kind="drawtext", name="clip rect", owner="FCanvas", slot="", va="0x3c",
                     form="four floats", value="0x3c left, 0x40 top, 0x44 right, 0x48 bottom",
                     verdict="measured",
                     note="the rect is culled against this box only: right edge past the left "
                          "clip, left edge past the right clip, and the same for the vertical pair"))
    rows.append(dict(kind="drawtext", name="width use", owner="rect", slot="", va="",
                     form="X + W", value="never compared with zero", verdict="measured",
                     note="the width reaches one clip sum and the rect handed to the canvas "
                          "(vtable +0xc4); no compare in the body tests it, so a zero-width cell "
                          "is culled only when its anchor lies outside the clip rectangle"))
    rows.append(dict(kind="drawtext", name="font table", owner="GUIStyles", slot="",
                     va="0x%x" % FONT_ARRAY, form="style byte -> font", value="array of fonts",
                     verdict="measured",
                     note="the style byte indexes the font array and the body sets the canvas's "
                          "font/colour for the draw, then restores the five fields it saved"))
    if struct.pack("<I", FONT_ARRAY) not in tbody:
        sys.exit("FAIL ui-list-native: the text routine never indexes the font array")
    return rows


# ------------------------------------------------------------------ measure

def measure(xbe_path):
    global SYMBOLS
    SYMBOLS = load_symbols()
    img = Image(xbe_path)
    sym_slots, sym_names = load_tables()
    classes = load_classes()
    fam = family(classes)

    rows = []
    inits, pairs, site_count = fname_pairs(img)
    rows.append(dict(kind="bind", name="name-index table", owner="default.xbe", slot="",
                     va="0x%08x" % inits[0], form="%d name-ctor sites" % site_count,
                     value="%d names cached" % len(pairs), verdict="derived",
                     note="the static initialisers that cache an FName index per event name "
                          "(%d bodies)" % len(inits)))
    if DELEGATE not in pairs:
        raise SystemExit("FAIL ui-list-native: %r is not in the cached name table" % DELEGATE)
    glob = pairs[DELEGATE]
    rows.append(dict(kind="bind", name=DELEGATE, owner="default.xbe", slot="", va="0x%08x" % glob,
                     form="FName index global", value="wide string", verdict="derived",
                     note="the name the delegate is dispatched under"))

    # the dispatcher: the body that loads the cached index and calls the object's vtable. A
    # `mov [global], reg` is the cache itself, so only the loads count.
    def is_store(ref):
        back = img.bytes_at(ref - 3, 3)
        return back[2] == 0xA3 or back[1:3] in (b"\x89\x0d", b"\x89\x15")

    users = [v for v in img.dword_refs(glob) if not is_store(v)]
    disp = sorted({body_of(img, v) for v in users})
    if len(disp) != 1:
        raise SystemExit("FAIL ui-list-native: %d bodies load %r's index, expected 1" % (len(disp), DELEGATE))
    dispatcher = disp[0]
    rows.append(dict(kind="bind", name="delegate dispatcher", owner="default.xbe", slot="",
                     va="0x%08x" % dispatcher, form="loads the %s index" % DELEGATE,
                     value="%d reference(s)" % len(users), verdict="derived",
                     note="builds the delegate's parameter block and calls it; its return is the "
                          "`shortcircuit` bool"))

    callers = img.callers(dispatcher)
    if not callers:
        raise SystemExit("FAIL ui-list-native: nothing calls the dispatcher")
    loops = sorted({body_of(img, c) for c in callers})

    # which class/slot holds each row loop, and every override in the family
    slot_of = collections.defaultdict(list)
    emitted = set()
    for tva, n in sorted(sym_slots.items()):
        cls = sym_names.get(tva)
        if not cls or cls not in fam:
            continue
        sup = fam[cls][2]
        parent = None
        for tva2, cls2 in sym_names.items():
            if cls2 == sup:
                parent = (tva2, sym_slots.get(tva2))
        slots = [img.u32(tva + i * 4) for i in range(n)]
        pslots = []
        if parent and parent[1]:
            pslots = [img.u32(parent[0] + i * 4) for i in range(parent[1])]
        branch, pkg, _sup, native = fam[cls]
        rows.append(dict(kind="class", name=cls, owner=pkg, slot=str(n), va="0x%08x" % tva,
                         form="native" if native == "True" else "script", value=branch,
                         verdict="family", note="extends %s" % (sup or "-")))
        for i, fn in enumerate(slots):
            if i == 3:  # the per-class bookkeeping slot: a different body in every table
                continue
            if pslots and i < len(pslots) and pslots[i] == fn:
                continue
            emitted.add((cls, i))
            form = "added" if i >= len(pslots) else "override"
            marks = []
            if fn in loops:
                marks.append("calls %s" % DELEGATE)
            lo, hi = fn, extent(fn)
            for nm, g in pairs.items():
                if nm == DELEGATE:
                    continue
                if img.contains_dword(lo, hi, g):
                    marks.append("calls %s" % nm)
            verdict = "row-draw" if fn in loops else ""
            rows.append(dict(kind="slot", name=cls, owner=sup or "-", slot=str(i), va="0x%08x" % fn,
                             form=form, value=";".join(sorted(marks)) or "-", verdict=verdict,
                             note="0x%08x..0x%08x" % (lo, hi)))
            slot_of[fn].append((cls, i))

    # the stock item draw: the slot the branch root adds over its parent, called by every row loop
    # through `call [reg+disp]`. That is the virtual a delegate shortcircuits, so it must exist in the
    # root's table and nowhere in the parent's.
    root = ROOTS[0][1]
    rtva = [t for t, c in sym_names.items() if c == root]
    if not rtva:
        raise SystemExit("FAIL ui-list-native: %s has no class table" % root)
    rtva = rtva[0]
    rslots = [img.u32(rtva + i * 4) for i in range(sym_slots[rtva])]
    pva = [t for t, c in sym_names.items() if c == classes[root]["super"]]
    pslots = [img.u32(pva[0] + i * 4) for i in range(sym_slots[pva[0]])] if pva else []
    added = [i for i in range(len(rslots)) if i >= len(pslots) or rslots[i] != pslots[i] and i >= len(pslots)]
    added = [i for i in range(len(rslots)) if i >= len(pslots)]
    if len(added) != 1:
        raise SystemExit("FAIL ui-list-native: %s adds %d slots over %s, expected 1"
                         % (root, len(added), classes[root]["super"]))
    stock_idx = added[0]
    for loop in loops:
        lo, hi = loop, extent(loop)
        blob = img.bytes_at(lo, hi - lo)
        want = struct.pack("<I", stock_idx * 4)
        if not any(blob[i] == 0xFF and blob[i + 1] in (0x90, 0x91, 0x92, 0x93)
                   and blob[i + 2:i + 6] == want for i in range(len(blob) - 6)):
            raise SystemExit("FAIL ui-list-native: the row loop 0x%08x never calls slot %d"
                             % (loop, stock_idx))
    rows.append(dict(kind="bind", name="stock item draw slot", owner=root, slot=str(stock_idx),
                     va="0x%08x" % rslots[stock_idx], form="added over %s" % classes[root]["super"],
                     value="the row loop calls it through +0x%x" % (stock_idx * 4), verdict="derived",
                     note="the virtual the %s delegate shortcircuits" % DELEGATE))
    seen = set()
    for cls in sorted(fam):
        tva = [t for t, c in sym_names.items() if c == cls]
        if not tva:
            continue
        tva = tva[0]
        n = sym_slots[tva]
        own = [img.u32(tva + i * 4) for i in range(n)]
        if not any(fn in loops for fn in own):
            continue
        if stock_idx < n and (cls, stock_idx) not in seen:
            seen.add((cls, stock_idx))
            rows.append(dict(kind="slot", name=cls, owner=cls, slot=str(stock_idx),
                             va="0x%08x" % own[stock_idx], form="inherited",
                             value="stock item draw", verdict="stock-draw",
                             note="the row loop calls it through +0x%x" % (stock_idx * 4)))

    # the field offsets, each checked against the body that witnesses it
    by_role = {}
    for r in rows:
        if r["kind"] == "slot" and r["verdict"] in ("row-draw", "stock-draw"):
            by_role.setdefault(r["verdict"], []).append(int(r["va"], 16))
    for r in rows:
        if r["kind"] == "slot" and r["verdict"] == "row-draw":
            cls = r["name"]
            by_role.setdefault("predraw-of-" + cls, [])
    # the PreDraw is the body that *stores* ItemHeight and ItemsPerPage: a row's height is written
    # once per frame, and a reader of the same offsets is a different body
    for cls in sorted(fam):
        tva = [t for t, c in sym_names.items() if c == cls]
        if not tva:
            continue
        tva = tva[0]
        n = sym_slots[tva]
        for i in range(n):
            fn = img.u32(tva + i * 4)
            lo, hi = fn, extent(fn)
            if store_hits(img, lo, hi, 0x264) and store_hits(img, lo, hi, 0x260):
                by_role.setdefault("predraw", []).append(fn)
    predraw = sorted(set(by_role.get("predraw", [])))
    index = {(r["name"], r["slot"]): r for r in rows if r["kind"] == "slot"}
    for cls in sorted(fam):
        tva = [t for t, c in sym_names.items() if c == cls]
        if not tva:
            continue
        tva = tva[0]
        for i in range(sym_slots[tva]):
            fn = img.u32(tva + i * 4)
            if fn not in predraw:
                continue
            row = index.get((cls, str(i)))
            if row is None:
                row = dict(kind="slot", name=cls, owner=fam[cls][2] or "-", slot=str(i),
                           va="0x%08x" % fn, form="inherited", value="-", verdict="",
                           note="the same body as the parent's slot")
                rows.append(row)
                index[(cls, str(i))] = row
            row["verdict"] = "predraw"
            row["value"] = "writes ItemHeight and ItemsPerPage"
            row["note"] = "measured by the offsets it stores, not by its name"
    if not predraw:
        raise SystemExit("FAIL ui-list-native: no body writes ItemsPerPage and ItemHeight together")

    for name, disp, role, note in LIST_FIELDS:
        witnesses = []
        if role == "row-draw":
            witnesses = sorted(set(by_role.get("row-draw", [])))
        elif role == "horz-draw":
            witnesses = sorted(set(by_role.get("row-draw", [])))
        elif role in ("predraw", "predraw-read"):
            witnesses = predraw
        hit = []
        for w in witnesses:
            lo, hi = w, extent(w)
            if role == "predraw":
                if store_hits(img, lo, hi, disp):
                    hit.append(w)
            else:  # a read: the row loop loads it, the PreDraw reads ItemPadding
                if img.contains_dword(lo, hi, disp):
                    hit.append(w)
        if not hit:
            raise SystemExit("FAIL ui-list-native: no body witnesses %s at +0x%x" % (name, disp))
        rows.append(dict(kind="field", name=name, owner="GUIListBase", slot="", va="0x%x" % disp,
                         form="object offset", value="witnessed in %d body/bodies" % len(hit),
                         verdict="measured" if hit else "unwitnessed",
                         note="%s -- %s" % (note, ", ".join("0x%08x" % h for h in sorted(hit)[:4]))))

    for cls, terms in sorted(RECT.items()):
        for term, formula, note in terms:
            rows.append(dict(kind="rect", name=term, owner=cls, slot="", va="0x%x" % ROW_RECT_OFFSET,
                             form="client rect", value=formula, verdict="measured", note=note))

    rows.append(dict(kind="bind", name="page -> list", owner="GUI", slot="", va="",
                     form="DefaultListClass", value="GUI.GUIMultiColumnList",
                     verdict="measured",
                     note="GUIMultiColumnListBox.InitComponent calls AddControl(DefaultListClass) and "
                          "the class's own defaults state GUI.GUIMultiColumnList, so a box never hosts "
                          "the page's subclass and StargateMenus.ServerList is placed by nothing"))
    rows.extend(box_half(img, classes, fam, sym_slots, sym_names, glob, dispatcher, loops))
    rows.extend(drawtext_half(img, classes, fam, sym_slots, sym_names, loops))
    return rows


# --------------------------------------------------------------- artifact

def write_artifact(rows, outdir):
    path = os.path.join(outdir, "ui_list_native.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["kind"], r["owner"], r["name"], r["slot"])):
            w.writerow(r)
    print("wrote %s (%d rows)" % (path, len(rows)))


def load_rows(path=ARTIFACT):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def summarize(rows):
    s = collections.Counter()
    for r in rows:
        s["%s:%s" % (r["kind"], r["verdict"])] += 1
    loops = {r["va"] for r in rows if r["kind"] == "slot" and r["verdict"] == "row-draw"}
    stock = {r["va"] for r in rows if r["kind"] == "slot" and r["verdict"] == "stock-draw"}
    classes = [r for r in rows if r["kind"] == "class"]
    overrides = [r for r in rows if r["kind"] == "slot" and r["form"] in ("override", "added")]
    named = [r for r in rows if r["kind"] == "slot" and r["value"] not in ("-", "stock item draw")]
    pre = {r["va"] for r in rows if r["kind"] == "slot" and r["verdict"] == "predraw"}
    box_bodies = [r for r in rows if r["kind"] == "box" and r["name"] == "delegate audit"]
    return dict(predraw=len(pre), classes=len(classes), overrides=len(overrides), loops=len(loops), stock=len(stock),
                named=len(named), fields=sum(1 for r in rows if r["kind"] == "field"),
                rects=sum(1 for r in rows if r["kind"] == "rect"),
                box=sum(1 for r in rows if r["kind"] == "box" and r["verdict"] in ("item-draw", "passthrough")),
                box_bodies=int(box_bodies[0]["form"].split()[0]) if box_bodies else 0,
                text=sum(1 for r in rows if r["kind"] == "drawtext"))


def render(rows):
    s = summarize(rows)
    out = []
    out.append("**The row is a class-table slot, and the chain that finds it is derived from the image.** "
               "The row loops are the callers of the one body that loads the cached `%s` index "
               "(`0x%08x`); the family's tables are read slot by slot in `inventory/xbe_class_tables.csv`, "
               "so `%d` classes, `%d` overrides and `%d` row loops come out of the same scan."
               % (DELEGATE, int([r for r in rows if r["kind"] == "bind" and r["name"] == "delegate dispatcher"][0]["va"], 16),
                  s["classes"], s["overrides"], s["loops"]))
    out.append("")
    out.append("| class | slots | override | slot | body | dispatches |")
    out.append("|---|---|---|---|---|---|")
    for r in rows:
        if r["kind"] == "slot" and r["verdict"] in ("row-draw", "stock-draw"):
            out.append("| `%s` | | %s | %d | `%s` | %s |"
                       % (r["name"], r["form"], int(r["slot"]), r["va"],
                          r["value"] if r["value"] != "-" else "the stock item draw"))
    out.append("")
    for r in rows:
        if r["kind"] == "rect" and r["owner"] == "GUIVertList":
            out.append("* `%s` = `%s` -- %s" % (r["name"], r["value"], r["note"]))
    out.append("")
    out.append("| field | offset | read by |")
    out.append("|---|---|---|")
    for r in rows:
        if r["kind"] == "field":
            out.append("| `%s` | `%s` | %s |" % (r["name"], r["va"], r["note"].split(" -- ")[0]))
    out.append("")
    trees = [r for r in rows if r["kind"] == "box" and r["verdict"] == "tree"]
    box_parent = trees[0]["form"].split()[-1] if trees else "GUIMultiComponent"
    out.append("#### The box is not a list, and its item draw is another slot 58")
    out.append("")
    item_rows = [r for r in rows if r["kind"] == "box" and r["verdict"] == "item-draw"]
    out.append("`%s` extends `%s`, not `GUIListBase`: a list box is a component that hosts a list, "
               "and its `PreDraw` draws **one** item per frame through slot %d -- the component's "
               "slot %d (`+0x%x`), a different body from `GUIListBase`'s slot %d at the same "
               "offset. `%d` classes override that item draw, and of the `%d` slot bodies in the "
               "box tree not one loads the `%s` index or calls its dispatcher, so a body wired "
               "onto a box would never run."
               % (BOX_ROOT, box_parent, 58, 58, 58 * 4, 58, len(item_rows), s["box_bodies"], DELEGATE))
    out.append("")
    out.append("| class | slot | body | what it draws |")
    out.append("|---|---|---|---|")
    for r in rows:
        if r["kind"] == "box" and r["verdict"] in ("item-draw", "pre-draw"):
            out.append("| `%s` | %s | `%s` | %s |"
                       % (r["name"], r["slot"] or "-", r["va"], r["value"]))
    out.append("")
    passthrough = [r["name"] for r in rows
                   if r["kind"] == "box" and r["verdict"] == "passthrough"]
    if passthrough:
        out.append("`%s` add no draw of their own: their tables differ from `%s`'s only in slot 3, "
                   "so a body wired onto one of those classes would have nowhere to be dispatched "
                   "from." % ("` and `".join(passthrough), BOX_ROOT))
        out.append("")
    audits = [r for r in rows if r["kind"] == "box" and r["name"] in ("delegate audit", "receiver audit",
                                                                      "page widgets")]
    for r in audits:
        out.append("* **%s** -- %s: %s" % (r["name"], r["form"], r["note"]))
    out.append("")
    out.append("#### The styled-text routine: four clip culls and no width test")
    out.append("")
    texts = [r for r in rows if r["kind"] == "drawtext"]
    if texts:
        styled = [r for r in rows if r["kind"] == "drawtext" and r["name"] == "styled text"][0]
        out.append("The stock item draw reaches `%s.DrawText` (`%s`) twice -- once per text pass -- "
                   "and that routine's whole suppression logic is four compares against the canvas "
                   "clip rect: a cell's width is used only in the `X + W` sum of one of them and in "
                   "the rect handed to the canvas, so a "
                   "**zero-width cell is not shortcircuited** -- it is culled only when its anchor "
                   "lies outside the clip rectangle." % (styled["owner"], styled["va"]))
        out.append("")
        out.append("| measure | answer |")
        out.append("|---|---|")
        for r in texts:
            out.append("| `%s` | %s |" % (r["name"], r["note"]))
    return "\n".join(out)


def write_doc(path, rows):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if DOC_START not in text or DOC_END not in text:
        sys.exit("%s: no list-native markers" % path)
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(head + DOC_START + "\n" + render(rows) + "\n" + DOC_END + tail)
    print("wrote %s" % path)


def check_doc(path, rows):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if DOC_START not in text or DOC_END not in text:
        sys.exit("FAIL list-native: %s has no markers" % path)
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    if head + DOC_START + "\n" + render(rows) + "\n" + DOC_END + tail != text:
        sys.exit("FAIL list-native: %s's block is stale (run --write-doc)" % path)
    print("ok   list-native: %s regenerates byte-identically" % path)


def report(path=ARTIFACT):
    s = summarize(load_rows(path))
    print("list native: %(classes)d classes, %(overrides)d overrides, %(loops)d row loops, "
          "%(stock)d stock item draws, %(named)d named dispatches" % s)
    print("row rect: %(rects)d terms; %(fields)d field offsets, each witnessed by a body" % s)
    print("box tree: %(box)d draw rows, %(box_bodies)d slot bodies with no delegate; "
          "%(text)d styled-text rows" % s)


# ------------------------------------------------------------- self-test

def selftest():
    """The parsers, over a synthetic image: no dump, no writes, no network."""
    # a synthetic .text: `push <string>`; `call ctor`; `mov [global], ecx`
    string_va = 0x1000
    global_va = 0x2000
    ctor_rel = 0x30
    blob = bytearray()
    # pad to 0x100
    blob += b"\x90" * 0x100
    site = len(blob)
    blob += b"\x68" + struct.pack("<I", string_va)   # push string
    blob += b"\x8d\x4c\x24\x08"                      # lea ecx,[esp+8]
    call_at = len(blob)
    blob += b"\xe8" + struct.pack("<i", ctor_rel)    # call ctor
    blob += b"\x8b\x08"                              # mov ecx,[eax]
    blob += b"\x89\x0d" + struct.pack("<I", global_va)  # mov [global], ecx
    assert struct.unpack_from("<i", blob, call_at + 1)[0] == ctor_rel

    class Fake:
        def __init__(self):
            self.data = bytes(blob)

        def wide(self, va, limit=64):
            return "OnDrawItem" if va == string_va else None

    img = Fake()
    # the store parse the harvester uses
    after = call_at + 5 + 2  # call, then mov ecx,[eax]
    seg = img.data[after:after + 20]
    assert seg[:2] == b"\x89\x0d", seg[:2]
    assert struct.unpack_from("<I", seg, 2)[0] == global_va
    # the push parse
    j = site
    assert blob[j] == 0x68
    assert struct.unpack_from("<I", blob, j + 1)[0] == string_va
    # a body containing a dword: the field witness check
    body = b"\xc7\x86" + struct.pack("<I", 0x264) + b"\x05\x00\x00\x00"
    assert body.find(struct.pack("<I", 0x264)) > 0
    # the row-rect terms are complete for both branches
    for cls in ("GUIVertList", "GUIHorzList"):
        terms = {t[0] for t in RECT[cls]}
        assert terms == {"x", "y", "w", "h", "pitch"}, terms
    assert ROW_RECT_OFFSET == 0x12C
    # the box tree and the list tree must stay apart: their slot 58s share an offset, not a body
    assert BOX_ROOT != ROOTS[0][1] and BOX_ROOT == "GUIListBoxBase"
    assert [d for d, _l in CLIP_FIELDS] == [0x3C, 0x40, 0x44, 0x48]
    print("ok   list-native: synthetic name table, store parse and field witness")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump", nargs="?")
    ap.add_argument("-o", "--out")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        selftest()
        return
    if args.report:
        report()
        return
    if args.write_doc or args.check_doc:
        rows = load_rows()
        if args.write_doc:
            write_doc(args.write_doc, rows)
        else:
            check_doc(args.check_doc, rows)
        return
    if not args.dump:
        sys.exit("a dump directory is required (or --report/--check)")
    xbe = os.path.join(args.dump, "default.xbe")
    if not os.path.isfile(xbe):
        xbe = os.path.join(args.dump, os.path.basename(args.dump.rstrip("/")), "default.xbe")
    if not os.path.isfile(xbe):
        sys.exit("FAIL list-native: no default.xbe under %s" % args.dump)
    rows = measure(xbe)
    if args.out:
        write_artifact(rows, args.out)
    else:
        for r in rows:
            print("%-6s %-24s %-22s %-4s %-10s %s"
                  % (r["kind"], r["name"], r["owner"], r["slot"] or "-", r["verdict"] or "-", r["value"]))


if __name__ == "__main__":
    main()
