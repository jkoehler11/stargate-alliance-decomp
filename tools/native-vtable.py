#!/usr/bin/env python3
"""Census a native (non-UObject) vtable in default.xbe: extent, slots, boundaries, dispatch.

Usage:
    native-vtable.py DUMP/default.xbe --va 0x53f770 [--symbols symbols/xbox_functions.csv]
                     [-o inventory/native_vtable_census.csv] [--json]
    native-vtable.py DUMP/default.xbe --block [-o inventory/native_vtable_merges.csv]
    native-vtable.py DUMP/default.xbe --reach [-o inventory/native_vtable_reach.csv]
    native-vtable.py --check          # synthetic self-test, no dump, writes nothing

Why this exists
---------------
The 32-byte helper the `.data` singleton at `0x7b9ba0` keeps at `+0x220` (U8, 2026-11-12) installs
vtable `0x0053f770`.  That table is a run of dwords in `.rdata` that point into `.text`, and the
obvious reading -- "count code pointers until one is not code" -- answers **26**.  It is wrong:
`0x53f7c4`, five dwords before the run ends, has its own install store (`mov [eax],0x53f7c4` at
`0x3313cc`, inside the body of the previous table's slot 0), so the run is **two classes**, 21 slots
and 5, laid out adjacently by the linker.  A table cannot contain another table's start, and only a
store census can see the split: every address in the run is a perfectly good function start.

A slot's boundary is measured three independent ways, cheapest first:

* `map`  -- the address is an exact function start in `symbols/xbox_functions.csv`;
* `call` -- a direct `E8 rel32` call lands exactly on it (so some body names it as a function);
* `tail` -- the bytes ending one past it decode as a terminator (`ret`, `ret imm16`, `int3` fill,
  `jmp rel8/rel32`, `jmp [reg]` / `jmp [reg+disp]` with the displacement included).

`tail` is the rule that carries this table: 8 of its 21 slots are in the map, **0** are call
targets, and all 21 pass `tail`.  So a census that required a known function start would have named
under half the slots and left the extent undefined -- which is how the 26 survived.

Each slot body is then read for its own dispatch: a body that calls `[reg+disp]` is dispatching
another object's table at that slot index, which is how this table's slots 17/18 come to call
`[eax+0x4c]` -- U8's open pair -- from inside an interface family rather than from game code.
"""
import argparse
import csv
import json
import os
import re
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import xbe as X  # noqa: E402


# ---------------------------------------------------------------- boundaries

def terminator(buf):
    """Label the instruction that ends exactly at the end of `buf`, or '' if none does.

    Tests run longest-first so a `ret imm16` is not read as its last byte, and a `jmp [reg(+disp)]`
    is only accepted when its displacement bytes are inside the window -- a modrm alone says
    nothing about where the instruction ends.
    """
    b, n = buf, len(buf)
    if n >= 1 and b[-1] in (0xC3, 0xCB):
        return "ret"
    if n >= 1 and b[-1] == 0xCC:
        return "int3"
    if n >= 3 and b[-3] == 0xC2:
        return "retn"
    if n >= 5 and b[-5] == 0xE9:
        return "jmp-rel32"
    if n >= 2 and b[-2] == 0xEB:
        return "jmp-rel8"
    for back in (2, 3, 6):
        j = n - back
        if j < 0 or b[j] != 0xFF:
            continue
        modrm = b[j + 1]
        if ((modrm >> 3) & 7) != 4:          # /4 = jmp r/m32
            continue
        mod, rm = modrm >> 6, modrm & 7
        need = 2 if (mod == 0 and rm != 5) else (3 if mod == 1 else 6)
        if need == back:
            return "jmp-mem"
    return ""


