#!/usr/bin/env python3
"""Is each native body a function start? The disassembler's answer, per address (PLAN 3.9).

`inventory/native_targets.csv` names a body for **883 distinct addresses**, and 274 of them have no row
in `symbols/xbox_functions.csv`. That is expected rather than suspicious -- a native's only reference is
the registration table in `.data`, so a call-graph census never makes a function out of it -- but
"expected" is not "measured", and the difference is a denominator: `docs/PATH.md` rests its session
estimate on 14,957 functions, and if the 274 are real function starts the number is wrong by 1.8%.

So this tool classifies every address on two independent axes:

* **the census**, from committed files only: does the address equal the entry point of a function in
  `symbols/xbox_functions.csv`, or fall inside one, or outside every function's `[address, address+size)`
  range? (Measured: 609 equal an entry, 0 fall inside a range, 274 fall outside all of them, so the
  question is not one of Ghidra having folded them into a neighbour.)
* **the image**, from `default.xbe`: what byte sits immediately before the address (a function boundary
  in this build is `0xCC` padding or the previous body's `ret`), what the first bytes look like as a
  prologue, and which section the address is in.

The third axis is the only one that can settle it, and it is Ghidra's own answer rather than a byte
pattern: `tools/ghidra/ghidra_scripts/NativeCensus.java` creates a function at each address and reports
whether the disassembler accepted it, the size of the body it produced, its instruction count and
whether that body ends on a flow terminator. Ghidra runs on the desktop (3-6 minutes for the import), so
its verdicts arrive separately:

    python3 tools/native-census.py --dump "$SG1_DUMP" -o inventory
    python3 tools/native-census.py --xbe default.xbe --verdicts $WORK/native-census/verdicts.csv -o inventory
    python3 tools/native-census.py --check        # the committed table, no dump

Without `--verdicts` the disassembler columns are carried over from the committed table, so `make native-census`
refreshes the cheap half without pretending the expensive half was re-measured.
    python3 tools/native-census.py --selftest     # classifier fixtures, no dump

`inventory/native_census.csv` holds one row per distinct body address. `--check` re-derives the census
column from the two committed tables (the third column of evidence needs the image, which CI does not
have) and refuses a table whose rows or split no longer reproduce.

The `extent` columns are the third reading: how far each body runs in the image. Ghidra's `body_bytes` is
the function its import created, and the image settles it -- 269 of the 274 bodies the census calls
`outside` close on a `ret imm16` at exactly that offset. Where the extent stops short of a terminator,
the body's own end (the first `cc cc` run) is the measured bound instead, and `extent_source` records
which of the two answered.

`--emit-boundaries` and `--emit-creations` print the repair rows that population earns -- one `add` in
`symbols/xbe_boundaries.csv` and the creation row in `symbols/xbe_overrides.csv` that `XbePost.java`
needs -- from the committed table alone (no dump, no image). They print rather than append: the two
files carry every other repair, so the rows are a reviewed append. A body whose extent encloses a row
the map already has is emitted with the `drop` that folds that row: an `add` on its own would overlap
the map, and the fragment inside is what U39's pairing names.

The `sweep`/`sweep_closes` columns are the fourth reading, and the one the resize population needs:
a **linear decode from the body's start** to the first unconditional terminator whose bytes to the next
map row are a *tail* rather than code -- `edge` (nothing), `pad` (`0xcc` fill of any length), `align`
(MSVC's `8b ff` hotpatch slot, `90`, `0f 1f..`) or `table` (a `switch`'s case table: dwords that all name
an offset inside the body), or a concatenation of those. The tail is what makes a refusal a measurement:
the old rule wanted a two-byte `cc cc` run and so walked through the single `int3` that 38 rows end on. It is a measurement of the row's own bytes, not of a walk's end: the island fallback above
stops at the first `cc cc` and so can read the *neighbour's* close (`0x00041000` declares 33 bytes and
the island ran 437), while a decode that must land on a terminator settles the same row at 144. The
columns reproduce the map's own size on 784 of the 877 registration rows and disagree on 36 -- the rows
that stop short -- which is what `--emit-boundaries` turns into `resize` rows.
"""
import argparse
import bisect
import collections
import csv
import importlib.util
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

TARGETS = os.path.join(ROOT, "inventory", "native_targets.csv")
FUNCTIONS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
BODIES = os.path.join(ROOT, "inventory", "native_bodies.csv")
OUT = os.path.join(ROOT, "inventory", "native_census.csv")

COLS = ["body_va", "names", "records", "classes", "section", "census", "boundary", "prologue",
        "first_bytes", "disassembler", "body_bytes", "instructions", "ends_terminal",
        "extent", "extent_source", "extent_closes", "sweep", "sweep_closes", "sweep_refused"]

SWEEP_REFUSALS = ("pad-not-after-a-terminator", "no-terminator-before-the-next-row", "undecoded",
                  "no-section", "unreadable", "close-inside-the-extension",
                  "declared-end-is-a-close")

