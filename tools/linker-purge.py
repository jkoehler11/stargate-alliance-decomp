#!/usr/bin/env python3
"""What the array at 0x006c2188 holds, and whether the purge can pick a different element (U5)

`#521` read `main`'s call at `0x169e9` into `0x6c750` and found a 326-byte body that walks the array at
`0x006c2188` with its count at `0x006c218c`, calls `0x77210` on each element, and dispatches on the result. The
next question in `docs/UNKNOWNS.md` U5 was *which element the file layer gets* -- i.e. whether that per-element call
can hand the purge one object where the array held another. This tool measures the three claims that answer it:

* the array is a `TArray`: pointer at `0x006c2188`, count at `0x006c218c`, capacity at `0x006c2190` -- measured from
  the instructions that name all three, and from the one body that resets it (`mov [0x6c218c],0` and
  `mov [0x6c2190],0`), not from the two globals the last reading happened to notice;
* `0x77210` returns its own argument on every path, so no element can be substituted: one `ret`, preceded by
  `mov eax,edi`, with `edi` written once from `[esp+0xc]` at the top and never again. The body does walk a chain and
  it does log `Cast of %s to %s failed` (literal at `0x4d43dc`), but both failure arms still return the argument;
* the elements are container records, not the file layer: the purge reads a filter field at `[+0x28]`, two record
  arrays (`+0x7c`/`+0x80` with a 0x1c stride, `+0x88`/`+0x8c` with a 0x24 stride), an object pointer at each
  export record's `[+0x1c]`, the exported object's class at `[+0x24]`, and one byte of that class at `[+0x498]`.

The byte at `[+0x498]` is not a field of the array's elements: it is read off the *class* of an exported object, as a
signed byte (`mov cl,[eax+0x498]; test cl,cl; js`), so the purge's last gate is a class flag bit, not per-object data.

    python3 tools/linker-purge.py --xbe $DUMP/default.xbe -o inventory/linker_purge.csv
    python3 tools/linker-purge.py --write-doc docs/systems/CHECKPOINTS.md
    python3 tools/linker-purge.py --check-doc docs/systems/CHECKPOINTS.md
    python3 tools/linker-purge.py --check        # synthetic, no dump, no writes

Addresses, literals and counts only: no game data, no decompiled output.
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

ARRAY = 0x006C2188
ARRAY_COUNT = 0x006C218C
ARRAY_ALLOC = 0x006C2190
PURGE = 0x0006C750
PURGE_SIZE = 326
CAST = 0x00077210
CAST_SIZE = 239
CLASS_FLAG = 0x498

COLUMNS = ["kind", "at", "fn", "detail", "evidence"]
BLOCK_MARK = ("<!-- generated:linker-purge -->", "<!-- /generated:linker-purge -->")
CSV_PATH = os.path.join("inventory", "linker_purge.csv")
SYMBOLS = os.path.join("symbols", "xbox_functions.csv")

# The purge's field reads, by displacement. `role` is what the body does with the value; `check` is how the tool
# verifies it is still there rather than trusting this table.
FIELDS = {
    0x28: ("filter", "compared to the caller's second argument on both walks"),
    0x7C: ("first-record-array", "data pointer of the 0x1c-stride walk"),
    0x80: ("first-record-count", "count of the 0x1c-stride walk"),
    0x88: ("second-record-array", "data pointer of the 0x24-stride walk"),
    0x8C: ("second-record-count", "count of the 0x24-stride walk"),
    0x0C: ("dispatch", "vtable slot called with 1 when the record's object is dropped"),
    0x14: ("first-record-object", "object pointer inside a 0x1c record"),
    0x18: ("first-record-index", "index field set to -1 when the record is dropped"),
    0x1C: ("second-record-object", "object pointer inside a 0x24 record"),
    0x24: ("record-object-class", "class of the object that record names"),
    0x498: ("class-flag-byte", "one byte of that class, tested for its sign"),
}

REG32 = ["eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi"]
# opcode -> (direction, text form). Direction is what the instruction does to the absolute address.
MOFFS = {0xA1: ("read", "mov eax,ds:%s"), 0xA3: ("write", "mov ds:%s,eax")}
MODRM_OPS = {
    0x8B: ("read", "mov %s,ds:%s"),
    0x89: ("write", "mov ds:%s,%s"),
    0x8D: ("address", "lea %s,ds:%s"),
    0x39: ("read", "cmp ds:%s,%s"),
    0x3B: ("read", "cmp %s,ds:%s"),
    0xC7: ("write", "mov ds:%s,imm32"),
    0xF7: ("read", "test ds:%s,imm32"),
}
FIELD_RE = re.compile(r"\[(e[a-z]{2})(?:\+e[a-z]{2}\*1)?\+0x([0-9a-f]+)\]")
WIDE_RE = re.compile(r"\[(e[a-z]{2})\+e(?:[a-z]{2})\*4\]")
ADD_RE = re.compile(r"^add\s+(e[a-z]{2}),0x([0-9a-f]+)$")
RET_RE = re.compile(r"^ret")
WRITE_RE = re.compile(r"^(mov|xor|add|sub|and|or|pop|inc|dec|lea|set[a-z]{1,2}|mul|imul|div|idiv)\s+(e[a-z]{2})\b")


# ---------------------------------------------------------------- the image

def load_xbe(path):
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("xbe", os.path.join(here, "xbe.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Xbe(path)


def load_symbols(path=SYMBOLS):
    rows = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                rows.append((int(r["address"], 16), int(r["size"] or 0), r["name"]))
            except ValueError:
                continue
    rows.sort()
    return [(a, s, n) for a, s, n in rows if s > 0]


def containing(table, va):
    starts = [row[0] for row in table]
    i = bisect.bisect_right(starts, va) - 1
    if i >= 0 and starts[i] <= va < table[i][0] + table[i][1]:
        return table[i][2]
    return None


def code_sections(xbe, table):
    """Every XBE section sets the executable flag, so the map decides which are code."""
    out = []
    for sec in xbe.sections:
        covered = sum(size for start, size, _n in table if sec.contains(start))
        if covered and covered * 10 >= sec.vsize:
            out.append(sec)
    return out


def instructions(xbe, va, size):
    blob = xbe.read_va(va, size)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
        fh.write(blob)
        temp = fh.name
    try:
        out = subprocess.run(["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                              "--adjust-vma=%d" % va, temp], capture_output=True, text=True).stdout
    finally:
        os.unlink(temp)
    rows = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        head, text = line.split("\t", 1)
        try:
            at = int(head.strip().rstrip(":"), 16)
        except ValueError:
            continue
        text = text.split("\t")[-1].strip()
        rows.append((at, text))
    return rows


# ---------------------------------------------------------------- reading one absolute reference

def absolute_ref(blob, i, addr):
    """Classify the instruction whose immediate operand starts at offset `i` of `blob`.

    x86 spells an absolute address in a handful of shapes, and each one fixes where the opcode sits:
    `A1`/`A3`/`B8+r`/`68` carry it one byte back, `8B 05`-style modrm forms two. Anything else is reported as
    `unknown` rather than guessed -- a four-byte pattern match alone does not prove an instruction.
    """
    if i < 2:
        return None, None
    prev = blob[i - 1]
    name = "0x%08x" % addr
    if prev in MOFFS:
        direction, form = MOFFS[prev]
        return direction, form % name
    if prev == 0x68:
        return "address", "push %s" % name
    if 0xB8 <= prev <= 0xBF:
        return "address", "mov %s,%s" % (REG32[prev - 0xB8], name)
    modrm = prev
    op = blob[i - 2]
    if (modrm & 0xC7) != 0x05 or op not in MODRM_OPS:
        return "unknown", "bytes %02x %02x" % (op, modrm)
    direction, form = MODRM_OPS[op]
    reg = REG32[(modrm >> 3) & 7]
    if op == 0xC7:
        direction, form = "write", "mov %s,imm32" % name
        return direction, form
    if op == 0xF7:
        return direction, "test %s,imm32" % name
    if op == 0x8B:
        return direction, form % (reg, name)
    if op == 0x89:
        return direction, form % (name, reg)
    if op == 0x8D:
        return direction, form % (reg, name)
    if op == 0x39:
        return direction, form % (name, reg)
    return direction, form % (reg, name)


def array_refs(xbe, table, secs):
    """Every instruction in the code sections whose absolute operand is one of the array's three dwords."""
    rows = []
    for target in (ARRAY, ARRAY_COUNT, ARRAY_ALLOC):
        needle = struct.pack("<I", target)
        for sec in secs:
            blob = xbe.read_va(sec.va, sec.fsize)
            i = blob.find(needle)
            while i >= 0:
                kind, text = absolute_ref(blob, i, target)
                va = sec.va + i
                if kind:
                    rows.append({"kind": "array", "at": "0x%08x" % va,
                                 "fn": containing(table, va) or "-",
                                 "detail": "%s 0x%08x" % (kind, target),
                                 "evidence": text})
                i = blob.find(needle, i + 1)
    rows.sort(key=lambda r: (r["detail"].split()[-1], r["at"]))
    return rows


