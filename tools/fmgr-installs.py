#!/usr/bin/env python3
"""Every install of the file-manager global, and the phase of the process each one belongs to (U5)

#519 left one question: the global `[0x005bf514]` holds a pointer into `Core_UnObjBas`'s own frame
(`[ebp-0x954]`, an object whose table is `0x004c8db8`), so does that frame outlive the save chain --
or does the loader see the `.data` fallback after the frame is popped? Reading the exit path answers
it, and the answer is neither of the two the note expected:

* **the containing function is the process itself.** `0x0012620` has exactly one direct caller, at
  `0x33f2ff` in the CRT start-up, and its body prints `Startup time %f`, `MEMORY: %d KByte of
  physical memory in use`, `Map linearization complete`, `Starting Engine`, `Launching map [%s]`,
  `Entering Loop` and `Engine->Init: %f`: it is `main`. It has **one** `ret` (`0x16cfe`), so the
  frame it builds in its prologue is live for the whole session, and a pointer into it is not a
  dangling object -- the `.data` initializer (`0x005c1a0c`, table `0x004d2fd0`) describes only the
  state *before* `main` runs.
* **there are two installs, not one, and the later one is the engine's.** `0x169e3` stores
  `lea ecx,[ebp-0xb0]` -- a second frame slot, zeroed at `0x12927` -- 795 bytes before that single
  `ret` and immediately before the `Starting Engine` literal, and the `call 0x6c750` that follows it
  is made with `ecx` still holding that slot. So the object #519 measured (`0x4c8db8`) is the
  *pre-engine* file manager, and what the engine phase and the save chain run against is the object
  in the second slot. `0x6c750`'s own body stores no table into it, so the second object's class is
  open.

This tool prints that table rather than asserting it: every instruction in the image that names the
global, grouped by containing function; every store to it with the value's provenance (immediate,
global, frame slot, incoming argument); for a frame slot, every other reference to that slot inside
the function, the `lea` that hands it to a constructor and the table that constructor stores at
`[this]`; the function's return sites and its direct callers; the first printable literal pushed
after the store (the phase the build prints there); and for each read, the dispatch displacement the
loaded pointer is called through.

    python3 tools/fmgr-installs.py --xbe $DUMP/default.xbe -o inventory/fmgr_installs.csv
    python3 tools/fmgr-installs.py --write-doc docs/systems/CHECKPOINTS.md
    python3 tools/fmgr-installs.py --check       # synthetic instructions, no dump, writes nothing

Addresses, counts and the build's own printed literals only: no game data, no decompiled text.
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

GLOBAL = 0x005BF514
COLUMNS = ["global", "store", "at", "fn", "fn_start", "fn_size", "rets", "callers", "kind", "value",
           "slot", "slot_refs", "builder", "table", "phase", "reads", "dispatch"]
CSV_PATH = os.path.join("inventory", "fmgr_installs.csv")
SYMBOLS = os.path.join("symbols", "xbox_functions.csv")
DOC = os.path.join("docs", "systems", "CHECKPOINTS.md")
BLOCK_MARK = ("<!-- generated:fmgr-installs -->", "<!-- /generated:fmgr-installs -->")

OBJDUMP_LINE = re.compile(r"^\s*([0-9a-f]+):\s+(?:[0-9a-f]{2} )+\s*(.*)$")
HEX = re.compile(r"0x([0-9a-f]+)")
DS = re.compile(r"ds:0x([0-9a-f]+)")
REGWRITE = re.compile(r"^(mov|xor|add|sub|and|or|pop|inc|dec|lea|imul|shl|shr)\s+(e[a-z]{2}),(.*)$")
FRAME = re.compile(r"\[ebp-0x([0-9a-f]+)\]")
STACK_IN = re.compile(r"\[(esp|ebp)\+0x([0-9a-f]+)\]")
DISPATCH = re.compile(r"^call\s+DWORD PTR \[(e[a-z]{2})\+0x([0-9a-f]+)\]$")
TABLE_STORE = re.compile(r"^mov\s+DWORD PTR \[(e[a-z]{2})\],0x([0-9a-f]+)$")
BRANCH = ("call", "ret", "jmp", "loop")


# ---------------------------------------------------------------- the image and the map

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
            except (KeyError, ValueError):
                continue
    rows.sort()
    return [r for r in rows if r[1] > 0]


def containing(table, va):
    starts = [row[0] for row in table]
    i = bisect.bisect_right(starts, va) - 1
    if i >= 0 and starts[i] <= va < table[i][0] + table[i][1]:
        return table[i]
    return (None, None, None)


def code_sections(xbe, table):
    """A section is code when the mapped functions cover a tenth of it (every XBE section sets the flag)."""
    out = []
    for sec in xbe.sections:
        covered = sum(size for start, size, _n in table if sec.contains(start))
        if covered and covered * 10 >= sec.vsize:
            out.append(sec)
    return out


def instructions(blob, base_va):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
        fh.write(blob)
        temp = fh.name
    try:
        out = subprocess.run(["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                              "--adjust-vma=%d" % base_va, temp],
                             capture_output=True, text=True).stdout
    finally:
        os.unlink(temp)
    for line in out.splitlines():
        m = OBJDUMP_LINE.match(line)
        if m:
            yield int(m.group(1), 16), m.group(2).strip()


# ---------------------------------------------------------------- instruction reading

def operands(text):
    m = re.match(r"^([a-z0-9]+)\s+(.*)$", text)
    if not m:
        return text, []
    parts, depth, cur = [], 0, ""
    for ch in m.group(2):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    parts.append(cur.strip())
    return m.group(1), [p for p in parts if p]


def names_absolute(text, va):
    if va in [int(v, 16) for v in DS.findall(text)]:
        return True
    return bool(re.search(r"(?<![\w])DWORD PTR 0x%x(?![\w])" % va, text))


def access(text, va):
    """'read' or 'write' for one instruction and one absolute address."""
    if not names_absolute(text, va):
        return None
    mnem, parts = operands(text)
    if not parts:
        return None
    if not names_absolute(parts[0], va):
        return "read"
    if mnem.startswith(("cmp", "test", "push")):
        return "read"
    return "write"


def value_operand(text, va):
    """The operand a store writes into `va`."""
    mnem, parts = operands(text)
    if len(parts) < 2 or not names_absolute(parts[0], va):
        return None
    return parts[1].strip()


def writer(insns, index, reg, floor, limit=48):
    """(va, source operand) of the last write to `reg` before `insns[index]`; a branch or the floor ends it."""
    for j in range(index - 1, max(-1, index - limit), -1):
        va, text = insns[j]
        if va < floor or text.startswith(BRANCH):
            return None
        m = REGWRITE.match(text)
        if m and m.group(2) == reg:
            return va, m.group(3).strip()
    return None


def provenance(insns, index, value, floor):
    """('immediate'|'global'|'frame'|'incoming'|'unread', detail) for a stored operand."""
    if value is None:
        return "", ""
    if re.match(r"^0x[0-9a-f]+$", value):
        return "immediate", value[2:]
    g = re.match(r"^DWORD PTR ds:0x([0-9a-f]+)$", value)
    if g:
        return "global", g.group(1)
    if re.match(r"^0x[0-9a-f]+\([a-z%]+\)$", value):
        return "global", HEX.findall(value)[0]
    s = STACK_IN.match(value.strip("DWORD PTR "))
    if s:
        return "incoming", s.group(1) + "+" + s.group(2)
    m = re.match(r"^(e[a-z]{2})$", value)
    if not m:
        return "", ""
    w = writer(insns, index, m.group(1), floor)
    if not w:
        return "unread", m.group(1)
    _va, src = w
    f = FRAME.search(src)
    if f:
        return "frame", "ebp-" + f.group(1)
    s = STACK_IN.search(src)
    if s:
        return "incoming", s.group(1) + "+" + s.group(2)
    g = re.search(r"ds:0x([0-9a-f]+)", src)
    if g:
        return "global", g.group(1)
    if re.match(r"^0x[0-9a-f]+$", src):
        return "immediate", src[2:]
    return "unread", m.group(1)


def slot_refs(insns, start, end, slot):
    """Every instruction in one function naming the frame slot, as (va, read|write)."""
    pat = re.compile(r"\[ebp-0x%x\]" % slot)
    out = []
    for va, text in insns:
        if va < start:
            continue
        if va >= end:
            break
        if not pat.search(text):
            continue
        mnem, parts = operands(text)
        if not parts:
            continue
        if not pat.search(parts[0]) or mnem.startswith(("cmp", "test", "push")):
            out.append((va, "read"))
        else:
            out.append((va, "write"))
    return out


def slot_receiver(insns, start, end, slot):
    """(lea site, call site, callee) for the `lea ecx,[ebp-X]` handed straight to a call, if any."""
    pat = re.compile(r"^lea\s+(e[a-z]{2}),\[ebp-0x%x\]$" % slot)
    index = {va: i for i, (va, _t) in enumerate(insns)}
    for va, text in insns:
        if va < start or va >= end:
            continue
        m = pat.match(text)
        if not m:
            continue
        reg = m.group(1)
        i = index[va]
        for j in range(i + 1, min(len(insns), i + 12)):
            va2, text2 = insns[j]
            if va2 >= end:
                break
            w = REGWRITE.match(text2)
            if w and w.group(2) == reg:
                break
            c = re.match(r"^call\s+0x([0-9a-f]+)$", text2)
            if c:
                return va, va2, int(c.group(1), 16)
            if text2.startswith(("ret", "jmp")):
                break
    return None, None, None


def ctor_table(insns, start, limit=32):
    """First `mov DWORD PTR [reg],<imm>` in the first `limit` instructions of a body: its vtable store."""
    for i, (va, text) in enumerate(insns):
        if i >= limit or va < start:
            break
        m = TABLE_STORE.match(text)
        if m:
            return int(m.group(2), 16)
    return None


def print_literal(xbe, va, limit=64):
    """A pushed operand read as a printable string, ASCII or UTF-16; None when it is not one."""
    for width, codec in ((2, "utf-16-le"), (1, "latin-1")):
        try:
            raw = xbe.read_va(va, limit * width)
        except Exception:
            continue
        chars = []
        for i in range(0, len(raw) - width + 1, width):
            c = raw[i] if width == 1 else raw[i]
            unit = c if width == 1 else struct.unpack_from("<H", raw, i)[0]
            if unit == 0:
                break
            if not (0x20 <= unit < 0x7F):
                chars = []
                break
            chars.append(chr(unit))
        if len(chars) >= 6:
            return "".join(chars)
    return None


def phase_after(xbe, insns, index, end, window=60):
    """The first printable literal pushed after a store: the phase the build announces there."""
    for j in range(index + 1, min(len(insns), index + window)):
        va, text = insns[j]
        if va >= end:
            break
        m = re.match(r"^push\s+0x([0-9a-f]+)$", text)
        if not m:
            continue
        lit = print_literal(xbe, int(m.group(1), 16))
        if lit:
            return "%#x %s" % (va, lit)
    return "-"


def dispatch_after(insns, index, end, window=10):
    """The `call [reg+disp]` the loaded pointer is used through, as the displacement."""
    for j in range(index + 1, min(len(insns), index + window)):
        va, text = insns[j]
        if va >= end:
            break
        m = DISPATCH.match(text)
        if m:
            return "+0x" + m.group(2)
    return "-"


# ---------------------------------------------------------------- the census

def census(xbe, table, insns_by_section):
    """Rows: the image's own initializer plus every store, in the order the build performs them."""
    rows = _stores(xbe, table, insns_by_section)
    rows.sort(key=lambda r: (r["_at"], r["store"]))
    for r in rows:
        del r["_at"]
    return rows


