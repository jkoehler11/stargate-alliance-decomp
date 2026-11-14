#!/usr/bin/env python3
"""Which directories the build's own path strings name, and which slot of the file manager the save module asks (U5)

`docs/systems/CHECKPOINTS.md` left one question open after #514: *the file manager's base directory*, because the
loader composes `\\\\Profiles\\\\<profile>\\\\<name>` and the disc keeps the same tail one `System\\\\` deeper. The cheap
next read was named as the `System\\\\` literal at `0x004c9790`, "stored at `0x00149603`, inside no committed
function".

This reads two populations instead of that one address, so the answer does not depend on guessing which function
matters:

* **every path-shaped wide literal in the image, and every instruction that names it.** A literal is path-shaped
  when its text holds a `\\` or a `/`, or is one of the directory words the save chain uses (`Saves`, `Profiles`,
  `defaultdevice`, `SavePath`, `System`, `cache`, `uvx`, `Auto`). A *site* is an instruction whose immediate operand
  is that literal's address, decoded by `objdump -D -b binary -m i386` from the section's own start -- so a byte
  pattern that happens to look like a `push` is not a site, and the site carries the instruction text and the mapped
  function that contains it.
* **the file-manager table the save module's singleton carries.** `[0x005bf514]` -> object `0x005c1a0c` -> vtable
  `0x004d2fd0`. The table's extent is read as a forward run of dwords that are plausible function starts, and each
  slot's body is swept for the same immediates, so "no slot of this table answers a directory" is a count, not an
  impression.

The third read is the one that closes the question: **a path that already has a drive or starts with a separator is
returned verbatim.** Slot `+0x3c` (`0x00034240`, 531 bytes) tests its argument for `L':'` and for a leading `L'\\\\'`
or `L'/'` and, on either hit, constructs the FString unchanged (`FUN_0001b260`, which is only an FString ctor -- it
copies the characters and nothing else). The device getter at `+0x38` is consulted only on the else arm, for a path
that is *not* rooted. So no base directory is prepended anywhere in that chain: the loader's rootless path reaches
the package loader exactly as composed.

    python3 tools/path-literals.py --xbe $DUMP/default.xbe -o inventory/path_literals.csv
    python3 tools/path-literals.py --write-doc docs/systems/CHECKPOINTS.md
    python3 tools/path-literals.py --check-doc docs/systems/CHECKPOINTS.md
    python3 tools/path-literals.py --check      # synthetic bytes, no dump, no writes

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

COLUMNS = ["literal", "va", "sites", "at", "functions"]
SLOT_COLUMNS = ["slot", "target", "name", "size", "literals"]
BLOCK_MARK = ("<!-- generated:path-literals -->", "<!-- /generated:path-literals -->")
CSV_PATH = os.path.join("inventory", "path_literals.csv")
SLOT_CSV = os.path.join("inventory", "file_manager_vtable.csv")
SYMBOLS = os.path.join("symbols", "xbox_functions.csv")

# The object the save module asks: [global] -> object -> vtable. The table address is the walk's start, not a slot.
FM_GLOBAL = 0x005BF514
FM_OBJECT = 0x005C1A0C
FM_TABLE = 0x004D2FD0
DEVICE_SLOT = 0x38
RESOLVER_SLOT = 0x3C
DEVICE_GETTER = 0x00034210
RESOLVER = 0x00034240
# The words the save/profile chain is made of, plus the cache directory the last run corrected.
VOCAB = ("saves", "savepath", "profiles", "defaultdevice", "system", "cache", "uvx", "auto")
# A slot body with no row in the map is bounded by this window, which is enough for a stub and never crosses the
# next mapped start (the walk stops at whichever comes first).
STUB_WINDOW = 32
MIN_LEN = 3
MAX_LEN = 120

IMM = re.compile(r"\$0x([0-9a-f]+)")
HEX = re.compile(r"0x([0-9a-f]+)")
OBJDUMP_LINE = re.compile(r"^\s*([0-9a-f]+):\s+(?:[0-9a-f]{2} )+\s*(.*)$")


# ---------------------------------------------------------------- wide strings, exactly

def wide_literals(data, min_len=MIN_LEN):
    """(offset, text) per printable UTF-16LE run that starts after a non-printable unit."""
    out = []
    i = 0
    n = len(data)
    while i < n - 5:
        c = struct.unpack_from("<H", data, i)[0]
        if 0x20 <= c < 0x7F:
            prev = struct.unpack_from("<H", data, i - 2)[0] if i >= 2 else 0
            if not (0x20 <= prev < 0x7F):
                j, chars = i, []
                while j < n - 1:
                    d = struct.unpack_from("<H", data, j)[0]
                    if d == 0 or not (0x20 <= d < 0x7F):
                        break
                    chars.append(chr(d))
                    j += 2
                if len(chars) >= min_len:
                    out.append((i, "".join(chars)))
                i = j + 2
                continue
        i += 2
    return out


def wide_at(xbe, va, limit=MAX_LEN):
    """The wide string starting at `va`, or None when the bytes there are not one."""
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
    if len(chars) < MIN_LEN:
        return None
    return "".join(chars)


def path_shaped(text):
    """A literal counts as a path when it shows a separator or is one of the chain's own words."""
    low = text.lower()
    if "\\" in text or "/" in text:
        return True
    return low in VOCAB or low.startswith("..")


