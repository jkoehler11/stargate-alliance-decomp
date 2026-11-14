#!/usr/bin/env python3
"""Which object the file-manager global actually holds: the identity of the singleton, and every access to it (U5)

#515/#516 read the table the save chain asks -- `[0x005bf514]` -> `0x005c1a0c` -> vtable `0x004d2fd0` -- and found
that its reader slot answers NULL. Both left the same question: **is `0x005c1a0c` the object the build runs with?**
The `.data` initializer says yes; the only `.text` write to the global says it is replaced, with an address taken
from the writer's own frame.

This tool answers the first half by identity rather than by initializer: it walks the row of singleton pointers the
global belongs to (`0x5bf500`..`0x5bf514`), reads each pointee's first dword out of the image, reports how far apart
the pointees are and what the first slots of each vtable name -- which separates a row of four-byte, memberless *error*
singletons from a row of real subsystem objects -- and counts how often each table is named by an instruction.

The second half is the access census: every instruction in the image whose operand is that absolute address,
classified read or write, with its containing function. A write is attributed to the value's last writer, so "an
immediate", "a global" and "a frame slot" are different answers, and a frame answer is followed into the slot's own
references inside that function -- which is what decides whether a constructor ever runs on it.

    python3 tools/fmgr-live-object.py --xbe $DUMP/default.xbe -o inventory/fmgr_singletons.csv
    python3 tools/fmgr-live-object.py --write-doc docs/systems/CHECKPOINTS.md
    python3 tools/fmgr-live-object.py --check-doc docs/systems/CHECKPOINTS.md
    python3 tools/fmgr-live-object.py --check      # synthetic instructions, no dump, no writes

Addresses, vtable slot targets and counts only: no game data and no decompiled text.
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

COLUMNS = ["global", "object", "gap", "vtable", "names", "table_text_refs", "table_other_refs",
           "sites", "reads", "writes", "value", "source", "slot", "origin"]
BLOCK_MARK = ("<!-- generated:fmgr-live-object -->", "<!-- /generated:fmgr-live-object -->")
CSV_PATH = os.path.join("inventory", "fmgr_singletons.csv")
SYMBOLS = os.path.join("symbols", "xbox_functions.csv")

FM_GLOBAL = 0x005BF514
ROW_START, ROW_END = 0x005BF500, 0x005BF518
SLOT_NAMES = 6
MAX_AT = 6
MIN_LEN = 3

OBJDUMP_LINE = re.compile(r"^\s*([0-9a-f]+):\s+(?:[0-9a-f]{2} )+\s*(.*)$")
DS = re.compile(r"ds:0x([0-9a-f]+)")
HEX = re.compile(r"0x([0-9a-f]+)")
WRITE = re.compile(r"^(mov|xor|add|sub|and|or|pop|inc|dec|lea)\s+(e[a-z]{2}),(.*)$")
BRANCH = ("call", "ret", "jmp", "loop")


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
        return table[i][2], table[i][1], table[i][0]
    return None, None, None


def code_sections(xbe, table):
    """Sections the mapped functions are laid out in (every XBE section sets the executable flag, so the map decides)."""
    out = []
    for sec in xbe.sections:
        covered = sum(size for start, size, _n in table if sec.contains(start))
        if covered and covered * 10 >= sec.vsize:
            out.append(sec)
    return out


def dword(xbe, va):
    try:
        return struct.unpack("<I", xbe.read_va(va, 4))[0]
    except Exception:
        return None


def wide_at(xbe, va, limit=60):
    try:
        raw = xbe.read_va(va, limit * 2)
    except Exception:
        return None
    chars = []
    for i in range(0, len(raw) - 1, 2):
        c = struct.unpack_from("<H", raw, i)[0]
        if c == 0:
            break
        if not (0x20 <= c < 0x7F):
            return None
        chars.append(chr(c))
    return "".join(chars) if len(chars) >= MIN_LEN else None


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
    """Split an intel instruction's operand list on top-level commas."""
    m = re.match(r"^([a-z0-9]+)\s+(.*)$", text)
    if not m:
        return text, []
    mnem, rest = m.group(1), m.group(2)
    parts, depth, cur = [], 0, ""
    for ch in rest:
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
    return mnem, [p for p in parts if p]


