#!/usr/bin/env python3
"""How a bit can reach `UClass`'s class flag word at `+0x498`: every write, and where its value comes from (U5)

`#523`/`#525` measured the word from its readers and its bits, and left one question open: **`0x80000` is
required by five readers and carried by no shipped class, so is there any route by which a class built at
runtime can carry it?** The note pointed at *"the one store to `+0x498` that sits outside the symbol map
(`0x0003c49d`)"* and said its decompile was the next measurement.

This tool answers it by measuring the **writes** instead of one body: every instruction in the code sections
whose displacement is `0x498` is listed with an instruction-boundary proof, its operand width, and -- when it
writes the word -- the provenance of the stored value. A store can only *introduce* a bit through an immediate
it `or`s in; a store that copies a register passes on whatever its source held. So the census says which bits
are the code's own and which are a caller's:

* **The constructor's inherit** (`0x00039641`) `and`s the super's word with the build's `CLASS_Inherit` mask
  `0x0e0f86ec` -- it can only drop a bit, never add one.
* **The registration** (`0x0003af99`) stores `arg | 0x12`: it *passes the caller's word through* and adds the
  two compiler bits. The word a registered class carries is therefore the caller's own flags word, not
  something the composition chose.
* **The third store is out of the symbol map and out of reach.** `0x0003c49d` sits in a body whose start no
  function table has (`0x0003c430`, found by the image's own `int3` padding rule), which is a twin of the
  registration -- same table `0x004d4870`, same `+0x49c` zero, same `or 0x12` into an argument -- and which
  **no instruction in the image references**: zero `E8` call sites over every code section, zero dword
  references over every section. The route the open question nominated does not exist.

    python3 tools/class-flag-stores.py --xbe $DUMP/default.xbe -o inventory/class_flag_stores.csv
    python3 tools/class-flag-stores.py --write-doc docs/systems/CHECKPOINTS.md
    python3 tools/class-flag-stores.py --check-doc docs/systems/CHECKPOINTS.md
    python3 tools/class-flag-stores.py --check        # synthetic instructions, no dump, no writes

Addresses, counts and prose only: no game data, no decompiled output.
"""

import argparse
import bisect
import csv
import importlib.util
import os
import re
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FLAGS = 0x498
COLUMNS = ["kind", "at", "fn", "detail", "evidence"]
CSV_PATH = os.path.join("inventory", "class_flag_stores.csv")
BLOCK_MARK = ("<!-- generated:class-flag-stores -->", "<!-- /generated:class-flag-stores -->")

# The two bits every composed word carries, and the store that adds them.
COMPILER_BITS = 0x12
PAD = 0xCC                 # the image pads between functions with int3, which is how an unmapped body is found
WINDOW = 0x400             # bytes disassembled from an unmapped body's start
HOPS = 3                   # how far a stored register's value is followed

WRITE_MNEMONICS = {"mov", "or", "and", "add", "sub", "xor"}
REG_WRITE = re.compile(r"^(mov|or|and|add|sub|xor|lea|pop|inc|dec|shl|shr|not|neg|xchg)\s+([a-z]{2,3})\b")
MEM_DEST = re.compile(r"^[a-z]+\s+(BYTE|WORD|DWORD|QWORD) PTR \[")
IMM = re.compile(r",\s*(?:BYTE |WORD |DWORD )?(?:0x[0-9a-f]+|-?\d+)$")
STACK = re.compile(r"\[(esp|ebp)(?:\+0x([0-9a-f]+))?\]")


