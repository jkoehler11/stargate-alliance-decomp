#!/usr/bin/env python3
"""Measure a native function's arity from the code: frame slots, epilogues, call sites.

The save path's writer (`0x00138680`) and its serializer (`0x0006f4b0`) are the two bodies a
rebuild has to call. A rebuilt caller needs each callee's **arity** and its **calling
convention** (who removes the arguments), and neither is in the package data: it is in the
machine code, so this tool answers both without a boot and without Ghidra.

Three independent readings, then the one number they agree on:

* **Frame slots** -- a function with a `push ebp; mov ebp, esp` frame reads its stack
  arguments at `[ebp+8]`, `[ebp+0xc]`, ... so the highest positive `ebp` displacement names
  the last argument. This is the *callee's own* statement of how many arguments it has.
* **Epilogues** -- `ret` (`c3`) removes nothing (caller cleans, `cdecl`); `ret imm16`
  (`c2 nnnn`) removes `n` bytes (`stdcall` / `__thiscall`). A byte scan for `c2` is not enough:
  it also matches the `c2` inside any instruction, and it misses a plain `ret` entirely, which
  is exactly how `0x0006f4b0`'s arity looked unsettled for a session.
* **Call sites** -- for each `E8 rel32` whose target is the function, the straight-line
  instructions before it give the number of dwords pushed, and the instructions after it give
  the cleanup the *caller* performs. pushes - caller cleanup = the bytes the callee must pop.

The decoder is a 32-bit x86 length decoder, not a disassembler: it only needs instruction
boundaries and the stack effects (`push`/`pop`/`sub esp`/`add esp`/`ret`), so it runs in a
second per function and needs no binutils. `--check` is a synthetic self-test (no dump).

Usage:
    argc-pass.py --va 0x0006f4b0 [--dump DIR] [--window 0x60] [--json]
    argc-pass.py --check                  # synthetic self-test, no dump, writes nothing
"""
import argparse
import bisect
import csv
import json
import os
import struct
import sys

# ---------------------------------------------------------------- x86 length decoder

PREFIXES = {0x26, 0x2e, 0x36, 0x3e, 0x64, 0x65, 0x66, 0x67, 0xf0, 0xf2, 0xf3}
# opcodes that take no operand
NO_OPERAND = set(range(0x90, 0x98)) | {0x98, 0x99, 0x9b, 0x9c, 0x9d, 0x9e, 0x9f,
                                     0xa4, 0xa5, 0xa6, 0xa7, 0xaa, 0xab, 0xac, 0xad,
                                     0xae, 0xaf, 0xcb, 0xcc, 0xce, 0xcf, 0xd7,
                                     0xec, 0xed, 0xee, 0xef, 0xf1, 0xf4, 0xf5,
                                     0xf8, 0xf9, 0xfa, 0xfb, 0xfc, 0xfd}
# arithmetic block 0x00-0x3d: (op & 7) selects the operand form
ARITH = set(range(0x00, 0x40))
# two-byte 0x0f opcodes that take a modrm but no immediate
TWOBYTE_MODRM = (set(range(0x10, 0x18)) | set(range(0x28, 0x30)) | set(range(0x40, 0x50)) |
                 set(range(0x50, 0x80)) | set(range(0x90, 0xa0)) | {0xa3, 0xa5, 0xab, 0xad,
                 0xae, 0xaf, 0xb0, 0xb1, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6, 0xb7, 0xb8, 0xb9,
                 0xba, 0xbb, 0xbc, 0xbd, 0xbe, 0xbf, 0xc0, 0xc1, 0xc3, 0xc7,
                 0xd0, 0xd1, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda, 0xdb,
                 0xdc, 0xdd, 0xde, 0xdf, 0xe0, 0xe1, 0xe2, 0xe3, 0xe4, 0xe5, 0xe6, 0xe7,
                 0xe8, 0xe9, 0xea, 0xeb, 0xec, 0xed, 0xee, 0xef, 0xf0, 0xf1, 0xf2, 0xf3,
                 0xf4, 0xf5, 0xf6, 0xf7, 0xf8, 0xf9, 0xfa, 0xfb, 0xfc, 0xfd, 0xfe, 0xff})
# two-byte opcodes whose immediate follows the modrm
TWOBYTE_IMM8 = {0x70, 0xc2, 0xc4, 0xc5, 0xc6, 0xa4, 0xac, 0xba}


