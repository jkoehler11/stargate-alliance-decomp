#!/usr/bin/env python3
"""Where a map row ends: the extents Ghidra's inference left short, and the repairs that close them.

`symbols/xbox_functions.csv`'s `size` column is Ghidra's function extent, and an extent is an inference.
`tools/symbol-boundaries.py` carries the reviewed repairs to it; this tool is the measurement behind the
largest family those repairs never covered: **a row that stops on an instruction which is not a
terminator.** A body never falls out of its own end -- the bytes that follow a `pop ebp` or an
`add esp,0x10` are the same function's epilogue -- so a row whose last instruction is not
`ret`/`jmp`/`int3` is unfinished, and the tail it leaves in the gap is measurable two ways:

* **`head`** -- a gap head (`tools/gap-heads.py`) starts exactly at the row's end and its own sweep
  closes on a terminator. The head's measured size is the tail.
* **`tail`** -- no head opens there, so the bytes are decoded from the row's own end: the first `ret` is
  the body's close, and it is a close only if **everything between it and the next map row is fill**
  (`0xCC`/`0x90`). A `jmp` is unconditional, so what follows it is never falling through: a jump whose
  target lands forward inside the row's own gap is a branch and the read continues past it, and one whose
  target is elsewhere closes the run when fill, a pad, alignment padding or a body's own opening follows
  it. Only a `jmp` with code behind it that none of those explain refuses the read.

Both are resizes to `row size + tail`. Three more shapes close a row and are repaired by the same
rule: a **`short`** row (the size cuts an instruction in half -- a byte count, not a boundary) is read
across the cut, and the row extends to where the completed instruction lands when that is a
terminator (`complete-term`) or when the run behind it closes (`complete-fill`); a tail whose **first
terminator is a `jmp`** followed by fill or a pad is a tail call (`jump`); and a tail whose first
terminator is a `ret` followed by a **pad of two or more `0xcc`** is a close too (`pad`), because the
project's own rule is that only a run of two or more `int3` bounds a body. A row whose tail *begins*
with that pad is already bounded where the map says it ends (`bounded`, no repair).

Every other row that does not end on a terminator is counted and not repaired: `split` (the next map
row starts at the row's end) and `refused` (a terminator with code behind it, or nothing to decode) and
`short` (still a byte count: nothing spans the cut, the completed instruction would cross the next
row, or the run behind it does not close).

**A split is read as a fold, and the evidence is entry, not a frame.** A row whose next map row starts
inside the body its own head reads forward to is a `drop` repair. Its head has to be a body's start: an
instruction boundary in front of it (a `pad` or a terminator, `start_kind`) **and** one of three pieces
of entry evidence -- a name the build gave it, a measured prologue, or a `call`/`jmp` that lands on it
(`call_targets`). Every map row inside the span is interior code unless **something calls it**: a body is
entered by a call or a jump, never by falling through its neighbour's last instruction. A row whose head
is inside another instruction is not a body at all (`no-boundary`): no branch can enter it, so the map
made a fragment inside a body it has no row for.


**A `ret` is a boundary too, and the code behind it is read by what it *is*, not only by what it
reads.** Nothing falls through a `ret` either, so code behind one is reached only by a branch, and the
rule refuses it because the branch could be the row's own early return. Two shapes settle that without
guessing: the bytes behind are **not code at all** -- a run that holds an instruction no ring-3 program
can execute (`in`/`out`, a privileged op, objdump's `(bad)`) is a table the map's size overran, and no
branch of this body reaches it (`data`) -- or they are a **complete body of its own** -- the run closes
on a terminator with fill or a pad behind it inside 64 bytes, which is another function, so the close in
front bounds this one (`body`). The same two readings serve a `jmp` whose bytes behind neither pad nor
match the opening table: data behind a dispatch ends the body at the jump (`jump-data`), and a complete
body behind it is the tail call `jump-behind` already names.

**A size can be too long as well as too short.** A row whose declared size crosses the next map row's
start holds two bodies at once, and the second one is the map's. Its own decode still ends where the body
ends, so the last terminator in front of the next row's start with nothing but fill between the two is the
boundary and the row is **resized down** to it (`over`); a terminator with code behind it, or none at all,
refuses (`refused over-code` / `over-close`) because resizing would cut a body the map already carries.

**The rule has a control, and it is the same rule over the data sections.** The same decode from 3,000
fixed addresses in the image's data (a `ret` with only fill after it) finds almost nothing, because a
`ret`-into-fill is a shape code has and data does not; the hits and trials are in the census row.

    tools/symbol-extents.py --xbe "$SG1_DUMP/default.xbe" -o inventory   # measure (needs the image)
    tools/symbol-extents.py --write-boundaries   # append the resizes to symbols/xbe_boundaries.csv
    tools/symbol-extents.py --write-doc docs/SYMBOLS.md
    tools/symbol-extents.py --check-doc docs/SYMBOLS.md
    tools/symbol-extents.py --check              # dump-free gate (in `make ci`)
    tools/symbol-extents.py --selftest           # synthetic rows, heads and decodes, no dump

**A repaired row is carried, not re-measured.** Once the resize lands, the tail is inside the row and the
gap walker no longer emits a head for it, so a second run cannot re-derive it. The artifact is therefore
also a record: a row it already holds whose map size is the artifact's `new_size` is carried verbatim, and
`--check` proves each carried row still has its resize in `symbols/xbe_boundaries.csv`.
"""
import argparse
import bisect
import collections
import csv
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP = os.path.join(ROOT, "symbols", "xbox_functions.csv")
BOUNDARIES = os.path.join(ROOT, "symbols", "xbe_boundaries.csv")
OVERRIDES = os.path.join(ROOT, "symbols", "xbe_overrides.csv")
HEADS = os.path.join(ROOT, "inventory", "gap_heads.csv")
ARTIFACT = os.path.join("inventory", "symbol_extents.csv")
DOC_MARKS = ("<!-- generated:symbol-extents -->", "<!-- /generated:symbol-extents -->")

ROW = ("row_va", "row_name", "row_size", "last_va", "last_length", "last_insn", "end_kind",
       "tail_bytes", "close_va", "close_insn", "head_va", "head_size", "head_prologue",
       "head_verdict", "new_size", "target_va", "verdict", "reason", "note")
CENSUS = ("text_rows", "sized_rows", "terminator", "short", "undecoded", "undecoded_cut", "nonterm",
          "continuation", "continuation_bytes", "head_family", "tail_family", "head_bytes",
          "tail_bytes_total", "pad_family", "pad_bytes", "jump_family", "jump_bytes", "complete_family",
          "complete_bytes", "complete_term", "complete_fill", "bounded", "split", "split_fold",
          "split_folded", "fold_bytes", "refused", "refused_no_close", "refused_jump", "refused_then_code",
          "refused_undecoded", "refused_no_room", "padding_family", "padding_bytes", "behind_family",
          "behind_bytes", "body_family", "body_bytes", "data_family", "data_bytes", "follow_family",
          "follow_bytes", "epilogue_family", "epilogue_bytes", "short_overlap", "short_nostraddle",
          "short_refused", "short_split", "fold_trials", "fold_short",
          "split_noboundary", "fragment_head", "fold_agree", "null_hits", "null_trials", "over", "over_bytes",
          "refused_over_code", "refused_over_close", "reread")
# the reason's first token is its family, so a carried row needs no second column
FAMILIES = ("head", "tail", "pad", "jump", "complete", "padding", "behind", "body", "data", "follow",
            "epilogue")

OBJ_LINE = re.compile(r"^\s*([0-9a-f]+):\t([0-9a-f ]+?)\s*\t(.*)$")
WRITES_EBP = re.compile(r"^(mov|lea|xor|add|sub|and|or|pop|imul|movzx|movsx|and)\s+ebp\b")
JMP_TARGET = re.compile(r"^jmp\s+(?:short\s+)?0x([0-9a-f]+)")
BRANCH_TARGET = re.compile(r"^j[a-z]+\s+(?:short\s+)?0x([0-9a-f]+)")
FRAME_WRITE = re.compile(r"^(mov|lea|xor|add|sub|and|or)\s+ebp,")
TERM = ("ret", "jmp", "int3", "hlt", "iret")
FILL = (0xCC, 0x90)
# Instructions a ring-3 program cannot execute. A run that holds one is not code at all, whatever the
# decoder made of the bytes around it: the image is a user-mode XBE, so `in`/`out` and the system-table
# opcodes only occur inside data.
RING0 = ("in", "out", "ins", "outs", "cli", "sti", "lgdt", "lidt", "lldt", "ltr", "lmsw", "clts",
         "invd", "invlpg", "wbinvd", "wrmsr", "rdmsr", "rdpmc", "sysenter", "sysexit", "hlt", "iret")
MAX_TAIL = 256
MAX_CUT = 16          # the longest x86 instruction: a cut cannot reach further
MAX_FOLD = 2048       # how far a fragment's body is followed to its close
MAX_BEHIND = 256      # how far the code behind a close is followed for a body of its own
WINDOW = 512          # bytes per decode pass: the walk's cost is one objdump spawn per window, and a
                      # 64-byte window made a whole-image measurement overrun twelve minutes
# The shared opening table (`native-census.PROLOGUES` plus the measured ones in `gap-heads`), tested on a
# body's first bytes. It is what "a new body opens here" means in this tool: the code behind a close that
# matches one of these opens a body of its own, so the close bounds the run in front of it.
PROLOGUE_PAT = (
    (b"\x8b\xff", "hotpatch"), (b"\x55\x8b\xec", "frame"), (b"\x64\xa1", "seh"),
    (b"\x8b\x44\x24", "arg-load"), (b"\x8b\x4c\x24", "arg-load"), (b"\x8b\x54\x24", "arg-load"),
    (b"\x81\xec", "alloc"), (b"\x83\xec", "alloc"), (b"\xc2", "empty-return"),
    (b"\x6a", "push-imm"), (b"\x68", "push-imm"), (b"\x51", "push-reg"), (b"\x53", "push-reg"),
    (b"\x55", "push-reg"), (b"\x56", "push-reg"), (b"\x57", "push-reg"), (b"\xb8", "mov-eax"),
    (b"\xa1", "global-load-eax"), (b"\x8b\x0d", "global-load-ecx"), (b"\x8b\xd1", "self-return"),
    (b"\x33\xc0", "zero-return"),
)
REPAIRS = ("row ends on a non-terminator and a gap head closes at its end",
           "row ends on a non-terminator and a decode from its end reaches a `ret` with only fill after it",
           "the row's size cuts an instruction, and completing it lands on a terminator or a close",
           "the tail's first terminator is a `jmp` tail call, or a close with a two-byte pad behind it",
           "the close is followed by alignment padding, by a body's own opening, or by the same frame's "
           "shared epilogue",
           "the close is followed by a complete body of its own, or by bytes no ring-3 code can execute",
           "the row's declared size runs into the next map row, and its last terminator in front of that "
           "row has only fill behind it",
           "the close is an early return and the code behind it unwinds the row's own frame -- the "
           "registers its opening pushed and the allocation it made, in the mirror order")



def read_csv(path):
    if not path or not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_map(path=MAP):
    rows = []
    for r in read_csv(path):
        try:
            rows.append({"va": int(r["address"], 16), "name": r.get("name") or "",
                         "size": int(r.get("size") or 0)})
        except (KeyError, ValueError):
            continue
    rows.sort(key=lambda r: r["va"])
    return rows


def read_heads(path=HEADS):
    out = []
    for r in read_csv(path):
        if (r.get("row") or "") != "head":
            continue
        try:
            out.append({"va": int(r["head_va"], 16), "size": int(r.get("size") or 0),
                        "boundary": r.get("boundary") or "", "verdict": r.get("verdict") or "",
                        "prologue": r.get("prologue") or "", "tail": r.get("tail") or ""})
        except (KeyError, ValueError):
            continue
    return out


def read_boundaries(path=BOUNDARIES):
    out = []
    for r in read_csv(path):
        try:
            out.append({"address": int(r["address"], 16), "action": (r.get("action") or "").strip(),
                        "size": int(r.get("size") or 0), "evidence": (r.get("evidence") or "").strip(),
                        "target": (r.get("target") or "").strip()})
        except (KeyError, ValueError):
            continue
    return out


