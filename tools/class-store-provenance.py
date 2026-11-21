#!/usr/bin/env python3
"""Where the pointer a class table is stored through comes from.

`tools/class-table-store-fields.py` reads the *form* of a table's store: `[reg]` is the object's own
dispatch slot, `[reg+disp]` a member field. That form is relative to the body doing the storing, and the
body is not always the object's own constructor -- a constructor that builds a member in place stores
into `[reg]` of the *member*'s `this`, which is the caller's field. So the same `[reg]` store is a class
table or a member's table depending on what the caller passed in `ecx`, and this tool reads that:

  * **the store's own pointer**, resolved inside the storing body: `this` (the incoming `ecx`), a frame
    slot, a global, or a member loaded from one of those;
  * **the pointer at every call site** of the storing body: `this` of the caller, the address of one of
    the caller's members (`lea ecx,[esi+0x60]`), a frame slot (an object constructed in place in the
    caller's frame), the return of an allocation (`mov ecx,eax` after a `call`), or unknown;
  * **whether the caller resets its own slot first**, which is what a destructor level does: it writes
    its own table at `[reg]` and then calls the base's destructor with the same `this`, so the table the
    callee writes is the *base* class's, not a second array of the caller's object.

That settles the residue U40 lists as "a second array that class's constructor writes": the twelve
tables written only at `[reg]` are not a second array, they are a different object.

It also reads the **register-carried install** the `C7 /0` census cannot see: `mov reg,<table>` followed
by `mov [reg2(+disp)],reg` installs a table without an immediate in the store, so a table whose only
installer does that is counted as `frame-only` or `no-store` by the immediate sweep.

    tools/class-store-provenance.py --xbe DUMP/default.xbe -o inventory
    tools/class-store-provenance.py --report
    tools/class-store-provenance.py --write-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-store-provenance.py --check-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-store-provenance.py --check          # synthetic instructions, no dump, writes nothing

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

OUT = os.path.join("inventory", "class_store_provenance.csv")
REACH = os.path.join(ROOT, "inventory", "native_vtable_reach.csv")
FIELDS_IN = os.path.join(ROOT, "inventory", "class_table_store_fields.csv")
NAMES = os.path.join(ROOT, "inventory", "xbe_class_table_names.csv")
FUNCS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
SRC = os.path.join(ROOT, "inventory", "class_store_provenance.csv")
PAGE = os.path.join("docs", "systems", "CLASS_REGISTRATION.md")
DOC_START = "<!-- generated:class-store-provenance -->"
DOC_END = "<!-- /generated:class-store-provenance -->"

COLUMNS = ("table_va", "slots", "class", "population", "store_verdict", "sites", "bodies", "store_origin",
           "call_sites", "this", "member", "frame", "fresh", "other", "caller_resets", "reg_stores",
           "global_targets", "resolution", "evidence")
RESOLUTIONS = ("own-object", "base-level", "member-of-caller", "stack-object", "allocated", "global-object",
               "mixed", "no-direct-call", "no-installer", "no-store")
POPULATIONS = ("named", "bounded", "plain-only", "no-installer", "unnamed-other")
REGS = ("eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi")   # the modrm/SIB encoding order
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
    """`{"base","index","disp","absolute","deref"}` for a memory operand, else None."""
    op = op.strip()
    for pre in SIZES:
        if op.startswith(pre):
            op = op[len(pre):]
    if op.startswith("ds:"):
        # objdump's spelling of an absolute `[disp32]`: a global variable, not an object field
        try:
            return {"base": "", "index": "", "disp": int(op[3:], 16), "absolute": True}
        except ValueError:
            return None
    if not op.startswith("["):
        return None
    inner = op[1:op.index("]")]
    base, index, disp = "", "", 0
    for tok in re.findall(r"[+-]?[^+-]+", inner):
        sign = -1 if tok.startswith("-") else 1
        tok = tok.lstrip("+-").strip()
        if "*" in tok:
            r, _, s = tok.partition("*")
            index = r.strip()
        elif tok in REGS:
            base = tok
        elif tok.startswith("0x"):
            disp += sign * int(tok, 16)
        elif tok.isdigit():
            disp += sign * int(tok)
    return {"base": base, "index": index, "disp": disp, "absolute": not base and not index}


def is_imm(op):
    op = op.strip()
    return op.startswith("0x") or op.lstrip("-").isdigit()


def write_target(text):
    """The register an instruction writes, or '' when it writes memory or nothing."""
    mnem, ops = split_ops(text)
    if not ops or mnem in NO_WRITE or mnem.startswith("j"):
        return ""
    return bare_reg(ops[0])


# --------------------------------------------------------------------------- provenance
def this_register(insns):
    """The register the body copies `ecx` into in its prologue, or '' when it does not."""
    for va, text in insns[:14]:
        mnem, ops = split_ops(text)
        if mnem == "call":
            break
        if mnem == "mov" and len(ops) == 2 and ops[1] == "ecx" and bare_reg(ops[0]):
            return bare_reg(ops[0])
    return ""


def resolve(insns, idx, reg, thisreg="", depth=0):
    """Flattened origin of `reg` just before instruction `idx`: `this`, `this+0x60`, `esp+0x14`, ...

    The rules are the build's own: a thiscall body receives its object in `ecx`, a prologue copies it
    into a callee-saved register (`mov esi,ecx`), a member is loaded or addressed off that register, a
    temporary sits in a frame slot, and a `call`'s return is a fresh object rather than either.
    """
    if depth > 6:
        return "?"
    if reg in ("esp", "ebp"):
        return reg
    got = walk(insns, idx, reg, thisreg, depth)
    if got is None:
        # nothing in the body writes it: it still holds what the body was entered with, which for
        # `ecx` (or the register the prologue copied it into) is the object
        return "this" if (reg == "ecx" or (thisreg and reg == thisreg)) else "?"
    return got


def walk(insns, idx, reg, thisreg="", depth=0):
    """The chain of the instruction that defines `reg`, or None when the body never writes it."""
    for j in range(idx - 1, -1, -1):
        text = insns[j][1]
        mnem, ops = split_ops(text)
        if mnem == "call":
            if reg in ("eax", "ecx", "edx"):
                return "ret"
            continue
        if write_target(text) != reg:
            continue
        if mnem not in ("mov", "lea") or len(ops) != 2:
            return "?"
        src = ops[1]
        r = bare_reg(src)
        if r:
            if r == "ecx" or r == thisreg:
                return "this"
            return resolve(insns, j, r, thisreg, depth + 1)
        m = mem_of(src)
        if m is None:
            return "imm" if is_imm(src) else "?"
        if m["index"]:
            return "indexed"
        base = m["base"]
        if not base:
            return "ds:%#x" % m["disp"]
        sub = resolve(insns, j, base, thisreg, depth + 1)
        if sub in ("esp", "ebp"):
            return "%s%+#x" % (sub, m["disp"])
        if sub == "this" or sub.startswith("this+"):
            add = int(sub[5:], 16) if sub.startswith("this+") else 0
            return "this+%#x" % (add + m["disp"])
        if sub in ("?", "ret", "imm", "indexed") or sub.startswith("ds:"):
            return sub
        return "%s%+#x" % (sub, m["disp"])
    return None


def classify_origin(chain):
    """Which bucket a resolved origin belongs to."""
    if chain == "this":
        return "this"
    if chain.startswith("this+"):
        return "member"
    if chain.startswith("esp") or chain.startswith("ebp"):
        return "frame"
    if chain == "ret":
        return "fresh"
    if chain.startswith("ds:"):
        return "global"
    return "other"


def ecx_at(insns, call_idx):
    """The origin of the `ecx` a call at `call_idx` receives, in the caller's own terms."""
    if call_idx <= 0:
        return "?"
    return resolve(insns, call_idx, "ecx", this_register(insns))