class Insn(object):
    __slots__ = ("size", "kind", "imm", "slot")

    def __init__(self, size, kind="other", imm=None, slot=None):
        self.size, self.kind, self.imm, self.slot = size, kind, imm, slot

    def __repr__(self):
        return "Insn(%d,%s,%r,%r)" % (self.size, self.kind, self.imm, self.slot)


def _signed(b, i, n):
    v = int.from_bytes(b[i:i + n], "little")
    return v - (1 << (8 * n)) if v & (1 << (8 * n - 1)) else v


def _modrm(b, i, osz):
    """Decode one modrm (+sib, +displacement). Returns (length, ebp_disp or None)."""
    if i >= len(b):
        raise IndexError("modrm past end")
    m = b[i]
    mod, reg, rm = m >> 6, (m >> 3) & 7, m & 7
    n, slot = 1, None
    if mod == 3:
        return n, slot
    if rm == 4:                                   # SIB
        if i + 1 >= len(b):
            raise IndexError("sib past end")
        sib = b[i + 1]
        n += 1
        base = sib & 7
        if mod == 0 and base == 5:
            n += 4
        elif base == 5 and mod != 0:
            slot = _signed(b, i + n, 1 if mod == 1 else 4) + 0x10000  # ebp via sib base
            n += 1 if mod == 1 else 4
        if mod == 1:
            n += 1
        elif mod == 2:
            n += 4
        return n, None
    if mod == 0:
        if rm == 5:
            n += 4                                # disp32, absolute
        return n, None
    if mod == 1:
        d = _signed(b, i + 1, 1)
        n += 1
    else:
        d = _signed(b, i + 1, 4)
        n += 4
    if rm == 5:                                   # ebp-relative
        slot = d
    return n, slot


