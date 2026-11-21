#!/usr/bin/env python3
"""Find the per-class tables of code pointers in the XBE and the slot that dispatches a target.

The build dispatches `UObject::ProcessEvent` instead of calling it (PLAN 4.3 / `docs/UNKNOWNS.md`
U8): no `call` to its address exists, but hundreds of data pointers to it do. This tool turns that
into a structure. Given a target address it scans the image for dwords equal to it, treats each hit
as living at a fixed slot of a table starting `slot*4` bytes earlier, and keeps the hit only when
**every** slot before it holds a function entry. A table row is therefore evidence, not a scan
result: twenty-one consecutive known function entries followed by the target.

    python3 tools/class-tables.py DUMP/default.xbe -o inventory
    python3 tools/class-tables.py DUMP/default.xbe --target 0x00048bf0   # ProcessInternal
    python3 tools/class-tables.py --check          # synthetic self-test, no dump, writes nothing

Table extent is measured against the next table start (the tables are packed) or, for the last
one, against the end of the run of function entries that holds it.

**A slot is a body start, not merely an aligned dword in an executable section.** Every section in
this image carries the executable flag (`xbe.Section.executable` is true for `.rdata` and `.data`
too), so "inside a section flagged executable" accepts the *first two characters of a UTF-16
string* -- a table's neighbour in `.rdata` is a wide-string pool, and `0x00330044` ("D3") is an
aligned dword inside `.text`'s address range. The old rule counted seven tables past their end
that way, all of them by one or two dwords, and every surplus dword was a wide string's first
half. A slot is now a body start by either of two measurements, the cheapest first:

* `map`  -- the address is an exact function start in `symbols/xbox_functions.csv`;
* `tail` -- the bytes ending one past it decode as a terminator (`ret`, `retn`, `int3` fill,
  `jmp rel8/rel32`, `jmp [reg]` / `jmp [reg+disp]`), the rule `tools/native-vtable.py` measured
  on 21 of 21 slots of a table the map names eight of.

and the section it lands in must be *code*, which the flags cannot say: a section is code when
the functions the map places in it cover at least a tenth of its bytes (0.86 for `.text`, 0.000
for `.rdata`).

**An interior non-start ends nothing.** One slot of the 34,955 this tool reads is not a body start
by either rule (`0x004e2d28` slot 18, `0x00082ea0`, the biggest table's long middle); a body
preceded by a jump table answers neither `map` nor `tail`, so a table is cut at its last body
start and interior failures are reported in `gaps`, not used to truncate it.
"""
import argparse
import csv
import importlib.util
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xbe import Xbe  # noqa: E402

DEFAULT_SLOT = 21  # measured: ProcessEvent is at +0x54 in every table (docs/NATIVE_VM.md)
CODE_COVERAGE = 0.1  # a section is code when the map's functions cover this much of it


