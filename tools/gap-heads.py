#!/usr/bin/env python3
"""The map's gaps, censused: every head a pad-before plus prologue test finds, and what names it.

`symbols/xbox_functions.csv` covers 3,445,170 of `.text`'s 4,227,324 bytes, so **783,490 bytes sit in
9,987 gaps** between consecutive rows. Only two things were ever read inside them: the 84 installer tail
jumps (`tools/native-island-gaps.py`) and the bodies a *table* names (`native-census.py`). The rest was
described, not measured -- the previous bundle's note guessed "~1,800 decodable heads, mostly one-`ret`
islands inside data and alignment". This tool replaces the guess with a census and a control.

A head is an address in a gap that satisfies two things a function start satisfies in this build:

* **a boundary in front of it** -- a run of `0xCC`/`0x90` fill, which is how MSVC separates bodies here,
  or the gap's own start (the row above hands over). A head inside a run whose predecessor is not a
  pad is not read: the bytes before it are code, so it is a block of that code, not a boundary.
* **an MSVC prologue shape** -- the same table `tools/native-census.py` classifies the 883 registration
  bodies with (`8b ff` hotpatch, `55 8b ec` frame, `64 a1` SEH, an argument load, `sub esp`, `ret imm16`,
  a push, `mov eax`), plus `EXTRA_PROLOGUES`: the openings that table does not name but that open the
  map's own rows far more often than they open pad-bounded bytes in the data sections (`a1 <imm32>`,
  `8b 0d <imm32>`, `8b 41 <disp8>`, `8b c1`, ...). Each one's two counts are in the artifact's census
  row, so the extension is a measurement and not a loosening of the test.

A head becomes a **body** when a linear decode from it closes on an unconditional terminator *with a tail
behind it* (`native-census.sweep_body`, whose five tails are `pad`/`edge`/`align`/`table`/`bytes`). It is
an **island** when that close is one or two instructions or four bytes (the `33 c0 c2 08 00` stub a vtable
slot holds); `no-close` and `undecoded` are the refusals.

**The two tests are not one rule, and the map's own rows measure them apart.** Over the 12,918 rows in
`.text`, 9,218 pass both, **1,528 have the fill but an opening neither table names** (the prologue test's
miss) and **1,753 have the opening but no fill in front** (the boundary test's miss); 419 have neither.
The boundary's miss is not a hole in the rule: 1,674 of those 1,753 rows are the successor of a
terminator, which is exactly what the split arm reads (a terminator whose successor no branch in the run
targets) -- so the bodies behind code are found when their opening is one the prologue test accepts, and
`8b c1` was the one it did not.

The census is only worth quoting with its controls, and they are measured in the same run:

* **precision, from a null population.** The same rule over 3.2 MB of the image's *data* sections
  (`.rdata`, `.data`, XNET, DSOUND) finds pad-before prologue-shaped bytes and **zero** bodies: a decode
  from a prologue in data does not close on a terminator with a tail. The gaps yield 3.1 bodies/KB.
* **recall, on the population that is known to be bodies.** The same two tests over the 11,784 map rows in
  `.text` pass on 9,640 (81.8%), so the rule is a **lower bound**: 1,833 rows start behind a pad on an
  opening the prologue table does not name (an XDK library body's `mov ecx,<global>`, most of them).
* **the naming channel's own base rate.** A dword outside `.text` names a *random* gap byte 1.1% of the
  time (22 of 2,000) and a map row's start 24.1% (481 of 2,000), so "a pointer names this head" is
  evidence only against those numbers, not on its own.

The last axis is what the head rule cannot answer: a body that nothing names is still a body. Each head
therefore carries the four channels that reach one -- an `E8` call, an `E9` jump, a `68` push, and a dword
outside `.text` (with its class-table slot resolved when the ref lands in one, mapped or unmapped).

    tools/gap-heads.py --dump "$SG1_DUMP" -o inventory     # measure (needs the image)
    tools/gap-heads.py --report                            # the table, from the artifact
    tools/gap-heads.py --write-doc docs/SYMBOLS.md
    tools/gap-heads.py --check-doc docs/SYMBOLS.md
    tools/gap-heads.py --check                             # dump-free gate (in `make ci`)
    tools/gap-heads.py --selftest                          # synthetic bodies, no dump

**A body that abuts its neighbour with no fill merges into it, and the split rule undoes that.** A
terminator with code behind it used to be walked past: the code could be an early `return` in the same
body or the neighbour's start with no fill between them, and only a branch *to* that code can tell them
apart. `branch_target` reads every relative branch in the run, so a terminator whose next instruction no
branch targets is a **boundary** (`tail=split`, `boundary=after-ret`) and the walk starts a new body
there; one the run's own branches reach stays merged and is counted in `inner_starts` (`0x00046330`'s
`execEqualEqual_ObjectObject` returns early and is rejoined by the `jcc` above it). `0x00418c30` is
five 21-byte bodies that decode as one 131-byte run without the rule.

**A split can also be a jump table's case blocks, and the table is the evidence.** A `switch` compiles to
`jmp [eax*4+<table>]` with the case blocks laid out behind it and the table inline in `.text`, so the case
blocks are reached by an *indirect* branch that `branch_target` cannot read -- every one of them looks like
"a terminator whose successor no branch targets". Reading the table is the missing test: a `.text` dword that
points into `.text` and sits in a run of three or more of them is a jump-table entry, and an opening one of
those names is a case block of the body above it, not a neighbour. **In this build there are 144 such runs
holding 1,642 entries**; over the map's own 12,321 rows the relative rule alone reads **59 rows as two
bodies**, and of their **385** split points **351 (91%)** are entries of a `.text` table -- so those 59 rows
are `switch` dispatches, not two-body rows, and a `resize` would cut a function at its own case block. The
same channel over the gap population names **0 of its 172 split heads**, which is what makes the two
populations different rather than a matter of taste.

**One gap is a generated family, not missed code.** `0x004100c8`..`0x00418cc0` holds 600 bodies in two
compiler shapes: a **register-once thunk** (`push <&item>; push 0xff; call 0x46c60; add esp,8; mov
<flag>,al; ret`, one distinct `.data` item and one distinct `al` flag byte each) and an **`_atexit`
destructor registration** (`push <dtor>; call _atexit; pop ecx; ret`). The `family` column names the
shape so the block is one story with one naming rule, not 600 unrelated bodies.

`--emit-adds` prints the `add` rows (`symbols/xbe_boundaries.csv`) the population earns and
`--apply-adds` writes them plus the `xbe_overrides.csv` creation row each needs, named
`<Class>_vt<slot>` from the slot that names it -- never an address, because that is the evidence.

The dump is never committed; the artifact and the page are. Addresses and counts only.
"""
import argparse
import bisect
import collections
import csv
import importlib.util
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

ARTIFACT = os.path.join(ROOT, "inventory", "gap_heads.csv")
FUNCTIONS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
NAMES = os.path.join(ROOT, "inventory", "xbe_class_table_names.csv")
BOUNDARIES = os.path.join(ROOT, "symbols", "xbe_boundaries.csv")
OVERRIDES = os.path.join(ROOT, "symbols", "xbe_overrides.csv")
DOC = os.path.join(ROOT, "docs", "SYMBOLS.md")
DOC_START = "<!-- generated:gap-heads -->"
DOC_END = "<!-- /generated:gap-heads -->"
GAME = "Stargate_SG-1_-_The_Alliance-2005-12-15"

COLS = ["row", "head_va", "gap_lo", "gap_hi", "gap_size", "boundary", "prologue", "verdict", "size",
        "insns", "close", "tail", "inner_starts", "calls", "jumps", "imms", "pointers", "slots",
        "slot_table", "slot_class", "slot_index", "family", "census", "name", "row_size", "family_item"]

# A tail shape's name as `native-census.tail_shape` writes it; the five kinds, concatenated with `+`.
# `split` is this tool's own: the bytes behind the terminator are the next body, not a tail.
TAILS = ("edge", "pad", "align", "table", "bytes", "split")
# The two generated shapes one gap holds, named by the instruction pattern that opens the body.
FAMILIES = ("register-once", "atexit")


_MODS = {}


def _load(name):
    """Import a sibling tool once: `strong_opening` asks for the prologue table at every terminator."""
    if name not in _MODS:
        spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MODS[name] = mod
    return _MODS[name]


def _art(path=ARTIFACT):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- the map's gaps
def map_table(funcs):
    """[(start, end, name)] sorted, from `symbols/xbox_functions.csv`."""
    out = [(int(f["address"], 16), int(f["address"], 16) + int(f.get("size") or 0), f.get("name") or "")
           for f in funcs]
    out.sort()
    return out


def gaps_of(table, lo, hi):
    """[(gap_lo, gap_hi)] -- the bytes of `[lo, hi)` no row covers, in order."""
    rows = [t for t in table if lo <= t[0] < hi]
    if not rows:
        return []
    out, prev_end = [], rows[0][1]
    for a, b, _n in rows[1:]:
        if a > prev_end:
            out.append((prev_end, a))
        prev_end = max(prev_end, b)
    if prev_end < hi:
        out.append((prev_end, hi))
    return out


# --------------------------------------------------------------------------- the head rule
# The prefix bytes the decoder accepts in front of an opcode; `branch_target` skips them to find it.
PREFIXES = (0x66, 0x67, 0xF0, 0xF2, 0xF3, 0x2E, 0x36, 0x3E, 0x26, 0x64, 0x65)
LOOPS = (0xE0, 0xE1, 0xE2, 0xE3)
# The inline jump tables `measure()` indexes: {"targets": set(va), "runs": {table_va: entries}}.
# A `switch` reaches its case blocks through `jmp [reg*4+<table>]`, which no relative-branch read sees.
_TABLE = {"targets": set(), "runs": {}}