def imms(text):
    """Immediates only: an address the instruction *reads* is inside brackets or a displacement, never an immediate."""
    bare = re.sub(r"\[[^\]]*\]", "", text)
    bare = re.sub(r"0x[0-9a-f]+\([^)]*\)", "", bare)
    return [int(v, 16) for v in HEX.findall(bare)]


def names_absolute(text, va):
    """True when an operand names `va` as an absolute memory address (not as an immediate)."""
    if va in [int(v, 16) for v in DS.findall(text)]:
        return True
    return bool(re.search(r"(?<![\w])DWORD PTR 0x%x(?![\w])" % va, text))


def access(text, va):
    """('read'|'write'|None) for one instruction and one absolute address."""
    if not names_absolute(text, va):
        return None
    mnem, parts = operands(text)
    if not parts:
        return None
    if not names_absolute(parts[0], va):
        return "read"
    if mnem.startswith("cmp") or mnem.startswith("test") or mnem.startswith("push"):
        return "read"
    return "write"


def writer(insns, i, reg, floor, limit=120):
    """The instruction that last wrote `reg` before index `i`, as (va, operand text); a branch or the floor ends it."""
    for j in range(i - 1, max(-1, i - limit), -1):
        va, text = insns[j]
        if va < floor or text.startswith(BRANCH):
            return None
        m = WRITE.match(text)
        if m and m.group(2) == reg:
            return va, m.group(3).strip()
    return None


def provenance(insns, i, value, floor):
    """What a stored value is: (kind, register, detail) with kind in immediate/global/frame/heap/unread."""
    m = re.match(r"^(e[a-z]{2})$", value)
    if not m:
        if re.match(r"^0x[0-9a-f]+$", value):
            return "immediate", "", value[2:]
        g = re.match(r"^DWORD PTR ds:0x([0-9a-f]+)$", value)
        if g:
            return "global", "", g.group(1)
        s = re.match(r"^(?:DWORD PTR )?\[(esp|ebp)\+0x([0-9a-f]+)\]$", value)
        if s:
            return "incoming", "", s.group(1) + "+" + s.group(2)
        return "", "", ""
    reg = m.group(1)
    w = writer(insns, i, reg, floor)
    if not w:
        j = i - 1
        while j >= 0 and insns[j][1].startswith("push"):
            j -= 1
        if reg == "eax" and j >= 0 and insns[j][1].startswith("call"):
            return "call-result", reg, insns[j][1].split()[-1]
        return "", "", ""
    _va, src = w
    f = re.match(r"^\[ebp-0x([0-9a-f]+)\]$", src)
    if f:
        return "frame", reg, f.group(1)
    s = re.match(r"^(?:DWORD PTR )?\[(esp|ebp)\+0x([0-9a-f]+)\]$", src)
    if s:
        return "incoming", reg, s.group(1) + "+" + s.group(2)
    g = re.match(r"^DWORD PTR ds:0x([0-9a-f]+)$", src)
    if g:
        return "global", reg, g.group(1)
    if re.match(r"^0x[0-9a-f]+$", src):
        return "immediate", reg, src[2:]
    if re.match(r"^DWORD PTR \[e[a-z]{2}\]$", src):
        return "heap", reg, ""
    return "unread", reg, ""


def slot_census(insns, start, end, off):
    """Every instruction inside one function that names the frame slot [ebp-<off>], as (va, read|write)."""
    pat = re.compile(r"\[ebp-0x%x\]" % off)
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
        if not pat.search(parts[0]):
            kind = "read"
        elif mnem.startswith(("cmp", "test", "push")):
            kind = "read"
        else:
            kind = "write"
        out.append((va, kind))
    return out