# MSVC 7.1 openings seen at this build's real entries, longest first: the order is the classifier.
PROLOGUES = (
    (b"\x8b\xff", "hotpatch"),          # mov edi, edi
    (b"\x55\x8b\xec", "frame"),         # push ebp; mov ebp, esp
    (b"\x64\xa1", "seh"),               # mov eax, fs:[0]
    (b"\x8b\x44\x24", "arg-load"),      # mov eax, [esp+n]
    (b"\x8b\x4c\x24", "arg-load"),
    (b"\x8b\x54\x24", "arg-load"),
    (b"\x81\xec", "alloc"),             # sub esp, imm32
    (b"\x83\xec", "alloc"),             # sub esp, imm8
    (b"\xc2", "empty-return"),          # ret imm16: a body that is only its own return
    (b"\x6a", "push-imm"),
    (b"\x68", "push-imm"),
    (b"\x51", "push-reg"),              # push ecx (the common first register)
    (b"\x53", "push-reg"),
    (b"\x55", "push-reg"),
    (b"\x56", "push-reg"),
    (b"\x57", "push-reg"),
    (b"\xb8", "mov-eax"),
)

BOUNDARIES = {0xCC: "int3", 0x00: "zero", 0xC3: "return", 0xC2: "return", 0x90: "nop"}


def prologue(first):
    """Name the prologue shape from the first bytes, or `other`."""
    for pat, name in PROLOGUES:
        if first.startswith(pat):
            return name
    return "other"


def boundary(byte):
    """What the byte before an address says about it: padding, a `ret`, or data."""
    return BOUNDARIES.get(byte, "data") if byte is not None else "unreadable"


def closes_with(data, end):
    """How the byte(s) at `end` close an instruction stream, read from the image."""
    if end <= 0 or end > len(data):
        return "unreadable"
    if end >= 3 and data[end - 3] == 0xC2:
        return "ret-imm"                       # c2 imm16 is this build's commonest close
    if data[end - 1] == 0xC3:
        return "ret"
    if data[end - 1] in (0xE9, 0xEB):
        return "jmp"
    if end >= 2 and data[end - 2] == 0xFF and data[end - 1] == 0x25:
        return "jmp-ind"
    return "other"


def island_end(data, start=1):
    """The offset of the first run of two or more `0xcc` -- where the body's code stops."""
    for i in range(start, len(data) - 1):
        if data[i] == 0xCC and data[i + 1] == 0xCC:
            return i
    return None