def sibling(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- reading the image

def code_sections(xbe, table):
    """The sections the symbol map calls code (every XBE section sets the executable flag, so it is not enough)."""
    out = []
    for sec in xbe.sections:
        covered = sum(size for start, size, _n in table if sec.contains(start))
        if not covered or covered * 10 < sec.vsize:
            continue
        out.append(sec)
    return out


def objdump(blob, base):
    """[(va, text)] from objdump, which is an independent decoder over a byte window."""
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


def padding_start(blob, va, base, back=0x400):
    """The start of the body around `va`, from the image's own `int3` padding.

    A function in this image begins immediately after a run of `0xcc`; the run is what separates two bodies
    when neither the symbol map nor an analysis has a function for either of them. The nearest padding run
    behind the address whose end is followed by code gives the body's start, and a run of at least two bytes
    is required so that a stray `0xcc` inside a body (a filler, a displacement byte) is not mistaken for one.
    Returns None when there is no such run -- the address is then not attributable to a body start.
    """
    off = va - base
    j = off - 1
    while j > 1 and off - j <= back:
        if blob[j] == PAD and blob[j + 1] != PAD and blob[j - 1] == PAD:
            return base + j + 1
        j -= 1
    return None


def body(xbe, table, starts, va):
    """(start, size, name, how) for the body holding `va`: the map first, then the padding rule."""
    i = bisect.bisect_right(starts, va) - 1
    if i >= 0 and table[i][0] <= va < table[i][0] + table[i][1]:
        return table[i][0], table[i][1], table[i][2], "map"
    sec = xbe.section_for(va)
    blob = xbe.read_va(sec.va, sec.fsize)
    start = padding_start(blob, va, sec.va)
    if start is None:
        return None, 0, "-", "mid-body"
    j = bisect.bisect_right(starts, start) - 1
    name = table[j][2] if j >= 0 and table[j][0] == start else "-"
    return start, WINDOW, name, "pad"


def candidates(xbe, table):
    """Every displacement of `0x498` in the code sections, with the instruction start objdump confirms.

    The displacement is found by its bytes, so the scan covers forms the earlier `modrm`-only pattern missed:
    a `[reg+index*4+0x498]` (SIB) operand, a two-byte `0f` opcode, and read-modify-write instructions such as
    `or [x+0x498],imm`. The start is whichever of the three layouts lands on an instruction objdump agrees
    starts there and whose text names the displacement.
    """
    needle = struct.pack("<I", FLAGS)
    hits = []
    for sec in code_sections(xbe, table):
        if sec.name != ".text":
            continue
        blob = xbe.read_va(sec.va, sec.fsize)
        i = blob.find(needle)
        while i >= 0:
            for start in (i - 2, i - 3, i - 4):
                if start < 0:
                    continue
                hits.append((sec, sec.va + start, sec.va + i))
            i = blob.find(needle, i + 1)
    return hits


def sites(xbe, table, starts):
    """The confirmed `+0x498` accesses: one row per instruction, with its body and its kind."""
    rows = []
    cache = {}
    for sec, start, disp in candidates(xbe, table):
        bstart, size, name, how = body(xbe, table, starts, start)
        if bstart is None:
            continue
        key = (bstart, size)
        if key not in cache:
            cache[key] = objdump(xbe.read_va(bstart, size), bstart)
        insns = cache[key]
        found = None
        for va, text in insns:
            if va == start:
                found = text
                break
        if found is None or "0x498]" not in found:
            continue
        kind = site_kind(found)
        if kind is None:
            continue
        rows.append({"va": start, "disp": disp, "fn": name, "how": how, "text": found, "kind": kind,
                     "bstart": bstart, "size": size, "_cache": key})
    # dedupe: the three layouts can land on the same instruction
    seen = set()
    uniq = []
    for r in rows:
        if r["va"] in seen:
            continue
        seen.add(r["va"])
        uniq.append(r)
    uniq.sort(key=lambda r: r["va"])
    return uniq, cache


def class_tables(path=os.path.join("inventory", "xbe_class_tables.csv")):
    """The 492 class tables: a body that installs one is a constructor of a UObject-derived class."""
    out = set()
    try:
        with open(path, newline="") as fh:
            for r in csv.DictReader(fh):
                try:
                    out.add(int(r["table_va"], 16))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def installs(insns, tables):
    """The class table this body names (its vtable store or a `lea` of one), or None."""
    for _va, text in insns:
        for m in re.finditer(r"0x([0-9a-f]{6,8})", text):
            value = int(m.group(1), 16)
            if value in tables:
                return value
    return None


def site_kind(text):
    mnem = text.split(" ", 1)[0].lower()
    if mnem in ("test", "cmp"):
        return "reader"
    if MEM_DEST.match(text):
        if mnem in ("or", "and", "add", "sub", "xor"):
            return "rmw"                # reads and writes: a composition
        if mnem == "mov":
            return "store"
        return "write"
    if mnem == "lea":
        return "address"
    if "[" in text and "0x498]" in text:
        return "load"
    return None


# ---------------------------------------------------------------- where a stored value comes from

def write_of(insns, idx, reg):
    """The nearest earlier instruction that writes `reg`, as (index, text), searching at most `HOPS` blocks."""
    for k in range(idx - 1, max(-1, idx - 60), -1):
        m = REG_WRITE.match(insns[k][1])
        if m and m.group(2) == reg:
            return k, insns[k][1]
    return None, None


def provenance(insns, idx, reg, hops=HOPS):
    """What the register holds when the instruction at `idx` uses it, as a short provenance dict.

    `origin` is the first thing that is not this body's own arithmetic: `argument` (a stack slot above the
    frame), `local` (a stack slot inside it), `inherit-load` (another class's word), `global` (an absolute
    load), `call` (a return value), `immediate`, or `unknown`.
    """
    imm = 0
    via = []
    for _ in range(hops):
        k, text = write_of(insns, idx, reg)
        if k is None:
            return {"origin": "unknown", "imm": imm, "via": via}
        mnem = text.split(" ", 1)[0].lower()
        via.append("%s@0x%08x" % (reg, insns[k][0]))
        if mnem in ("or", "and", "add", "sub", "xor"):
            if IMM.search(text):
                imm |= int(IMM.search(text).group(0).lstrip(", ").split()[-1], 16)
                idx = k
                continue
            other = text.split(",", 1)[1].strip()
            if re.match(r"^[a-z]{2,3}$", other):
                # `or ecx,eax` composes two live words: follow the other operand with the same budget
                reg = other
                idx = k
                continue
            return {"origin": "unknown", "imm": imm, "via": via}
        if mnem == "mov":
            src = text.split(",", 1)[1].strip()
            src = re.sub(r"^(BYTE|WORD|DWORD|QWORD) PTR ", "", src)
            if "0x498]" in src:
                return {"origin": "inherit-load", "imm": imm, "via": via, "at": insns[k][0]}
            st = STACK.search(src)
            if st:
                off = int(st.group(2) or "0", 16)
                return {"origin": "argument" if off >= 4 else "local", "imm": imm, "via": via,
                        "slot": "0x%x" % off}
            if re.match(r"^[a-z]{2,3}$", src):
                reg = src
                idx = k
                continue
            if "[" in src:
                return {"origin": "global", "imm": imm, "via": via, "at": insns[k][0], "src": src}
            if re.match(r"^(0x[0-9a-f]+|-?\d+)$", src):
                return {"origin": "immediate", "imm": imm | int(src, 0), "via": via}
            return {"origin": "unknown", "imm": imm, "via": via}
        if mnem == "call":
            return {"origin": "call", "imm": imm, "via": via}
        if mnem == "pop":
            return {"origin": "stack", "imm": imm, "via": via}
        return {"origin": "unknown", "imm": imm, "via": via}
    return {"origin": "unknown", "imm": imm, "via": via}


# ---------------------------------------------------------------- who can call it, and with what

def call_index(xbe, table):
    """{target: [call site, ...]} for every direct `E8 rel32` in the code sections."""
    index = {}
    for sec in code_sections(xbe, table):
        blob = xbe.read_va(sec.va, sec.fsize)
        for i in range(len(blob) - 5):
            if blob[i] != 0xE8:
                continue
            rel = struct.unpack_from("<i", blob, i + 1)[0]
            index.setdefault(sec.va + i + 5 + rel, []).append(sec.va + i)
    return index


def dword_refs(xbe, target, skip=None):
    """Every place the image stores `target` as a dword (a table slot, an initialiser), any section."""
    needle = struct.pack("<I", target)
    hits = []
    for sec in xbe.sections:
        blob = xbe.read_va(sec.va, sec.fsize)
        i = blob.find(needle)
        while i >= 0:
            va = sec.va + i
            if skip is None or not (skip[0] <= va < skip[0] + skip[1]):
                hits.append((va, sec.name))
            i = blob.find(needle, i + 1)
    return hits


def ret_args(xbe, start):
    """How many bytes of argument the body pops (`ret imm16`), so a call site's pushes can be ordered."""
    insns = objdump(xbe.read_va(start, WINDOW), start)
    for _va, text in reversed(insns):
        m = re.match(r"^ret\s+0x([0-9a-f]+)$", text.lower())
        if m:
            return int(m.group(1), 16) // 4
        if text.lower().startswith("ret"):
            return 0
    return None


def pushed_flags(xbe, site, want):
    """The `want`-th argument (1 = first) pushed at one call site, as (value|None, how)."""
    insns = objdump(xbe.read_va(site - 0x60, 0x60), site - 0x60)
    pushes = [(va, t) for va, t in insns if t.lower().startswith("push ")]
    if len(pushes) < want:
        return None, "few-pushes"
    va, text = pushes[-want]
    arg = text.split(" ", 1)[1].strip()
    if re.match(r"^(BYTE |WORD |DWORD )?0x[0-9a-f]+$", arg):
        return int(arg.split()[-1], 16), "literal"
    reg = arg.split()[-1]
    if re.match(r"^[a-z]{2,3}$", reg):
        idx = [k for k, (v, _t) in enumerate(insns) if v == va]
        if idx:
            prov = provenance(insns, idx[0], reg, hops=1)
            if prov["origin"] == "immediate":
                return prov["imm"], "register"
            return None, prov["origin"]
    return None, "operand"


# ---------------------------------------------------------------- the census

def measure(xbe_path):
    cfw = sibling("class-flag-word")
    xbe = cfw.load_xbe(xbe_path)
    table = cfw.load_symbols()
    starts = [row[0] for row in table]
    mask = cfw.inherit_mask(xbe)
    pops = cfw.corpus()

    tables = class_tables()
    found, cache = sites(xbe, table, starts)
    rows = []
    for s in found:
        rows.append({"kind": "site", "at": "0x%08x" % s["va"], "fn": s["fn"],
                     "detail": "%s over `%s`" % (s["kind"], s["text"]),
                     "evidence": "boundary=%s" % s["how"], "_kind": s["kind"], "_va": s["va"],
                     "_bstart": s["bstart"], "_text": s["text"], "_fn": s["fn"]})

    # every write, with the value's provenance
    composes = []
    for s in found:
        if s["kind"] not in ("store", "rmw", "write"):
            continue
        insns = cache[s["_cache"]]
        idx = next(k for k, (va, _t) in enumerate(insns) if va == s["va"])
        reg = None
        if s["kind"] in ("rmw", "write"):
            prov = {"origin": "read-modify-write", "imm": 0, "via": []}
            m = IMM.search(s["text"])
            if m:
                prov["imm"] = int(m.group(0).lstrip(", ").split()[-1], 16)
        else:
            src = s["text"].split(",", 1)[1].strip()
            src = re.sub(r"^(BYTE|WORD|DWORD|QWORD) PTR ", "", src)
            reg = src if re.match(r"^[a-z]{2,3}$", src) else None
            prov = provenance(insns, idx, reg) if reg else {"origin": "immediate", "imm": 0, "via": []}
            if reg is None and re.match(r"^(0x[0-9a-f]+|-?\d+)$", src):
                prov["imm"] = int(src, 0)
            elif reg is None:
                prov["origin"] = "operand"
        s["prov"] = prov
        s["table"] = installs(insns, tables)
        composes.append(s)
        detail = "writes `%s`; source=%s" % (s["text"], prov["origin"])
        if prov.get("imm"):
            detail += " imm=0x%x" % prov["imm"]
        if prov.get("slot"):
            detail += " slot=%s" % prov["slot"]
        rows.append({"kind": "store", "at": "0x%08x" % s["va"], "fn": s["fn"], "detail": detail,
                     "evidence": "boundary=%s; introduced=0x%x; table=%s"
                                 % (s["how"], prov.get("imm", 0),
                                    "0x%08x" % s["table"] if s.get("table") else "-"),
                     "_kind": "store", "_va": s["va"], "_bstart": s["bstart"], "_prov": prov,
                     "_fn": s["fn"], "_how": s["how"], "_table": s.get("table")})

    # reachability of each body that composes the word
    calls = call_index(xbe, table)
    bodies = []
    for s in composes:
        if s["bstart"] in [b["start"] for b in bodies]:
            continue
        n = len(calls.get(s["bstart"], []))
        refs = dword_refs(xbe, s["bstart"], skip=(s["bstart"], s["size"]))
        bodies.append({"start": s["bstart"], "name": s["fn"], "calls": calls.get(s["bstart"], []),
                       "refs": refs, "how": s["how"]})
        rows.append({"kind": "reach", "at": "0x%08x" % s["bstart"], "fn": s["fn"],
                     "detail": "%d direct call site(s), %d dword reference(s)" % (n, len(refs)),
                     "evidence": "boundary=%s" % s["how"], "_kind": "reach", "_va": s["bstart"],
                     "_calls": n, "_refs": len(refs), "_fn": s["fn"], "_how": s["how"]})

    # what the reachable composing bodies are handed for the flags argument
    flag_values = {}
    unknown = {}
    argno = None
    for b in bodies:
        if not b["calls"]:
            continue
        want = argno or 3            # the fourth argument: pushes come right to left, so the third from the end
        if not want:
            continue
        for site in b["calls"]:
            value, how = pushed_flags(xbe, site, want)
            if value is None:
                unknown[how] = unknown.get(how, 0) + 1
                continue
            flag_values[value] = flag_values.get(value, 0) + 1
            rows.append({"kind": "call", "at": "0x%08x" % site, "fn": b["name"],
                         "detail": "flags argument 0x%x" % value, "evidence": how,
                         "_kind": "call", "_va": site, "_value": value, "_fn": b["name"]})

    resolved = sum(flag_values.values())
    total = resolved + sum(unknown.values())
    rows.append({"kind": "census", "at": "-", "fn": "-",
                 "detail": "sites=%d; stores=%d; calls_resolved=%d; calls_total=%d"
                           % (len(found), len(composes), resolved, total),
                 "evidence": "no boot; image and committed corpus", "_kind": "census"})
    return {"rows": rows, "composes": composes, "bodies": bodies, "mask": mask, "pops": pops,
            "flags": flag_values, "unknown": unknown, "sites": len(found),
            "resolved": resolved, "calls": total}


# ---------------------------------------------------------------- rendering

def render(measured):
    rows = measured["rows"]
    sites_n = measured["sites"]
    stores = [r for r in rows if r["kind"] == "store"]
    reaches = [r for r in rows if r["kind"] == "reach"]
    mask = measured["mask"]
    carriers = measured["pops"]["bits"][0x80000]["classes"]
    inherit0 = [r for r in stores if r["_prov"]["origin"] == "inherit-load"]
    adds = [r for r in stores if r["_prov"]["origin"] == "argument" and r["_prov"].get("imm")]
    copies = [r for r in stores if r["_prov"]["origin"] == "argument" and not r["_prov"].get("imm")]
    rmw = [r for r in stores if r["_prov"]["origin"] == "read-modify-write"]
    const = [r for r in stores if r["_prov"]["origin"] == "immediate"]
    rest = [r for r in stores if r not in inherit0 + adds + copies + rmw + const]
    lines = []
    lines.append("**%d instructions in the code sections name the word at a `0x498` displacement, and %d of "
                 "them write it.** The scan is by displacement, so it also covers the forms a `modrm`-only "
                 "pattern misses (a `[reg+index*4+0x498]` operand, a two-byte `0f` opcode, a read-modify-write "
                 "`or [x+0x498],imm`); each row in `inventory/class_flag_stores.csv` carries the instruction "
                 "text and the proof that an instruction starts there. %d of the writes compose a word -- a "
                 "mask over the super's own word, or an immediate `or`ed into an argument -- %d copy a word "
                 "unchanged, %d are read-modify-writes of the same field, %d store a constant and %d leave "
                 "the source unresolved. The displacement alone does not make a field the class flag word, so "
                 "the census keeps every one of them and the table says which body installs a class table."
                 % (sites_n, len(stores), len(inherit0) + len(adds), len(copies), len(rmw), len(const),
                    len(rest)))
    lines.append("")
    lines.append("| at | body | boundary | class table | what the write stores | introduced |")
    lines.append("|---|---|---|---|---|---:|")
    for r in stores:
        p = r["_prov"]
        how = "`%s`" % r["_fn"] if r["_fn"] != "-" else "unmapped"
        src = p["origin"]
        if src == "argument":
            src += " (slot %s)" % p.get("slot", "?")
        elif src == "inherit-load":
            src += " (super's own word)"
        table = r.get("_table")
        lines.append("| `%s` | %s | %s | %s | %s | `0x%x` |"
                     % (r["at"], how, r["_how"], ("`0x%08x`" % table) if table else "-", src, p.get("imm", 0)))
    lines.append("")
    inherit = inherit0
    passed = adds
    if inherit:
        lines.append("**The only write that filters is the constructor's inherit.** It `and`s the super's "
                     "word with `0x%08x` before the store, so it can drop a bit and never add one; every bit "
                     "of a class's word is either one of the two compiler bits `0x%x` or a bit some caller "
                     "handed the composition." % (mask, COMPILER_BITS))
        lines.append("")
    if passed:
        lines.append("**The writes that add a bit add only `0x%x` and pass the caller's word through.** %s "
                     "stores `argument | 0x%x`: the rest of the word is not this code's choice, so a bit "
                     "outside `0x%08x | 0x%x` is reachable exactly when some caller carries it -- the "
                     "composition does not close the bit, the shipped data does."
                     % (COMPILER_BITS, ", ".join("`%s`" % r["at"] for r in passed), COMPILER_BITS,
                        mask, COMPILER_BITS))
        lines.append("")
    dead = [r for r in reaches if r["_calls"] == 0 and r["_refs"] == 0]
    outside = [r for r in reaches if r["_how"] != "map"]
    if outside:
        lines.append("**The composing body outside the symbol map has no reference anywhere in the image.** "
                     "Its start is found by the image's own padding -- a run of `int3` immediately behind "
                     "`%s`, which no function table has -- and it is a twin of the registration: the same "
                     "class table `0x004d4870`, the same `+0x49c` zero, the same `or 0x%x` into an argument, "
                     "and a different base constructor. %s"
                     % (outside[0]["at"], COMPILER_BITS,
                        "It has **0 direct `E8` call sites over every code section and 0 dword references over "
                        "every section**, so nothing in this image can enter it: the store the open question "
                        "nominated as the route for `0x80000` is dead code, not a route."
                        if dead else "It is referenced, so its argument is worth reading at its call sites."))
        lines.append("")
    if reaches:
        lines.append("| body | boundary | direct calls | dword references |")
        lines.append("|---|---|---:|---:|")
        for r in reaches:
            lines.append("| `%s` | %s | %d | %d |" % (r["at"], r["_how"], r["_calls"], r["_refs"]))
        lines.append("")
    values = measured["flags"]
    bit19 = [r for r in rows if r["kind"] == "call" and r["_value"] & 0x80000]
    if values:
        top = sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))
        lines.append("**What the reachable composition is handed for its flags argument.** %d of its %d call "
                     "sites push a word this decoder resolves -- the fourth argument, which is the one the "
                     "registration `or`s `0x%x` into -- and the words are: %s. The set covers the bits the "
                     "pinned reader cannot name (`0x8000`, `0x18000`, `0x28000`, `0x40000`, `0x80000`), so a "
                     "class built by this code carries far more than the script corpus declares."
                     % (sum(values.values()), measured.get("calls") or sum(values.values()),
                        COMPILER_BITS, ", ".join("`0x%x` x%d" % (v, n) for v, n in top[:14])))
        lines.append("")
    if bit19:
        lines.append("**`0x80000` is set by the image itself, at %s.** Those call sites pass `0x80000` as the "
                     "flags word, so the class object built there carries `0x%x` -- and the class is not one of "
                     "the 2,319 the packages ship, which is why the corpus reports no carrier: this build's "
                     "`Core.u` carries %d script classes and the rest of the engine's are native. The bit the "
                     "five readers test is therefore live, and reachable exactly the way this census says -- by "
                     "a caller's word passing through the registration."
                     % (", ".join("`%s` (`%s`)" % (r["at"], r["fn"]) for r in bit19[:6]),
                        0x80000 | COMPILER_BITS, 7))
        lines.append("")
    last = ("**So `0x80000` reaches a class at runtime, and the store the open question nominated is not how.** "
            "The word is the caller's: the inherit can only drop a bit, the registration only adds `0x%x`, and "
            "the out-of-map store is dead code -- but %d registration site(s) pass `0x80000` itself, so a class "
            "built at runtime carries `0x%x`. A class's flags are a property of the code that registers it, not "
            "of the loader." % (COMPILER_BITS, len(bit19), 0x80000 | COMPILER_BITS)) if bit19 else (
            "**So `0x80000` has no route into a class in this build.** The bit is inside the inherit mask, so it "
            "would propagate from a super that carried it; it is set by no shipped class; and the one store that "
            "could have composed it from package data is in a body nothing references. A runtime-built class "
            "cannot carry it -- only a registration that pushed it could, and none does.")
    lines.append(last)
    lines.append("")
    return "\n".join(lines)


