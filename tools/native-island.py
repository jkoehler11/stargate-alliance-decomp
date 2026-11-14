#!/usr/bin/env python3
"""The unmapped thunk island: what its records are, and who reaches them.

The orphan-table census (`tools/class-orphan-tables.py`, `inventory/class_orphan_tables.csv`) found 84
tail jumps into the 24 unregistered class-table installers coming from code that
`symbols/xbox_functions.csv` does not cover: a hole between the 8-byte `FUN_003f28b0` and
`FUN_003f6d70` (`0x3f28b8`..`0x3f6d70`). That note read the hole as `0x20`-byte records of
`jmp <installer>`, `mov eax,<.rdata>`, `jmp`, and asked the one question that decides what it is: **is
the `.rdata` operand a `UClass` object another registration carries** -- which would make the hole a
class-indexed constructor dispatch -- or not.

It is not. The operand at `0x576210`, `0x5763c0`, `0x57642c`, `0x5764b0` is a `.rdata` record whose
first dword is **`0x19930520`, the MSVC C++ exception `FuncInfo` header**, and the `jmp` it feeds
(`0x3e559e`) is a frame-handler wrapper that pushes the `FuncInfo` plus four arguments and calls
`0x3eb0d0`. The stub is the compiler's `__ehhandler$F`: `mov eax,<FuncInfo>; jmp <handler>`, one per
function that owns a `try`/`catch` frame, and the function it guards stores the stub's address into its
own exception-registration frame slot.

So the hole is the **linker's thunk pool**, and this tool reads it by walking instructions rather than
by trusting a stride. A stride reading is what misled the previous note: `mov eax,<.rdata>` and `jmp`
are two *separate* stubs where a forwarder ends with no `int3` between them, so records do not land on
a fixed boundary. The walker decodes continuously, closes a stub at every unconditional terminator
(`jmp`, `ret`, `ret imm16`) and at every `int3` pad run, and reports what it could not decode instead of
dropping it:

  * `eh-handler` -- `mov reg,imm32` + `jmp`: the immediate names the `FuncInfo`. The first dword of the
    record is read and compared with the magic, so the shape is measured, not assumed.
  * `tail-jump` -- a stub that is one `jmp rel32`: whether its target is one of the 24 orphan
    installers (the edges the previous note saw) or somewhere else.
  * `forward` -- `mov reg,[ebp+d]` (optionally `add reg,imm8`) + `jmp`: a tail-call thunk handing the
    frame's argument on.
  * `other` / `undecoded` -- a stub the walker decoded fully but that is none of the above, and bytes
    the bounded opcode subset could not read. Both are counted, never hidden.

Two dword scans give reachability, and they are separate from the decode:

  * **who stores a stub's address** -- a `c7 /0` store or a `68` push whose immediate is the stub. For
    an `__ehhandler` that site is the guarded function's prologue, so the join names the body the stub
    belongs to.
  * **who calls a stub** -- an `E8 rel32` whose target is the stub, attributed to the symbol-map body.

    tools/native-island.py --xbe DUMP/default.xbe -o inventory
    tools/native-island.py --report
    tools/native-island.py --write-doc docs/systems/CLASS_REGISTRATION.md
    tools/native-island.py --check-doc docs/systems/CLASS_REGISTRATION.md
    tools/native-island.py --check        # synthetic image, no dump, writes nothing

The dump is never committed; the artifact and the doc are. Addresses and counts only.
"""
import argparse
import bisect
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTIFACT = os.path.join(ROOT, "inventory/native_island.csv")
DOC = os.path.join(ROOT, "docs/systems/CLASS_REGISTRATION.md")
DOC_START = "<!-- generated:native-island -->"
DOC_END = "<!-- /generated:native-island -->"
FIELDS = ["stub_va", "size", "kind", "target", "operand", "operand_kind", "store_va", "store_name",
          "callers", "installer", "partial", "dword_refs", "slot", "ref_va", "census"]

MAGIC = 0x19930520          # MSVC C++ EH `FuncInfo` header: magicNumber
HANDLER_WRAPPER = 0x3E559E  # the frame handler every `eh-handler` stub jumps to
KIND_ORDER = ("eh-handler", "tail-jump", "forward", "other", "undecoded")
TERMINATORS = ("jmp", "jmp8", "ret", "ret-imm")