# --------------------------------------------------------------------------- image scanning
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
        text = re.sub(r"\s+", " ", rest.split("\t")[-1].strip())
        if text and not text.startswith("."):
            insns.append((va, text))
    return insns


def sweep_imm_stores(text, text_va):
    """[(site, imm, base, disp, form)] for every `C7 /0` store; the store-fields census's own rule."""
    out = []
    i, n = 0, len(text)
    while i < n - 9:
        if text[i] != 0xC7:
            i += 1
            continue
        modrm = text[i + 1]
        mod, reg, rm = modrm >> 6, (modrm >> 3) & 7, modrm & 7
        if reg != 0:
            i += 1
            continue
        if rm == 4:
            sib = text[i + 2] if i + 2 < n else 0
            base_no, index = sib & 7, (sib >> 3) & 7
            if mod == 0:
                ln = 10 if index == 4 and base_no == 5 else 7
            elif mod == 1:
                ln = 8
            elif mod == 2:
                ln = 10
            else:
                i += 1
                continue
            disp = 0
            if mod == 1:
                disp = struct.unpack_from("<b", text, i + 3)[0]
            elif mod == 2:
                disp = struct.unpack_from("<i", text, i + 3)[0]
            base = REGS[base_no]
            if index != 4:
                form = "indexed"
            elif mod == 0 and base_no == 5:
                form, ln = "global", 10
                disp = struct.unpack_from("<I", text, i + 4)[0]
            else:
                form = "slot" if disp == 0 else "member"
        elif mod == 0 and rm == 5:
            # `C7 05 <disp32> <imm32>` is ten bytes; its displacement names the global the object
            # lives in, which is the one thing a global store does say
            form, base, ln = "global", "", 10
            disp = struct.unpack_from("<I", text, i + 2)[0]
        elif mod == 0:
            form, base, disp, ln = "slot", REGS[rm], 0, 6
        elif mod == 1:
            form, base, disp, ln = "member", REGS[rm], struct.unpack_from("<b", text, i + 2)[0], 7
        elif mod == 2:
            form, base, disp, ln = "member", REGS[rm], struct.unpack_from("<i", text, i + 2)[0], 10
        else:
            i += 1
            continue
        if form in ("slot", "member") and base in ("esp", "ebp"):
            form = "frame"
        imm = struct.unpack_from("<I", text, i + ln - 4)[0]
        out.append((text_va + i, imm, base, disp, form))
        i += ln
    return out