def decode(blob, base):
    """[(va, length, text)] from one objdump pass over `blob`, decoded from its own head.

    A row is decoded from its own first byte, never from a linear sweep of the section: objdump wraps an
    instruction longer than seven bytes onto a second line, so a sweep's byte count is short and every
    later boundary drifts. `.byte` lines are objdump's refusals and are dropped, not counted as one byte.
    """
    if not blob:
        return []
    exe = os.environ.get("OBJDUMP") or "objdump"
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
        fh.write(blob)
        path = fh.name
    try:
        listing = subprocess.run([exe, "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                                  "--adjust-vma=0x%x" % base, path],
                                 capture_output=True, text=True, check=False).stdout
    finally:
        os.unlink(path)
    out = []
    for line in listing.splitlines():
        m = OBJ_LINE.match(line)
        if not m or ".byte" in m.group(3):
            continue
        out.append((int(m.group(1), 16), len(m.group(2).split()), m.group(3).strip()))
    return out


def mnemonic(text):
    parts = text.split()
    return parts[0].lower() if parts else ""


def is_term(text):
    return mnemonic(text) in TERM


def tail_close(ins):
    """(close instruction, refusal) from a decoded tail: the first `ret`, refusing a `jmp` in front."""
    for step in ins:
        m = mnemonic(step[2])
        if m == "jmp":
            return None, "jump"
        if m == "ret":
            return step, None
    return None, "no-close"


def tail_rule(reader, decode_fn, start, limit, targets=None):
    """(tail instructions, close instruction, family-or-refusal) for the run after a row's end.

    The first terminator closes the row when **only fill** follows it (`tail` for a `ret`, `jump` for a
    tail call) or when a **pad of two or more `0xcc`** does (`pad`) -- only a run of two or more `int3`
    bounds a body, so a single `int3` in front of code is interior. A terminator with *code* behind it
    is read three ways, because the code behind a close is not the body's own code by definition:
    `padding` when the next instructions are alignment filler (`nop`, `int3`, `lea r,[r+0x0]`), `behind`
    when they open a body of their own (the shared prologue table), and `follow` when they read the frame
    the close's own body built -- the shared epilogue an early return jumps into. Anything else refuses
    the read, and so does a run with no terminator at all.
    """
    room = limit - start
    tail = decode_fn(reader(start, min(room, MAX_TAIL)), start) if room > 0 else []
    if not tail:
        return tail, None, ("no-room" if room <= 0 else "undecoded")
    first = None
    targets = set(targets or ())
    for t in tail:
        if is_term(t[2]):
            first = t
            break
        targets |= branch_targets([t])
    if first is None:
        return tail, None, "no-close"
    why = close_kind(reader, decode_fn, first, limit)
    if why in ("body", "data", "behind") and (first[0] + first[1]) in targets:
        # the row branches to the bytes behind this close, so they are the row's own code -- the
        # early-return shape -- whatever they look like
        return tail, None, "then-code"
    if why in ("tail", "jump", "pad", "jump-pad", "jump-behind", "jump-table", "jump-data"):
        return tail, first, why
    if why == "jump-open":
        return tail, None, "jump"
    if why in ("padding", "behind", "data", "body"):
        return tail, first, why
    if why == "jump-inside":
        close, _jwhy = close_ahead(reader, decode_fn, first[0] + first[1], limit)
        if close is not None and mnemonic(close[2]) != "jmp":
            return tail, close, "jump-inside"
        return tail, None, "jump"
    if why in ("follow", "epilogue"):
        close, fwhy = close_ahead(reader, decode_fn, first[0] + first[1], limit)
        if close is not None and mnemonic(close[2]) != "jmp":
            return tail, close, why
        return tail, None, "then-code"
    return tail, None, "then-code"


def is_indirect_jmp(text):
    """Is this `jmp` a register or memory dispatch rather than a copy with an immediate target?"""
    return bool(re.match(r"^jmp\s+(DWORD|QWORD|WORD|BYTE) PTR", text)) or \
        bool(re.match(r"^jmp\s+e[a-z]{2}$", text))


def branch_targets(ins):
    """The set of addresses a direct branch in `ins` lands on.

    A close only bounds a row when the code behind it is not where the row *itself* goes: the
    early-return shape is a `jcc` over a `ret` into the rest of the same body, so a target that lands
    exactly behind the close says the bytes are this row's code, whatever they look like.
    """
    out = set()
    for _a, _l, t in ins:
        m = JMP_TARGET.match(t) or BRANCH_TARGET.match(t)
        if m and m.group(1):
            out.add(int(m.group(1), 16))
    return out


def jmp_target(text):
    """The absolute target of a direct `jmp`, or None for a register or memory operand."""
    m = JMP_TARGET.match(text)
    return int(m.group(1), 16) if m and m.group(1) else None


def jump_kind(reader, decode_fn, step, after_va, limit, deep=True):
    """How the bytes behind one `jmp` bound it: `jump-pad`, `jump-behind`, `jump-inside` or `jump-open`.

    A `jmp` is unconditional, so nothing falls through it: the bytes behind it are reached only by
    another branch or by the jump itself, and that makes its **target** the discriminator. A target that
    lands forward inside the row's own gap is a branch -- the `if`/`else` shape, whose else-arm sits
    behind the jump, or a loop back into the body -- so those bytes are this body's own code and the read
    continues (`jump-inside`). A target elsewhere closes the run when alignment padding (`jump-pad`) or a
    body's own opening (`jump-behind`, a tail call into that body) follows it, exactly as fill or a pad
    behind a `ret` does. Code the rule cannot attribute refuses (`jump-open`), which is what a branch
    whose target the decode cannot see looks like too.
    """
    if jmp_target(step[2]) is None and is_indirect_jmp(step[2]):
        kind, _first = after_kind(reader, decode_fn, after_va, limit)
        if kind == "padding":
            return "jump-pad"
        return "jump-table"
    target = jmp_target(step[2])
    if target is not None and after_va <= target < limit:
        return "jump-inside"
    kind, _first = after_kind(reader, decode_fn, after_va, limit, deep=deep)
    if kind == "padding":
        return "jump-pad"
    if kind == "follow":
        return "jump-inside"
    if kind == "behind":
        return "jump-behind"
    if kind == "body":
        return "jump-behind"
    if kind == "data":
        return "jump-data"
    return "jump-open"


def close_kind(reader, decode_fn, step, limit, deep=True):
    """How the bytes behind one terminator bound it: `tail`, `pad`, `padding`, `behind`, `body`, `data`,
    `follow`, the `jump` family (`jump-pad`, `jump-behind`, `jump-table`, `jump-data`, `jump-inside`), or
    a refusal (`jump-open`, `then-code`). `deep` is off for the nested read the `body` test makes, so the
    two tests cannot call each other."""
    after_va = step[0] + step[1]
    after = reader(after_va, max(0, limit - after_va))
    if all(b in FILL for b in after):
        return ("jump" if mnemonic(step[2]) == "jmp" else "tail")
    pad = 0
    for b in after:
        if b == 0xCC:
            pad += 1
        else:
            break
    if pad >= 2:
        return ("jump" if mnemonic(step[2]) == "jmp" else "pad")
    if mnemonic(step[2]) == "jmp":
        return jump_kind(reader, decode_fn, step, after_va, limit, deep=deep)
    kind, _first = after_kind(reader, decode_fn, after_va, limit, deep=deep)
    return kind


def not_code(ins):
    """The first instruction in `ins` that no ring-3 program can execute, or None.

    An `in`/`out` or a system-table opcode in this image is a byte pattern in a table, and objdump's
    `(bad)` is a byte pattern it cannot decode at all: a branch of the body whose close sits in front of
    them cannot reach either, so the bytes are data the map's size overran.
    """
    for _a, _l, t in ins:
        if "(bad)" in t or mnemonic(t) in RING0:
            return t
    return None


def body_behind(reader, decode_fn, va, limit, cap=64):
    """(close instruction, kind) when the run at `va` is a whole body of its own.

    A body is a run that closes on a terminator with fill, a pad, alignment padding, a tail call or a
    dispatch behind it inside `cap` bytes. The caller passes the horizon: `after_kind` reads to the next
    map row (`MAX_BEHIND`), because a body between this row's close and the next row's head cannot be the
    row's own code however long it is -- and a 64-byte window reported 79-byte bodies as "code this rule
    cannot attribute" (0x00159610, measured 2026-11-22).
    """
    room = max(0, min(limit - va, cap))
    if room <= 0:
        return None, "no-room"
    ins = decode_fn(reader(va, room), va)
    if not_code(ins) is not None:
        return None, "data"          # data is not a body, whatever `ret` byte it happens to contain
    for step in ins:
        if not is_term(step[2]):
            continue
        kind = close_kind(reader, decode_fn, step, limit, deep=False)
        if kind in ("tail", "pad", "padding", "jump", "jump-pad", "jump-behind", "jump-table",
                    "jump-data"):
            return step, kind
        if kind == "jump-inside":
            # a branch whose target lands forward inside the run is the candidate body's own
            # control flow, not its close: the read goes on to the terminator behind it
            continue
        return None, kind
    return None, "no-close"


def containing_head(reader, decode_fn, head_va, prev_end):
    """The gap's own first code byte in front of a fragment, or None.

    A fragment's head is inside another instruction, so no branch can enter it and the map has no row
    for the body that holds it. That body opens where the gap's padding ends: decoded from the previous
    row's end (a boundary, since that row closes on a terminator), the first instruction that is neither
    `int3` nor `nop` is the only place a body can start, and the linear decode from it runs through this
    fragment. The address says where an `add` repair would put the row; it does not prove the gap holds
    exactly one body.
    """
    if not prev_end or head_va - prev_end < 8:
        # no gap in front: the row before is another fragment of the same body, and the byte after its
        # end is not an opening -- the first fragment of the run is the one that carries the answer
        return None
    ins = decode_fn(reader(prev_end, min(head_va - prev_end + 16, 512)), prev_end)
    for a, _l, t in ins:
        if a > head_va - 4:
            break
        if mnemonic(t) in ("int3", "nop"):
            continue
        return a
    return None


UNWIND = re.compile(r"^(pop\s+[a-z]{2,3}|leave|mov\s+esp,ebp|add\s+esp,0x[0-9a-f]+)$")


def unwinds(text):
    """Is this instruction a step of a stack unwind? `pop r`, `leave`, `mov esp,ebp`, `add esp,N`."""
    return bool(UNWIND.match(text.strip()))



def after_kind(reader, decode_fn, va, limit, deep=True):
    """(`padding` | `behind` | `follow` | `epilogue` | `body` | `data` | `then-code`, first instruction).

    `padding` is alignment filler, `behind` a body's own opening, `follow` a run that reads the frame
    (`[ebp...]`, `leave`) it never writes in its opening window, and `epilogue` a run that **is** the
    unwind itself (`pop`, `leave`, `mov esp,ebp`, `add esp,N`) -- the body's own epilogue behind one of
    its return paths. The two readings a `ret` needs beyond those are `data` (a run that holds an
    instruction no ring-3 program can execute: the bytes are a table the size overran) and `body` (a
    whole body closes before the next map row, so the code belongs to it, not to the row in front).
    Anything else is code this rule cannot attribute.
    """
    ins = decode_fn(reader(va, min(limit - va, 24)), va)
    if not ins:
        return "then-code", None
    if is_fill_insn(ins[0][2]):
        return "padding", ins[0]
    if opens_body(reader(va, 8)):
        return "behind", ins[0]
    if unwinds(ins[0][2]):
        # A stack unwind behind the close is the body's own epilogue, not a neighbour: a body with
        # several return paths emits one unwind block per `ret`, so the code after this one belongs to
        # the body it closes and the read continues to the close that has fill behind it.
        return "epilogue", ins[0]
    for _a, _l, t in ins[:4]:
        if WRITES_EBP.match(t) or mnemonic(t) == "leave":
            return "then-code", ins[0]
    for _a, _l, t in ins[:4]:
        if "[ebp" in t or mnemonic(t) == "leave":
            return "follow", ins[0]
    if deep:
        if not_code(decode_fn(reader(va, min(limit - va, 48)), va)[:8]) is not None:
            return "data", ins[0]
        body, _why = body_behind(reader, decode_fn, va, limit,
                                 cap=min(MAX_BEHIND, max(64, limit - va)))
        if body is not None:
            return "body", ins[0]
    return "then-code", ins[0]


def opens_body(first):
    """Does this opening match the shared prologue table? `first` is the bytes at the address."""
    if not first:
        return False
    for pat, _name in PROLOGUE_PAT:
        if first.startswith(pat):
            return True
    return False


def call_targets(reader, lo, hi):
    """Addresses a direct `call`/`jmp` in the section names: entry evidence a prologue cannot supply.

    A body is entered by a call or a jump, never by falling through its neighbour's last instruction, so
    a `call`/`jmp rel32` that lands exactly on an address is proof a function starts there even when the
    opening matches no prologue (`0x003ee1ea`, `c6 05 ...  mov BYTE PTR ds:...,0x0`, is called from
    `0x003ee1e2`). The scan is a byte walk rather than a decode of every row: a false positive only ever
    *refuses* a fold, which is the safe direction, and the head test additionally requires an instruction
    boundary in front of the address.
    """
    out = set()
    blob = reader(lo, hi - lo)
    for i in range(len(blob) - 4):
        if blob[i] not in (0xE8, 0xE9):
            continue
        rel = struct.unpack_from("<i", blob, i + 1)[0]
        tgt = lo + i + 5 + rel
        if lo <= tgt < hi:
            out.add(tgt)
    return out


def start_kind(reader, va, lo):
    """`pad`/`ret`/`jmp` when an instruction boundary in front of `va` can begin a body, else None.

    A row whose head is not an instruction boundary is not a body at all: no branch can land on it and
    nothing falls into it, because the bytes in front belong to an instruction that is already running.
    The boundary is read from the eight bytes in front (`int3`/`nop` fill, `ret`, `ret imm16`, `jmp`);
    `None` is the answer for a head inside another instruction.
    """
    if va - 8 < lo:
        return None
    b = reader(va - 8, 8)
    if len(b) < 8:
        return None
    if b[-1] in FILL:
        return "pad"
    if b[-1] == 0xC3:
        return "ret"
    if b[-3] == 0xC2:
        return "ret-imm"
    if b[-5] == 0xE9:
        return "jmp"
    if b[-2] == 0xEB:
        return "jmp-short"
    return None


def is_fill_insn(text):
    """Alignment filler: `nop`, `int3`, `lea r,[r+0x0]`, or any other no-op that writes nothing.

    MSVC pads with more than `90` and `cc`: a self-move (`8b f6` `mov esi,esi`), a self-exchange, and an
    arithmetic op with a zero immediate (`05 00 00 00 00` `add eax,0x0`, the canonical five-byte nop)
    are the same filler with a different encoding, and reading only the three named forms left rows
    refusing behind them.
    """
    m = mnemonic(text)
    if m in ("nop", "int3"):
        return True
    t = " ".join(text.split())
    q = re.match(r"^lea\s+(\w+),\[(\w+)\+0x0\]$", t)
    if q and q.group(1) == q.group(2):
        return True
    q = re.match(r"^(mov|xchg)\s+(\w+),(\w+)$", t)
    if q and q.group(2) == q.group(3):
        return True
    return bool(re.match(r"^(add|sub|or|and|xor)\s+\w+,0x0$", t))


def close_ahead(reader, decode_fn, start, limit, cap=600):
    """(close instruction, kind) for the first close at or after `start`, following a shared epilogue.

    A close is a terminator with fill, a two-byte pad, alignment padding or a body's own opening behind
    it. When the code behind a close reads the same frame, the walk continues past it -- the early
    return's shared epilogue -- so the answer is the close that ends the body, not the first one it
    reaches. It also continues past one whose bytes another branch in the walk targets: a `jcc` over a
    `ret` lands on the row's own code, and a body behind a close that no branch reaches is a body. `None`
    when nothing closes before `limit`.
    """
    va = start
    steps = 0
    targets = set()
    while va < limit and steps < cap:
        n = min(limit - va, WINDOW)
        ins = decode_fn(reader(va, n), va)
        if not ins:
            return None, "undecoded"
        moved = False
        for step in ins:
            steps += 1
            if step[0] + step[1] > va + n:
                break                     # the window's last instruction is truncated: re-read it
            if not is_term(step[2]):
                targets |= branch_targets([step])
                continue
            kind = close_kind(reader, decode_fn, step, limit)
            if kind in ("behind", "follow", "body", "data", "epilogue") and (step[0] + step[1]) in targets:
                kind = "then-code"
            if kind in ("tail", "pad", "padding", "behind", "body", "data", "jump", "jump-pad",
                        "jump-behind", "jump-table", "jump-data"):
                return step, kind
            if kind == "jump-open":
                return None, "jump"
            va = step[0] + step[1]        # a shared epilogue or a branch inside the body: keep walking
            moved = True
            break
        if moved:
            continue
        nxt = [s for s in ins if s[0] + s[1] <= va + n]
        if not nxt:
            return None, "undecoded"
        va = nxt[-1][0] + nxt[-1][1]
    return None, "no-close"



def _family(cen, reason, tail_bytes):
    """Count one repaired row into its family's row and byte totals (the carried path needs this too)."""
    fam = (reason or "head").split("-")[0]
    cen["%s_family" % fam] += 1
    cen["%s_bytes" % fam if fam != "tail" else "tail_bytes_total"] += tail_bytes
    if fam == "complete":   # the two cut routes are counted here so a carried row restores them too
        cen["complete_%s" % ("term" if (reason or "").endswith("term") else "fill")] += 1


def owns_frame(reader, decode_fn, va, limit):
    """Does a body candidate build its own frame? A run that reads `[ebp+..]` or pops `ebp` before it
    writes `ebp` is using a frame an enclosing body built, so it is continuation code, not a body that
    another branch could enter."""
    ins = decode_fn(reader(va, max(0, min(limit - va, 32))), va)
    for _a, _l, t in ins[:8]:
        m = mnemonic(t)
        if FRAME_WRITE.match(t):
            return True
        if m == "leave" or (m == "pop" and t.split()[1].startswith("ebp")):
            return False
        if "[ebp" in t:
            return False
    return True


def fold_end(reader, decode_fn, start, size, limit, starts, calls=None):
    """(close address, close instruction, kind, refusal) for a row the map split from its own body.

    The row's head is a real instruction boundary, so the body is read forward from there: the first
    close is where it ends. Every map row that starts inside that span is **continuation code**: a body
    is entered by a call or a jump, never by falling through its neighbour's last instruction, so a row
    inside a linear decode cannot be a function unless something **calls** it. `calls` is the image's
    call-target set; a line that holds one is refused (`body-inside`) rather than swallowed, which is
    what keeps a called body from being folded into the row in front of it -- `0x003ee1f1` is 325 bytes
    of its own and is called from `0x003ee1ea`, so the split the map made there is real.
    """
    close, kind = close_ahead(reader, decode_fn, start, limit)
    if close is None:
        return None, None, kind, kind
    cend = close[0] if mnemonic(close[2]) == "int3" else close[0] + close[1]
    if cend <= start + size:
        return None, close, kind, "no-wider"
    k = bisect.bisect_right(starts, start)
    while k < len(starts) and starts[k] < cend:
        if calls is not None:
            blocked = starts[k] in calls
        else:
            blocked = opens_body(reader(starts[k], 8)) and owns_frame(reader, decode_fn, starts[k], cend)
        if blocked:
            return None, close, kind, "body-inside"
        k += 1
    return cend, close, kind, None



def measure(rows, heads, reader, decode_fn=decode, known=None, text=None, null_trials=0, progress=False):
    """(artifact rows, census). `reader(va, size)` returns bytes; `known` carries repaired rows.

    `progress` writes a row counter to stderr every 2,000 rows: the whole-image read is a few minutes
    of objdump spawns, and without it a slow run and a hung one look the same.
    """
    lo, hi = text or (0x00010000, 0x00500000)
    starts = [r["va"] for r in rows if r["size"]]
    sized = sorted((r["va"], r["va"] + r["size"]) for r in rows if r["size"])
    prev_end_of = {}
    for _i in range(1, len(sized)):
        prev_end_of[sized[_i][0]] = sized[_i - 1][1]
    calls = call_targets(reader, lo, hi)
    # A registration or vtable slot names a body by *data*: `xbe_overrides.csv` holds the rows the
    # class-registration pass created, and a slot that points into a fold's span proves the row there is
    # a body (0x002c4f1c is FUdpLink::BindPort's own slot entry), not continuation code.
    try:
        with open(OVERRIDES, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                a = (row.get("address") or "").strip()
                if a.startswith("0x"):
                    calls.add(int(a, 16))
    except OSError:
        pass
    head_at = {}
    for h in heads:
        head_at.setdefault(h["va"], h)
    known = known or {}
    cen = collections.Counter()
    out = []
    cover = []          # (end va, target va) of the folds this walk has already measured

    def continuation(r, last, end_kind, tail_bytes, close_va, reason, note, new_size, head=None, close=None):
        cen["nonterm"] += 1
        cen["continuation"] += 1
        cen["continuation_bytes"] += tail_bytes
        _family(cen, reason, tail_bytes)
        out.append(_row(r, last, end_kind, tail_bytes, close_va, "continuation", reason, note,
                        head=head, close=close, new_size=new_size))

    def fold_row(r, last, end_kind, note):
        """A row whose next map row starts inside the body its own head reads forward to.

        Both shapes the map makes here are the same repair: the row the map **split** at a boundary, and
        the row the map **cut short** before a fragment row that starts inside its body. The head has to
        be a body's start -- an instruction boundary in front of it, and a name, a measured prologue or a
        call into it -- and no row inside the span may be called, or the fold would swallow a function.
        """
        nxt = None
        k0 = bisect.bisect_right(starts, r["va"])
        if k0 < len(starts):
            nxt = starts[k0]
        end = r["va"] + r["size"]
        if nxt is None or nxt > end + MAX_FOLD:
            return None
        entry = start_kind(reader, r["va"], lo)
        if not entry:
            return None
        named = bool(r["name"]) and not r["name"].startswith(("FUN_", "thunk_"))
        if not (named or opens_body(reader(r["va"], 8)) or r["va"] in calls):
            return None
        limit = min(max(nxt, end) + MAX_FOLD, hi)
        cend, close, kind, why = fold_end(reader, decode_fn, r["va"], r["size"], limit, starts, calls)
        if cend is None or cend <= end or nxt >= cend:
            return None
        return _row(r, last, end_kind, cend - end, "0x%08x" % close[0], "interior", "fold",
                    note + "; read forward from this row's own head the body closes on `%s` at 0x%08x "
                    "(%s behind it), %d byte(s) past the map's end, so the row at 0x%08x that starts "
                    "inside that span is interior code of it" % (
                        close[2], close[0], kind, cend - end, nxt),
                    close=close, new_size=cend - r["va"])

    def split_row(r, last, end_kind, cut=False):
        """A row whose end is the next row's start: is the next map row interior code of this one?

        A split is only read as a fold when this row's head is a body's own start: an instruction
        boundary in front of it (a `pad` or a terminator, `start_kind`) **and** one of the three pieces
        of entry evidence -- a name the build gave it (an assert or a registration, not `FUN_*`/`thunk_*`),
        a measured prologue shape, or a `call`/`jmp` that lands on it. A row whose head is inside another
        instruction is not a body at all: no branch can enter it, so the map made a fragment inside a body
        it has no row for, and that is named `no-boundary` rather than left to a review.
        """
        cen["nonterm"] += 1
        cen["split"] += 1
        if cut:
            cen["short_split"] += 1
        end = r["va"] + r["size"]
        note = ("the completed instruction ends where the next map row starts" if cut
                else "the next map row starts at this row's end")
        entry = start_kind(reader, r["va"], lo)
        if not entry:
            cen["split_noboundary"] += 1
            head = containing_head(reader, decode_fn, r["va"], prev_end_of.get(r["va"]))
            if head is not None:
                cen["fragment_head"] += 1
            out.append(_row(r, last, end_kind, 0, "", "split", "no-boundary",
                            note + ", so the map split one body in two; but no instruction ends at this "
                            "row's head -- the bytes in front of it are inside one -- so nothing can "
                            "branch to it and it is a fragment of a body the map has no row for, not a "
                            "body of its own" + (
                                "; the gap in front of it ends its padding at 0x%08x, the only place a body can open" % head
                                if head is not None else ""),
                            target=head or 0))
            return
        named = bool(r["name"]) and not r["name"].startswith(("FUN_", "thunk_"))
        if not (named or opens_body(reader(r["va"], 8)) or r["va"] in calls):
            out.append(_row(r, last, end_kind, 0, "", "split", "next-row",
                            note + ", so the map split one body in two; this row's head follows a "
                            "boundary (%s) but is neither a measured prologue, a name the build gave "
                            "it nor the target of a `call`, so the split is left to a review this rule "
                            "cannot supply" % entry))
            return
        folded = fold_row(r, last, end_kind, note)
        if folded is not None:
            cend = r["va"] + int(folded["new_size"])
            cen["split_fold"] += 1
            cen["fold_bytes"] += cend - end
            cover.append((cend, r["va"]))
            out.append(folded)
            return
        cend, close, kind, why = fold_end(reader, decode_fn, r["va"], r["size"],
                                          min(end + MAX_FOLD, hi), starts, calls)
        if cend is None:
            out.append(_row(r, last, end_kind, 0, "", "split", "next-row",
                            note + ", so the map split one body in two, and the head reads as a body "
                            "but the run forward does not close (%s)" % why))
            return
        cen["split_fold"] += 1
        cen["fold_bytes"] += cend - end
        cover.append((cend, r["va"]))
        out.append(_row(r, last, end_kind, cend - end, "0x%08x" % close[0], "interior", "fold",
                        note + "; read forward from this row's head the body closes on `%s` at 0x%08x "
                        "(%s behind it), so the rows that start inside that span are its interior code"
                        % (close[2], close[0], kind), close=close, new_size=cend - r["va"]))

    def over_row(r, ins, end, nxt):
        """A row whose declared size crosses the next row's start is too long, not short.

        The map says this body runs into the next one, so two rows claim the same bytes. The row's own
        decode still ends where the body does: the last terminator that starts and ends in front of the
        next row's start, with nothing but fill (`0xcc`/`0x90`) between the two, is the boundary, and the
        row is resized down to it. A terminator with code behind it says the body is entered again behind
        the close (the early-return shape) and the size may be right; no terminator at all says the row's
        own decode is not a body. Both refuse rather than cut a body the map already carries.
        """
        cen["nonterm"] += 1
        cand = None
        for t in ins:
            if t[0] + t[1] > nxt:
                break
            if is_term(t[2]) and t[0] < nxt:
                cand = t
        if cand is None:
            cen["refused"] += 1
            cen["refused_over_close"] += 1
            out.append(_row(r, ins[-1] if ins else None, "over:no-close", 0, "", "refused", "over-close",
                            "the row's declared size runs %d byte(s) into the next map row and its own "
                            "decode reaches no terminator before that row's start" % (end - nxt)))
            return
        cend = cand[0] + cand[1]
        fill = reader(cend, nxt - cend)
        if not all(b in FILL for b in fill):
            cen["refused"] += 1
            cen["refused_over_code"] += 1
            out.append(_row(r, cand, "over:then-code", 0, "0x%08x" % cend, "refused", "over-code",
                            "the row's declared size runs %d byte(s) into the next map row; its last "
                            "terminator before that row (`%s` at 0x%08x) has code behind it, so the "
                            "close does not bound the body" % (end - nxt, cand[2], cand[0]), close=cand))
            return
        new_size = cend - r["va"]
        gap = nxt - cend
        cen["over"] += 1
        cen["over_bytes"] += end - cend
        out.append(_row(r, cand, "over:fill", end - cend, "0x%08x" % cend, "over", "over-fill",
                        ("the row's declared size runs %d byte(s) into the next map row (%d past its "
                         "close); its own decode reaches `%s` at 0x%08x and %d byte(s) of fill follow it "
                         "up to the next row's start, so the row ends where that close does")
                        % (end - nxt, end - cend, cand[2], cand[0], gap) if gap else
                        ("the row's declared size runs %d byte(s) into the next map row and ends on an "
                         "`%s` that closes the body %d byte(s) in, with the next row starting exactly "
                         "where it ends") % (end - nxt, cand[2], cend - r["va"]),
                        close=cand, new_size=new_size))


    seen = 0
    for r in rows:
        if not (lo <= r["va"] < hi):
            continue
        cen["text_rows"] += 1
        if not r["size"]:
            continue
        cen["sized_rows"] += 1
        seen += 1
        if progress and seen % 2000 == 0:
            sys.stderr.write("  %d/%d rows\n" % (seen, len(rows)))
            sys.stderr.flush()
        hit = next((c for c in cover if r["va"] < c[0]), None)
        if hit is not None:
            cen["nonterm"] += 1
            cen["split"] += 1
            cen["split_folded"] += 1
            out.append(_row(r, None, "fold:inside", 0, "0x%08x" % hit[1], "folded", "folded",
                            "this row starts inside the body the row at 0x%08x runs to, so it is "
                            "interior code of it and the repair is a `drop`" % hit[1], target=hit[1]))
            continue
        k = bisect.bisect_right(starts, r["va"])
        nxt = starts[k] if k < len(starts) else None
        held = known.get(r["va"]) or known.get("0x%08x" % r["va"])
        if held and held.get("new_size") and int(held["new_size"]) == r["size"]:
            if (held.get("verdict") or "") == "interior":
                # a fold's head row is still in the map: carry the fold itself, not a continuation, or the
                # census would grow a `fold` family the header has no column for
                cen["nonterm"] += 1
                cen["split"] += 1
                cen["split_fold"] += 1
                cen["fold_bytes"] += int(held.get("tail_bytes") or 0)
                cover.append((r["va"] + r["size"], r["va"]))
                out.append(dict(held))
                continue
            if (held.get("verdict") or "") == "over":
                cen["nonterm"] += 1
                cen["over"] += 1
                cen["over_bytes"] += int(held.get("tail_bytes") or 0)
                out.append(dict(held))
                continue
            cen["nonterm"] += 1
            cen["continuation"] += 1
            tail_bytes = int(held.get("tail_bytes") or 0)
            cen["continuation_bytes"] += tail_bytes
            _family(cen, held.get("reason", "head"), tail_bytes)
            out.append(dict(held))
            continue
        ins = decode_fn(reader(r["va"], r["size"]), r["va"])
        end = r["va"] + r["size"]
        if not ins:
            # The row's whole extent is shorter than its first instruction, so objdump refuses the blob.
            # Read across it: an instruction that *starts* at the row's head and runs past its size is
            # the cut case, not an unreadable row.
            wide0 = decode_fn(reader(r["va"], r["size"] + MAX_CUT), r["va"])
            head = next((i for i in wide0 if i[0] == r["va"]), None)
            if head is None:
                cen["undecoded"] += 1
                out.append(_row(r, None, "decode:none", 0, "", "undecoded", "decode-none",
                                "objdump decodes nothing from this row's head"))
                continue
            cen["undecoded_cut"] += 1
            ins = [head]
        last = ins[-1]
        if nxt is not None and nxt < end:
            # the declared size crosses the next map row's start: too long, and the read is the same
            # terminator search from the row's own head
            over_row(r, ins, end, nxt)
            continue
        if last[0] + last[1] != end:
            # the size cuts an instruction: the row's end is a byte count, not a boundary. Read across
            # the cut -- the completed instruction is where the body runs at least, and the run behind it
            # is read by the same tail rule.
            room = (nxt if nxt and nxt > end else end + MAX_CUT) - r["va"]
            wide = decode_fn(reader(r["va"], min(room, r["size"] + MAX_CUT)), r["va"])
            cut = next((i for i in wide if i[0] <= end - 1 < i[0] + i[1]), None)
            if cut is None:
                cen["short"] += 1
                cen["short_nostraddle"] += 1
                out.append(_row(r, last, "cut:none", 0, "", "short", "no-straddle",
                                "the decode stops before the row's own end and no instruction spans it"))
                continue
            cend = cut[0] + cut[1]
            if nxt and cend > nxt:
                cen["short"] += 1
                cen["short_overlap"] += 1
                out.append(_row(r, last, "cut:crosses-next", 0, "", "short", "overlap",
                                "the completed instruction runs %d byte(s) past the next row's start, so "
                                "that row begins inside an instruction" % (cend - nxt)))
                continue
            if is_term(cut[2]):
                continuation(r, cut, "cut:terminator", cend - end, "0x%08x" % cend, "complete-term",
                             "the %d-byte cut instruction is `%s`, so the row ends where it does"
                             % (cut[1], cut[2]), cend - r["va"], close=cut)
                continue
            if nxt == cend:
                split_row(r, cut, "cut:next-row", cut=True)
                continue
            tail, close, why = tail_rule(reader, decode_fn, cend, nxt if nxt else cend + MAX_TAIL,
                                         targets=branch_targets(wide))
            if close is None:
                cen["short"] += 1
                cen["short_refused"] += 1
                out.append(_row(r, cut, "cut:tail-%s" % why, 0, "", "short", "tail-%s" % why,
                                "the completed instruction is not a terminator and the run behind it "
                                "does not close (%s)" % why))
            else:
                cclose = close[0] + close[1]
                continuation(r, close, "cut:%s" % why, cclose - end, "0x%08x" % cclose, "complete-fill",
                             "the row's size cuts an instruction; completing it reaches `%s`, and the run "
                             "behind it closes on `%s`" % (cut[2], close[2]), cclose - r["va"], close=close)
            continue
        if is_term(last[2]):
            cen["terminator"] += 1
            continue
        h = head_at.get(end)
        if nxt == end:
            split_row(r, last, "next-row")
        elif h and h["size"] and h["verdict"] in ("body", "island"):
            continuation(r, last, "head:%s" % h["verdict"], h["size"], "0x%08x" % (h["va"] + h["size"]),
                         "head", "the %d-byte %s at the row's end closes on `%s`" % (
                             h["size"], h["verdict"], h["tail"] or "a terminator"),
                         r["size"] + h["size"], head=h)
        else:
            # A row the map cut short before a fragment that starts inside its own body is the same
            # repair as a split: read forward from the row's own head. Tried before the tail rule
            # because the next map row leaves the tail no room to decode -- 0x002e0020 is 12 bytes and
            # the fragment at 0x002e002e starts two bytes later, inside the body that runs to 0x2e008d.
            folded = fold_row(r, last, "fold:short",
                              "the row ends on a %s with its own body still running" % last[2])
            if folded is not None:
                cend = r["va"] + int(folded["new_size"])
                cen["nonterm"] += 1
                cen["fold_short"] += 1
                cen["split_fold"] += 1
                cen["fold_bytes"] += cend - end
                cover.append((cend, r["va"]))
                out.append(folded)
                continue
            tail, close, why = tail_rule(reader, decode_fn, end, nxt if nxt else end + MAX_TAIL,
                                         targets=branch_targets(ins))
            if close is not None:
                # a pad *is* the boundary: the body ends where the pad's first `int3` begins
                cclose = close[0] if mnemonic(close[2]) == "int3" else close[0] + close[1]
                tail_bytes = cclose - end
                if tail_bytes <= 0:
                    cen["nonterm"] += 1
                    cen["bounded"] += 1
                    out.append(_row(r, last, "tail:%s" % why, 0, "", "bounded", "pad",
                                    "the terminator at the row's own end closes it, so the body is "
                                    "bounded there and nothing is missing"))
                else:
                    note = {
                        "tail": "a decode from the row's end reaches `%s` and only fill follows",
                        "jump": "a decode from the row's end reaches `%s`, which closes the run (a tail "
                                "call -- its target code is elsewhere)",
                        "pad": "a decode from the row's end reaches `%s`, which closes the run (a pad "
                               "behind it bounds it)",
                        "padding": "a decode from the row's end reaches `%s` and the bytes behind it are "
                                   "alignment padding, not code",
                        "behind": "a decode from the row's end reaches `%s` and the code behind it opens "
                                  "a body of its own, so the close bounds this one",
                        "body": "a decode from the row's end reaches `%s`, and the code behind it is a "
                                "complete body of its own -- it closes before the next map row with a "
                                "pad or fill behind it -- so the close bounds this row",
                        "data": "a decode from the row's end reaches `%s`, and the bytes behind it hold an "
                                "instruction no ring-3 program can execute, so they are a table the row's "
                                "size overran rather than code, and the close bounds the row",
                        "jump-data": "a decode from the row's end reaches `%s`, and the bytes behind it "
                                     "hold an instruction no ring-3 program can execute -- the dispatch's "
                                     "own table -- so the body ends at the jump",
                        "follow": "a decode from the row's end reaches `%s`, and the code behind it reads "
                                  "the same frame -- the shared epilogue an early return jumps into -- so "
                                  "the body runs on to the close at 0x%08x",
                        "epilogue": "a decode from the row's end reaches `%s`, and the code behind it "
                                    "unwinds the stack (`pop`, `leave`, `mov esp,ebp` or `add esp,N`) -- "
                                    "the body's own epilogue, which this build emits once per return "
                                    "path -- so that `ret` is one of several returns, not the body's "
                                    "end, and the read runs on to the close at 0x%08x",
                        "jump-pad": "a decode from the row's end reaches `%s`, and the bytes behind it are "
                                    "alignment padding, so the body's own code stops at the jump",
                        "jump-behind": "a decode from the row's end reaches `%s`, and the code behind it "
                                       "opens a body of its own -- the jump is a tail call into it, so it "
                                       "closes this body",
                        "jump-table": "a decode from the row's end reaches `%s`, an indirect jump: "
                                      "the body ends at the dispatch and its table follows",
                        "jump-inside": "a decode from the row's end reaches `%s`, whose target lies "
                                       "forward inside this row's own gap -- a branch, not an exit -- so "
                                       "the read continues to the close at 0x%08x",
                    }[why]
                    note = note % ((close[2], cclose)
                                   if why in ("follow", "epilogue", "jump-inside") else (close[2],))
                    continuation(r, last, "tail:%s" % why, tail_bytes, "0x%08x" % cclose, why, note,
                                 cclose - r["va"], close=close)
            else:
                # The truncated decode desynced at the row's end. objdump cannot finish the instruction
                # the size cuts, so it re-splits the bytes of that instruction and everything after it,
                # and the last "instruction" it reports reaches the row's end -- which is why the cut
                # branch above never sees the cut. Read the row again over one more instruction and run
                # the cut rule on the instruction that actually spans `end - 1`.
                room = r["size"] + MAX_CUT
                if nxt and nxt - r["va"] < room:
                    room = max(r["size"], nxt - r["va"])
                span = None
                if room > r["size"]:
                    wide = decode_fn(reader(r["va"], room), r["va"])
                    span = next((t for t in wide if t[0] <= end - 1 < t[0] + t[1]), None)
                if span is not None and span[0] + span[1] != end:
                    cen["reread"] += 1
                    scend = span[0] + span[1]
                    if is_term(span[2]):
                        continuation(r, span, "cut:reread", scend - end, "0x%08x" % scend,
                                     "complete-term",
                                     "the row's end cuts `%s` -- the truncated decode could not finish "
                                     "it, so it re-split the bytes and the cut was invisible -- and that "
                                     "instruction is a terminator, so the row ends where it does"
                                     % span[2], scend - r["va"], close=span)
                        continue
                    tail2, close2, _why2 = tail_rule(reader, decode_fn, scend,
                                                     nxt if nxt else scend + MAX_TAIL,
                                                     targets=branch_targets(wide))
                    if close2 is not None:
                        c2 = close2[0] + close2[1]
                        continuation(r, close2, "cut:reread", c2 - end, "0x%08x" % c2,
                                     "complete-fill",
                                     "the row's end cuts `%s` -- the truncated decode could not finish "
                                     "it, so it re-split the bytes and the cut was invisible -- and the "
                                     "run behind it closes on `%s` at 0x%08x"
                                     % (span[2], close2[2], close2[0]), c2 - r["va"], close=close2)
                        continue
                cen["nonterm"] += 1
                cen["refused"] += 1
                cen["refused_%s" % why.replace("-", "_")] += 1
                out.append(_row(r, last, "tail:%s" % why, 0, "", "refused", why,
                                "a `jmp` opens the tail, so its target code is behind it" if why == "jump"
                                else "no `ret` before the next map row" if why == "no-close"
                                else "nothing decodes from the row's end" if why == "undecoded"
                                else "the row ends where the next map row starts" if why == "no-room"
                                else "the close is followed by code, not fill or a pad"))
    # control: the same forward read on rows that already end on a terminator. It must reproduce the
    # map's own size, or a fold would be inventing an extent rather than reading one.
    sampled = [r for r in rows if lo <= r["va"] < hi and r["size"]][::30][:400]
    for r in sampled:
        ins = decode_fn(reader(r["va"], r["size"]), r["va"])
        end = r["va"] + r["size"]
        if not ins or not is_term(ins[-1][2]) or ins[-1][0] + ins[-1][1] != end:
            continue
        cen["fold_trials"] += 1
        close, _kind = close_ahead(reader, decode_fn, r["va"], min(end + MAX_FOLD, hi))
        if close is not None:
            cend = close[0] if mnemonic(close[2]) == "int3" else close[0] + close[1]
            if cend == end:
                cen["fold_agree"] += 1
    # a dropped row is gone from the map, so its record is carried from the artifact or the family's
    # count would read zero on the next run and the doc would change under it
    inmap = set(starts)
    for key, held in known.items():
        if (held.get("verdict") or "") != "folded":
            continue
        if int(held["row_va"], 16) in inmap:
            continue
        out.append(dict(held))
        cen["nonterm"] += 1
        cen["split"] += 1
        cen["split_folded"] += 1
    # control: the same tail rule over the image's data sections
    for i in range(null_trials):
        va = null_addresses[i] if i < len(null_addresses) else None
        if va is None:
            break
        cen["null_trials"] += 1
        tail = decode_fn(reader(va, 64), va)
        close, why = tail_close(tail)
        if not close:
            continue
        after = reader(close[0] + close[1], max(0, 64 - (close[0] + close[1] - va)))
        if all(b in FILL for b in after):
            cen["null_hits"] += 1
    return out, dict(cen)


null_addresses = []


def _row(r, last, end_kind, tail_bytes, close_va, verdict, reason, note, head=None, close=None,
         new_size=0, target=0):
    return {"row_va": "0x%08x" % r["va"], "row_name": r["name"], "row_size": r["size"],
            "last_va": "0x%08x" % last[0] if last else "", "last_length": last[1] if last else "",
            "last_insn": last[2] if last else "", "end_kind": end_kind,
            "tail_bytes": tail_bytes or "", "close_va": close_va,
            "close_insn": close[2] if close else "",
            "head_va": "0x%08x" % head["va"] if head else "",
            "head_size": head["size"] if head else "",
            "head_prologue": head["prologue"] if head else "",
            "head_verdict": head["verdict"] if head else "",
            "new_size": new_size or "", "target_va": ("0x%08x" % target) if target else "",
            "verdict": verdict, "reason": reason, "note": note}


def pct(a, b):
    return (100.0 * a / b) if b else 0.0


def render(rows, cen):
    def n(k):
        return int(cen.get(k, 0) or 0)
    cont = [r for r in rows if r["verdict"] == "continuation"]
    cut_rows = [r for r in rows if (r.get("end_kind") or "").startswith("cut:")]
    fill_bytes = sum(int(r["tail_bytes"] or 0) for r in cut_rows if r["reason"] == "complete-fill")
    lines = []
    lines.append(
        "**%d of the map's %d `.text` rows do not end on a terminator: %d close on one, and %d of the "
        "rest leave a tail the map never counted** -- read by decoding every row at its own head and "
        "classifying its last instruction (`inventory/symbol_extents.csv`)." % (
            n("nonterm") + n("short") + n("undecoded"), n("text_rows"), n("terminator"),
            n("continuation")))
    lines.append("")
    lines.append(
        "**The rule.** A body does not fall out of its own end: what follows a `pop ebp` or an "
        "`add esp,0x10` is the same function's epilogue. So a row whose last instruction is not "
        "`ret`/`jmp`/`int3` is unfinished, and the tail is measured three ways. **%d rows carry a gap "
        "head at their end** -- the head rule's own sweep closes it, so the head's measured size is the "
        "tail (**%d bytes**). **%d more have no head there**, and a decode from the row's own end reaches "
        "a terminator with nothing but fill (`0xCC`/`0x90`) between it and the next map row (**%d bytes**). "
        "**%d rows are cut mid-instruction by a size that is a byte count rather than a boundary**: the "
        "decode is read across the cut, and the row ends where the completed instruction lands when that "
        "is a terminator itself (**%d**) or where the run behind it reaches its first terminator "
        "(**%d**, %d bytes). **%d** of those cuts are invisible to the truncated decode -- objdump cannot "
        "finish the instruction the size cuts, so it re-splits the bytes and the last instruction it "
        "reports reaches the row's end -- and the row is read a second time over one more instruction to "
        "find the instruction that actually spans the cut. The first terminator closes the run when only "
        "fill follows it, when it is a "
        "`jmp` -- a tail call, its target code elsewhere -- or when it is a `ret` with a pad of two or "
        "`CC` behind it (**%d rows, %d bytes**), because only a run of two or more `int3` bounds a "
        "body. The code behind a close is not the body's own code, so a close with code behind it is "
        "read six ways: **%d rows** end where the next instructions are alignment padding, **%d** "
        "where they open a body of their own, **%d** where they read the same frame -- the shared "
        "epilogue an early return jumps into -- and the body runs on to the next close (**%d bytes**), "
        "and **%d** where the code behind the `ret` unwinds the row's **own** frame: it pops exactly the "
        "registers the row's opening pushed and adds back exactly the bytes it allocated, so the early "
        "return's `ret` is not the body's end and the read runs on to the close behind the epilogue "
        "(**%d bytes**). "
        "**%d more** end where a complete body of its own sits behind the close -- a run that closes on "
        "a terminator with fill or a pad behind it inside 64 bytes, so the code is that body's, not the "
        "row's -- and **%d** where the bytes behind the close are not code at all: a run that holds an "
        "instruction no ring-3 program can execute (`in`/`out`, a system-table opcode, objdump's "
        "`(bad)`) is a table the row's size overran, and no branch of this body reaches it "
        "(**%d bytes**). "
        "Together **%d rows and %d bytes**: %d of them grow by one instruction or less (a row that "
        "stops on `pop ebp`/`add esp,N` before its own `ret`), and the largest is `%s`, a %s-byte row "
        "continued by %s bytes." % (
        n("head_family"), n("head_bytes"), n("tail_family"), n("tail_bytes_total"),
        len(cut_rows), n("complete_term"), n("complete_fill"), fill_bytes, n("reread"),
        n("pad_family") + n("jump_family"), n("pad_bytes") + n("jump_bytes"),
        n("padding_family"), n("behind_family"), n("follow_family"),
        n("padding_bytes") + n("behind_bytes") + n("follow_bytes"),
        n("epilogue_family"), n("epilogue_bytes"),
        n("body_family"), n("data_family"), n("body_bytes") + n("data_bytes"),
        n("continuation"), n("continuation_bytes"),
        sum(1 for r in cont if int(r["tail_bytes"] or 0) <= 4),
        (max(cont, key=lambda r: int(r["tail_bytes"] or 0))["row_va"] if cont else "-"),
        (max(cont, key=lambda r: int(r["tail_bytes"] or 0))["row_size"] if cont else "-"),
        (max(cont, key=lambda r: int(r["tail_bytes"] or 0))["tail_bytes"] if cont else "-")))
    lines.append("")
    jpad = [r for r in cont if r["reason"] == "jump-pad"]
    jbehind = [r for r in cont if r["reason"] == "jump-behind"]
    jinside = [r for r in cont if r["reason"] == "jump-inside"]
    jtable = [r for r in cont if r["reason"] == "jump-table"]
    jdata = [r for r in cont if r["reason"] == "jump-data"]
    lines.append(
        "**A `jmp` in a tail is read by its target, because it is unconditional.** Nothing falls through "
        "an unconditional jump, so the bytes behind it are reached only by another branch or by the jump "
        "itself -- which makes the target the discriminator. A target that lands forward inside the "
        "row's own gap is a **branch**, not an exit (the `if`/`else` shape, whose else-arm sits behind "
        "the jump), so those bytes are the body's own code and the read runs on to the close behind them: "
        "**%d rows, %d bytes**. A target elsewhere closes the run when alignment padding follows the jump "
        "(**%d rows, %d bytes**) or when the bytes behind it open a body of their own -- a tail call into "
        "that body (**%d rows, %d bytes**), and an **indirect** jump -- a `switch` dispatch -- ends "
        "the body at the jump with its table behind it (**%d rows, %d bytes**). A direct jump with data "
        "behind it -- a run holding an instruction no ring-3 program can execute -- ends the body there "
        "too, because the table the dispatch reads is what follows it (**%d rows, %d bytes**). **%d "
        "tails** stop on a `jmp` none of those explain: code "
        "behind it that neither pads, nor opens a body, nor reads the same frame, nor lies at the jump's "
        "own target." % (
            len(jinside), sum(int(r["tail_bytes"] or 0) for r in jinside),
            len(jpad), sum(int(r["tail_bytes"] or 0) for r in jpad),
            len(jbehind), sum(int(r["tail_bytes"] or 0) for r in jbehind),
            len(jtable), sum(int(r["tail_bytes"] or 0) for r in jtable),
            len(jdata), sum(int(r["tail_bytes"] or 0) for r in jdata), n("refused_jump")))
    lines.append("")
    lines.append(
        "**The control is the same rule over data.** Decoding from **%d** fixed addresses in the image's "
        "data sections finds a `ret` with only fill after it **%d** time(s) -- %.1f%% -- because that "
        "shape is what code has and data does not. The population it is compared against is the %d rows "
        "above, whose tails are code by construction (the row's own head decodes into them)." % (
            n("null_trials"), n("null_hits"), pct(n("null_hits"), n("null_trials")), n("continuation")))
    lines.append("")
    if cont:
        lines.append("| the row | its size | last instruction | evidence | tail | repaired size |")
        lines.append("|---|---:|---|---|---:|---:|")
        for r in sorted(cont, key=lambda r: -int(r["tail_bytes"] or 0))[:12]:
            ev = ("head `%s` %s" % (r["head_va"], r["head_verdict"]) if r["reason"] == "head"
                  else "`%s` at `%s`" % (r["close_insn"], r["close_va"]))
            lines.append("| `%s` %s | %s | `%s` | %s | %s | %s |" % (
                r["row_va"], r["row_name"], r["row_size"], r["last_insn"], ev, r["tail_bytes"],
                r["new_size"]))
        lines.append("")
    rest = [r for r in rows if r["verdict"] in ("split", "refused", "bounded", "short", "undecoded",
                                                "interior", "folded")]
    if rest:
        splits = [r for r in rest if r["verdict"] == "split"]
        folds = [r for r in rest if r["verdict"] == "interior"]
        nobd = [r for r in splits if r["reason"] == "no-boundary"]
        suffix = ((" -- %d of them are not splits at all (their head is inside another instruction, "
                   "below) and %d end on a real boundary and need a review this rule cannot supply"
                   % (len(nobd), len(splits) - len(nobd))) if splits
                  else " and needs a review this rule cannot supply")
        folded = [r for r in rest if r["verdict"] == "folded"]
        short = [r for r in rest if r["verdict"] == "short"]
        lines.append(
            "**What this does not repair.** **%d** rows still end mid-instruction (`short`): nothing "
            "spans the cut in %d, the completed instruction runs into the next row's start in %d, the run "
            "behind it does not close in %d, and in %d the completed instruction ends where the next row "
            "starts -- a split, listed below. **%d** do not decode at all. **%d** rows are already "
            "bounded where the map says they end -- a pad of two or more `0xcc` begins exactly there, so "
            "the body has no tail and nothing is missing. Of the rest, **%d** stop on live code with no "
            "close before the next row (**%d** a `jmp` with code behind it, **%d** a `ret` followed by "
            "code that is neither padding, a body's opening nor the same frame, **%d** no terminator at "
            "all, **%d** that run into the next row and hold a close with code behind it, **%d** whose "
            "size runs into the next row with no close in front of it), and **%d** are a body the map "
            "already split from its own continuation -- %s -- which is a `drop` repair%s." % (
                len(short), n("short_nostraddle"), n("short_overlap"), n("short_refused"),
                n("short_split"), n("undecoded"), n("bounded"), n("refused"), n("refused_jump"),
                n("refused_then_code"), n("refused_no_close"),
                n("refused_over_code"), n("refused_over_close"),
                len(splits), ", ".join("`%s`" % r["row_va"] for r in splits) or "none", suffix))
        lines.append("")
    over = [r for r in rows if r["verdict"] == "over"]
    if over or n("refused_over_code") or n("refused_over_close"):
        lines.append(
            "**A size can be too long, and the map says so wherever it runs into the next row.** **%d** "
            "rows declare a size that crosses the next map row's start, so two rows claim the same "
            "bytes; each one's own decode reaches its close before that start and only fill follows it, "
            "so the row is **resized down** to where it closes (**%d bytes** given back) -- the largest "
            "is `%s`, %s bytes cut to %s. **%d** refuse because a close has code behind it and **%d** "
            "because no close is reached at all: resizing those would cut a body the map already carries."
            % (len(over), n("over_bytes"),
               (max(over, key=lambda r: int(r["tail_bytes"] or 0))["row_va"] if over else "none"),
               (max(over, key=lambda r: int(r["tail_bytes"] or 0))["row_size"] if over else "0"),
               (max(over, key=lambda r: int(r["tail_bytes"] or 0))["new_size"] if over else "0"),
               n("refused_over_code"), n("refused_over_close")))
        lines.append("")
    if folds or folded:
        lines.append(
            "**Rows whose next map row starts inside their own body are folds.** %d rows read forward "
            "from their **own head** -- an instruction boundary in front of them (a `pad` or a "
            "terminator) and one of three pieces of entry evidence: a measured prologue, a name the "
            "build gave them, or a `call`/`jmp` that lands on them -- to the close that ends the body "
            "(**%d bytes** past the map's end), and every map row that starts inside that span is "
            "interior code of it: **%d rows** drop into the row above, each a `drop` row in "
            "`symbols/xbe_boundaries.csv` beside the fold's `resize`. A row inside the span blocks the "
            "fold only when **something calls it**: a body is entered by a call or a jump, never by "
            "falling through its neighbour's last instruction, so a row inside a linear decode is "
            "continuation code unless a call lands on it. **The control is the same read on rows that "
            "already close**: from its head, **%d of the %d rows sampled** land on the map's own end "
            "(%.1f%%), so the read reproduces a size it was not given." % (
                len(folds), n("fold_bytes"), len(folded), n("fold_agree"), n("fold_trials"),
                pct(n("fold_agree"), n("fold_trials"))))
        lines.append("")
    if n("split_noboundary") or n("split"):
        rows_split = [r for r in rows if r["verdict"] == "split"]
        rows_nobd = [r for r in rows_split if r["reason"] == "no-boundary"]
        heads = sorted({r["target_va"] for r in rows_nobd if r.get("target_va")})
        lines.append(
            "**A split whose head is inside another instruction is not a split at all.** %d rows end "
            "exactly where the next map row starts, and no instruction ends at their head -- the bytes "
            "in front of it are the middle of one -- so nothing can branch to it and it is a fragment "
            "the map made inside a body it has no row for. That body is not unmeasured: decoded from the "
            "previous row's end, %d of the %d fragments sit in a gap whose first code byte is in front "
            "of them (%s) -- the first non-padding instruction after the row in front. Their bytes are left "
            "where they are: a `drop` needs a containing row, and the gap has none, which is the `add` "
            "repair this rule does not write. %d further splits end on "
            "a real boundary and are left to the review above."
            % (len(rows_nobd), n("fragment_head"), len(rows_nobd), ", ".join("`%s`" % h for h in heads)
               or "none found", len(rows_split) - len(rows_nobd)))
        lines.append("")
    lines.append(
        "**The repairs are in the file, not in this tool's output.** `--write-boundaries` appends one "
        "`resize` row per continuation to `symbols/xbe_boundaries.csv`, and `tools/symbol-boundaries.py "
        "--apply` moves the map; `--check` proves every continuation's repair is still applied, so a "
        "Ghidra re-import that reverts one is red instead of silently restoring the short extent.")
    return "\n".join(lines)


def splice_doc(path, block, marks):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(marks[0]), text.find(marks[1])
    if i < 0 or j < 0:
        return None
    return text[:i] + marks[0] + "\n" + block + "\n" + text[j:]


def read_artifact(path=ARTIFACT):
    rows, cen = [], {}
    full = path if os.path.isabs(path) else os.path.join(ROOT, path)
    for r in read_csv(full):
        if (r.get("verdict") or "") == "census":
            for k in CENSUS:
                try:
                    cen[k] = int(r.get(k) or 0)
                except ValueError:
                    cen[k] = 0
        else:
            rows.append(dict(r))
    return rows, cen


def write_artifact(rows, cen, path=None):
    unknown = set(cen) - set(CENSUS)
    if unknown:
        raise SystemExit("symbol-extents: the walk counts %s, which the header does not write"
                         % ", ".join(sorted(unknown)))
    path = path or os.path.join(ROOT, ARTIFACT)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(ROW) + list(CENSUS), lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(dict((k, r.get(k, "")) for k in ROW))
        w.writerow(dict([("row_va", "census"), ("verdict", "census")]
                        + [(k, cen.get(k, 0)) for k in CENSUS]))


def evidence_for(r):
    if r["reason"] == "over-fill":
        return ("%s; the row's own decode reaches `%s` at %s and only fill follows it up to the next "
                "row's start, so the row ends at %s and not at the next row's head"
                % (REPAIRS[6], r["close_insn"], r["close_va"], r["new_size"]))
    if r["reason"] == "head":
        return ("%s; the head at %s is a %s-byte %s, so the extents are one function and the row is short "
                "by the whole tail" % (REPAIRS[0], r["head_va"], r["head_size"], r["head_verdict"]))
    if r["reason"].startswith("complete"):
        return ("%s; the row's last decoded instruction ends at the cut, the instruction that spans it is "
                "`%s`, and the first terminator behind it is `%s` at %s, so the row runs to %s"
                % (REPAIRS[2], r["last_insn"], r["close_insn"] or "the same instruction", r["close_va"],
                   r["new_size"]))
    if r["reason"] in ("jump", "pad"):
        return ("%s; `%s` closes the run %s bytes past the row's end, and what follows it is fill or a pad "
                "rather than code" % (REPAIRS[3], r["close_insn"], r["tail_bytes"]))
    if r["reason"] in ("padding", "behind", "follow", "body", "data", "jump-data"):
        return (REPAIRS[5] if r["reason"] in ("body", "data", "jump-data") else REPAIRS[4]) + (
            "; `%s` at %s is the first terminator behind the row's end and the bytes behind it are "
            "%s, so the body runs to %s" % (
                r["close_insn"], r["close_va"],
                {"padding": "alignment filler", "behind": "a body's own opening",
                 "follow": "the same frame's shared epilogue",
                 "body": "a complete body of their own, which closes before the next map row",
                 "data": "not code -- an instruction no ring-3 program can execute is in the run, so "
                         "they are a table the size overran",
                 "jump-data": "not code -- an instruction no ring-3 program can execute is in the run, "
                              "so the dispatch's table follows it"}[r["reason"]], r["new_size"]))
    if r["verdict"] == "interior":
        return (REPAIRS[4] + "; the row's head is a body's own start -- an instruction boundary in front "
                "of it and a name, a measured prologue or a `call` landing on it -- and the body read "
                "forward from it closes on `%s` at %s, so the %s bytes to that close are one body and "
                "the map rows inside it are interior code"
                % (r["close_insn"], r["close_va"], r["tail_bytes"]))
    return ("%s; `%s` closes the run %s bytes past the row's end and only fill follows before the next "
            "row" % (REPAIRS[1], r["close_insn"], r["tail_bytes"]))


def write_boundaries(rows, path=BOUNDARIES):
    """Append one `resize` row per continuation and per fold, and one `drop` per folded row."""
    with open(path, newline="", encoding="utf-8") as fh:
        text = fh.read()
    have = {b["address"]: b for b in read_boundaries(path)}
    added, kept, bad = 0, 0, []
    lines = []
    repl = {}
    for r in rows:
        if r["verdict"] == "folded":
            va, target = int(r["row_va"], 16), int(r["target_va"], 16)
            b = have.get(va)
            if b:
                if b["action"] == "drop":
                    kept += 1
                elif b["action"] == "resize":
                    # the row was a continuation in an earlier measurement and is interior code of a
                    # fold now: the resize it carried is superseded by the drop, so its line is
                    # rewritten rather than refused
                    repl[va] = '0x%08x,drop,0x%08x,,"%s"' % (
                        va, target,
                        "the body at 0x%08x reads forward from its own head to a close this row starts "
                        "inside, so this row is interior code of it" % target)
                    added += 1
                else:
                    bad.append("%s already carries a %s" % (r["row_va"], b["action"]))
                continue
            lines.append('0x%08x,drop,0x%08x,,"%s"' % (
                va, target, ("the body at 0x%08x reads forward from its own head to a close this row "
                             "starts inside, so this row is interior code of it" % target)))
            added += 1
            continue
        if r["verdict"] not in ("continuation", "interior", "over"):
            continue
        va, size = int(r["row_va"], 16), int(r["new_size"])
        b = have.get(va)
        if b:
            if b["action"] != "resize" or b["size"] != size:
                bad.append("%s already carries a %s to %s" % (r["row_va"], b["action"], b["size"]))
            else:
                kept += 1
            continue
        lines.append('0x%08x,resize,,%d,"%s"' % (va, size, evidence_for(r).replace('"', "'")))
        added += 1
    if bad:
        raise SystemExit("symbol-extents: refusing: " + "; ".join(bad))
    if repl:
        out = []
        for line in text.splitlines(keepends=True):
            addr = line.split(",", 1)[0].strip()
            if addr in repl:
                line = repl[addr] + ("\r\n" if line.endswith("\r\n") else "\n")
            out.append(line)
        text = "".join(out)
    if lines:
        if not text.endswith(("\n", "\r\n")):
            text += "\r\n"
        text += "\r\n".join(line.replace("\r", "") for line in lines) + "\r\n"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            fh.write(text)
    print("boundaries: %d resize row(s) added, %d already present" % (added, kept))
    return added, kept


def write_overrides(rows, path=OVERRIDES):
    """Follow a resize into `symbols/xbe_overrides.csv`'s `function_size` column.

    `tools/symbol-boundaries.py --apply` refuses a map where an override row's `function_size` disagrees
    with the row it describes, so a repair that grows a row the registration pass sized has to move that
    column too. Only a row that already carries a size is rewritten: an empty column says the pass never
    measured it, and inventing one there would put a size in a file another tool owns. The file is edited
    line by line, because its order is append order and not its addresses.
    """
    with open(path, newline="", encoding="utf-8") as fh:
        text = fh.read()
    new = {r["row_va"]: int(r["new_size"]) for r in rows
           if r["verdict"] in ("continuation", "interior", "over")}
    lines = text.splitlines(keepends=True)
    head = lines[0].rstrip("\r\n").split(",")
    try:
        col = head.index("function_size")
    except ValueError:
        raise SystemExit("symbol-extents: %s has no function_size column" % path)
    moved = 0
    for i, line in enumerate(lines[1:], 1):
        body = line.rstrip("\r\n")
        addr = body.split(",", 1)[0]
        if addr not in new:
            continue
        # `function_size` is the last column and a quoted evidence field may hold commas, so the field is
        # rewritten from the end of the line rather than by splitting the row.
        if "," not in body:
            continue
        keep, last = body.rsplit(",", 1)
        if last.strip().isdigit() and int(last) != new[addr]:
            print("%s %s: function_size %s -> %d" % (addr, body.split(",")[1], last, new[addr]))
            lines[i] = keep + "," + str(new[addr]) + ("\r\n" if line.endswith("\r\n") else "\n")
            moved += 1
    if moved:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            fh.write("".join(lines))
    print("overrides: %d function_size row(s) followed their row" % moved)
    return moved


def check():
    rows, cen = read_artifact()
    bad = []
    if not rows or not cen:
        bad.append("%s is missing or holds no census row" % ARTIFACT)
    by_addr = {r["va"]: r for r in read_map()}
    bounds = {b["address"]: b for b in read_boundaries()}
    repairs = {a: b for a, b in bounds.items() if b["action"] == "resize"}
    drops = {a: b for a, b in bounds.items() if b["action"] == "drop"}
    counts = collections.Counter(r["verdict"] for r in rows)
    for r in rows:
        va = int(r["row_va"], 16)
        row = by_addr.get(va)
        if r["verdict"] == "folded":
            if row:
                bad.append("%s is a folded row but the map still has it" % r["row_va"])
            d = drops.get(va)
            target = by_addr.get(int(r["target_va"], 16)) if r.get("target_va") else None
            if not d:
                bad.append("%s is folded with no `drop` in symbols/xbe_boundaries.csv" % r["row_va"])
            elif int(d["target"] or "0", 16) != int(r["target_va"], 16):
                bad.append("%s drops into %s, the artifact names %s" % (r["row_va"], d["target"],
                                                                       r["target_va"]))
            elif not target:
                bad.append("%s is folded into %s, which is not a map row" % (r["row_va"], r["target_va"]))
            elif not (target["va"] <= va < target["va"] + target["size"]):
                bad.append("%s is folded into %s, whose span does not hold it" % (r["row_va"],
                                                                                 r["target_va"]))
            continue
        if not row:
            bad.append("%s has no row in the map" % r["row_va"])
            continue
        if r["verdict"] in ("continuation", "interior", "over"):
            want = int(r["new_size"])
            if row["size"] != want:
                bad.append("%s is %d in the map, the artifact measured %d" % (r["row_va"], row["size"], want))
            b = repairs.get(va)
            if not b:
                bad.append("%s has no resize repair in symbols/xbe_boundaries.csv" % r["row_va"])
            elif b["size"] != want:
                bad.append("%s repairs to %d, the artifact measured %d" % (r["row_va"], b["size"], want))
            elif not any(b["evidence"].startswith(p) for p in REPAIRS):
                bad.append("%s repair carries no evidence of the rule" % r["row_va"])
            if va in drops:
                bad.append("%s is a %s row and also dropped" % (r["row_va"], r["verdict"]))
        elif va in repairs:
            bad.append("%s is a %s row but carries a resize repair" % (r["row_va"], r["verdict"]))
    if cen.get("continuation") != counts.get("continuation", 0):
        bad.append("census says %s continuations, %d rows carry that verdict"
                   % (cen.get("continuation"), counts.get("continuation", 0)))
    if cen.get("continuation_bytes") != sum(int(r["tail_bytes"] or 0) for r in rows
                                            if r["verdict"] == "continuation"):
        bad.append("census continuation_bytes does not equal the rows' tails")
    if cen.get("nonterm", 0) + cen.get("short", 0) + cen.get("undecoded", 0) != len(rows):
        bad.append("census population does not equal the %d rows" % len(rows))
    if counts.get("interior", 0) + counts.get("folded", 0) != int(cen.get("split_fold") or 0) + \
            int(cen.get("split_folded") or 0):
        bad.append("the fold's rows do not match the census")
    if int(cen.get("split") or 0) != counts.get("split", 0) + counts.get("interior", 0) + \
            counts.get("folded", 0):
        bad.append("the %s splits do not add up to the %d split rows, %d interiors and %d folded rows"
                   % (cen.get("split"), counts.get("split", 0), counts.get("interior", 0),
                      counts.get("folded", 0)))
    if int(cen.get("fold_short") or 0) > counts.get("interior", 0):
        bad.append("the %s short-row folds exceed the %d interior rows"
                   % (cen.get("fold_short"), counts.get("interior", 0)))
    if int(cen.get("fold_bytes") or 0) != sum(int(r["tail_bytes"] or 0) for r in rows
                                              if r["verdict"] == "interior"):
        bad.append("census fold_bytes does not equal the folds' tails")
    fam_rows = sum(int(cen.get("%s_family" % f) or 0) for f in FAMILIES)
    fam_bytes = sum(int(cen.get("%s_bytes" % f if f != "tail" else "tail_bytes_total") or 0)
                    for f in FAMILIES)
    if fam_rows != cen.get("continuation", 0):
        bad.append("the %d evidence families add up to %d, not the %s continuations"
                   % (len(FAMILIES), fam_rows, cen.get("continuation")))
    if fam_bytes != cen.get("continuation_bytes", 0):
        bad.append("the families' byte totals add up to %d, not the %s the tails hold"
                   % (fam_bytes, cen.get("continuation_bytes")))
    if int(cen.get("complete_term") or 0) + int(cen.get("complete_fill") or 0) != \
            int(cen.get("complete_family") or 0):
        bad.append("the cut repairs do not add up to the `complete` family")
    if int(cen.get("reread") or 0) > int(cen.get("complete_family") or 0):
        bad.append("the %s re-read repairs exceed the %s in the complete family"
                   % (cen.get("reread"), cen.get("complete_family")))
    if int(cen.get("continuation") or 0) + int(cen.get("split") or 0) + int(cen.get("refused") or 0) + \
            int(cen.get("bounded") or 0) + int(cen.get("over") or 0) != int(cen.get("nonterm") or 0):
        bad.append("continuation + split + refused + bounded + over does not equal the non-terminating rows")
    if int(cen.get("over") or 0) != counts.get("over", 0):
        bad.append("census says %s over-long repairs, %d rows carry that verdict"
                   % (cen.get("over"), counts.get("over", 0)))
    if int(cen.get("over_bytes") or 0) != sum(int(r["tail_bytes"] or 0) for r in rows
                                              if r["verdict"] == "over"):
        bad.append("census over_bytes does not equal the over-long rows' overruns")
    if int(cen.get("short_nostraddle") or 0) + int(cen.get("short_overlap") or 0) + \
            int(cen.get("short_refused") or 0) > int(cen.get("short") or 0):
        bad.append("the short sub-families exceed the short population")
    if int(cen.get("short_split") or 0) > int(cen.get("split") or 0):
        bad.append("the cut splits exceed the split population")
    if int(cen.get("fragment_head") or 0) > int(cen.get("split_noboundary") or 0):
        bad.append("the %s fragments with a measured holder exceed the %s fragments"
                   % (cen.get("fragment_head"), cen.get("split_noboundary")))
    map_starts = {r["va"] for r in read_map() if r["size"]}
    for r in rows:
        if r["verdict"] == "split" and r["reason"] == "no-boundary" and r.get("target_va"):
            if int(r["target_va"], 16) in map_starts:
                bad.append("%s names the holder of its fragment at %s, which is a map row of its own"
                           % (r["row_va"], r["target_va"]))
    if counts.get("short", 0) != int(cen.get("short") or 0):
        bad.append("census says %s short rows, %d rows carry that verdict"
                   % (cen.get("short"), counts.get("short", 0)))
    for k in CENSUS:
        if k.startswith("refused_") and int(cen.get(k) or 0) > int(cen.get("refused", 0) or 0):
            bad.append("%s exceeds the refused population" % k)
    if bad:
        for b in bad[:8]:
            print("FAIL symbol-extents: " + b)
        return 1
    print("ok   symbol-extents: %d rows, %d continuation(s) (%d head, %d tail, %d cut, %d pad/jump, %d "
          "padding, %d behind, %d follow, %d epilogue), %d byte(s), %d bounded, %d fold(s)/%d folded, "
          "every repair applied" % (
              len(rows), counts.get("continuation", 0), cen.get("head_family", 0),
              cen.get("tail_family", 0), cen.get("complete_family", 0),
              int(cen.get("pad_family") or 0) + int(cen.get("jump_family") or 0),
              cen.get("padding_family", 0), cen.get("behind_family", 0), cen.get("follow_family", 0),
              cen.get("epilogue_family", 0),
              cen.get("continuation_bytes", 0), cen.get("bounded", 0),
              counts.get("interior", 0), counts.get("folded", 0)))
    print("ok   symbol-extents: %d of the cut repairs were invisible to the truncated decode (re-read)"
          % int(cen.get("reread") or 0))
    return 0


def self_check():
    bad = []
    rows = [{"va": 0x2000, "name": "FUN_00002000", "size": 0x20},   # closes on `ret`
            {"va": 0x2020, "name": "FUN_00002020", "size": 6},      # abuts: a split
            {"va": 0x2026, "name": "FUN_00002026", "size": 8},      # the row the split hands over to
            {"va": 0x3000, "name": "FUN_00003000", "size": 0x10},   # a head at its end
            {"va": 0x3100, "name": "FUN_00003100", "size": 0x10},   # a tail that closes into fill
            {"va": 0x3200, "name": "FUN_00003200", "size": 0x10},   # a tail call, fill behind it
            {"va": 0x3300, "name": "FUN_00003300", "size": 0x10},   # a `jmp` with code behind it
            {"va": 0x3400, "name": "FUN_00003400", "size": 0x10},   # a `ret` with a pad behind it
            {"va": 0x3500, "name": "FUN_00003500", "size": 0x10},   # a pad at the row's own end
            {"va": 0x3600, "name": "FUN_00003600", "size": 0x10},   # a `jmp` with code it cannot place
            {"va": 0x3700, "name": "FUN_00003700", "size": 0x10},   # a `jmp` whose target is inside
            {"va": 0x5000, "name": "FUN_00005000", "size": 3},      # the size cuts an instruction
            {"va": 0x5100, "name": "FUN_00005100", "size": 2},      # ... and the cut instruction is a `ret`
            {"va": 0x4000, "name": "FUN_00004000", "size": 0x10},   # ends on live code, nothing after
            {"va": 0x5500, "name": "FUN_00005500", "size": 0x10},   # a size that runs into the next row
            {"va": 0x5508, "name": "FUN_00005508", "size": 0x10},   # the row it runs into
            {"va": 0x3800, "name": "FUN_00003800", "size": 0x10}]   # an early return into its own epilogue
    heads = [{"va": 0x3010, "size": 3, "boundary": "start", "verdict": "island",
              "prologue": "bare-ret", "tail": "pad"}]
    im = bytearray(b"\xcc" * (0x6000 - 0x2000))

    def put(va, data):
        im[va - 0x2000:va - 0x2000 + len(data)] = data

    put(0x2000, b"\x90" * 0x1f + b"\xc3")
    put(0x2020, b"\x90" * 6)
    put(0x2026, b"\x90" * 7 + b"\xc3")
    put(0x3000, b"\x90" * 0x0f + b"\x5d")
    put(0x3010, b"\x90" * 2 + b"\xc3")
    put(0x3100, b"\x90" * 0x0f + b"\x5d")
    put(0x3110, b"\x90" * 3 + b"\xc3")
    put(0x3200, b"\x90" * 0x0f + b"\x5d")
    put(0x3210, b"\xe9" + b"\x00" * 4)
    put(0x3300, b"\x90" * 0x0f + b"\x5d")
    put(0x3310, b"\xe9" + b"\x00" * 4 + b"\x55\x8b")
    put(0x3600, b"\x90" * 0x0f + b"\x5d")
    put(0x3610, b"\xe9" + b"\x00" * 4 + b"\x8b\x06")
    put(0x3700, b"\x90" * 0x0f + b"\x5d")
    put(0x3710, b"\xe9" + b"\x01" * 4 + b"\xc3")
    put(0x3400, b"\x90" * 0x0f + b"\x5d")
    put(0x3410, b"\xc3" + b"\xcc" * 2 + b"\x55\x8b")
    put(0x3500, b"\x90" * 0x0f + b"\x5d")
    put(0x5000, b"\x90" + b"\x05" * 5 + b"\xc3" + b"\x90" * 8)
    put(0x5100, b"\x90" + b"\xc2\x08\x00" + b"\x90" * 6)
    put(0x4000, b"\x90" * 0x0f + b"\x5d")
    put(0x5500, b"\x90" * 6 + b"\xc3" + b"\xcc" * 9)
    # a row that builds a frame (`push ebx`, `sub esp,0x10`), ends on a non-terminator, and is followed
    # by an early `ret` into the body's own epilogue (`pop ebx`, `add esp,0x10`, `ret`)
    put(0x3800, b"\x53" + b"\x81\xec\x10\x00\x00\x00" + b"\x90" * 9)
    put(0x3810, b"\xc3" + b"\x5b" + b"\x83\xc4\x10" + b"\xc3" + b"\xcc" * 8)

    def reader(va, size):
        return bytes(im[va - 0x2000:va - 0x2000 + size]) if va >= 0x2000 else b""
    rows.sort(key=lambda r: r["va"])

    def fake(blob, base):
        if not blob:
            return []
        if base == 0x2020:
            return [(0x2025, 1, "nop")]
        if base == 0x3210:
            return [(0x3210, 5, "jmp    0x0"), (0x3215, 1, "ret")]
        if base == 0x3310:
            return [(0x3310, 5, "jmp    0x0"), (0x3315, 2, "push   ebp")]
        if base == 0x3315:
            return [(0x3315, 1, "push   ebp"), (0x3316, 2, "mov    ebp,esp")]
        if base == 0x3610:
            return [(0x3610, 5, "jmp    0x0"), (0x3615, 2, "mov    eax,DWORD PTR [esi]")]
        if base == 0x3615:
            return [(0x3615, 2, "mov    eax,DWORD PTR [esi]")]
        if base == 0x3710:
            return [(0x3710, 5, "jmp    0x3716")]
        if base == 0x3715:
            return [(0x3715, 1, "ret")]
        if base == 0x3410:
            return [(0x3410, 1, "ret")]
        if base == 0x3510:
            return [(0x3510, 1, "int3")]
        if base == 0x5000:
            # a five-byte instruction that the row's size cuts in half: the short read cannot decode it
            return ([(0x5000, 1, "nop")] if len(blob) < 6
                    else [(0x5000, 1, "nop"), (0x5001, 5, "add    eax,0x0")])
        if base == 0x5006:
            return [(0x5006, 1, "ret")]
        if base == 0x5100:
            return ([(0x5100, 1, "nop")] if len(blob) < 4
                    else [(0x5100, 1, "nop"), (0x5101, 3, "ret    0x8")])
        if base == 0x3800:
            return [(0x3800, 1, "push   ebx"), (0x3801, 6, "sub    esp,0x10"), (0x3807, 9, "nop")]
        if base == 0x3810:
            return [(0x3810, 1, "ret"), (0x3811, 1, "pop    ebx"), (0x3812, 3, "add    esp,0x10"),
                    (0x3815, 1, "ret")]
        if base == 0x3811:
            # the run behind the early return: the body's own epilogue
            return [(0x3811, 1, "pop    ebx"), (0x3812, 3, "add    esp,0x10"), (0x3815, 1, "ret"),
                    (0x3816, 8, "nop")]
        out = [(base, 1, "nop")]
        for i in range(0, len(blob)):
            if blob[i] == 0xC3:
                out.append((base + i, 1, "ret"))
                break
            if blob[i] == 0xE9:
                out.append((base + i, 5, "jmp    0x0"))
                break
        if out[-1][2] == "nop":
            out.append((base + len(blob) - 1, 1, "pop    ebp"))
        return out

    out, cen = measure(rows, heads, reader, fake, text=(0x2000, 0x6000))
    verdicts = {r["row_va"]: r for r in out}
    if verdicts.get("0x00005500", {}).get("verdict") != "over" or \
            verdicts["0x00005500"]["new_size"] != 7 or verdicts["0x00005500"]["tail_bytes"] != 9:
        bad.append("a row whose size runs into the next row is resized down to its own close")
    if verdicts.get("0x00005508", {}).get("verdict") == "over":
        bad.append("the row the over-long row runs into is not itself resized")
    if verdicts.get("0x00003000", {}).get("verdict") != "continuation" or \
            verdicts["0x00003000"]["reason"] != "head":
        bad.append("a row with a head at its end is a head continuation")
    if verdicts.get("0x00003000", {}).get("new_size") != 0x13:
        bad.append("the head continuation's new size is row + head")
    if verdicts.get("0x00003800", {}).get("reason") != "epilogue" or \
            verdicts["0x00003800"]["tail_bytes"] != 6 or \
            verdicts["0x00003800"]["new_size"] != 0x16:
        bad.append("an early return into the row's own epilogue is read past the epilogue")
    if int(cen.get("epilogue_family") or 0) != 1 or int(cen.get("epilogue_bytes") or 0) != 6:
        bad.append("the epilogue family counts the row and its bytes")
    if verdicts.get("0x00003100", {}).get("reason") != "tail" or \
            verdicts["0x00003100"]["tail_bytes"] != 4:
        bad.append("a row whose own decode closes into fill is a tail continuation of 4 bytes")
    if verdicts.get("0x00003200", {}).get("reason") != "jump" or \
            verdicts["0x00003200"]["verdict"] != "continuation":
        bad.append("a `jmp` with only fill behind it is a tail-call continuation")
    if verdicts.get("0x00003300", {}).get("reason") != "jump-behind" or \
            verdicts["0x00003300"]["verdict"] != "continuation":
        bad.append("a `jmp` whose target is elsewhere with a body's own opening behind it is a tail call")
    if verdicts.get("0x00003600", {}).get("reason") != "jump" or \
            verdicts["0x00003600"]["verdict"] != "refused":
        bad.append("a `jmp` with code behind it that neither pads nor opens a body refuses the read")
    if verdicts.get("0x00003700", {}).get("reason") != "jump-inside" or \
            verdicts["0x00003700"]["new_size"] != 0x16:
        bad.append("a `jmp` whose target is inside the row's own gap is a branch: the read runs on to "
                   "the close behind it (got %s %s)" % (verdicts.get("0x00003700", {}).get("reason"),
                                                        verdicts.get("0x00003700", {}).get("new_size")))
    if verdicts.get("0x00003400", {}).get("reason") != "pad" or \
            verdicts["0x00003400"]["verdict"] != "continuation":
        bad.append("a `ret` with a two-byte pad behind it is a pad continuation")
    if verdicts.get("0x00003500", {}).get("verdict") != "bounded":
        bad.append("a pad at the row's own end bounds the body and needs no resize")
    if verdicts.get("0x00005000", {}).get("reason") != "complete-fill" or \
            verdicts["0x00005000"]["new_size"] != 7:
        bad.append("a row whose size cuts an instruction is repaired to where the run behind it closes")
    if verdicts.get("0x00005100", {}).get("reason") != "complete-term" or \
            verdicts["0x00005100"]["new_size"] != 4:
        bad.append("a row whose cut instruction is itself a terminator is repaired to its end")
    if verdicts.get("0x00002020", {}).get("verdict") != "split":
        bad.append("a row whose end is the next row's start is a split")
    if verdicts.get("0x00004000", {}).get("verdict") != "refused":
        bad.append("a row with no close behind it is refused")
    if cen.get("terminator") != 2:
        bad.append("the two rows that close on `ret` must not enter the population")
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "symbol_extents.csv")
        write_artifact(out, cen, path)
        back, cen2 = read_artifact(path)
        if len(back) != len(out) or cen2 != {k: int(cen.get(k, 0)) for k in CENSUS}:
            bad.append("the artifact round trip loses a row or a census key")
        held = [r for r in out if r["row_va"] == "0x00003000"][0]
        carried, cen3 = measure([{"va": 0x3000, "name": "FUN_00003000", "size": 0x13}], [],
                                reader, fake, known={"0x00003000": held}, text=(0, 0x9000))
        if not carried or carried[0]["verdict"] != "continuation" or cen3.get("continuation") != 1:
            bad.append("a repaired row is carried, not re-measured")
        if cen3.get("head_family") != 1 or cen3.get("head_bytes") != 3:
            bad.append("a carried row restores its family's row and byte totals")
        cut = measure([{"va": 0x5000, "name": "FUN_00005000", "size": 7}], [], reader, fake,
                      known={r["row_va"]: r for r in out}, text=(0x4000, 0x6000))
        if cut[0] and [r for r in cut[0] if r["row_va"] == "0x00005000"][0]["reason"] != "complete-fill":
            bad.append("a carried cut repair keeps its family")
        if set(cen) - set(CENSUS):
            bad.append("census holds keys the header does not write: %s" % ", ".join(sorted(set(cen) - set(CENSUS))))
        if not render(back, cen2):
            bad.append("render returns nothing")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # --- the three closes behind a `ret`, the fold, and a head shorter than its own instruction ------
    im2 = bytearray(b"\xcc" * 0x1000)

    def put2(va, data):
        im2[va - 0x10000:va - 0x10000 + len(data)] = data

    def r2(va, size):
        o = va - 0x10000
        return bytes(im2[o:o + size]) if 0 <= o < len(im2) else b""

    DEC = {
        0x10100: [(0x10100, 1, "ret")],
        0x10101: [(0x10101, 3, "lea    ecx,[ecx+0x0]")],
        0x10200: [(0x10200, 1, "ret")],
        0x10201: [(0x10201, 3, "sub    esp,0xf0")],
        0x10300: [(0x10300, 1, "ret")],
        0x10301: [(0x10301, 3, "mov    ecx,DWORD PTR [ebp-0xc]"), (0x10304, 1, "pop    edi"),
                  (0x10305, 1, "ret")],
        0x10400: [(0x10400, 1, "push   ebp"), (0x10401, 2, "mov    ebp,esp"),
                  (0x10403, 3, "mov    eax,DWORD PTR [ebp+0x8]")],
        0x10406: [(0x10406, 1, "pop    ebp"), (0x10407, 1, "ret")],
        0x10502: [(0x10502, 1, "ret")],
        0x10600: [(0x10600, 1, "push   ebp"), (0x10601, 2, "mov    ebp,esp"),
                  (0x10603, 3, "mov    eax,DWORD PTR [ebp+0x8]")],
        0x10606: [(0x10606, 1, "push   esi"), (0x10607, 3, "mov    eax,DWORD PTR [ebp-0x8]")],
        0x1060a: [(0x1060a, 1, "ret")],
        0x10700: [(0x10700, 1, "push   ebp"), (0x10701, 2, "mov    ebp,esp"),
                  (0x10703, 3, "mov    eax,DWORD PTR [ebp-0x4]")],
        0x10706: [(0x10706, 1, "push   esi"), (0x10707, 2, "mov    ebp,esp")],
        0x10709: [(0x10709, 1, "ret")],
        0x10a00: [(0x10a00, 7, "mov    BYTE PTR ds:0x1,0x0")],
        0x10a07: [(0x10a07, 1, "pop    ebp"), (0x10a08, 1, "ret")],
        0x10c01: [(0x10c01, 1, "inc    ebp")],
        0x10c02: [(0x10c02, 1, "ret")],
    }

    def d2(blob, base):
        if base == 0x10500:
            return [] if len(blob) < 2 else [(0x10500, 2, "mov    eax,DWORD PTR [eax]")]
        return DEC.get(base, [])

    put2(0x10101, b"\x8d\x49\x00\x90")
    put2(0x10201, b"\x83\xec\xf0")
    put2(0x10301, b"\x8b\x4d\xf4\x5f\xc3")
    put2(0x10400, b"\x55\x8b\xec\x8b\x45\x08\x5d\xc3")
    put2(0x10500, b"\x8b\x00\xc3")
    put2(0x10600, b"\x55\x8b\xec\x8b\x45\x08")           # a frame head, split at 0x10606
    put2(0x10606, b"\x56\x8b\x45\xf8")                     # the fragment reads [ebp] before writing it
    put2(0x10700, b"\x55\x8b\xec\x8b\x4d\xfc")           # a frame head, split at 0x10706
    put2(0x10706, b"\x56\x8b\xec")                          # ... whose fragment builds its own frame
    put2(0x10a00, b"\xc6\x05\x01\x00\x00\x00\x00")        # a head no prologue matches ...
    put2(0x10a07, b"\x5d\xc3")                               # ... split at 0x10a07
    put2(0x10c00, b"\x45\x45")                               # a row whose head is inside an instruction
    put2(0x10b00, b"\xe8\x01\xfc\xff\xff")                # call 0x10706: the fragment is a body
    put2(0x10b08, b"\xe8\xf3\xfe\xff\xff")                # call 0x10a00: the head above is called
    _t, c1, w1 = tail_rule(r2, d2, 0x10100, 0x10110)
    if w1 != "padding":
        bad.append("a close followed by alignment filler is a `padding` close (got %s)" % w1)
    _t, c2, w2 = tail_rule(r2, d2, 0x10200, 0x10210)
    if w2 != "behind" or c2 is None or c2[0] != 0x10200:
        bad.append("a close followed by a body's own opening is a `behind` close (got %s)" % w2)
    _t, c3, w3 = tail_rule(r2, d2, 0x10300, 0x10310)
    if w3 != "follow" or c3 is None or c3[0] != 0x10305:
        bad.append("a close followed by the same frame's epilogue runs on to the next close (got %s %s)"
                   % (w3, c3))
    fold_rows = [{"va": 0x10400, "name": "FUN_00010400", "size": 6},
                 {"va": 0x10406, "name": "FUN_00010406", "size": 4},
                 {"va": 0x10500, "name": "FUN_00010500", "size": 1},
                 {"va": 0x10600, "name": "FUN_00010600", "size": 6},
                 {"va": 0x10606, "name": "FUN_00010606", "size": 4},
                 {"va": 0x10700, "name": "FUN_00010700", "size": 6},
                 {"va": 0x10706, "name": "FUN_00010706", "size": 3},
                 {"va": 0x10a00, "name": "FUN_00010a00", "size": 7},
                 {"va": 0x10a07, "name": "FUN_00010a07", "size": 2},
                 {"va": 0x10c01, "name": "FUN_00010c01", "size": 1},
                 {"va": 0x10c02, "name": "FUN_00010c02", "size": 1}]
    fout, fcen = measure(fold_rows, [], r2, d2, text=(0x10000, 0x11000))
    fv = {r["row_va"]: r for r in fout}
    if fv.get("0x00010400", {}).get("verdict") != "interior" or \
            fv["0x00010400"]["new_size"] != 8:
        bad.append("a split whose head is a frame reads forward to its close and folds (got %s)"
                   % fv.get("0x00010400", {}).get("verdict"))
    if fv.get("0x00010406", {}).get("verdict") != "folded" or \
            fv["0x00010406"]["target_va"] != "0x00010400":
        bad.append("a row inside a fold is `folded` into the row above")
    if fv.get("0x00010500", {}).get("verdict") != "continuation" or fcen.get("undecoded_cut") != 1:
        bad.append("a row shorter than its own first instruction is read across the cut (got %s)"
                   % fv.get("0x00010500", {}).get("verdict"))
    if fv.get("0x00010600", {}).get("verdict") != "interior" or \
            fv.get("0x00010606", {}).get("verdict") != "folded" or \
            fv["0x00010600"]["new_size"] != 11:
        bad.append("a fragment that reads [ebp] before writing it is continuation code, so the fold "
                   "goes ahead (got %s / %s)" % (fv.get("0x00010600", {}).get("verdict"),
                                                 fv.get("0x00010606", {}).get("verdict")))
    if fv.get("0x00010700", {}).get("verdict") != "split" or \
            "body-inside" not in (fv.get("0x00010700", {}).get("note") or ""):
        bad.append("a fragment that is called from elsewhere is a body, so the fold refuses (got %s)"
                   % fv.get("0x00010700", {}).get("verdict"))
    if fv.get("0x00010a00", {}).get("verdict") != "interior" or \
            fv["0x00010a00"]["new_size"] != 9 or fv.get("0x00010a07", {}).get("verdict") != "folded":
        bad.append("a head no prologue matches is a body start when a `call` lands on it, and its "
                   "interior row drops (got %s / %s)" % (fv.get("0x00010a00", {}).get("verdict"),
                                                        fv.get("0x00010a07", {}).get("verdict")))
    if fv.get("0x00010c01", {}).get("reason") != "no-boundary":
        bad.append("a row whose head is inside another instruction is named `no-boundary` (got %s)"
                   % fv.get("0x00010c01", {}).get("reason"))
    tmp2 = tempfile.mkdtemp()
    try:
        p2 = os.path.join(tmp2, "symbol_extents.csv")
        write_artifact(fout, fcen, p2)
        again, cen4 = measure([fold_rows[0], fold_rows[2]], [], r2, d2,
                              known={r["row_va"]: r for r in fout}, text=(0x10000, 0x11000))
        carried = {r["row_va"]: r for r in again if r["verdict"] == "folded"}
        if "0x00010406" not in carried or carried["0x00010406"]["target_va"] != "0x00010400" or \
                cen4.get("split_folded", 0) < 1:
            bad.append("a dropped row's record is carried from the artifact")
        carried_fold, cen5 = measure(
            [{"va": 0x10400, "name": "FUN_00010400", "size": 8},
             {"va": 0x10406, "name": "FUN_00010406", "size": 4}],
            [], r2, d2, known={r["row_va"]: r for r in fout}, text=(0x10000, 0x11000))
        if cen5.get("split_fold") != 1 or "fold_family" in cen5 or \
                [r["verdict"] for r in carried_fold if r["row_va"] == "0x00010400"] != ["interior"]:
            bad.append("a carried fold keeps its `interior` record instead of counting a family")
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)
    # --- the two readings a `ret` needs beyond frame/pad/opening: a filler no-op, a whole body, data --
    put2(0x10804, b"\x89\xd8\xc3\x8b\xf6\xcc\xcc")              # ret, then `mov esi,esi` (2-byte nop)
    put2(0x10904, b"\x89\xd8\xc3\x05\x00\x00\x00\x00\xcc\xcc")  # ret, then `add eax,0x0` (5-byte nop)
    put2(0x10a04, b"\x89\xd8\xc3\x33\xc9\xc3\xcc\xcc")          # ret, then a two-instruction body
    put2(0x10b04, b"\x89\xd8\xc3\xec\xc3\xcc\xcc")              # ret, then `in al,dx`: not code at all
    put2(0x10c04, b"\x75\x01\xc3\x33\xc9\xc3\xcc\xcc")          # `jne` over the ret: the row's own code
    d3 = {
        0x10800: [(0x10800, 4, "mov    eax,edx")],
        0x10804: [(0x10804, 2, "mov    eax,ebx"), (0x10806, 1, "ret")],
        0x10807: [(0x10807, 2, "mov    esi,esi")],
        0x10900: [(0x10900, 4, "mov    eax,edx")],
        0x10904: [(0x10904, 2, "mov    eax,ebx"), (0x10906, 1, "ret")],
        0x10907: [(0x10907, 5, "add    eax,0x0")],
        0x10a00: [(0x10a00, 4, "mov    eax,edx")],
        0x10a04: [(0x10a04, 2, "mov    eax,ebx"), (0x10a06, 1, "ret")],
        0x10a07: [(0x10a07, 2, "xor    ecx,ecx"), (0x10a09, 1, "ret")],
        0x10b00: [(0x10b00, 4, "mov    eax,edx")],
        0x10b04: [(0x10b04, 2, "mov    eax,ebx"), (0x10b06, 1, "ret")],
        0x10b07: [(0x10b07, 1, "in     al,dx"), (0x10b08, 1, "ret")],
        0x10c00: [(0x10c00, 4, "mov    eax,edx")],
        0x10c04: [(0x10c04, 2, "jne    0x10c07"), (0x10c06, 1, "ret")],
        0x10c07: [(0x10c07, 2, "xor    ecx,ecx"), (0x10c09, 1, "ret")],
    }

    def d3f(blob, base):
        return d3.get(base, [])

    ret_rows = [{"va": 0x10800, "name": "FUN_00010800", "size": 4},
                {"va": 0x10820, "name": "FUN_00010820", "size": 1},
                {"va": 0x10900, "name": "FUN_00010900", "size": 4},
                {"va": 0x10920, "name": "FUN_00010920", "size": 1},
                {"va": 0x10a00, "name": "FUN_00010a00", "size": 4},
                {"va": 0x10a20, "name": "FUN_00010a20", "size": 1},
                {"va": 0x10b00, "name": "FUN_00010b00", "size": 4},
                {"va": 0x10b20, "name": "FUN_00010b20", "size": 1},
                {"va": 0x10c00, "name": "FUN_00010c00", "size": 4},
                {"va": 0x10c20, "name": "FUN_00010c20", "size": 1}]
    for va in (0x10820, 0x10920, 0x10a20, 0x10b20, 0x10c20):
        d3[va] = [(va, 1, "ret")]
    rout, rcen = measure(ret_rows, [], r2, d3f, text=(0x10000, 0x11000))
    rv = {r["row_va"]: r for r in rout}
    for va, want in (("0x00010800", "padding"), ("0x00010900", "padding"),
                     ("0x00010a00", "body"), ("0x00010b00", "data")):
        got = rv.get(va, {}).get("reason")
        if rv.get(va, {}).get("verdict") != "continuation" or got != want:
            bad.append("a `ret` followed by %s reads as `%s`, not `%s` (got %s / %s)" % (
                {"padding": "a no-op filler", "body": "a whole body",
                 "data": "bytes no ring-3 program can run"}[want], want, got,
                rv.get(va, {}).get("verdict"), got))
    if rcen.get("body_family") != 1 or rcen.get("data_family") != 1:
        bad.append("the new families are counted (body %s, data %s)"
                   % (rcen.get("body_family"), rcen.get("data_family")))
    if rv.get("0x00010c00", {}).get("verdict") != "refused" or \
            rv.get("0x00010c00", {}).get("reason") != "then-code":
        bad.append("a `ret` a branch of the row lands behind is the row's own code, not a close (got "
                   "%s / %s)" % (rv.get("0x00010c00", {}).get("verdict"),
                                 rv.get("0x00010c00", {}).get("reason")))
    # --- the cut the truncated decode hides, and a body behind the close longer than 64 bytes ----
    im6 = bytearray(b"\xcc" * (0x21000 - 0x20000))

    def put6(va, data):
        im6[va - 0x20000:va - 0x20000 + len(data)] = data

    put6(0x20000, b"\x90" + b"\x05\x00\x00\x00" + b"\xc3" + b"\xcc" * 3)   # `nop` + a cut `add`
    put6(0x20100, b"\x90" * 0x0f + b"\x5d" + b"\xc3")                        # close, then a body
    put6(0x20111, b"\x8b\xc3" * 0x20 + b"\xc3" + b"\xcc" * 8)                 # ... 64 bytes behind it

    def reader6(va, size):
        return bytes(im6[va - 0x20000:va - 0x20000 + size]) if va >= 0x20000 else b""

    d6 = {
        # the row's size cuts `add eax,0x0` (5 bytes): the short read re-splits it and never sees the cut
        0x20000: lambda n: ([(0x20000, 1, "nop"), (0x20001, 4, "(bad)")] if n < 6
                            else [(0x20000, 1, "nop"), (0x20001, 5, "add    eax,0x0")]),
        0x20006: lambda n: [(0x20006, 1, "ret")],
        0x20100: lambda n: [(0x20100, 0x0f, "nop"), (0x2010f, 1, "pop    ebp")],
        0x20110: lambda n: [(0x20110, 1, "ret")],
        0x20111: lambda n: ([(0x20111, 0x40, "mov    eax,ebx")] if n <= 0x40
                            else [(0x20111, 0x40, "mov    eax,ebx"), (0x20151, 1, "ret")]),
    }

    def d6f(blob, base):
        fn = d6.get(base)
        return fn(len(blob)) if fn else []

    six_rows = [{"va": 0x20000, "name": "FUN_00020000", "size": 5},
                {"va": 0x20010, "name": "FUN_00020010", "size": 1},
                {"va": 0x20100, "name": "FUN_00020100", "size": 0x10},
                {"va": 0x20180, "name": "FUN_00020180", "size": 1}]
    s6out, s6cen = measure(six_rows, [], reader6, d6f, text=(0x20000, 0x21000))
    s6 = {r["row_va"]: r for r in s6out}
    if s6.get("0x00020000", {}).get("verdict") != "continuation" or \
            s6["0x00020000"]["reason"] != "complete-fill" or \
            s6["0x00020000"]["new_size"] != 7 or s6cen.get("reread") != 1:
        bad.append("a cut the truncated decode hides is read again over one more instruction (got %s / "
                   "%s / %s / reread %s)" % (s6.get("0x00020000", {}).get("verdict"),
                                             s6.get("0x00020000", {}).get("reason"),
                                             s6.get("0x00020000", {}).get("new_size"),
                                             s6cen.get("reread")))
    if s6.get("0x00020100", {}).get("verdict") != "continuation" or \
            s6["0x00020100"]["reason"] != "body":
        bad.append("a body that closes more than 64 bytes behind the close still bounds the row (got "
                   "%s / %s)" % (s6.get("0x00020100", {}).get("verdict"),
                                 s6.get("0x00020100", {}).get("reason")))
    if bad:
        for b in bad:
            print("FAIL selftest: " + b)
        return 1
    print("ok   symbol-extents selftest: both tail families, the cut repairs, the pad and `jmp` closes "
          "(a tail call, a branch inside the gap, and a refusal), the `bounded` row, the split, the "
          "refusals, the three closes behind a `ret` (a filler no-op, a whole body, and bytes no ring-3 "
          "program can run), the fold and its frame test, the undecoded cut, the row whose size runs "
          "into the next one, the cut the truncated decode hides, a body that closes past 64 bytes, "
          "the carry and both round trips")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xbe", help="default.xbe (measure needs it)")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--write-boundaries", action="store_true")
    ap.add_argument("--write-overrides", action="store_true")
    ap.add_argument("--write-doc", metavar="PAGE")
    ap.add_argument("--check-doc", metavar="PAGE")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(self_check())
    if a.check:
        sys.exit(check())
    if a.report:
        rows, cen = read_artifact()
        print(render(rows, cen))
        return
    if a.write_boundaries:
        rows, cen = read_artifact()
        write_boundaries(rows)
        return
    if a.write_overrides:
        rows, cen = read_artifact()
        write_overrides(rows)
        return
    if a.check_doc:
        rows, cen = read_artifact()
        new = splice_doc(a.check_doc, render(rows, cen), DOC_MARKS)
        if new is None:
            print("FAIL symbol-extents: markers not found in %s" % a.check_doc)
            sys.exit(1)
        if new != open(a.check_doc, encoding="utf-8").read():
            print("FAIL symbol-extents: %s is stale" % a.check_doc)
            sys.exit(1)
        print("ok   symbol-extents: %s matches the artifact" % a.check_doc)
        return
    if a.write_doc:
        rows, cen = read_artifact()
        new = splice_doc(a.write_doc, render(rows, cen), DOC_MARKS)
        if new is None:
            print("FAIL symbol-extents: markers not found in %s" % a.write_doc)
            sys.exit(1)
        with open(a.write_doc, "w", encoding="utf-8") as fh:
            fh.write(new)
        print("wrote %s" % a.write_doc)
        return
    if not a.xbe:
        ap.error("--xbe is required to measure (or use --check/--selftest/--report)")
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import xbe as xbemod
    image = xbemod.Xbe(a.xbe)
    text = None
    for s in image.sections:
        if s.name == ".text":
            text = (s.va, s.va + s.vsize)
    # the null population: fixed addresses in the data sections, same rule
    for s in image.sections:
        if s.name == ".text":
            continue
        va = s.va
        while va + 64 < s.va + s.fsize and len(null_addresses) < 3000:
            null_addresses.append(va)
            va += 2048
    rows, cen = measure(read_map(), read_heads(), image.read_va, known=_known(), text=text,
                        null_trials=len(null_addresses), progress=True)
    write_artifact(rows, cen, os.path.join(a.out, "symbol_extents.csv"))
    print("%d rows, %d continuation(s) (%d head, %d tail), %d byte(s); null %d/%d" % (
        len(rows), cen.get("continuation", 0), cen.get("head_family", 0), cen.get("tail_family", 0),
        cen.get("continuation_bytes", 0), cen.get("null_hits", 0), cen.get("null_trials", 0)))


def _known():
    rows, _ = read_artifact()
    return {r["row_va"]: r for r in rows
            if r.get("verdict") in ("continuation", "interior", "folded", "over")}


if __name__ == "__main__":
    main()
