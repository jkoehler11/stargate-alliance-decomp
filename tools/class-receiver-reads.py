#!/usr/bin/env python3
"""What object a mixed or callerless class-table store actually constructs.

`tools/class-store-provenance.py` resolves the pointer each store is made through one level deep and
leaves two residues in U40: the **mixed** tables -- two callers that disagree, or a receiver its
resolver could not name -- and the **no-direct-call** tables, whose installing body no `E8` reaches.
Both are questions about one instruction, so both are answered by reading the same sites one instruction
deeper, and this tool does exactly that:

  * **the receiver comes out of arithmetic.** The build constructs an object in place inside a dynamic
    array: the element's address is the array's data pointer plus `index * <element size>`
    (`mov eax,[esi]` / `imul edi,edi,0x1c` / `add edi,eax` / `mov ecx,edi`), so the receiver is an
    **element of the array at offset `<n>` of the caller**, and the stride in the `imul`/`shl` is the
    element's size. A `lea ecx,[eax+esi*4]` and an `add reg,<imm>` pointer walk are the same read.
  * **the receiver is a pointer field or a fixed object.** `mov ecx,[esi+0x1c]` is not the field at
    `this+0x1c` (an embedded object addressed by `lea`) but the pointer *stored* there, and
    `mov ecx,0x7ab94c` is a `.data` singleton at a named address -- not an unnamed temporary.
  * **the temporary has a size.** An allocation's return is preceded by the argument the allocator was
    handed, and on the control population that argument is the class's registered `object_size`, which
    makes it a size for the tables no registration names.
  * **the callerless table still has a reader.** An installer no `E8` reaches is either tail-called
    (`E9`), pointed at by a dword (a table slot or a `.data` pointer), or genuinely unreached -- and a
    store site inside a gap of the symbol map names its gap head instead of failing.

    tools/class-receiver-reads.py --xbe DUMP/default.xbe -o inventory
    tools/class-receiver-reads.py --report
    tools/class-receiver-reads.py --write-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-receiver-reads.py --check-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-receiver-reads.py --check            # synthetic instructions, no dump, writes nothing

The dump is never committed; the artifact and the doc are. Addresses, field offsets and counts only.
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
sys.path.insert(0, os.path.join(ROOT, "tools"))

OUT = os.path.join("inventory", "class_receiver_reads.csv")
PROV = os.path.join(ROOT, "inventory", "class_store_provenance.csv")
REACH = os.path.join(ROOT, "inventory", "native_vtable_reach.csv")
REG = os.path.join(ROOT, "inventory", "class_registrations.csv")
GAPS = os.path.join(ROOT, "inventory", "gap_heads.csv")
FUNCS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
SRC = os.path.join(ROOT, "inventory", "class_receiver_reads.csv")
PAGE = os.path.join("docs", "systems", "CLASS_REGISTRATION.md")
DOC_START = "<!-- generated:class-receiver-reads -->"
DOC_END = "<!-- /generated:class-receiver-reads -->"

COLUMNS = ("table_va", "class", "population", "resolution", "sites", "bodies", "installer", "callers",
           "verdict", "channels", "receivers", "array_heads", "element_sizes", "statics", "temporary_sizes",
           "registry_size", "size_match", "reach", "evidence")
VERDICTS = ("array-element", "static-object", "pointer-field", "walked-pointer", "both-ways",
            "no-caller", "unresolved")
REGS = ("eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi")
SIZES = ("BYTE PTR ", "WORD PTR ", "DWORD PTR ", "QWORD PTR ", "XMMWORD PTR ", "FWORD PTR ")
NO_WRITE = ("cmp", "test", "push", "jmp", "je", "jne", "jz", "jnz", "jbe", "jae", "ja", "jb", "jg", "jge",
            "jl", "jle", "call", "ret", "nop", "int3", "fld", "fst", "fstp", "fadd", "fsub", "fmul",
            "fdiv", "fild", "fistp", "movss", "movsd", "addss", "mulss", "divss", "subss", "cvttss2si",
            "cvtsi2ss", "comiss", "ucomiss")


# --------------------------------------------------------------------------- text decoding
def split_ops(text):
    """`("mov", ["DWORD PTR [esi+0x64]", "0x4c7854"])` from one objdump intel-syntax instruction."""
    head = text.split(None, 1)
    if not head:
        return "", []
    mnem, ops, cur, depth = head[0], [], "", 0
    if len(head) > 1:
        for ch in head[1]:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            if ch == "," and depth == 0:
                ops.append(cur.strip())
                cur = ""
            else:
                cur += ch
        if cur.strip():
            ops.append(cur.strip())
    return mnem, ops


def bare_reg(op):
    """The register an operand names, or '' for an immediate or a memory operand."""
    op = op.strip()
    for pre in SIZES:
        if op.startswith(pre):
            op = op[len(pre):]
    return op if op in REGS else ""


def mem_of(op):
    """`{"base","index","disp","absolute"}` for a memory operand, else None."""
    op = op.strip()
    for pre in SIZES:
        if op.startswith(pre):
            op = op[len(pre):]
    if op.startswith("ds:"):
        try:
            return {"base": "", "index": "", "disp": int(op[3:], 16), "absolute": True, "scale": 1}
        except ValueError:
            return None
    if not op.startswith("["):
        return None
    inner = op[1:op.index("]")]
    base, index, disp, scale = "", "", 0, 1
    for tok in re.findall(r"[+-]?[^+-]+", inner):
        sign = -1 if tok.startswith("-") else 1
        tok = tok.lstrip("+-").strip()
        if "*" in tok:
            r, _, s = tok.partition("*")
            index = r.strip()
            try:
                scale = int(s.strip(), 16) if s.strip().startswith("0x") else int(s.strip())
            except ValueError:
                scale = 1
        elif tok in REGS:
            base = tok
        elif tok.startswith("0x"):
            disp += sign * int(tok, 16)
        elif tok.isdigit():
            disp += sign * int(tok)
    return {"base": base, "index": index, "disp": disp, "absolute": not base and not index,
            "scale": scale}


def is_imm(op):
    op = op.strip()
    return op.startswith("0x") or op.lstrip("-").isdigit()


def write_target(text):
    """The register an instruction writes, or '' when it writes memory or nothing."""
    mnem, ops = split_ops(text)
    if not ops or mnem in NO_WRITE or mnem.startswith("j"):
        return ""
    return bare_reg(ops[0])


def this_register(insns):
    """The register the body copies `ecx` into in its prologue, or '' when it does not."""
    for _va, text in insns[:14]:
        mnem, ops = split_ops(text)
        if mnem == "call":
            break
        if mnem == "mov" and len(ops) == 2 and ops[1] == "ecx" and bare_reg(ops[0]):
            return bare_reg(ops[0])
    return ""


# --------------------------------------------------------------------------- the deep read
def index_size(insns, idx, reg):
    """The stride a scaled index register carries: `imul r,r,0x1c` -> 0x1c, `shl r,2` -> 4, else 0."""
    for j in range(idx - 1, -1, -1):
        text = insns[j][1]
        mnem, ops = split_ops(text)
        if mnem == "call":
            continue
        if write_target(text) != reg:
            continue
        if mnem == "imul" and len(ops) >= 2:
            last = ops[-1].strip()
            if is_imm(last):
                return int(last, 16) if last.startswith("0x") else int(last)
            return 0
        if mnem == "shl" and len(ops) == 2 and is_imm(ops[1]):
            n = int(ops[1], 16) if ops[1].startswith("0x") else int(ops[1])
            return (1 << n) if 0 < n < 16 else 0
        return 0
    return 0


def pushed_arg(insns, idx, back=4):
    """The immediate the allocating call at `idx` was handed (`push <imm>` / `mov [esp],<imm>`)."""
    for j in range(idx - 1, max(-1, idx - 1 - back), -1):
        _va, text = insns[j]
        mnem, ops = split_ops(text)
        if mnem == "push" and len(ops) == 1 and is_imm(ops[0]):
            return int(ops[0], 16) if ops[0].startswith("0x") else int(ops[0])
        if mnem in ("mov", "movzx", "movsx") and len(ops) == 2:
            m = mem_of(ops[0])
            if m and m["base"] in ("esp", "ebp") and is_imm(ops[1]):
                return int(ops[1], 16) if ops[1].startswith("0x") else int(ops[1])
            break
    return 0


def call_return(insns, idx):
    """`new(<arg>:<callee>)` for the call at `idx`: the size argument it is handed, and its target.

    Only an argument in the range an object size can occupy is reported; a smaller immediate is a flag
    (`push 0xffffffff` is common before the allocator) and a larger one is not a size at all.
    """
    arg = pushed_arg(insns, idx)
    _va, text = insns[idx]
    _mnem, ops = split_ops(text)
    target = ops[0].strip() if ops else "?"
    if not (0x10 <= arg <= 0x200000):
        arg = 0
    return "new(%s:%s)" % ("%#x" % arg if arg else "-", target)


def alloc_arg(chain):
    """The size an `new(<arg>:<callee>)` chain carries, or 0."""
    inner = chain[4:chain.index(":")]
    return int(inner, 16) if inner.startswith("0x") else 0


def deep(insns, idx, reg, thisreg="", depth=0):
    """The receiver `reg` holds just before instruction `idx`, read through arithmetic and loads.

    The vocabulary distinguishes what the one-level read flattened: `this+0x60` is an embedded member
    (addressed with `lea`), `[this+0x60]` is the pointer stored in that field, `array[0x1c]@[this+0x0]`
    is an element of the dynamic array whose data pointer lives at `this+0x0`, `static:0x7ab94c` is a
    fixed object, and `new(0x8e7c:[eax])` is an allocation's return.
    """
    if depth > 8:
        return "?"
    if reg in ("esp", "ebp"):
        return reg
    for j in range(idx - 1, -1, -1):
        text = insns[j][1]
        mnem, ops = split_ops(text)
        if mnem == "call":
            if reg in ("eax", "ecx", "edx"):
                return call_return(insns, j)
            continue
        if write_target(text) != reg:
            continue
        # --- a copy, an address or a load
        if mnem in ("mov", "lea") and len(ops) == 2:
            src = ops[1]
            r = bare_reg(src)
            if r and mnem == "mov":
                if r == "ecx" or r == thisreg:
                    return "this"
                return deep(insns, j, r, thisreg, depth + 1)
            m = mem_of(src)
            if m is None:
                if is_imm(src):
                    n = int(src, 16) if src.startswith("0x") else int(src)
                    # only a value in the image's address range is an object; a small immediate is a
                    # value the body is handing over, not a receiver
                    return ("static:%#x" if n >= 0x10000 else "imm:%#x") % n
                return "?%s" % mnem
            if m["index"]:
                head = deep(insns, j, m["base"], thisreg, depth + 1) if m["base"] else "ds"
                return "array[%#x]@%s" % (m["scale"], add_disp(head, m["disp"]))
            if m["absolute"]:
                return "[%#x]" % m["disp"]
            sub = deep(insns, j, m["base"], thisreg, depth + 1)
            if mnem == "lea":
                return add_disp(sub, m["disp"])
            return deref(sub, m["disp"])                      # a load: the pointer stored in that field
        # --- arithmetic on the register itself
        if mnem in ("add", "sub") and len(ops) == 2 and bare_reg(ops[0]) == reg:
            src = ops[1]
            sign = -1 if mnem == "sub" else 1
            if is_imm(src):
                base = deep(insns, j, reg, thisreg, depth + 1)
                n = int(src, 16) if src.startswith("0x") else int(src)
                return walk(base, sign * n)
            sr = bare_reg(src)
            if not sr:
                return "?%s" % mnem
            head = deep(insns, j, sr, thisreg, depth + 1)
            here = index_size(insns, j, reg)
            there = index_size(insns, j, sr)
            if here and not head.startswith("?"):
                return "array[%#x]@%s" % (here, head)
            if there:
                base = deep(insns, j, reg, thisreg, depth + 1)
                if not base.startswith("?"):
                    return "array[%#x]@%s" % (there, base)
            base = deep(insns, j, reg, thisreg, depth + 1)
            if base.startswith("?"):
                return "walk(%s)" % head
            return "walk(%s)" % base
        if mnem in ("xor", "and") and len(ops) == 2 and ops[1] == reg:
            return "zero"
        if mnem in ("pop", "movzx", "movsx", "inc", "dec", "adc", "sbb"):
            if mnem == "pop":
                return "spill"
            return "?%s" % mnem
        return "?%s" % mnem
    # nothing in the body writes it: the body was entered with it, and for `ecx` (or the register the
    # prologue copied it into) that is the object itself
    return "this" if (reg == "ecx" or (thisreg and reg == thisreg)) else "?"


def deref(chain, disp):
    """The pointer a load reads: `mov ecx,[esi+0x1c]` -> `[this+0x1c]`; a load of a loaded pointer nests."""
    if chain in ("esp", "ebp"):
        return "[%s%+#x]" % (chain, disp)
    if chain.startswith("this"):
        try:
            base = int(chain[5:], 16) if chain.startswith("this+") else 0
        except ValueError:
            return "[%s]" % chain
        return "[this+%#x]" % (base + disp)
    if chain.startswith("[") and chain.endswith("]"):
        return "[%s]" % chain if not disp else "[%s%+#x]" % (chain, disp)
    return "[%s%+#x]" % (chain, disp)


def add_disp(chain, disp):
    """`this` + 0x60 -> `this+0x60`; `[this+0x0]` + 0x1c -> `[this+0x1c]`; a frame slot stays a slot."""
    if not disp:
        return chain
    if chain in ("esp", "ebp"):
        return "%s%+#x" % (chain, disp)
    if chain.startswith("this"):
        try:
            base = int(chain[5:], 16) if chain.startswith("this+") else 0
        except ValueError:
            return chain
        return "this+%#x" % (base + disp)
    if chain.startswith("[") and chain.endswith("]"):
        inner = chain[1:-1]
        if inner.startswith("this"):
            base = int(inner[5:], 16) if inner.startswith("this+") else 0
            return "[this+%#x]" % (base + disp)
        if inner.startswith("0x"):
            return "[%#x]" % (int(inner, 16) + disp)
        return "[%s%+#x]" % (inner, disp)
    return "%s%+#x" % (chain, disp)


def walk(chain, n):
    """A pointer stepped by a constant (a loop over an array of records)."""
    if chain.startswith("?"):
        return chain
    return "walk(%s%+#x)" % (chain, n)


def kind(chain):
    """Which bucket a deep chain belongs to."""
    if chain == "this":
        return "own-object"
    if chain.startswith("this+"):
        return "member"
    if chain.startswith("array["):
        return "array-element"
    if chain.startswith("static:"):
        return "static-object"
    if chain.startswith("imm:"):
        return "unresolved"
    if chain.startswith("ptr("):
        return "pointer-field"
    if chain.startswith("[") or chain == "ds":
        return "global-pointer"
    if chain.startswith("walk("):
        return "walked-pointer"
    if chain.startswith("new("):
        return "allocation"
    if chain in ("esp", "ebp") or chain.startswith("esp+") or chain.startswith("esp-") \
            or chain.startswith("ebp+") or chain.startswith("ebp-") or chain == "spill":
        return "stack-object"
    if chain == "zero":
        return "null"
    return "unresolved"


def verdict_of(kinds):
    """One table's verdict from the buckets its receivers fall in.

    The verdict names the channel that answers U40's question, in that order: a receiver this read
    cannot name is the residue and comes first; an array element is the arithmetic the one-level read
    reported as opaque; then a fixed `.data` object, a pointer field and a walked pointer. A table whose
    receivers are only the caller's own object, a frame slot or an allocation is a class constructed both
    as a member and as a temporary, which is a fact about its callers and not a gap.
    """
    if not kinds:
        return "no-caller"
    uniq = set(kinds) - {"null"}
    if not uniq:
        return "unresolved"
    for name in ("unresolved", "array-element", "static-object", "walked-pointer", "pointer-field",
                 "global-pointer"):
        if name in uniq:
            return name if name != "global-pointer" else "pointer-field"
    return "both-ways"


# --------------------------------------------------------------------------- image helpers
def objdump(blob, base):
    """[(va, text)] from objdump, an independent decoder over a byte window."""
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
        text = " ".join(rest.split("\t")[-1].split())
        if text and not text.startswith("."):
            insns.append((va, text))
    return insns


def sweep_imm_stores(text, text_va, wanted):
    """[(site, imm, base, disp, form)] for every `C7 /0` store of a wanted table.

    The immediate's offset is computed from the addressing form, because the forms differ by five bytes:
    `C7 05 <disp32> <imm32>` is ten, `C7 84 24 <disp32> <imm32>` eleven, `C7 44 24 <disp8> <imm32>`
    eight. Reading a `disp32` frame store at the `disp8` offset lands on the displacement and loses the
    table -- six stores in this image are of that form.
    """
    out = []
    i, n = 0, len(text)
    while i < n - 11:
        if text[i] != 0xC7:
            i += 1
            continue
        modrm = text[i + 1]
        mod, reg, rm = modrm >> 6, (modrm >> 3) & 7, modrm & 7
        if reg != 0 or mod == 3:
            i += 1
            continue
        if rm == 4:
            sib = text[i + 2]
            base_no, index = sib & 7, (sib >> 3) & 7
            disp_len = {0: 4 if base_no == 5 else 0, 1: 1, 2: 4}.get(mod)
            if disp_len is None:
                i += 1
                continue
            disp = 0
            if disp_len == 1:
                disp = struct.unpack_from("<b", text, i + 3)[0]
            elif disp_len == 4:
                disp = struct.unpack_from("<i", text, i + 3)[0]
            imm = struct.unpack_from("<I", text, i + 3 + disp_len)[0]
            base = REGS[base_no]
            if index != 4:
                form = "indexed"
            elif base in ("esp", "ebp"):
                form = "frame"
            else:
                form = "slot" if disp == 0 else "member"
            if imm in wanted:
                out.append((text_va + i, imm, base, disp, form))
            i += 3 + disp_len + 4
            continue
        if rm == 5 and mod == 0:
            disp = struct.unpack_from("<I", text, i + 2)[0]
            imm = struct.unpack_from("<I", text, i + 6)[0]
            if imm in wanted:
                out.append((text_va + i, imm, "abs", disp, "global"))
            i += 10
            continue
        disp_len = {0: 0, 1: 1, 2: 4}[mod]
        disp = 0
        if disp_len == 1:
            disp = struct.unpack_from("<b", text, i + 2)[0]
        elif disp_len == 4:
            disp = struct.unpack_from("<i", text, i + 2)[0]
        imm = struct.unpack_from("<I", text, i + 2 + disp_len)[0]
        form = "frame" if REGS[rm] in ("esp", "ebp") else ("slot" if disp == 0 else "member")
        if imm in wanted:
            out.append((text_va + i, imm, REGS[rm], disp, form))
        i += disp_len + 6
    return out


def tail_jumps(blob, base):
    """{target: [site]} for every `E9 rel32` in the window -- a tail call the `E8` census cannot see."""
    out = collections.defaultdict(list)
    for i in range(len(blob) - 5):
        if blob[i] == 0xE9:
            out[base + i + 5 + struct.unpack_from("<i", blob, i + 1)[0]].append(base + i)
    return out


def dword_sites(blob, base, addr):
    """The byte offsets in the window that hold `addr` as a dword."""
    needle = struct.pack("<I", addr)
    out, i = [], blob.find(needle)
    while i >= 0 and i + 4 <= len(blob):
        out.append(base + i)
        i = blob.find(needle, i + 1)
    return out


def reach_of(body, tails, sections, tables):
    """How an installer body nothing `E8`-calls is reached: a tail jump, a dword, a table slot, or not."""
    got = []
    for site in tails.get(body, [])[:2]:
        got.append("tail:%#x" % site)
    for name, blob, base in sections:
        for site in dword_sites(blob, base, body)[:3]:
            hit = None
            for tva, slots in tables.items():
                if tva <= site < tva + slots * 4:
                    hit = "slot:%#x+%d" % (tva, (site - tva) // 4)
                    break
            got.append(hit or "ptr:%s+%#x" % (name, site - base))
    return " ".join(got) if got else "none"


# --------------------------------------------------------------------------- the census
def harvest(rows, prov, by_va, calls_of, dis_of, body_name, gap_of, reach_of_body, reg_size):
    """One row per table of the two residues, from its store sites and the receivers read at every caller."""
    out = []
    for tva in sorted(by_va):
        p = prov[tva]
        table = by_va[tva]
        bodies = table["bodies"]
        chains = []
        for body in bodies:
            chains.extend(calls_of.get(body, []))
        kinds = [kind(c) for c in chains]
        arrays = sorted({c for c in chains if c.startswith("array[")})
        statics = sorted({c[7:] for c in chains if c.startswith("static:")})
        sizes = sorted({"%#x" % alloc_arg(c) for c in chains if c.startswith("new(") and alloc_arg(c)})
        reach = ""
        if not chains:
            reach = " ".join(r for r in (reach_of_body(b) for b in bodies) if r) or "none"
            if not bodies:
                reach = "gap:" + (gap_of.get(table["sites"][0][0], "?") if table["sites"] else "no-store")
        row = {
            "table_va": "%#010x" % tva,
            "class": p.get("class", ""),
            "population": p.get("population", ""),
            "resolution": p.get("resolution", ""),
            "sites": len(table["sites"]),
            "bodies": len(bodies),
            "installer": " ".join(body_name(b) for b in bodies) or (gap_of.get(table["sites"][0][0], "?")
                                                                   if table["sites"] else ""),
            "callers": len(chains),
            "verdict": verdict_of(kinds),
            "channels": " ".join(sorted(set(kinds))) or "",
            "receivers": " ".join(sorted(set(chains))[:6]),
            "array_heads": " ".join(arrays[:4]),
            "element_sizes": " ".join(sorted({c[6:c.index("]")] for c in chains
                                             if c.startswith("array[")})),
            "statics": " ".join(statics[:4]),
            "temporary_sizes": " ".join(sizes[:4]),
            "registry_size": "%#x" % reg_size[tva] if reg_size.get(tva) else "",
            "size_match": "",
            "reach": reach,
            "evidence": "%d store site(s); %d caller(s): %s" % (
                len(table["sites"]), len(chains), ",".join(sorted(set(chains))[:4]) or "-"),
        }
        if row["registry_size"] and row["temporary_sizes"]:
            row["size_match"] = ("agree" if int(row["registry_size"], 16) in
                                 [int(s, 16) for s in row["temporary_sizes"].split()] else "differ")
        out.append(row)
    return out


# --------------------------------------------------------------------------- rendering
def count(rows, pred):
    return sum(1 for r in rows if pred(r))


def render(rows):
    """The page block: a verdict table, then one reading per receiver channel."""
    mixed = [r for r in rows if r["resolution"] == "mixed"]
    nocall = [r for r in rows if r["resolution"] == "no-direct-call"]
    lines = ["| population | tables | an array element | a static object | a pointer field | a walked pointer "
             "| two ways (member + temporary) | unresolved | no receiver |",
             "|---|---|---|---|---|---|---|---|---|"]
    for label, sel in (("mixed", mixed), ("no direct caller", nocall)):
        cells = [count(sel, lambda r, v=v: r["verdict"] == v) for v in
                 ("array-element", "static-object", "pointer-field", "walked-pointer", "both-ways",
                  "unresolved", "no-caller")]
        lines.append("| %s | %d | %s |" % (label, len(sel), " | ".join(str(c) if c else "--" for c in cells)))
    lines.append("")

    arrays = [r for r in rows if r["verdict"] == "array-element"]
    statics = [r for r in rows if r["verdict"] == "static-object"]
    pointers = [r for r in rows if r["verdict"] == "pointer-field"]
    walked = [r for r in rows if r["verdict"] == "walked-pointer"]
    both = [r for r in rows if r["verdict"] == "both-ways"]
    unresolved = [r for r in rows if r["verdict"] == "unresolved"]

    text = ["\n".join(lines)]
    text.append("**%d of the %d mixed tables construct their object inside a dynamic array, and the element's "
                "size is in the stride.** The build's shape is `mov eax,[esi]` (the array's data pointer), "
                "`imul edi,edi,<size>` (or `shl edi,<n>`), `add edi,eax`, `mov ecx,edi`, `call <installer>` -- so "
                "`ecx` is the new element itself and the stride is the element's size: a struct of that size inside "
                "a `TArray`. The head is the pointer the data pointer was loaded from -- the caller's own object "
                "at `this+0`, a frame slot holding the `TArray`, or a global -- so the array is the caller's member "
                "or local, and the element is the object this table belongs to. The one-level read reported that "
                "receiver as one opaque `indexed` or `?`." % (len(arrays), len(mixed)))
    if arrays:
        text.append("")
        text.append("| table | class | element size | the array's head | installer |")
        text.append("|---|---|---|---|---|")
        for r in arrays:
            head = r["array_heads"].split()[0] if r["array_heads"] else "--"
            head = head.split("@", 1)[1] if "@" in head else head
            text.append("| %s | %s | %s | %s | %s |" % (
                r["table_va"], r["class"] or "--", r["element_sizes"] or "--", head,
                r["installer"].split()[0] if r["installer"] else "--"))
    text.append("")
    text.append("**%d are a fixed object in `.data` and %d a pointer loaded out of a field, so neither is an "
                "unnamed temporary.** `mov ecx,0x7ab94c` hands the installer the address of a module singleton: "
                "%s. A `mov ecx,[esi+0x1c]` is the pointer *stored* in that field rather than the field itself "
                "(the field itself is reached with `lea`, which is how a table installed into an embedded member "
                "reads), and %d tables are installed into an object a field points at.%s" % (
                    len(statics), len(pointers),
                    ", ".join("%s -> %s" % (r["table_va"], r["statics"].split()[0]) for r in statics[:6]),
                    len(pointers),
                    (" A pointer stepped by a constant (`add reg,0x10`, %d tables) is a loop over an array of "
                     "records and names no object either: %s." % (len(walked),
                                                                  ", ".join(r["table_va"] for r in walked[:6])))
                    if walked else ""))
    text.append("")
    agree = [r for r in rows if r["size_match"] == "agree"]
    differ = [r for r in rows if r["size_match"] == "differ"]
    text.append("**A temporary's size is the argument the allocating call is handed, and on the control it is the "
                "class's registered `object_size`.** The receiver of an installation into a fresh object is read "
                "as `new(<arg>:<callee>)` -- the immediate pushed before the call that returns it -- and joined "
                "against `inventory/class_registrations.csv` it agrees on **%d and differs on %d** of the tables "
                "whose class a registration names, so the argument is a size. The %d tables constructed both as a "
                "member and as a temporary are a fact about their callers, not a residue, and the size is what "
                "separates two classes with the same shape: %s%s." % (
                    len(agree), len(differ), len(both),
                    ", ".join("%s %s%s" % (r["table_va"], r["temporary_sizes"].split()[0],
                                           "" if r["size_match"] != "differ" else " (registered %s)"
                                           % r["registry_size"])
                              for r in [x for x in both if x["temporary_sizes"]][:8]),
                    (" and %d more" % (len([x for x in both if x["temporary_sizes"]]) - 8))
                    if len([x for x in both if x["temporary_sizes"]]) > 8 else ""))
    text.append("")
    reached = [r for r in nocall if r["reach"] and r["reach"] != "none"]
    by_kind = {}
    for r in nocall:
        for tok in (r["reach"] or "").split():
            by_kind.setdefault(tok.split(":")[0], []).append("%s %s" % (r["table_va"], tok))
    kinds = [k for k in ("slot", "tail", "ptr", "gap") if by_kind.get(k)]
    text.append("**The %d tables no `E8` reaches have a channel, and it names the reader.** An installer is "
                "reached by a tail jump (`E9`), by a dword that holds its address -- a class-table slot, or a "
                "`.data` pointer -- or by nothing the image carries. %d of %d have a channel; %d are pointed at by "
                "nothing at all, which makes them tables whose class no shipped code instantiates.%s" % (
                    len(nocall), len(reached), len(nocall), len(nocall) - len(reached),
                    (" By channel: %s." % "; ".join(
                        "%d by a %s (%s)" % (len(by_kind[k]), {"slot": "class-table slot", "tail": "tail jump",
                                                               "ptr": "dword pointer", "gap": "gap head"}[k],
                                             ", ".join(sorted(set(by_kind[k]))[:4]))
                        for k in kinds)) if kinds else ""))
    if unresolved:
        text.append("")
        text.append("**%d stay unresolved** (%s): the instruction that defines the receiver is not a copy, an "
                    "address, a load or a scaled add, and the artifact names its mnemonic." % (
                        len(unresolved), ", ".join(r["table_va"] for r in unresolved[:8])))
    return "\n".join(text)


def rows_for_csv(rows):
    return [{k: r.get(k, "") for k in COLUMNS} for r in rows]


def write_artifact(rows, path):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows_for_csv(rows):
            w.writerow(r)


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as fh:
        return [{k: (v if v is not None else "") for k, v in r.items()} for r in csv.DictReader(fh)]


def splice_doc(path, block):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(DOC_START), text.find(DOC_END)
    if i < 0 or j < 0:
        return None
    return text[:i + len(DOC_START)] + "\n" + block + "\n" + text[j:]


# --------------------------------------------------------------------------- self-test
def self_check():
    """Synthetic instructions: every arithmetic and load form, the verdicts, the reach scans and the gap join."""
    bad = []
    seen = []

    def want(name, got, expected):
        seen.append(name)
        if got != expected:
            bad.append("%s: %r != %r" % (name, got, expected))

    # --- an element of a dynamic array: the TArray::Add-then-construct shape
    body = [(0x1000, "mov esi,ecx"), (0x1002, "mov ecx,esi"), (0x1004, "call 0x55ed0"),
            (0x1009, "mov eax,DWORD PTR [esi]"), (0x100b, "imul edi,edi,0x1c"),
            (0x100e, "add edi,eax"), (0x1010, "je 0x1030"), (0x1012, "mov ecx,edi"),
            (0x1014, "call 0x179860")]
    want("array element", deep(body, 8, "ecx", this_register(body)), "array[0x1c]@[this+0x0]")
    want("array element bucket", kind("array[0x1c]@[this+0x0]"), "array-element")
    want("array head", deep(body, 8, "edi", this_register(body)), "array[0x1c]@[this+0x0]")

    # --- a `shl` stride and a `lea` with a scaled index
    body2 = [(0x2000, "mov esi,ecx"), (0x2002, "mov eax,DWORD PTR [esi]"), (0x2004, "shl edi,0x5"),
             (0x2007, "add edi,eax"), (0x2009, "mov ecx,edi"), (0x200b, "call 0x1234")]
    want("shl stride", deep(body2, 5, "ecx", "esi"), "array[0x20]@[this+0x0]")
    body3 = [(0x3000, "mov esi,ecx"), (0x3002, "mov eax,DWORD PTR [esi]"), (0x3004, "lea ecx,[eax+edi*4]"),
             (0x3008, "call 0x1234")]
    want("lea with an index", deep(body3, 3, "ecx", "esi"), "array[0x4]@[this+0x0]")

    # --- a fixed object, a pointer field, an embedded member and an allocation's return
    want("a fixed object", deep([(0x4000, "mov ecx,0x7ab94c")], 1, "ecx", ""), "static:0x7ab94c")
    want("the pointer stored in a field", deep([(0x5000, "mov esi,ecx"), (0x5002, "mov ecx,DWORD PTR [esi+0x1c]")],
                                               2, "ecx", "esi"), "[this+0x1c]")
    want("an embedded member", deep([(0x6000, "mov esi,ecx"), (0x6002, "lea ecx,[esi+0x60]")], 2, "ecx", "esi"),
         "this+0x60")
    alloc = [(0x7000, "push 0x8e7c"), (0x7005, "sbb edi,edi"), (0x7007, "neg edi"),
             (0x7009, "call DWORD PTR [eax]"), (0x700b, "mov ecx,eax"), (0x700d, "call 0x1234")]
    want("an allocation's return", deep(alloc, 4, "ecx", ""), "new(0x8e7c:DWORD PTR [eax])")  # mov ecx,eax
    want("the allocation bucket", kind("new(0x8e7c:DWORD PTR [eax])"), "allocation")

    # --- a pointer stepped by a constant, and a frame slot
    want("a walked pointer", deep([(0x8000, "mov esi,ecx"), (0x8002, "mov edi,DWORD PTR [esi+0x14]"),
                                   (0x8005, "add edi,0x10"), (0x8008, "mov ecx,edi")], 4, "ecx", "esi"),
         "walk([this+0x14]+0x10)")
    want("a frame slot", deep([(0x9000, "lea ecx,[esp+0x28]")], 1, "ecx", ""), "esp+0x28")

    # --- the verdicts
    want("two ways", verdict_of(["own-object", "allocation"]), "both-ways")
    want("one channel", verdict_of(["array-element", "array-element"]), "array-element")
    want("the channel answers first", verdict_of(["array-element", "static-object"]), "array-element")
    want("no caller", verdict_of([]), "no-caller")
    want("unresolved", verdict_of(["unresolved"]), "unresolved")
    want("a null receiver is unresolved", verdict_of(["null"]), "unresolved")

    # --- the ten-byte absolute store, read whole
    text = bytearray(b"\x90" * 0x40)
    text[0x10:0x1a] = b"\xc7\x05" + struct.pack("<I", 0x7ab9e8) + struct.pack("<I", 0x9000)
    got = sweep_imm_stores(bytes(text), 0x1000, {0x9000})
    want("an absolute store", [(f, "%#x" % d) for _, _, _, d, f in got], [("global", "0x7ab9e8")])

    # --- a `disp32` frame store is eleven bytes, not the eight a `disp8` store takes
    frame = bytearray(b"\x90" * 0x40)
    frame[0x08:0x13] = b"\xc7\x84\x24" + struct.pack("<I", 0x310) + struct.pack("<I", 0x9100)
    got = sweep_imm_stores(bytes(frame), 0x1000, {0x9100})
    want("a disp32 frame store", [(f, "%#x" % d) for _, _, _, d, f in got], [("frame", "0x310")])
    want("a disp8 frame store is read at its own length",
         [(f, "%#x" % d) for _, _, _, d, f in sweep_imm_stores(
             b"\x90" * 4 + b"\xc7\x44\x24\x14" + struct.pack("<I", 0x9200) + b"\x90" * 8, 0x1000, {0x9200})],
         [("frame", "0x14")])
    want("an indexed store", [f for _, _, _, _, f in sweep_imm_stores(
        b"\x90" * 4 + b"\xc7\x04\x8e" + struct.pack("<I", 0x9300) + b"\x90" * 8, 0x1000, {0x9300})], ["indexed"])

    # --- the reach scans: a tail jump and a dword pointer, one of them into a table slot
    win = bytearray(b"\x90" * 0x60)
    win[0x10:0x15] = b"\xe9" + struct.pack("<i", 0x2000 - (0x1000 + 0x10 + 5))
    win[0x20:0x24] = struct.pack("<I", 0x2000)
    win[0x30:0x34] = struct.pack("<I", 0x2000)
    tails = tail_jumps(bytes(win), 0x1000)
    want("a tail jump to the body", sorted(tails.get(0x2000, [])), [0x1010])
    want("a dword that holds the body", dword_sites(bytes(win), 0x1000, 0x2000), [0x1020, 0x1030])
    want("a reach string", reach_of(0x2000, tails, [("text", bytes(win), 0x1000)], {0x1030: 4}),
         "tail:0x1010 ptr:text+0x20 slot:0x1030+0")
    want("an unreached body", reach_of(0x3000, tails, [("text", bytes(win), 0x1000)], {}), "none")

    # --- the pushed argument and the gap join
    want("the pushed size", pushed_arg([(0x1, "push 0x24"), (0x6, "call DWORD PTR [eax]")], 1), 0x24)
    want("a flag is not a size", alloc_arg(call_return([(0x1, "push 0xffffffff"),
                                                        (0x6, "call 0x1f75c0")], 1)), 0)
    want("a size argument", alloc_arg("new(0x4fb4:0x181d0)"), 0x4fb4)
    want("no pushed size", pushed_arg([(0x1, "call 0x1234")], 0), 0)
    want("a helper chain", deref("this", 0x1c), "[this+0x1c]")
    want("a walked chain", walk("this+0x10", 0x8), "walk(this+0x10+0x8)")

    if bad:
        for b in bad:
            print("FAIL class-receiver-reads: %s" % b)
        return 1
    print("ok   class-receiver-reads: %d assertions on synthetic instructions, no dump, nothing written"
          % len(seen))
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

    if args.check_doc or ((args.report or args.write_doc) and not args.xbe):
        rows = read_csv(SRC)
        if not rows:
            print("FAIL class-receiver-reads: %s is missing" % SRC)
            return 1
        for r in rows:
            for k in ("sites", "bodies", "callers"):
                r[k] = int(r[k] or 0)
        if args.check_doc:
            text = open(args.check_doc, encoding="utf-8").read()
            i, j = text.find(DOC_START), text.find(DOC_END)
            if i < 0 or j < 0:
                print("FAIL class-receiver-reads: %s has no generated block" % args.check_doc)
                return 1
            if text[i + len(DOC_START):j].strip() != render(rows).strip():
                print("FAIL class-receiver-reads: %s is stale; re-run --write-doc" % args.check_doc)
                return 1
            print("ok   class-receiver-reads: the page block matches the committed artifact")
            return 0
        if args.write_doc:
            spliced = splice_doc(args.write_doc, render(rows))
            if spliced is None:
                print("FAIL class-receiver-reads: %s has no generated block" % args.write_doc)
                return 1
            open(args.write_doc, "w", encoding="utf-8").write(spliced)
            print("ok   class-receiver-reads: wrote %s from the committed artifact" % args.write_doc)
            return 0
        print(render(rows))
        counts = collections.Counter(r["verdict"] for r in rows)
        print("tables=%d %s" % (len(rows), " ".join("%s=%d" % (k, counts[k]) for k in VERDICTS if counts[k])))
        return 0

    if not args.xbe:
        print("FAIL class-receiver-reads: --xbe is required to read the image")
        return 1

    import xbe as xbemod
    image = xbemod.Xbe(args.xbe)
    code = next(s for s in image.sections if s.name == ".text")
    blob = image.data[code.foff:code.foff + min(code.fsize, code.vsize)]
    sections = [(s.name, image.data[s.foff:s.foff + min(s.fsize, s.vsize)], s.va)
                for s in image.sections if s.name != ".text"]

    prov = {int(r["table_va"], 16): r for r in read_csv(PROV)}
    reach_rows = {int(r["table_va"], 16): int(r["table_slots"] or 0) for r in read_csv(REACH)}
    reg_size = {}
    for r in read_csv(REG):
        try:
            tva = int(r["table_va"], 16)
            size = int(r["object_size"] or 0)
        except ValueError:
            continue
        if tva and size:
            reg_size.setdefault(tva, size)

    funcs = read_csv(FUNCS)
    sizes = {int(r["address"], 16): int(r["size"] or 0) for r in funcs}
    fnames = {int(r["address"], 16): r["name"] for r in funcs}
    starts = sorted(a for a, n in sizes.items() if n)
    gaps = []
    for r in read_csv(GAPS):
        try:
            gaps.append((int(r["head_va"], 16), int(r["size"] or 0), r.get("verdict", ""),
                         (r.get("name") or "").strip()))
        except ValueError:
            continue
    gaps.sort()

    def body_of(site):
        i = bisect.bisect_right(starts, site) - 1
        while i >= 0 and site - starts[i] <= 0x4000:
            a = starts[i]
            if a <= site < a + sizes[a]:
                return a
            i -= 1
        return 0

    def body_name(body):
        return fnames.get(body) or "%#x" % body

    def gap_of(site):
        for head, size, verdict, name in gaps:
            if head <= site < head + max(size, 1):
                return "%#x:%s:%s" % (head, verdict, name or "unnamed")
        return "?"

    # the two residues, and every table's store sites read again
    wanted = {tva: p for tva, p in prov.items() if p["resolution"] in ("mixed", "no-direct-call")}
    sites = sweep_imm_stores(blob, code.va, set(wanted))
    tables = {}
    for tva in wanted:
        mine = [s for s in sites if s[1] == tva]
        bodies = []
        for site, _imm, _base, _disp, _form in mine:
            b = body_of(site)
            if b and b not in bodies:
                bodies.append(b)
        tables[tva] = {"sites": mine, "bodies": sorted(bodies)}

    calls_into = collections.defaultdict(list)
    for i in range(len(blob) - 5):
        if blob[i] != 0xE8:
            continue
        target = code.va + i + 5 + struct.unpack_from("<i", blob, i + 1)[0]
        calls_into[target].append(code.va + i)
    tails = tail_jumps(blob, code.va)

    body_cache = {}

    def dis(body):
        if body in body_cache:
            return body_cache[body]
        n = sizes.get(body) or 0
        if not n:
            i = bisect.bisect_right(starts, body)
            n = min((starts[i] - body) if i < len(starts) else 0x200, 0x400) or 0x100
        insns = objdump(blob[body - code.va:body - code.va + n], body)
        body_cache[body] = insns
        return insns

    calls_of = {}
    for body in {b for t in tables.values() for b in t["bodies"]}:
        chains = []
        for site in calls_into.get(body, []):
            caller = body_of(site)
            if not caller:
                continue
            insns = dis(caller)
            idx = next((i for i, (va, _) in enumerate(insns) if va == site), None)
            if idx is None:
                chains.append("?undecoded")
                continue
            chains.append(deep(insns, idx, "ecx", this_register(insns)))
        calls_of[body] = chains

    def reach_of_body(body):
        return reach_of(body, tails, sections, reach_rows)

    rows = harvest(wanted, prov, tables, calls_of, dis, body_name, gap_of, reach_of_body, reg_size)
    if args.out:
        write_artifact(rows, os.path.join(args.out, os.path.basename(OUT)))
    if args.write_doc:
        spliced = splice_doc(args.write_doc, render(rows))
        if spliced is None:
            print("FAIL class-receiver-reads: %s has no generated block" % args.write_doc)
            return 1
        open(args.write_doc, "w", encoding="utf-8").write(spliced)
        print("ok   class-receiver-reads: wrote %s" % args.write_doc)
    if args.report or not (args.out or args.write_doc):
        print(render(rows))
    counts = collections.Counter(r["verdict"] for r in rows)
    print("tables=%d %s" % (len(rows), " ".join("%s=%d" % (k, counts[k]) for k in VERDICTS if counts[k])))
    return 0


if __name__ == "__main__":
    sys.exit(main())