def _stores(xbe, table, insns_by_section):
    """One row per store, plus the image's static initializer as the first row.

    `at` is the position in the entry function's body where the install happens: a store inside a
    callee is placed at the call site that enters that callee, a store inside the function the CRT
    calls is placed at its own address, and the static initializer at 0 (before the process).
    """
    allins = [pair for sec in insns_by_section for pair in insns_by_section[sec]]
    allins.sort()
    rows = []

    static = None
    try:
        static = struct.unpack("<I", xbe.read_va(GLOBAL, 4))[0]
    except Exception:
        pass
    if static:
        tbl = None
        try:
            tbl = struct.unpack("<I", xbe.read_va(static, 4))[0]
        except Exception:
            pass
        rows.append({
            "global": "0x%08x" % GLOBAL, "store": "image", "_at": -1, "at": "image",
            "fn": "-", "fn_start": "-", "fn_size": "-", "rets": "-", "callers": "-",
            "kind": "data-initializer", "value": "0x%08x" % static, "slot": "-", "slot_refs": "-",
            "builder": "-", "table": "0x%08x" % tbl if tbl else "-", "phase": "pre-main",
            "reads": str(sum(1 for _v, t in allins if access(t, GLOBAL) == "read")), "dispatch": "-",
        })

    for i, (va, text) in enumerate(allins):
        if access(text, GLOBAL) != "write":
            continue
        fn = containing(table, va)
        start, size, name = fn
        end = start + size if start else va + 1
        body = [(v, t) for v, t in allins if start and start <= v < end]
        rets = [v for v, t in body if re.match(r"^ret", t)]
        callers = [v for v, t in allins if start and re.match(r"^call\s+0x%x$" % start, t)]
        inside = [containing(table, v)[0] for v in callers]
        if not start or all(c is None for c in inside):
            at, at_text = va, "0x%08x own body" % va
        else:
            at = min(callers)
            at_text = "0x%08x call" % at
        value = value_operand(text, GLOBAL) or "-"
        kind, detail = provenance(allins, i, value if value != "-" else None, start or 0)
        slot = detail if kind == "frame" else "-"
        refs = " ".join("0x%08x %s" % (v, k) for v, k in slot_refs(allins, start, end, int(detail.split("-")[1], 16))) \
            if kind == "frame" else "-"
        builder, ctor = "-", "-"
        if kind == "frame":
            real = int(detail.split("-")[1], 16)
            lea, call, callee = slot_receiver(allins, start, end, real)
            if lea:
                builder = "0x%08x -> call 0x%08x" % (lea, callee)
                cfn = containing(table, callee)
                cstart = cfn[0] if cfn[0] else callee
                cbody = [(v, t) for v, t in allins if cstart <= v < cstart + (cfn[1] or 64)]
                tbl = ctor_table(cbody, cstart)
                ctor = "0x%08x" % tbl if tbl else "none"
        phase = phase_after(xbe, allins, i, 1 << 32)
        reads = " ".join("%#x%s" % (v, dispatch_after(body, j, end))
                         for j, (v, t) in enumerate(body) if access(t, GLOBAL) == "read") or "-"
        rows.append({
            "global": "0x%08x" % GLOBAL, "store": "0x%08x" % va, "_at": at, "at": at_text,
            "fn": name or "-", "fn_start": "0x%08x" % start if start else "-",
            "fn_size": str(size) if size else "-",
            "rets": " ".join("0x%08x" % v for v in rets) or "-",
            "callers": " ".join("0x%08x" % v for v in callers) or "-",
            "kind": kind, "value": value, "slot": slot, "slot_refs": refs,
            "builder": builder, "table": ctor, "phase": phase, "reads": reads, "dispatch": "-",
        })
    return rows


