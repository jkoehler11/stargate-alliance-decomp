#!/usr/bin/env python3
"""What loads the receiver at each `.text` slot-19/20 dispatch site (U8).

`inventory/vcall_dispatch_sites.csv` counted the sites where the image dispatches through
`+0x4c`/`+0x50` -- the displacements slots 19 and 20 of a 492-entry class table sit at
(`19*4`, `20*4`) -- and named the 14 that sit inside a named function. It could not say *what the
receiver is*, because a displacement is not a type. This reads the instruction that ends exactly at
each site and writes the register the call dispatches through, then classifies where that value came
from:

    lea edx,[ebp-0x40c]      a stack local             -> the table is a local C++ object
    mov edx,[esi]            the object's first dword  -> its vtable pointer
    mov ecx,[esi+0x4c]       a field of an object
    mov edx,[0x7bf51c]       an absolute global
    mov eax,imm32 / xor      not an object at all
    (nothing decodes)        unread -- never counted as "no receiver"

The backward read is exact-length, the way `gnatives-writes.py` reads a store's value: the
instruction that *ends* at the site is the one executed last, so a candidate is accepted only when
its decoded length lands on the site. A row also carries how many candidates landed and whether a
second link (an instruction that also ends at the candidate's own start) confirms the alignment. A
window that decodes to nothing is `unread` and keeps its bytes, so a silent decoder never reads as
"the receiver is not an object".

    python3 tools/vcall-receiver.py --xbe DUMP/default.xbe -o inventory/vcall_receiver_sites.csv
    python3 tools/vcall-receiver.py --check       # synthetic bytes, no dump, writes nothing

**A second, independent decoder decides the boundary.** The narrow decoder below matches the byte pattern
with its own instruction lengths, and an instruction that *ends* at the site is not proof: at `0x2d113c`
the bytes `ff 51 50` are the tail of a `lea ecx,[ebp-0x15c]` displacement, and a wrong-length decode of
that `lea` (`add [ebp-0x15b73],ecx`) lands on the site with a chain of wrong-length decodes behind it, so
the three-link test passes. `objdump` (`-D -b binary -m i386`, no dump needed) decodes the same bytes from
the function's own start and says whether an instruction starts at the site, spans it, or its own sweep
desynced before it. The verdict is recorded per row (`oracle`), the oracle also supplies the sweep for the
receivers the narrow decoder cannot reach (`oracle_sweep`), and `--no-oracle` reproduces the old reading.
The decoder is deliberately narrow -- the 32-bit integer mov/lea/pop/immediate/arith forms and the call
forms -- rather than a disassembler: an opcode it does not know makes a row `unread` instead of guessing,
and it is the oracle, not this file, that guarantees a boundary.

Addresses and counts only: nothing here carries game data.
"""
import argparse
import csv
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

SITES = os.path.join(ROOT, "inventory", "vcall_dispatch_sites.csv")
TABLES = os.path.join(ROOT, "inventory", "class_table_slot_pairs.csv")
OUT = os.path.join(ROOT, "inventory", "vcall_receiver_sites.csv")
HEADER = ["site_va", "slot", "reg", "function", "defining_va", "bytes", "form", "source",
          "base", "disp", "value", "value_kind", "candidates", "walk", "aligned", "oracle",
          "oracle_form"]

WINDOW = 24
REGS = ["eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi"]
PREFIXES = {0x66, 0x67, 0xF0, 0xF2, 0xF3, 0x2E, 0x36, 0x3E, 0x26, 0x64, 0x65}
ARITH_NAMES = {0: "add", 8: "or", 0x10: "adc", 0x18: "sbb",
               0x20: "and", 0x28: "sub", 0x30: "xor", 0x38: "cmp"}
ARITH_BASES = set(ARITH_NAMES)
# 0x0F is the two-byte escape and 0x26/0x2E/0x36/0x3E are segment prefixes, all of which share a
# `& 0xF8` bucket with an arithmetic family; 0x27/0x2F/0x37/0x3F are the BCD adjusts.
ARITH_SKIP = {0x0F, 0x26, 0x27, 0x2E, 0x2F, 0x36, 0x37, 0x3E, 0x3F}
GROUP1 = {0x80: 1, 0x81: 4, 0x83: 1}
GROUP1_NAMES = {0: "add", 1: "or", 2: "adc", 3: "sbb", 4: "and", 5: "sub", 6: "xor", 7: "cmp"}
SHIFTS = {0xC0: 1, 0xC1: 1, 0xD0: 0, 0xD1: 0, 0xD2: 0, 0xD3: 0}
SHIFT_NAMES = {0: "rol", 1: "ror", 2: "rcl", 3: "rcr", 4: "shl", 5: "shr", 6: "sal", 7: "sar"}
GROUP3_NAMES = {0: "test", 1: "test", 2: "not", 3: "neg", 4: "mul", 5: "imul", 6: "div",
                7: "idiv"}
COND = ["o", "no", "b", "ae", "e", "ne", "be", "a", "s", "ns", "p", "np", "l", "ge", "le", "g"]


class Insn:
    """One decoded instruction: its length, a rendered form, what it writes, and its source shape."""

    __slots__ = ("length", "text", "dests", "kind", "base", "index", "disp", "imm")

    def __init__(self, length, text, dests, kind="other", base=None, index=None, disp=None,
                 imm=None):
        self.length, self.text, self.dests, self.kind = length, text, dests, kind
        self.base, self.index, self.disp, self.imm = base, index, disp, imm


def sx8(v):
    return v - 0x100 if v >= 0x80 else v


def sx32(v):
    return v - 0x100000000 if v >= 0x80000000 else v