# ---------------------------------------------------------------- the instruction sweep

def instructions(blob, base_va):
    """(va, text) per instruction, decoded by objdump from the start of the blob."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
        fh.write(blob)
        temp = fh.name
    try:
        out = subprocess.run(["objdump", "-D", "-b", "binary", "-m", "i386",
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


# ---------------------------------------------------------------- what a site is inside

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
    """(name, size) of the mapped function holding `va`, or (None, None)."""
    starts = [row[0] for row in table]
    i = bisect.bisect_right(starts, va) - 1
    if i >= 0 and starts[i] <= va < table[i][0] + table[i][1]:
        return table[i][2], table[i][1]
    return None, None


def code_sections(xbe, table):
    """Sections the mapped functions are laid out in.

    Every old-style XBE section sets the executable flag (`.rdata` and `.data` carry it too), so the header cannot
    separate code from data and the map has to: a section is code when the functions mapped inside it cover at least
    a tenth of its bytes. Measured, that is 0.6-0.9 of every code section against 0.02 of `.rdata` and 0.03 of
    `.data`, so the threshold separates them by more than an order of magnitude.
    """
    out = []
    for sec in xbe.sections:
        covered = sum(size for start, size, _n in table if sec.contains(start))
        if covered and covered * 10 >= sec.vsize:
            out.append(sec)
    return out


def terminator_before(xbe, va):
    """True when the byte before `va` ends a function (padding fill or a return)."""
    try:
        prev = xbe.read_va(va - 1, 1)[0]
    except Exception:
        return False
    return prev in (0xCC, 0xC3, 0x90)


def table_extent(xbe, va, table):
    """Slots of the vtable that starts at `va`: the forward run of plausible function starts.

    A slot is plausible when its target is a mapped function start or sits just past fill/return padding. The run
    stops at the first dword that is not, which for `0x004d2fd0` is the zero at `+0x64` that separates the table from
    the wide-string pool behind it.
    """
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


# ---------------------------------------------------------------- the measurement

def census(xbe_path, symbols=SYMBOLS):
    xbe = load_xbe(xbe_path)
    table = load_symbols(symbols)
    code = code_sections(xbe, table)

    candidates = {}
    for sec in xbe.sections:
        if sec in code:
            continue
        for off, text in wide_literals(xbe.read_va(sec.va, sec.vsize)):
            if len(text) > MAX_LEN or not path_shaped(text):
                continue
            candidates[sec.va + off] = text

    found = {va: [] for va in candidates}
    for sec in code:
        for va, text in instructions(xbe.read_va(sec.va, sec.vsize), sec.va):
            for value in immediates(text):
                if value in found:
                    found[value].append((va, text))

    rows = []
    for va in sorted(candidates):
        name, _size = None, None
        sites = sorted(found[va])
        functions = []
        for site, _text in sites:
            fn, _s = containing(table, site)
            fn = fn or "no committed function"
            if fn not in functions:
                functions.append(fn)
        rows.append({
            "literal": candidates[va],
            "va": "%#010x" % va,
            "sites": str(len(sites)),
            "at": " ".join("%#010x" % s for s, _t in sites[:6]),
            "functions": " ".join(functions[:4]),
        })

    slot_rows = []
    for index, target in enumerate(table_extent(xbe, FM_TABLE, table)):
        name, size = containing(table, target)
        body_end = target + (size if size else STUB_WINDOW)
        body = xbe.read_va(target, max(1, body_end - target))
        literals = []
        for _va, text in instructions(body, target):
            for value in immediates(text):
                lit = wide_at(xbe, value)
                if lit is not None and lit not in literals:
                    literals.append(lit)
        slot_rows.append({
            "slot": "%#04x" % (index * 4),
            "target": "%#010x" % target,
            "name": name or "",
            "size": str(size) if size else "",
            "literals": " | ".join(literals),
        })
    return rows, slot_rows


def load_xbe(path):
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("xbe", os.path.join(here, "xbe.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Xbe(path)


# ---------------------------------------------------------------- the page

def render(rows, slot_rows):
    def table(headers, body):
        lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
        lines += ["| " + " | ".join(cells) + " |" for cells in body]
        return lines

    named = [r for r in rows if r["sites"] != "0"]
    total_sites = sum(int(r["sites"]) for r in rows)
    directory = [r for r in rows
                 if any(w in r["literal"].lower() for w in ("saves", "profiles", "system", "uvx", "defaultdevice"))]

    out = ["<!-- generated:path-literals -->"]
    out += ["**The sweep.** %d path-shaped wide literals in the image's data sections, %d of them named by at least"
            " one instruction -- %d sites in all, plus the %d no instruction names."
            % (len(rows), len(named), total_sites, len(rows) - len(named)), ""]
    out += ["**Every literal that names a save, profile or system directory**, with the instructions that carry it"
            " (at most six addresses per literal, at most four containing functions):", ""]
    out += table(["literal", "address", "sites", "at", "in"],
                 [["`%s`" % r["literal"], "`%s`" % r["va"], r["sites"], r["at"], r["functions"]]
                  for r in directory])
    out += ["", "**The file-manager table the save module's singleton carries** (`[%#010x]` -> `%#010x` -> table"
                " `%#010x`): every slot, and the wide literals its body names." % (FM_GLOBAL, FM_OBJECT, FM_TABLE), ""]
    out += table(["slot", "target", "name", "size", "literals"],
                 [["`%s`" % r["slot"], "`%s`" % r["target"], r["name"] or "-", r["size"] or "-",
                   ", ".join("`%s`" % lit for lit in r["literals"].split(" | ")) if r["literals"] else "-"]
                  for r in slot_rows])
    out += ["<!-- /generated:path-literals -->"]
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


def write_doc(path, rows, slot_rows):
    new = splice(path, render(rows, slot_rows))
    with open(path, "w") as fh:
        fh.write(new)
    print("ok   path-literals: wrote the generated block of %s" % path)


def check_doc(path, rows, slot_rows):
    new = splice(path, render(rows, slot_rows))
    with open(path) as fh:
        old = fh.read()
    if new == old:
        print("ok   path-literals: %s matches the committed tables" % path)
        return 0
    for n, (a, b) in enumerate(zip(new.splitlines(), old.splitlines()), 1):
        if a != b:
            print("FAIL path-literals: %s line %d\n  page:  %r\n  table: %r" % (path, n, b, a))
            return 1
    print("FAIL path-literals: %s length %d vs %d"
          % (path, len(new.splitlines()), len(old.splitlines())))
    return 1


# ---------------------------------------------------------------- the dry check

def selftest():
    failures = []
    count = [0]

    def want(label, got, expect):
        count[0] += 1
        if got != expect:
            failures.append("%s: %r != %r" % (label, got, expect))

    # a wide run is a literal only where a non-printable unit precedes it
    blob = "abc".encode("utf-16-le") + b"\x00\x00" + "c\\d".encode("utf-16-le") + b"\x00\x00"
    want("wide_literals", [t for _o, t in wide_literals(blob)], ["abc", "c\\d"])
    want("wide_literals offset", wide_literals(blob)[1][0], 8)
    want("wide_literals min_len", wide_literals("ab".encode("utf-16-le") + b"\x00\x00", 3), [])
    # a suffix of a run is not its own literal
    want("no suffix rows", len(wide_literals("abcd".encode("utf-16-le") + b"\x00\x00")), 1)

    # what counts as path-shaped
    want("backslash", path_shaped("System\\"), True)
    want("slash", path_shaped("../System/"), True)
    want("vocabulary", path_shaped("Saves"), True)
    want("relative", path_shaped(".."), True)
    want("prose", path_shaped("Startup time %f"), False)
    want("case", path_shaped("SAVEPATH"), True)

    # an immediate is the `$`-marked operand, never a displacement
    want("immediate", immediates("push   $0x4c9790"), [0x4C9790])
    want("displacement", immediates("mov    0x4c9790(%eax),%ecx"), [])
    want("both", immediates("movl   $0x4d0ff8,0x2003c(%esp)"), [0x4D0FF8])

    # a site is attributed to the mapped function that spans it
    table = [(0x1000, 0x100, "first"), (0x1100, 0x40, "second")]
    want("containing", containing(table, 0x1050), ("first", 0x100))
    want("containing edge", containing(table, 0x1100), ("second", 0x40))
    want("containing last byte", containing(table, 0x10FF), ("first", 0x100))
    want("containing last", containing(table, 0x1139), ("second", 0x40))
    want("containing past end", containing(table, 0x1140), (None, None))

    # a table extent is the forward run of plausible slots and stops at the first dword that is not
    class Fake:
        def __init__(self, blob, base):
            self.blob, self.base = blob, base

        def read_va(self, va, n):
            off = va - self.base
            if off < 0 or off + n > len(self.blob):
                raise ValueError("out of range")
            return self.blob[off:off + n]
    # slot 0 and 2 are mapped starts, slot 1 ends in fill then an unmapped start, slot 3 is data
    pointer_run = struct.pack("<IIIII", 0x1000, 0x1100, 0x2000, 0x3000, 0x9999)
    fill = bytes([0x90]) * 8 + bytes([0xCC]) + bytes([0x90]) * 7
    fake = Fake(pointer_run + fill, 0x4000)
    want("extent", table_extent(fake, 0x4000, [(0x1000, 4, "a"), (0x1100, 4, "b"), (0x2000, 4, "c")]),
         [0x1000, 0x1100, 0x2000])

    # the page is generated from the two committed tables alone
    rows = [
        {"literal": "..\\Saves\\", "va": "0x00523374", "sites": "1", "at": "0x002989f9", "functions": "FUN_00298980"},
        {"literal": "Startup time %f", "va": "0x004d1104", "sites": "0", "at": "", "functions": ""},
    ]
    slot_rows = [
        {"slot": "0x38", "target": "0x00034210", "name": "FUN_00034210", "size": "33", "literals": ""},
        {"slot": "0x3c", "target": "0x00034240", "name": "FUN_00034240", "size": "531", "literals": "..\\ | \\"},
    ]
    page = render(rows, slot_rows)
    want("render markers", (page.splitlines()[0], page.splitlines()[-1]), BLOCK_MARK)
    want("path literal row", "`..\\Saves\\`" in page, True)
    want("prose literal hidden", "Startup time %f" in page, False)
    want("named count", "1 path-shaped wide literals" in page or "2 path-shaped wide literals" in page, True)
    want("slot row", "`0x3c`" in page and "`0x00034240`" in page, True)
    want("empty literal cell", "| `0x38` | `0x00034210` | FUN_00034210 | 33 | - |" in page, True)
    want("no unfilled format", bool(re.search(r"%[sd](?![a-z])", page)), False)
    lines = page.splitlines()
    heads = [n for n, line in enumerate(lines)
             if line.startswith("| ") and (n == 0 or not lines[n - 1].startswith("|"))]
    want("every table has a header",
         all(n + 1 < len(lines) and lines[n + 1].startswith("|---|") for n in heads), True)
    for line in lines:
        if line.startswith("|") and line.count("|") < 3:
            failures.append("ragged row: %r" % line)

    src = open(os.path.abspath(__file__)).read()
    for name in ("--write-doc", "--check-doc", "--check", "--xbe"):
        if name not in src:
            failures.append("source lost %s" % name)
    if failures:
        for line in failures:
            print("FAIL path-literals: " + line)
        return 1
    print("ok   path-literals: %d assertions, no dump" % count[0])
    return 0


# ---------------------------------------------------------------- the CLI

def read_csv(path, columns):
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if rows and list(rows[0].keys()) != columns:
        sys.exit("%s: header %r, expected %r" % (path, list(rows[0].keys()), columns))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("--symbols", default=SYMBOLS)
    ap.add_argument("-o", "--out", default=CSV_PATH)
    ap.add_argument("--slots-out", default=SLOT_CSV)
    ap.add_argument("--write-doc", metavar="PAGE")
    ap.add_argument("--check-doc", metavar="PAGE")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    if args.check:
        return selftest()
    if args.write_doc or args.check_doc:
        rows = read_csv(args.out, COLUMNS)
        slot_rows = read_csv(args.slots_out, SLOT_COLUMNS)
        if args.check_doc:
            return check_doc(args.check_doc, rows, slot_rows)
        write_doc(args.write_doc, rows, slot_rows)
        return 0
    if not args.xbe:
        ap.error("--xbe is required to measure (or --check/--check-doc/--write-doc)")
    if not os.path.exists(args.xbe):
        sys.exit("%s: no such file" % args.xbe)
    rows, slot_rows = census(args.xbe, args.symbols)
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    with open(args.slots_out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SLOT_COLUMNS)
        w.writeheader()
        w.writerows(slot_rows)
    print("ok   path-literals: %d literals (%d named) and %d slots -> %s, %s"
          % (len(rows), sum(1 for r in rows if r["sites"] != "0"), len(slot_rows), args.out, args.slots_out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