def decode(b, i):
    """Return an Insn for the instruction at b[i], or None when the bytes are not decodable."""
    start, n = i, len(b)
    p66 = p67 = False
    while i < n and b[i] in PREFIXES:
        if b[i] == 0x66:
            p66 = True
        elif b[i] == 0x67:
            p67 = True
        i += 1
    if i >= n:
        return None
    osz = 2 if p66 else 4
    op = b[i]
    i += 1
    if op == 0x0f:
        if i >= n:
            return None
        op2 = b[i]
        i += 1
        if op2 == 0x05:
            return Insn(i - start, "syscall")
        if op2 in (0x0b, 0x31, 0x32, 0x33, 0x34, 0x35, 0x77, 0xa2, 0xaa, 0x0e):
            return Insn(i - start, "other")
        if op2 in (0xa0, 0xa1, 0xa8, 0xa9):
            return Insn(i - start, "other")
        if 0x80 <= op2 <= 0x8f:
            return Insn(i - start + 4, "other")          # jcc rel32
        if op2 in TWOBYTE_IMM8:
            ln, slot = _modrm(b, i, osz)
            return Insn(i - start + ln + 1, "other", slot=slot)
        if op2 in TWOBYTE_MODRM:
            ln, slot = _modrm(b, i, osz)
            return Insn(i - start + ln, "other", slot=slot)
        return None
    if op in (0xc2,):                                        # ret imm16
        imm = int.from_bytes(b[i:i + 2], "little")
        return Insn(i - start + 2, "retn", imm)
    if op in (0xc3,):
        return Insn(i - start, "ret")
    if op == 0xca:
        return Insn(i - start + 2, "retf")
    if op == 0xc9:
        return Insn(i - start, "leave")
    if op == 0xc8:                                           # enter imm16, imm8
        return Insn(i - start + 3, "other")
    if op == 0xe8:
        return Insn(i - start + 4, "call", _signed(b, i, 4))
    if op == 0xe9:
        return Insn(i - start + 4, "jmp", _signed(b, i, 4))
    if op == 0xeb:
        return Insn(i - start + 1, "jmp", _signed(b, i, 1))
    if 0x70 <= op <= 0x7f:
        return Insn(i - start + 1, "jcc", _signed(b, i, 1))
    if 0xe0 <= op <= 0xe3:
        return Insn(i - start + 1, "jcc", _signed(b, i, 1))
    if 0x50 <= op <= 0x57:
        return Insn(i - start, "push", 4)
    if 0x58 <= op <= 0x5f:
        return Insn(i - start, "pop", 4)
    if op == 0x68:
        return Insn(i - start + 4, "push", 4)
    if op == 0x6a:
        return Insn(i - start + 1, "push", 4)
    if op == 0x9a:
        return Insn(i - start + 6, "callfar")
    if op == 0xea:
        return Insn(i - start + 6, "jmpfar")
    if op in (0xa0, 0xa1, 0xa2, 0xa3):
        return Insn(i - start + (2 if p67 else 4), "other")
    if op == 0xa8:
        return Insn(i - start + 1, "other")
    if op == 0xa9:
        return Insn(i - start + osz, "other")
    if 0xb0 <= op <= 0xb7:
        return Insn(i - start + 1, "other")
    if 0xb8 <= op <= 0xbf:
        return Insn(i - start + osz, "other")
    if op in (0x04, 0x0c, 0x14, 0x1c, 0x24, 0x2c, 0x34, 0x3c, 0x6a):
        return Insn(i - start + 1, "other")
    if op in (0x05, 0x0d, 0x15, 0x1d, 0x25, 0x2d, 0x35, 0x3d, 0x68):
        return Insn(i - start + osz, "other")
    if op == 0x69:                                           # imul r, r/m, imm32
        ln, slot = _modrm(b, i, osz)
        return Insn(i - start + ln + osz, "other", slot=slot)
    if op == 0x6b:
        ln, slot = _modrm(b, i, osz)
        return Insn(i - start + ln + 1, "other", slot=slot)
    if op in (0x80, 0x82, 0x83, 0xc0, 0xc1, 0xc6, 0xcd, 0xe4, 0xe5, 0xe6, 0xe7,
              0xd4, 0xd5):
        if op in (0x80, 0x82, 0x83, 0xc0, 0xc1, 0xc6):
            ln, slot = _modrm(b, i, osz)
            imm = i + ln
            if op in (0x81,):
                pass
            return Insn(imm - start + 1, "other", slot=slot)
        return Insn(i - start + 1, "other")
    if op in (0x81, 0xc7):
        ln, slot = _modrm(b, i, osz)
        return Insn(i - start + ln + osz, "other", slot=slot)
    if op in (0x8f,):                                        # pop r/m
        ln, slot = _modrm(b, i, osz)
        return Insn(i - start + ln, "pop", 4)
    if op in (0xfe, 0xff):
        ln, slot = _modrm(b, i, osz)
        reg = (b[i] >> 3) & 7
        kind = "callind" if (op == 0xff and reg in (2, 3)) else "other"
        return Insn(i - start + ln, kind, slot=slot)
    if op in (0xf6, 0xf7):
        ln, slot = _modrm(b, i, osz)
        reg = (b[i] >> 3) & 7
        extra = (1 if op == 0xf6 else osz) if reg in (0, 1) else 0
        return Insn(i - start + ln + extra, "other", slot=slot)
    if op in ARITH or op in (0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8a, 0x8b, 0x8c,
                             0x8d, 0x8e, 0xd0, 0xd1, 0xd2, 0xd3, 0x28, 0x29, 0x2a, 0x2b):
        ln, slot = _modrm(b, i, osz)
        return Insn(i - start + ln, "other", slot=slot)
    if 0xd8 <= op <= 0xdf:
        ln, slot = _modrm(b, i, osz)
        return Insn(i - start + ln, "other", slot=slot)
    if op in NO_OPERAND:
        return Insn(i - start, "other")
    return None


def sweep(b, limit=None, stop_at_pad=True):
    """Linear sweep with resync: [(offset, Insn)].

    A real body desyncs a strict linear sweep: one jump table or a `db`-emitted byte makes every
    later instruction boundary wrong, and the tail then reports "no epilogue" (which is what made
    `0x0006f4b0`'s arity look unsettled). So an undecodable byte advances one byte instead of
    ending the sweep, and the sweep stops at the `ret` that is followed by `int3` padding -- the
    real end of a function, not the end of the buffer it was read into.
    """
    out, i, skips = [], 0, 0
    while i < len(b) and (limit is None or len(out) < limit):
        try:
            ins = decode(b, i)
        except IndexError:
            ins = None
        if ins is None or ins.size <= 0:
            skips += 1
            i += 1
            continue
        out.append((i, ins))
        i += ins.size
        if stop_at_pad and ins.kind in ("ret", "retn") and b[i:i + 2] == b"\xcc\xcc":
            break
    return out