def _decoder():
    """The instruction decoder the resize reading shares with `tools/vcall-receiver.py`.

    One decoder, not two: that tool's own docstring is where the lengths this image needs were measured
    (`0x40-0x4F` one-byte inc/dec, `F6/F7 /0` taking an immediate, `0F 31`/`0F A2` taking no modrm).
    """
    spec = importlib.util.spec_from_file_location("vcall_receiver", os.path.join(HERE, "vcall-receiver.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.decode


def terminator_kind(data, start, text):
    """The canonical name of the unconditional transfer the instruction at `start` makes, or None.

    The decoder renders `E9`/`EB` and every `jcc` as `jmp/jcc`, so the opcode byte at the instruction's
    own start decides it -- a conditional jump falls through and cannot end a body. `group5 /4` is
    `jmp r/m32`, which the same decoder names by the group rather than the mnemonic.
    """
    if text == "ret":
        return "ret"
    if text.startswith("ret imm16"):
        return "ret-imm"
    if text.startswith("jmp/jcc"):
        return "jmp" if start < len(data) and data[start] in (0xE9, 0xEB) else None
    return "jmp-ind" if text.startswith("group5 /4") else None


# What a body's bytes can run into after its closing terminator. A body's code cannot continue past an
# unconditional transfer, so whatever sits between that instruction and the next map row is one of these.
TAIL_SHAPES = ("edge", "pad", "align", "table")


def _align_at(tail, i):
    """The length of an alignment idiom at `tail[i]`, or 0. `8b ff` is MSVC's hotpatch slot."""
    for idiom in (b"\x8b\xff", b"\x66\x90", b"\x0f\x1f\x44\x00\x00", b"\x0f\x1f\x40\x00", b"\x0f\x1f\x00",
                  b"\x0f\x1f\x84\x00\x00\x00\x00\x00", b"\x90"):
        if tail.startswith(idiom, i):
            return len(idiom)
    return 0


def _table_at(tail, i, va, span):
    """The byte count of a case table at `tail[i]`, or 0: dwords that all name an offset inside the body.

    The table is the one structure that reads as code and is not, and it is what a `switch` compiles its
    dispatch to. `span` is the body's fixed window (its start to the next map row), so the test does not
    depend on the end being measured. Three entries is the floor: two dwords that happen to land in the
    body are not a table.
    """
    n = 0
    while i + 4 * (n + 1) <= len(tail):
        d = struct.unpack("<I", tail[i + 4 * n:i + 4 * (n + 1)])[0]
        if not (va <= d < va + span):
            break
        n += 1
    return 4 * n if n >= 3 else 0


def tail_shape(data, p, limit, va):
    """What follows a body's closing terminator, by name, or None when the bytes are code.

    Every row the sweep used to refuse ends on one of these rather than on nothing: 38 close on a
    `ret`/`ret imm16` followed by a single `0xcc` (the old two-byte pad rule walked straight through it),
    two dispatchers run into their own case tables, two more end in the `8b ff` slot in front of a table,
    and three continue into the row the map split them from. Naming the shape is what turns a refusal
    into a measurement -- the body's end is the terminator either way.
    """
    tail = data[p:limit]
    if not tail:
        return "edge"
    parts, i = [], 0
    while i < len(tail):
        if tail[i] == 0xCC:
            # A `0xcc` run *is* the boundary the image draws between bodies: what follows it belongs to
            # another body (22 rows here sit in a map gap whose next row starts hundreds of bytes later,
            # so a rule that had to consume the whole tail walked into the next body's code and refused).
            parts.append("pad")
            return "+".join(parts)
        elif _align_at(tail, i):
            i += _align_at(tail, i)
            shape = "align"
        else:
            n = _table_at(tail, i, va, limit)
            if not n:
                return None
            i += n
            shape = "table"
        if not parts or parts[-1] != shape:
            parts.append(shape)
    return "+".join(parts)


def sweep_body(decode, data, limit=None, floor=None, va=0):
    """(extent, closes, why): the body's end by linear decode, or why the decode will not claim one.

    A body's last instruction is an unconditional terminator, so the sweep takes an end only where one
    ends and the bytes to the next map row are a *tail* rather than code (`tail_shape`). A terminator with
    code behind it is a jump inside the body -- a `switch` case, an early `ret` -- so the walk goes on,
    which is why the first close is not the body's end.

    `va` is the body's start: the case-table test needs it, because the entries name offsets inside the body.

    `floor` is the row's declared end, and it makes the reading safe for a *resize*: when a complete
    close already sits between it and the body's own end, the bytes past the declaration are a block of
    its own (a cold tail or an uncreated body), not code the declaration cut in half, so the row is
    refused instead of grown over it.
    """
    i, prev, prev_start, closes = 0, None, None, []
    while i < len(data):
        ins = decode(data, i)
        if ins is None or ins.length <= 0:
            return None, None, "undecoded"
        prev_start = i
        i += ins.length
        prev = ins.text
        kind = terminator_kind(data, prev_start, prev)
        if kind:
            closes.append(i)
            shape = tail_shape(data, i, limit if limit is not None else len(data), va)
            # A pad cannot end a body *inside* the size the disassembler gave it: the body is longer than
            # this close, so the `0xcc` is an `int3` within the code and the walk goes on past it.
            if shape and not (floor is not None and i < floor and shape.split("+")[0] == "pad"):
                if _inner(closes, floor, i):
                    return None, None, "close-inside-the-extension"
                # A row whose own declaration already ends on a terminator does not grow over the body
                # that follows it: the code after an unconditional transfer is somebody else's, and a
                # later close with a pad behind it is the neighbour's end (10 rows here). Growth is only
                # sound where the declaration was cut mid-instruction.
                if floor is not None and i > floor and closes_with(data, floor) != "other":
                    return None, None, "declared-end-is-a-close"
                return i, kind, shape
        if limit is not None and i >= limit:
            return None, None, "no-terminator-before-the-next-row"
    if limit is not None and i == limit and prev is not None:
        kind = terminator_kind(data, prev_start, prev)
        if kind and not _inner(closes, floor, i):
            return i, kind, "edge"
    return None, None, "no-terminator-before-the-next-row"


def _inner(closes, floor, end):
    """Does a complete close sit strictly between the declared end and the body's own?"""
    if floor is None:
        return False
    return any(floor < c < end for c in closes)


def body_extent(data, body_bytes):
    """(extent, source, closes): the disassembler's extent where the image closes it, else the island's.

    Ghidra's `body_bytes` is the function that import created; 269 of the 274 bodies this table calls
    `outside` end on a `ret imm16` at exactly that offset. Where the extent does not close on a
    terminator it stopped short, and the body's own end is the first `cc cc` run -- used only as the
    fallback, because one `0xcc` between two bodies is not padding and the island end can then be the
    neighbour's.
    """
    if body_bytes and 0 < body_bytes <= len(data):
        kind = closes_with(data, body_bytes)
        if kind != "other":
            return body_bytes, "ghidra", kind
    end = island_end(data)
    if end:
        return end, "island", closes_with(data, end)
    return None, "", "unreadable"


def census_table(rows, funcs):
    """[(start, end, name)] sorted by start, from the committed symbol map."""
    out = [(int(f["address"], 16), int(f["address"], 16) + int(f["size"] or 0), f["name"]) for f in funcs]
    out.sort()
    return out


def classify(va, table, starts):
    """`census-start`, `inside` or `outside` — the whole containment question, from committed files."""
    i = bisect.bisect_right(starts, va) - 1
    if i >= 0 and table[i][0] <= va < table[i][1]:
        return "census-start" if table[i][0] == va else "inside"
    return "outside"


def load_targets(path):
    """{va: {names: [...], records: n, classes: [...]}} over every registration record with a body."""
    bodies = collections.OrderedDict()
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            body = (r.get("body") or "").strip()
            if not body:
                continue
            va = int(body, 16)
            e = bodies.setdefault(va, {"names": [], "records": 0, "classes": []})
            e["records"] += 1
            name = (r.get("name") or "").strip()
            if name and name not in e["names"]:
                e["names"].append(name)
            cls = (r.get("class") or "").strip()
            if cls and cls not in e["classes"]:
                e["classes"].append(cls)
    return bodies


def load_verdicts(path):
    """{va: {column: value}} from NativeCensus.java; the image-side axis, optional."""
    out = {}
    if not path or not os.path.isfile(path):
        return out
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            out[int(r["body_va"], 16)] = r
    return out


def build(bodies, table, starts, xbe, verdicts):
    rows = []
    decode = _decoder() if xbe is not None else None
    for va in sorted(bodies):
        e = bodies[va]
        first = prev = None
        section = ""
        if xbe is not None:
            sec = xbe.section_for(va)
            section = sec.name if sec else "(none)"
            try:
                first = xbe.read_va(va, 8)
            except Exception:                        # a page hole: the address is still classified
                first = b""
            try:
                prev = xbe.read_va(va - 1, 1)[0]
            except Exception:
                prev = None
        v = verdicts.get(va, {})
        row = collections.OrderedDict()
        row["body_va"] = "0x%08x" % va
        row["names"] = " ".join(e["names"])
        row["records"] = e["records"]
        row["classes"] = " ".join(e["classes"])
        row["section"] = section
        row["census"] = classify(va, table, starts)
        row["boundary"] = boundary(prev) if xbe is not None else ""
        row["prologue"] = prologue(first or b"") if xbe is not None else ""
        row["first_bytes"] = (first or b"").hex()
        # NativeCensus.java names the column `verdict` and the table names it `disassembler`: reading only
        # the first blanked the committed column on a --dump-only regeneration (measured 2026-11-20).
        row["disassembler"] = v.get("disassembler") or v.get("verdict") or ""
        row["body_bytes"] = v.get("body_bytes", "")
        row["instructions"] = v.get("instructions", "")
        row["ends_terminal"] = v.get("ends_terminal", "")
        if xbe is not None:
            sec = xbe.section_for(va)
            span = min(65536, sec.va + sec.vsize - va) if sec else 0
            try:
                data = xbe.read_va(va, span) if span > 0 else b""
            except Exception:                    # a page hole: the extent stays unmeasured
                data = b""
            body_bytes = int(v["body_bytes"]) if (v.get("body_bytes") or "").isdigit() else 0
            extent, source, closes = body_extent(data, body_bytes)
            row["extent"] = extent or ""
            row["extent_source"] = source
            row["extent_closes"] = closes
            # The sweep reads the same window but stops at a terminator, and it is bounded by the next
            # row the map already has: an island end past that row is the neighbour's close, not a body.
            # A map row that starts *inside* the body the disassembler measured is a fragment of this
            # body, not its neighbour (four rows here), so the bound is the first row at or past the body's
            # declared end and `--emit-boundaries` turns the pair into a resize plus the fragment's drop.
            i = bisect.bisect_right(starts, va)
            limit = (starts[i] - va) if i < len(starts) else None
            # A row that starts inside the body the disassembler measured is a fragment of it, not its
            # neighbour. The strict window is tried first, so a row a neighbour merely overlaps keeps its
            # own reading; where that window holds no terminator at all, the bound is the first row at or
            # past the declared end and `--emit-boundaries` turns the pair into a resize plus a drop.
            wide = limit
            if body_bytes:
                j = bisect.bisect_left(starts, va + body_bytes)
                wide = (starts[j] - va) if j < len(starts) else None
            wanted = table[bisect.bisect_right(starts, va) - 1]
            floor = (wanted[1] - wanted[0]) if wanted[0] == va else None
            swept, swept_close, why = sweep_body(decode, data, limit, floor, va)
            if why == "no-terminator-before-the-next-row" and wide and wide > (limit or 0):
                swept, swept_close, why = sweep_body(decode, data, wide, floor, va)
            if swept is None:
                row["sweep"] = row["sweep_closes"] = ""
                row["sweep_refused"] = why
            else:
                row["sweep"], row["sweep_closes"], row["sweep_refused"] = \
                    swept, "%s %s" % (swept_close, why), ""
        else:
            row["extent"] = row["extent_source"] = row["extent_closes"] = ""
            row["sweep"] = row["sweep_closes"] = row["sweep_refused"] = ""
        rows.append(row)
    return rows


def summarise(rows, table=None):
    """The numbers a doc quotes: the split, and the evidence behind each outside address."""
    census = collections.Counter(r["census"] for r in rows)
    outside = [r for r in rows if r["census"] == "outside"]
    verdict = collections.Counter(r["disassembler"] or "(not asked)" for r in outside)
    print("%d distinct body addresses: %d are function starts in the census, %d fall inside a function,"
          " %d fall outside every function" % (len(rows), census["census-start"], census["inside"],
                                               census["outside"]))
    for label, col in (("boundary byte before an outside address", "boundary"),
                       ("prologue shape at an outside address", "prologue")):
        c = collections.Counter(r[col] for r in outside)
        print("  %s: %s" % (label, ", ".join("%s %d" % kv for kv in c.most_common())))
    print("  disassembler verdict on the outside addresses: %s"
          % ", ".join("%s %d" % kv for kv in verdict.most_common()))
    accepted = [r for r in outside if r["disassembler"] == "accepted"]
    if accepted:
        term = collections.Counter((r["ends_terminal"] or "(not recorded)") for r in accepted)
        sizes = sorted(int(r["body_bytes"]) for r in accepted if r["body_bytes"].isdigit())
        insn = sorted(int(r["instructions"]) for r in accepted if r["instructions"].isdigit())
        print("  accepted bodies: %s; median %d bytes, median %d instructions"
              % (", ".join("ends-terminator=%s %d" % kv for kv in term.most_common()),
                 sizes[len(sizes) // 2] if sizes else -1, insn[len(insn) // 2] if insn else -1))
    if table is not None:
        declared = {a: (b - a) for a, b, _n in table}
        settled = collections.Counter()
        for r in rows:
            if r["census"] != "census-start" or not r["sweep"]:
                continue
            got, want = int(r["sweep"]), declared.get(int(r["body_va"], 16))
            settled["agrees" if got == want else ("stops-short" if got > want else "sweep-short")] += 1
        refused = collections.Counter(r["sweep_refused"] for r in rows
                                      if r["sweep_refused"] and r["census"] == "census-start")
        print("  the sweep on the %d rows the map places: %s; refused %s"
              % (census["census-start"],
                 ", ".join("%s %d" % kv for kv in sorted(settled.items())) or "not measured",
                 ", ".join("%s %d" % kv for kv in sorted(refused.items())) or "none"))
    return census, verdict


def sweep_evidence(r, size):
    """The one sentence a `resize` row carries: what the row's own bytes say its end is."""
    va = int(r["body_va"], 16)
    close, anchor = r["sweep_closes"].rsplit(" ", 1)
    where = {"pad": "the `0xcc` pad that follows it",
             "edge": "the next row"}.get(anchor, "the `%s` bytes that follow it" % anchor)
    return ("the declared size ends at 0x%08x, which is not where the body's code stops: a linear decode "
            "from 0x%08x reaches a `%s` at 0x%08x and %s begins there, so the row stops %d byte(s) short "
            "of its own close. The `extent` column read the end as %s (%s), which is the fallback walk's "
            "answer -- it stops at the first `cc cc` and can read a neighbour's close -- not the decode's"
            % (va + size, va, close, va + int(r["sweep"]), where, int(r["sweep"]) - size,
               r["extent"] or "?", r["extent_source"] or "unmeasured"))


def gap_adds(rows, table):
    """[(row, size, refusal)] -- the `add` each outside native body earns, or why it earns none.

    The extent must close on a terminator (a body, not a fragment). A body whose extent encloses a row
    the map already has is *not* refused any more: it is emitted with the `drop` that folds that row,
    which is the pairing U39 names. `interior` carries those rows' addresses.
    """
    starts = [t[0] for t in table]
    out = []
    for r in rows:
        if r["census"] != "outside":
            continue
        va = int(r["body_va"], 16)
        size = int(r["extent"] or 0)
        if r["disassembler"] != "accepted":
            out.append((r, 0, "the disassembler never accepted a body here", []))
            continue
        if not size or r["extent_closes"] in ("other", "unreadable"):
            out.append((r, 0, "the extent does not close on a terminator", []))
            continue
        inside = []
        i = bisect.bisect_right(starts, va)
        while i < len(table) and table[i][0] < va + size:
            inside.append((table[i][0], table[i][2]))
            i += 1
        out.append((r, size, None, inside))
    return out


def evidence_text(r, size, row_name):
    """The one sentence a repair row carries: what measured this body, and why the map lacked it."""
    first = (r["names"] or "(unnamed)").split(" ")[0]
    va = int(r["body_va"], 16)
    return ("the body of the native %s (%s registration record(s), classes %s) in "
            "inventory/native_targets.csv; the byte before it is %s, it opens on an MSVC %s prologue, and "
            "the disassembler accepted a %d-byte function closing on `%s` at 0x%08x. A native's only "
            "reference is the registration table -- no call in the image reaches it -- so only this row "
            "can create the function"
            % (first, r["records"], r["classes"], r["boundary"], r["prologue"], size,
               r["extent_closes"], va + size))


def emit(args):
    """Print the rows `--emit-boundaries` / `--emit-creations` earn; nothing is written to the tree."""
    if not os.path.isfile(OUT):
        sys.exit("native-census: %s is missing" % os.path.relpath(OUT, ROOT))
    with open(OUT, newline="") as fh:
        rows = list(csv.DictReader(fh))
    with open(FUNCTIONS, newline="") as fh:
        table = census_table(None, list(csv.DictReader(fh)))
    declared = {a: (b - a) for a, b, _n in table}
    adds = gap_adds(rows, table)
    ok = [(r, size, inside) for r, size, why, inside in adds if why is None]
    refused = [(r, size, why) for r, size, why, _in in adds if why is not None]
    resizes = [r for r in rows
               if r["census"] == "census-start" and r["sweep"]
               and int(r["sweep"]) > declared.get(int(r["body_va"], 16), 0)]
    w = csv.writer(sys.stdout, lineterminator="\n")
    for r, size, inside in ok:
        va = int(r["body_va"], 16)
        name = "FUN_%08x" % va
        ev = evidence_text(r, size, name)
        if inside:
            ev += ("; the row(s) %s start inside it and are fragments of this body -- they are the assert "
                   "blocks it inlined, and each is dropped with this add as its target"
                   % ", ".join("%#010x (%s)" % (a, n) for a, n in inside))
        if args.emit_boundaries:
            w.writerow(["0x%08x" % va, "add", name, size, ev])
            for a, n in inside:
                w.writerow(["0x%08x" % a, "drop", "0x%08x" % va, "",
                            "interior code of the body added at %#010x, entered by fall-through and named "
                            "after the header its assert literal comes from; nothing in the image calls it, "
                            "so the row that carries it is a split of that body, not a body" % va])
        else:
            w.writerow(["0x%08x" % va, name, "added to the map by symbols/xbe_boundaries.csv: " + ev,
                        "", "", "", "", "", "", size])
    for r in resizes:
        va = int(r["body_va"], 16)
        size = int(r["sweep"])
        # A row that starts inside the body this resize declares, and ends inside it too, is a fragment of
        # that body -- the same defect the `add` pairing folds, measured from the row the map already has.
        inside = [(a, n, b - a) for a, b, n in table if va < a < va + size and b <= va + size]
        if args.emit_boundaries:
            ev = sweep_evidence(r, declared[va])
            if inside:
                ev += ("; and the row(s) %s start inside the body and end inside it, which is what makes "
                       "the declared size a split rather than a short row: each is dropped with this "
                       "resize as its target"
                       % ", ".join("%#010x (%s, %d bytes)" % (a, n, s) for a, n, s in inside))
            w.writerow([r["body_va"], "resize", "", r["sweep"], ev])
            for a, n, s in inside:
                w.writerow(["0x%08x" % a, "drop", r["body_va"], "",
                            "interior code of the body resized at %#010x: the enclosing body's own decode "
                            "runs straight through this start into it, and no call in the image reaches it, "
                            "so the row is a split of that body and not a body" % va])
    print("native-census: %d add(s), %d drop(s), %d resize(s) emitted, %d add(s) refused"
          % (len(ok), sum(len(i) for _r, _s, i in ok), len(resizes), len(refused)), file=sys.stderr)
    for r, size, why in refused:
        print("  refused %s (%s): %s" % (r["body_va"], (r["names"] or "").split(" ")[0], why),
              file=sys.stderr)
    return 0


def run(args):
    bodies = load_targets(args.targets)
    with open(args.functions, newline="") as fh:
        funcs = list(csv.DictReader(fh))
    table = census_table(None, funcs)
    starts = [t[0] for t in table]
    xbe = None
    if args.xbe:
        spec = importlib.util.spec_from_file_location("xbe", os.path.join(HERE, "xbe.py"))
        xbemod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(xbemod)
        xbe = xbemod.Xbe(args.xbe)
    # The image half is a Ghidra round of its own, so a run without `--verdicts` carries the verdicts the
    # committed table already holds instead of blanking the column.
    verdicts = load_verdicts(args.verdicts or (OUT if os.path.isfile(OUT) else None))
    rows = build(bodies, table, starts, xbe, verdicts)
    summarise(rows, table)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "native_census.csv")
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLS, lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print("wrote %s (%d rows)" % (path, len(rows)))
    if args.walk:
        for r in rows:
            if r["census"] == "outside":
                print("%-11s %-34s %-8s %-12s %s" % (r["body_va"], r["names"].split(" ")[0][:34],
                                                     r["boundary"], r["prologue"], r["disassembler"]))
    return 0


def check(args):
    """Re-derive the census column from the committed tables and compare it with the committed CSV."""
    if not os.path.isfile(OUT):
        sys.exit("native-census: %s is missing" % os.path.relpath(OUT, ROOT))
    with open(OUT, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if list(rows[0].keys()) != COLS:
        sys.exit("native-census: --check: the table's columns are %r, the tool writes %r"
                 % (list(rows[0].keys()), COLS))
    bodies = load_targets(TARGETS)
    with open(FUNCTIONS, newline="") as fh:
        table = census_table(None, list(csv.DictReader(fh)))
    starts = [t[0] for t in table]
    declared = {a: (b - a) for a, b, _n in table}
    if len(rows) != len(bodies):
        sys.exit("native-census: --check: %d rows for %d distinct body addresses" % (len(rows), len(bodies)))
    seen = set()
    bad = []
    for r in rows:
        va = int(r["body_va"], 16)
        if va in seen:
            bad.append("%s twice" % r["body_va"])
        seen.add(va)
        if va not in bodies:
            bad.append("%s is not a body in native_targets.csv" % r["body_va"])
            continue
        want = classify(va, table, starts)
        if r["census"] != want:
            bad.append("%s: table says %s, the symbol map says %s" % (r["body_va"], r["census"], want))
        # The image-side columns are blank when the table was built without the XBE; a half-filled
        # column is the failure to catch, not an empty one.
        if bool(r["boundary"]) != bool(r["prologue"]):
            bad.append("%s: one image-side column is filled and the other is not" % r["body_va"])
        if r["boundary"] and r["disassembler"] not in ("accepted", "existing", "inside", "rejected"):
            bad.append("%s: the table was built from the image but carries no disassembler verdict (%r)"
                       % (r["body_va"], r["disassembler"]))
        if r["extent_source"] and r["extent_source"] not in ("ghidra", "island"):
            bad.append("%s: unknown extent source %r" % (r["body_va"], r["extent_source"]))
        if bool(r["extent"]) != bool(r["extent_source"]) or bool(r["extent"]) != bool(r["extent_closes"]):
            bad.append("%s: the three extent columns are not filled together" % r["body_va"])
        if r["extent"] and r["body_bytes"].isdigit() and int(r["extent"]) < int(r["body_bytes"]):
            bad.append("%s: extent %s is shorter than the body the disassembler accepted (%s)"
                       % (r["body_va"], r["extent"], r["body_bytes"]))
        # The sweep is one reading with one refusal: either an extent and the instruction that closes it,
        # or a reason, never both and never neither.
        swept = r.get("sweep") or ""
        if swept:
            if r.get("sweep_refused"):
                bad.append("%s: the sweep is filled and refused at once" % r["body_va"])
            if not swept.isdigit() or int(swept) <= 0:
                bad.append("%s: sweep %r is not a positive size" % (r["body_va"], swept))
            elif r["census"] == "census-start" and va in declared and int(swept) < declared[va] - 0:
                bad.append("%s: the sweep (%s) is shorter than the row the map declares (%d)"
                           % (r["body_va"], swept, declared[va]))
            close = (r.get("sweep_closes") or "").rsplit(" ", 1)
            if len(close) != 2 or not close[0]:
                bad.append("%s: sweep_closes %r is not `<terminator> <tail>`"
                           % (r["body_va"], r.get("sweep_closes")))
            elif not close[1] or any(p not in TAIL_SHAPES for p in close[1].split("+")):
                bad.append("%s: sweep_closes %r names a tail that is not one of %s"
                           % (r["body_va"], r.get("sweep_closes"), "|".join(TAIL_SHAPES)))
            elif close[0] not in ("ret", "ret-imm", "jmp", "jmp-ind"):
                bad.append("%s: sweep_closes names %r, which is not a terminator this build ends on"
                           % (r["body_va"], close[0]))
        elif r.get("sweep_refused") not in SWEEP_REFUSALS:
            bad.append("%s: no sweep and no known refusal (%r)"
                       % (r["body_va"], r.get("sweep_refused")))
        if r["disassembler"] and r["disassembler"] not in ("accepted", "existing", "inside", "rejected"):
            bad.append("%s: unknown disassembler verdict %r" % (r["body_va"], r["disassembler"]))
    if bad:
        sys.exit("native-census: --check: " + "; ".join(bad[:6]))
    census = collections.Counter(r["census"] for r in rows)
    verdict = collections.Counter(r["disassembler"] for r in rows if r["census"] == "outside")
    swept = collections.Counter()
    for r in rows:
        if r["census"] != "census-start":
            continue
        got = r.get("sweep") or ""
        if not got:
            swept["refused " + (r.get("sweep_refused") or "?")] += 1
        elif int(got) == declared.get(int(r["body_va"], 16)):
            swept["agrees"] += 1
        else:
            swept["stops short"] += 1
    print("native-census: %d body addresses, %d census starts, %d outside every function (%s)"
          % (len(rows), census["census-start"], census["outside"],
             ", ".join("%s %d" % kv for kv in sorted(verdict.items())) or "disassembler not asked"))
    print("native-census: the sweep on the %d rows the map places: %s"
          % (census["census-start"], ", ".join("%s %d" % kv for kv in sorted(swept.items()))))
    return 0


def selftest():
    cases = 0
    # Containment: an entry, the last byte inside a function, the byte after it, and a gap.
    table = [(0x1000, 0x1010, "a"), (0x1020, 0x1030, "b")]
    starts = [t[0] for t in table]
    assert classify(0x1000, table, starts) == "census-start"
    assert classify(0x100f, table, starts) == "inside"
    assert classify(0x1010, table, starts) == "outside"
    assert classify(0x1024, table, starts) == "inside"
    assert classify(0x0fff, table, starts) == "outside"
    cases += 1
    # Prologues: the order matters, `mov edi,edi` must not read as `push reg`.
    assert prologue(b"\x8b\xff\x55\x8b\xec") == "hotpatch"
    assert prologue(b"\x55\x8b\xec\x83") == "frame"
    assert prologue(b"\x64\xa1\x00\x00") == "seh"
    assert prologue(b"\x8b\x44\x24\x04") == "arg-load"
    assert prologue(b"\x83\xec\x28\x56") == "alloc"
    assert prologue(b"\xc2\x08\x00\xcc") == "empty-return"
    assert prologue(b"\x51\x56\x8b\x74") == "push-reg"
    assert prologue(b"\x0f\x57\xc0\x56") == "other"
    cases += 1
    # Boundaries: the three this build uses, and data.
    assert boundary(0xCC) == "int3" and boundary(0x00) == "zero"
    assert boundary(0xC3) == "return" and boundary(0xC2) == "return"
    assert boundary(0x42) == "data" and boundary(None) == "unreadable"
    cases += 1
    # The row key is the address, and a body named by two registrations is one row with two records.
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as fh:
        fh.write("block,block_va,record_va,name,class,function,body\n")
        fh.write("0,0x100,0x100,intACexecOne,A,One,0x00401000\n")
        fh.write("1,0x110,0x110,intACexecTwo,A,Two,0x00401000\n")
        path = fh.name
    bodies = load_targets(path)
    assert list(bodies) == [0x401000], bodies
    assert bodies[0x401000]["records"] == 2 and len(bodies[0x401000]["names"]) == 2, bodies
    os.unlink(path)
    cases += 1
    # The extent rule: a `ret imm16` closes, a fragment does not, and the island is only the fallback.
    body = b"\x55\x8b\xec\xc2\x08\x00\xcc\xcc\x8b\x44\x24\x04\xc3\xcc\xcc"
    assert closes_with(body, 6) == "ret-imm"
    assert closes_with(body, 13) == "ret"
    assert closes_with(body, 4) == "other"
    assert island_end(body) == 6
    assert body_extent(body, 6) == (6, "ghidra", "ret-imm")
    assert body_extent(body, 1) == (6, "island", "ret-imm")
    cases += 1
    # The sweep: a pad after a terminator settles the end, a pad after a `jcc` does not (a conditional
    # jump falls through), a body that ends exactly on the next row is an `edge`, and one that reaches
    # the row without a terminator is refused rather than cut.
    decode = _decoder()
    assert sweep_body(decode, b"\x55\x8b\xec\xc2\x08\x00\xcc\xcc") == (6, "ret-imm", "pad")
    # One `int3` between a body and the next row is padding the map's own start corroborates: 38 rows end
    # on exactly that, and refusing them was the old rule's two-byte requirement, not a body's shape.
    assert sweep_body(decode, b"\x55\x8b\xec\xc3\xcc", 5) == (4, "ret", "pad")
    assert sweep_body(decode, b"\x55\x8b\xec\xc3", 4) == (4, "ret", "edge")
    assert sweep_body(decode, b"\x90\x90\x90\x90", 4) == (None, None, "no-terminator-before-the-next-row")
    # A conditional jump falls through and cannot end a body, and a `cc` behind code is not padding.
    assert sweep_body(decode, b"\x74\x02\x90\x90\xcc\xcc") == (None, None,
                                                                   "no-terminator-before-the-next-row")
    assert sweep_body(decode, b"\x55\x8b\xec\xeb\x02\xcc\xcc") == (5, "jmp", "pad")
    assert sweep_body(decode, b"\xff\x24\x85\x00\x00\x00\x00\xcc\xcc") == (7, "jmp-ind", "pad")
    # The tail shapes the 48 refused rows actually end on: MSVC's `8b ff` slot, and a switch's case table
    # (dwords that name offsets inside the body), alone and in front of a pad.
    assert sweep_body(decode, b"\x55\x8b\xec\xc3\x8b\xff\xcc\xcc", 8) == (4, "ret", "align+pad")
    # A pad ends the reading: code after it is the next body's, not this one's (the 22 rows in map gaps).
    assert sweep_body(decode, b"\x55\x8b\xec\xc3\xcc\xcc\x55\x8b\xec\xc3", 10) == (4, "ret", "pad")
    table = b"\x55\x8b\xec\xc3" + struct.pack("<3I", 0x1000, 0x1002, 0x1004) + b"\xcc"
    assert sweep_body(decode, table, len(table), None, 0x1000) == (4, "ret", "table+pad")
    assert sweep_body(decode, b"\x55\x8b\xec\xc3" + struct.pack("<2I", 0x1000, 0x1002), 12,
                      None, 0x1000) == (None, None, "no-terminator-before-the-next-row")
    # A row whose own end already closes on a terminator does not grow over the body after it, even when
    # a later close has a pad behind it; one cut mid-instruction (no close at its declared end) does.
    assert sweep_body(decode, b"\x55\x8b\xec\xc3\x55\x8b\xec\xc3\xcc", 9, 4,
                      0) == (None, None, "declared-end-is-a-close")
    assert sweep_body(decode, b"\x55\x8b\xec\x8b\xff\xc3\xcc", 7, 3, 0) == (6, "ret", "pad")
    # A `0xcc` inside the declared size is an `int3` in the code, not the body's end: the walk goes on and
    # settles the row at its own close.
    assert sweep_body(decode, b"\x55\x8b\xec\xc3\xcc\x90\xc3\xcc", 8, 7) == (7, "ret", "pad")
    # A close that sits inside the extension is what the resize rule refuses to grow over.
    assert sweep_body(decode, b"\xc3\x90\xc3\xcc", 4, 0) == (None, None, "close-inside-the-extension")
    cases += 1
    print("native-census: selftest passed (%d cases)" % cases)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--targets", default=TARGETS)
    ap.add_argument("--functions", default=FUNCTIONS)
    ap.add_argument("--xbe", help="default.xbe; the image-side columns need it")
    ap.add_argument("--dump", help="dump root, for --xbe's default location")
    ap.add_argument("--verdicts", help="NativeCensus.java's CSV")
    ap.add_argument("-o", "--out", default=None, help="write native_census.csv here")
    ap.add_argument("--walk", action="store_true", help="print the outside addresses")
    ap.add_argument("--emit-boundaries", action="store_true",
                    help="print the symbols/xbe_boundaries.csv rows the outside population earns")
    ap.add_argument("--emit-creations", action="store_true",
                    help="print the symbols/xbe_overrides.csv creation rows those adds need")
    ap.add_argument("--check", action="store_true", help="the committed table, no dump")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.check:
        return check(args)
    if args.emit_boundaries or args.emit_creations:
        return emit(args)
    if not args.xbe and args.dump:
        args.xbe = os.path.join(args.dump, "Stargate_SG-1_-_The_Alliance-2005-12-15", "default.xbe")
    if args.xbe and not os.path.isfile(args.xbe):
        sys.exit("native-census: required: %s is not a file (give --xbe or --dump)" % args.xbe)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