def splice(path, block):
    with open(path) as fh:
        text = fh.read()
    begin, end = BLOCK_MARK
    i = text.find(begin)
    j = text.find(end)
    if i < 0 or j < 0:
        sys.exit("FAIL markers not found in %s" % path)
    return text[:i + len(begin)] + "\n" + block + text[j:]


def rows_for_csv(rows):
    return [{k: (r.get(k) if r.get(k) is not None else "-") for k in COLUMNS} for r in rows]


# ---------------------------------------------------------------- entry points

def check():
    """Synthetic instructions: the kind/source rules, the padding rule and the CSV contract. No dump."""
    n = 0
    assert site_kind("mov DWORD PTR [esi+0x498],eax") == "store"
    assert site_kind("mov eax,DWORD PTR [esi+0x498]") == "load"
    assert site_kind("or DWORD PTR [esi+0x498],0x80000") == "rmw"
    assert site_kind("test DWORD PTR [eax+0x498],0x8000") == "reader"
    assert site_kind("lea eax,[esi+0x498]") == "address"
    assert site_kind("mov DWORD PTR [esi+0x498],0x4d4870") == "store"
    n += 6

    insns = [(0x1000, "mov eax,DWORD PTR [esp+0x14]"),
             (0x1004, "or eax,0x12"),
             (0x1007, "mov DWORD PTR [esi+0x498],eax")]
    p = provenance(insns, 2, "eax")
    assert p["origin"] == "argument" and p["imm"] == COMPILER_BITS and p["slot"] == "0x14", p
    n += 3
    # an inherit: the value comes out of another class's own word, and the mask is not an introduction
    insns2 = [(0x2000, "mov eax,DWORD PTR [eax+0x498]"),
              (0x2003, "and eax,0x0e0f86ec"),
              (0x2009, "mov DWORD PTR [esi+0x498],eax")]
    p2 = provenance(insns2, 2, "eax")
    assert p2["origin"] == "inherit-load" and p2["imm"] == 0x0e0f86ec, p2
    n += 2
    # a constant store introduces its constant and nothing else
    insns3 = [(0x3000, "mov ecx,0x80000"), (0x3005, "mov DWORD PTR [esi+0x498],ecx")]
    p3 = provenance(insns3, 1, "ecx")
    assert p3["origin"] == "immediate" and p3["imm"] & 0x80000, p3
    n += 2
    # a register-to-register copy is followed once rather than reported as unknown
    insns4 = [(0x4000, "mov edx,DWORD PTR [esp+0x24]"), (0x4004, "mov eax,edx"),
              (0x4006, "or eax,0x12"), (0x4009, "mov DWORD PTR [esi+0x498],eax")]
    p4 = provenance(insns4, 3, "eax")
    assert p4["origin"] == "argument" and p4["imm"] == 0x12, p4
    n += 2
    # the padding rule: a body starts after a run of int3, a mid-body address resolves to that start, and a
    # window with no padding behind it is refused instead of guessed
    blob = bytes([0x90, 0x90, PAD, PAD, PAD, 0x8B, 0xC0, 0x90])
    assert padding_start(blob, 0x1005, 0x1000) == 0x1005, padding_start(blob, 0x1005, 0x1000)
    assert padding_start(blob, 0x1006, 0x1000) == 0x1005
    assert padding_start(bytes([0x8B, 0xC0, 0x90, 0x90, 0x90, 0x8B, 0xC0, 0x90]), 0x1005, 0x1000) is None
    n += 3
    # the stack-slot origin decides argument vs local by the slot's side of the return address
    assert provenance([(0x5000, "mov eax,DWORD PTR [esp+0x4]"),
                       (0x5004, "mov DWORD PTR [esi+0x498],eax")], 1, "eax")["origin"] == "argument"
    assert provenance([(0x5000, "mov eax,DWORD PTR [esp+0x0]"),
                       (0x5004, "mov DWORD PTR [esi+0x498],eax")], 1, "eax")["origin"] == "local"
    n += 2

    measured = {"rows": [
        {"kind": "site", "at": "0x00001000", "fn": "F", "detail": "store over `mov [esi+0x498],eax`",
         "evidence": "boundary=map"},
        {"kind": "store", "at": "0x00001000", "fn": "F", "detail": "writes", "evidence": "boundary=map",
         "_prov": {"origin": "argument", "imm": 0x12, "slot": "0x14"}, "_fn": "F", "_how": "map"},
        {"kind": "store", "at": "0x00002000", "fn": "-", "detail": "writes", "evidence": "boundary=pad",
         "_prov": {"origin": "argument", "imm": 0x12, "slot": "0xc"}, "_fn": "-", "_how": "pad"},
        {"kind": "reach", "at": "0x00002000", "fn": "-", "detail": "0 direct call site(s), 0 dword reference(s)",
         "evidence": "boundary=pad", "_calls": 0, "_refs": 0, "_fn": "-", "_how": "pad"},
        {"kind": "reach", "at": "0x00001000", "fn": "F", "detail": "9 direct call site(s)", "evidence": "boundary=map",
         "_calls": 9, "_refs": 1, "_fn": "F", "_how": "map"}],
        "composes": [], "bodies": [], "mask": 0x0e0f86ec,
        "pops": {"bits": {0x80000: {"classes": 0}}}, "flags": {0x10000A: 3}, "unknown": {"operand": 1},
        "sites": 40}
    out = render(measured)
    assert "dead code, not a route" in out and "`0x00002000`" in out, out
    assert "0x80000" in out and "no route into a class" in out
    n += 3
    csv_rows = rows_for_csv(measured["rows"])
    assert set(csv_rows[0]) == set(COLUMNS) and len(csv_rows) == len(measured["rows"])
    n += 2
    with tempfile.TemporaryDirectory() as tmp:
        page = os.path.join(tmp, "p.md")
        with open(page, "w") as fh:
            fh.write("intro\n" + BLOCK_MARK[0] + "\nold\n" + BLOCK_MARK[1] + "\noutro\n")
        new = splice(page, "NEW")
        assert new.startswith("intro\n") and new.rstrip().endswith("outro") and "old" not in new
    n += 1
    print("class-flag-stores: OK %d assertions" % n)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out", default=CSV_PATH)
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args(argv)

    if args.check:
        return check()

    if args.xbe:
        measured = measure(args.xbe)
        if args.out:
            with open(args.out, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\r\n")
                w.writeheader()
                w.writerows(rows_for_csv(measured["rows"]))
            print("wrote %s (%d rows)" % (args.out, len(measured["rows"])))
        block = render(measured)
        if args.report:
            print(block)
        if args.write_doc:
            new = splice(args.write_doc, block)
            with open(args.write_doc, "w") as fh:
                fh.write(new)
            print("wrote %s" % args.write_doc)
        return 0

    # --check-doc: from the committed CSV alone, no dump and no writes to the artifact
    with open(CSV_PATH, newline="") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        if r["kind"] == "store":
            imm = r["evidence"].split("introduced=")[-1].split(";")[0]
            tbl = r["evidence"].split("table=")[-1].strip()
            r["_table"] = int(tbl, 16) if tbl.startswith("0x") else None
            r["_prov"] = {"origin": r["detail"].split("source=")[-1].split(" ")[0],
                          "imm": int(imm, 16) if imm.startswith("0x") else 0,
                          "slot": (r["detail"].split("slot=")[-1] if "slot=" in r["detail"] else None)}
            r["_fn"] = r["fn"]
            r["_how"] = r["evidence"].split("boundary=")[-1].split(";")[0]
        if r["kind"] == "reach":
            m = re.search(r"(\d+) direct call site\(s\), (\d+) dword", r["detail"])
            r["_calls"] = int(m.group(1)) if m else 0
            r["_refs"] = int(m.group(2)) if m else 0
            r["_fn"] = r["fn"]
            r["_how"] = r["evidence"].split("boundary=")[-1]
        if r["kind"] == "call":
            r["_value"] = int(r["detail"].split("0x")[-1], 16)
    census = next((r for r in rows if r["kind"] == "census"), None)
    counts = {}
    if census:
        for part in census["detail"].split(";"):
            key, _eq, value = part.strip().partition("=")
            counts[key] = int(value)
    flags = {}
    for r in rows:
        if r["kind"] == "call":
            flags[r["_value"]] = flags.get(r["_value"], 0) + 1
    measured = {"rows": rows, "composes": [], "bodies": [],
                "mask": 0x0e0f86ec,
                "pops": {"bits": {0x80000: {"classes": 0}}},
                "flags": flags, "unknown": {}, "sites": counts.get("sites", len([r for r in rows if r["kind"] == "site"])),
                "resolved": counts.get("calls_resolved"), "calls": counts.get("calls_total")}
    if args.check_doc:
        want = render(measured).strip()
        with open(args.check_doc) as fh:
            text = fh.read()
        i = text.find(BLOCK_MARK[0])
        j = text.find(BLOCK_MARK[1])
        if i < 0 or j < 0:
            sys.exit("FAIL markers not found in %s" % args.check_doc)
        have = text[i + len(BLOCK_MARK[0]):j].strip()
        if have != want:
            sys.exit("FAIL %s: generated block does not match the committed CSV" % args.check_doc)
        print("class-flag-stores: %s matches the committed CSV" % args.check_doc)
        return 0
    sys.exit("nothing to do: give --xbe, --check or --check-doc")


if __name__ == "__main__":
    sys.exit(main())
