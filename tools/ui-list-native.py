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
    return dict(predraw=len(pre), classes=len(classes), overrides=len(overrides), loops=len(loops), stock=len(stock),
                named=len(named), fields=sum(1 for r in rows if r["kind"] == "field"),
                rects=sum(1 for r in rows if r["kind"] == "rect"))


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
