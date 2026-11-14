#!/usr/bin/env python3
"""Which slots of the file-manager table the image actually dispatches, and what each of those bodies answers (U5)

#515 censused the table the save chain asks (`[0x005bf514]` -> `0x005c1a0c` -> vtable `0x004d2fd0`) and found no slot
that answers a directory: `+0x04`/`+0x08` are `FFileManagerError_CreateFileReader`/`_CreateFileWriter` -- bodies that
log a line and answer 0 -- and the rest are stubs of eight bytes or less. That answers *what the table says*. It does
not answer *whether anything calls those two slots*, which is what decides whether the build can open a file at all
through this object.

So this censuses the other direction. Every `call DWORD PTR [reg+disp]` in the image is a virtual dispatch; a site
belongs to this object when `reg` was loaded from the object's vtable (`mov reg,[base]`, then `base` from
`ds:0x5bf514`) or straight from `ds:0x5bf514`. The walk is exact-length: the instruction that *ends* at a load is the
one executed last, a call or a jump between the two ends the search, and an unread chain is dropped rather than counted
as "not this object".

    python3 tools/fmgr-dispatch.py --xbe $DUMP/default.xbe -o inventory/fmgr_dispatch.csv
    python3 tools/fmgr-dispatch.py --write-doc docs/systems/CHECKPOINTS.md
    python3 tools/fmgr-dispatch.py --check-doc docs/systems/CHECKPOINTS.md
    python3 tools/fmgr-dispatch.py --check      # synthetic instructions, no dump, no writes

Each slot row also carries what its body answers, read the same way: a body that logs a wide literal shows it, a body
that only sets a register and returns shows the instructions it runs. Addresses, literals and counts only.
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

COLUMNS = ["slot", "target", "name", "size", "answers", "sites", "at", "functions"]
BLOCK_MARK = ("<!-- generated:fmgr-dispatch -->", "<!-- /generated:fmgr-dispatch -->")
CSV_PATH = os.path.join("inventory", "fmgr_dispatch.csv")
SYMBOLS = os.path.join("symbols", "xbox_functions.csv")

FM_GLOBAL = 0x005BF514
FM_GLOBAL_TEXT = "ds:0x5bf514"
FM_TABLE = 0x004D2FD0
RESOLVER = 0x00034240
STUB_WINDOW = 32
MAX_FUNCS = 4
MAX_AT = 6
MIN_LEN = 3

IMM = re.compile(r"\$0x([0-9a-f]+)")
HEX = re.compile(r"0x([0-9a-f]+)")
OBJDUMP_LINE = re.compile(r"^\s*([0-9a-f]+):\s+(?:[0-9a-f]{2} )+\s*(.*)$")
CALL = re.compile(r"^call\s+DWORD PTR \[(e[a-z]{2})(?:\+0x([0-9a-f]+))?\]$")
WRITE = re.compile(r"^(mov|xor|add|sub|and|or|pop|inc|dec|lea)\s+(e[a-z]{2}),(.*)$")
GLOBAL_LOAD = re.compile(r"^mov\s+(e[a-z]{2}),DWORD PTR ds:0x5bf514$")
VTB_LOAD = re.compile(r"^DWORD PTR \[(e[a-z]{2})(?:\+0x([0-9a-f]+))?\]$")


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
        return table[i][2], table[i][1]
    return None, None


def code_sections(xbe, table):
    """Sections the mapped functions are laid out in (every XBE section sets the executable flag, so the map decides)."""
    out = []
    for sec in xbe.sections:
        covered = sum(size for start, size, _n in table if sec.contains(start))
        if covered and covered * 10 >= sec.vsize:
            out.append(sec)
    return out


def terminator_before(xbe, va):
    try:
        return xbe.read_va(va - 1, 1)[0] in (0xCC, 0xC3, 0x90)
    except Exception:
        return False


def table_extent(xbe, va, table):
    starts = set(row[0] for row in table)
    slots = []
    while True:
        target = struct.unpack("<I", xbe.read_va(va + len(slots) * 4, 4))[0]
        if target not in starts and not terminator_before(xbe, target):
            break
        slots.append(target)
        if len(slots) > 0x400:
            break
    return slots


def wide_at(xbe, va, limit=120):
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


def immediates(text):
    """Immediate operand values only.

    A `$`-marked operand is an immediate. Otherwise the operand text has its displacements removed first -- the intel
    form spells one `[ebx+0x28]`, the AT&T form `0x4c9790(%eax)` -- so an address the instruction *reads* is never
    mistaken for one it names.
    """
    found = IMM.findall(text)
    if found:
        return [int(v, 16) for v in found]
    bare = re.sub(r"\[[^\]]*\]", "", text)
    bare = re.sub(r"0x[0-9a-f]+\([^)]*\)", "", bare)
    return [int(v, 16) for v in HEX.findall(bare)]


# ---------------------------------------------------------------- the dispatch chain

def writer(insns, i, reg, limit=80):
    """The instruction that last wrote `reg` before index `i`, as (index, operand text), or None.

    A call, a jump or a return between the two ends the search: the register's value at the site would then come from
    the callee, which this read does not follow.
    """
    for j in range(i - 1, max(-1, i - limit), -1):
        text = insns[j][1]
        if text.startswith(("call", "ret", "jmp")):
            return None
        m = WRITE.match(text)
        if m and m.group(2) == reg:
            return j, m.group(3).strip()
    return None


def dispatch(insns):
    """(index, slot) per virtual call whose receiver is the file-manager global, one or two links back."""
    out = []
    for i, (_va, text) in enumerate(insns):
        m = CALL.match(text)
        if not m:
            continue
        reg, disp = m.group(1), int(m.group(2) or "0", 16)
        w = writer(insns, i, reg)
        if not w:
            continue
        j, value = w
        if GLOBAL_LOAD.match("mov %s,%s" % (reg, value)):
            out.append((i, disp, "direct"))
            continue
        v = VTB_LOAD.match(value)
        if not v:
            continue
        base = v.group(1)
        w2 = writer(insns, j, base)
        if w2 and w2[1].startswith("DWORD PTR " + FM_GLOBAL_TEXT):
            out.append((i, disp, "vtable"))
    return out


def answers(xbe, target, size):
    """What a slot's body answers: the wide literal it logs, or the instructions it runs."""
    body_end = target + (size if size else STUB_WINDOW)
    body = xbe.read_va(target, max(1, body_end - target))
    ran, literals = [], []
    for _va, text in instructions(body, target):
        if text.startswith(".byte"):
            break
        if text.startswith("call"):
            continue
        literals_hit = [lit for lit in (wide_at(xbe, v) for v in immediates(text)) if lit]
        if literals_hit:
            literals += literals_hit
            continue
        if len(ran) < 3:
            ran.append(text)
        if text.startswith(("ret", "jmp")):
            break
    if literals:
        return 'logs "%s"' % literals[0]
    if ran:
        return " ".join(ran)
    return ""