# ---------------------------------------------------------------- the three readings


def frame_slots(b):
    """Stack-argument slots the body reads or writes: {disp: [insn offsets]} for disp >= 8."""
    slots = {}
    for off, ins in sweep(b):
        if ins.slot is not None and ins.slot >= 8:
            slots.setdefault(ins.slot, []).append(off)
    return slots


def epilogue_signatures(b):
    """`mov esp,ebp; pop ebp; ret[ imm16]` found by pattern: [(offset, kind, imm)].

    This is the one reading that survives a desynced sweep, so it is checked separately from the
    decoded one and the two are reported together: MSVC's frame epilogue is always this four- or
    six-byte shape.
    """
    out, i = [], 0
    while True:
        i = b.find(b"\x8b\xe5\x5d", i)
        if i < 0:
            return out
        j = i + 3
        if j < len(b) and b[j] == 0xc3:
            out.append((j, "ret", None))            # address of the ret, not of the mov
            i = j + 1
        elif j + 3 <= len(b) and b[j] == 0xc2:
            out.append((j, "retn", int.from_bytes(b[j + 1:j + 3], "little")))
            i = j + 3
        else:
            i = j


def epilogues(b):
    """Union of the decoded and signature epilogues: [(offset, kind, imm)]."""
    seen, out = set(), []
    for off, kind, imm in epilogue_signatures(b) + [
            (off, ins.kind, ins.imm) for off, ins in sweep(b) if ins.kind in ("ret", "retn")]:
        if off not in seen:
            seen.add(off)
            out.append((off, kind, imm))
    return sorted(out)


def arg_run(slots):
    """The contiguous run of argument slots from +0x08: (argc, [dwords]).

    A frame reads `+0x08`, `+0x0c`, ... for its arguments in order, so the contiguous run from
    +0x08 is the argument list. A *gap* means a higher slot is not an argument: `0x00138680`
    reads `[ebp+0x30]` and `[ebp+0x11c]` because it re-establishes ebp over a large local frame,
    and taking the maximum would call it a 70-argument function.
    """
    run = []
    d = 8
    while d in slots:
        run.append(d)
        d += 4
    return len(run), run


def stack_delta(ins):
    """Bytes this instruction adds to esp (positive = esp grows)."""
    if ins.kind == "push":
        return -ins.imm
    if ins.kind == "pop":
        return 4
    return 0


def back_path(b, call_off, window):
    """Decode a straight-line run that ends exactly at `call_off`.

    Tries every start in [call_off-window, call_off-1] and keeps the decode that lands on the
    call with the most instructions (MSVC pushes a call's arguments in one run, so the longest
    landing path is the argument block). Instruction bytes are what makes this honest: summing
    raw `6a`/`68` bytes would count opcodes inside displacements as pushes.
    """
    best = None
    for start in range(max(0, call_off - window), call_off):
        off, path, ok = start, [], True
        while off < call_off:
            try:
                ins = decode(b, off)
            except IndexError:
                ok = False
                break
            if ins is None or ins.size <= 0:
                ok = False
                break
            if ins.kind in ("ret", "retn", "retf", "jmp", "jmpfar", "call", "callind"):
                ok = False      # a call ends the run: pushes before it belong to that call
                break
            path.append((off, ins))
            off += ins.size
        if ok and off == call_off and path:
            if best is None or len(path) > len(best):
                best = path
    return best or []


def cleanup_after(b, after_off, limit=12):
    """What the caller removes right after the call: (bytes, [(off, what)]).

    Only a contiguous run of `add esp, imm` and `pop` counts -- that is the shape MSVC emits for
    a call's arguments, and stopping at the first other instruction keeps an unrelated `sub esp`
    later in the block out of the number.
    """
    total, seen, off, stray = 0, [], after_off, 0
    while off < len(b) and len(seen) + stray < limit:
        try:
            ins = decode(b, off)
        except IndexError:
            break
        if ins is None:
            break
        if ins.kind == "other" and ins.size == 3 and b[off] == 0x83 and (b[off + 1] & 0xc7) == 0xc4:
            total += b[off + 2]                                    # add esp, imm8
            seen.append((off, "add esp,0x%x" % b[off + 2]))
        elif ins.kind == "other" and ins.size == 6 and b[off] == 0x81 and (b[off + 1] & 0xc7) == 0xc4:
            total += int.from_bytes(b[off + 2:off + 6], "little")   # add esp, imm32
            seen.append((off, "add esp,0x%x" % int.from_bytes(b[off + 2:off + 6], "little")))
        elif ins.kind == "pop":
            total += 4
            seen.append((off, "pop"))
        else:
            if seen or ins.kind not in ("other",):
                break          # cleanup already taken: the next instruction ends it
            stray += 1         # MSVC may read the return value before the esp adjust
            if ins.kind == "call" or ins.kind == "jcc":
                break
        off += ins.size
    return total, seen