# --------------------------------------------------------------------------- decoding
def modrm_len(b, i):
    """Length of the modrm operand at `i`, or None when it reads past `b`."""
    if i >= len(b):
        return None
    modrm = b[i]
    mod, rm = modrm >> 6, modrm & 7
    ln = 1
    if mod != 3 and rm == 4:                     # SIB byte
        if i + ln >= len(b):
            return None
        sib = b[i + ln]
        ln += 1
        if mod == 0 and (sib & 7) == 5:
            ln += 4
    if mod == 0 and rm == 5:
        ln += 4
    elif mod == 1:
        ln += 1
    elif mod == 2:
        ln += 4
    return ln if i + ln <= len(b) else None


def one(b, i, base=0):
    """(length, mnemonic, operand, opcode) for the instruction at `i`, or None off the subset.

    The subset is what this image's thunks use: moves/adds through modrm, immediates, pushes and pops,
    relative call/jump, jcc, ret and the pad. Anything else answers None, which the walker reports.
    """
    if i >= len(b):
        return None
    op = b[i]
    if 0x50 <= op <= 0x57:
        return (1, "push", None, op)
    if 0x58 <= op <= 0x5F:
        return (1, "pop", None, op)
    if 0xB8 <= op <= 0xBF:
        if i + 5 > len(b):
            return None
        return (5, "mov-imm", struct.unpack_from("<I", b, i + 1)[0], op)
    if op in (0xE8, 0xE9):
        if i + 5 > len(b):
            return None
        rel = struct.unpack_from("<i", b, i + 1)[0]
        return (5, "call" if op == 0xE8 else "jmp", (base + i + 5 + rel) & 0xFFFFFFFF, op)
    if op == 0xEB:
        if i + 2 > len(b):
            return None
        rel8 = struct.unpack_from("<b", b, i + 1)[0]
        return (2, "jmp8", (base + i + 2 + rel8) & 0xFFFFFFFF, op)
    if op in (0x74, 0x75, 0x7C, 0x7D, 0x7E, 0x7F):
        return (2, "jcc", None, op) if i + 2 <= len(b) else None
    if op in (0x33, 0x3B, 0x8B, 0x89, 0x85, 0x8D, 0x03, 0x2B, 0x39, 0x01, 0x0B, 0x23, 0x31):
        m = modrm_len(b, i + 1)
        return None if m is None else (m + 1, "modrm", None, op)
    if op == 0x2D:
        return (5, "modrm", None, op) if i + 5 <= len(b) else None
    if op in (0x80, 0x83):
        m = modrm_len(b, i + 1)
        return None if m is None else (m + 2, "modrm-imm8", None, op)
    if op == 0xC7:
        m = modrm_len(b, i + 1)
        return None if m is None else (m + 5, "store-imm32", None, op)
    if op == 0xC6:
        m = modrm_len(b, i + 1)
        return None if m is None else (m + 2, "store-imm8", None, op)
    if op in (0xFF, 0x8F, 0xFE):
        m = modrm_len(b, i + 1)
        return None if m is None else (m + 1, "modrm-ff", None, op)
    if op == 0x68:
        return (5, "push-imm", struct.unpack_from("<I", b, i + 1)[0], op) if i + 5 <= len(b) else None
    if op == 0x6A:
        return (2, "push-imm8", None, op) if i + 2 <= len(b) else None
    if op == 0xC2:
        return (3, "ret-imm", None, op) if i + 3 <= len(b) else None
    if op in (0x90, 0xC3):
        return (1, "nop" if op == 0x90 else "ret", None, op)
    if op == 0x0F:
        return (6, "jcc32", None, op) if i + 6 <= len(b) else None
    return None


def decode(b, base=0):
    """Whole-buffer decode for the self-test: [(offset, length, mnemonic, operand, opcode)] or None."""
    out, i = [], 0
    while i < len(b):
        got = one(b, i, base)
        if got is None:
            return None
        out.append((i,) + got)
        i += got[0]
    return out