# ---------------------------------------------------------------- the measurement

def census(xbe_path, symbols=SYMBOLS):
    xbe = load_xbe(xbe_path)
    table = load_symbols(symbols)
    slots = table_extent(xbe, FM_TABLE, table)

    sites = {}
    for sec in code_sections(xbe, table):
        insns = list(instructions(xbe.read_va(sec.va, sec.vsize), sec.va))
        for i, disp, kind in dispatch(insns):
            sites.setdefault(disp, []).append((insns[i][0], kind))

    rows = []
    for index, target in enumerate(slots):
        slot = index * 4
        name, size = containing(table, target)
        found = sorted(sites.get(slot, []))
        functions = []
        for site, _kind in found:
            fn = containing(table, site)[0] or "no committed function"
            if fn not in functions:
                functions.append(fn)
        rows.append({
            "slot": "%#04x" % slot,
            "target": "%#010x" % target,
            "name": name or "",
            "size": str(size) if size else "",
            "answers": answers(xbe, target, size),
            "sites": str(len(found)),
            "at": " ".join("%#010x" % s for s, _k in found[:MAX_AT]),
            "functions": " ".join(functions[:MAX_FUNCS]),
        })
    return rows


# ---------------------------------------------------------------- the page

def render(rows):
    total = sum(int(r["sites"]) for r in rows)
    busiest = sorted(rows, key=lambda r: -int(r["sites"]))[:4]

    def table(headers, body):
        lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
        lines += ["| " + " | ".join(cells) + " |" for cells in body]
        return lines

    out = ["<!-- generated:fmgr-dispatch -->"]
    out += ["**The dispatch census.** %d virtual calls reach the file-manager object through its own vtable"
            " (`ds:%#010x`), spread over %d of the table's %d slots. The busiest are %s."
            % (total, FM_GLOBAL, sum(1 for r in rows if r["sites"] != "0"), len(rows),
               ", ".join("`+%s` (%s sites)" % (r["slot"], r["sites"]) for r in busiest)), ""]
    out += ["**Per slot: what the entry answers, and how many sites call it.**", ""]
    out += table(["slot", "target", "name", "size", "answers", "sites", "at", "in"],
                 [["`%s`" % r["slot"], "`%s`" % r["target"], r["name"] or "-", r["size"] or "-",
                   ("`%s`" % r["answers"]) if r["answers"] else "-", r["sites"], r["at"] or "-",
                   r["functions"] or "-"] for r in rows])
    out += ["<!-- /generated:fmgr-dispatch -->"]
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
    print("ok   fmgr-dispatch: wrote the generated block of %s" % path)


def check_doc(path, rows):
    new = splice(path, render(rows))
    with open(path) as fh:
        old = fh.read()
    if new == old:
        print("ok   fmgr-dispatch: %s matches the committed table" % path)
        return 0
    for n, (a, b) in enumerate(zip(new.splitlines(), old.splitlines()), 1):
        if a != b:
            print("FAIL fmgr-dispatch: %s line %d\n  page:  %r\n  table: %r" % (path, n, b, a))
            return 1
    print("FAIL fmgr-dispatch: %s length %d vs %d" % (path, len(new.splitlines()), len(old.splitlines())))
    return 1


