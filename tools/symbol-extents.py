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
  (`0xCC`/`0x90`). A `jmp` before the `ret` refuses the read: a tail-call's target code is behind it, so
  the "close" would be a branch out of the middle of the run.

Both are resizes to `row size + tail`. Three more shapes close a row and are repaired by the same
rule: a **`short`** row (the size cuts an instruction in half -- a byte count, not a boundary) is read
across the cut, and the row extends to where the completed instruction lands when that is a
terminator (`complete-term`) or when the run behind it closes (`complete-fill`); a tail whose **first
terminator is a `jmp`** followed by fill or a pad is a tail call (`jump`); and a tail whose first
terminator is a `ret` followed by a **pad of two or more `0xcc`** is a close too (`pad`), because the
project's own rule is that only a run of two or more `int3` bounds a body. A row whose tail *begins*
with that pad is already bounded where the map says it ends (`bounded`, no repair).

Every other row that does not end on a terminator is counted and not repaired: `split` (the next map
row starts at the row's end -- Ghidra split one body in two, which is a `drop` repair and needs a
review this rule cannot supply), `refused` (a terminator with code behind it, or nothing to decode)
and `short` (still a byte count: nothing spans the cut, the completed instruction would cross the next
row, or the run behind it does not close).

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
          "behind_bytes", "follow_family", "follow_bytes", "short_overlap", "short_nostraddle",
          "short_refused", "short_split", "fold_trials", "fold_agree", "null_hits", "null_trials")
# the reason's first token is its family, so a carried row needs no second column
FAMILIES = ("head", "tail", "pad", "jump", "complete", "padding", "behind", "follow")

OBJ_LINE = re.compile(r"^\s*([0-9a-f]+):\t([0-9a-f ]+?)\s*\t(.*)$")
WRITES_EBP = re.compile(r"^(mov|lea|xor|add|sub|and|or|pop|imul|movzx|movsx|and)\s+ebp\b")
TERM = ("ret", "jmp", "int3", "hlt", "iret")
FILL = (0xCC, 0x90)
MAX_TAIL = 256
MAX_CUT = 16          # the longest x86 instruction: a cut cannot reach further
MAX_FOLD = 2048       # how far a fragment's body is followed to its close
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
           "shared epilogue")



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
                        "size": int(r.get("size") or 0), "evidence": (r.get("evidence") or "").strip()})
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


def tail_rule(reader, decode_fn, start, limit):
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
    for t in tail:
        if is_term(t[2]):
            first = t
            break
    if first is None:
        return tail, None, "no-close"
    why = close_kind(reader, decode_fn, first, limit)
    if why in ("tail", "jump", "pad"):
        return tail, first, why
    if why == "jump-open":
        return tail, None, "jump"
    if why in ("padding", "behind"):
        return tail, first, why
    if why == "follow":
        close, fwhy = close_ahead(reader, decode_fn, first[0] + first[1], limit)
        if close is not None and mnemonic(close[2]) != "jmp":
            return tail, close, "follow"
        return tail, None, "then-code"
    return tail, None, "then-code"


def close_kind(reader, decode_fn, step, limit):
    """How the bytes behind one terminator bound it: `tail`, `pad`, `padding`, `behind`, `follow`, or a
    refusal (`jump-open`, `then-code`)."""
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
        return "jump-open"
    kind, _first = after_kind(reader, decode_fn, after_va, limit)
    return kind


def after_kind(reader, decode_fn, va, limit):
    """(`padding` | `behind` | `follow` | `then-code`, first instruction) for the code at `va`.

    `padding` is alignment filler, `behind` a body's own opening, and `follow` a run that reads a frame
    (`[ebp...]`, `leave`) it never writes in its opening window -- the shared epilogue an early return
    jumps into. Anything else is code this rule cannot attribute."""
    ins = decode_fn(reader(va, min(limit - va, 24)), va)
    if not ins:
        return "then-code", None
    if is_fill_insn(ins[0][2]):
        return "padding", ins[0]
    if opens_body(reader(va, 8)):
        return "behind", ins[0]
    for _a, _l, t in ins[:4]:
        if WRITES_EBP.match(t) or mnemonic(t) == "leave":
            return "then-code", ins[0]
    for _a, _l, t in ins[:4]:
        if "[ebp" in t or mnemonic(t) == "leave":
            return "follow", ins[0]
    return "then-code", ins[0]


def opens_body(first):
    """Does this opening match the shared prologue table? `first` is the bytes at the address."""
    if not first:
        return False
    for pat, _name in PROLOGUE_PAT:
        if first.startswith(pat):
            return True
    return False