def hxv(v):
    if v is None:
        return ""
    return "-0x%x" % -v if v < 0 else "+0x%x" % v


def mtext(base, index, disp):
    """Render an effective address the way a listing does."""
    if base is None and index is None:
        return "[0x%08x]" % (disp & 0xFFFFFFFF)
    inner = base or ""
    if index:
        inner = (inner + "+" if inner else "") + index
    if disp:
        inner += hxv(disp) if inner else "0x%x" % disp
    return "[" + inner + "]"


def operand(code, i):
    """Decode a modrm (+SIB, +displacement) at `i`: (next, mod, reg, rm, base, index, disp)."""
    if i >= len(code):
        return None
    b = code[i]
    i += 1
    mod, reg, rm = b >> 6, (b >> 3) & 7, b & 7
    base, index, disp = None, None, None
    if mod == 3:
        return i, mod, reg, rm, None, None, None
    if rm == 4:                                   # SIB
        if i >= len(code):
            return None
        sib = code[i]
        i += 1
        idx = (sib >> 3) & 7
        index = None if idx == 4 else REGS[idx]
        if mod == 0 and (sib & 7) == 5:
            if i + 4 > len(code):
                return None
            disp = sx32(struct.unpack_from("<I", code, i)[0])
            i += 4
        else:
            base = REGS[sib & 7]
    elif mod == 0 and rm == 5:                    # disp32 only: an absolute address
        if i + 4 > len(code):
            return None
        disp = sx32(struct.unpack_from("<I", code, i)[0])
        i += 4
    else:
        base = REGS[rm]
    if mod == 1:
        if i + 1 > len(code):
            return None
        disp = sx8(code[i])
        i += 1
    elif mod == 2:
        if i + 4 > len(code):
            return None
        disp = sx32(struct.unpack_from("<I", code, i)[0])
        i += 4
    return i, mod, reg, rm, base, index, disp


def where(mod, rm, base, index, disp):
    """Operand text and the source shape a `mov`/`lea` from it has."""
    if mod == 3:
        return REGS[rm], "register"
    text = mtext(base, index, disp)
    if base is None and index is None:
        return text, "global"
    if index is not None:
        return text, "array"
    if base in ("esp", "ebp"):
        return text, "stack"
    if disp:
        return text, "field"
    return text, "head"