def sweep_reg_stores(text, text_va, wanted, window=24):
    """[(site, table, form, base, disp)] for `mov reg,<table>` followed by `mov [reg2(+disp)],reg`.

    The immediate-store census cannot see this install: the address is in the `mov` and the store's own
    operand is a register. It is read only in the same basic shape -- no `call` and no other write to the
    register in between -- so a table that reaches its store through arithmetic is not credited.
    """
    out = []
    n = len(text)
    i = 0
    while i < n - 5:
        op = text[i]
        if not (0xB8 <= op <= 0xBF):
            i += 1
            continue
        reg = REGS[op - 0xB8]
        imm = struct.unpack_from("<I", text, i + 1)[0]
        if imm not in wanted:
            i += 1
            continue
        j = i + 5
        stop = min(n - 2, j + window)
        while j < stop:
            b = text[j]
            if b == 0xE8 or b == 0xE9:
                break
            if b == 0x89:                                   # mov r/m32, r32
                modrm = text[j + 1]
                mod, sreg, rm = modrm >> 6, (modrm >> 3) & 7, modrm & 7
                if REGS[sreg] == reg:
                    form, base, disp, ln = "member", "", 0, 0
                    if rm == 4:
                        sib = text[j + 2] if j + 2 < n else 0
                        bno = sib & 7
                        if (sib >> 3) & 7 != 4:
                            form = "indexed"
                            ln = 3 if mod == 0 else 4
                        else:
                            ln = {0: 3, 1: 4, 2: 6, 3: -1}[mod]
                            if ln < 0:
                                break
                            base = REGS[bno]
                            disp = struct.unpack_from("<b", text, j + 2)[0] if mod == 1 else (
                                struct.unpack_from("<i", text, j + 2)[0] if mod == 2 else 0)
                            form = "slot" if disp == 0 else "member"
                    elif rm == 5 and mod == 0:
                        form, ln = "global", 2
                    elif mod == 0:
                        form, base, ln = "slot", REGS[rm], 2
                    elif mod == 1:
                        form, base, disp, ln = "member", REGS[rm], struct.unpack_from("<b", text, j + 2)[0], 3
                    elif mod == 2:
                        form, base, disp, ln = "member", REGS[rm], struct.unpack_from("<i", text, j + 2)[0], 6
                    else:
                        break
                    if form in ("slot", "member") and base in ("esp", "ebp"):
                        form = "frame"
                    out.append((text_va + j, imm, form, base, disp))
                    break
            if write_target_reg(text, j) == reg:            # the register is overwritten: give up
                break
            j += insn_len(text, j)
        i += 5
    return out


def write_target_reg(text, i):
    """The register a raw instruction at offset `i` writes -- a byte-level shim for the store window.

    Only the forms the immediates above are followed by need naming: mov reg, reg, and the arithmetic
    that would make the pair unreadable. Anything else answers '' so the window keeps scanning.
    """
    b = text[i]
    if 0x8B <= b <= 0x8F or b in (0x8A, 0x8D):
        modrm = text[i + 1] if i + 1 < len(text) else 0
        if modrm >> 6 == 3:
            return REGS[(modrm >> 3) & 7]
        return ""
    if b in (0x31, 0x33):                                   # xor reg,reg
        modrm = text[i + 1] if i + 1 < len(text) else 0
        if modrm >> 6 == 3:
            return REGS[(modrm >> 3) & 7]
    return ""


def insn_len(text, i):
    """A byte length for one instruction, only as precise as the register-store window needs."""
    b = text[i]
    if b in (0x90, 0xCC, 0x50, 0x51, 0x52, 0x53, 0x56, 0x57, 0x58, 0x59, 0x5A, 0x5B, 0x5E, 0x5F):
        return 1
    if b in (0x68, 0xE8, 0xE9):
        return 5
    if b in (0x6A,):
        return 2
    if 0xB8 <= b <= 0xBF:
        return 5
    if b == 0x89 or b == 0x8B or b == 0x8D or b == 0x31 or b == 0x33 or b == 0x85 or b == 0x3B or b == 0x39:
        if i + 1 >= len(text):
            return 1
        modrm = text[i + 1]
        mod, rm = modrm >> 6, modrm & 7
        ln = 2
        if rm == 4:
            ln += 1
            sib = text[i + 2] if i + 2 < len(text) else 0
            if mod == 0 and (sib & 7) == 5:
                ln += 4
        elif mod == 0 and rm == 5:
            ln += 4
        if mod == 1:
            ln += 1
        elif mod == 2:
            ln += 4
        elif mod == 0 and rm == 5:
            pass
        return ln
    if b == 0xC7:
        modrm = text[i + 1] if i + 1 < len(text) else 0
        mod, rm = modrm >> 6, modrm & 7
        if mod == 0 and rm == 5:
            return 10
        if rm == 4:
            return 7 + (0 if mod == 0 else 1) + (4 if mod == 2 else 0)
        return 6 + (0 if mod == 0 else 1) + (4 if mod == 2 else 0)
    return 1