def call_sites(insns, target):
    """Instructions that call `target` by relative displacement (`E8 rel32`), from the decoded sweep."""
    pat = re.compile(r"^call\s+0x%x$" % target)
    return [va for va, text in insns if pat.match(text)]


def stack_delta(insns, start, read_va):
    """How far `esp` has moved from the entry to the read: pushes and `sub esp` grow it, pops shrink it.

    A body that spills an argument to `[esp+N]` *after* its own `sub esp,<frame>` is not reading the
    same slot a leaf would: without the delta, `Core_UnMisc`'s mid-body reads answer `arg667` instead
    of `arg2`. Returns None when a form this cannot account for (`mov esp`, `leave`, `enter`) appears.
    """
    delta = 0
    for va, text in insns:
        if va < start:
            continue
        if va >= read_va:
            break
        if text.startswith("push"):
            delta += 4
        elif re.match(r"^pop\s", text):
            delta -= 4
        elif re.match(r"^sub\s+esp,0x([0-9a-f]+)$", text):
            delta += int(re.match(r"^sub\s+esp,0x([0-9a-f]+)$", text).group(1), 16)
        elif re.match(r"^add\s+esp,0x([0-9a-f]+)$", text):
            delta -= int(re.match(r"^add\s+esp,0x([0-9a-f]+)$", text).group(1), 16)
        elif text.startswith(("mov    esp", "mov esp", "leave", "enter")):
            return None
    return delta


def arg_index(insns, start, read_va, esp_off):
    """Which argument `[esp+N]` is, or None when the slot is inside the body's own frame."""
    delta = stack_delta(insns, start, read_va)
    if delta is None or esp_off < delta or (esp_off - delta) % 4:
        return None
    argn = (esp_off - delta) // 4
    return argn if 1 <= argn <= 24 else None


STACK_MOVE = ("add esp", "sub esp", "pop", "mov esp", "leave")


def argument_pushes(insns, index, need, limit=32):
    """The pushes that build a call's arguments, oldest first, walking back from `insns[index]`.

    A `lea`/`mov` that prepares a register is *not* a push but sits between two of them (the real site
    loads `[ebp-0x954]` into `ecx` and pushes it between arguments), so the walk skips setup
    instructions and stops only at a stack change or a branch -- a run that stops at the first non-push
    undercounts the site and reports the argument as absent.
    """
    out, j = [], index - 1
    while j >= 0 and len(out) < min(need, limit):
        va, text = insns[j]
        if text.startswith("push"):
            out.append((va, text))
        elif text.startswith(BRANCH) or text.startswith(STACK_MOVE):
            break
        j -= 1
    return list(reversed(out))


def describe_push(insns, index, text):
    """What one `push` hands over: an immediate, a frame local, a global, or the register itself."""
    operand = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
    m = re.match(r"^0x([0-9a-f]+)$", operand)
    if m:
        return "0x%08x" % int(m.group(1), 16)
    if re.match(r"^e[a-z]{2}$", operand):
        w = writer(insns, index, operand, 0)
        if w:
            src = w[1]
            f = re.match(r"^\[ebp-0x([0-9a-f]+)\]$", src)
            if f:
                return "[ebp-0x%s]" % f.group(1)
            g = re.match(r"^(?:DWORD PTR )?ds:0x([0-9a-f]+)$", src)
            if g:
                return "[0x%08x]" % int(g.group(1), 16)
            return src
        return operand
    return operand