def store_prefix_len(blob, op):
    """Bytes from a `C7 /0` opcode at `op` to its immediate, or None if that is not a memory store.

    Every mod/rm form stores 32 bits; what varies is the addressing bytes in between:

        mod 00, rm != 4,5     2   (disp32 absolute is `mod 00 rm 5` -> 6)
        mod 01                3   (rbp/ebp + disp8 included -- the rm==5 special case is mod 00 only)
        mod 10                6
        rm == 4 (SIB)        +1, and +4 more when mod 00 and the SIB base is 5 (no base register)

    Reading only mod 00/01/10 without a SIB byte answers 2/3/6 and silently drops every
    `mov [esp+disp8],imm32` and `mov [ebp+disp8],imm32` store in the image.
    """
    if op < 0 or op + 2 > len(blob) or blob[op] != 0xC7:
        return None
    modrm = blob[op + 1]
    if (modrm >> 3) & 7:
        return None
    mod, rm = modrm >> 6, modrm & 7
    n = 2
    if rm == 4:
        if op + 2 >= len(blob):
            return None
        if mod == 0 and (blob[op + 2] & 7) == 5:
            n += 4
        n += 1
    if mod == 1:
        n += 1
    elif mod == 2:
        n += 4
    elif mod == 0 and rm == 5:
        n += 4
    return n


def immediate_store(blob, i, value):
    """True when blob[i:i+4] is the immediate of a `mov [reg(+disp)(+index)], imm32` storing `value`.

    A `push imm32`, a table entry or a compare is not an install.  The addressing form is decided
    by `store_prefix_len`.
    """
    if i < 2 or i + 4 > len(blob):
        return False
    if struct.unpack_from("<I", blob, i)[0] != value:
        return False
    for back in range(2, 8):
        if store_prefix_len(blob, i - back) == back:
            return True
    return False


def find_installs(xbe, lo, hi):
    """VA -> [install site VAs] for every immediate store of an aligned address in [lo, hi)."""
    out = {}
    for s in xbe.sections:
        if not s.executable:
            continue
        blob = xbe.data[s.foff:s.foff + min(s.fsize, s.vsize)]
        for i in range(len(blob) - 4):
            v = struct.unpack_from("<I", blob, i)[0]
            if v % 4 or not (lo <= v < hi):
                continue
            if immediate_store(blob, i, v):
                out.setdefault(v, []).append(s.va + i)
    return out


def table_run(xbe, va, code_section, limit=400):
    """Dwords from `va` while they land in `code_section`."""
    out, cur = [], va
    while len(out) < limit:
        try:
            d = struct.unpack("<I", xbe.read_va(cur, 4))[0]
        except KeyError:
            break
        if not code_section.contains(d):
            break
        out.append(d)
        cur += 4
    return out


# ---------------------------------------------------------------- the census

def body_extent(insns, start, limit=512):
    """Walk decoded instructions from `start` to the first terminator; return its end address.

    A slot body ends where control leaves it, so a body is bounded by instruction flow, not by the
    next address in the table (slot order in a vtable says nothing about layout in .text).
    """
    a = start
    while a < start + limit:
        ins = insns.get(a)
        if ins is None:
            return a
        op, args, n = ins
        a += max(n, 1)
        if op in ("ret", "retf"):
            return a
        if op == "jmp":
            return a
        if op in ("hlt", "ud2"):
            return a
    return a