def decode(code, i):
    """One instruction at `i`, or None when this decoder does not know it."""
    start = i
    while i < len(code) and code[i] in PREFIXES:
        i += 1
    if i >= len(code):
        return None
    op = code[i]
    i += 1

    if 0x40 <= op <= 0x47:                        # inc r32
        return Insn(i - start, "inc %s" % REGS[op - 0x40], [REGS[op - 0x40]], kind="other")
    if 0x48 <= op <= 0x4F:                        # dec r32
        return Insn(i - start, "dec %s" % REGS[op - 0x48], [REGS[op - 0x48]], kind="other")
    if 0x50 <= op <= 0x57:
        return Insn(i - start, "push %s" % REGS[op - 0x50], [])
    if 0x58 <= op <= 0x5F:
        return Insn(i - start, "pop %s" % REGS[op - 0x58], [REGS[op - 0x58]], kind="stack")
    if 0xB0 <= op <= 0xB7:                        # mov r8, imm8
        if i + 1 > len(code):
            return None
        return Insn(i + 1 - start, "mov %s,0x%x" % (REGS[op - 0xB0], code[i]),
                    [REGS[op - 0xB0]], kind="other")
    if 0xB8 <= op <= 0xBF:
        if i + 4 > len(code):
            return None
        imm = struct.unpack_from("<I", code, i)[0]
        return Insn(i + 4 - start, "mov %s,0x%08x" % (REGS[op - 0xB8], imm), [REGS[op - 0xB8]],
                    kind="immediate", imm=imm)
    if op == 0x68:
        if i + 4 > len(code):
            return None
        return Insn(i + 4 - start, "push 0x%08x" % struct.unpack_from("<I", code, i)[0], [])
    if op == 0x6A:
        if i + 1 > len(code):
            return None
        return Insn(i + 1 - start, "push 0x%x" % code[i], [])
    if op == 0xE8:
        if i + 4 > len(code):
            return None
        return Insn(i + 4 - start, "call rel 0x%08x"
                    % struct.unpack_from("<I", code, i)[0], ["eax"], kind="call")
    if 0xE0 <= op <= 0xE3:                        # loopne/loope/loop/jecxz rel8
        return Insn(i + 1 - start, "loop rel8", []) if i + 1 <= len(code) else None
    if op in (0xD4, 0xD5):                        # aam / aad, imm8
        return Insn(i + 1 - start, "aam/aad", [], kind="other") if i + 1 <= len(code) else None
    if op in (0x9B, 0x9E, 0x9F, 0xC9, 0xD7, 0xA4, 0xA5, 0xA6, 0xA7, 0xAA, 0xAB, 0xAC, 0xAD,
              0xAE, 0xAF, 0x60, 0x61, 0x06, 0x07, 0x0E, 0x16, 0x17, 0x1E, 0x1F):
        return Insn(i - start, "one-byte", [], kind="other")
    if op in (0xA8, 0xA9):                        # test al/eax, imm
        w = 1 if op == 0xA8 else 4
        if i + w > len(code):
            return None
        return Insn(i + w - start, "test %s,imm" % ("al" if w == 1 else "eax"), [], kind="other")
    if op == 0xFE:                                # inc/dec r/m8
        got = operand(code, i)
        if not got:
            return None
        return Insn(got[0] - start, "inc/dec r/m8", [], kind="other")
    if 0xD8 <= op <= 0xDF:                        # x87: modrm + displacement, no immediate
        got = operand(code, i)
        if not got:
            return None
        i2, mod, reg, rm, base, index, disp = got
        text, _shape = where(mod, rm, base, index, disp)
        return Insn(i2 - start, "fpu %s" % text, [], kind="other")
    if op == 0xE9 or op == 0xEB or 0x70 <= op <= 0x7F:
        w = 4 if op == 0xE9 else 1
        return Insn(i + w - start, "jmp/jcc", []) if i + w <= len(code) else None
    if op in (0x90, 0x98, 0x99, 0x9C, 0x9D, 0xC3, 0xCC, 0xF4, 0xFC, 0xFD, 0xF5, 0xF8, 0xF9):
        return Insn(i - start, {0x90: "nop", 0xC3: "ret", 0xCC: "int3"}.get(op, "one-byte"), [])
    if op == 0xC2:
        return Insn(i + 2 - start, "ret imm16", []) if i + 2 <= len(code) else None
    if op in (0xA0, 0xA1, 0xA2, 0xA3):
        if i + 4 > len(code):
            return None
        va = struct.unpack_from("<I", code, i)[0]
        if op == 0xA1:
            return Insn(i + 4 - start, "mov eax,[0x%08x]" % va, ["eax"], kind="global",
                        disp=sx32(va))
        if op == 0xA3:
            return Insn(i + 4 - start, "mov [0x%08x],eax" % va, [], kind="store")
        return Insn(i + 4 - start, "mov al/eax,moffs", ["eax"] if op == 0xA0 else [], kind="other")
    if op < 0x40 and (op & 0xF8) in ARITH_BASES and op not in ARITH_SKIP and (op & 7) >= 4:
        # the immediate forms of the same families: `add al,imm8` / `add eax,imm32`
        w = 1 if (op & 7) == 4 else 4
        if i + w > len(code):
            return None
        return Insn(i + w - start, "%s %s,imm" % (ARITH_NAMES[op & 0xF8],
                                                 "al" if w == 1 else "eax"),
                    ["eax"] if w == 4 else [], kind="other")
    if op < 0x40 and (op & 0xF8) in ARITH_BASES and op not in ARITH_SKIP:
        base_op = op & 0xF8
        got = operand(code, i)
        if not got:
            return None
        i2, mod, reg, rm, base, index, disp = got
        text, shape = where(mod, rm, base, index, disp)
        name = ARITH_NAMES[base_op]
        if op & 1:                                # `xor r32, r/m32`: the register is written
            return Insn(i2 - start, "%s %s,%s" % (name, REGS[reg], text), [REGS[reg]],
                        kind="zero" if (mod == 3 and rm == reg) else shape,
                        base=base, index=index, disp=disp)
        return Insn(i2 - start, "%s %s,%s" % (name, text, REGS[reg]),
                    [REGS[rm]] if mod == 3 else [], kind="other", base=base, index=index, disp=disp)
    if op in GROUP1:
        got = operand(code, i)
        if not got:
            return None
        i2, mod, reg, rm, base, index, disp = got
        if i2 + GROUP1[op] > len(code):
            return None
        text, _shape = where(mod, rm, base, index, disp)
        return Insn(i2 + GROUP1[op] - start, "%s %s,imm" % (GROUP1_NAMES[reg], text),
                    [REGS[rm]] if mod == 3 else [], kind="other")
    if op in SHIFTS or op in (0xC0, 0xC1):
        got = operand(code, i)
        if not got:
            return None
        i2, mod, reg, rm, base, index, disp = got
        extra = 1 if op in (0xC0, 0xC1) else 0
        if i2 + extra > len(code):
            return None
        text, _shape = where(mod, rm, base, index, disp)
        return Insn(i2 + extra - start, "%s %s" % (SHIFT_NAMES[reg], text),
                    [REGS[rm]] if mod == 3 else [], kind="other")
    if op in (0xF6, 0xF7):
        got = operand(code, i)
        if not got:
            return None
        i2, mod, reg, rm, base, index, disp = got
        text, _shape = where(mod, rm, base, index, disp)
        immw = (1 if op == 0xF6 else 4) if reg in (0, 1) else 0
        if i2 + immw > len(code):
            return None
        dests = [REGS[rm]] if (mod == 3 and reg in (2, 3)) else []
        return Insn(i2 + immw - start, "%s %s" % (GROUP3_NAMES[reg], text), dests, kind="other")
    if op in (0x8B, 0x8D):                        # mov r32, r/m32  /  lea r32, m
        got = operand(code, i)
        if not got:
            return None
        i2, mod, reg, rm, base, index, disp = got
        text, shape = where(mod, rm, base, index, disp)
        name = "mov" if op == 0x8B else "lea"
        if op == 0x8D and shape in ("head", "field"):
            shape = "lea-field"
        if op == 0x8D and shape in ("global", "stack", "array"):
            shape = "lea-" + shape
        if op == 0x8B and shape == "global":
            disp = disp if disp is not None else 0
        return Insn(i2 - start, "%s %s,%s" % (name, REGS[reg], text), [REGS[reg]], kind=shape,
                    base=base, index=index, disp=disp)
    if op in (0x88, 0x89, 0x8A):
        got = operand(code, i)
        if not got:
            return None
        i2, mod, reg, rm, base, index, disp = got
        text, _shape = where(mod, rm, base, index, disp)
        text = text if op != 0x88 else text
        if op == 0x8A:
            return Insn(i2 - start, "mov %s,byte %s" % (REGS[reg], text), [REGS[reg]],
                        kind="load8")
        return Insn(i2 - start, "mov byte %s,%s" % (text, REGS[reg]), [], kind="store")
    if op in (0x84, 0x85):                        # test r/m, r: flags only
        got = operand(code, i)
        if not got:
            return None
        i2, mod, reg, rm, base, index, disp = got
        text, _shape = where(mod, rm, base, index, disp)
        return Insn(i2 - start, "test %s,%s" % (text, REGS[reg]), [], kind="other")
    if op in (0x86, 0x87):
        got = operand(code, i)
        if not got:
            return None
        i2, mod, reg, rm, base, index, disp = got
        text, _shape = where(mod, rm, base, index, disp)
        return Insn(i2 - start, "xchg %s,%s" % (text, REGS[reg]), [REGS[reg]], kind="register")
    if op == 0x8F:
        got = operand(code, i)
        if not got:
            return None
        i2, mod, reg, rm, base, index, disp = got
        text, _shape = where(mod, rm, base, index, disp)
        if mod != 3:
            return Insn(i2 - start, "pop %s" % text, [], kind="other")
        return Insn(i2 - start, "pop %s" % text, [REGS[rm]], kind="stack")
    if op in (0xC6, 0xC7):
        got = operand(code, i)
        if not got:
            return None
        i2, mod, reg, rm, base, index, disp = got
        immw = 1 if op == 0xC6 else 4
        if i2 + immw > len(code):
            return None
        if mod == 3:
            imm = code[i2] if immw == 1 else struct.unpack_from("<I", code, i2)[0]
            return Insn(i2 + immw - start, "mov %s,0x%x" % (REGS[rm], imm), [REGS[rm]],
                        kind="immediate", imm=imm)
        return Insn(i2 + immw - start, "mov %s,imm" % mtext(base, index, disp), [], kind="store")
    if op in (0x69, 0x6B):
        got = operand(code, i)
        if not got:
            return None
        i2, mod, reg, rm, base, index, disp = got
        immw = 4 if op == 0x69 else 1
        if i2 + immw > len(code):
            return None
        return Insn(i2 + immw - start, "imul %s,..." % REGS[reg], [REGS[reg]], kind="other")
    if op == 0xFF:
        got = operand(code, i)
        if not got:
            return None
        i2, mod, reg, rm, base, index, disp = got
        text, _shape = where(mod, rm, base, index, disp)
        if reg in (2, 3):
            return Insn(i2 - start, "call %s" % text, ["eax"], kind="call",
                        base=base, index=index, disp=disp)
        if reg in (0, 1):
            return Insn(i2 - start, "%s %s" % ("inc" if reg == 0 else "dec", text),
                        [REGS[rm]] if mod == 3 else [], kind="other")
        return Insn(i2 - start, "group5 /%d %s" % (reg, text), [], kind="other")
    if op == 0x0F:
        if i >= len(code):
            return None
        op2 = code[i]
        i += 1
        if 0x80 <= op2 <= 0x8F:
            return Insn(i + 4 - start, "jcc rel32", []) if i + 4 <= len(code) else None
        if op2 in (0x31, 0xA2, 0x0B, 0xC8, 0xC9, 0xCA, 0xCB, 0xCC, 0xCD, 0xCE, 0xCF, 0x77):
            return Insn(i - start, "cpuid/rdtsc/bswap/emms", [], kind="other")
        got = operand(code, i)
        if not got:
            return None
        i2, mod, reg, rm, base, index, disp = got
        text, _shape = where(mod, rm, base, index, disp)
        if op2 in (0xA4, 0xA5, 0xAC, 0xAD):           # shld/shrd
            w = 1 if op2 in (0xA4, 0xAC) else 0
            return Insn(i2 + w - start, "shld/shrd", [REGS[reg]], kind="other") \
                if i2 + w <= len(code) else None
        if op2 in (0xB6, 0xB7, 0xBE, 0xBF):
            return Insn(i2 - start, "mov%s %s,%s" % ("zx" if op2 < 0xBE else "sx", REGS[reg], text),
                        [REGS[reg]], kind="load8" if op2 in (0xB6, 0xBE) else "load16")
        if op2 == 0xAF:
            return Insn(i2 - start, "imul %s,%s" % (REGS[reg], text), [REGS[reg]], kind="other")
        if 0x40 <= op2 <= 0x4F:
            return Insn(i2 - start, "cmov%s %s,%s" % (COND[op2 - 0x40], REGS[reg], text),
                        [REGS[reg]], kind="other")
        if 0x90 <= op2 <= 0x9F:
            return Insn(i2 - start, "set%s %s" % (COND[op2 - 0x90], text),
                        [REGS[rm]] if (mod == 3 and rm < 4) else [], kind="other")
        if op2 == 0xB1:
            return Insn(i2 - start, "cmpxchg %s,%s" % (text, REGS[reg]), [REGS[reg], "eax"],
                        kind="other")
        if op2 == 0x1F:
            return Insn(i2 - start, "nop r/m", [], kind="other")
        # Every remaining SSE/integer opcode in this range takes a modrm: assume one rather than
        # refuse the instruction, since a refusal ends the sweep and hides the whole function.
        # The operand is consumed but no GPR write is claimed: for an SSE form the destination may
        # be the r/m side, and a wrong claim here would mis-attribute a later call's receiver.
        return Insn(i2 - start, "0f %02x %s" % (op2, text), [], kind="other")
    if op in (0x8C, 0x8E):
        got = operand(code, i)
        if not got:
            return None
        return Insn(got[0] - start, "mov seg", [], kind="other")
    return None