# ---------------------------------------------------------------- the dry check

def selftest():
    failures = []
    count = [0]

    def want(label, got, expect):
        count[0] += 1
        if got != expect:
            failures.append("%s: %r != %r" % (label, got, expect))

    # a two-link chain: base <- global, vtable <- [base], call [vtable+disp]
    chain = [
        (0x1000, "mov    ecx,DWORD PTR ds:0x5bf514"),
        (0x1004, "push   0x0"),
        (0x1006, "mov    edx,DWORD PTR [ecx]"),
        (0x1008, "mov    eax,DWORD PTR [esp+0x4]"),
        (0x100c, "call   DWORD PTR [edx+0x4]"),
    ]
    want("vtable chain", [slot for _i, slot, _k in dispatch(chain)], [0x4])
    want("vtable chain kind", [kind for _i, _s, kind in dispatch(chain)], ["vtable"])
    direct = [(0x2000, "mov    edx,DWORD PTR ds:0x5bf514"), (0x2006, "call   DWORD PTR [edx+0x38]")]
    want("direct chain", [slot for _i, slot, _k in dispatch(direct)], [0x38])
    # a call between the load and the site ends the walk
    broken = [(0x3000, "mov    ecx,DWORD PTR ds:0x5bf514"), (0x3006, "call   0x1234"),
              (0x300b, "mov    edx,DWORD PTR [ecx]"), (0x300d, "call   DWORD PTR [edx+0x4]")]
    want("call ends the walk", dispatch(broken), [])
    # a different global is not this object
    other = [(0x4000, "mov    ecx,DWORD PTR ds:0x5fbe04"), (0x4006, "mov    edx,DWORD PTR [ecx]"),
             (0x4008, "call   DWORD PTR [edx+0x4]")]
    want("other global", dispatch(other), [])
    # a register clobbered by an arithmetic write is not a chain either
    clobbered = [(0x5000, "mov    ecx,DWORD PTR ds:0x5bf514"), (0x5006, "xor    ecx,ecx"),
                 (0x5008, "mov    edx,DWORD PTR [ecx]"), (0x500a, "call   DWORD PTR [edx+0x4]")]
    want("clobbered", dispatch(clobbered), [])
    # immediates: the intel form has no `$`, the at&t form does
    want("intel immediate", immediates("mov    eax,0x4c85ac"), [0x4C85AC])
    want("at&t immediate", immediates("mov    $0x4c85ac,%eax"), [0x4C85AC])
    want("no immediate", immediates("cmp    DWORD PTR [ebx+0x28],ebp"), [])
    table = [(0x1000, 0x100, "first"), (0x1100, 0x40, "second")]
    want("containing", containing(table, 0x1050), ("first", 0x100))
    want("containing past end", containing(table, 0x1140), (None, None))

    rows = [
        {"slot": "0x04", "target": "0x000341a0", "name": "FFileManagerError_CreateFileReader", "size": "24",
         "answers": "logs `Called FFileManagerError::CreateFileReader`", "sites": "20", "at": "0x000121df",
         "functions": "FUN_00012110"},
        {"slot": "0x38", "target": "0x00034210", "name": "FUN_00034210", "size": "33", "answers": "",
         "sites": "0", "at": "", "functions": ""},
    ]
    page = render(rows)
    want("render markers", (page.splitlines()[0], page.splitlines()[-1]), BLOCK_MARK)
    want("total sites", "20 virtual calls" in page, True)
    want("slot row", "`0x04`" in page and "FFileManagerError_CreateFileReader" in page, True)
    want("zero row keeps its dash", "| `0x38` | `0x00034210` | FUN_00034210 | 33 | - | 0 | - | - |" in page, True)
    # a logged literal carries backticks inside a table cell; it must not be split into two cells
    width = None
    for line in page.splitlines():
        if not line.startswith("|"):
            width = None
            continue
        if width is None:
            width = line.count("|")
        elif line.count("|") != width:
            failures.append("ragged row: %r" % line)
    lines = page.splitlines()
    heads = [n for n, line in enumerate(lines)
             if line.startswith("| ") and (n == 0 or not lines[n - 1].startswith("|"))]
    want("every table has a header",
         all(n + 1 < len(lines) and lines[n + 1].startswith("|---|") for n in heads), True)
    src = open(os.path.abspath(__file__)).read()
    for name in ("--write-doc", "--check-doc", "--check", "--xbe"):
        if name not in src:
            failures.append("source lost %s" % name)
    if failures:
        for line in failures:
            print("FAIL fmgr-dispatch: " + line)
        return 1
    print("ok   fmgr-dispatch: %d assertions, no dump" % count[0])
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
    print("ok   fmgr-dispatch: %d slots, %d sites -> %s"
          % (len(rows), sum(int(r["sites"]) for r in rows), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