def render(rows):
    lines = []
    lines.append("**The file-manager global has %d store(s) in the image, and they are not the same kind of "
                 "object.** The rows below are the image's own initializer and every store, in the order the build "
                 "performs them, with the frame slot each value points at, the call that builds that slot, the table "
                 "that call stores at `[this]`, and the literal the build prints next -- the phase the store belongs "
                 "to. `at` orders them: an install inside a callee is placed at the call site that enters it, one "
                 "inside the function the CRT calls at its own address. The static row's `reads` cell is the count of "
                 "instructions in the image that name the global."
                 % (sum(1 for r in rows if r["store"] != "image"),))
    lines.append("")
    lines.append("| store | at | in | fn size | rets | callers | kind | value | slot | builder | table | phase | slot refs |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        lines.append("| `%s` | %s | %s | %s | %s | %s | %s | `%s` | %s | %s | %s | %s | %s |" % (
            r["store"], r["at"], r["fn"], r["fn_size"], r["rets"], r["callers"], r["kind"], r["value"],
            r["slot"], r["builder"], r["table"], r["phase"], r["slot_refs"]))
    lines.append("")
    lines.append("**What the columns decide.** A `frame` value means the object lives in the containing function's "
                 "own frame, so whether it can be dispatched through depends on that frame outliving the reader: the "
                 "`rets` column is the function's whole exit set, and a store that precedes every one of them leaves "
                 "the frame alive only as long as the function does. The `callers` column says how that function is "
                 "entered -- one site outside every mapped function is the CRT's call into `main`. `table` is `none` "
                 "when the call the slot is handed to stores no immediate at `[this]`, which is the difference "
                 "between an install and a pointer to scratch. A `-` in `rets` is a body whose decoded sweep has no "
                 "`ret` at all -- it leaves through a jump, so its exit set is not this column's to state.")
    return "\n".join(lines)