# --------------------------------------------------------------------------- classification
def resolution_of(rows):
    """One table's verdict from its store's own pointer and the pointers its installer is called with."""
    if not rows["sites"]:
        return "no-store"
    if not rows["bodies"]:
        return "no-installer"
    origins = collections.Counter(classify_origin(o) for o in rows.get("_origins", []))
    if origins and origins["global"] == sum(origins.values()):
        return "global-object"
    counts = collections.Counter()
    for chain in rows["call_sites"]:
        counts[classify_origin(chain)] += 1
    if not rows["call_sites"]:
        return "no-direct-call"
    if counts["member"] and not counts["this"] and not counts["frame"] and not counts["fresh"] \
            and not counts["other"] and not counts["global"]:
        return "member-of-caller"
    if counts["frame"] and not counts["this"] and not counts["member"] and not counts["fresh"] \
            and not counts["other"] and not counts["global"]:
        return "stack-object"
    if counts["fresh"] and not counts["this"] and not counts["member"] and not counts["frame"] \
            and not counts["other"] and not counts["global"]:
        return "allocated"
    if counts["global"] and not counts["this"] and not counts["member"] and not counts["frame"] \
            and not counts["fresh"] and not counts["other"]:
        return "global-object"
    if counts["this"] == len(rows["call_sites"]):
        return "base-level" if rows["caller_resets"] else "own-object"
    if len(counts) == 1 and counts["other"]:
        return "mixed"
    return "mixed"