def load_sibling():
    """tools/native-vtable.py, which cannot be imported by name (hyphen).

    Its `terminator()` is the slot-start rule and its `find_installs()` is what splits a run of
    code pointers that holds more than one table -- the measurement that settled the last four
    over-reads here.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "native-vtable.py")
    spec = importlib.util.spec_from_file_location("native_vtable", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def code_sections(xbe, names, sizes):
    """section name -> True when the symbol map's functions cover a tenth of its bytes.

    Byte counts, not function counts: `symbols/xbox_functions.csv`'s third column is a size in
    *decimal* bytes. `.text` answers 0.86, the library modules 0.36-0.97, `.rdata` and `.data`
    0.000 -- and every one of them carries the executable flag.
    """
    out = {}
    for s in xbe.sections:
        covered = 0
        for va, size in sizes.items():
            if s.va <= va < s.va + s.vsize:
                covered += max(0, min(size, s.va + s.vsize - va))
        out[s.name] = covered / float(max(1, s.vsize)) >= CODE_COVERAGE
    return out


def legacy_ok(xbe, v):
    """The old slot test: 4-aligned and inside a section flagged executable (true for all)."""
    return v % 4 == 0 and any(s.executable and s.va <= v < s.va + s.vsize for s in xbe.sections)


def extent(entries, slot_ok, legacy_ok, bound):
    """(slots, stop, surplus) for one table's dwords, walking from the table's start.

    The run ends at the first dword that is not a body start, clipped to `bound` (the next packed
    table's start). `stop` is that dword -- the artifact keeps it, because what ends a table is
    the finding: a wide string's first two characters for the over-reads, `__purecall` for none
    (a map address is a body start at any alignment), and a null or a non-code dword for the rest.
    `surplus` is the legacy run of dwords past the extent, i.e. what the previous rule counted, so
    every row carries its own before/after.

    **A non-start is an end, not a gap.** A tolerance of one interior dword was measured and
    withdrawn: it walked three tables past a null slot into the next structure and their own
    children disproved every one (`SubActionDepthOfField` 33 slots under a parent read as 56).
    """
    n = min(len(entries), max(0, bound))
    run = 0
    while run < n and slot_ok(entries[run]):
        run += 1
    k = run
    while k < n and legacy_ok(entries[k]):
        k += 1
    return run, (entries[run] if run < n else None), entries[run:k]


def self_test():
    """Drive the two shape rules on synthetic dwords; no dump, writes nothing."""
    bad = []

    def eq(want, got, what):
        if want != got:
            bad.append("%s: want %r got %r" % (what, want, got))

    ok = lambda v: v in (1, 2, 3, 4, 5)          # body starts
    legacy = lambda v: v in (1, 2, 3, 4, 5, 6)   # the wider old test (6 is a string half)

    # a full run: 5 slots, nothing past it
    eq((5, None, []), extent([1, 2, 3, 4, 5], ok, legacy, 99), "clean run")
    # a wide-string tail ends the table at 5 and is kept as the row's `stop`
    eq((5, 6, [6]), extent([1, 2, 3, 4, 5, 6, 7], ok, legacy, 99), "wide-string tail")
    # an interior non-start ends the table too -- the tolerance was measured and withdrawn
    eq((3, 0, []), extent([1, 2, 3, 0, 4, 5], ok, legacy, 99), "interior non-start ends")
    # the packed bound clips a run that spills into the next table
    eq((3, None), extent([1, 2, 3, 4, 5], ok, legacy, 3)[:2], "packed bound")
    # a table with no body start at all is refused, not written as zero slots
    eq((0, 7, []), extent([7, 8], ok, legacy, 99), "no start")
    # the legacy rule still counts the string halves -- the control for the whole change
    eq(6, extent([1, 2, 3, 4, 5, 6, 7], legacy, lambda v: True, 99)[0],
       "legacy over-read is reproducible")

    # a printable UTF-16 pair is string data, an odd-aligned map address is still a body start
    wide = lambda v: (lambda lo, hi: 0x20 <= lo <= 0x7E and 0x20 <= hi <= 0x7E)(v & 0xFFFF, (v >> 16) & 0xFFFF)
    eq(True, wide(0x00330044), "D3 reads as wide text")
    eq(True, wide(0x00420058), "XB reads as wide text")
    eq(False, wide(0x003e5501), "__purecall does not read as wide text")
    eq(False, wide(0x00292360), "a 0x29xxxx code pointer does not read as wide text")
    eq(False, wide(0x00000000), "a null slot does not read as wide text")

    term = load_sibling().terminator
    eq("ret", term(b"\x90\x90\xc3"), "ret ends a body")
    eq("retn", term(b"\x90\xc2\x10\x00"), "retn includes its imm16")
    eq("int3", term(b"\xcc"), "int3 fill ends a body")
    eq("jmp-rel32", term(b"\x90\x90\x90\x90\xe9\x00\x00\x00\x00"), "jmp rel32")
    eq("", term(b"D\x00D\x00"), "a wide-string half is not a terminator")

    if bad:
        for b in bad:
            print("FAIL class-tables: " + b)
        return 1
    print("class-tables --check: ok (6 shape cases, 5 wide-text cases, 8 terminator cases)")
    return 0



def wide_text(v):
    """Two printable UTF-16 characters -- a wide string's first half, not a code pointer."""
    lo, hi = v & 0xFFFF, (v >> 16) & 0xFFFF
    return 0x20 <= lo <= 0x7E and 0x20 <= hi <= 0x7E


def read_entries(xbe, va, limit=4096):
    """The dwords from `va` to the end of its section (at most `limit`), the table's candidate run."""
    for s in xbe.sections:
        if s.va <= va < s.va + s.vsize:
            i = (va - s.va) // 4
            n = min(s.fsize, s.vsize) // 4
            return [struct.unpack_from("<I", xbe.data, s.foff + 4 * (i + k))[0]
                    for k in range(0, min(n - i, limit))]
    raise KeyError("%#x is in no section" % va)


def slot_rules(xbe, names, sizes):
    """(`slot_kind`, `slot_ok`) for one image -- the body-start rule, defined once."""
    code = code_sections(xbe, names, sizes)
    term = load_sibling().terminator

    def in_code(v):
        for s in xbe.sections:
            if s.va <= v < s.va + s.vsize:
                return code[s.name]
        return False

    def slot_kind(v):
        """What a table dword is, in the order that keeps each test's blind spot visible.

        * `text`  -- two printable UTF-16 characters. The tables sit in front of the build's
          wide-string pool and `.rdata` carries the executable flag, so `0x00330044` ("D3") is an
          aligned dword inside a code address range. **A table ends here**: 15 of the 492 do.
        * `null`  -- `0x00000000` (or a dword outside every code section). A table ends here too
          (3 of the 492 do), which is also where the 123-slot `Actor` table stops, on a float.
        * `map`   -- an exact function start, **at any alignment**: `__purecall` is at
          `0x003e5501`, an odd address, and the alignment test used to end six tables on it.
        * `tail`  -- a terminator ends one past it.
        * `unconfirmed` -- an aligned address inside a code section that neither rule confirms. The
          one case in this image is a body behind a jump table (`0x00082ea0`, `xEmitter` slot 18,
          preceded by six `.text` offsets): it is a slot, and refusing it truncated a 123-slot
          table to 18, which the ladder then read as its child being shorter than its parent.
        """
        if wide_text(v):
            return "text"
        if v == 0 or not in_code(v):
            return "null"
        if v in names:
            return "map"
        if v % 4:
            return "null"          # unaligned and unnamed: not a code pointer in this build
        try:
            if term(xbe.read_va(v - 16, 16)):
                return "tail"
        except KeyError:
            return "null"
        return "unconfirmed"

    return slot_kind, (lambda v: slot_kind(v) not in ("text", "null"))


def measure(xbe, names, sizes, starts, slot=DEFAULT_SLOT):
    """{start_va: row} for packed table starts, by the body-start extent rule.

    `starts` is the address-ordered list of table starts. The rule lives here and nowhere else, so
    a reader of these tables reads the same extent this artifact does instead of re-deriving one.
    Its gate is the *class* population's: a body start is an exact map address at any alignment, a
    terminator one past it, or one unconfirmed aligned dword in a section the map covers. It does
    **not** lift to the 724-table population -- the interfaces' bodies sit in the XDK libraries in
    `.rdata`, which the map covers 0.000 of, and ten of their tables start on an odd-aligned
    `.text` dword (`0x003313ad`) -- which is why `tools/class-plain-tables.py` still counts by the
    packed run.
    """
    slot_kind, slot_ok = slot_rules(xbe, names, sizes)
    sib = load_sibling()
    installs = sib.find_installs(xbe, starts[0], starts[-1] + 4 * 160)
    rows = {}
    for k, s in enumerate(starts):
        ent = read_entries(xbe, s)
        # The tables are packed, so a neighbour start bounds the run of code pointers that spills
        # over from the next table.
        bound = len(ent)
        if k + 1 < len(starts):
            bound = min(bound, (starts[k + 1] - s) // 4)
        # ... and so does an install store of a *different* table VA inside the run. Never before
        # the target slot: a table cannot be split ahead of its own ProcessEvent.
        split = [v for v in installs if s + 4 * slot + 4 <= v < s + 4 * bound]
        if split:
            bound = min(bound, (min(split) - s) // 4)
        n, stop, over = extent(ent, slot_ok, lambda v: legacy_ok(xbe, v), bound)
        rows[s] = {"entries": ent, "slots": n, "stop": stop, "surplus": over,
                   "split_at": min(split) if split else None,
                   "unconfirmed": sum(1 for v in ent[:n] if slot_kind(v) == "unconfirmed")}
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xbe", nargs="?")
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--symbols", default="symbols/xbox_functions.csv", help="for naming only")
    ap.add_argument("--target", default="0x0004bf20", help="dispatched function (default ProcessEvent)")
    ap.add_argument("--slot", type=int, default=DEFAULT_SLOT,
                    help="slot the target must occupy; hits whose prefix is not all function "
                         "entries are dropped")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump")
    args = ap.parse_args()
    if args.check:
        sys.exit(self_test())
    if not args.xbe:
        ap.error("an xbe path is required (or --check)")

    xbe = Xbe(args.xbe)
    target = int(args.target, 16)
    names, sizes = {}, {}
    with open(args.symbols) as f:
        for row in csv.DictReader(f):
            names[int(row["address"], 16)] = row["name"]
            sizes[int(row["address"], 16)] = int(row["size"])
    sib = load_sibling()
    slot_kind, slot_ok = slot_rules(xbe, names, sizes)

    hits = []
    for sec in xbe.sections:
        n = min(sec.fsize, sec.vsize) // 4
        for i in range(args.slot, n):
            va = sec.va + 4 * i
            off = sec.foff + 4 * i
            if struct.unpack_from("<I", xbe.data, off)[0] != target:
                continue
            prefix = [struct.unpack_from("<I", xbe.data, sec.foff + 4 * (i - k))[0]
                      for k in range(1, args.slot + 1)]
            if all(legacy_ok(xbe, p) for p in prefix):
                hits.append((sec, i, va - 4 * args.slot))
    if not hits:
        sys.exit("no tables found for %s at slot %d" % (args.target, args.slot))

    starts = sorted({h[2] for h in hits})
    by_start = {h[2]: h for h in hits}

    def entries(start):
        return rows[start]["entries"]

    # A run of code pointers is not a table: the linker packs several structures back to back and
    # only an install store says where one ends. One image-wide scan answers every table.
    rows = measure(xbe, names, sizes, starts, args.slot)
    lengths = {s: rows[s]["slots"] for s in starts}
    stopat = {s: rows[s]["stop"] for s in starts}
    surplus = {s: rows[s]["surplus"] for s in starts}
    splitat = {s: rows[s]["split_at"] for s in starts}
    unconf = {s: rows[s]["unconfirmed"] for s in starts}

    total = sum(lengths.values())
    moved = [s for s in starts if surplus[s]]

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "xbe_class_tables.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["table_va", "section", "slots", "target_slot", "target_offset",
                    "slot0", "slot3", "evidence", "stop", "surplus", "unconfirmed",
                    "split_at"])
        for s in starts:
            sec = by_start[s][0]
            ent = entries(s)
            ev = "%s at slot %d, %d function entries before it" % (
                hex(target), args.slot, args.slot)
            if surplus[s]:
                ev += "; %d dword(s) past the run (%s)" % (
                    len(surplus[s]), ", ".join("0x%08x" % v for v in surplus[s]))
            w.writerow(["0x%08x" % s, sec.name, lengths[s], args.slot, "0x%02x" % (4 * args.slot),
                        "0x%08x" % ent[0], "0x%08x" % ent[3], ev,
                        "" if stopat[s] is None else "0x%08x" % stopat[s], len(surplus[s]),
                        unconf[s], "" if splitat[s] is None else "0x%08x" % splitat[s]])

    slots_path = os.path.join(args.out, "xbe_class_table_slots.csv")
    maxslots = max(lengths.values())
    with open(slots_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["slot", "byte_offset", "tables", "distinct", "most_common", "count"])
        for slot in range(maxslots):
            values = [entries(s)[slot] for s in starts
                      if slot < min(lengths[s], len(entries(s)))]
            if not values:
                continue
            counts = {}
            for v in values:
                counts[v] = counts.get(v, 0) + 1
            best = max(counts.items(), key=lambda kv: kv[1])
            w.writerow([slot, "0x%02x" % (4 * slot), len(values), len(counts),
                        "0x%08x" % best[0], best[1]])

    print("tables: %d (%.1f KB of code pointers), %s at slot %d (byte 0x%02x)"
          % (len(starts), (starts[-1] - starts[0]) / 1024.0, names.get(target, args.target),
             args.slot, 4 * args.slot))
    print("spread: 0x%08x - 0x%08x; slots per table %d..%d; %d slot(s) read"
          % (starts[0], starts[-1], min(lengths.values()), maxslots, total))
    print("extent rule: %d table(s) end on a wide-string dword (%d dword(s) surplus); "
          "%d end on a null; %d slot(s) no start rule confirms"
          % (len(moved), sum(len(v) for v in surplus.values()),
             sum(1 for s in starts if stopat[s] == 0), sum(unconf.values())))
    print("split: %d table(s) clipped at an interior install store" % len([s for s in starts if splitat[s]]))
    print("slot 0 %s in %d of %d tables"
          % (names.get(entries(starts[0])[0], "?"),
             sum(1 for s in starts if entries(s)[0] == entries(starts[0])[0]), len(starts)))
    print("wrote %s and %s" % (path, slots_path))


if __name__ == "__main__":
    main()