OBJDUMP_LINE = re.compile(r"^\s*([0-9a-f]+):\t(.*)$")
DEST_FIRST = set("mov lea movzx movsx movsxd add sub and or xor adc sbb imul shl sal shr sar rol ror "
                 "rcl rcr inc dec neg not pop xchg bswap cmpxchg".split())
SIZE_PTR = re.compile(r"^(BYTE|WORD|DWORD|QWORD|XMMWORD|TBYTE|FWORD) PTR ")
CALL_CLOBBER = {"eax", "ecx", "edx"}


def parse_objdump(text):
    """(instructions, desync addresses) from an `objdump -D` listing: (va, length, text, bytes)."""
    insns, desync = [], []
    for line in text.splitlines():
        m = OBJDUMP_LINE.match(line)
        if not m:
            continue
        va, body = int(m.group(1), 16), m.group(2)
        fields = body.split("\t")
        hexs = fields[0]
        mnem = (fields[1] if len(fields) > 1 else fields[0]).strip()
        if mnem.startswith(".byte"):
            desync.append(va)
            continue
        toks = [t for t in hexs.split() if re.fullmatch(r"[0-9a-f]{2}", t)]
        insns.append((va, len(toks), mnem, hexs.strip()))
    return insns, desync


def written_regs(insn):
    """The 32-bit GPRs an objdump instruction writes (Intel syntax); nothing is guessed for SSE."""
    mnem, _, rest = insn[2].partition(" ")
    op = mnem.lower()
    if op in ("call", "callq"):
        return set(CALL_CLOBBER)
    if op in ("mul", "div", "idiv"):
        return {"eax", "edx"}
    if op in ("cdq", "cwd"):
        return {"edx"}
    if op.startswith(("cmov", "set", "f", "movs", "movap", "movd", "movq", "pxor", "pand", "por")):
        return set()
    rest = rest.strip()
    if not rest:
        return set()
    ops = [o.strip() for o in rest.split(",")]
    first = SIZE_PTR.sub("", ops[0])
    out = set()
    if op in DEST_FIRST:
        if first in REGS:
            out.add(first)
        if op == "xchg" and len(ops) > 1 and ops[1] in REGS:
            out.add(ops[1])
    return out