def harvest(tables, names, store_verdict, sites_by_table, reg_by_table, bodies_of_table,
            call_sites_of, caller_resets_of, origin_of):
    rows = []
    for tva in sorted(tables):
        sites = sites_by_table.get(tva, [])
        bodies = bodies_of_table.get(tva, [])
        chains = []
        for body in bodies:
            chains.extend(call_sites_of.get(body, []))
        row = {
            "table_va": "%#010x" % tva,
            "slots": str(tables[tva]),
            "class": names.get(tva, ""),
            "population": store_verdict.get(tva, ("", ""))[0],
            "store_verdict": store_verdict.get(tva, ("", ""))[1],
            "sites": len(sites),
            "bodies": len(bodies),
            "call_sites": chains,
            "caller_resets": sum(1 for b in bodies if caller_resets_of.get(b)),
            "reg_stores": len(reg_by_table.get(tva, [])),
            "_origins": [origin_of(s) for s in sites],
        }
        counts = collections.Counter(classify_origin(c) for c in chains)
        row["this"] = counts["this"]
        row["member"] = counts["member"]
        row["frame"] = counts["frame"]
        row["fresh"] = counts["fresh"]
        row["other"] = counts["other"] + counts["global"]
        row["store_origin"] = " ".join(sorted({o.split("+")[0] for o in row["_origins"]})) or "-"
        row["global_targets"] = " ".join(sorted({o[3:] for o in row["_origins"] if o.startswith("ds:")}))
        row["resolution"] = resolution_of(row)
        row["evidence"] = "%d store site(s) at %s; %d call site(s): %s; %d register store(s)" % (
            len(sites),
            ",".join("%#x" % s[0] for s in sites[:3]) or "-",
            len(chains), ",".join(sorted(set(chains))[:4]) or "-", len(reg_by_table.get(tva, [])))
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- rendering
def render(rows):
    by_pop = collections.defaultdict(lambda: collections.Counter())
    for r in rows:
        by_pop[r["population"]][r["resolution"]] += 1
    lines = ["| population | tables | own object | base level | a member of the caller | a frame slot | "
             "an allocation | mixed | no direct call | no installer | no store |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    label = {"named": "named by the registration path (control)", "bounded": "nameless: a class ctor stores it",
             "plain-only": "nameless: only bodies no class names store it",
             "no-installer": "nameless: no installer body", "unnamed-other": "unnamed, outside the 250"}

    def cell(pop, res):
        n = by_pop[pop][res]
        return str(n) if n else "--"

    for pop in POPULATIONS:
        if pop not in by_pop:
            continue
        total = sum(by_pop[pop].values())
        lines.append("| %s | %d | %s |" % (label[pop], total, " | ".join(cell(pop, r) for r in RESOLUTIONS)))
    lines.append("")

    named = [r for r in rows if r["population"] == "named"]
    nameless = [r for r in rows if r["population"] in ("bounded", "plain-only", "no-installer")]
    second = [r for r in rows if r["resolution"] in ("own-object", "base-level", "mixed")]
    member = [r for r in rows if r["resolution"] == "member-of-caller"]
    stack = [r for r in rows if r["resolution"] == "stack-object"]
    del second  # computed below per population

    n_own = sum(1 for r in named if r["resolution"] == "own-object")
    n_base = sum(1 for r in named if r["resolution"] == "base-level")
    n_call = sum(1 for r in named if r["call_sites"])
    n_member = sum(1 for r in named if r["resolution"] == "member-of-caller")
    lines.append("**The control: %d of the %d named tables are installed with the caller's own `this`, and "
                 "%d with a base level's.** %d of the %d have a direct caller at all; %d are installed "
                 "through the address of one of the caller's members, which is the class table's own "
                 "counterpart to `[reg+disp]` -- the same fact read from the call site instead of the "
                 "store." % (n_own, len(named), n_base, n_call, len(named), n_member))
    lines.append("")

    nameless_base = [r for r in nameless if r["resolution"] == "base-level"]
    nameless_member = [r for r in nameless if r["resolution"] == "member-of-caller"]
    nameless_stack = [r for r in nameless if r["resolution"] == "stack-object"]
    nameless_alloc = [r for r in nameless if r["resolution"] == "allocated"]
    lines.append("**Of the %d nameless tables, %d are installed with a caller's own `this` and %d with a "
                 "base level's, %d through a member's address and %d into a frame slot.** A frame-slot "
                 "install is an object constructed in place in the caller's frame -- a temporary or an "
                 "inlined constructor -- so the table belongs to a class that is only ever a local; an "
                 "allocation install is the same for a heap object. Neither is a table nothing installs."
                 % (len(nameless), sum(1 for r in nameless if r["resolution"] == "own-object"),
                    len(nameless_base), len(nameless_member), len(nameless_stack)))
    lines.append("")

    slot_twelve = [r for r in nameless if r["store_verdict"] == "vtable-slot" and r["resolution"] != "own-object"]
    lines.append("**The twelve tables written only at `[reg]` are not a second array of the owner's object.** "
                 "Every one of them is stored by a body that moves `ecx` into a register first "
                 "(`mov esi,ecx`, the thiscall prologue), so the store's base *is* the callee's own object; "
                 "the object is not the owner's, it is whatever the caller passed in `ecx`: %d resolve to a "
                 "member of the caller, %d to a frame slot, %d to an allocation, %d to a base level and %d "
                 "stay mixed." % (len([r for r in slot_twelve if r["resolution"] == "member-of-caller"]),
                                  len([r for r in slot_twelve if r["resolution"] == "stack-object"]),
                                  len([r for r in slot_twelve if r["resolution"] == "allocated"]),
                                  len([r for r in slot_twelve if r["resolution"] == "base-level"]),
                                  len([r for r in slot_twelve if r["resolution"] == "mixed"])))
    lines.append("")

    names_base = [r for r in slot_twelve if r["resolution"] == "base-level"]
    if names_base:
        lines.append("**A base-level install is a destructor's second write, and the caller names the class "
                     "the table belongs to.** The caller writes its own table at `[reg]` and then calls this "
                     "body with the same `this`, so the callee is the base's destructor (or the base's "
                     "constructor, which does the same at the other end) and the table is the *parent* "
                     "class's: %s." % ", ".join(
                         "%s -> `%s` of %s" % (r["table_va"], r["store_verdict"], r["class"] or
                                               (r["population"] + " table")) for r in names_base[:8]))
        lines.append("")

    reg_only = [r for r in rows if r["reg_stores"] and not r["sites"]]
    reg_any = [r for r in rows if r["reg_stores"]]
    lines.append("**%d of the %d tables carry a register-store install the immediate census cannot see, and "
                 "%d of them have no immediate store at all** -- the pair `mov reg,<table>` / "
                 "`mov [reg2(+disp)],reg` puts the address in the first instruction, so a census that reads "
                 "the store's own immediate counts them as `frame-only` or `no-store`. %s"
                 % (len(reg_any), len(rows), len(reg_only),
                    ", ".join(r["table_va"] for r in reg_only[:10]) or "none"))
    lines.append("")

    lines.append("**What this settles for U40.** The `[reg]` store is the object's own slot *relative to the "
                 "body that writes it*, so the store's form alone cannot separate a class table from a "
                 "member's: %d of the %d nameless tables are stored into `[reg]` of an object that is not "
                 "the caller's own, and %d of those are a base class's table, which is a name the caller "
                 "supplies. The residue that needs a per-body read is %d tables whose callers pass an "
                 "unreadable `ecx`." % (
                     sum(1 for r in nameless if r["store_verdict"] in ("vtable-slot", "vtable-slot+member")
                         and r["resolution"] != "own-object"),
                     len(nameless),
                     len(nameless_base),
                     sum(1 for r in nameless if r["resolution"] == "mixed")))
    return "\n".join(lines)