def text_tables(text, lo):
    """(targets, runs) for the jump tables `.text` holds inline: aligned runs of >=3 VAs into `.text`.

    `branch_target` reads relative branches only, so a `switch`'s case blocks look like code that nothing
    reaches. MSVC 2005 lays the table out in `.text` right behind the dispatch, as consecutive 4-byte
    addresses, so a run of three or more aligned dwords that all point into `.text` is a table and every
    entry is a case block. The run length is what separates a table from a code immediate that happens to
    hold an address: a single `push 0x401000` is not a run. The cost of the rule is measured, not assumed
    -- 2 of the census's 1,892 one-body gap bodies sit in such a run, so a false entry can merge at most
    those.
    """
    flags = bytearray(len(text) // 4)
    vals = [0] * (len(text) // 4)
    for off in range(0, len(text) - 3, 4):
        v = struct.unpack_from("<I", text, off)[0]
        vals[off // 4] = v
        flags[off // 4] = 1 if lo <= v < lo + len(text) else 0
    targets, runs, i, m = set(), {}, 0, len(flags)
    while i < m:
        if not flags[i]:
            i += 1
            continue
        j = i
        while j < m and flags[j]:
            j += 1
        if j - i >= 3:
            runs[lo + 4 * i] = j - i
            for k in range(i, j):
                targets.add(vals[k])
        i = j
    return targets, runs



def sx8(v):
    return v - 0x100 if v >= 0x80 else v


def branch_target(blob, i, length):
    """The offset in `blob` a *relative* branch at `i` targets, or None.

    A run's own branches are the only evidence that separates an early `return` from a neighbour's
    start: the code behind a terminator is reachable -- same body -- when one of the run's jumps lands
    on it, and is a body of its own when nothing does. `E9`/`EB`/every `jcc`/`loop`/`jcxz` carry a
    displacement in their last byte(s), which is all this reads; everything else returns None.
    """
    if i < 0 or i + length > len(blob) or length < 2:
        return None
    k = i
    while k < len(blob) and blob[k] in PREFIXES:
        k += 1
    if k >= len(blob):
        return None
    op = blob[k]
    end = i + length
    if 0x70 <= op <= 0x7F or op in LOOPS:
        return end + sx8(blob[end - 1])
    if op == 0x0F and k + 1 < len(blob) and 0x80 <= blob[k + 1] <= 0x8F:
        return end + struct.unpack_from("<i", blob, end - 4)[0]
    if op in (0xE9, 0xEB):
        return end + (sx8(blob[end - 1]) if op == 0xEB else struct.unpack_from("<i", blob, end - 4)[0])
    return None


def walk(decode, tail_shape, terminator_kind, pro, blob, limit, va, table=None):
    """(size, insns, close, tail, inner, split) for the body at `blob[0]`, or a refusal.

    The run is decoded twice: once to its close (the same close-with-a-tail the registration census
    takes) or to the region's end, collecting every instruction and every relative branch target;
    then once over the terminators *inside* it, where the first one whose successor no branch targets
    and which opens a body (`strong_opening`) is a split point. An opening a branch does target is an
    early `return` rejoined by its own code, counted in `inner` and walked past. `table` adds the
    channel a relative read cannot see: a jump table's entries are case blocks of the body above them,
    so an opening one of them names rejoins the body too.
    """
    insns, targets, i, close, shape, close_start, close_len = [], set(), 0, None, None, None, None
    refused = False
    while i < limit:
        ins = decode(blob[i:limit], 0)
        if ins is None or ins.length <= 0:
            refused = True
            break
        insns.append((i, ins.length, ins.text))
        t = branch_target(blob, i, ins.length)
        if t is not None:
            targets.add(t)
        kind = terminator_kind(blob, i, ins.text)
        i += ins.length
        if kind:
            sh = tail_shape(blob, i, limit, va)
            if sh:
                close, shape, close_start, close_len = kind, sh, i - ins.length, ins.length
                break
    # The split: a terminator behind which a body opens and to which nothing in the run branches.
    split, inner, split_close = None, 0, None
    for start, length, text in insns:
        if close_start is not None and start >= close_start:
            break
        kind = terminator_kind(blob, start, text)
        if not kind:
            continue
        nxt = start + length
        if nxt >= limit:
            continue
        if nxt in targets or (table and (va + nxt) in table):
            if strong_opening(pro, blob, nxt):
                inner += 1
            continue
        if strong_opening(pro, blob, nxt):
            split, split_close = nxt, kind
            break
    # A split is only read inside a run that closes on a *tail* somewhere: that close is the
    # structural evidence the region is code, and without it a decode through data would "close" at
    # the first `ret`-shaped byte in front of a `mov` -- the control that keeps this rule honest
    # (0 bodies in 3.3 MB of data) depends on it.
    if split is not None and close is not None:
        return split, sum(1 for s, _l, _t in insns if s < split), split_close, "split", inner, split
    if close is not None:
        return close_start + close_len, sum(1 for s, _l, _t in insns if s <= close_start), close, shape, inner, None
    return None, len(insns), "undecoded" if refused else "no-close", None, inner, None


def strong_opening(pro, blob, i):
    """Does the instruction at `i` open a body: `mov r,imm32`, `push imm32`, a call, or a prologue?"""
    if i >= len(blob):
        return False
    op = blob[i]
    if 0xB8 <= op <= 0xBF or op == 0x68 or op == 0xE8:
        return True
    return pro(blob[i:i + 8]) != "other"


# The openings `native-census.PROLOGUES` does not name, measured on the map's own rows: each pattern
# below opens rows in `symbols/xbox_functions.csv` (a known body start) at a rate its pad-bounded
# occurrences in the four data sections do not support. `(pattern, name, rows, data)` -- the two counts
# are re-measured every run and land in the census row, so this table is evidence, not a judgement call.
EXTRA_PROLOGUES = (
    (b"\xa1", "global-load-eax", 841, 8),          # mov eax, ds:[imm32]
    (b"\x8b\x0d", "global-load-ecx", 77, 0),       # mov ecx, [imm32] -- the XDK library's opening
    (b"\x8b\x15", "global-load-edx", 21, 0),
    (b"\x8b\xc1", "self-return", 85, 0),           # mov eax, ecx -- the thiscall thunk / ctor opening
    (b"\x8b\x41", "getter-disp8", 82, 0),          # mov eax, [ecx+disp8] -- a field getter
    (b"\x8b\x81", "getter-disp32", 47, 0),
    (b"\xd9\x44\x24", "fpu-arg", 73, 0),           # fld dword [esp+n]
    (b"\xf3\x0f\x10", "sse-load", 48, 0),          # movss xmm0, ...
    (b"\x33\xc0", "zero-return", 42, 1),           # xor eax, eax
    (b"\x83\xc1", "add-reg-imm8", 26, 0),
    (b"\xff\x74", "push-arg", 25, 0),              # push dword [esp+n]
    (b"\xc3", "bare-ret", 20, 0),                  # the shared table names `c2 imm16`, not a bare `ret`
)
EXTRA_NAMES = [name for _p, name, _r, _d in EXTRA_PROLOGUES]


def prologue(nc, first):
    """The shared table's name for an opening, else one of this census's measured ones, else `other`."""
    name = nc.prologue(first)
    if name != "other":
        return name
    for pat, label, _rows, _data in EXTRA_PROLOGUES:
        if first.startswith(pat):
            return label
    return "other"


def scan(decode, nc, lo, hi, blob, table=None):
    """Every head in `blob` between `lo` and `hi`: heads, walked from the region's first byte."""
    pro = lambda head: prologue(nc, head)
    out, off, first, forced = [], 0, True, set()
    while off < len(blob):
        pad = 0
        k = off - 1
        while k >= 0 and blob[k] in (0xCC, 0x90):
            pad += 1
            k -= 1
        preceded = first or off in forced or (off >= 1 and blob[off - 1] in (0xCC, 0x90))
        if preceded:
            pr = pro(blob[off:off + 8])
            # A split point is a body even when its opening is not one of the named prologues: the
            # terminator in front of it and the absence of a branch into it are the evidence.
            if pr != "other" or off in forced:
                size, n, close, tail, inner, split = walk(decode, nc.tail_shape, nc.terminator_kind,
                                                          pro, blob[off:], len(blob) - off, lo + off,
                                                          table)
                if off in forced:
                    boundary = "after-ret"
                else:
                    boundary = "start" if first and not pad else "pad:%d" % pad
                out.append({"head_va": lo + off, "boundary": boundary,
                            "prologue": pr, "size": size, "insns": n, "close": close, "tail": tail,
                            "inner_starts": inner})
                if split is not None:
                    forced.add(off + split)
                off += (size if size else 1)
                first = False
                continue
        first = False
        off += 1
    return out


def verdict(h):
    """`body`, `island`, or the refusal -- the three populations the page counts."""
    if not h["size"]:
        return "undecoded" if h["close"] == "undecoded" else "no-close"
    if h["insns"] <= 2 or h["size"] <= 4:
        return "island"
    return "body"


# --------------------------------------------------------------------------- the reference channels
def channels(xbe, raw, secs, blobs, tva, heads, spans, names):
    """Fill calls/jumps/imms/pointers/slots on every head, then report the per-channel counts."""
    body_vas = {h["head_va"] for h in heads}
    calls, jumps = collections.Counter(), collections.Counter()
    text = blobs[".text"]
    for i in range(len(text) - 4):
        op = text[i]
        if op in (0xE8, 0xE9):
            rel = struct.unpack("<i", text[i + 1:i + 5])[0]
            t = tva + i + 5 + rel
            if t in body_vas:
                (calls if op == 0xE8 else jumps)[t] += 1
    imms = set()
    for i in range(len(text) - 5):
        if text[i] == 0x68:
            t = struct.unpack("<I", text[i + 1:i + 5])[0]
            if t in body_vas:
                imms.add(t)
    outside = b"".join(blobs[s] for s in secs)
    starts = [s[0] for s in spans]
    count = collections.Counter()
    for h in heads:
        va = h["head_va"]
        h["calls"] = calls.get(va, 0)
        h["jumps"] = jumps.get(va, 0)
        h["imms"] = 1 if va in imms else 0
        pat = struct.pack("<I", va)
        refs, pos, slot = [], outside.find(pat), None
        while pos != -1 and pos + 4 <= len(outside):
            refs.append(pos)
            pos = outside.find(pat, pos + 1)
        h["pointers"] = len(refs)
        h["slots"], h["slot_table"], h["slot_class"], h["slot_index"] = 0, "", "", ""
        for o in refs:
            ref = off_to_va(secs, o)
            i = bisect.bisect_right(starts, ref) - 1
            if i < 0:
                continue
            tva_, sn, kind = spans[i]
            if tva_ <= ref < tva_ + 4 * sn:
                h["slots"] += 1
                if slot is None:
                    slot = ("0x%08x" % tva_, names.get(tva_, ""), (ref - tva_) // 4)
        if slot:
            h["slot_table"], h["slot_class"], h["slot_index"] = slot
        count["pointer"] += 1 if refs else 0
        count["slot"] += 1 if h["slots"] else 0
        count["branch"] += 1 if (h["calls"] or h["jumps"]) else 0
        count["imm"] += 1 if h["imms"] else 0
        count["any"] += 1 if (refs or h["imms"] or h["calls"] or h["jumps"]) else 0
    return count


def off_to_va(secs, o):
    acc = 0
    for name in secs:
        size = len(_RAW[name])
        if o < acc + size:
            return _SECVA[name] + (o - acc)
        acc += size
    return 0


# The concatenated data sections, set by `measure()` and read by `off_to_va` (one global, no threading).
_RAW, _SECVA = {}, {}


def table_spans():
    """Every class-table span in the image, mapped and unmapped, with the class name where one is known."""
    spans, names = [], {}
    for path, key, kind in (("inventory/xbe_class_tables.csv", "slots", "mapped"),
                            ("inventory/native_vtable_shared.csv", "table_slots", "unmapped")):
        fp = os.path.join(ROOT, path)
        if not os.path.isfile(fp):
            continue
        for r in csv.DictReader(open(fp, newline="")):
            if r.get("table_va") and r.get(key):
                spans.append((int(r["table_va"], 16), int(r[key]), kind))
    if os.path.isfile(NAMES):
        for r in csv.DictReader(open(NAMES, newline="")):
            if r.get("table_va"):
                names[int(r["table_va"], 16)] = r.get("class") or ""
    spans.sort()
    return spans, names


def family_of(decode, data, size, atexit_va, va=0):
    """(family, item) the generated body at `va` opens in, or ("", 0) -- the shape, read off its instructions.

    Two shapes recur in the one gap that holds 600 bodies. A **register-once thunk** is
    `push <&item>; push 0xff; call <helper>; add esp,8; mov <flag>,al; ret`: a distinct `.data` item
    and a distinct flag byte each, so it is a per-item registration, not spill. An **`_atexit`
    destructor registration** is `push <dtor>; call _atexit; pop ecx; ret`, the static destructor of a
    global object. Neither is named by a call -- `_atexit` is the only callee, and the helper is
    reached by every member of the first family -- which is why the block reads as one family.

    `item` is what the body names: the `.data` object a register-once thunk guards, or the destructor an
    `_atexit` registration installs. It is the only name either shape carries, so it is the name.
    """
    if not size or size < 6:
        return "", 0
    ins, i = [], 0
    while i < min(size, len(data)):
        x = decode(data, i)
        if x is None or x.length <= 0:
            break
        ins.append((i, x))
        i += x.length
    if len(ins) < 2:
        return "", 0
    s0, i0 = ins[0]
    if data[s0] != 0x68 or i0.text.split()[0] != "push":
        return "", 0
    item = struct.unpack_from("<I", data, s0 + 1)[0]
    # the destructor shape: one argument, and a call to the CRT's registration helper
    if data[ins[1][0]] == 0xE8:
        target = va + ins[1][0] + 5 + struct.unpack_from("<i", data, ins[1][0] + 1)[0]
        return ("atexit", item) if atexit_va and target == atexit_va else ("", 0)
    if len(ins) < 3:
        return "", 0
    s1, i1 = ins[1]
    s2, i2 = ins[2]
    if data[s1] not in (0x68, 0x6A) or i1.text.split()[0] != "push":
        return "", 0
    if data[s2] != 0xE8:
        return "", 0
    target = va + s2 + 5 + struct.unpack_from("<i", data, s2 + 1)[0]
    if atexit_va and target == atexit_va:
        return "atexit", item
    for s, _x in ins[3:]:
        if data[s] == 0xA2:                          # `mov ds:<abs32>, al`: the flag the thunk writes
            return "register-once", item
    return "", 0


def split_points(decode, nc, text, lo, a, b):
    """The split points the relative rule alone reads inside the map row `[a, b)`, in offset order."""
    out, cur = [], a
    while cur < b:
        size, _n, _c, tail, _i, split = walk(decode, nc.tail_shape, nc.terminator_kind, nc.prologue,
                                             text[cur - lo:b - lo], b - cur, cur)
        if not size or tail != "split":
            break
        out.append(cur + size)
        cur += size
    return out


def row_slot(va, secs, blobs, spans, names):
    """(table_va, class, slot) when a data-section dword holds `va` in a class-table slot, else None."""
    starts = [s[0] for s in spans]
    pat = struct.pack("<I", va)
    for name in secs:
        blob = blobs[name]
        pos = blob.find(pat)
        while pos != -1:
            ref = _SECVA[name] + pos
            i = bisect.bisect_right(starts, ref) - 1
            if i >= 0:
                tva, sn, _kind = spans[i]
                if tva <= ref < tva + 4 * sn:
                    return (tva, names.get(tva, ""), (ref - tva) // 4)
            pos = blob.find(pat, pos + 1)
    return None


def row_repairs(decode, nc, text, lo, split_rows, spans, names, data_secs, blobs):
    """The map rows a `resize` + `add` repairs: a split whose opening a class-table slot names.

    The row audit leaves 9 rows the split rule still cuts once the inline jump tables are read, and 8 of
    their openings nothing names at all -- those stay whole, because "no channel" is not evidence. The
    ninth is a body a slot names (the same channel that named 537 of the gap population), so the row is
    resized to end where that body begins and the body is added with its `<Class>_vt<slot>` name.
    """
    out = []
    for a, b in split_rows:
        size, _i, _c, tail, _in, _sp = walk(decode, nc.tail_shape, nc.terminator_kind, nc.prologue,
                                           text[a - lo:b - lo], b - a, a, _TABLE["targets"])
        if tail != "split" or not size:
            continue
        for p in split_points(decode, nc, text, lo, a, b):
            hit = row_slot(p, data_secs, blobs, spans, names)
            if not hit or not hit[1]:
                continue
            piece, _i2, close2, _t2, _in2, _sp2 = walk(decode, nc.tail_shape, nc.terminator_kind, nc.prologue,
                                                      text[p - lo:b - lo], b - p, p, _TABLE["targets"])
            if not piece:
                continue
            out.append({"row": "repair", "head_va": "0x%08x" % p, "gap_lo": "0x%08x" % a,
                        "gap_hi": "0x%08x" % b, "size": piece, "close": close2 or "",
                        "slot_table": "0x%08x" % hit[0], "slot_class": hit[1], "slot_index": hit[2],
                        "name": "%s_vt%d" % (hit[1], hit[2]),
                        "row_size": size})
    return out


# --------------------------------------------------------------------------- the measurement
def measure(args):
    xbe = _load("xbe")
    nc = _load("native-census")
    X = xbe.Xbe(args.xbe)
    decode = nc._decoder()
    table = map_table(_art(FUNCTIONS))
    secs = [s.name for s in X.sections]
    raw = open(args.xbe, "rb").read()
    blobs = {s.name: raw[s.foff:s.foff + s.fsize] for s in X.sections}
    tva = [s for s in X.sections if s.name == ".text"][0].va
    vsize = [s for s in X.sections if s.name == ".text"][0].vsize
    text = blobs[".text"]
    lo, hi = tva, tva + vsize
    _TABLE["targets"], _TABLE["runs"] = text_tables(text, lo)
    gaps = gaps_of(table, lo, hi)
    print("text %#x..%#x, %d gaps, %d gap bytes; %d inline jump tables, %d entries"
          % (lo, hi, len(gaps), sum(b - a for a, b in gaps), len(_TABLE["runs"]),
             len(_TABLE["targets"])))

    heads = []
    for gs, ge in gaps:
        if ge - gs < 4:
            continue
        blob = X.read_va(gs, ge - gs)
        for h in scan(decode, nc, gs, ge, blob, _TABLE["targets"]):
            h["gap_lo"], h["gap_hi"], h["gap_size"] = gs, ge, ge - gs
            heads.append(h)
    spans, names = table_spans()
    for name in secs:
        _RAW[name], _SECVA[name] = blobs[name], [s for s in X.sections if s.name == name][0].va
    ch = channels(xbe, raw, [s for s in secs if s != ".text"], blobs, tva, heads, spans, names)
    atexit_va = next((a for a, _b, n in table if n == "_atexit"), 0)
    for h in heads:
        if not h["size"]:
            h["family"], h["family_item"] = "", ""
            continue
        room = max(1, min(h["gap_hi"] - h["head_va"], h["size"] + 16, 128))
        fam, item = family_of(decode, X.read_va(h["head_va"], room), h["size"], atexit_va,
                              h["head_va"])
        h["family"] = fam
        h["family_item"] = "0x%08x" % item if item else ""

    # the controls: the same two tests over the rows the map already places
    pro = lambda head: prologue(nc, head)
    ctl = collections.Counter()
    split_rows = []
    for a, b, _n in [t for t in table if lo <= t[0] < hi]:
        pad = a > lo and text[a - 1 - lo] in (0xCC, 0x90)
        pr = pro(text[a - lo:a - lo + 8])
        ctl["both" if (pad and pr != "other") else ("pad" if pad else ("prologue" if pr != "other" else "neither"))] += 1
        # which half refused the row: an opening the shared table does not name but this census does is
        # the prologue half's own miss, and a row behind a terminator is the boundary half's.
        if pr != "other" and nc.prologue(text[a - lo:a - lo + 8]) == "other":
            ctl["ext_" + ("pad" if pad else "nopad")] += 1
        if not pad and pr != "other":
            for s in range(max(lo, a - 16), a):
                ins = decode(text[s - lo:a - lo], 0)
                if ins is None or ins.length != a - s:
                    continue
                k = nc.terminator_kind(text, s - lo, ins.text)
                if k:
                    ctl["nopad_term_" + k] += 1
                    break
        size, n, close, tail, _i, _sp = walk(decode, nc.tail_shape, nc.terminator_kind, pro,
                                             text[a - lo:b - lo], b - a, a, _TABLE["targets"])
        if tail == "split":
            ctl["split"] += 1
            split_rows.append((a, b))
        elif size == b - a:
            ctl["decode"] += 1
        elif size:
            ctl["short"] += 1
        else:
            ctl["noclose"] += 1
        # what the same rule says *without* the table channel, and how many of its openings a table names
        size0, _n0, _c0, tail0, _i0, _sp0 = walk(decode, nc.tail_shape, nc.terminator_kind, pro,
                                                text[a - lo:b - lo], b - a, a)
        if tail0 == "split":
            ctl["split_rel"] += 1
            for p in split_points(decode, nc, text, lo, a, b):
                ctl["split_pts"] += 1
                if p in _TABLE["targets"]:
                    ctl["split_pts_table"] += 1
    # the null population: pad-before prologue-shaped bytes in the data sections
    for name in (".rdata", ".data", "XNET", "DSOUND"):
        if name not in blobs:
            continue
        sec = [s for s in X.sections if s.name == name][0]
        found = scan(decode, nc, sec.va, sec.va + sec.vsize, blobs[name])
        ctl["null_" + name] = sum(1 for f in found if f["size"])
        ctl["nullheads_" + name] = len(found)
    # each extra opening's two counts, re-measured: the map rows it opens, and the pad-bounded openings
    # it matches in the data sections. The ratio is the whole argument for calling it a prologue.
    row_starts = [t[0] for t in table if lo <= t[0] < hi]
    data_pads = []
    for name in (".rdata", ".data", "XNET", "DSOUND"):
        if name not in blobs:
            continue
        blob = blobs[name]
        data_pads += [blob[o:o + 8] for o in range(1, len(blob))
                      if blob[o - 1] in (0xCC, 0x90) and blob[o] not in (0xCC, 0x90)]
    for pat, label, _rows, _data in EXTRA_PROLOGUES:
        ctl["extrows_" + label] = sum(1 for a in row_starts
                                      if nc.prologue(text[a - lo:a - lo + 8]) == "other"
                                      and text[a - lo:a - lo + len(pat)] == pat)
        ctl["extdata_" + label] = sum(1 for chunk in data_pads if chunk.startswith(pat))
    # base rates for the pointer channel
    import random
    random.seed(7)
    body_vas = {h["head_va"] for h in heads}
    allgap = [va for gs, ge in gaps for va in range(gs, ge)]
    nonbody = [va for va in allgap if va not in body_vas]
    outside = b"".join(blobs[s] for s in secs if s != ".text")
    for label, pool, n in (("base_gap", nonbody, 2000), ("base_row", [t[0] for t in table if lo <= t[0] < hi], 2000)):
        sample = random.sample(pool, min(n, len(pool)))
        hit = sum(1 for va in sample if struct.pack("<I", va) in outside)
        ctl[label] = hit
        ctl[label + "_n"] = len(sample)

    rows = []
    for h in sorted(heads, key=lambda x: x["head_va"]):
        v = verdict(h)
        rows.append({"row": "head", "head_va": "0x%08x" % h["head_va"],
                     "gap_lo": "0x%08x" % h["gap_lo"], "gap_hi": "0x%08x" % h["gap_hi"],
                     "gap_size": h["gap_size"], "boundary": h["boundary"], "prologue": h["prologue"],
                     "verdict": v, "size": h["size"] or "", "insns": h["insns"],
                     "close": h["close"] or "", "tail": h["tail"] or "",
                     "inner_starts": h["inner_starts"], "calls": h["calls"], "jumps": h["jumps"],
                     "imms": h["imms"], "pointers": h["pointers"], "slots": h["slots"],
                     "slot_table": h["slot_table"], "slot_class": h["slot_class"],
                     "slot_index": h["slot_index"], "family": h["family"],
                     "family_item": h.get("family_item", "")})
    # the row audit's repairs: of the rows still cut, the one a class-table slot names is a body
    repairs = row_repairs(decode, nc, text, lo, split_rows, spans, names,
                          [s for s in secs if s != ".text"], blobs)
    vc = collections.Counter(r["verdict"] for r in rows if r["row"] == "head")
    bodies = [r for r in rows if r["row"] == "head" and r["verdict"] in ("body", "island")]
    meta = collections.OrderedDict()
    meta["text_lo"], meta["text_hi"] = lo, hi
    meta["gaps"] = len(gaps)
    meta["gap_bytes"] = sum(b - a for a, b in gaps)
    meta["gaps_under_8"] = sum(1 for a, b in gaps if b - a < 8)
    meta["heads"] = len(rows)
    meta["bodies"] = vc["body"]
    meta["islands"] = vc["island"]
    meta["no_close"] = vc["no-close"]
    meta["undecoded"] = vc["undecoded"]
    meta["body_bytes"] = sum(int(r["size"]) for r in bodies)
    meta["body_bytes_pct"] = int(round(100.0 * meta["body_bytes"] / max(meta["gap_bytes"], 1)))
    meta["inner_starts"] = sum(int(r["inner_starts"]) for r in rows)
    meta["bodies_with_inner"] = sum(1 for r in rows if int(r["inner_starts"]))
    meta["splits"] = sum(1 for r in rows if r["boundary"] == "after-ret")
    meta["splits_table"] = sum(1 for r in rows if r["boundary"] == "after-ret"
                               and int(r["head_va"], 16) in _TABLE["targets"])
    meta["family_register_once"] = sum(1 for r in rows if r["family"] == "register-once")
    meta["family_atexit"] = sum(1 for r in rows if r["family"] == "atexit")
    meta["slot_named"] = sum(1 for r in rows if int(r["slots"]))
    meta["named_pointer"] = ch["pointer"]
    meta["named_slot"] = ch["slot"]
    meta["named_branch"] = ch["branch"]
    meta["named_imm"] = ch["imm"]
    meta["named_any"] = ch["any"]
    meta["unnamed"] = len(bodies) - ch["any"]
    meta["map_rows"] = sum(v for k, v in ctl.items() if k in ("both", "pad", "prologue", "neither"))
    meta["map_rows_head"] = ctl["both"]
    meta["map_rows_pad_only"] = ctl["pad"]
    meta["map_rows_prologue_only"] = ctl["prologue"]
    meta["map_rows_neither"] = ctl["neither"]
    # the two tests apart: the boundary half's miss is the prologue-only rows, and 1,674 of them stand
    # behind a terminator; the prologue half's miss is the pad-only rows plus the ones this census names.
    meta["map_rows_nopad"] = ctl["prologue"] + ctl["neither"]
    meta["map_rows_nopad_term"] = sum(v for k, v in ctl.items() if k.startswith("nopad_term_"))
    meta["map_rows_ext_pad"] = ctl["ext_pad"]
    meta["map_rows_ext_nopad"] = ctl["ext_nopad"]
    # the same four cells with the *shared* table alone, so the page can state the split both ways
    meta["map_rows_shared_head"] = meta["map_rows_head"] - meta["map_rows_ext_pad"]
    meta["map_rows_shared_pad_only"] = meta["map_rows_pad_only"] + meta["map_rows_ext_pad"]
    meta["map_rows_shared_pro_only"] = meta["map_rows_prologue_only"] - meta["map_rows_ext_nopad"]
    meta["map_rows_shared_neither"] = meta["map_rows_neither"] + meta["map_rows_ext_nopad"]
    meta["heads_start"] = sum(1 for r in rows if r.get("boundary") == "start")
    meta["heads_after_ret"] = sum(1 for r in rows if r.get("boundary") == "after-ret")
    meta["heads_ext"] = sum(1 for r in rows if r.get("prologue") in EXTRA_NAMES)
    meta["bodies_ext"] = sum(1 for r in rows if r.get("prologue") in EXTRA_NAMES
                             and r.get("verdict") in ("body", "island"))
    meta["unnamed_ext"] = sum(1 for r in rows if r.get("prologue") in EXTRA_NAMES
                              and r.get("verdict") in ("body", "island")
                              and not (int(r.get("calls") or 0) or int(r.get("jumps") or 0)
                                       or int(r.get("imms") or 0) or int(r.get("pointers") or 0)))
    for pat, label, _r, _d in EXTRA_PROLOGUES:
        meta["extrows_" + label] = ctl["extrows_" + label]
        meta["extdata_" + label] = ctl["extdata_" + label]
    meta["map_rows_decode"] = ctl["decode"]
    meta["map_rows_short"] = ctl["short"]
    meta["map_rows_noclose"] = ctl["noclose"]
    meta["map_rows_split"] = ctl["split"]
    meta["map_rows_split_rel"] = ctl["split_rel"]
    meta["map_rows_split_points"] = ctl["split_pts"]
    meta["map_rows_split_points_table"] = ctl["split_pts_table"]
    meta["text_tables"] = len(_TABLE["runs"])
    meta["text_table_entries"] = len(_TABLE["targets"])
    for name in (".rdata", ".data", "XNET", "DSOUND"):
        if "nullheads_" + name in ctl:
            meta["null_heads_" + name.strip(".").lower()] = ctl["nullheads_" + name]
            meta["null_bodies_" + name.strip(".").lower()] = ctl["null_" + name]
    meta["base_gap"], meta["base_gap_n"] = ctl["base_gap"], ctl["base_gap_n"]
    meta["base_row"], meta["base_row_n"] = ctl["base_row"], ctl["base_row_n"]
    meta["tables"] = len(spans)
    meta["slot_classes"] = len({r["slot_class"] for r in rows if r["slot_class"]})
    meta["slot_unclassed"] = sum(1 for r in rows if int(r["slots"]) and not r["slot_class"])
    meta["tables_unclassed"] = sum(1 for t_va, _n, _k in spans if not names.get(t_va))
    meta["map_rows_split_named"] = len(repairs)
    meta["map_rows_split_unnamed"] = ctl["split"] - len(repairs)
    meta["row_repairs"] = len(repairs)
    for r in repairs:
        rows.append(r)
    rows.append({"row": "census", "census": ";".join("%s=%s" % (k, v) for k, v in meta.items())})
    assert len(rows) == len(heads) + len(repairs) + 1, (len(rows), len(heads), len(repairs))
    print("heads %d: %d bodies, %d islands, %d no-close, %d undecoded; %d body bytes (%d%% of the gaps)"
          % (meta["heads"], meta["bodies"], meta["islands"], meta["no_close"], meta["undecoded"],
             meta["body_bytes"], meta["body_bytes_pct"]))
    print("named: pointer %d, table slot %d, branch %d, push %d; any %d, none %d"
          % (meta["named_pointer"], meta["named_slot"], meta["named_branch"], meta["named_imm"],
             meta["named_any"], meta["unnamed"]))
    print("split %d bodies out of merged runs (%d openings stay merged); families: %d register-once, %d atexit"
          % (meta["splits"], meta["inner_starts"], meta["family_register_once"], meta["family_atexit"]))
    print("row repairs: %d (a class-table slot names the opening; %d rows nothing names stay whole)"
          % (len(repairs), ctl["split"] - len(repairs)))
    print("controls: map rows head-rule %d of %d; decode-to-size %d; rows a split would cut %d (relative rule alone %d, "
          "its %d openings %d named by an inline table); tables %d/%d; null bodies %s; base %.1f%% gap vs %.1f%% rows"
          % (meta["map_rows_head"], meta["map_rows"], meta["map_rows_decode"], meta["map_rows_split"],
             meta["map_rows_split_rel"], meta["map_rows_split_points"], meta["map_rows_split_points_table"],
             meta["text_tables"], meta["text_table_entries"],
             {k: v for k, v in meta.items() if k.startswith("null_bodies_")},
             100.0 * meta["base_gap"] / max(meta["base_gap_n"], 1),
             100.0 * meta["base_row"] / max(meta["base_row_n"], 1)))
    return rows, meta


# --------------------------------------------------------------------------- the rows the population earns
POPULATIONS = ("slot", "family", "all")


def add_candidates(rows, population):
    """[(head row, name)] -- the bodies whose own evidence is strong enough to create a map row.

    The population is the point: a body nothing calls is added only when something *else* names it,
    and a class-table slot is the strongest channel this census has (59.7% of the population against
    a 1.6% base rate). `slot` takes the bodies whose slot also resolves a class, because that is what
    a name can be built from; `family` takes the generated block; `all` takes every decodable body.
    """
    out, taken = [], set()
    for r in rows:
        if r.get("row") != "head" or r["verdict"] not in ("body", "island"):
            continue
        if not r["size"] or r["close"] not in ("ret", "ret-imm", "jmp", "jmp-ind"):
            continue
        if population == "slot" and not r["slot_class"]:
            continue
        if population == "family" and not r["family"]:
            continue
        if r["slot_class"]:
            name = "%s_vt%s" % (r["slot_class"], r["slot_index"])
        elif r["family"] == "atexit":
            name = "dtor_%s" % (r["family_item"][2:] if r.get("family_item") else r["head_va"][2:])
        elif r["family"] == "register-once":
            name = "static_init_%s" % (r["family_item"][2:] if r.get("family_item") else r["head_va"][2:])
        else:
            name = "FUN_%s" % r["head_va"][2:]
        if name in taken:
            name += "_b"
        taken.add(name)
        out.append((r, name))
    return out


def add_evidence(r):
    """The one sentence an `add` row carries: what names this body, and why the map has no row."""
    who = ("a dword in the class table %s at slot %s, which that table's class (%s) declares"
           % (r["slot_table"], r["slot_index"], r["slot_class"]))
    if not r["slot_class"]:
        who = ("a dword in the class table %s at slot %s" % (r["slot_table"], r["slot_index"]))
    if r.get("family") == "register-once":
        who = ("its own instructions: the body registers the `.data` item %s once (a flag byte each, one "
               "`call` to the shared initialiser), so the item names it" % (r.get("family_item") or "?"))
    elif r.get("family") == "atexit":
        who = ("its own instructions: `push %s; call _atexit`, the static destructor registration of that "
               "destructor" % (r.get("family_item") or "?"))
    pad = (("%d bytes of pad" % int(r["boundary"].split(":")[1])) if r["boundary"].startswith("pad")
           else {"after-ret": "a split boundary"}.get(r["boundary"], "the gap's start"))
    calls, jumps = int(r["calls"] or 0), int(r["jumps"] or 0)
    if calls or jumps:
        reach = ("the image's only references to it are %d call site(s) and %d jump(s), and the map "
                 "turned neither into a row" % (calls, jumps))
    else:
        reach = ("no `E8` and no pointer outside a table names it, so the call graph that builds this "
                 "map can never make it a function")
    return ("the body in the gap %s..%s: %s; %s in front, an MSVC %s prologue, and a linear decode "
            "closing on `%s` at 0x%08x; %s"
            % (r["gap_lo"], r["gap_hi"], who, pad, r["prologue"], r["close"],
               int(r["head_va"], 16) + int(r["size"]), reach))


def emit_adds(args):
    rows = _art()
    cand = add_candidates(rows, args.add_population)
    w = csv.writer(sys.stdout, lineterminator="\n")
    for r, name in cand:
        w.writerow([r["head_va"], "add", name, r["size"], add_evidence(r)])
    print("gap-heads: %d add(s) for population %r" % (len(cand), args.add_population), file=sys.stderr)
    return 0


def apply_adds(args):
    """Append the population's `add` rows and the creation row each needs, refusing any duplicate.

    An `add` with no `xbe_overrides.csv` row is invisible to a fresh import (`XbePost.java` builds the
    function from that file), so the two rows are written together or not at all.
    """
    rows = _art()
    cand = add_candidates(rows, args.add_population)
    with open(BOUNDARIES, newline="") as fh:
        b_rows = list(csv.DictReader(fh))
    with open(OVERRIDES, newline="") as fh:
        o_rows = list(csv.DictReader(fh))
    have = {int(r["address"], 16) for r in b_rows} | {int(r["address"], 16) for r in o_rows}
    dup = [r["head_va"] for r, _n in cand if int(r["head_va"], 16) in have]
    if dup:
        sys.exit("gap-heads: --apply-adds: %d of the population is already a row (%s); regenerate the "
                 "census after a repair instead of re-adding it" % (len(dup), ", ".join(dup[:4])))
    for r, name in cand:
        ev = add_evidence(r)
        b_rows.append({"address": r["head_va"], "action": "add", "target": name, "size": r["size"],
                       "evidence": ev})
        o_rows.append({"address": r["head_va"], "name": name,
                       "evidence": "added to the map by symbols/xbe_boundaries.csv: " + ev,
                       "class": "", "outer": "", "object_size": "", "assert_line": "", "module": "",
                       "source_file": "", "function_size": r["size"]})
    with open(BOUNDARIES, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["address", "action", "target", "size", "evidence"],
                           lineterminator="\n")
        w.writeheader()
        w.writerows(b_rows)
    with open(OVERRIDES, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["address", "name", "evidence", "class", "outer",
                                           "object_size", "assert_line", "module", "source_file",
                                           "function_size"], lineterminator="\r\n")
        w.writeheader()
        w.writerows(o_rows)
    print("gap-heads: %d add(s) and %d creation row(s) written for population %r"
          % (len(cand), len(cand), args.add_population))
    return 0


def repair_rows_of(rows):
    """The `row=repair` rows: a split whose opening a class-table slot names."""
    return [r for r in rows if r.get("row") == "repair"]


def repair_evidence(r):
    return ("the map row %s..%s declares %d bytes but holds a second body: the row's own decode closes on `%s` "
            "at 0x%08x and that address is the body a dword in the class table %s slot %s names (%s), so the "
            "row is resized to end there and the slot body is added"
            % (r["gap_lo"], r["gap_hi"], int(r["gap_hi"], 16) - int(r["gap_lo"], 16), r.get("close") or "ret",
               int(r["head_va"], 16), r["slot_table"], r["slot_index"], r["name"]))


def emit_repairs(args):
    rows = _art()
    rep = repair_rows_of(rows)
    w = csv.writer(sys.stdout, lineterminator="\n")
    for r in rep:
        w.writerow([r["gap_lo"], "resize", "", int(r["head_va"], 16) - int(r["gap_lo"], 16), repair_evidence(r)])
        w.writerow([r["head_va"], "add", r["name"], r["size"], repair_evidence(r)])
    print("gap-heads: %d row repair(s) -> %d boundary rows" % (len(rep), 2 * len(rep)), file=sys.stderr)
    return 0


def apply_repairs(args):
    """Write the `resize` + `add` pair each split row needs, with the creation row the add requires.

    A row may already carry a `resize` in the other direction -- `0x0014c1b0` was *extended* to 179 by an
    earlier bundle because the walk ran through the row's own `ret 8` into the `ret 4` behind it. The split
    rule resolves that merge, so the repair **replaces** that resize instead of refusing: the correction is
    the finding, and the evidence sentence says so instead of quietly dropping the old row.
    """
    rows = _art()
    rep = repair_rows_of(rows)
    if not rep:
        print("gap-heads: no row repairs in the artifact")
        return 0
    with open(BOUNDARIES, newline="") as fh:
        b_rows = list(csv.DictReader(fh))
    with open(OVERRIDES, newline="") as fh:
        o_rows = list(csv.DictReader(fh))
    have = {int(r["address"], 16) for r in b_rows} | {int(r["address"], 16) for r in o_rows}
    dup = [r["head_va"] for r in rep if int(r["head_va"], 16) in have]
    if dup:
        sys.exit("gap-heads: --apply-repairs: %s is already a row; re-measure instead of re-applying"
                 % ", ".join(dup[:4]))
    for r in rep:
        ev = repair_evidence(r)
        old = [x for x in b_rows if x["action"] == "resize" and int(x["address"], 16) == int(r["gap_lo"], 16)]
        size = int(r["head_va"], 16) - int(r["gap_lo"], 16)
        if old:
            was = old[0]["size"]
            ev += ("; this replaces the earlier resize of the same row to %s, which ran through the row's own "
                   "terminator into the body behind it" % was)
            b_rows = [x for x in b_rows if x not in old]
        b_rows.append({"address": r["gap_lo"], "action": "resize", "target": "", "size": size, "evidence": ev})
        b_rows.append({"address": r["head_va"], "action": "add", "target": r["name"], "size": r["size"],
                       "evidence": ev})
        o_rows.append({"address": r["head_va"], "name": r["name"],
                       "evidence": "added to the map by symbols/xbe_boundaries.csv: " + ev,
                       "class": r["slot_class"], "outer": "", "object_size": "", "assert_line": "",
                       "module": "", "source_file": "", "function_size": r["size"]})
    with open(BOUNDARIES, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["address", "action", "target", "size", "evidence"],
                           lineterminator="\n")
        w.writeheader()
        w.writerows(b_rows)
    with open(OVERRIDES, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["address", "name", "evidence", "class", "outer", "object_size",
                                           "assert_line", "module", "source_file", "function_size"],
                           lineterminator="\r\n")
        w.writeheader()
        w.writerows(o_rows)
    print("gap-heads: %d row repair(s) written (%d resize + %d add + %d creation row)"
          % (len(rep), len(rep), len(rep), len(rep)))
    return 0


# --------------------------------------------------------------------------- the artifact and the page
def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in COLS})


def census_of(rows):
    for r in rows:
        if r.get("row") == "census" and r.get("census"):
            out = {}
            for pair in r["census"].split(";"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    out[k] = int(v, 16) if v.startswith("0x") else int(v)
            return out
    return {}


def render(rows):
    """The page's block, rebuilt from the artifact alone so the prose and the table cannot disagree."""
    m = census_of(rows)
    heads = [r for r in rows if r.get("row") == "head"]
    gaps = collections.Counter(r["gap_lo"] for r in heads)
    lines = ["| measure | value |", "|---|---|"]
    if m:
        lines += [
            "| `.text` | `0x%08x`..`0x%08x` (%d bytes) |" % (m["text_lo"], m["text_hi"], m["text_hi"] - m["text_lo"]),
            "| gaps between map rows / bytes they cover | %d / %d (%d gaps under 8 bytes) |"
            % (m["gaps"], m["gap_bytes"], m["gaps_under_8"]),
            "| heads the pad-before + prologue rule finds | %d |" % m["heads"],
            "| ...decode to a body / are a 1-2 instruction island | %d / %d |" % (m["bodies"], m["islands"]),
            "| ...refused (`no-close` / `undecoded`) | %d / %d |" % (m["no_close"], m["undecoded"]),
            "| bytes the bodies cover | %d (%.1f%% of the gap bytes) |" % (m["body_bytes"], m["body_bytes_pct"]),
            "| runs holding an inner strong opening (a branch rejoins it, so it stays merged) | %d in %d runs |"
            % (m["inner_starts"], m["bodies_with_inner"]),
            "| bodies a split ends (a terminator no branch in the run targets) | %d |" % m["splits"],
            "| bodies in a generated family (`register-once` / `_atexit`) | %d / %d |"
            % (m["family_register_once"], m["family_atexit"]),
            "| named by a dword outside `.text` | %d |" % m["named_pointer"],
            "| ...of those, in a class-table slot (%d bodies; %d tables, %d of them class-named) | %d |"
            % (m["slot_named"], m["tables"], m["slot_classes"], m["named_slot"]),
            "| named by an `E8`/`E9` branch | %d |" % m["named_branch"],
            "| named by a `68` push | %d |" % m["named_imm"],
            "| named by any channel / by none | %d / %d |" % (m["named_any"], m["unnamed"]),
            "| boundaries: the gap's start / fill / a split | %d / %d / %d |"
            % (m.get("heads_start", 0),
               m["heads"] - m.get("heads_start", 0) - m.get("heads_after_ret", 0), m.get("heads_after_ret", 0)),
            "| heads a measured opening adds (not in the shared prologue table) / of them decode | %d / %d |"
            % (m.get("heads_ext", 0), m.get("bodies_ext", 0)),
            "| ...of those, named by no channel | %d |" % m.get("unnamed_ext", 0),
            "| control: map rows passing the head rule / decoding to their size | %d of %d / %d |"
            % (m["map_rows_head"], m["map_rows"], m["map_rows_decode"]),
            "| ...the two tests apart: both / fill only (prologue miss) / opening only (boundary miss) / neither | %d / %d / %d / %d |"
            % (m["map_rows_head"], m.get("map_rows_pad_only", 0), m.get("map_rows_prologue_only", 0),
               m.get("map_rows_neither", 0)),
            "| ...the same four cells with the shared prologue table alone | %d / %d / %d / %d |"
            % (m.get("map_rows_shared_head", 0), m.get("map_rows_shared_pad_only", 0),
               m.get("map_rows_shared_pro_only", 0), m.get("map_rows_shared_neither", 0)),
            "| ...of the no-fill rows, the successor of a terminator (what the split arm reads) / of nothing | %d / %d |"
            % (m.get("map_rows_nopad_term", 0), m.get("map_rows_nopad", 0) - m.get("map_rows_nopad_term", 0)),
            "| ...rows the measured openings name that the shared table refuses: behind fill / behind code | %d / %d |"
            % (m.get("map_rows_ext_pad", 0), m.get("map_rows_ext_nopad", 0)),
            "| ...whose declared span the split rule reads as two bodies: relative branches alone / with the inline tables | %d / %d |"
            % (m.get("map_rows_split_rel", 0), m.get("map_rows_split", 0)),
            "| ...split points that reading means / of those, entries of a `.text` jump table | %d / %d |"
            % (m.get("map_rows_split_points", 0), m.get("map_rows_split_points_table", 0)),
            "| ...of the rows still cut, a class-table slot names the opening / nothing names it | %d / %d |"
            % (m.get("map_rows_split_named", 0), m.get("map_rows_split_unnamed", 0)),
            "| inline jump tables in `.text` (aligned runs of >=3 code addresses) / entries | %d / %d |"
            % (m.get("text_tables", 0), m.get("text_table_entries", 0)),
            "| control: the same rule over `.rdata`, `.data`, XNET, DSOUND (%d head-shaped runs, 3.3 MB of data) | %d / %d / %d / %d bodies |"
            % (m.get("null_heads_rdata", 0) + m.get("null_heads_data", 0) + m.get("null_heads_xnet", 0)
               + m.get("null_heads_dsound", 0),
               m.get("null_bodies_rdata", 0), m.get("null_bodies_data", 0), m.get("null_bodies_xnet", 0),
               m.get("null_bodies_dsound", 0)),
            "| base rate: a dword outside `.text` names a random gap byte / a map row's start | %d of %d (%.1f%%) / %d of %d (%.1f%%) |"
            % (m["base_gap"], m["base_gap_n"], 100.0 * m["base_gap"] / max(m["base_gap_n"], 1),
               m["base_row"], m["base_row_n"], 100.0 * m["base_row"] / max(m["base_row_n"], 1)),
        ]
    top = gaps.most_common(8)
    if top:
        lines += ["", "| gap | heads | bodies | first head |", "|---|---:|---:|---|"]
        for g, n in top:
            row = [r for r in heads if r["gap_lo"] == g]
            nb = sum(1 for r in row if r["verdict"] in ("body", "island"))
            lines.append("| `%s`..`%s` | %d | %d | `%s` |" % (row[0]["gap_lo"], row[0]["gap_hi"], n, nb,
                                                             min(r["head_va"] for r in row)))
    if m:
        lines += ["", "| opening this census adds | map rows it opens | pad-bounded data openings |",
                  "|---|---:|---:|"]
        for name in EXTRA_NAMES:
            lines.append("| `%s` | %d | %d |" % (name, m.get("extrows_" + name, 0), m.get("extdata_" + name, 0)))
        lines += ["", "%d of the %d bodies sit in a class-table slot; %d of those are in the %d tables no "
                  "registration names, and %d are named by no channel at all: nothing calls them, nothing "
                  "points at them, and the map has no row for them."
                  % (m["slot_named"], m["bodies"], m["slot_unclassed"], m["tables_unclassed"],
                     m["unnamed"])]
        if m.get("map_rows_split_rel") is not None:
            lines += ["", "The split rule does not hold on the map's own rows the way it holds on the gaps: "
                      "%d rows read as two bodies with relative branches alone, and **%d of their %d openings "
                      "are entries of the %d inline jump tables** (`switch` case blocks, reached by an indirect "
                      "branch no relative read sees) -- so they are one body each, and the rule now consults the "
                      "table. Over the gap population the same channel names **%d of its %d split heads**, which "
                      "is what makes the %d splits bodies rather than case blocks."
                      % (m.get("map_rows_split_rel", 0), m.get("map_rows_split_points_table", 0),
                         m.get("map_rows_split_points", 0), m.get("text_tables", 0),
                         m.get("splits_table", 0), m["splits"], m["splits"])]
    return "\n".join(lines)


def splice(path, block):
    text = open(path, encoding="utf-8").read()
    if DOC_START not in text or DOC_END not in text:
        return None
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + "\n" + DOC_END + tail


# --------------------------------------------------------------------------- the gate
def check():
    """Dump-free: the rows against the map, and the census row against the rows."""
    if not os.path.isfile(ARTIFACT):
        sys.exit("gap-heads: %s is missing" % os.path.relpath(ARTIFACT, ROOT))
    rows = _art()
    if list(rows[0].keys()) != COLS:
        sys.exit("gap-heads: --check: the table's columns are %r, the tool writes %r" % (list(rows[0].keys()), COLS))
    m = census_of(rows)
    for key in ("text_lo", "text_hi", "gaps", "heads", "bodies", "islands", "no_close", "undecoded",
                "splits", "family_register_once", "family_atexit", "slot_named",
                "heads_ext", "map_rows_nopad", "map_rows_nopad_term"):
        if key not in m:
            sys.exit("gap-heads: --check: the census row carries no %s" % key)
    # the boundaries partition the heads, the two-test split partitions the rows, and the closer the
    # rule gets the smaller the residues are -- the claims this bundle makes, held by the gate.
    if m["heads_start"] + m["heads_after_ret"] > m["heads"]:
        sys.exit("gap-heads: --check: %d start + %d split heads of %d"
                 % (m["heads_start"], m["heads_after_ret"], m["heads"]))
    if (m["map_rows_head"] + m["map_rows_pad_only"] + m["map_rows_prologue_only"]
            + m["map_rows_neither"]) != m["map_rows"]:
        sys.exit("gap-heads: --check: the two-test split does not sum to %d rows" % m["map_rows"])
    if m["map_rows_nopad"] != m["map_rows_prologue_only"] + m["map_rows_neither"]:
        sys.exit("gap-heads: --check: %d no-fill rows against %d + %d"
                 % (m["map_rows_nopad"], m["map_rows_prologue_only"], m["map_rows_neither"]))
    if m["map_rows_nopad_term"] > m["map_rows_nopad"]:
        sys.exit("gap-heads: --check: %d of %d no-fill rows stand behind a terminator"
                 % (m["map_rows_nopad_term"], m["map_rows_nopad"]))
    for name in EXTRA_NAMES:
        if "extrows_" + name not in m or "extdata_" + name not in m:
            sys.exit("gap-heads: --check: the census row carries no counts for the opening %r" % name)
        if m["extrows_" + name] < 1:
            sys.exit("gap-heads: --check: the opening %r opens no map row, so it is not a prologue" % name)
    # The map-row control: every row falls in exactly one bucket, and the rule no longer cuts a row once
    # the inline jump tables are read -- that is the claim this bundle makes, so the gate holds it.
    if "map_rows_split" in m:
        bucket = m.get("map_rows_decode", 0) + m.get("map_rows_short", 0) + m.get("map_rows_split", 0) \
            + m.get("map_rows_noclose", 0)
        if bucket != m["map_rows"]:
            sys.exit("gap-heads: --check: the map-row control buckets sum to %d, the census says %d"
                     % (bucket, m["map_rows"]))
        if m["map_rows_split"] != m.get("map_rows_split_named", 0) + m.get("map_rows_split_unnamed", 0):
            sys.exit("gap-heads: --check: %d split rows are not named + unnamed (%d + %d)"
                     % (m["map_rows_split"], m.get("map_rows_split_named", 0),
                        m.get("map_rows_split_unnamed", 0)))
        if m.get("map_rows_split_points_table", 0) > m.get("map_rows_split_points", 0):
            sys.exit("gap-heads: --check: %d table-named openings of %d split points"
                     % (m["map_rows_split_points_table"], m["map_rows_split_points"]))
        if m.get("map_rows_split_named"):
            sys.exit("gap-heads: --check: %d map rows are cut where a class-table slot names the opening; "
                     "run --apply-repairs, `make boundaries` and re-measure" % m["map_rows_split_named"])
    for r in repair_rows_of(rows):
        want = "%s_vt%s" % (r.get("slot_class") or "", r.get("slot_index") or "")
        if not r.get("slot_class") or not r.get("size") or r.get("name") != want:
            sys.exit("gap-heads: --check: repair row %s carries %r, not a class-table name"
                     % (r.get("head_va"), r.get("name")))
    heads = [r for r in rows if r["row"] == "head"]
    table = map_table(_art(FUNCTIONS))
    gaps = gaps_of(table, m["text_lo"], m["text_hi"])
    bad = []
    if len(heads) != m["heads"]:
        bad.append("%d head rows for a census that says %d" % (len(heads), m["heads"]))
    vc = collections.Counter(r["verdict"] for r in heads)
    for key, name in (("bodies", "body"), ("islands", "island"), ("no_close", "no-close"),
                      ("undecoded", "undecoded")):
        if vc[name] != m[key]:
            bad.append("census says %s %d, the rows hold %d" % (key, m[key], vc[name]))
    total = sum(int(r["size"]) for r in heads if r["size"])
    if total != m["body_bytes"]:
        bad.append("the rows' sizes sum to %d, the census says %d" % (total, m["body_bytes"]))
    for key, field, want in (("splits", "boundary", "after-ret"),):
        got = sum(1 for r in heads if r[field] == want)
        if got != m[key]:
            bad.append("census says %s %d, the rows hold %d" % (key, m[key], got))
    for key, fam in (("family_register_once", "register-once"), ("family_atexit", "atexit")):
        got = sum(1 for r in heads if r["family"] == fam)
        if got != m[key]:
            bad.append("census says %s %d, the rows hold %d" % (key, m[key], got))
    got = sum(1 for r in heads if int(r["slots"]))
    if got != m["slot_named"]:
        bad.append("census says slot_named %d, the rows hold %d" % (m["slot_named"], got))
    if len(gaps) != m["gaps"]:
        bad.append("the map holds %d gaps in .text, the census says %d" % (len(gaps), m["gaps"]))
    starts = [t[0] for t in table]
    for r in heads:
        va = int(r["head_va"], 16)
        if not (m["text_lo"] <= va < m["text_hi"]):
            bad.append("%s is outside .text" % r["head_va"])
            continue
        i = bisect.bisect_right(starts, va) - 1
        if i >= 0 and table[i][0] <= va < table[i][1]:
            bad.append("%s is inside the map row %s" % (r["head_va"], table[i][2]))
        if not r["boundary"].startswith("pad") and r["boundary"] not in ("start", "after-ret"):
            bad.append("%s: boundary %r is not a pad, the gap's start or a split" % (r["head_va"], r["boundary"]))
        v = r["verdict"]
        size = int(r["size"] or 0)
        if r["family"] and r["family"] not in FAMILIES:
            bad.append("%s: family %r is not one of %s" % (r["head_va"], r["family"], "|".join(FAMILIES)))
        if r["boundary"] == "after-ret":
            prev = next((p for p in heads if int(p["head_va"], 16) + int(p["size"] or 0) == va), None)
            if prev is None or prev["tail"] != "split":
                bad.append("%s: a split head that no split row abuts" % r["head_va"])
        if v in ("body", "island"):
            if not size or r["close"] not in ("ret", "ret-imm", "jmp", "jmp-ind"):
                bad.append("%s: %s with no close" % (r["head_va"], v))
            if r["tail"] and any(p not in TAILS for p in r["tail"].split("+")):
                bad.append("%s: tail %r is not one of %s" % (r["head_va"], r["tail"], "|".join(TAILS)))
            isl = int(r["insns"]) <= 2 or size <= 4
            if (v == "island") != isl:
                bad.append("%s: %s on %s bytes / %s instructions" % (r["head_va"], v, r["size"], r["insns"]))
        elif size:
            bad.append("%s: %s carries a size" % (r["head_va"], v))
    by_va = {int(r["head_va"], 16): r for r in heads}
    for r in heads:
        if r["tail"] != "split":
            continue
        nxt = by_va.get(int(r["head_va"], 16) + int(r["size"] or 0))
        if nxt is None or nxt["boundary"] != "after-ret":
            bad.append("%s: a split whose successor is not the next head" % r["head_va"])
    if bad:
        sys.exit("gap-heads: --check: " + "; ".join(bad[:6]))
    print("gap-heads: %d heads in %d gaps -- %d bodies, %d islands, %d no-close, %d undecoded"
          % (m["heads"], m["gaps"], m["bodies"], m["islands"], m["no_close"], m["undecoded"]))
    return 0


# --------------------------------------------------------------------------- the self test
def selftest():
    nc = _load("native-census")
    decode = nc._decoder()
    blob = (b"\xcc\xcc"
            + b"\x55\x8b\xec\x83\xec\x10\xc3"          # frame body
            + b"\xcc" * 4
            + b"\xc2\x04\x00"                           # the one-instruction island a slot holds
            + b"\xcc" * 2
            + b"\x51\x56\xc3"                           # a push-reg body: three instructions, so not an island
            + b"\xcc")
    heads = scan(decode, nc, 0x1000, 0x1000 + len(blob), blob)
    assert [h["prologue"] for h in heads] == ["frame", "empty-return", "push-reg"], heads
    assert verdict(heads[0]) == "body" and heads[0]["size"] == 7, heads[0]
    assert verdict(heads[1]) == "island" and heads[1]["size"] == 3, heads[1]
    assert heads[0]["boundary"] == "pad:2", heads[0]
    # a prologue-shaped run that never closes is refused, not called a body
    junk = b"\x90\x90" + b"\x55\x8b\xec" + b"\x90" * 40
    j = scan(decode, nc, 0x2000, 0x2000 + len(junk), junk)
    assert j and verdict(j[0]) == "no-close", j
    # an abutting neighbour: a `ret` then `mov ecx,imm32` is a body boundary with no fill -- nothing
    # in the run branches to it -- and the walk must split there rather than merge the two bodies
    abut = b"\x55\x8b\xec\xc3" + b"\xb9" + struct.pack("<I", 0x7b9ba8) + b"\xc3" + b"\xcc"
    a = scan(decode, nc, 0x3000, 0x3000 + len(abut), abut)
    assert len(a) == 2 and a[0]["size"] == 4 and a[0]["tail"] == "split", a
    assert a[1]["boundary"] == "after-ret" and a[1]["size"] == 6, a
    # `0x00418c30` is five abutting bodies: five heads, each 6 bytes, one pad and four splits
    five = b"\xcc\xcc" + (b"\xb8" + struct.pack("<I", 0x401000) + b"\xc3") * 5 + b"\xcc"
    f = scan(decode, nc, 0x4000, 0x4000 + len(five), five)
    assert len(f) == 5 and {h["size"] for h in f} == {6}, f
    assert [h["boundary"] for h in f] == ["pad:2"] + ["after-ret"] * 4, f
    # an early `return` the run's own branch rejoins stays inside the body: `je` over the `ret` onto
    # the `mov` below it is the `0x00046330` shape, and it must not split
    early = b"\x55\x8b\xec" + b"\x74\x01" + b"\xc3" + b"\xb8" + struct.pack("<I", 1) + b"\xc3" + b"\xcc"
    e = scan(decode, nc, 0x5000, 0x5000 + len(early), early)
    assert len(e) == 1 and e[0]["inner_starts"] == 1 and e[0]["tail"] == "pad", e
    # a body behind code, opened by `8b c1` -- the thiscall opening the shared table does not name, and
    # the shape all three of #808's unlisted constructors open on: the split arm reads it only because
    # `EXTRA_PROLOGUES` makes it a strong opening
    after = b"\xcc\xcc\x55\x8b\xec\xc3" + b"\x8b\xc1\xc3" + b"\xcc"
    ac = scan(decode, nc, 0x5800, 0x5800 + len(after), after)
    assert len(ac) == 2 and ac[1]["boundary"] == "after-ret", ac
    assert ac[1]["prologue"] == "self-return" and ac[1]["size"] == 3, ac
    assert nc.prologue(b"\x8b\xc1" + b"\xc3" * 6) == "other", "the shared table names `8b c1`"
    # a branch into the code behind the close is the same body's early return: no second head
    joined = b"\xcc\xcc\x55\x8b\xec\x74\x01\xc3" + b"\x8b\xc1\xc3" + b"\xcc"
    j2 = scan(decode, nc, 0x5900, 0x5900 + len(joined), joined)
    assert len(j2) == 1 and j2[0]["inner_starts"] == 1, j2
    # the two generated families, from their own instructions
    thunk = b"\x68" + struct.pack("<I", 0x5c29e0) + b"\x68\xff\x00\x00\x00" + b"\xe8" + \
        struct.pack("<i", 0x46c60 - 0x500a - 5) + b"\x83\xc4\x08" + b"\xa2" + \
        struct.pack("<I", 0x6c21ac) + b"\xc3"
    assert family_of(decode, thunk, len(thunk), 0x3e66a3, 0x5000) == ("register-once", 0x5c29e0), thunk
    dtor = b"\x68" + struct.pack("<I", 0x418cf0) + b"\xe8" + struct.pack("<i", 0x3e66a3 - 0x500a) + \
        b"\x59\xc3"
    assert family_of(decode, dtor, len(dtor), 0x3e66a3, 0x5000) == ("atexit", 0x418cf0), dtor
    assert family_of(decode, b"\x55\x8b\xec\xc3", 4, 0x3e66a3, 0x5000) == ("", 0)
    # a `switch`: the body ends `jmp [eax*4+<table>]` and the case blocks behind it are the table's entries,
    # so with the table read they stay one body and without it they are read as neighbours
    cases = b"\x55\x8b\xec" + b"\x33\xc0\xff\x24\x85" + struct.pack("<I", 0x6200)
    case1 = b"\xb8" + struct.pack("<I", 1) + b"\xeb\xf0"
    case2 = b"\xb8" + struct.pack("<I", 2) + b"\xeb\xea"
    tbl = struct.pack("<3I", 0x6010, 0x6017, 0x601e)
    sw = b"\xcc\xcc" + cases + case1 + case2 + b"\xcc" * 3 + tbl + b"\xcc"
    lo_sw = 0x6000
    off1, off2 = 2 + len(cases), 2 + len(cases) + len(case1)
    tset = {lo_sw + off1, lo_sw + off2}
    one = scan(decode, nc, lo_sw, lo_sw + 2 + len(cases) + len(case1) + len(case2), sw, tset)
    assert len(one) == 1 and one[0]["tail"] == "pad", one
    many = scan(decode, nc, lo_sw, lo_sw + 2 + len(cases) + len(case1) + len(case2), sw)
    assert len(many) > 1, many
    # the table index itself: three consecutive code addresses are a table, two are not
    texts = b"".join(struct.pack("<I", 0x7000 + 4 * i) for i in range(3)) + b"\x00" * 4 \
        + struct.pack("<2I", 0x7004, 0x7008)
    tg, runs = text_tables(texts, 0x7000)
    assert tg == {0x7000, 0x7004, 0x7008} and len(runs) == 1, (tg, runs)
    # the gaps: a placed row splits its neighbours, and an overlap does not open one
    table = [(0x1000, 0x1010, "a"), (0x1020, 0x1030, "b"), (0x1030, 0x1040, "c"), (0x1050, 0x1060, "d")]
    assert gaps_of(table, 0x1000, 0x1060) == [(0x1010, 0x1020), (0x1040, 0x1050)], gaps_of(table, 0x1000, 0x1060)
    # the census round trip: render reads the census row, not the measurement
    blank = {k: "" for k in COLS}
    fake = [dict(blank, row="head", head_va="0x1007", gap_lo="0x1000", gap_hi="0x1010", gap_size="16",
                 boundary="pad:2", prologue="frame", verdict="body", size="7", insns="4", close="ret",
                 tail="pad", inner_starts="0", calls="0", jumps="0", imms="0", pointers="2", slots="1",
                 slot_table="0x004c78c0", slot_class="Camera", slot_index="7", family=""),
            dict(blank, row="census", census="text_lo=0x1000;text_hi=0x2000;gaps=1;gap_bytes=16;gaps_under_8=0;"
                 "heads=1;bodies=1;islands=0;no_close=0;undecoded=0;body_bytes=7;body_bytes_pct=44;"
                 "inner_starts=0;bodies_with_inner=0;splits=0;family_register_once=0;family_atexit=0;"
                 "slot_named=1;slot_unclassed=0;tables_unclassed=0;named_pointer=1;named_slot=1;named_branch=0;named_imm=0;"
                 "named_any=1;unnamed=0;map_rows=10;map_rows_head=8;map_rows_pad_only=1;map_rows_prologue_only=1;"
                 "map_rows_decode=9;null_heads_rdata=212;null_bodies_rdata=0;base_gap=22;base_gap_n=2000;"
                 "base_row=481;base_row_n=2000;tables=724;slot_classes=1")]
    m = census_of(fake)
    assert (m["heads"], m["bodies"], m["base_gap_n"]) == (1, 1, 2000), m
    page = render(fake)
    assert "| heads the pad-before + prologue rule finds | 1 |" in page, page
    assert "| `0x1000`..`0x1010` | 1 | 1 | `0x1007` |" in page, page
    assert splice(os.path.join(ROOT, "Makefile"), "x") is None   # no markers: refused, not echoed
    print("ok   gap-heads: 21 assertions (head rule, island, refusal, split, merged early return, the "
          "measured opening that makes a body behind code a split and the branch that cancels it, five "
          "abutting bodies, both families, the switch's case blocks and the table index, gaps, census "
          "round trip, marker refusal)")
    return 0


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("--dump")
    ap.add_argument("-o", "--out")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc", nargs="?", const=DOC, default=None)
    ap.add_argument("--check-doc", nargs="?", const=DOC, default=None)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--emit-adds", action="store_true",
                    help="print the `add` rows the population earns (symbols/xbe_boundaries.csv)")
    ap.add_argument("--apply-adds", action="store_true",
                    help="write those rows, and the xbe_overrides.csv creation row each needs")
    ap.add_argument("--emit-repairs", action="store_true",
                    help="print the `resize` + `add` rows the split rows earn (symbols/xbe_boundaries.csv)")
    ap.add_argument("--apply-repairs", action="store_true",
                    help="write those rows, and the xbe_overrides.csv creation row each add needs")
    ap.add_argument("--add-population", choices=POPULATIONS, default="slot",
                    help="which bodies to add: `slot` (class-named, the default), `family`, or `all`")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.check:
        return check()
    if args.emit_adds:
        return emit_adds(args)
    if args.apply_adds:
        return apply_adds(args)
    if args.emit_repairs:
        return emit_repairs(args)
    if args.apply_repairs:
        return apply_repairs(args)
    if args.report or args.write_doc or args.check_doc:
        rows = _art()
    else:
        if not args.xbe:
            if not args.dump:
                ap.error("--xbe or --dump is required to measure")
            args.xbe = os.path.join(args.dump, GAME, "default.xbe")
        if not os.path.isfile(args.xbe):
            sys.exit("gap-heads: %s is not a file" % args.xbe)
        rows, _m = measure(args)
        if args.out:
            os.makedirs(args.out, exist_ok=True)
            path = os.path.join(args.out, "gap_heads.csv")
            write_artifact(rows, path)
            print("wrote %s (%d head rows + 1 census)" % (path, len(rows) - 1))
    if args.report or args.write_doc or args.check_doc:
        print(render(rows))
    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        new = splice(path, render(rows))
        if new is None:
            print("FAIL gap-heads: markers not found in %s" % path)
            return 1
        old = open(path, encoding="utf-8").read()
        if args.check_doc:
            if new != old:
                print("FAIL gap-heads: %s is stale against the committed artifact" % path)
                return 1
            print("ok   gap-heads: %s matches the committed artifact" % path)
            return 0
        if new != old:
            open(path, "w", encoding="utf-8").write(new)
            print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