def load_symbols(path):
    """[(address, size, name)] from symbols/xbox_functions.csv, sorted by address."""
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                rows.append((int(r["address"], 16), int(r.get("size") or 0, 0), r.get("name") or ""))
            except (KeyError, ValueError):
                continue
    rows.sort()
    return rows


def owner(rows, va):
    """The symbol row containing `va`, or None."""
    if not rows:
        return None
    i = bisect.bisect_right(rows, (va, float("inf"), "")) - 1
    if i >= 0 and rows[i][0] <= va < rows[i][0] + max(rows[i][1], 1):
        return rows[i]
    return None


def anchored_block(text, call_off, body_off, body_len):
    """Walk the decoded boundaries of the containing function back/forward from a call.

    The argument block is the run of instructions since the last control transfer, and it has to
    be read on the *caller's own* instruction boundaries: a backward search for a landing decode
    finds plausible chains that start inside an instruction (`01 ff` decodes as `add edi,edi`),
    which is how a 7-push call first read as 13 pushes. Returns (path, pushed, cleaned) or None
    when the sweep does not contain the call.
    """
    body = text[body_off:body_off + body_len]
    insns = sweep(body)
    index = {off: (i, ins) for i, (off, ins) in enumerate(insns)}
    want = call_off - body_off
    if want not in index:
        return None
    i, _ = index[want]
    path, pushed = [], 0
    j = i - 1
    while j >= 0:
        off, ins = insns[j]
        if off + ins.size != (insns[j + 1][0] if j + 1 < len(insns) else want):
            break                                   # a resync gap: the run is not contiguous
        if ins.kind in ("call", "callind", "jmp", "jmpfar", "jcc", "ret", "retn", "retf"):
            break                                   # the run starts after the last transfer
        path.insert(0, (off + body_off, ins))
        pushed += -stack_delta(ins)
        j -= 1
    cleaned, _ = cleanup_after(body, want + 5)
    return path, pushed, cleaned


def call_sites(text, text_va, target, window=0x60, symbols=None):
    """Direct `E8 rel32` call sites to `target`: [(site_va, pushed, cleaned, path, how)]."""
    hits = []
    pat = b"\xe8"
    i = text.find(pat)
    while i != -1:
        if i + 5 <= len(text):
            rel = struct.unpack_from("<i", text, i + 1)[0]
            if text_va + i + 5 + rel == target:
                site = text_va + i
                got = None
                row = owner(symbols or [], site)
                if row:
                    start, size, _ = row
                    got = anchored_block(text, i, start - text_va, max(size, 0x20))
                if got:
                    path, pushed, cleaned = got
                    hits.append((site, pushed, cleaned, path, "anchored in %s" % row[2]))
                else:
                    path = back_path(text, i, window)
                    pushed = -sum(stack_delta(ins) for _, ins in path)
                    cleaned, _ = cleanup_after(text, i + 5)
                    hits.append((site, pushed, cleaned, path, "back-scan (no symbol row)"))
        i = text.find(pat, i + 1)
    return hits


MNEMONIC = {0x89: "mov", 0x8b: "mov", 0x8c: "mov", 0x8e: "mov", 0xc6: "mov", 0xc7: "mov",
            0x8d: "lea", 0x33: "xor", 0x2b: "sub", 0x03: "add", 0x39: "cmp", 0x3b: "cmp",
            0x85: "test", 0x6a: "push", 0x68: "push", 0xa1: "mov", 0xa3: "mov", 0x50: "push"}