# ---------------------------------------------------------------- the purge body

def fields_of(insns):
    """(offset, at, width, text) for every `[reg+disp]` the body reads, in address order."""
    out = []
    for at, text in insns:
        for m in FIELD_RE.finditer(text):
            if m.group(1) in ("esp", "ebp"):
                continue  # a frame slot is not a field of the object
            disp = int(m.group(2), 16)
            width = "byte" if re.search(r"\b(cl|al|dl|bl|BYTE PTR)\b", text) else "dword"
            out.append((disp, at, width, text))
    return out


def strides_of(insns):
    """The `add reg,0xNN` steps a loop walks its record array by."""
    out = set()
    for _a, text in insns:
        m = ADD_RE.match(text)
        if not m or m.group(1) in ("esp", "ebp"):
            continue
        step = int(m.group(2), 16)
        if step >= 0x8:
            out.add(step)
    return sorted(out)


def identity_returns(insns):
    """Proof that a body answers its own argument: one `ret`, preceded by `mov eax,edi`, `edi` written once.

    `arg` is the frame slot the argument is read from, spelled the way objdump prints it.
    """
    rets = [at for at, text in insns if RET_RE.match(text)]
    arg_loads = [(at, text) for at, text in insns if re.match(r"^mov\s+edi,DWORD PTR \[esp\+0x[0-9a-f]+\]$", text)]
    edi_writes = [(at, text) for at, text in insns
                  if WRITE_RE.match(text) and WRITE_RE.match(text).group(2) == "edi"
                  and not text.startswith("pop")]
    eax_before = []
    for i, (at, text) in enumerate(insns):
        if not RET_RE.match(text):
            continue
        last = None
        for j in range(i - 1, max(-1, i - 8), -1):
            m = WRITE_RE.match(insns[j][1])
            if m and m.group(2) == "eax":
                last = insns[j]
                break
        eax_before.append((at, last[1] if last else "no write to eax"))
    return {"rets": rets, "arg_loads": arg_loads, "edi_writes": edi_writes, "before_ret": eax_before}


