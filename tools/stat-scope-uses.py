#!/usr/bin/env python3
"""Which sites index the engine's cycle-scope array with one of the audio module's eight ids.

`XboxAudio_RegisterCounters` (`0x0002e900`) registers eight named counters in group `Audio` and stores
each returned value at `+0x04`..`+0x20` of an embedded block (the device body hands it `lea ecx,[this+0x184]`).
The previous two bundles read those values as opaque *handles* and left "which method increments an audio
counter" open. They are not opaque: the registration returns an **index into the engine's slot array**, the
pointer the registry object holds at `0x7b5dd8 + 0x24` = `0x7b5dfc`, and a counter is accumulated by the
cycle-scope idiom around it:

    mov  eax,[this+0x1a0]        ; one of the eight ids
    mov  ecx,ds:0x7b5dfc         ; the array base
    lea  ecx,[ecx+eax*4]         ; the slot
    rdtsc
    sub  [ecx],eax               ; entry: subtract the start timestamp
    ...
    add  [ecx],eax               ; exit: add the end timestamp (the difference is the elapsed cycles)

So the increment is not a registry method and not a `Core` initializer: the module does it itself, and this
tool counts where.

**Two readings this tool had wrong, both of which made the population smaller than the image's.** First, the
reference scan kept only *4-byte-aligned* dwords that spell `0x7b5dfc`; but the `A1`/`A3` form of
`mov eax,ds:<abs>` puts its immediate one byte past the opcode, so half of the module's own references --
including the `PlaySound` scope's *open* -- were never candidates. Every byte offset is a candidate now, and a
candidate is a site only when an instruction *contains* it (decoded by a linear sweep of the body it lands
in), so a displacement byte that happens to spell the address is reported as `not-a-boundary` instead of
counted. Second, the index was read only out of a `lea reg,[base+idx*4]`; the compiler also folds the slot
into the operand (`inc DWORD PTR [eax+ecx*4]`), which is the shape 22 sites use. The index register is now
taken from whichever register in the `*4` term is not the one the array was loaded into, and its writer is
traced back to the body's start (past pushes, stopped at a `call` for a volatile register).

    python3 tools/stat-scope-uses.py --xbe DUMP/default.xbe -o inventory/stat_scope_uses.csv
    python3 tools/stat-scope-uses.py --check     # no dump: the artifact against stat_registry.csv and the map

Addresses, offsets, names the module itself pushes and instruction text: no game data.
"""

import argparse
import csv
import os
import re
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

INVENTORY = os.path.join(ROOT, "inventory")
MAP = os.path.join(ROOT, "symbols", "xbox_functions.csv")
REGISTRY_CSV = os.path.join(INVENTORY, "stat_registry.csv")
MODULES = os.path.join(INVENTORY, "function_modules.csv")
OUT = os.path.join(INVENTORY, "stat_scope_uses.csv")

REGISTRY = 0x007b5dd8        # the engine's stat/cycle-scope object
ARRAY = 0x007b5dfc           # REGISTRY + 0x24: pointer to the slot array, indexed by a registered id
REGISTER = 0x002530f0        # the registration method
AUDIO_BODY = 0x0002e900      # XboxAudio_RegisterCounters
DEVICE_BODY = 0x0002fd70     # XboxAudio_CreateDevice, which passes the block's address in ecx
BLOCK_FALLBACK = 0x184       # `lea ecx,[esi+0x184]` before the registration call
CODE_COVER = 0.1             # a section is code when the map's bodies cover this much of it
PAD = 48                     # bytes read past a body's declared size, for a size that stops short

HEADER = ["kind", "site", "insn", "body", "body_name", "module", "index_source", "audio_id", "op", "note"]