def argument_origin(insns, table, index, reg, detail, limit=2):
    """Follow an `incoming` value out of its callee: argument number -> the pushes at each call site.

    `detail` is `esp+<off>` from `provenance`; the read instruction is the register's last writer, so the
    pushes between the function's entry and that read say which argument the slot is.
    """
    base, off = detail.split("+")
    if base != "esp":
        return ""
    site = insns[index][0]
    fn, size, start = containing(table, site)
    if not fn:
        return ""
    w = writer(insns, index, reg, 0)
    if not w:
        return ""
    read_va, _src = w
    argn = arg_index(insns, start, read_va, int(off, 16))
    if not argn:
        return ""
    out = []
    where = {va: i for i, (va, _t) in enumerate(insns)}
    for call_va in call_sites(insns, start)[:limit]:
        i = where.get(call_va)
        if i is None:
            continue
        pushes = argument_pushes(insns, i, argn)
        if len(pushes) < argn:
            out.append("arg%d @%#010x (only %d push(es))" % (argn, call_va, len(pushes)))
            continue
        p_va, p_text = pushes[-argn]
        out.append("arg%d %s in %s @%#010x" % (argn, describe_push(insns, where[p_va], p_text),
                                              fn, call_va))
    return " ; ".join(out)


# ---------------------------------------------------------------- the measurement

def vtable_names(xbe, table, vtable):
    """The first slot names of a table: the committed function name, else the wide literal its body logs."""
    out = []
    for slot in range(SLOT_NAMES):
        target = dword(xbe, vtable + slot * 4)
        if not target:
            continue
        name = containing(table, target)[0]
        if not name:
            try:
                body = xbe.read_va(target, 48)
            except Exception:
                body = b""
            for i in range(len(body) - 4):
                if body[i] == 0x68:
                    lit = wide_at(xbe, struct.unpack_from("<I", body, i + 1)[0])
                    if lit:
                        name = "logs " + lit
                        break
        if name and name not in out:
            out.append(name)
    return " ".join(out[:3])


def table_refs(xbe, sections, va):
    """(instructions that name the table as an immediate, dwords of it outside code).

    A vtable reaches an object through a store of its address, so an immediate naming it in a code section is a
    construction -- the count a "never built at runtime" claim has to be measured against.
    """
    text_hits = 0
    for sec in sections:
        for _i, text in instructions(xbe.read_va(sec.va, sec.vsize), sec.va):
            if va in imms(text):
                text_hits += 1
    other_hits = 0
    pat = struct.pack("<I", va)
    for sec in xbe.sections:
        if any(sec is s for s in sections):
            continue
        try:
            other_hits += xbe.read_va(sec.va, sec.vsize).count(pat)
        except Exception:
            continue
    return text_hits, other_hits


def census(xbe_path, symbols=SYMBOLS):
    xbe = load_xbe(xbe_path)
    table = load_symbols(symbols)
    sections = code_sections(xbe, table)

    insns = []
    for sec in sections:
        insns += list(instructions(xbe.read_va(sec.va, sec.vsize), sec.va))
    insns.sort()

    objects = sorted({o for o in (dword(xbe, g) for g in range(ROW_START, ROW_END, 4)) if o})
    rows = []
    for gva in range(ROW_START, ROW_END, 4):
        obj = dword(xbe, gva)
        vt = dword(xbe, obj) if obj else None
        hits = [(i, access(text, gva)) for i, (_va, text) in enumerate(insns) if access(text, gva)]
        reads = [i for i, k in hits if k == "read"]
        writes = [i for i, k in hits if k == "write"]
        value, source, slot, origin = [], [], [], []
        for i in writes:
            site = insns[i][0]
            _mnem, parts = operands(insns[i][1])
            what = parts[1] if len(parts) > 1 else ""
            kind, _reg, detail = provenance(insns, i, what, 0)
            label = "frame ebp-" + detail if kind == "frame" else (
                "global " + detail if kind == "global" else (kind if kind else "unread"))
            value.append("%#010x %s" % (site, what))
            source.append("%#010x %s" % (site, label))
            if kind == "frame":
                fn, size, start = containing(table, site)
                if fn:
                    found = slot_census(insns, start, start + size, int(detail, 16))
                    slot.append("%#010x: %s" % (site, " ".join("%#010x %s" % (va, k) for va, k in found[:4])))
            if kind == "incoming":
                followed = argument_origin(insns, table, i, _reg, detail)
                if followed:
                    origin.append("%#010x %s" % (site, followed))
        ttext, tother = table_refs(xbe, sections, vt) if vt else (0, 0)
        gap = ""
        if obj in objects:
            nxt = [o for o in objects if o > obj]
            if nxt:
                gap = str(nxt[0] - obj)
        rows.append({
            "global": "%#010x" % gva,
            "object": ("%#010x" % obj) if obj else "",
            "gap": gap,
            "vtable": ("%#010x" % vt) if vt else "",
            "names": vtable_names(xbe, table, vt) if vt else "",
            "table_text_refs": str(ttext),
            "table_other_refs": str(tother),
            "sites": " ".join("%#010x" % insns[i][0] for i, _k in hits[:MAX_AT]),
            "reads": str(len(reads)),
            "writes": str(len(writes)),
            "value": " ; ".join(value),
            "source": " ; ".join(source),
            "slot": " ; ".join(slot),
            "origin": " ; ".join(origin),
        })
    return rows