def census(xbe, va, names, insns, calls):
    first = struct.unpack("<I", xbe.read_va(va, 4))[0]
    code = xbe.section_for(first)
    if code is None or not code.executable:
        raise SystemExit(f"{va:#x}: first slot {first:#x} is not a code pointer")
    slots = table_run(xbe, va, code)
    run_lo, run_hi = va, va + 4 * len(slots)
    installs = find_installs(xbe, run_lo, run_hi)
    starts = sorted({v for v in installs if run_lo <= v < run_hi} | {va})
    rows = []
    for k, tva in enumerate(starts):
        end_limit = starts[k + 1] if k + 1 < len(starts) else run_hi
        count = (end_limit - tva) // 4
        for slot in range(count):
            sva = struct.unpack("<I", xbe.read_va(tva + 4 * slot, 4))[0]
            end = body_extent(insns, sva) if sva in insns else sva
            size = end - sva
            flags = []
            if sva in names:
                flags.append("map")
            if sva in calls:
                flags.append("call")
            term = terminator(xbe.read_va(sva - 16, 16))
            if term:
                flags.append("tail")
            dispatch, a = [], sva
            while a < end:
                ins = insns.get(a)
                if not ins:
                    break
                op, args, n = ins[0], ins[1], ins[2]
                if op in ("call", "jmp") and "PTR [" in args:
                    dispatch.append(("jmp " if op == "jmp" else "")
                                    + args.replace("DWORD PTR ", ""))
                a += max(n, 1)
            rows.append({
                "table_va": f"{tva:#010x}",
                "table_slots": count,
                "slot": slot,
                "slot_va": f"{sva:#010x}",
                "name": names.get(sva, ""),
                "boundary": "+".join(flags) or "none",
                "tail": term,
                "body_bytes": size,
                "dispatch": " ".join(dispatch),
                "install_site": " ".join(f"{s:#010x}" for s in installs[tva]),
            })
    return rows, starts