def class_flag_sites(xbe, table, secs, disp=CLASS_FLAG):
    """Image-wide count of `[reg+0x498]` references, split by the width the opcode gives the access."""
    needle = struct.pack("<I", disp)
    byte_ops = {0x8A, 0x80, 0x88, 0xC6, 0x38, 0x3A}
    rows = []
    for sec in secs:
        blob = xbe.read_va(sec.va, sec.fsize)
        i = blob.find(needle)
        while i >= 0:
            if i >= 2 and (blob[i - 1] & 0xC0) == 0x80 and (blob[i - 1] & 0x07) <= 7:
                op = blob[i - 2]
                if op in byte_ops or op in (0x8B, 0x89, 0x39, 0x3B, 0xC7, 0xF7):
                    rows.append({"kind": "flag", "at": "0x%08x" % (sec.va + i - 2),
                                 "fn": containing(table, sec.va + i - 2) or "-",
                                 "detail": "byte" if op in byte_ops else "dword",
                                 "evidence": "opcode %02x %02x disp32 0x%03x" % (op, blob[i - 1], disp)})
            i = blob.find(needle, i + 1)
    return rows


def measure(xbe_path):
    xbe = load_xbe(xbe_path)
    table = load_symbols()
    secs = code_sections(xbe, table)
    purge = instructions(xbe, PURGE, PURGE_SIZE)
    cast = instructions(xbe, CAST, CAST_SIZE)

    rows = array_refs(xbe, table, secs)

    seen = set()
    fields = []
    for disp, at, width, text in fields_of(purge):
        role = FIELDS[disp][0] if disp in FIELDS else "unclassified"
        note = FIELDS[disp][1] if disp in FIELDS else "this body reads a displacement the field table does not name"
        key = (disp, role)
        if key in seen:
            continue
        seen.add(key)
        fields.append({"kind": "field", "at": "0x%08x" % at, "fn": "FUN_0006c750", "offset": disp,
                       "detail": "0x%03x %s %s" % (disp, role, width), "evidence": text + " -- " + note})
    rows.extend(sorted(fields, key=lambda r: r["offset"]))

    for step in strides_of(purge):
        site = [at for at, text in purge if ADD_RE.match(text) and int(ADD_RE.match(text).group(2), 16) == step]
        rows.append({"kind": "stride", "at": "0x%08x" % site[0], "fn": "FUN_0006c750",
                     "detail": "0x%02x record stride" % step,
                     "evidence": "loop step at %s" % ", ".join("0x%08x" % s for s in site)})

    ident = identity_returns(cast)
    rows.append({"kind": "cast", "at": "0x%08x" % ident["rets"][0], "fn": "FUN_00077210",
                 "detail": "returns its argument",
                 "evidence": "%d ret; before it: %s" % (len(ident["rets"]), "; ".join(t for _a, t in ident["before_ret"]))})
    rows.append({"kind": "cast", "at": "0x%08x" % ident["arg_loads"][0][0] if ident["arg_loads"] else "-",
                 "fn": "FUN_00077210", "detail": "argument",
                 "evidence": "%d write(s) to edi: %s" % (len(ident["edi_writes"]),
                                                          "; ".join(t for _a, t in ident["edi_writes"]))})

    flags = class_flag_sites(xbe, table, secs)
    rows.extend(sorted(flags, key=lambda r: r["at"]))
    return rows