# ---------------------------------------------------------------- the page

def render(rows):
    def table(headers, body):
        lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
        lines += ["| " + " | ".join(cells) + " |" for cells in body]
        return lines

    fm = next((r for r in rows if r["global"] == "%#010x" % FM_GLOBAL), rows[-1])
    gaps = sorted({r["gap"] for r in rows if r["gap"]})
    text_total = sum(int(r["table_text_refs"]) for r in rows)
    neighbour = rows[[i for i, r in enumerate(rows) if r["global"] == fm["global"]][0] - 1] if rows else None
    out = ["<!-- generated:fmgr-live-object -->"]
    out += ["**The file-manager pointer is one of %d consecutive globals.** `%s`..`%s` hold pointers into the same "
            "run of statics, %s bytes apart -- one dword per object, the vtable, so none of them carries state and "
            "none of them is a subsystem object the engine built. The table behind `[%s]` names `%s`; the global "
            "beside it at -4 names `%s`."
            % (len(rows), "%#010x" % ROW_START, "%#010x" % (ROW_END - 4), "/".join(gaps), fm["global"],
               fm["names"] or "-", (neighbour or {}).get("names") or "-"), ""]
    if text_total == 0:
        out += ["**No instruction in the image names any of these %d tables as an immediate** (the `table .text` "
                "column is 0 on every row), so nothing constructs one of these objects: they exist only as statics, "
                "and the dispatch census that reads this table describes the fallback implementation rather than a "
                "file layer the build assembled." % len(rows), ""]
    else:
        out += ["**%d instruction(s) in the image name one of these tables as an immediate**, so at least one of them "
                "is constructed in code; the per-row count says which." % text_total, ""]
    out += ["**Per global: the object it holds, its vtable, and every instruction that names it.**", ""]
    out += table(["global", "object", "gap", "vtable", "names", "table .text", "table other",
                  "sites", "reads", "writes", "value", "source", "slot", "origin"],
                 [["`%s`" % r["global"], "`%s`" % r["object"] if r["object"] else "-", r["gap"] or "-",
                   "`%s`" % r["vtable"] if r["vtable"] else "-", r["names"] or "-",
                   r["table_text_refs"], r["table_other_refs"], r["sites"] or "-", r["reads"], r["writes"],
                   ("`%s`" % r["value"]) if r["value"] else "-", "`%s`" % r["source"] if r["source"] else "-",
                   r["slot"] or "-", r["origin"] or "-"]
                  for r in rows])
    out += ["", "**The access census.** `[%s]` is named by %s read(s) and %s write(s) in the image: %s. A value "
            "whose last writer is a frame slot is followed into that slot's own references inside the containing "
            "function -- `%s` -- because a slot that is never given a table cannot answer a dispatch, and then the "
            "write installs no second file manager."
            % (fm["global"], fm["reads"], fm["writes"],
               "; ".join("`%s`" % s for s in fm["source"].split(" ; ")) or "never",
               fm["slot"] or "none"), ""]
    if fm.get("origin"):
        out += ["**The value is followed out of the callee, and it is the caller's own frame.** `origin` takes the "
                "`incoming` source one step further: the slot `[esp+N]` the callee reads is argument `N/4` less every "
                "push and `sub esp` made since its entry, and each `E8` site that calls the callee is decoded back to "
                "its push run, so the argument is named by the value the caller actually hands over. `%s` -- a "
                "pointer into a caller frame, pushed there and stored before the callee does anything else, so the "
                "global is live from that instruction on and answers a dispatch as an object."
                % fm["origin"], ""]
    out += ["<!-- /generated:fmgr-live-object -->"]
    return "\n".join(out)