# ---------------------------------------------------------------- doc plumbing

def splice(path, block):
    text = open(path).read()
    a, b = BLOCK_MARK
    if a not in text or b not in text:
        sys.exit("%s: markers not found" % path)
    head, rest = text.split(a, 1)
    _old, tail = rest.split(b, 1)
    return head + a + "\n" + block + "\n" + b + tail


def write_doc(path, rows):
    new = splice(path, render(rows))
    with open(path, "w") as fh:
        fh.write(new)
    print("wrote %s" % path)


def check_doc(path, rows):
    want = splice(path, render(rows))
    have = open(path).read()
    if want != have:
        print("FAIL %s: generated block differs from a recomputation" % path)
        return 1
    print("ok %s" % path)
    return 0


# ---------------------------------------------------------------- self-test

def self_check():
    """Synthetic instructions: no dump, no writes."""
    n = [0]

    def ok(cond, label):
        n[0] += 1
        if not cond:
            sys.exit("FAIL %s" % label)

    g = GLOBAL
    ok(access("mov    ecx,DWORD PTR ds:0x5bf514", g) == "read", "ds read")
    ok(access("mov    DWORD PTR ds:0x5bf514,ecx", g) == "write", "ds write")
    ok(access("mov    eax,ds:0x5bf514", g) == "read", "ds read eax")
    ok(access("cmp    DWORD PTR ds:0x5bf514,ebx", g) == "read", "cmp is read")
    ok(access("push   DWORD PTR ds:0x5bf514", g) == "read", "push is read")
    ok(access("mov    ecx,DWORD PTR ds:0x5bf513", g) is None, "neighbour is not the global")
    ok(value_operand("mov    DWORD PTR ds:0x5bf514,ecx", g) == "ecx", "value operand")
    ok(value_operand("mov    ecx,DWORD PTR ds:0x5bf514", g) is None, "read has no value")

    body = [
        (0x1000, "lea    ecx,[ebp-0xb0]"),
        (0x1006, "push   ebx"),
        (0x1007, "push   ebx"),
        (0x1009, "mov    DWORD PTR ds:0x5bf514,ecx"),
        (0x100f, "call   0x2000"),
        (0x1014, "push   0x4d1124"),
        (0x1019, "mov    DWORD PTR [ebp-0xb0],ebx"),
        (0x101f, "ret"),
    ]
    ok(provenance(body, 3, "ecx", 0x1000) == ("frame", "ebp-b0"), "frame provenance")
    ok(provenance([(0, "mov    eax,0x1234"), (5, "mov    DWORD PTR ds:0x5bf514,eax")],
                  1, "eax", 0) == ("immediate", "1234"), "immediate provenance")
    ok(provenance([(0, "mov    eax,DWORD PTR [esp+0x28]"), (5, "mov    ds:0x5bf514,eax")],
                  1, "eax", 0)[0] == "incoming", "incoming provenance")
    ok(provenance([(0, "call   0x10"), (5, "mov    DWORD PTR ds:0x5bf514,eax")],
                  1, "eax", 0)[0] == "unread", "call result is unread, not a frame")
    lea, call, callee = slot_receiver(body, 0x1000, 0x1020, 0xB0)
    ok((lea, call, callee) == (0x1000, 0x100f, 0x2000), "slot receiver")
    ok(slot_refs(body, 0x1000, 0x1020, 0xB0) == [(0x1000, "read"), (0x1019, "write")], "slot refs")
    ok(ctor_table([(0x2000, "push   ebx"), (0x2001, "mov    DWORD PTR [edi],0x4c8db8")], 0x2000) == 0x4C8DB8,
       "ctor table")
    ok(ctor_table([(0x2000, "push   ebx")], 0x2000) is None, "no table store is none")
    ok(dispatch_after(body, 0, 0x1020) == "-", "no dispatch")
    ok(DISPATCH.match("call   DWORD PTR [edx+0x4]").group(2) == "4", "dispatch regex")
    ok(names_absolute("mov    ecx,0x5bf514", g) is False, "immediate is not an absolute access")
    ok(re.match(r"^ret", "ret    4") is not None, "retn")

    class Fake:
        blob = b"Starting Engine\x00"

        def read_va(self, va, size):
            return self.blob[va:va + size]
    ok(print_literal(Fake(), 0) == "Starting Engine", "literal")
    ok(print_literal(Fake(), 10) is None, "a five-letter tail is not a literal")
    class Binary:
        def read_va(self, va, size):
            return b"\x01\x02\x03\x04\x05\x06\x07\x08"
    ok(print_literal(Binary(), 0) is None, "binary is not a literal")
    ok(print_literal(Fake(), 0, limit=3) is None, "short read is not a literal")
    print("ok %d assertions (no dump, no writes)" % n[0])
    return 0