def describe(path, b=None):
    """Short names for the instructions of a path, so the table reads like the listing."""
    out = []
    for off, ins in path:
        if ins.kind in ("push", "pop", "call"):
            out.append(ins.kind)
            continue
        op = b[off] if b is not None else None
        if op == 0x83 and off + 1 < len(b) and (b[off + 1] & 0xc7) == 0xc4:
            out.append("add esp,0x%x" % b[off + 2])
        elif op == 0x81 and off + 1 < len(b) and (b[off + 1] & 0xc7) == 0xc4:
            out.append("add esp,0x%x" % int.from_bytes(b[off + 2:off + 6], "little"))
        else:
            out.append(MNEMONIC.get(op, "op%02x" % op if op is not None else "other"))
    return out


# ---------------------------------------------------------------- dump access


def load_image(dump):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    from xbe import Xbe
    path = os.path.join(dump, "default.xbe") if os.path.isdir(dump) else dump
    return Xbe(path)


def section_of(xbe, va):
    for s in xbe.sections:
        if s.contains(va):
            return s
    raise SystemExit("0x%08x is not in any section" % va)


def read_body(xbe, va, size):
    return xbe.read_va(va, size)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--va", help="function virtual address (hex)")
    ap.add_argument("--dump", default=os.environ.get("SG1_DUMP", os.path.expanduser("~/sg1/dump")),
                    help="dump game directory or default.xbe (default: $SG1_DUMP)")
    ap.add_argument("--size", type=lambda s: int(s, 0), default=0x1800,
                    help="bytes of the body to sweep (default 0x1800)")
    ap.add_argument("--window", type=lambda s: int(s, 0), default=0x60,
                    help="bytes to look back from each call site (default 0x60)")
    ap.add_argument("--no-callers", action="store_true", help="skip the call-site scan")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump")
    args = ap.parse_args(argv)
    if args.check:
        return self_test()
    if not args.va:
        ap.error("--va is required (or --check)")

    target = int(args.va, 16)
    xbe = load_image(args.dump)
    body = read_body(xbe, target, args.size)
    slots = frame_slots(body)
    eps = epilogues(body)
    arg_max = max(slots) if slots else None
    argc = (arg_max - 8) // 4 + 1 if arg_max else None
    cleans = sorted({imm for _, k, imm in eps if k == "retn"})
    plain = [off for off, k, _ in eps if k == "ret"]

    hits = []
    text = None
    if not args.no_callers:
        s = section_of(xbe, target)
        text = xbe.read_va(s.va, s.vsize)
        sym_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                "symbols", "xbox_functions.csv")
        symbols = load_symbols(sym_path) if os.path.exists(sym_path) else []
        hits = call_sites(text, s.va, target, args.window, symbols)

    if args.json:
        print(json.dumps({
            "va": "0x%08x" % target,
            "argc_from_frame": argc,
            "frame_slots": {"+0x%02x" % k: len(v) for k, v in sorted(slots.items())},
            "epilogues": [{"va": "0x%08x" % (target + o), "kind": k, "imm": imm}
                          for o, k, imm in eps],
            "ret_imm": cleans,
            "plain_ret_sites": ["0x%08x" % (target + o) for o in plain],
            "call_sites": [{"va": "0x%08x" % va, "pushed": p, "caller_cleanup": c,
                            "args": p // 4, "callee_cleans": p - c, "how": how}
                           for va, p, c, _, how in hits],
        }, indent=2))
        return 0

    print("function 0x%08x  (swept 0x%x bytes)" % (target, len(body)))
    if slots:
        n, run = arg_run(slots)
        far = sorted(k for k in slots if k >= 0x24)
        print("frame slots read: %s" % ", ".join("+0x%02x(%d)" % (k, len(v))
                                                 for k, v in sorted(slots.items())))
        if n:
            print("arity from the frame: %d stack argument(s) (+0x08..+0x%02x)"
                  % (n, run[-1]))
        else:
            print("arity from the frame: none (no contiguous run from +0x08)")
        if far:
            print("  beyond the argument window: %s -- ebp re-based over a local frame, not"
                  " arguments" % ", ".join("+0x%02x" % k for k in far))
    else:
        print("frame slots read: none with a positive ebp displacement")
    print("epilogues: %d" % len(eps))
    for o, k, imm in eps:
        print("  0x%08x  %s%s" % (target + o, k, "" if imm is None else " 0x%x" % imm))
    argc_ret = None
    if plain and cleans:
        print("MIXED: both plain ret and ret imm16 -- the ret imm16 belongs to a neighbour")
    elif plain:
        print("convention: cdecl (plain ret; the caller removes every argument)")
    elif cleans:
        argc_ret = cleans[0] // 4
        print("convention: callee cleans %d byte(s) = %d argument(s)"
              % (cleans[0], argc_ret))
    n_frame = arg_run(slots)[0] if slots else 0
    if argc_ret is not None and n_frame and argc_ret != n_frame:
        print("DISAGREEMENT: the frame reads %d slot(s), the epilogue pops %d -- read both"
              % (n_frame, argc_ret))
    elif argc_ret is not None:
        print("arity: %d" % argc_ret)
    elif n_frame:
        print("arity: %d" % n_frame)
    for va, pushed, clean, path, how in hits:
        print("call site 0x%08x (%s): %d dword(s) pushed, caller cleans %d -> callee cleans %d"
              % (va, how, pushed // 4, clean, (pushed - clean)))
        print("   path: %s" % " ".join(describe(path, text)))
        if plain and pushed and clean and clean < pushed:
            print("   note: the callee is cdecl, so %d of the %d pushed byte(s) are still on the"
                  " stack after the call -- stack scratch, not argument cleanup" % (pushed - clean, pushed))
    if not hits and not args.no_callers:
        print("call sites: none (indirect dispatch, or a virtual slot -- try vtable-reach.py)")
    return 0


# ---------------------------------------------------------------- self-test


SYNTH_CALLEE = bytes.fromhex(
    "55 8b ec 6a ff 68 85 6c 3f 00 81 ec b4 02 00 00 "   # push ebp; mov ebp,esp; ...; sub esp,0x2b4
    "8b 45 08 85 c0 8b 4d 0c 89 4d 10 "                   # reads +0x08, +0x0c, writes +0x10
    "8b 45 18 85 c0 74 02 33 c0 "                         # reads +0x18
    "8b 4d f4 8b e5 5d c3 "                               # epilogue: plain ret
    "8b 45 0c 8b e5 5d c2 0c 00"                          # second epilogue: ret 0xc
)
SYNTH_CALLER = bytes.fromhex(
    "55 8b ec 83 ec 40 "                                  # push ebp; mov ebp,esp; sub esp,0x40
    "57 6a 01 8d 44 24 10 50 e8 00 00 00 00 83 c4 10"     # push edi; push 1; lea; push; call; add esp,0x10
    "c9 c3"
)


def self_test():
    ok = True

    def chk(name, got, want):
        nonlocal ok
        if got != want:
            ok = False
            print("FAIL %s: got %r want %r" % (name, got, want))

    chk("decode push ebp", decode(SYNTH_CALLEE, 0).size, 1)
    chk("decode mov ebp,esp", decode(SYNTH_CALLEE, 1).size, 2)
    chk("decode sub esp,0x2b4", decode(SYNTH_CALLEE, 10).size, 6)

    slots = frame_slots(SYNTH_CALLEE)
    chk("frame slots present", sorted(k for k in slots if k < 0x100), [0x08, 0x0c, 0x10, 0x18])
    chk("arity from frame", max(k for k in slots if k < 0x100), 0x18)

    eps = epilogues(SYNTH_CALLEE)
    chk("epilogue kinds", [k for _, k, _ in eps], ["ret", "retn"])
    chk("ret imm", [imm for _, k, imm in eps if k == "retn"], [0x0c])

    # the synthetic caller's call is at offset 14, so its rel32 0 targets 0x1000+19
    sites = call_sites(SYNTH_CALLER, 0x1000, 0x1000 + 19, window=0x20)
    chk("one call site", len(sites), 1)
    if sites:
        va, pushed, clean, path, how = sites[0]
        chk("pushed bytes", pushed, 16)          # push edi, push 1, push edx(from lea), push eax
        chk("caller cleanup", clean, 16)
        chk("callee cleans", pushed - clean, 0)  # plain ret -> cdecl

    # a byte scan for `c2` must not be the instrument: the immediate 0xc2 inside `b4 02 00 00`
    # is not an opcode, and the synthetic callee's first epilogue is a plain `c3`.
    chk("c2 bytes present but not a ret", SYNTH_CALLEE.count(b"\xc2"), 1)
    chk("first epilogue is plain ret", eps[0][1], "ret")

    if ok:
        print("argc-pass --check: ok (%d decoder cases, %d epilogues, %d call site)"
              % (4, len(eps), len(sites)))
        return 0
    print("argc-pass --check: FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