def splice(path, block):
    with open(path) as fh:
        text = fh.read()
    head, tail = BLOCK_MARK
    if head not in text or tail not in text:
        sys.exit("%s: markers %s .. %s not found" % (path, head, tail))
    before, rest = text.split(head, 1)
    _old, after = rest.split(tail, 1)
    return before + block + after


def write_doc(path, rows):
    """Splice first, then write: opening the path for writing truncates the page the splice has to read."""
    new = splice(path, render(rows))
    with open(path, "w") as fh:
        fh.write(new)
    print("ok   fmgr-live-object: wrote the generated block of %s" % path)


def check_doc(path, rows):
    new = splice(path, render(rows))
    with open(path) as fh:
        old = fh.read()
    if new == old:
        print("ok   fmgr-live-object: %s matches the committed table" % path)
        return 0
    for n, (a, b) in enumerate(zip(new.splitlines(), old.splitlines()), 1):
        if a != b:
            print("FAIL fmgr-live-object: %s line %d\n  page:  %r\n  table: %r" % (path, n, b, a))
            return 1
    print("FAIL fmgr-live-object: %s length %d vs %d" % (path, len(new.splitlines()), len(old.splitlines())))
    return 1


# ---------------------------------------------------------------- the dry check

def selftest():
    failures = []
    count = [0]

    def want(label, got, expect):
        count[0] += 1
        if got != expect:
            failures.append("%s: %r != %r" % (label, got, expect))

    g = 0x5BF514
    want("write form", access("mov    DWORD PTR ds:0x5bf514,ecx", g), "write")
    want("read form", access("mov    ecx,DWORD PTR ds:0x5bf514", g), "read")
    want("short form", access("mov    eax,ds:0x5bf514", g), "read")
    want("push is a read", access("push   DWORD PTR ds:0x5bf514", g), "read")
    want("or is a write", access("or     DWORD PTR ds:0x5bf514,0x4", g), "write")
    want("other global", access("mov    ecx,DWORD PTR ds:0x5bf510", g), None)
    want("an immediate is not an access", access("mov    eax,0x5bf514", g), None)
    want("operands", operands("mov    DWORD PTR ds:0x5bf514,ecx"), ("mov", ["DWORD PTR ds:0x5bf514", "ecx"]))
    want("operands with a comma inside the memory operand",
         operands("mov    eax,DWORD PTR [ecx+edx*4+0x10]"), ("mov", ["eax", "DWORD PTR [ecx+edx*4+0x10]"]))
    want("immediate strip", imms("mov    eax,0x004d2fd0"), [0x4D2FD0])
    want("displacement is not an immediate", imms("cmp    DWORD PTR [ebx+0x28],ebp"), [])
    want("at&t displacement is not an immediate", imms("mov    0x4c9790(%eax),%ecx"), [])

    insns = [(0x1000, "push   0x0"), (0x1002, "lea    ecx,[ebp-0xb0]"), (0x1008, "push   0x0"),
             (0x100a, "mov    DWORD PTR ds:0x5bf514,ecx"), (0x1010, "call   0x6c750")]
    kind, reg, detail = provenance(insns, 3, "ecx", 0)
    want("frame provenance", (kind, reg, detail), ("frame", "ecx", "b0"))
    imm = [(0x2000, "mov    eax,0x4c85ac"), (0x2005, "mov    DWORD PTR ds:0x5bf514,eax")]
    want("immediate provenance", provenance(imm, 1, "eax", 0)[0], "immediate")
    glob = [(0x3000, "mov    ecx,DWORD PTR ds:0x5bf510"), (0x3006, "mov    DWORD PTR ds:0x5bf514,ecx")]
    want("global provenance", provenance(glob, 1, "ecx", 0)[:1] + provenance(glob, 1, "ecx", 0)[2:],
         ("global", "5bf510"))
    across = [(0x4000, "lea    ecx,[ebp-0x40]"), (0x4004, "call   0x1234"),
              (0x4009, "mov    DWORD PTR ds:0x5bf514,ecx")]
    want("a call ends the walk", provenance(across, 2, "ecx", 0)[0], "")
    floored = [(0x5000, "lea    ecx,[ebp-0x40]"), (0x5004, "mov    DWORD PTR ds:0x5bf514,ecx")]
    want("the floor ends the walk", provenance(floored, 1, "ecx", 0x5002)[0], "")
    slot = [(0x6000, "push   ebx"), (0x6001, "mov    DWORD PTR [ebp-0xb0],ebx"),
            (0x6007, "lea    ecx,[ebp-0xb0]"), (0x600d, "mov    DWORD PTR [ebp-0xb0],esi")]
    want("slot census", slot_census(slot, 0x6000, 0x6100, 0xB0), [(0x6001, "write"), (0x6007, "read"),
                                                                (0x600D, "write")])
    arg = [(0x7000, "mov    eax,DWORD PTR [esp+0x28]"), (0x7004, "push   edi"),
           (0x7005, "mov    ds:0x5bf514,eax")]
    want("incoming provenance", provenance(arg, 2, "eax", 0), ("incoming", "eax", "esp+28"))
    ret = [(0x8000, "push   0x40"), (0x8003, "call   0x3af60"), (0x8008, "mov    ds:0x5bf514,eax")]
    want("call-result provenance", provenance(ret, 2, "eax", 0)[0], "call-result")

    # the argument walk: three SEH pushes, then [esp+0x28] is the seventh argument
    callee = [(0x9000, "mov    eax,fs:0x0"), (0x9006, "push   0xffffffff"),
              (0x9008, "push   0x3f640a"), (0x900d, "push   eax"),
              (0x900e, "mov    eax,DWORD PTR [esp+0x28]"),
              (0x9012, "mov    ds:0x5bf514,eax")]
    want("argument index", arg_index(callee, 0x9000, 0x900E, 0x28), 7)
    want("no pushes means arg N/4", arg_index(callee, 0x9000, 0x9000, 0x28), 10)
    framed = [(0x9100, "push   0xffffffff"), (0x9102, "sub    esp,0xa5c"),
              (0x9108, "mov    ecx,DWORD PTR [esp+0xa70]")]
    want("an argument read after the frame is still an argument",
         arg_index(framed, 0x9100, 0x9108, 0xA70), 4)
    want("a slot inside the frame is not an argument",
         arg_index(framed, 0x9100, 0x9108, 0xA60), None)
    want("an untracked esp ends the walk",
         arg_index([(0x9200, "leave"), (0x9201, "mov    eax,DWORD PTR [esp+0x8]")], 0x9200, 0x9201, 0x8),
         None)
    caller = [(0xA000, "push   0x1"), (0xA002, "push   0x2a120"), (0xA007, "lea    ecx,[ebp-0x954]"),
              (0xA00D, "push   ecx"), (0xA00E, "push   0x5bf3fc"), (0xA013, "push   0x5bf3f0"),
              (0xA018, "push   0x5bf3ec"), (0xA01D, "push   0x59ed68"), (0xA022, "push   0x635e90"),
              (0xA027, "push   0x599f10"), (0xA02C, "call   0x5a990")]
    want("the push run skips a setup instruction",
         [t for _va, t in argument_pushes(caller, 10, 7)],
         ["push   ecx", "push   0x5bf3fc", "push   0x5bf3f0", "push   0x5bf3ec", "push   0x59ed68",
          "push   0x635e90", "push   0x599f10"])
    want("a short run stops at the branch",
         len(argument_pushes(caller, 3, 9)), 2)
    want("the seventh argument is the frame local",
         describe_push(caller, 3, "push   ecx"), "[ebp-0x954]")
    want("an immediate argument", describe_push(caller, 9, "push   0x599f10"), "0x00599f10")
    want("call sites from the decoded sweep", call_sites(caller, 0x5A990), [0xA02C])
    want("a different callee", call_sites(caller, 0x5A994), [])

    rows = [
        {"global": "0x005bf510", "object": "0x005c1a08", "gap": "4", "vtable": "0x004d2eb0",
         "names": "appMallocBeforeInit", "table_text_refs": "0", "table_other_refs": "1",
         "sites": "0x00016841", "reads": "2", "writes": "0", "value": "", "source": "", "slot": "",
         "origin": ""},
        {"global": "0x005bf514", "object": "0x005c1a0c", "gap": "", "vtable": "0x004d2fd0",
         "names": "FFileManagerError_CreateFileReader FFileManagerError_CreateFileWriter", "table_text_refs": "0",
         "table_other_refs": "1", "sites": "0x000167cd 0x00169e3", "reads": "1", "writes": "1",
         "value": "0x00169e3 ecx", "source": "0x00169e3 frame ebp-b0",
         "slot": "0x00169e3: 0x0012927 write 0x00169d2 read",
         "origin": "0x00012b7d arg7 [ebp-0x954] in Core_UnObjBas @0x00012b7d"},
    ]
    page = render(rows)
    want("render markers", (page.splitlines()[0], page.splitlines()[-1]), BLOCK_MARK)
    want("row count in prose", "one of 2 consecutive globals" in page, True)
    want("the write sentence", "0x00169e3 frame ebp-b0" in page and "1 write(s)" in page, True)
    want("the zero construction sentence", "No instruction in the image names any of these 2 tables" in page, True)
    want("the origin sentence", "pointer into a caller frame" in page, True)
    want("the origin column", "arg7 [ebp-0x954]" in page, True)
    built = [dict(r) for r in rows]
    built[0]["table_text_refs"] = "3"
    want("the built-in-code sentence", "3 instruction(s) in the image name" in render(built), True)
    width = None
    for line in page.splitlines():
        if not line.startswith("|"):
            width = None
            continue
        if width is None:
            width = line.count("|")
        elif line.count("|") != width:
            failures.append("ragged row: %r" % line)
    src = open(os.path.abspath(__file__)).read()
    for name in ("--write-doc", "--check-doc", "--check", "--xbe"):
        if name not in src:
            failures.append("source lost %s" % name)
    if failures:
        for line in failures:
            print("FAIL fmgr-live-object: " + line)
        return 1
    print("ok   fmgr-live-object: %d assertions, no dump" % count[0])
    return 0


# ---------------------------------------------------------------- the CLI

def read_csv(path):
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if rows and list(rows[0].keys()) != COLUMNS:
        sys.exit("%s: header %r, expected %r" % (path, list(rows[0].keys()), COLUMNS))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("--symbols", default=SYMBOLS)
    ap.add_argument("-o", "--out", default=CSV_PATH)
    ap.add_argument("--write-doc", metavar="PAGE")
    ap.add_argument("--check-doc", metavar="PAGE")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    if args.check:
        return selftest()
    if args.write_doc or args.check_doc:
        rows = read_csv(args.out)
        if args.check_doc:
            return check_doc(args.check_doc, rows)
        write_doc(args.write_doc, rows)
        return 0
    if not args.xbe:
        ap.error("--xbe is required to measure (or --check/--check-doc/--write-doc)")
    if not os.path.exists(args.xbe):
        sys.exit("%s: no such file" % args.xbe)
    rows = census(args.xbe, args.symbols)
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print("ok   fmgr-live-object: %d globals, %d accesses -> %s"
          % (len(rows), sum(int(r["reads"]) + int(r["writes"]) for r in rows), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