def sweep(xbe, code):
    """objdump's linear sweep of one code section: {va: (op, args, length)} and call targets."""
    blob = xbe.data[code.foff:code.foff + min(code.fsize, code.vsize)]
    path = os.path.join(os.environ.get("TMPDIR", "/tmp"), "sg1-native-vtable-sweep.bin")
    with open(path, "wb") as fh:
        fh.write(blob)
    try:
        out = subprocess.run(["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                              f"--adjust-vma={code.va:#x}", path],
                             capture_output=True, text=True, check=True).stdout
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    pat = re.compile(r"^\s*([0-9a-f]+):\t([0-9a-f ]+)\t(\S+)\s*(.*)$")
    insns, calls = {}, {}
    for line in out.splitlines():
        m = pat.match(line)
        if not m:
            continue
        a, raw, op, args = int(m.group(1), 16), m.group(2).split(), m.group(3), m.group(4).strip()
        insns[a] = (op, args, len(raw))
        if op == "call":
            mm = re.match(r"0x([0-9a-f]+)$", args)
            if mm:
                calls.setdefault(int(mm.group(1), 16), []).append(a)
    # body length = distance to the next decoded instruction (the raw byte count under-reports
    # where objdump keeps a suffix on its own line)
    addrs = sorted(insns)
    for i, a in enumerate(addrs):
        nxt = addrs[i + 1] if i + 1 < len(addrs) else a + 16
        op, args, rawlen = insns[a]
        insns[a] = (op, args, max(nxt - a, rawlen))
    return insns, calls


def load_symbols(path):
    names = {}
    if not os.path.exists(path):
        return names
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            names[int(r["address"], 16)] = r["name"]
    return names



def install_census(xbe, data):
    """{table_va: [install site VAs]} for every immediate store into `data` from executable code."""
    installs = {}
    for s in xbe.sections:
        if not s.executable:
            continue
        blob = xbe.data[s.foff:s.foff + min(s.fsize, s.vsize)]
        for i in range(len(blob) - 4):
            v = struct.unpack_from("<I", blob, i)[0]
            if v % 4 or not data.contains(v):
                continue
            if immediate_store(blob, i, v):
                installs.setdefault(v, []).append(s.va + i)
    return installs


def block_scan(xbe, section_name=".rdata", min_slots=3):
    """Every run of >= `min_slots` code pointers in one data section, split at install starts.

    A linker packs class vtables back to back with no delimiter, so "code pointers until one is not
    code" merges neighbours: this walk keeps the run and the install starts inside it, which is the
    only evidence that a run holds more than one table.
    """
    text = next((s for s in xbe.sections if s.name == ".text"), None)
    data = next((s for s in xbe.sections if s.name == section_name), None)
    if text is None or data is None:
        raise SystemExit(f"{section_name}: section not found")
    installs = install_census(xbe, data)
    n = data.vsize // 4
    vals = struct.unpack_from("<%dI" % n, xbe.data, data.foff)
    rows, i = [], 0
    while i < n:
        if not text.contains(vals[i]):
            i += 1
            continue
        j = i
        while j < n and text.contains(vals[j]):
            j += 1
        if j - i >= min_slots:
            run_va, slots = data.va + i * 4, j - i
            ends = sorted(s for s in installs if run_va <= s < run_va + 4 * slots)
            bounds = ends + [run_va + 4 * slots]
            counts = [(b - a) // 4 for a, b in zip(ends, ends[1:] + [bounds[-1]])] if ends else [slots]
            rows.append({
                "run_va": f"{run_va:#010x}",
                "run_slots": slots,
                "tables": len(ends) if ends else 0,
                "first_is_table": "yes" if run_va in installs else "no",
                "slot_counts": " ".join(str(c) for c in counts),
                "install_sites": " ".join(f"{s:#010x}" for s in ends),
            })
        i = j
    return rows


# ---------------------------------------------------------------- literal dispatch

REGISTERS = ("eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi")


def imm_values(args):
    """Every `0x...` literal in an operand string, as ints (objdump intel syntax)."""
    return [int(m, 16) for m in re.findall(r"0x([0-9a-f]+)", args)]


def ref_kind(op, args):
    """How one instruction uses a table VA: `install`, `load`, `lea`, `push`, `absolute`, `other`.

    `install` is `mov [mem],imm32` -- the store that makes an object point at the table.  `load`
    and `lea` are the two ways code puts the table's *address* in a register, which is the only
    form a literal `call [reg+disp]` can dispatch through; `push` passes it as an argument;
    `absolute` is `call`/`jmp DWORD PTR ds:<slot>`, a dispatch that names a slot by address.  Any
    other instruction (a `cmp`, an `add`) mentions it without using it as a table.
    """
    if op in ("call", "jmp") and "PTR ds:" in args:
        return "absolute"
    if op == "mov":
        return "install" if "PTR " in args else "load"
    if op == "lea":
        return "lea"
    if op == "push":
        return "push"
    return "other"


def written_register(op, args):
    """The register a `mov`/`lea`/`xor`/`pop` writes, or '' when it writes memory or is not one."""
    if "PTR " in args or not args:
        return ""
    head = args.split(",", 1)[0].strip()
    if head not in REGISTERS:
        return ""
    return head if op in ("mov", "lea", "xor", "pop", "movzx", "and", "or", "add", "sub") else ""


def dispatch_disp(args, reg):
    """Slot displacement of `call [reg+disp]`, 0 for `call [reg]`, or None.

    Only the memory form counts: a `call eax` whose eax is a table VA would be a call into
    `.rdata`, which is not code, so a bare register call is not a dispatch through a table.
    """
    m = re.match(r"DWORD PTR \[%s\]$" % reg, args)
    if m:
        return 0
    m = re.match(r"DWORD PTR \[%s([+-]0x[0-9a-f]+)\]$" % reg, args)
    if m:
        try:
            return int(m.group(1), 16)
        except ValueError:
            return None
    return None


def reach_scan(xbe, tables, insns, functions, mapped, classes, installs=None, window=24):
    """One row per table start: how `.text` mentions it, and whether a mention is a dispatch.

    For every instruction whose operand holds a table VA the reference is classified by
    `ref_kind`.  A `load`/`lea` that puts the table in a register is followed forward (at most
    `window` instructions, stopping when the register is rewritten or control leaves) for
    `call [reg+disp]`: that is a dispatch through a literally-known table, and `disp//4` is the
    slot it calls.  `installs` (site lists from `install_census`) adds the store sites, the
    bodies that hold them, and the classes whose tables share one of those bodies.
    """
    by_site = {}
    starts = set(tables)
    slot_map = {}
    for tva, n in tables.items():
        for k in range(n):
            slot_map[tva + 4 * k] = (tva, k)
    absolute = {}
    for a in sorted(insns):
        op, args, n = insns[a]
        for v in imm_values(args):
            if v in starts:
                by_site.setdefault(v, []).append((a, op, args, n))
        if op in ("call", "jmp") and "PTR ds:" in args:
            for v in imm_values(args):
                hit = slot_map.get(v)
                if hit:
                    absolute.setdefault(hit[0], []).append((a, hit[1], op))
    rows = []
    for tva in sorted(starts):
        refs = by_site.get(tva, [])
        kinds = {"install": 0, "load": 0, "lea": 0, "push": 0, "absolute": 0, "other": 0}
        other_ops, into, disp_list, slots, sites, funcs = set(), set(), [], [], [], []
        for a, op, args, n in refs:
            kind = ref_kind(op, args)
            kinds[kind] += 1
            if kind == "other":
                other_ops.add(op)
                continue
            if kind not in ("load", "lea"):
                continue
            reg = args.split(",", 1)[0].strip()
            if reg not in REGISTERS:
                continue
            into.add(reg)
            sites.append(a)
            cur = a + max(n, 1)
            for _ in range(window):
                ins = insns.get(cur)
                if ins is None:
                    break
                iop, iargs, ilen = ins
                if iop in ("ret", "retf", "jmp"):
                    break
                if iop == "call":
                    d = dispatch_disp(iargs, reg)
                    if d is not None:
                        disp_list.append((reg, d))
                        slots.append(d // 4 if d % 4 == 0 else -1)
                        funcs.append(function_at(functions, a))
                    break
                if written_register(iop, iargs) == reg:
                    break
                cur += max(ilen, 1)
        rows.append({
            "table_va": f"{tva:#010x}",
            "table_slots": tables.get(tva, 0),
            "mapped": "yes" if tva in mapped else "no",
            "class": classes.get(tva, ""),
            "refs": len(refs),
            "installs": kinds["install"],
            "loads": kinds["load"],
            "lea": kinds["lea"],
            "pushes": kinds["push"],
            "absolute": kinds["absolute"],
            "absolute_slots": " ".join(str(k) for _, k, _ in absolute.get(tva, [])),
            "absolute_sites": " ".join(f"{a:#010x}" for a, _, _ in absolute.get(tva, [])),
            "other": kinds["other"],
            "other_ops": " ".join(sorted(other_ops)),
            "loads_into": " ".join(sorted(into)),
            "dispatch": " ".join(f"{r}+{d:#x}" for r, d in disp_list),
            "dispatch_slots": " ".join(str(s) for s in slots),
            "dispatch_functions": " ".join(sorted({f for f in funcs if f})),
            "load_sites": " ".join(f"{s:#010x}" for s in sites),
        })
    fn_tables = {}
    for tva, sl in (installs or {}).items():
        for sp in sl:
            fn_tables.setdefault(function_at(functions, sp), set()).add(tva)
    for row in rows:
        tva = int(row["table_va"], 16)
        sites = (installs or {}).get(tva, [])
        bodies = sorted({function_at(functions, s) for s in sites})
        shared = set()
        for b in bodies:
            if not b:
                continue
            for other in fn_tables.get(b, ()):
                if other != tva and other in mapped:
                    shared.add(f"{other:#010x}:{classes.get(other, '')}".rstrip(":"))
        row["install_sites"] = " ".join(f"{s:#010x}" for s in sites)
        row["install_bodies"] = " ".join(b or "-" for b in bodies)
        row["shared_classes"] = " ".join(sorted(shared))
    return rows


def function_at(functions, va):
    """Name of the innermost sized function body containing `va`, or ''."""
    best, best_start = "", -1
    for start, size, name in functions:
        if size and start <= va < start + size and start > best_start:
            best, best_start = name, start
    return best


def table_starts(rows):
    """{table_va: slots} for every install-started table in a block_scan result."""
    out = {}
    for r in rows:
        starts = [int(t, 16) for t in r["install_sites"].split()]
        counts = [int(c) for c in r["slot_counts"].split()]
        for va, n in zip(starts, counts):
            out[va] = n
    return out


def load_function_spans(path):
    """[(address, size, name)] from the symbol map, for naming a reference's body."""
    out = []
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                addr = int(r["address"], 0)
            except (KeyError, ValueError):
                continue
            try:
                size = int(r.get("size") or 0, 0)
            except ValueError:
                size = 0
            out.append((addr, size, r.get("name") or ""))
    return out


def load_table_rows(path, key="table_va"):
    """{table_va: row} from a committed class-table inventory (mapped tables)."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                out[int(r[key], 16)] = r
            except (KeyError, ValueError):
                continue
    return out


# ---------------------------------------------------------------- self-test

def cmd_check():
    """Drive both byte rules against synthetic bytes -- no dump, no writes to the tree."""
    fails = []

    def eq(what, got, want):
        if got != want:
            fails.append(f"{what}: got {got!r}, want {want!r}")

    pad = bytes(16)
    eq("ret", terminator(pad[:15] + b"\xc3"), "ret")
    eq("int3", terminator(pad[:15] + b"\xcc"), "int3")
    eq("retn", terminator(pad[:13] + b"\xc2\x04\x00"), "retn")
    eq("jmp rel32", terminator(pad[:11] + b"\xe9\xf4\xfb\xff\xff"), "jmp-rel32")
    eq("jmp rel8", terminator(pad[:14] + b"\xeb\x02"), "jmp-rel8")
    eq("jmp [eax]", terminator(pad[:14] + b"\xff\x20"), "jmp-mem")
    eq("jmp [eax+0x38]", terminator(pad[:13] + b"\xff\x60\x38"), "jmp-mem")
    eq("jmp [eax+imm32]", terminator(pad[:10] + b"\xff\xa0\x38\x00\x00\x00"), "jmp-mem")
    eq("jmp [eax] then byte", terminator(pad[:13] + b"\xff\x20\x90"), "")
    eq("jmp [eax+0x38] one short", terminator(pad[:14] + b"\xff\x60"), "")
    eq("not a terminator", terminator(b"\x90" * 16), "")
    eq("ret byte not at end", terminator(b"\xc3" + b"\x90" * 15), "")

    for label, pre in (("mod00", b"\x00"), ("mod01", b"\x40\x08"), ("mod10", b"\x80\x00\x01\x00\x00"),
                       ("ebp+disp8", b"\x45\x08"), ("esp+sib+disp8", b"\x44\x24\x08"),
                       ("sib disp32", b"\x84\x24\x00\x01\x00\x00")):
        blob = b"\x90" * 8 + b"\xc7" + pre + struct.pack("<I", 0x53F770) + b"\xc3"
        eq("store " + label, immediate_store(blob, blob.index(struct.pack("<I", 0x53F770)),
                                             0x53F770), True)
    blob = b"\x90" * 8 + b"\x68" + struct.pack("<I", 0x53F770) + b"\xc3"
    eq("push is not a store", immediate_store(blob, 9, 0x53F770), False)
    blob = b"\x90" * 8 + b"\xc7\x08" + struct.pack("<I", 0x53F770) + b"\xc3"
    eq("c7 /1 is not /0", immediate_store(blob, 10, 0x53F770), False)
    blob = b"\x90" * 8 + b"\xc7\x00" + struct.pack("<I", 0x53F771) + b"\xc3"
    eq("other value", immediate_store(blob, 10, 0x53F770), False)

    # literal dispatch: how an instruction mentions a table, and where a `call [reg+disp]` lands
    eq("install", ref_kind("mov", "DWORD PTR [eax],0x53f770"), "install")
    eq("load", ref_kind("mov", "ecx,0x53f770"), "load")
    eq("lea", ref_kind("lea", "eax,[0x53f770]"), "lea")
    eq("push", ref_kind("push", "0x53f770"), "push")
    eq("cmp", ref_kind("cmp", "eax,0x53f770"), "other")
    eq("absolute call", ref_kind("call", "DWORD PTR ds:0x53f770"), "absolute")
    eq("absolute jmp", ref_kind("jmp", "DWORD PTR ds:0x53f770"), "absolute")
    eq("imm", imm_values("eax,[0x53f770]"), [0x53F770])
    eq("no imm", imm_values("eax,ecx"), [])
    eq("writes eax", written_register("mov", "eax,0x53f770"), "eax")
    eq("writes memory", written_register("mov", "DWORD PTR [eax],0x53f770"), "")
    eq("slot 0", dispatch_disp("DWORD PTR [eax]", "eax"), 0)
    eq("slot 19", dispatch_disp("DWORD PTR [eax+0x4c]", "eax"), 0x4C)
    eq("slot 20", dispatch_disp("DWORD PTR [eax+0x50]", "eax"), 0x50)
    eq("wrong reg", dispatch_disp("DWORD PTR [ecx+0x4c]", "eax"), None)
    eq("bare call", dispatch_disp("eax", "eax"), None)
    eq("direct call", dispatch_disp("0x53f770", "eax"), None)
    eq("two registers", dispatch_disp("DWORD PTR [eax+ecx*4]", "eax"), None)

    # a table start splits at its install store: 0x4c7800 is 21 + 2, the store sits at +21*4
    fake = [{"run_va": "0x004c7800", "run_slots": 23, "tables": 2, "first_is_table": "yes",
             "slot_counts": "21 2", "install_sites": "0x004c7800 0x004c7854"}]
    eq("table starts", table_starts(fake), {0x4C7800: 21, 0x4C7854: 2})

    if fails:
        for f in fails:
            print("FAIL " + f)
        return 1
    print("ok: 12 terminator cases, 6 immediate-store cases, 19 dispatch cases")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xbe", nargs="?")
    ap.add_argument("--va", default="0x53f770")
    ap.add_argument("--symbols", default=os.path.join(os.path.dirname(HERE), "symbols",
                                                      "xbox_functions.csv"))
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--block", action="store_true",
                    help="census every code-pointer run in a data section, split at install starts")
    ap.add_argument("--reach", action="store_true",
                    help="census how .text references every install-started table, and its dispatches")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return cmd_check()
    if not args.xbe:
        ap.error("an XBE is required unless --check")
    here = os.path.dirname(HERE)
    if args.reach:
        x = X.Xbe(args.xbe)
        runs = block_scan(x)
        tables = table_starts(runs)
        text = next((s for s in x.sections if s.name == ".text"), None)
        if text is None:
            raise SystemExit(".text: section not found")
        insns, _ = sweep(x, text)
        mapped = load_table_rows(os.path.join(here, "inventory", "xbe_class_tables.csv"))
        classes = {va: r.get("class", "") for va, r in
                   load_table_rows(os.path.join(here, "inventory",
                                                "xbe_class_table_names.csv")).items()}
        reach = reach_scan(x, tables, insns, load_function_spans(args.symbols), set(mapped),
                           classes, install_census(x, next(s for s in x.sections
                                                           if s.name == ".rdata")))
        out = args.out or os.path.join(here, "inventory", "native_vtable_reach.csv")
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        fields = ["table_va", "table_slots", "mapped", "class", "refs", "installs", "loads",
                  "lea", "pushes", "absolute", "other", "other_ops", "loads_into", "dispatch",
                  "dispatch_slots", "dispatch_functions", "load_sites", "absolute_slots",
                  "absolute_sites", "install_sites", "install_bodies", "shared_classes"]
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(reach)

        def tally(pred, flag=None):
            sel = [r for r in reach if pred(r) and (flag is None or r["mapped"] == flag)]
            return len(sel), sum(1 for r in sel if r["mapped"] == "yes"), \
                sum(1 for r in sel if r["mapped"] == "no")

        def show(what, pred):
            n, m, u = tally(pred)
            print(f"  {what:<34} {n:4d}  mapped {m:4d}  unmapped {u:4d}")

        print(f"{len(reach)} install-started tables, {sum(1 for r in reach if r['mapped'] == 'yes')} "
              f"mapped class tables, {sum(1 for r in reach if r['mapped'] == 'no')} unmapped")
        show("referenced in .text at all", lambda r: r["refs"] > 0)
        show("only its install store", lambda r: r["refs"] > 0 and r["refs"] == r["installs"])
        show("materialised in a register", lambda r: r["loads"] + r["lea"] > 0)
        show("pushed as an argument", lambda r: r["pushes"] > 0)
        show("dispatched through an absolute slot", lambda r: bool(r["absolute"]))
        show("dispatched through a register base", lambda r: bool(r["dispatch"]))
        show("installed beside a mapped class", lambda r: bool(r["shared_classes"]))
        print(f"  {sum(int(r['absolute']) for r in reach)} absolute dispatch sites, "
              f"{sum(1 for r in reach if r['absolute'])} tables; "
              f"{sum(1 for r in reach for d in r['dispatch'].split())} register-base dispatch sites, "
              f"{sum(1 for r in reach if r['dispatch'])} tables")
        print(f"wrote {out}")
        return 0
    if args.block:
        rows = block_scan(X.Xbe(args.xbe))
        out = args.out or os.path.join(here, "inventory", "native_vtable_merges.csv")
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["run_va", "run_slots", "tables", "first_is_table",
                                              "slot_counts", "install_sites"])
            w.writeheader()
            w.writerows(rows)
        multi = [r for r in rows if r["tables"] >= 2]
        tables = sum(r["tables"] for r in rows)
        print(f"{len(rows)} runs of >=3 code pointers, {tables} table starts, "
              f"{len(multi)} runs holding two or more tables "
              f"({sum(1 for r in rows if r['first_is_table'] == 'yes')} runs start with a table)")
        print(f"wrote {args.out}")
        return 0
    va = int(args.va, 0)
    x = X.Xbe(args.xbe)
    first = struct.unpack("<I", x.read_va(va, 4))[0]
    insns, calls = sweep(x, x.section_for(first))
    rows, starts = census(x, va, load_symbols(args.symbols), insns, calls)
    if args.json:
        print(json.dumps({"tables": [f"{s:#010x}" for s in starts], "rows": rows}, indent=1))
        return 0
    args.out = args.out or os.path.join(here, "inventory", "native_vtable_census.csv")
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["table_va", "table_slots", "slot", "slot_va", "name",
                                           "boundary", "tail", "body_bytes", "dispatch",
                                           "install_site"])
        w.writeheader()
        w.writerows(rows)
    by_table = {}
    for r in rows:
        by_table.setdefault(r["table_va"], []).append(r)
    print(f"{len(rows)} slots over {len(by_table)} table(s) from {va:#x}")
    for tva, rs in by_table.items():
        cnt = lambda k: sum(1 for r in rs if k in r["boundary"].split("+"))
        dis = sorted({d for r in rs for d in r["dispatch"].split() if d.startswith("[")
                      or d.startswith("jmp [")})
        print(f"  {tva} slots={len(rs):2d} tail={cnt('tail'):2d} map={cnt('map'):2d} "
              f"call={cnt('call'):2d} install={rs[0]['install_site'] or '-'} "
              f"dispatch={dis}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