# ---------------------------------------------------------------- main

def load_csv(path=CSV_PATH):
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for k in COLUMNS:
            r.setdefault(k, "-")
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe", help="default.xbe (not needed with --check-doc)")
    ap.add_argument("-o", "--csv", default=None, help="write the artifact (%s)" % CSV_PATH)
    ap.add_argument("--write-doc", default=None, metavar="PAGE")
    ap.add_argument("--check-doc", default=None, metavar="PAGE")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump, no writes")
    args = ap.parse_args(argv)

    if args.check:
        return self_check()

    rows = None
    if args.xbe:
        xbe = load_xbe(args.xbe)
        table = load_symbols()
        insns_by_section = {}
        for sec in code_sections(xbe, table):
            insns_by_section[sec.name] = list(instructions(xbe.read_va(sec.va, sec.vsize), sec.va))
        rows = census(xbe, table, insns_by_section)

    if args.csv:
        if rows is None:
            ap.error("--csv needs --xbe")
        parent = os.path.dirname(args.csv)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(args.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
        print("wrote %s (%d rows)" % (args.csv, len(rows)))

    # the page is rendered from the committed artifact when no image is given, so --check-doc stays
    # dump-free: a page-only check against a CSV that is itself stale would agree and both be wrong.
    if args.write_doc or args.check_doc:
        if rows is None:
            rows = load_csv(CSV_PATH)
    if args.write_doc:
        write_doc(args.write_doc, rows)
    if args.check_doc:
        return check_doc(args.check_doc, rows)

    if rows is not None:
        for r in rows:
            print("%s %-16s %-10s %-16s slot=%-8s table=%-12s phase=%s" % (
                r["store"], r["fn"], r["kind"], r["value"], r["slot"], r["table"], r["phase"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