def oracle_sweep(insns, site_va, reg):
    """The last instruction before the site that writes `reg`, from an objdump listing.

    A linear walk over the oracle's own starts: unlike the narrow sweep this cannot desync, because
    objdump decoded every byte between the function's start and the site. `None` means the register
    is never written on the way in -- the receiver is the value passed in.
    """
    writer, steps = None, 0
    for insn in insns:
        if insn[0] >= site_va:
            break
        steps += 1
        if reg in written_regs(insn):
            writer = insn
    return writer, steps


def objdump_read(exe, blob, base_va, lo_off, site_off, site_va):
    """What the independent decoder says about the site: agree / spans / desync / none.

    Returns (verdict, instructions, text at the site or of the instruction that spans it). A `.byte`
    before the site means objdump's own sweep lost alignment, so its verdict is not evidence there
    and the row keeps the narrow decoder's reading.
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
        fh.write(blob[lo_off:min(site_off + 16, len(blob))])
        path = fh.name
    try:
        out = subprocess.run([exe, "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                              "--adjust-vma=0x%x" % (base_va + lo_off), path],
                             capture_output=True, text=True, check=False).stdout
    finally:
        os.unlink(path)
    insns, desync = parse_objdump(out)
    if any(va < site_va for va in desync):
        return "desync", insns, ""
    here = [i for i in insns if i[0] == site_va]
    if here:
        return "agree", insns, here[0][2]
    cont = [i for i in insns if i[0] < site_va < i[0] + i[1]]
    if cont:
        return "spans", insns, cont[0][2]
    return "none", insns, ""


def sweep(blob, fn_off, site_off, limit=8000):
    """Linear sweep from the function's start to the call, tracking the last write of each register.

    The call's receiver is the last value written to that register on the way in, and the function
    start is known from `symbols/xbox_functions.csv`, so this is a forward read rather than a
    backward guess: every instruction consumed advances the cursor by its own decoded length, and
    the sweep only counts when it lands **exactly** on the call. A linear sweep ignores branches, so
    a write the taken path skips can be reported; the row names the instruction's address, which is
    what makes that checkable. Returns (aligned, steps, writers).
    """
    writers = {}
    cur, steps = fn_off, 0
    while cur < site_off and steps < limit:
        ins = decode(blob, cur)
        if not ins:
            return False, steps, writers
        for reg in ins.dests:
            writers[reg] = (cur, ins)
        cur += ins.length
        steps += 1
    return cur == site_off, steps, writers


def backward(blob, off, reg, steps=8):
    """Fallback when the sweep does not align: walk back one exact-length instruction at a time.

    Each step collects every start whose decoded length lands on the current head, then prefers the
    candidate that a second link confirms (some instruction also ends at its own start), that reads
    memory or an immediate, and that is widest. A wrong pick here derails the whole walk, which is
    why the sweep is tried first.
    """
    cur, walked = off, 0
    while walked < steps and cur > 0:
        cands = []
        for start in range(max(0, cur - WINDOW), cur):
            ins = decode(blob, start)
            if not ins or start + ins.length != cur:
                continue
            cands.append((start, ins))
        if not cands:
            return None
        cands.sort(key=lambda s: (0 if chain_links(blob, s[0]) >= 2 else 1,
                                  1 if s[1].kind in ("register", "load8", "load16", "other",
                                                     "store") else 0,
                                  -s[1].length))
        start, ins = cands[0]
        walked += 1
        if reg in ins.dests:
            return start, ins
        cur = start
    return None


def chain_links(blob, off, depth=2):
    """How many consecutive instruction ends confirm the boundary at `off`."""
    if depth == 0:
        return 0
    for start in range(max(0, off - 15), off):
        ins = decode(blob, start)
        if ins and start + ins.length == off:
            return 1 + chain_links(blob, start, depth - 1)
    return 0


def class_tables(path):
    out = {}
    if not path or not os.path.isfile(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                out[int(row["table_va"], 16)] = row.get("class") or ""
            except (KeyError, ValueError, TypeError):
                continue
    return out


def global_kind(xbe, tables, va):
    """What an absolute operand points at: a class table, a pointer to one, or plain data."""
    if va in tables:
        return "class-table" + (":" + tables[va] if tables[va] else "")
    try:
        dword = struct.unpack_from("<I", xbe.read_va(va, 4))[0]
    except (KeyError, IndexError, struct.error):
        return "outside-image"
    if dword in tables:
        return "ptr-to-class-table" + (":" + tables[dword] if tables[dword] else "")
    for section in getattr(xbe, "sections", []):
        if section.contains(dword):
            return "ptr-" + ("text" if section.executable else section.name.strip("."))
    return "data:0x%08x" % dword


def spanning(blob, off):
    """The instruction that *contains* `off` when no instruction ends there (a byte-pattern false positive)."""
    for start in range(max(0, off - 15), off):
        ins = decode(blob, start)
        if ins and start < off < start + ins.length:
            return start, ins
    return None


def analyse(xbe, tables, blob, base_va, site_va, reg, fn_va=None, oracle=None):
    """One row: whether the site is a boundary (a disagreement between the decoders is named), and
    what loads the receiver there. `oracle` is (verdict, instructions, text) from `objdump_read`."""
    off = site_va - base_va
    verdict, oracle_insns, oracle_text = oracle if oracle else ("", [], "")
    row = {"site_va": "0x%08x" % site_va, "reg": reg, "defining_va": "",
           "bytes": blob[max(0, off - WINDOW):off].hex(" "), "form": "", "source": "unread",
           "base": "", "disp": "", "value": "", "value_kind": "", "candidates": "0", "walk": "0",
           "aligned": "no", "oracle": verdict, "oracle_form": oracle_text}
    # A `call [reg+0x4c]` is a byte pattern, so it also matches the tail of some other instruction's
    # displacement. An instruction that *ends* exactly at the site is what makes it a real boundary.
    ends = [s for s in range(max(0, off - 15), off)
            if (i := decode(blob, s)) and s + i.length == off]
    # ...and a single landing instruction is not enough: at `0x0006dfc8` the `0xFF` is the last
    # displacement byte of a `lea`, and a wrong-length decode of the same bytes also lands there.
    # A real boundary has a chain of instruction ends behind it; require three links.
    if ends and max(chain_links(blob, s, 4) for s in ends) < 3:
        ends = []
    # The narrow decoder can also be talked into a chain of wrong-length decodes (0x2d113c: the
    # `lea`'s own bytes read as `add [ebp-0x15b73],ecx`), so the independent decoder decides.
    if verdict == "spans":
        cont = [i for i in oracle_insns if i[0] < site_va < i[0] + i[1]]
        row["source"] = "not-a-boundary"
        if cont:
            va, length, text, hexs = cont[0]
            row["defining_va"] = "0x%08x" % va
            row["bytes"] = blob[va - base_va:va - base_va + length].hex(" ")
            row["form"] = text
        return row
    if not ends and verdict != "agree":
        span = spanning(blob, off)
        row["source"] = "not-a-boundary"
        row["form"] = span[1].text if span else ""
        if span:
            row["defining_va"] = "0x%08x" % (base_va + span[0])
            row["bytes"] = blob[span[0]:span[0] + span[1].length].hex(" ")
        return row
    start = ins = None
    if fn_va is not None and base_va <= fn_va < site_va:
        aligned, steps, writers = sweep(blob, fn_va - base_va, off)
        row["walk"] = str(steps)
        if aligned:
            row["aligned"] = "yes"
            if reg in writers:
                start, ins = writers[reg]
            else:
                # never written since the function was entered: the receiver is the incoming
                # register itself, which in this build's C++ convention is `this`.
                row["source"] = "entry-register"
                row["form"] = "(%s as passed in)" % reg
                row["defining_va"] = "0x%08x" % fn_va
                return row
    if ins is None and verdict == "agree" and fn_va is not None and oracle_insns:
        # the narrow decoder could not follow the stream to the site; walk the oracle's own starts
        writer, steps = oracle_sweep(oracle_insns, site_va, reg)
        row["walk"] = str(steps)
        row["aligned"] = "yes"
        if writer is None:
            row["source"] = "entry-register"
            row["form"] = "(%s as passed in)" % reg
            row["defining_va"] = "0x%08x" % fn_va
            return row
        start = writer[0] - base_va
        row["defining_va"] = "0x%08x" % writer[0]
        row["bytes"] = blob[start:start + writer[1]].hex(" ")
        ins = decode(blob, start)
        if ins is None:
            # the oracle read the boundary and the writer, this decoder not the instruction's shape
            row["form"] = writer[2]
            row["source"] = "unread"
            return row
    if ins is None:
        got = backward(blob, off, reg)
        if got is None:
            return row
        start, ins = got
    row["defining_va"] = "0x%08x" % (base_va + start)
    row["bytes"] = blob[start:start + ins.length].hex(" ")
    row["form"] = ins.text

    def finish(source, base="", disp="", value="", value_kind=""):
        row["source"], row["base"], row["disp"] = source, base, disp
        row["value"], row["value_kind"] = value, value_kind
        return row

    if ins.kind == "call":
        return finish("call-result")
    if ins.kind == "immediate":
        return finish("immediate", value="0x%08x" % ins.imm if ins.imm is not None else "")
    if ins.kind == "stack":
        return finish("stack", base=ins.base or "", disp=hxv(ins.disp))
    if ins.kind == "global":
        va = (ins.disp or 0) & 0xFFFFFFFF
        return finish("global", base=ins.base or "", value="0x%08x" % va,
                      value_kind=global_kind(xbe, tables, va))
    if ins.kind == "zero":
        return finish("zero")
    if ins.kind in ("head", "field", "array"):
        source = {"head": "object-head", "field": "object-field",
                  "array": "array-element"}[ins.kind]
        return finish(source, base=ins.base or "", disp=hxv(ins.disp))
    if ins.kind.startswith("lea-"):
        source = {"lea-field": "address-of-field", "lea-stack": "stack-address",
                  "lea-global": "global-address", "lea-array": "array-address"}[ins.kind]
        lea_global = ins.kind == "lea-global"
        return finish(source, base=ins.base or "", disp=hxv(ins.disp),
                      value="0x%08x" % (ins.disp & 0xFFFFFFFF) if lea_global else "",
                      value_kind=global_kind(xbe, tables, ins.disp & 0xFFFFFFFF)
                      if lea_global else "")
    return finish(ins.kind, base=ins.base or "", disp=hxv(ins.disp))


def selftest():
    base = 0x1000
    pad = b"\x90" * 4
    cases = [
        (b"\x8d\x95\xf4\xfb\xff\xff", "edx", "stack-address", "lea edx,[ebp-0x40c]"),
        (b"\x56\x8b\xf1\x8b\x06", "eax", "object-head", "mov eax,[esi]"),
        (b"\x8b\x4e\x4c\x8b\x01", "eax", "object-head", "mov eax,[ecx]"),
        (b"\x8b\x15\x1c\xf5\x7b\x00", "edx", "global", "mov edx,[0x007bf51c]"),
        (b"\xa1\xac\xef\x7b\x00", "eax", "global", "mov eax,[0x007befac]"),
        (b"\x8b\x75\x18", "esi", "stack", "mov esi,[ebp+0x18]"),
        (b"\xb8\xe8\x13\x63\x00", "eax", "immediate", "mov eax,0x006313e8"),
        (b"\x33\xc9", "ecx", "zero", "xor ecx,ecx"),
        (b"\x5e", "esi", "stack", "pop esi"),
        (b"\x8b\x44\x24\x10", "eax", "stack", "mov eax,[esp+0x10]"),
        (b"\x8d\x9e\x78\x14\x00\x00", "ebx", "address-of-field", "lea ebx,[esi+0x1478]"),
        (b"\x8b\x54\x95\x00", "edx", "array-element", "mov edx,[ebp+edx]"),
        (b"\xe8\x6c\x80\xff\xff", "eax", "call-result", "call rel 0xffff806c"),
        (b"\x8b\x8c\x24\xdc\x00\x00\x00", "ecx", "stack", "mov ecx,[esp+0xdc]"),
    ]
    blob, sites = bytearray(), []
    for before, reg, _src, _form in cases:
        blob += b"\x90\x90\x90\x90"          # a real chain behind the site, so it is a boundary
        blob += before
        sites.append(len(blob))
        blob += b"\xff\x52\x4c" + pad
    blob = bytes(blob)
    bad = []
    for (before, reg, want_source, want_form), site_off in zip(cases, sites):
        row = analyse(_StubXbe(), {}, blob, base, base + site_off, reg)
        if row["source"] != want_source or row["form"] != want_form:
            bad.append("%s / %s: got %s %r, want %s %r" % (before.hex(" "), reg, row["source"],
                                                           row["form"], want_source, want_form))
    # a boundary whose register nothing in the window writes is `unread`, never a guess, and a site
    # with no chain of instruction ends behind it is `not-a-boundary`, never a receiver
    row = analyse(_StubXbe(), {}, b"\x90\x90\x90\x90\xff\x52\x4c", base, base + 4, "ecx")
    if row["source"] != "unread":
        bad.append("unwritten register read as %s" % row["source"])
    row = analyse(_StubXbe(), {}, b"\x0f\x0b\x0f\x0b\xff\x52\x4c", base, base + 4, "edx")
    if row["source"] != "not-a-boundary":
        bad.append("a site with no chain behind it read as %s" % row["source"])

    # the oracle: its listing parses to (va, length, text), it names what each instruction writes, and
    # a site it says is inside an instruction is `not-a-boundary` even where the narrow chain passes
    listing = (" 1000:\t55                   \tpush   ebp\n"
               " 1001:\t8b ec                \tmov    ebp,esp\n"
               " 1003:\t8b 06                \tmov    eax,DWORD PTR [esi]\n"
               " 1005:\tff 50 4c             \tcall   DWORD PTR [eax+0x4c]\n"
               " 1008:\t8d                   \t.byte 0x8d\n")
    insns, desync = parse_objdump(listing)
    if [(va, n) for va, n, _t, _b in insns] != [(0x1000, 1), (0x1001, 2), (0x1003, 2), (0x1005, 3)]:
        bad.append("objdump listing parsed as %r" % (insns,))
    if desync != [0x1008]:
        bad.append(".byte line not recorded as a desync: %r" % (desync,))
    if written_regs(insns[2]) != {"eax"} or written_regs(insns[0]) or written_regs(insns[3]) != {"eax", "ecx", "edx"}:
        bad.append("written_regs: %r %r %r" % (written_regs(insns[0]), written_regs(insns[2]),
                                               written_regs(insns[3])))
    writer, _steps = oracle_sweep(insns, 0x1005, "eax")
    if writer is None or writer[0] != 0x1003:
        bad.append("oracle sweep picked %r" % (writer,))
    if oracle_sweep(insns, 0x1005, "edi")[0] is not None:
        bad.append("oracle sweep invented a writer for edi")

    # `spans`: the site is the last displacement byte of a `lea`, so it is not a call at all
    spans_blob = b"\x90\x90\x90\x90" + b"\x8d\x8d\xa4\xfe\xff\xff" + b"\x51\x50"
    spans_insns = [(base + 4, 6, "lea    ecx,[ebp-0x15c]", "8d 8d a4 fe ff ff")]
    row = analyse(_StubXbe(), {}, spans_blob, base, base + 9, "ecx",
                  oracle=("spans", spans_insns, "lea    ecx,[ebp-0x15c]"))
    if row["source"] != "not-a-boundary" or row["oracle_form"] != "lea    ecx,[ebp-0x15c]":
        bad.append("a spanned site read as %s %r" % (row["source"], row["oracle_form"]))
    # `agree` where the narrow sweep cannot follow the stream: the oracle sweep still reads it
    gap_blob = b"\xc4\x01\x90\x90\x8b\x06\xff\x50\x4c"
    gap_insns = [(base, 2, "les    eax,[ecx]", "c4 01"), (base + 2, 1, "nop", "90"),
                 (base + 3, 1, "nop", "90"), (base + 4, 2, "mov    eax,DWORD PTR [esi]", "8b 06")]
    row = analyse(_StubXbe(), {}, gap_blob, base, base + 6, "eax", fn_va=base,
                  oracle=("agree", gap_insns, "call   DWORD PTR [eax+0x4c]"))
    if row["source"] != "object-head" or row["defining_va"] != "0x%08x" % (base + 4):
        bad.append("oracle sweep row read as %s %s" % (row["source"], row["defining_va"]))
    # ...and a register the oracle never sees written is the value passed in, not "unread"
    row = analyse(_StubXbe(), {}, gap_blob, base, base + 6, "edi", fn_va=base,
                  oracle=("agree", gap_insns, "call   DWORD PTR [edi+0x4c]"))
    if row["source"] != "entry-register":
        bad.append("unwritten oracle register read as %s" % row["source"])
    for line in bad:
        print("FAIL " + line)
    if bad:
        raise SystemExit(1)
    print("selftest ok: %d windows classified, garbled window left unread" % len(cases))


class _StubXbe:
    sections = []

    def read_va(self, va, n):
        raise KeyError(va)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("--sites", default=SITES)
    ap.add_argument("--tables", default=TABLES)
    ap.add_argument("-o", "--out", default=OUT)
    ap.add_argument("--oracle", metavar="PATH",
                    help="independent decoder used to decide the boundary (default: objdump on PATH)")
    ap.add_argument("--no-oracle", action="store_true",
                    help="narrow decoder only: reproduces the pre-oracle reading")
    ap.add_argument("--check", action="store_true",
                    help="synthetic self-test; no dump, writes nothing")
    ap.add_argument("--summary", action="store_true", help="print the histogram only, write nothing")
    args = ap.parse_args()
    if args.check:
        selftest()
        return 0
    if not args.xbe:
        ap.error("--xbe is required unless --check")
    exe = None if args.no_oracle else (args.oracle or shutil.which("objdump"))
    if exe is None and not args.no_oracle:
        print("no objdump on PATH: falling back to the narrow decoder only", file=sys.stderr)
    from xbe import Xbe  # imported late so --check needs no dump
    xbe = Xbe(args.xbe)
    tables = class_tables(args.tables)
    rows = []
    with open(args.sites, newline="", encoding="utf-8") as fh:
        for site in csv.DictReader(fh):
            if site["section"] != ".text":
                continue
            va = int(site["site_va"], 16)
            section = next((s for s in xbe.sections if s.contains(va)), None)
            if section is None:
                continue
            blob = xbe.read_va(section.va, section.vsize)
            fn_va = int(site["function_va"], 16) if site.get("function_va") else None
            oracle = None
            if exe:
                lo = (fn_va - section.va) if fn_va is not None and fn_va < va \
                    else max(0, va - section.va - 0x600)
                oracle = objdump_read(exe, blob, section.va, lo, va - section.va, va)
            row = analyse(xbe, tables, blob, section.va, va, site["reg"], fn_va=fn_va,
                          oracle=oracle)
            row["slot"] = site["slot"]
            row["function"] = site["function"]
            rows.append(row)
    rows.sort(key=lambda r: (r["slot"], r["site_va"]))
    if not args.summary:
        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=HEADER, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    def counts(subset, key):
        out = {}
        for row in subset:
            k = row[key] or "-"
            out[k] = out.get(k, 0) + 1
        return ", ".join("%s %d" % (k, v) for k, v in sorted(out.items(),
                                                             key=lambda kv: (-kv[1], kv[0])))

    print("%d .text site(s)" % len(rows))
    print("  slot: " + counts(rows, "slot"))
    print("  source: " + counts(rows, "source"))
    print("  oracle: " + counts([r for r in rows if r["oracle"]], "oracle"))
    print("  form: " + counts(rows, "form"))
    print("  base: " + counts([r for r in rows if r["base"]], "base"))
    print("  unread %d, aligned from the function start %d, entry-register %d"
          % (sum(1 for r in rows if r["source"] == "unread"),
             sum(1 for r in rows if r["aligned"] == "yes"),
             sum(1 for r in rows if r["source"] == "entry-register")))
    for slot in sorted({r["slot"] for r in rows}):
        subset = [r for r in rows if r["slot"] == slot]
        print("  slot %s (%d): %s" % (slot, len(subset), counts(subset, "source")))
    print("  globals: " + counts([r for r in rows if r["value_kind"]], "value_kind"))
    if not args.summary:
        print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