LEA_INDEX = re.compile(r"^lea\s+\w+\s*,\s*\[\s*\w+\+(e[a-d]x|e[sd]i|e[bp]p)\*4\s*\]")
LEA_INDEX_2 = re.compile(r"^lea\s+\w+\s*,\s*\[\s*(e[a-d]x|e[sd]i|e[bp]p)\*4\+")
LEA_DST = re.compile(r"^lea\s+(\w+)\s*,")
ARRAY_LOAD = re.compile(r"^(mov|lea)\s+(\w+)\s*,\s*(?:DWORD PTR )?(?:ds:)?0x0*7b5dfc$")
SLOT_TERM = re.compile(r"\[\s*(e[a-d]x|e[sd]i|e[bp]p)\s*\+\s*(e[a-d]x|e[sd]i|e[bp]p)\s*\*4")
FIELD_LOAD = re.compile(r"^(?P<op>mov|movzx|movsx)\s+(?P<dst>\w+)\s*,\s*(?:DWORD PTR |WORD PTR |BYTE PTR )?\[(?P<base>\w+)(?:\+(?P<disp>0x[0-9a-f]+))?\]$")
GLOBAL_LOAD = re.compile(r"^mov\s+(?P<dst>\w+)\s*,\s*(?:DWORD PTR )?(?:ds:)?(?P<va>0x[0-9a-f]{6,})$")
IMM_LOAD = re.compile(r"^mov\s+(?P<dst>\w+)\s*,\s*(?P<va>0x[0-9a-f]{1,5})$")
MOV_REG = re.compile(r"^mov\s+(?P<dst>\w+)\s*,\s*(?P<src>\w+)$")
ARITH = re.compile(r"^(?P<op>sub|add|mov|inc|dec)\s+(?:DWORD PTR |\w+\s*,\s*)?\[(?P<reg>[\w+*]+)\](?:\s*,\s*(?P<src>.+))?$")
JUMP = re.compile(r"^jmp\s+(0x[0-9a-f]+)$")
WRITES = re.compile(r"^(?P<op>mov|movzx|movsx|lea|pop|xor|add|sub|and|or|imul|mul|shl|shr|sar|sal|inc|dec|"
                    r"neg|not|adc|sbb|cmov\w*|xchg|bswap|movs\w*)\s+(?P<a>\w+)\s*[,]?")
VOLATILE = {"eax", "ecx", "edx"}