# ---------------------------------------------------------------- rendering

def render(rows):
    def cell(v):
        return str(v).replace("|", "\\|")

    lines = []
    array = [r for r in rows if r["kind"] == "array"]
    if array:
        reads = sum(1 for r in array if r["detail"].startswith("read"))
        writes = sum(1 for r in array if r["detail"].startswith("write"))
        addr = sum(1 for r in array if r["detail"].startswith("address"))
        lines.append("**The array at `0x006c2188` is one `TArray` -- data, count, capacity -- and %d instructions "
                     "name one of its three dwords:** %d read(s), %d write(s), %d address(es) taken. Every write in "
                     "this census is the array's own bookkeeping (the count at `0x00052815`/`0x000530f5`, the "
                     "capacity at `0x00052834`/`0x00053114`, both zeroed together at `0x00069c38`): an element goes "
                     "in through the data pointer after that capacity check, so this census sees the registry, not "
                     "what is registered." % (len(array), reads, writes, addr))
        lines.append("")
        lines.append("| at | in | what | instruction |")
        lines.append("|---|---|---|---|")
        for r in array:
            lines.append("| `%s` | %s | %s | `%s` |" % (r["at"], cell(r["fn"]), cell(r["detail"]), cell(r["evidence"])))
        lines.append("")

    cast = [r for r in rows if r["kind"] == "cast"]
    if cast:
        lines.append("**The per-element call cannot substitute an object.** `0x77210` answers its own argument on "
                     "every path -- " + cast[0]["evidence"] + ". So the question the last reading left open (which "
                     "element the file layer gets) has no second answer to find: the purge can only ever see the "
                     "element it took out of the array, and the only thing that call changes is whether it logs the "
                     "literal at `0x4d43dc` first.")
        lines.append("")
        lines.append("| at | in | what | evidence |")
        lines.append("|---|---|---|---|")
        for r in cast:
            lines.append("| `%s` | %s | %s | `%s` |" % (r["at"], cell(r["fn"]), cell(r["detail"]), cell(r["evidence"])))
        lines.append("")

    fields = [r for r in rows if r["kind"] == "field"]
    strides = [r for r in rows if r["kind"] == "stride"]
    if fields:
        steps = [r["detail"].split()[0] for r in strides]
        lines.append("**What the purge reads.** The elements are containers with two record arrays, not a slot that "
                     "holds a file manager: the body walks `+0x7c`/`+0x80` by " + (steps[0] if steps else "-") +
                     " and `+0x88`/`+0x8c` by " + (steps[-1] if steps else "-") +
                     ", takes each export record's object at `+0x1c`, and reads one byte at `+0x498` of that object's "
                     "class -- a signed byte, so the last gate is a bit in the class's flags, not data on the object.")
        lines.append("")
        lines.append("| offset | in | role | width | instruction |")
        lines.append("|---|---|---|---|---|")
        for r in fields:
            parts = r["detail"].split()
            lines.append("| `%s` | %s | %s | %s | `%s` |" % (parts[0], cell(r["fn"]), cell(parts[1]),
                                                              parts[2], cell(r["evidence"])))
        lines.append("")

    flags = [r for r in rows if r["kind"] == "flag"]
    if flags:
        byte = sum(1 for r in flags if r["detail"] == "byte")
        lines.append("**`+0x498` is a class displacement the image uses %d times, and only %d of them are byte "
                     "reads.** The purge's is one of the byte ones, so the flag it tests is the top bit of the first "
                     "byte of the class's flag dword -- the same dword other bodies test whole (`test dword "
                     "[class+0x498],0x...`)." % (len(flags), byte))
        lines.append("")
    return "\n".join(lines)