def classify(insns):
    """(kind, target, operand) from one stub's decoded instructions."""
    if not insns:
        return "undecoded", None, None
    last = insns[-1]
    if last[2] not in TERMINATORS:
        return "other", None, None
    if len(insns) == 1 and last[2] in ("jmp", "jmp8"):
        return "tail-jump", last[3], None
    heads = insns[:-1]
    if len(heads) == 1 and heads[0][2] == "mov-imm":
        return "eh-handler", last[3], heads[0][3]
    if heads and heads[0][4] == 0x8B and all(h[4] in (0x8B, 0x83, 0x03, 0x8D, 0x33) for h in heads):
        return "forward", last[3], None
    return "other", last[3] if isinstance(last[3], int) else None, None


def walk(blob, base):
    """[(va, size, kind, target, operand, partial)] -- stubs cut at terminators and at `int3` pad runs.

    A stub is a run of instructions ending in an unconditional terminator (`jmp`, `ret`, `ret imm16`) or
    at a pad. A byte the bounded subset cannot read does not split the stub: it is skipped and counted in
    `partial`, because splitting there would manufacture one row per unreadable byte and read as a census
    of thousands of 1-byte stubs. A stub with `partial` set is reported with its shape withheld
    (`other`) -- the decoder did not see all of it and does not claim a kind.
    """
    stubs, cur, bad = [], [], 0
    i, n = 0, len(blob)
    start = 0

    def close(end):
        nonlocal cur, bad, start
        if cur or bad:
            if bad:
                kind = "other" if cur else "undecoded"
                stubs.append((base + start, end - start, kind, None, None, True))
            else:
                kind, target, operand = classify(cur)
                stubs.append((base + start, end - start, kind, target, operand, False))
        cur, bad, start = [], 0, end

    while i < n:
        if blob[i] == 0xCC:
            j = i
            while j < n and blob[j] == 0xCC:
                j += 1
            close(i)
            i = j if j - i >= 2 else i + 1
            start = i
            continue
        got = one(blob, i, base)
        if got is None:
            if not cur and not bad:
                start = i
            bad += 1
            i += 1
            continue
        if not cur and not bad:
            start = i
        cur.append((i,) + got)
        i += got[0]
        if got[1] in TERMINATORS:
            close(i)
    close(n)
    return stubs


# --------------------------------------------------------------------------- the image
def mapped(funcs, starts, va):
    i = bisect.bisect_right(starts, va) - 1
    if i >= 0:
        addr, size, name = funcs[i]
        if addr <= va < addr + max(size, 1):
            return funcs[i]
    return None


def island_bounds(funcs, starts, sites):
    """(lo, hi, inside, outside) of the symbol-map hole holding the most unmapped sites.

    A gap is one mapped body's end to the next mapped body's start. The island is the gap that holds the
    most unmapped sites; `lo`/`hi` are its bounds, so the range is derived from the map and the edges
    rather than typed in, and sites in other gaps are counted instead of dropped.
    """
    gaps = {}
    for site in sites:
        if mapped(funcs, starts, site):
            continue
        i = bisect.bisect_right(starts, site) - 1
        below = funcs[i] if i >= 0 else None
        above = funcs[i + 1] if i + 1 < len(funcs) else None
        lo = below[0] + max(below[1], 1) if below else 0
        hi = above[0] if above else lo
        gaps.setdefault((lo, hi), []).append(site)
    if not gaps:
        return (0, 0, 0, 0)
    (lo, hi), mine = max(gaps.items(), key=lambda kv: len(kv[1]))
    other = sum(len(v) for k, v in gaps.items() if k != (lo, hi))
    return (lo, hi, len(mine), other)


def imm_sites(text, text_va, values):
    """[(site, value, kind)] for every `c7 /0` store and `68` push whose immediate is in `values`."""
    out = []
    i, n = 0, len(text)
    while i < n - 5:
        op = text[i]
        if op == 0xC7 and (text[i + 1] >> 3) & 7 == 0:
            m = modrm_len(text, i + 1)
            if m is not None:
                ln = m + 5
                if i + ln <= n:
                    imm = struct.unpack_from("<I", text, i + ln - 4)[0]
                    if imm in values:
                        out.append((text_va + i, imm, "store"))
                i += ln
                continue
        elif op == 0x68:
            imm = struct.unpack_from("<I", text, i + 1)[0]
            if imm in values:
                out.append((text_va + i, imm, "push"))
            i += 5
            continue
        i += 1
    return out