def read_map():
    rows = []
    with open(MAP, newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append((int(row["address"], 16), row["name"], int(row["size"] or 0), row["module"].strip()))
    return sorted(rows)


def modules():
    out = {}
    with open(MODULES, newline="") as fh:
        for row in csv.DictReader(fh):
            out[int(row["address"], 16)] = row["module"].strip()
    return out


def objdump(blob, base):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
        fh.write(blob)
        temp = fh.name
    try:
        out = subprocess.run(["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                              "--adjust-vma=%d" % base, temp],
                             capture_output=True, text=True).stdout
    finally:
        os.unlink(temp)
    insns = []
    for line in out.splitlines():
        if line.count("\t") < 2:
            continue
        head, rest = line.split("\t", 1)
        try:
            va = int(head.strip().rstrip(":"), 16)
        except ValueError:
            continue
        text = re.sub(r"\s+", " ", rest.split("\t")[-1].strip())
        if text and not text.startswith("."):
            insns.append((va, text))
    insns.sort()
    return insns


def disasm(xbe, va, size):
    return objdump(xbe.read_va(va, size), va)


def code_sections(xbe, funcs):
    """The sections the map's bodies cover at least `CODE_COVER` of: `Section.executable` is set on every
    section of this image, so the flag cannot tell code from a wide-string pool."""
    out = []
    for s in xbe.sections:
        if s.fsize <= 0:
            continue
        covered = 0
        for va, _name, size, _m in funcs:
            lo, hi = max(va, s.va), min(va + size, s.va + s.fsize)
            if hi > lo:
                covered += hi - lo
        if covered >= s.fsize * CODE_COVER:
            out.append(s)
    return out


def ref_candidates(xbe, funcs):
    """Every byte offset in a code section that spells the array's address, aligned or not.

    The aligned-only scan this replaces dropped the `A1`/`A3` form, whose immediate sits one byte past the
    opcode: half of the audio module's own references, the `PlaySound` scope's open among them.
    """
    needle = struct.pack("<I", ARRAY)
    hits = []
    for s in code_sections(xbe, funcs):
        blob = xbe.data[s.foff:s.foff + s.fsize]
        start = 0
        while True:
            i = blob.find(needle, start)
            if i < 0:
                break
            hits.append((s.va + i, i % 4))
            start = i + 1
    hits.sort()
    return hits


def spans(insns):
    """(start, end) per instruction, from the next instruction's address."""
    out = []
    for k, (va, _t) in enumerate(insns):
        end = insns[k + 1][0] if k + 1 < len(insns) else va + 16
        out.append((va, end))
    return out


def contains(insns, sp, cand):
    for k, (lo, hi) in enumerate(sp):
        if lo <= cand < hi:
            return k
    return None


def array_reg(insns, i):
    """The register the array base was loaded into, and the instruction that loaded it."""
    for j in range(i, max(-1, i - 6), -1):
        m = ARRAY_LOAD.match(insns[j][1])
        if m:
            return m.group(2), j
    return None, None


def slot_term(insns, i, base):
    """The `base+idx*4` operand that indexes the array, and the index register.

    The compiler writes the same slot two ways: through a `lea` into a slot register (the scope idiom) and
    inline in the operand (`inc DWORD PTR [eax+ecx*4]`, which is what 22 sites use). In both, the index is
    the `*4` register that is *not* the array base -- the operand's register order is not a claim.
    """
    for j in list(range(i, min(len(insns), i + 7))) + list(range(i - 1, max(-1, i - 5), -1)):
        text = insns[j][1]
        m = SLOT_TERM.search(text)
        if not m:
            continue
        a, b = m.group(1), m.group(2)
        if base in (a, b):
            idx = b if a == base else a
            dst = LEA_DST.match(text)
            slot = dst.group(1) if (dst and text.startswith("lea")) else "%s+%s*4" % (a, b)
            return idx, slot, j
    return None, None, None


def writer(insns, stop, reg):
    """The last instruction before `stop` that writes `reg`: `(index, text)` or `(None, None)`.

    Pushes and unrelated instructions are crossed; a `call` stops the walk for a volatile register, because
    the callee may clobber it (the previous reader gave up after eight instructions instead and called every
    such site `unread`).
    """
    for j in range(stop - 1, -1, -1):
        text = insns[j][1]
        if text.startswith("call ") or text.startswith("jmp "):
            if reg in VOLATILE:
                return None, None
            continue
        m = WRITES.match(text)
        if m and m.group("a") == reg:
            return j, text
    return None, None


def classify_writer(text):
    """Name a writer's source in the artifact's vocabulary: field/global/immediate/stack/register."""
    f = FIELD_LOAD.match(text)
    if f:
        base = f.group("base")
        disp = f.group("disp") or "0x0"
        if base == "esp":
            return "stack %s" % disp
        return "field %s of %s" % (disp, base)
    g = GLOBAL_LOAD.match(text)
    if g:
        return "global 0x%08x" % int(g.group("va"), 16)
    i = IMM_LOAD.match(text)
    if i:
        return "immediate %s" % i.group("va")
    return None


def index_source(insns, i):
    """Classify the register that indexes the array, from the instructions around one array reference.

    Returns (source, idx_reg, slot) where `source` is `field +0xNN of <base>`, `global 0xNNNN`,
    `immediate 0xNN`, `stack +0xNN`, `register <src>` or `unread`.
    """
    base, _load = array_reg(insns, i)
    if base is None:
        # no array load next to the site: fall back on the shape of the operand itself
        m = SLOT_TERM.search(insns[i][1])
        if not m:
            return "unread", None, None
        idx = m.group(2)
        slot = "%s+%s*4" % (m.group(1), m.group(2))
        at = i
    else:
        idx, slot, at = slot_term(insns, i, base)
        if idx is None:
            return "unread", None, None
    j, text = writer(insns, at, idx)
    if text is None:
        return "unread", idx, slot
    src = classify_writer(text)
    if src is not None:
        return src, idx, slot
    hop = MOV_REG.match(text)
    if hop and hop.group("src") not in ("esp",):
        j2, text2 = writer(insns, j, hop.group("src"))
        src2 = classify_writer(text2) if text2 else None
        if src2 is not None:
            return src2, idx, slot
        return "register %s" % hop.group("src"), idx, slot
    return "register %s" % (MOV_REG.match(text).group("src") if MOV_REG.match(text) else text), idx, slot


def window(insns, at, slot):
    """Instructions of one scope block: from the slot expression to the end of its use.

    A slot register (`lea ecx,[eax+edx*4]`) is reused by the next scope in the same body, so the window
    stops at the first instruction that reloads it -- otherwise the following block's `sub` reads as this
    site's open (measured: an `add tsc` close at `0x00114278` classified `sub tsc` from the block after it).
    A `ret` or an unconditional `jmp` ends the block too; a `jmp` is kept because the two streaming
    counters share one merged tail that both branches jump into.
    """
    texts = []
    slot_writes = 0
    for va, text in insns[at:at + 24]:
        if texts:
            if re.match(r"^ret\b", text):
                texts.append(text)
                break
            if re.match(r"^jmp\s+0x", text):
                texts.append(text)
                break
            if ARRAY_LOAD.match(text):
                # every scope block begins by reloading the array base, and the next block's index is
                # loaded into the same register this one's slot lives in -- so this, not a write count,
                # is what separates two blocks (a write count cut the forming `lea` off its own index load)
                break
        texts.append(text)
    return texts


def _arith_name(op, kind):
    if kind == "tsc":
        return "%s tsc" % op
    if kind == "1":
        return "%s by 1" % ("inc" if op == "add" else "dec")
    return "%s delta" % op


def operation(insns, at, idx_reg=None, slot=None):
    """What the site does with the slot, in the shapes the compiler emits for a profiled counter.

    Five shapes, all of them one idiom over a slot in the engine's array:

    * `sub DWORD PTR [slot],<tsc>` / `mov R,[slot]` `sub R,<tsc>` `mov [slot],R` -- scope **opened**;
    * the `add` of the same -- scope **closed** (the `-12` in `add edx,0xfffffff4` is the `rdtsc` overhead);
    * `inc`/`dec` of the slot -- a plain count (`Streaming`, `Regular`, `PlaySound`'s second id);
    * `add`/`sub` of a value that is **not** a timestamp -- an accumulate by a measured delta (a byte count,
      a sound's remaining time), which the old classifier reported as `read only`;
    * nothing written at all -- `read only`, a sample of the slot.

    `slot` is either a register the expression was `lea`'d into or the `base+idx*4` text of an inline
    operand. `add`/`sub DWORD PTR [slot],0x1` is the compiler's spelling of an increment.
    """
    texts = window(insns, at, slot)
    tsc_regs = set()
    for t in texts:
        m = re.match(r"^mov\s+(\w+)\s*,\s*(?:DWORD PTR )?(?:ds:)?0x0*5d65e8$", t)
        if m:
            tsc_regs.add(m.group(1))
    slot_read = set()
    slot_addr = set()
    if slot:
        for t in texts:
            m = re.match(r"^mov\s+(\w+)\s*,\s*DWORD PTR \[%s\]$" % re.escape(slot), t)
            if m:
                slot_read.add(m.group(1))
            m = re.match(r"^lea\s+(\w+)\s*,\s*\[%s\]$" % re.escape(slot), t)
            if m:
                slot_addr.add(m.group(1))
    has_tsc = any(re.match(r"^rdtsc$", t) for t in texts) or bool(tsc_regs)

    def stored(reg):
        pat = r"^mov\s+DWORD PTR \[(?:%s)\]" % (re.escape(slot) if slot else r"\w+")
        for t in texts:
            if re.match(pat + r",\s*%s$" % re.escape(reg), t):
                return True
            for a in slot_addr:
                if re.match(r"^mov\s+DWORD PTR \[%s\],\s*%s$" % (re.escape(a), re.escape(reg)), t):
                    return True
        return False

    # 1. arithmetic straight on the slot
    for t in texts:
        m = re.match(r"^(sub|add)\s+DWORD PTR \[%s\],\s*(0x[0-9a-f]+|\w+)$"
                     % (re.escape(slot) if slot else r"\w+"), t)
        if m:
            v = m.group(2)
            kind = "tsc" if (v in tsc_regs or (has_tsc and v not in ("0x0", "0x1"))) else \
                   ("1" if v in ("0x1", "0xffffffff") else "delta")
            return _arith_name(m.group(1), kind)
        m = re.match(r"^(inc|dec)\s+DWORD PTR \[%s\]$" % (re.escape(slot) if slot else r"\w+"), t)
        if m:
            return "%s by 1" % m.group(1)
    # 2. a register round-trip: load the slot, change it, store it back
    for t in texts:
        m = re.match(r"^(add|sub)\s+(\w+),\s*(0x[0-9a-f]+|\w+)$", t)
        if m and m.group(2) in slot_read and stored(m.group(2)):
            v = m.group(3)
            kind = "tsc" if v in tsc_regs else ("1" if v == "0x1" else "delta")
            return _arith_name(m.group(1), kind)
        m = re.match(r"^(inc|dec)\s+(\w+)$", t)
        if m and m.group(2) in slot_read and stored(m.group(2)):
            return "%s by 1" % m.group(1)
    # 3. a merged tail: an unconditional jmp out of the window is shared by two branches
    for k, t in enumerate(texts):
        if k and JUMP.match(t):
            target = int(JUMP.match(t).group(1), 16)
            for j, (va, _t) in enumerate(insns):
                if va == target:
                    return operation(insns, j, idx_reg, slot)
            break
    return "read only"


def audio_ids(xbe, funcs):
    """The eight registrations, as {offset in the block: name} with the block base read from the device body."""
    rows = list(csv.DictReader(open(REGISTRY_CSV, newline="")))
    regs = [r for r in rows if r["kind"] == "registration" and r["body"].lower() == "0x%08x" % AUDIO_BODY]
    block = BLOCK_FALLBACK
    device = next((f for f in funcs if f[0] == DEVICE_BODY), None)
    if device:
        insns = disasm(xbe, device[0], device[2])
        for i, (va, text) in enumerate(insns):
            if re.match(r"^call\s+0x0*%x$" % AUDIO_BODY, text):
                for back in range(i - 1, max(-1, i - 4), -1):
                    m = re.match(r"^lea\s+ecx\s*,\s*\[e\w+\+(0x[0-9a-f]+)\]$", insns[back][1])
                    if m:
                        block = int(m.group(1), 16)
                        break
    out = {}
    for r in regs:
        m = re.match(r"e\w+\+(0x[0-9a-f]+)$", r["store"].strip())
        if not m:
            continue
        out[block + int(m.group(1), 16)] = r["name"]
    return out, block


def build(args):
    from xbe import Xbe  # noqa: PLC0415

    xbe = Xbe(args.xbe)
    funcs = read_map()
    mods = modules()
    ids, block = audio_ids(xbe, funcs)
    if len(ids) != 8:
        raise SystemExit("expected the eight audio registrations in %s, read %d" % (REGISTRY_CSV, len(ids)))

    # candidates -> (body index or None, [operand addresses])
    body_of = {}
    loose = []
    for cand, _align in ref_candidates(xbe, funcs):
        k = None
        for j, (base, _name, size, _m) in enumerate(funcs):
            if base <= cand < base + size:
                k = j
                break
        if k is None:
            loose.append(cand)
        else:
            body_of.setdefault(k, []).append(cand)

    rows = []
    not_boundary = []
    noload = []
    bodies = set()
    per_id = {off: [] for off in ids}

    def emit(base, name, site, insn, insns, i, note=""):
        source, _idx, slot = index_source(insns, i)
        op = operation(insns, i, _idx, slot)
        hit = None
        m = re.match(r"^field (0x[0-9a-f]+) of ", source)
        if m:
            off = int(m.group(1), 16)
            if off in ids:
                hit = off
        rows.append({
            "kind": "scope-site", "site": "0x%08x" % site, "insn": "0x%08x" % insn,
            "body": "0x%08x" % base if base is not None else "",
            "body_name": name, "module": mods.get(base, "(unnamed)") if base is not None else "",
            "index_source": source,
            "audio_id": "%s (+0x%02x)" % (ids[hit], hit) if hit is not None else "",
            "op": op, "note": note,
        })
        if base is not None:
            bodies.add(base)
        if hit is not None:
            per_id[hit].append((base, name, site))
        if source == "unread":
            noload.append(site)

    for k in sorted(body_of):
        base, name, size, _m = funcs[k]
        # read past the declared size: a symbol row can stop short of its own last store (the pump at
        # 0x0002feb0 is 5,277 bytes and its closing store ends at 0x00031360), and a window that ends
        # with the row then loses the store and calls the close `read only`.
        insns = disasm(xbe, base, size + PAD)
        sp = spans(insns)
        for cand in sorted(body_of[k]):
            i = contains(insns, sp, cand)
            if i is None or "0x7b5dfc" not in insns[i][1]:
                not_boundary.append((cand, base, name))
                continue
            emit(base, name, cand, insns[i][0], insns, i)

    # a reference outside every map body is still a reference: decode a window around it and say so
    for cand in loose:
        insns = disasm(xbe, cand - 0x40, 0x60)
        sp = spans(insns)
        i = contains(insns, sp, cand)
        if i is None or "0x7b5dfc" not in insns[i][1]:
            not_boundary.append((cand, None, ""))
            continue
        emit(None, "(no body)", cand, insns[i][0], insns, i, note="outside every map body")

    for cand, base, name in not_boundary:
        rows.append({
            "kind": "not-a-boundary", "site": "0x%08x" % cand, "insn": "",
            "body": "0x%08x" % base if base is not None else "", "body_name": name, "module": "",
            "index_source": "", "audio_id": "", "op": "",
            "note": "the dword sits inside an instruction's displacement, not at an instruction boundary",
        })

    for off in sorted(ids):
        hits = per_id[off]
        rows.append({
            "kind": "id", "site": "0x%08x" % off, "insn": "", "body": "", "body_name": ids[off],
            "module": "", "index_source": "%d site(s)" % len(hits),
            "audio_id": ids[off], "op": "used" if hits else "never indexed",
            "note": "" if hits else "registered, and no site in the image indexes the array with it",
        })
    sites = [r for r in rows if r["kind"] == "scope-site"]
    wob = len([r for r in sites if not r["body"]])
    rows.append({
        "kind": "census", "site": "0x%08x" % ARRAY, "insn": "", "body": "",
        "body_name": "%d site(s) over %d bod%s" % (len(sites), len(bodies), "y" if len(bodies) == 1 else "ies"),
        "module": "", "index_source": "%d id(s) used of %d" % (sum(1 for o in per_id if per_id[o]), len(ids)),
        "audio_id": "", "op": "",
        "note": "%d reference(s) total, %d outside every map body, %d not-a-boundary, "
                "%d with no resolved index source; block base +0x%02x read from the device body"
                % (len(sites) + len(not_boundary), wob, len(not_boundary),
                   len(noload), block),
    })
    return rows


def write(rows, path):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def check(path):
    spans_by_name = {}
    for va, name, size, _m in read_map():
        spans_by_name.setdefault(name, []).append((va, size))
    regs = [r for r in csv.DictReader(open(REGISTRY_CSV, newline=""))
            if r["kind"] == "registration" and r["body"].lower() == "0x%08x" % AUDIO_BODY]
    want = {}
    for r in regs:
        m = re.match(r"e\w+\+(0x[0-9a-f]+)$", r["store"].strip())
        if m:
            want[BLOCK_FALLBACK + int(m.group(1), 16)] = r["name"]
    rows = list(csv.DictReader(open(path, newline="")))
    bad = []
    if len(want) != 8:
        bad.append("stat_registry.csv holds %d audio store offsets, expected 8" % len(want))
    ids = [r for r in rows if r["kind"] == "id"]
    if len(ids) != 8:
        bad.append("%d id rows, expected 8" % len(ids))
    seen = {}
    for r in ids:
        off = int(r["site"], 16)
        seen[off] = int(r["index_source"].split()[0])
        if want.get(off) != r["audio_id"]:
            bad.append("id 0x%08x is %r in the artifact and %r in stat_registry.csv"
                       % (off, r["audio_id"], want.get(off)))
        if r["body_name"] != r["audio_id"]:
            bad.append("id 0x%08x: name column %r does not match audio_id %r" % (off, r["body_name"], r["audio_id"]))
    if set(seen) != set(want):
        bad.append("id offsets differ from stat_registry.csv: %s"
                   % sorted("0x%02x" % o for o in set(seen) ^ set(want)))
    sites = [r for r in rows if r["kind"] == "scope-site"]
    for off, n in seen.items():
        got = len([r for r in sites if r["audio_id"].endswith("(+0x%02x)" % off)])
        if got != n:
            bad.append("id 0x%02x: %d site rows, id row says %d" % (off, got, n))
    for r in sites:
        if not r["body"]:
            if r["note"] != "outside every map body":
                bad.append("%s: a body-less site must say so in its note" % r["site"])
            if not r["index_source"]:
                bad.append("%s: site %s carries no index source" % (r["body"], r["site"]))
            continue
        if r["body_name"] not in spans_by_name:
            bad.append("%s: %s is not in the symbol map" % (r["body"], r["body_name"]))
            continue
        base = int(r["body"], 16)
        size = dict(spans_by_name[r["body_name"]]).get(base)
        if size is None:
            bad.append("%s: %s is not a start of %s" % (r["body"], r["body"], r["body_name"]))
            continue
        if not base <= int(r["site"], 16) < base + size:
            bad.append("%s: site %s is outside the body" % (r["body"], r["site"]))
        if not base <= int(r["insn"], 16) < base + size:
            bad.append("%s: instruction %s is outside the body" % (r["body"], r["insn"]))
        if not r["index_source"]:
            bad.append("%s: site %s carries no index source" % (r["body"], r["site"]))
    for r in [r for r in rows if r["kind"] == "not-a-boundary"]:
        if not r["note"].startswith("the dword sits inside"):
            bad.append("%s: a not-a-boundary row must carry its note" % r["site"])
    census = [r for r in rows if r["kind"] == "census"]
    if len(census) != 1:
        bad.append("%d census rows, expected 1" % len(census))
    else:
        note = census[0]["note"]
        m = re.match(r"^(\d+) reference\(s\) total, (\d+) outside every map body, (\d+) not-a-boundary, "
                     r"(\d+) with no resolved", note)
        if not m:
            bad.append("census note does not start with the four counts: %r" % note)
        else:
            total, wob, nb, unread = (int(m.group(k)) for k in (1, 2, 3, 4))
            n_sites = len(sites)
            n_wob = len([r for r in sites if not r["body"]])
            n_nb = len([r for r in rows if r["kind"] == "not-a-boundary"])
            if total != n_sites + n_nb:
                bad.append("census says %d reference(s), the artifact lists %d sites and %d not-a-boundary"
                           % (total, n_sites, n_nb))
            if wob != n_wob:
                bad.append("census says %d outside every map body, the artifact lists %d" % (wob, n_wob))
            if nb != n_nb:
                bad.append("census says %d not-a-boundary, the artifact lists %d" % (nb, n_nb))
            if unread != len([r for r in sites if r["index_source"] == "unread"]):
                bad.append("census says %d with no resolved index source, the artifact lists %d"
                           % (unread, len([r for r in sites if r["index_source"] == "unread"])))
        used = sum(1 for off, n in seen.items() if n)
        if "%d id(s) used" % used not in census[0]["index_source"]:
            bad.append("census says %r, the id rows say %d used" % (census[0]["index_source"], used))
    if bad:
        for b in bad:
            print("FAIL stat-scope-uses: %s" % b)
        return 1
    print("ok stat-scope-uses: %d scope site(s), %d not-a-boundary, 8 ids, %d used, %d never indexed"
          % (len(sites), len([r for r in rows if r["kind"] == "not-a-boundary"]),
             sum(1 for n in seen.values() if n), sum(1 for n in seen.values() if not n)))
    return 0


def selftest():
    """The index-source parser over synthetic instruction rows: no dump, no writes."""
    cases = [
        (["mov eax,DWORD PTR [esi+0x1a0]", "mov ecx,DWORD PTR ds:0x7b5dfc", "lea eax,[ecx+eax*4]"],
         2, "field 0x1a0 of esi"),
        (["mov eax,DWORD PTR [ebp+0x18c]", "mov ecx,DWORD PTR ds:0x7b5dfc", "lea ecx,[ecx+eax*4]"],
         2, "field 0x18c of ebp"),
        (["mov eax,ds:0x7b6090", "mov edx,DWORD PTR ds:0x7b5dfc", "lea ecx,[edx+eax*4]"],
         2, "global 0x007b6090"),
        (["mov eax,0x3", "mov ecx,DWORD PTR ds:0x7b5dfc", "lea ecx,[ecx+eax*4]"],
         2, "immediate 0x3"),
        # the inline operand form, with the index loaded two instructions before the array base
        (["mov ecx,DWORD PTR ds:0x7b5f88", "push esi", "push edi", "mov eax,ds:0x7b5dfc",
          "lea ecx,[eax+ecx*4]"], 3, "global 0x007b5f88"),
        # `inc DWORD PTR [eax+ecx*4]`: no `lea`, index is the field, base is the register loaded with the array
        (["mov ecx,DWORD PTR [edi+0x1a4]", "mov edx,DWORD PTR ds:0x7b5dfc", "inc DWORD PTR [edx+ecx*4]"],
         2, "field 0x1a4 of edi"),
        # a push between the array load and the slot expression must not stop the trace
        (["mov eax,DWORD PTR [esi+0x188]", "mov ecx,DWORD PTR ds:0x7b5dfc", "push ebx", "push ebp",
          "lea ecx,[ecx+eax*4]"], 2, "field 0x188 of esi"),
    ]
    bad = 0
    for texts, i, want in cases:
        insns = [(0x1000 + 4 * k, t) for k, t in enumerate(texts)]
        got, _reg, _slot = index_source(insns, i)
        if got != want:
            print("FAIL stat-scope-uses selftest: %r -> %r, expected %r" % (texts, got, want))
            bad += 1
    # the operation classifier: an add/sub after the `lea` that follows an `rdtsc` is a timestamp scope
    ops = [("mov ecx,ds:0x7b5dfc", "lea ecx,[ecx+eax*4]", "rdtsc", "sub DWORD PTR [ecx],eax")]
    insns = [(0x2000 + 4 * k, t) for k, t in enumerate(ops[0])]
    got = operation(insns, 1, "eax", "ecx")
    if got != "sub tsc":
        print("FAIL stat-scope-uses selftest: %r -> %r, expected 'sub tsc'" % (ops[0], got))
        bad += 1
    plain = [("mov ecx,ds:0x7b5dfc", "lea ecx,[ecx+eax*4]", "mov ecx,DWORD PTR [ecx]")]
    got = operation([(0x3000 + 4 * k, t) for k, t in enumerate(plain[0])], 1, "eax", "ecx")
    if got != "read only":
        print("FAIL stat-scope-uses selftest: %r -> %r, expected 'read only'" % (plain[0], got))
        bad += 1
    # the inline operand form: `inc DWORD PTR [eax+ecx*4]` is a plain counter, not a read
    inline = ["mov edx,ds:0x7b5dfc", "mov ecx,DWORD PTR [edi+0x1a4]", "inc DWORD PTR [edx+ecx*4]"]
    got = operation([(0x4000 + 4 * k, t) for k, t in enumerate(inline)], 2, "ecx", "edx+ecx*4")
    if got != "inc by 1":
        print("FAIL stat-scope-uses selftest: %r -> %r, expected 'inc by 1'" % (inline, got))
        bad += 1
    # a candidate inside an instruction's displacement is not a site
    insns = [(0x5000, "mov eax,0x7b5dfc"), (0x5005, "ret")]
    k = contains(insns, spans(insns), 0x5001)
    if k != 0:
        print("FAIL stat-scope-uses selftest: a dword at 0x5001 should be inside the instruction at 0x5000, got %r" % k)
        bad += 1
    k = contains(insns, spans(insns), 0x5030)
    if k is not None:
        print("FAIL stat-scope-uses selftest: a dword past the last instruction should have no boundary, got %r" % k)
        bad += 1
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out", default=OUT)
    ap.add_argument("--check", nargs="?", const=OUT, help="validate the artifact without the dump")
    ap.add_argument("--selftest", action="store_true", help="parser only, no dump and no writes")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.check:
        return check(args.check)
    if not args.xbe:
        ap.error("--xbe is required unless --check or --selftest is given")
    rows = build(args)
    write(rows, args.out)
    for r in rows:
        if r["kind"] == "id":
            print("%-22s +0x%-4s %-52s %s" % (r["audio_id"], r["site"][2:], r["index_source"], r["op"]))
        elif r["kind"] == "census":
            print("%-22s %s %s" % ("census", r["body_name"], r["index_source"]))
            print("   %s" % r["note"])
        elif r["audio_id"]:
            print("%s %-24s %-22s %s" % (r["site"], r["body_name"], r["audio_id"], r["op"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