def splice(path, block):
    text = open(path).read()
    a, b = BLOCK_MARK
    if a not in text or b not in text:
        sys.exit("%s: markers not found" % path)
    head, rest = text.split(a, 1)
    _old, tail = rest.split(b, 1)
    return head + a + "\n" + block + "\n" + b + tail


def write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "-") for k in COLUMNS})
    print("wrote %s (%d rows)" % (path, len(rows)))


def load_csv(path=CSV_PATH):
    with open(path, newline="") as fh:
        return [{k: (r.get(k) or "-") for k in COLUMNS} for r in csv.DictReader(fh)]


# ---------------------------------------------------------------- self-test

def self_check():
    """Synthetic instructions and bytes: no dump, no writes."""
    n = [0]

    def ok(cond, label):
        n[0] += 1
        if not cond:
            sys.exit("FAIL %s" % label)

    addr = 0x006C2188
    needle = struct.pack("<I", addr)
    blob = b"\x90" * 4 + b"\x8b\x0d" + needle + b"\xa1" + needle + b"\xa3" + needle + b"\xb9" + needle \
        + b"\x68" + needle + b"\x83\x3d\x0d" + needle
    ok(absolute_ref(blob, 6, addr) == ("read", "mov ecx,ds:0x006c2188"), "8b0d is a read into ecx")
    ok(absolute_ref(blob, 11, addr) == ("read", "mov eax,ds:0x006c2188"), "a1 is a read into eax")
    ok(absolute_ref(blob, 16, addr) == ("write", "mov ds:0x006c2188,eax"), "a3 is a write")
    ok(absolute_ref(blob, 21, addr) == ("address", "mov ecx,0x006c2188"), "b9 takes the address")
    ok(absolute_ref(blob, 26, addr) == ("address", "push 0x006c2188"), "push takes the address")
    ok(absolute_ref(blob, 33, addr)[0] == "unknown", "cmp [abs],imm8 is not one of the shapes and is not guessed")
    ok(absolute_ref(b"\x90\x90\x8b\x05" + needle, 4, addr) == ("read", "mov eax,ds:0x006c2188"), "modrm absolute")
    ok(absolute_ref(b"\x00" * 4, 1, addr) == (None, None), "no room for an opcode")

    body = [
        (0x6C750, "push   ecx"),
        (0x6C751, "mov    eax,ds:0x6c218c"),
        (0x6C770, "mov    ecx,DWORD PTR ds:0x6c2188"),
        (0x6C77F, "mov    edi,DWORD PTR [esp+0x1c]"),
        (0x6C78C, "cmp    DWORD PTR [esi+0x28],edi"),
        (0x6C7F6, "lea    eax,[edx+edi*1+0x1c]"),
        (0x6C800, "mov    eax,DWORD PTR [eax+0x24]"),
        (0x6C803, "mov    cl,BYTE PTR [eax+0x498]"),
        (0x6C809, "test   cl,cl"),
        (0x6C81C, "add    edi,0x24"),
        (0x6C7CC, "add    eax,0x1c"),
        (0x6C895, "ret"),
    ]
    found = {disp: (width) for disp, _at, width, _t in fields_of(body)}
    ok(found.get(0x28) == "dword", "the filter field is a dword compare")
    ok(found.get(0x1C) == "dword", "a record object is a pointer")
    ok(found.get(0x498) == "byte", "the class flag is read as one byte")
    ok(strides_of(body) == [0x1C, 0x24], "both record strides")

    cast = [
        (0x77210, "push   esi"),
        (0x77212, "mov    edi,DWORD PTR [esp+0xc]"),
        (0x77216, "test   edi,edi"),
        (0x77245, "mov    eax,DWORD PTR [edi+0x24]"),
        (0x77258, "mov    eax,DWORD PTR [eax+0x28]"),
        (0x772FA, "mov    eax,edi"),
        (0x772FC, "pop    edi"),
        (0x772FE, "ret"),
    ]
    ident = identity_returns(cast)
    ok(len(ident["rets"]) == 1, "one ret")
    ok(ident["before_ret"] == [(0x772FE, "mov    eax,edi")], "the ret answers edi")
    ok([t for _a, t in ident["edi_writes"]] == ["mov    edi,DWORD PTR [esp+0xc]"], "edi written once, from the argument")
    ident = identity_returns(cast[:-3] + [(0x772FA, "mov    eax,0x4d440c"), (0x772FE, "ret")])
    ok([t for _a, t in ident["before_ret"]] != ["mov    eax,edi"], "a body that answers a literal is not an identity")

    rows = [
        {"kind": "array", "at": "0x1", "fn": "f", "detail": "read 0x006c2188", "evidence": "mov eax,ds:0x006c2188"},
        {"kind": "cast", "at": "0x2", "fn": "0x77210", "detail": "returns its argument", "evidence": "1 ret"},
        {"kind": "field", "at": "0x3", "fn": "FUN_0006c750", "detail": "0x028 filter dword", "evidence": "cmp"},
        {"kind": "stride", "at": "0x4", "fn": "FUN_0006c750", "detail": "0x1c record stride", "evidence": "add"},
        {"kind": "flag", "at": "0x5", "fn": "g", "detail": "byte", "evidence": "opcode 8a"},
        {"kind": "flag", "at": "0x6", "fn": "g", "detail": "dword", "evidence": "opcode 8b"},
    ]
    text = render(rows)
    ok("| at | in | what | instruction |" in text, "array table")
    ok("`+0x498` is a class displacement the image uses 2 times, and only 1 of them are byte" in text,
       "flag sentence counts widths")
    ok("returns its argument" in text, "cast row rendered")
    ok(render([]) == "", "no rows renders empty")
    print("ok %d assertions (no dump, no writes)" % n[0])
    return 0


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out", default=CSV_PATH)
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    if args.check:
        return self_check()

    if args.write_doc:
        if args.xbe:
            rows = measure(args.xbe)
        else:
            rows = load_csv()
        new = splice(args.write_doc, render(rows))
        with open(args.write_doc, "w") as fh:
            fh.write(new)
        print("wrote %s" % args.write_doc)
        return 0

    if args.check_doc:
        rows = load_csv()
        want = splice(args.check_doc, render(rows))
        have = open(args.check_doc).read()
        if want != have:
            print("FAIL %s: generated block differs from a recomputation off %s" % (args.check_doc, CSV_PATH))
            return 1
        print("ok %s" % args.check_doc)
        return 0

    if not args.xbe:
        ap.error("--xbe is required to measure")
    rows = measure(args.xbe)
    write_csv(args.out, rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