def is_fill_insn(text):
    """Alignment filler: `nop`, `int3`, or a `lea r,[r+0x0]` that writes nothing."""
    m = mnemonic(text)
    if m in ("nop", "int3"):
        return True
    if m == "lea":
        q = re.match(r"^lea\s+(\w+),\[\1\+0x0\]$", text.replace("  ", " "))
        return bool(q)
    return False


def close_ahead(reader, decode_fn, start, limit, cap=600):
    """(close instruction, kind) for the first close at or after `start`, following a shared epilogue.

    A close is a terminator with fill, a two-byte pad, alignment padding or a body's own opening behind
    it. When the code behind a close reads the same frame, the walk continues past it -- the early
    return's shared epilogue -- so the answer is the close that ends the body, not the first one it
    reaches. `None` when nothing closes before `limit`.
    """
    va = start
    steps = 0
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
                continue
            kind = close_kind(reader, decode_fn, step, limit)
            if kind in ("tail", "pad", "padding", "behind"):
                return step, kind
            if kind == "jump-open":
                return None, "jump"
            va = step[0] + step[1]        # a shared epilogue: keep walking
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


def fold_end(reader, decode_fn, start, size, limit, starts, names):
    """(close address, close instruction, kind, refusal) for a row the map split from its own body.

    The row's head is a real instruction boundary, so the body is read forward from there: the first
    close is where it ends. A map row that starts inside that span is a fragment of the same body unless
    its own head opens a body (the shared prologue table), and then the read is refused rather than
    swallowing a function.
    """
    close, kind = close_ahead(reader, decode_fn, start, limit)
    if close is None:
        return None, None, kind, kind
    cend = close[0] if mnemonic(close[2]) == "int3" else close[0] + close[1]
    if cend <= start + size:
        return None, close, kind, "no-wider"
    k = bisect.bisect_right(starts, start)
    while k < len(starts) and starts[k] < cend:
        if opens_body(reader(starts[k], 8)):
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

    def split_row(r, last, end_kind, cut=False):
        """A row whose end is the next row's start: is the next map row interior code of this one?

        A split is only read as a fold when this row's head is a body's own start -- a measured prologue
        shape, or a name the build gave it (an assert or a registration) rather than `FUN_*`/`thunk_*`
        -- and the body read forward from that head reaches a close that the next row starts inside.
        Every map row inside the repaired span becomes a `drop`."""
        cen["nonterm"] += 1
        cen["split"] += 1
        if cut:
            cen["short_split"] += 1
        end = r["va"] + r["size"]
        note = ("the completed instruction ends where the next map row starts" if cut
                else "the next map row starts at this row's end")
        named = bool(r["name"]) and not r["name"].startswith(("FUN_", "thunk_"))
        if not (named or opens_body(reader(r["va"], 8))):
            out.append(_row(r, last, end_kind, 0, "", "split", "next-row",
                            note + ", so the map split one body in two; this row's head is neither a "
                            "measured prologue nor a name the build gave it, so the split is left to a "
                            "review that this rule cannot supply"))
            return
        cend, close, kind, why = fold_end(reader, decode_fn, r["va"], r["size"],
                                          min(end + MAX_FOLD, hi), starts, None)
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
            tail, close, why = tail_rule(reader, decode_fn, cend, nxt if nxt else cend + MAX_TAIL)
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
            tail, close, why = tail_rule(reader, decode_fn, end, nxt if nxt else end + MAX_TAIL)
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
                        "follow": "a decode from the row's end reaches `%s`, and the code behind it reads "
                                  "the same frame -- the shared epilogue an early return jumps into -- so "
                                  "the body runs on to the close at 0x%08x",
                    }[why]
                    note = note % ((close[2], cclose) if why == "follow" else (close[2],))
                    continuation(r, last, "tail:%s" % why, tail_bytes, "0x%08x" % cclose, why, note,
                                 cclose - r["va"], close=close)
            else:
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
        "(**%d**, %d bytes). The first terminator closes the run when only fill follows it, when it is a "
        "`jmp` -- a tail call, its target code elsewhere -- or when it is a `ret` with a pad of two or "
        "`CC` behind it (**%d rows, %d bytes**), because only a run of two or more `int3` bounds a "
        "body. The code behind a close is not the body's own code, so a close with code behind it is "
        "read three ways: **%d rows** end where the next instructions are alignment padding, **%d** "
        "where they open a body of their own, and **%d** where they read the same frame -- the shared "
        "epilogue an early return jumps into -- and the body runs on to the next close (**%d bytes**). "
        "Together **%d rows and %d bytes**: %d of them grow by one instruction or less (a row that "
        "stops on `pop ebp`/`add esp,N` before its own `ret`), and the largest is `%s`, a %s-byte row "
        "continued by %s bytes." % (
        n("head_family"), n("head_bytes"), n("tail_family"), n("tail_bytes_total"),
        len(cut_rows), n("complete_term"), n("complete_fill"), fill_bytes,
        n("pad_family") + n("jump_family"), n("pad_bytes") + n("jump_bytes"),
        n("padding_family"), n("behind_family"), n("follow_family"),
        n("padding_bytes") + n("behind_bytes") + n("follow_bytes"),
        n("continuation"), n("continuation_bytes"),
        sum(1 for r in cont if int(r["tail_bytes"] or 0) <= 4),
        (max(cont, key=lambda r: int(r["tail_bytes"] or 0))["row_va"] if cont else "-"),
        (max(cont, key=lambda r: int(r["tail_bytes"] or 0))["row_size"] if cont else "-"),
        (max(cont, key=lambda r: int(r["tail_bytes"] or 0))["tail_bytes"] if cont else "-")))
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
            "all, **%d** no byte behind them), and **%d** are a body the map already split from its own "
            "continuation -- %s -- which is a `drop` repair and needs a review this rule cannot supply." % (
                len(short), n("short_nostraddle"), n("short_overlap"), n("short_refused"),
                n("short_split"), n("undecoded"), n("bounded"), n("refused"), n("refused_jump"),
                n("refused_then_code"), n("refused_no_close"),
                n("refused_undecoded") + n("refused_no_room"),
                len(splits), ", ".join("`%s`" % r["row_va"] for r in splits) or "none"))
        lines.append("")
    if folds or folded:
        lines.append(
            "**The splits this rule does repair are folds.** %d rows the map split from their own "
            "continuation read forward from their **own head** -- a measured prologue, or a name the "
            "build gave them -- to the close that ends the body (**%d bytes** past the row's end), and "
            "every map row that starts inside that span is interior code of it: **%d rows** drop into "
            "the row above, each a `drop` row in `symbols/xbe_boundaries.csv` beside the fold's "
            "`resize`. A split whose head is neither a prologue nor a name (`FUN_*`) is left alone: "
            "reading it forward would be a guess about where a body began, not a measurement. **The "
            "control is the same read on rows that already close**: from its head, **%d of the %d "
            "rows sampled** land on the map's own end (%.1f%%), so the read reproduces a size it was "
            "not given." % (
            len(folds), n("fold_bytes"), len(folded), n("fold_agree"), n("fold_trials"),
            pct(n("fold_agree"), n("fold_trials"))))
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
    if r["reason"] in ("padding", "behind", "follow"):
        return ("%s; `%s` at %s is the first terminator behind the row's end and the bytes behind it are "
                "%s, so the body runs to %s" % (
                    REPAIRS[4], r["close_insn"], r["close_va"],
                    {"padding": "alignment filler", "behind": "a body's own opening",
                     "follow": "the same frame's shared epilogue"}[r["reason"]], r["new_size"]))
    if r["verdict"] == "interior":
        return ("%s; the row's head is a body's own start and the body read forward from it closes on `%s` "
                "at %s with %s behind it, so the %s bytes to that close are one body and the map rows "
                "inside it are interior code" % (
                    REPAIRS[4], r["close_insn"], r["close_va"], r["reason"], r["tail_bytes"]))
    return ("%s; `%s` closes the run %s bytes past the row's end and only fill follows before the next "
            "row" % (REPAIRS[1], r["close_insn"], r["tail_bytes"]))