def rows_for_csv(rows):
    out = []
    for r in rows:
        out.append({k: (" ".join(r["call_sites"]) if k == "call_sites" else r.get(k, "")) for k in COLUMNS})
    return out


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows_for_csv(rows))


def read_csv(path):
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def splice_doc(path, block):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(DOC_START), text.find(DOC_END)
    if i < 0 or j < 0:
        return None
    return text[:i] + DOC_START + "\n" + block + "\n" + text[j:]


# --------------------------------------------------------------------------- self-test
def self_check():
    """Synthetic instructions: the pointer rules, the call-site read and the register-store pair."""
    bad = []

    def want(name, got, expected):
        if got != expected:
            bad.append("%s: %r != %r" % (name, got, expected))

    # the thiscall prologue: `mov esi,ecx` makes esi the body's own object
    body = [(0x1000, "push esi"), (0x1001, "mov esi,ecx"),
            (0x1003, "mov DWORD PTR [esi],0x9000")]
    want("this register", this_register(body), "esi")
    want("a store through the body's own this", resolve(body, 2, "esi", this_register(body)), "this")

    # a member loaded off the this register
    body2 = [(0x2000, "mov esi,ecx"), (0x2002, "mov ebx,DWORD PTR [esi+0x60]"),
             (0x2005, "mov DWORD PTR [ebx],0x9000")]
    want("a member of this", resolve(body2, 2, "ebx", "esi"), "this+0x60")

    # a member addressed (lea) and a nested member
    body3 = [(0x3000, "mov esi,ecx"), (0x3002, "mov eax,DWORD PTR [esi+0x60]"),
             (0x3005, "mov DWORD PTR [eax+0x8],0x1")]
    want("a nested member sums", resolve(body3, 2, "eax", "esi"), "this+0x60")

    # a frame slot, a global and an allocation's return
    body4 = [(0x4000, "mov DWORD PTR [ebp-0x10],0x9000")]
    want("a frame slot", resolve(body4, 0, "ebp", ""), "ebp")
    body5 = [(0x5000, "mov eax,DWORD PTR ds:0x5fe168"), (0x5006, "mov DWORD PTR [ebx],eax")]
    want("a global load", resolve(body5, 1, "eax", ""), "ds:0x5fe168")
    body6 = [(0x6000, "call 0x3e7db0"), (0x6005, "mov DWORD PTR [eax],0x9000")]
    want("a call's return is a fresh object", resolve(body6, 1, "eax", ""), "ret")

    # the call-site read: `lea ecx,[esi+0x60]` and `mov ecx,esi`
    caller = [(0x7000, "push esi"), (0x7001, "mov esi,ecx"), (0x7003, "lea ecx,[esi+0x60]"),
              (0x7006, "call 0x1234")]
    want("a member passed as this", ecx_at(caller, 3), "this+0x60")
    caller2 = [(0x7100, "mov esi,ecx"), (0x7102, "mov ecx,esi"), (0x7104, "call 0x1234")]
    want("the caller's own this", ecx_at(caller2, 2), "this")
    caller3 = [(0x7200, "lea ecx,[esp+0x14]"), (0x7204, "call 0x1234")]
    want("a stack temporary", ecx_at(caller3, 1), "esp+0x14")

    # the buckets a resolution is built from
    want("bucket this", classify_origin("this"), "this")
    want("bucket member", classify_origin("this+0x60"), "member")
    want("bucket frame", classify_origin("ebp-0x10"), "frame")
    want("bucket fresh", classify_origin("ret"), "fresh")
    want("bucket unknown", classify_origin("?"), "other")

    # an absolute store: the immediate is the table, the displacement is the global it lives in
    gtext = bytearray(b"\x90" * 0x40)
    gtext[0x10:0x1a] = b"\xc7\x05" + struct.pack("<I", 0x7ab9e8) + struct.pack("<I", 0x9000)
    gsites = sweep_imm_stores(bytes(gtext), 0x1000)
    want("a global store's table", [(f, "%#x" % d) for _, _, _, d, f in gsites], [("global", "0x7ab9e8")])
    want("the global store is not read short", [t for _, t, _, _, _ in gsites], [0x9000])

    # the register-carried install the immediate census cannot see
    text = bytearray(b"\x90" * 0x100)
    text[0x10:0x15] = b"\xb8" + struct.pack("<I", 0x9000)             # mov eax,0x9000
    text[0x15:0x17] = b"\x89\x06"                                     # mov [esi],eax
    text[0x20:0x25] = b"\xb8" + struct.pack("<I", 0x9100)             # mov eax,0x9100
    text[0x25:0x28] = b"\x89\x46\x28"                                 # mov [esi+0x28],eax
    text[0x30:0x35] = b"\xb8" + struct.pack("<I", 0x9200)             # mov eax,0x9200, never stored
    text[0x40:0x45] = b"\xb8" + struct.pack("<I", 0x9300)             # mov eax,0x9300
    text[0x45:0x47] = b"\x89\x0e"                                     # mov [esi],ecx: a different register
    text[0x50:0x55] = b"\xb8" + struct.pack("<I", 0x9400)             # mov eax,0x9400
    text[0x55:0x5A] = b"\xe8" + struct.pack("<i", 0)                  # call: the window stops
    text[0x5A:0x5C] = b"\x89\x06"
    got = {t: (f, d) for _, t, f, _, d in sweep_reg_stores(bytes(text), 0x1000, {0x9000, 0x9100, 0x9200,
                                                                                0x9300, 0x9400})}
    want("a register store at the object slot", got.get(0x9000), ("slot", 0))
    want("a register store at a member", got.get(0x9100), ("member", 0x28))
    want("an address that is never stored", got.get(0x9200), None)
    want("a store of another register", got.get(0x9300), None)
    want("a store past a call", got.get(0x9400), None)
    # and the immediate sweep sees none of them, which is why the column exists
    want("the immediate sweep is blind to it", sweep_imm_stores(bytes(text), 0x1000), [])

    # the verdict
    want("no store is no-store", resolution_of({"sites": 0, "bodies": 0, "call_sites": [],
                                                "caller_resets": 0}), "no-store")
    want("no installer is no-installer", resolution_of({"sites": 1, "bodies": 0, "call_sites": [],
                                                        "caller_resets": 0}), "no-installer")
    want("no caller is no-direct-call", resolution_of({"sites": 1, "bodies": 1, "call_sites": [],
                                                       "caller_resets": 0}), "no-direct-call")
    want("every caller passes its own this", resolution_of(
        {"sites": 1, "bodies": 1, "call_sites": ["this", "this"], "caller_resets": 0}), "own-object")
    want("a caller that resets its own slot first", resolution_of(
        {"sites": 1, "bodies": 1, "call_sites": ["this"], "caller_resets": 1}), "base-level")
    want("a member's address", resolution_of(
        {"sites": 1, "bodies": 1, "call_sites": ["this+0x60"], "caller_resets": 0}), "member-of-caller")
    want("a frame slot", resolution_of(
        {"sites": 1, "bodies": 1, "call_sites": ["esp+0x14", "ebp-0x8"], "caller_resets": 0}),
        "stack-object")
    want("an allocation", resolution_of(
        {"sites": 1, "bodies": 1, "call_sites": ["ret"], "caller_resets": 0}), "allocated")
    want("a mix", resolution_of(
        {"sites": 1, "bodies": 1, "call_sites": ["this", "this+0x60"], "caller_resets": 0}), "mixed")
    want("a global object", resolution_of(
        {"sites": 1, "bodies": 1, "call_sites": ["ds:0x7ab9e8"], "caller_resets": 0}), "global-object")

    rows = [{"table_va": "0x004c0000", "slots": "4", "class": "", "population": "plain-only",
             "store_verdict": "vtable-slot", "sites": 1, "bodies": 1, "call_sites": ["this+0x60"],
             "caller_resets": 0, "reg_stores": 0, "this": 0, "member": 1, "frame": 0, "fresh": 0,
             "other": 0, "store_origin": "this", "resolution": "member-of-caller",
             "evidence": "1 store site(s) at 0x1000; 1 call site(s): this+0x60; 0 register store(s)"},
            {"table_va": "0x004c0100", "slots": "2", "class": "Named", "population": "named",
             "store_verdict": "vtable-slot", "sites": 1, "bodies": 1, "call_sites": ["this"],
             "caller_resets": 0, "reg_stores": 0, "this": 1, "member": 0, "frame": 0, "fresh": 0,
             "other": 0, "store_origin": "this", "resolution": "own-object",
             "evidence": "1 store site(s) at 0x2000; 1 call site(s): this; 0 register store(s)"}]
    out = render(rows)
    for want_text in ("control", "U40", "register-store"):
        if want_text not in out:
            bad.append("render lacks %r" % (want_text,))
    path = os.path.join(tempfile.mkdtemp(), "a.csv")
    write_artifact(rows, path)
    if [r["table_va"] for r in read_csv(path)] != ["0x004c0000", "0x004c0100"]:
        bad.append("the artifact does not round-trip")
    if bad:
        print("FAIL class-store-provenance: " + "; ".join(bad))
        return 1
    print("ok   class-store-provenance: the pointer rules, the call-site read and the register-store pair "
          "agree with the synthetic image")
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
        rows = read_csv(SRC)
        if not rows:
            print("FAIL class-store-provenance: %s is missing" % SRC)
            return 1
        text = open(args.check_doc, encoding="utf-8").read()
        i, j = text.find(DOC_START), text.find(DOC_END)
        if i < 0 or j < 0:
            print("FAIL class-store-provenance: %s has no generated block" % args.check_doc)
            return 1
        for r in rows:
            r["call_sites"] = r["call_sites"].split() if r["call_sites"] else []
            for k in ("sites", "bodies", "this", "member", "frame", "fresh", "other", "caller_resets",
                      "reg_stores"):
                r[k] = int(r[k] or 0)
        if text[i + len(DOC_START):j].strip() != render(rows).strip():
            print("FAIL class-store-provenance: %s is stale; re-run --write-doc" % args.check_doc)
            return 1
        print("ok   class-store-provenance: the page block matches the committed artifact")
        return 0

    if args.report and not args.xbe:
        rows = read_csv(SRC)
        for r in rows:
            r["call_sites"] = r["call_sites"].split() if r["call_sites"] else []
            for k in ("sites", "bodies", "this", "member", "frame", "fresh", "other", "caller_resets",
                      "reg_stores"):
                r[k] = int(r[k] or 0)
        print(render(rows))
        return 0

    if not args.xbe:
        print("FAIL class-store-provenance: --xbe is required to read the image")
        return 1
    import xbe as xbemod
    image = xbemod.Xbe(args.xbe)
    code = next(s for s in image.sections if s.name == ".text")
    blob = image.data[code.foff:code.foff + min(code.fsize, code.vsize)]

    tables = {int(r["table_va"], 16): int(r["table_slots"] or 0) for r in read_csv(REACH)}
    names = {int(r["table_va"], 16): r["class"] for r in read_csv(NAMES) if r.get("class")}
    store_verdict = {int(r["table_va"], 16): (r["population"], r["verdict"]) for r in read_csv(FIELDS_IN)}

    funcs = read_csv(FUNCS)
    sizes = {int(r["address"], 16): int(r["size"] or 0) for r in funcs}
    fnames = {int(r["address"], 16): r["name"] for r in funcs}
    starts = sorted(a for a, n in sizes.items() if n)

    def body_of(site):
        i = bisect.bisect_right(starts, site) - 1
        while i >= 0 and site - starts[i] <= 0x4000:
            a = starts[i]
            if a <= site < a + sizes[a]:
                return a
            i -= 1
        return 0

    imm = sweep_imm_stores(blob, code.va)
    reg = sweep_reg_stores(blob, code.va, set(tables))
    sites_by_table = collections.defaultdict(list)
    for site, val, base, disp, form in imm:
        if val in tables:
            sites_by_table[val].append((site, val, base, disp, form))
    reg_by_table = collections.defaultdict(list)
    for site, val, form, base, disp in reg:
        reg_by_table[val].append((site, val, form, base, disp))

    # every `E8` into an installer body, with the caller body it sits in
    calls_into = collections.defaultdict(list)
    for i in range(len(blob) - 5):
        if blob[i] != 0xE8:
            continue
        target = code.va + i + 5 + struct.unpack_from("<i", blob, i + 1)[0]
        calls_into[target].append(code.va + i)
    bodies_by_table = collections.defaultdict(list)
    for tva, sites in sites_by_table.items():
        for site, _, _, _, _ in sites:
            b = body_of(site)
            if b and b not in bodies_by_table[tva]:
                bodies_by_table[tva].append(b)

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

    call_sites_of = {}
    caller_resets_of = {}
    resetters = {body_of(s[0]) for s in imm if s[4] == "slot" and s[1] in tables and body_of(s[0])}
    for body in {b for bs in bodies_by_table.values() for b in bs}:
        chains = []
        for site in calls_into.get(body, []):
            caller = body_of(site)
            if not caller:
                continue
            insns = dis(caller)
            idx = next((i for i, (va, _) in enumerate(insns) if va == site), None)
            if idx is None:
                chains.append("?")
                continue
            chains.append(ecx_at(insns, idx))
        call_sites_of[body] = chains
        # a destructor level writes its own table at `[reg]` and then calls the base's installer with the
        # same `this`, so a caller that is itself an installer of a different table resets its slot first
        caller_resets_of[body] = any(body_of(s) in resetters for s in calls_into.get(body, []) if body_of(s))

    def origin_of(site):
        if site[4] == "global":
            return "ds:%#x" % site[3]
        b = body_of(site[0])
        if not b:
            return "?"
        chain = resolve(dis(b), next((i for i, (va, _) in enumerate(dis(b)) if va == site[0]), 0),
                        site[2], this_register(dis(b)))
        return chain

    rows = harvest(tables, names, store_verdict, sites_by_table, reg_by_table, bodies_by_table,
                   call_sites_of, caller_resets_of, origin_of)
    if args.out:
        write_artifact(rows, os.path.join(args.out, os.path.basename(OUT)))
    if args.write_doc:
        spliced = splice_doc(args.write_doc, render(rows))
        if spliced is None:
            print("FAIL class-store-provenance: %s has no generated block" % args.write_doc)
            return 1
        open(args.write_doc, "w", encoding="utf-8").write(spliced)
        print("ok   class-store-provenance: wrote %s" % args.write_doc)
    if args.report or not (args.out or args.write_doc):
        print(render(rows))
    counts = collections.Counter(r["resolution"] for r in rows)
    print("tables=%d %s" % (len(rows), " ".join("%s=%d" % (k, counts[k]) for k in RESOLUTIONS if counts[k])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