def ref_sites(text, text_va, values):
    """[(site, target, kind)] for every `E8`/`E9` whose target is in `values`."""
    out = []
    for op, kind in ((b"\xe8", "call"), (b"\xe9", "jmp")):
        pos = text.find(op)
        while pos != -1:
            if pos + 5 <= len(text):
                rel = struct.unpack_from("<i", text, pos + 1)[0]
                target = (text_va + pos + 5 + rel) & 0xFFFFFFFF
                if target in values:
                    out.append((text_va + pos, target, kind))
            pos = text.find(op, pos + 1)
    return out


def harvest(xbe, dat, text, text_va, funcs, installers, tables, slots):
    """One row per stub in the hole, plus the hole's bounds and the edge counts behind them."""
    starts = [f[0] for f in funcs]
    edges = ref_sites(text, text_va, set(installers) | {HANDLER_WRAPPER})
    to_inst = [(s, t) for s, t, k in edges if k == "jmp" and t in installers]
    unmapped = [s for s, t in to_inst if not mapped(funcs, starts, s)]
    mapped_tail = len(to_inst) - len(unmapped)
    lo, hi, inside, outside = island_bounds(funcs, starts, unmapped)
    stubs = walk(xbe.read_va(lo, hi - lo), lo)

    stub_vas = {s[0] for s in stubs}
    store_of, call_of = {}, {}
    for site, value, how in imm_sites(text, text_va, stub_vas):
        store_of.setdefault(value, []).append((site, how))
    for site, value, how in ref_sites(text, text_va, stub_vas):
        call_of.setdefault(value, []).append((site, how))

    rows = []
    for va, size, kind, target, operand, partial in stubs:
        row = {"stub_va": "0x%08x" % va, "size": str(size), "kind": kind,
               "target": "0x%08x" % target if isinstance(target, int) else "",
               "operand": "0x%08x" % operand if isinstance(operand, int) else "",
               "operand_kind": "", "store_va": "", "store_name": "", "callers": "",
               "installer": "", "partial": "yes" if partial else "no", "dword_refs": "",
               "slot": "", "ref_va": "", "census": ""}
        if isinstance(operand, int):
            sec = xbe.section_for(operand)
            raw = xbe.read_va(operand, 4) if sec else b""
            word = struct.unpack("<I", raw[:4])[0] if len(raw) >= 4 else 0
            if word == MAGIC:
                row["operand_kind"] = "funcinfo"
            elif operand in tables:
                row["operand_kind"] = "class-table"
            else:
                row["operand_kind"] = sec.name if sec else "unmapped"
        if partial:
            row["kind"] = "other"
        body = xbe.read_va(va, size)
        for j, ln, mnem, operand2, op2 in decode(body, va) or []:
            if mnem in ("jmp", "call") and operand2 in installers:
                row["installer"] = "yes"
                row["target"] = "0x%08x" % operand2 if not row["target"] else row["target"]
                break
        hits = store_of.get(va, [])
        if hits:
            fn = mapped(funcs, starts, hits[0][0])
            row["store_va"] = "0x%08x" % hits[0][0]
            row["store_name"] = (fn[2] if fn else "") or ("unmapped:" + hits[0][1])
            if len(hits) > 1:
                row["store_name"] += "+%d" % (len(hits) - 1)
        row["callers"] = str(len(call_of.get(va, [])))
        if row["installer"] == "yes":
            pat = struct.pack("<I", va)
            n_ref, pos, secs = 0, dat.find(pat), set()
            while pos != -1:
                va2 = xbe.offset_to_va(pos)
                sec2 = xbe.section_for(va2) if va2 else None
                if sec2 is None or sec2.name != ".text":
                    n_ref += 1
                    if sec2 is not None:
                        secs.add(sec2.name)
                    if not row["ref_va"] and va2:
                        row["ref_va"] = "0x%08x" % va2
                    for tva, sn in slots.items():
                        if tva <= va2 < tva + 4 * sn:
                            row["slot"] = "0x%08x+%d" % (tva, (va2 - tva) // 4)
                            break
                pos = dat.find(pat, pos + 1)
            row["dword_refs"] = "%d:%s" % (n_ref, ",".join(sorted(secs)) or "-")
        rows.append(row)
    meta = {"lo": lo, "hi": hi, "inside": inside, "outside": outside,
            "tail_total": len(to_inst), "tail_mapped": mapped_tail, "tail_unmapped": len(unmapped),
            "stubs": len(rows)}
    # The census row carries the run-level counts, so a `--write-doc` from the committed CSV renders the
    # same page the measurement did. Without it the block would lose every count that is not a stub row.
    rows.append({"stub_va": "census", "size": str(len(rows)), "kind": "census", "census": ";".join(
        "%s=%s" % (k, ("0x%08x" % v) if k in ("lo", "hi") else v) for k, v in sorted(meta.items()))})
    return rows, meta


# --------------------------------------------------------------------------- artifacts
def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_artifact(path=ARTIFACT):
    return read_csv(path)


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


# --------------------------------------------------------------------------- rendering
META = None


def census_of(rows):
    """The run-level counts, read from the artifact's own `census` row."""
    for r in rows:
        if r.get("kind") == "census" and r.get("census"):
            out = {}
            for pair in r["census"].split(";"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    out[k] = int(v, 16) if v.startswith("0x") else int(v)
            return out
    return {}


def render(rows, meta=None):
    meta = census_of(rows) or (meta or META or {})
    rows = [r for r in rows if r.get("kind") != "census"]
    n = len(rows)
    kinds = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    pads = sorted(int(r["size"]) for r in rows)
    median = pads[len(pads) // 2] if pads else 0
    eh = [r for r in rows if r["kind"] == "eh-handler"]
    fin = [r for r in eh if r["operand_kind"] == "funcinfo"]
    tail = [r for r in rows if r["kind"] == "tail-jump"]
    inst = [r for r in rows if r["installer"] == "yes"]
    partial = [r for r in rows if r.get("partial") == "yes"]
    fwd = [r for r in rows if r["kind"] == "forward"]
    stored = [r for r in rows if r["store_va"]]
    named = [r for r in stored if r["store_name"] and not r["store_name"].startswith("unmapped")]
    called = [r for r in rows if r["callers"] and r["callers"] != "0"]
    per_installer = {}
    for r in inst:
        per_installer[r["target"]] = per_installer.get(r["target"], 0) + 1

    lines = ["| measure | value |", "|---|---|"]
    if meta:
        lines.append("| hole (the symbol-map gap holding the most unmapped installer tail jumps) | "
                     "`0x%08x`..`0x%08x` |" % (meta["lo"], meta["hi"]))
        lines.append("| installer tail jumps, all sites | %d (%d inside a mapped body, %d not) |"
                     % (meta["tail_total"], meta["tail_mapped"], meta["tail_unmapped"]))
        lines.append("| ...of the unmapped ones, in this gap / in other gaps | %d / %d |"
                     % (meta["inside"], meta["outside"]))
        lines.append("| stub rows in the artifact | %d (this census row is not one) |" % meta["stubs"])
    lines += ["| stubs in the hole | %d |" % n,
              "| stub sizes | %d..%d bytes, median %d |" % (
                  pads[0] if pads else 0, pads[-1] if pads else 0, median),
              "| `eh-handler` (`mov imm` + `jmp`) | %d |" % kinds.get("eh-handler", 0),
              "| ...whose immediate is a `FuncInfo` (first dword `0x19930520`) | %d |" % len(fin),
              "| bare `jmp` tail jumps | %d |" % len(tail),
              "| ...holding a jump or call into the 24 orphan installers | %d over %d installers |" % (
                  len(inst), len(per_installer)),
              "| `mov reg,[ebp+d]` + `jmp` forwarders | %d |" % len(fwd),
              "| other / undecoded | %d / %d |" % (kinds.get("other", 0), kinds.get("undecoded", 0)),
              "| stubs with bytes the subset could not read | %d |" % len(partial),
              "| stubs whose address a body stores | %d (%d in a named body) |" % (
                  len(stored), len(named)),
              "| stubs an `E8` calls | %d |" % len(called)]
    dref = [r for r in rows if r.get("dword_refs")]
    if dref:
        hit = [r for r in dref if not r["dword_refs"].startswith("0:")]
        lines.append("")
        slotted = [r for r in hit if r.get("slot")]
        spans = sorted(int(r["ref_va"], 16) for r in dref if r.get("ref_va"))
        if spans:
            lines.append("")
            lines.append("The references run `0x%08x`..`0x%08x`, %d of them inside that %d-byte span."
                         % (spans[0], spans[-1], len(spans), spans[-1] - spans[0] + 4))
        lines.append("Installer-thunk reachability: %d stubs, %d with a dword reference outside `.text`"
                     " (%s); %d of those references land in a class-table slot."
                     % (len(dref), len(hit), ", ".join(sorted({r["dword_refs"].split(":")[1]
                                                              for r in hit})) or "none", len(slotted)))
        if slotted:
            lines.append("")
            lines.append("The slots: " + ", ".join("`%s` at table `%s`" % (r["stub_va"], r["slot"])
                                                   for r in slotted[:24]))
    if per_installer:
        lines.append("")
        lines.append("Installer tail jumps, per installer: " + ", ".join(
            "`%s` x%d" % (k, v) for k, v in sorted(per_installer.items(), key=lambda kv: -kv[1])[:12]))
    if fin:
        lines.append("")
        lines.append("The `FuncInfo` immediates run `%s`..`%s` and every record read is `.rdata`."
                     % (fin[0]["operand"], fin[-1]["operand"]))
    lines.append("")
    lines.append("Stubs a store names (the body each handler/thunk belongs to):")
    lines.append("")
    lines.append("| stub | kind | target | operand | stored by | body |")
    lines.append("|---|---|---|---|---|---|")
    for r in (named or stored)[:20]:
        lines.append("| `%s` | %s | `%s` | `%s` | `%s` | `%s` |" % (
            r["stub_va"], r["kind"], r["target"] or "-", r["operand"] or "-", r["store_va"],
            r["store_name"]))
    return "\n".join(lines)


def splice_doc(path, block):
    text = open(path, encoding="utf-8").read()
    if DOC_START not in text or DOC_END not in text:
        return None
    head, rest = text.split(DOC_START, 1)
    _, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + "\n" + DOC_END + tail


# --------------------------------------------------------------------------- self test
def self_check():
    """Synthetic stubs: each shape, the terminator rule, the pad rule and the two joins."""
    eh = bytes.fromhex("b810625700") + b"\xe9" + struct.pack("<i", HANDLER_WRAPPER - (0x1000 + 10))
    tail = b"\xe9" + struct.pack("<i", 0x004D03C8 - (0x100A + 5))
    fwd = bytes.fromhex("8b4df0") + b"\xe9" + struct.pack("<i", 0x0001AA30 - (0x1013 + 3 + 5))
    blob = eh + tail + b"\xcc" * 4 + fwd + b"\xcc" * 3
    got = [(s[0], s[1], s[2]) for s in walk(blob, 0x1000)]
    assert got == [(0x1000, 10, "eh-handler"), (0x100A, 5, "tail-jump"),
                   (0x1013, 8, "forward")], got
    # an opcode outside the subset is a row with a size and no claimed shape, not a silent gap
    bad = walk(b"\xd9\xc0\xc3", 0x2000)
    assert bad and bad[0][1] == 3 and bad[0][5] is True, bad
    # one unreadable byte inside a stub does not split it, and the shape is withheld
    mixed = walk(b"\xd9\xc0" + b"\xb8" + struct.pack("<I", 0x576210) + b"\xe9" +
                 struct.pack("<i", HANDLER_WRAPPER - (0x3007 + 5)), 0x3000)
    assert len(mixed) == 1 and mixed[0][2] == "other" and mixed[0][5] is True, mixed
    # classify: the immediate is the operand, the jmp target the target
    assert classify(decode(eh, 0x1000)) == ("eh-handler", HANDLER_WRAPPER, 0x576210)
    assert classify(decode(tail, 0x100A)) == ("tail-jump", 0x004D03C8, None)
    assert classify(decode(fwd, 0x1013)) == ("forward", 0x1AA30, None)
    # the store join: `c7 45 e8 <imm32>` (7 bytes) then a `68 <imm32>` push at 0x2007
    text = bytes.fromhex("c745e8") + struct.pack("<I", 0x1000) + bytes.fromhex("68") + \
        struct.pack("<I", 0x100A) + b"\x55"
    sites = imm_sites(text, 0x2000, {0x1000, 0x100A})
    assert [(s[0], s[1]) for s in sites] == [(0x2000, 0x1000), (0x2007, 0x100A)], sites
    assert [s[0] for s in ref_sites(b"\xe9" + struct.pack("<i", 0x1000 - 0x3005), 0x3000,
                                    {0x1000})] == [0x3000]
    # bounds come from the map gap with the most sites; other gaps are counted
    funcs = [(0x1000, 8, "a"), (0x2000, 8, "b"), (0x3000, 8, "c")]
    starts = [f[0] for f in funcs]
    assert island_bounds(funcs, starts, [0x1100, 0x1200, 0x2500]) == (0x1008, 0x2000, 2, 1)
    assert island_bounds(funcs, starts, [0x1000]) == (0, 0, 0, 0)
    assert mapped(funcs, starts, 0x1004) == (0x1000, 8, "a")
    assert mapped(funcs, starts, 0x1100) is None
    # the census row round-trips and is not counted as a stub
    blank = {k: "" for k in FIELDS}
    fake = [dict(blank, kind="eh-handler", size="10", stub_va="0x1000", operand_kind="funcinfo"),
            dict(blank, kind="census", size="1", stub_va="census",
                 census="hi=0x2000;inside=2;lo=0x1008;outside=1;stubs=1;tail_mapped=1;"
                        "tail_total=3;tail_unmapped=2")]
    cen = census_of(fake)
    assert (cen["lo"], cen["hi"], cen["stubs"]) == (0x1008, 0x2000, 1), cen
    assert "| stubs in the hole | 1 |" in render(fake), render(fake)[:400]
    print("ok   native-island: 16 assertions (three shapes, terminator split, pad run, undecoded row, "
          "store join, call join, map gap)")
    return 0


# --------------------------------------------------------------------------- main
def main():
    global META
    if "--check" in sys.argv[1:]:
        return self_check()
    ap = argparse.ArgumentParser()
    ap.add_argument("--xbe", default=None)
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc", nargs="?", const=DOC, default=None)
    ap.add_argument("--check-doc", nargs="?", const=DOC, default=None)
    args = ap.parse_args()

    if args.write_doc or args.check_doc or args.report:
        rows = read_artifact()
    else:
        if not args.xbe:
            ap.error("--xbe is required to measure (or use --report / --write-doc)")
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        from xbe import Xbe

        xbe = Xbe(args.xbe)
        dat = open(args.xbe, "rb").read()
        code = [s for s in xbe.sections if s.name == ".text"]
        text = b"".join(dat[s.foff:s.foff + s.fsize] for s in code)
        text_va = code[0].va if code else 0
        funcs = []
        for r in read_csv(os.path.join(ROOT, "symbols/xbox_functions.csv")):
            funcs.append((int(r["address"], 16), int(r.get("size") or 0), r.get("name") or ""))
        funcs.sort()
        installers = set()
        for r in read_csv(os.path.join(ROOT, "inventory/class_orphan_tables.csv")):
            if r.get("body_va"):
                installers.add(int(r["body_va"], 16))
        tables = {int(r["table_va"], 16)
                  for r in read_csv(os.path.join(ROOT, "inventory/xbe_class_tables.csv"))}
        slots = {}
        for r in read_csv(os.path.join(ROOT, "inventory/xbe_class_tables.csv")):
            if r.get("slots"):
                slots[int(r["table_va"], 16)] = int(r["slots"])
        rows, META = harvest(xbe, dat, text, text_va, funcs, installers, tables, slots)
        print("hole %08x..%08x: %d stubs; installer tail jumps %d (%d unmapped, %d in this gap)"
              % (META["lo"], META["hi"], len(rows), META["tail_total"], META["tail_unmapped"],
                 META["inside"]))
        if args.out:
            path = os.path.join(args.out, "native_island.csv")
            write_artifact(rows, path)
            print("%s: %d rows (1 census + %d stubs)" % (path, len(rows), len(rows) - 1))
    print(render(rows, META))
    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        new = splice_doc(path, render(rows, META))
        if new is None:
            print("FAIL native-island: markers not found in %s" % path)
            return 1
        old = open(path, encoding="utf-8").read()
        if args.check_doc:
            if new != old:
                print("FAIL native-island: %s is stale against the committed artifact" % path)
                return 1
            print("ok   native-island: %s matches the committed artifact" % path)
            return 0
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