def write_boundaries(rows, path=BOUNDARIES):
    """Append one `resize` row per continuation and per fold, and one `drop` per folded row."""
    with open(path, newline="", encoding="utf-8") as fh:
        text = fh.read()
    have = {b["address"]: b for b in read_boundaries(path)}
    added, kept, bad = 0, 0, []
    lines = []
    for r in rows:
        if r["verdict"] == "folded":
            va, target = int(r["row_va"], 16), int(r["target_va"], 16)
            b = have.get(va)
            if b:
                if b["action"] != "drop":
                    bad.append("%s already carries a %s" % (r["row_va"], b["action"]))
                else:
                    kept += 1
                continue
            lines.append('0x%08x,drop,0x%08x,,"%s"' % (
                va, target, ("the body at 0x%08x reads forward from its own head to a close this row "
                             "starts inside, so this row is interior code of it" % target)))
            added += 1
            continue
        if r["verdict"] not in ("continuation", "interior"):
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
           if r["verdict"] in ("continuation", "interior")}
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
        if r["verdict"] in ("continuation", "interior"):
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
    if int(cen.get("continuation") or 0) + int(cen.get("split") or 0) + int(cen.get("refused") or 0) + \
            int(cen.get("bounded") or 0) != int(cen.get("nonterm") or 0):
        bad.append("continuation + split + refused + bounded does not equal the non-terminating rows")
    if int(cen.get("short_nostraddle") or 0) + int(cen.get("short_overlap") or 0) + \
            int(cen.get("short_refused") or 0) > int(cen.get("short") or 0):
        bad.append("the short sub-families exceed the short population")
    if int(cen.get("short_split") or 0) > int(cen.get("split") or 0):
        bad.append("the cut splits exceed the split population")
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
          "padding, %d behind, %d follow), %d byte(s), %d bounded, %d fold(s)/%d folded, every repair "
          "applied" % (
              len(rows), counts.get("continuation", 0), cen.get("head_family", 0),
              cen.get("tail_family", 0), cen.get("complete_family", 0),
              int(cen.get("pad_family") or 0) + int(cen.get("jump_family") or 0),
              cen.get("padding_family", 0), cen.get("behind_family", 0), cen.get("follow_family", 0),
              cen.get("continuation_bytes", 0), cen.get("bounded", 0),
              counts.get("interior", 0), counts.get("folded", 0)))
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
            {"va": 0x5000, "name": "FUN_00005000", "size": 3},      # the size cuts an instruction
            {"va": 0x5100, "name": "FUN_00005100", "size": 2},      # ... and the cut instruction is a `ret`
            {"va": 0x4000, "name": "FUN_00004000", "size": 0x10}]   # ends on live code, nothing after
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
    put(0x3400, b"\x90" * 0x0f + b"\x5d")
    put(0x3410, b"\xc3" + b"\xcc" * 2 + b"\x55\x8b")
    put(0x3500, b"\x90" * 0x0f + b"\x5d")
    put(0x5000, b"\x90" + b"\x05" * 5 + b"\xc3" + b"\x90" * 8)
    put(0x5100, b"\x90" + b"\xc2\x08\x00" + b"\x90" * 6)
    put(0x4000, b"\x90" * 0x0f + b"\x5d")

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
    if verdicts.get("0x00003000", {}).get("verdict") != "continuation" or \
            verdicts["0x00003000"]["reason"] != "head":
        bad.append("a row with a head at its end is a head continuation")
    if verdicts.get("0x00003000", {}).get("new_size") != 0x13:
        bad.append("the head continuation's new size is row + head")
    if verdicts.get("0x00003100", {}).get("reason") != "tail" or \
            verdicts["0x00003100"]["tail_bytes"] != 4:
        bad.append("a row whose own decode closes into fill is a tail continuation of 4 bytes")
    if verdicts.get("0x00003200", {}).get("reason") != "jump" or \
            verdicts["0x00003200"]["verdict"] != "continuation":
        bad.append("a `jmp` with only fill behind it is a tail-call continuation")
    if verdicts.get("0x00003300", {}).get("reason") != "jump" or \
            verdicts["0x00003300"]["verdict"] != "refused":
        bad.append("a `jmp` with code behind it refuses the read")
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
                 {"va": 0x10500, "name": "FUN_00010500", "size": 1}]
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
    tmp2 = tempfile.mkdtemp()
    try:
        p2 = os.path.join(tmp2, "symbol_extents.csv")
        write_artifact(fout, fcen, p2)
        again, cen4 = measure([fold_rows[0], fold_rows[2]], [], r2, d2,
                              known={r["row_va"]: r for r in fout}, text=(0x10000, 0x11000))
        if cen4.get("split_folded") != 1 or not [r for r in again if r["verdict"] == "folded"]:
            bad.append("a dropped row's record is carried from the artifact")
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)
    if bad:
        for b in bad:
            print("FAIL selftest: " + b)
        return 1
    print("ok   symbol-extents selftest: both tail families, the cut repairs, the pad and `jmp` closes, "
          "the `bounded` row, the split, the refusals, the three closes behind a `ret`, the fold, the "
          "undecoded cut, the carry and both round trips")
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
    return {r["row_va"]: r for r in rows if r.get("verdict") in ("continuation", "interior", "folded")}


if __name__ == "__main__":
    main()
