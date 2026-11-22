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

Both are resizes to `row size + tail`. Every other row that does not end on a terminator is counted and
not repaired: `split` (the next map row starts at the row's end -- Ghidra split one body in two, which is
a `drop` repair and needs a review this rule cannot supply), `refused` (no close, a `jmp` first, code
after the close, or nothing to decode), `short` (the decode does not land on the row's own end -- a byte
count, not a boundary) and `undecoded`.

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
HEADS = os.path.join(ROOT, "inventory", "gap_heads.csv")
ARTIFACT = os.path.join("inventory", "symbol_extents.csv")
DOC_MARKS = ("<!-- generated:symbol-extents -->", "<!-- /generated:symbol-extents -->")

ROW = ("row_va", "row_name", "row_size", "last_va", "last_length", "last_insn", "end_kind",
       "tail_bytes", "close_va", "close_insn", "head_va", "head_size", "head_prologue",
       "head_verdict", "new_size", "verdict", "reason", "note")
CENSUS = ("text_rows", "sized_rows", "terminator", "short", "undecoded", "nonterm", "continuation",
          "continuation_bytes", "head_family", "tail_family", "head_bytes", "tail_bytes_total",
          "split", "refused", "refused_no_close", "refused_jump", "refused_then_code",
          "refused_undecoded", "refused_no_room", "null_hits", "null_trials")

OBJ_LINE = re.compile(r"^\s*([0-9a-f]+):\t([0-9a-f ]+?)\s*\t(.*)$")
TERM = ("ret", "jmp", "int3", "hlt", "iret")
FILL = (0xCC, 0x90)
MAX_TAIL = 256
REPAIRS = ("row ends on a non-terminator and a gap head closes at its end",
           "row ends on a non-terminator and a decode from its end reaches a `ret` with only fill after it")


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


def measure(rows, heads, reader, decode_fn=decode, known=None, text=None, null_trials=0):
    """(artifact rows, census). `reader(va, size)` returns bytes; `known` carries repaired rows."""
    lo, hi = text or (0x00010000, 0x00500000)
    starts = [r["va"] for r in rows if r["size"]]
    head_at = {}
    for h in heads:
        head_at.setdefault(h["va"], h)
    known = known or {}
    cen = collections.Counter()
    out = []
    for r in rows:
        if not (lo <= r["va"] < hi):
            continue
        cen["text_rows"] += 1
        if not r["size"]:
            continue
        cen["sized_rows"] += 1
        k = bisect.bisect_right(starts, r["va"])
        nxt = starts[k] if k < len(starts) else None
        held = known.get(r["va"]) or known.get("0x%08x" % r["va"])
        if held and held.get("new_size") and int(held["new_size"]) == r["size"]:
            out.append(dict(held))
            cen["nonterm"] += 1
            cen["continuation"] += 1
            cen["continuation_bytes"] += int(held.get("tail_bytes") or 0)
            cen["%s_family" % held.get("reason", "head")] += 1
            continue
        ins = decode_fn(reader(r["va"], r["size"]), r["va"])
        end = r["va"] + r["size"]
        if not ins:
            cen["undecoded"] += 1
            out.append(_row(r, None, "decode:none", 0, "", "undecoded", "decode-none",
                            "objdump decodes nothing from this row's head"))
            continue
        last = ins[-1]
        if last[0] + last[1] != end:
            cen["short"] += 1
            out.append(_row(r, last, "decode:stops-at", 0, "", "short", "decode-stops",
                            "the decode stops %d byte(s) before the row's own end" % (end - last[0] - last[1])))
            continue
        if is_term(last[2]):
            cen["terminator"] += 1
            continue
        cen["nonterm"] += 1
        h = head_at.get(end)
        if nxt == end:
            cen["split"] += 1
            out.append(_row(r, last, "next-row", 0, "", "split", "next-row",
                            "the next map row starts here, so the map split one body in two -- a `drop` "
                            "repair, not a resize"))
        elif h and h["size"] and h["verdict"] in ("body", "island"):
            cen["continuation"] += 1
            cen["continuation_bytes"] += h["size"]
            cen["head_family"] += 1
            cen["head_bytes"] += h["size"]
            out.append(_row(r, last, "head:%s" % h["verdict"], h["size"],
                            "0x%08x" % (h["va"] + h["size"]), "continuation", "head",
                            "the %d-byte %s at the row's end closes on `%s`" % (
                                h["size"], h["verdict"], h["tail"] or "a terminator"),
                            head=h, new_size=r["size"] + h["size"]))
        else:
            room = (nxt if nxt else end + MAX_TAIL) - end
            tail = decode_fn(reader(end, min(room, MAX_TAIL)), end) if room > 0 else []
            close, why = tail_close(tail)
            after = b""
            if close:
                after = reader(close[0] + close[1], max(0, (nxt if nxt else close[0] + close[1] + 1)
                                                        - (close[0] + close[1])))
            if not tail:
                reason, note = ("no-room" if room <= 0 else "undecoded"), \
                    ("the row ends where the next map row starts" if room <= 0
                     else "nothing decodes from the row's end")
            elif why:
                reason, note = why, ("a `jmp` opens the tail, so its target code is behind it" if why == "jump"
                                     else "no `ret` before the next map row")
            elif not all(b in FILL for b in after):
                reason, note = "then-code", "the close is followed by code, not fill"
            else:
                reason, note = None, None
            if reason:
                cen["refused"] += 1
                cen["refused_%s" % reason.replace("-", "_")] += 1
                out.append(_row(r, last, "tail:%s" % reason, 0, "", "refused", reason, note))
            else:
                tail_bytes = close[0] + close[1] - end
                cen["continuation"] += 1
                cen["continuation_bytes"] += tail_bytes
                cen["tail_family"] += 1
                cen["tail_bytes_total"] += tail_bytes
                out.append(_row(r, last, "tail:fill", tail_bytes, "0x%08x" % (close[0] + close[1]),
                                "continuation", "tail",
                                "a decode from the row's end reaches `%s` and only fill follows"
                                % close[2], close=close, new_size=r["size"] + tail_bytes))
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
         new_size=0):
    return {"row_va": "0x%08x" % r["va"], "row_name": r["name"], "row_size": r["size"],
            "last_va": "0x%08x" % last[0] if last else "", "last_length": last[1] if last else "",
            "last_insn": last[2] if last else "", "end_kind": end_kind,
            "tail_bytes": tail_bytes or "", "close_va": close_va,
            "close_insn": close[2] if close else "",
            "head_va": "0x%08x" % head["va"] if head else "",
            "head_size": head["size"] if head else "",
            "head_prologue": head["prologue"] if head else "",
            "head_verdict": head["verdict"] if head else "",
            "new_size": new_size or "", "verdict": verdict, "reason": reason, "note": note}


def pct(a, b):
    return (100.0 * a / b) if b else 0.0


def render(rows, cen):
    def n(k):
        return int(cen.get(k, 0) or 0)
    cont = [r for r in rows if r["verdict"] == "continuation"]
    heads = [r for r in cont if r["reason"] == "head"]
    tails = [r for r in cont if r["reason"] == "tail"]
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
        "`ret`/`jmp`/`int3` is unfinished, and the tail is measured two ways. **%d rows carry a gap head "
        "at their end** -- the head rule's own sweep closes it, so the head's measured size is the tail "
        "(**%d bytes**). **%d more have no head there**, and a decode from the row's own end reaches a "
        "`ret` with nothing but fill (`0xCC`/`0x90`) between it and the next map row (**%d bytes**) -- a "
        "`jmp` before that `ret` refuses the read, because the tail-call's target code would sit behind "
        "it. Together **%d rows and %d bytes**: %d of them are short by one instruction (a row that stops "
        "on `pop ebp`/`add esp,N` before its own `ret`), and the largest is `%s`, a %s-byte row continued "
        "by %s bytes." % (
            n("head_family"), n("head_bytes"), n("tail_family"), n("tail_bytes_total"),
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
    rest = [r for r in rows if r["verdict"] in ("split", "refused")]
    if rest:
        splits = [r for r in rest if r["verdict"] == "split"]
        lines.append(
            "**What this does not repair.** **%d** rows end mid-instruction (`short`: the size cuts an "
            "instruction in half) and **%d** do not decode at all -- both a byte count, not a boundary. Of "
            "the rest, **%d** stop on live code with no close before the next row (**%d** open a `jmp`, "
            "**%d** close and then fall into code, **%d** reach nothing that decodes, **%d** have no byte "
            "behind them), and **%d** are a body the map already split from its own continuation -- %s -- "
            "which is a `drop` repair and needs a review this rule cannot supply." % (
                n("short"), n("undecoded"), n("refused"), n("refused_jump"), n("refused_then_code"),
                n("refused_no_close"), n("refused_undecoded") + n("refused_no_room"),
                len(splits), ", ".join("`%s`" % r["row_va"] for r in splits) or "none"))
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
    return ("%s; `%s` closes the run %s bytes past the row's end and only fill follows before the next "
            "row" % (REPAIRS[1], r["close_insn"], r["tail_bytes"]))


def write_boundaries(rows, path=BOUNDARIES):
    """Append one `resize` row per continuation. Refuses a conflicting repair on the same address."""
    with open(path, newline="", encoding="utf-8") as fh:
        text = fh.read()
    have = {b["address"]: b for b in read_boundaries(path)}
    added, kept, bad = 0, 0, []
    lines = []
    for r in rows:
        if r["verdict"] != "continuation":
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


def check():
    rows, cen = read_artifact()
    bad = []
    if not rows or not cen:
        bad.append("%s is missing or holds no census row" % ARTIFACT)
    by_addr = {r["va"]: r for r in read_map()}
    repairs = {b["address"]: b for b in read_boundaries() if b["action"] == "resize"}
    counts = collections.Counter(r["verdict"] for r in rows)
    for r in rows:
        va = int(r["row_va"], 16)
        row = by_addr.get(va)
        if not row:
            bad.append("%s has no row in the map" % r["row_va"])
            continue
        if r["verdict"] == "continuation":
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
    if cen.get("head_family", 0) + cen.get("tail_family", 0) != cen.get("continuation", 0):
        bad.append("the two evidence families do not add up to the continuations")
    if cen.get("continuation", 0) + cen.get("split", 0) + cen.get("refused", 0) != cen.get("nonterm", 0):
        bad.append("continuation + split + refused does not equal the non-terminating rows")
    for k in CENSUS:
        if k.startswith("refused_") and int(cen.get(k) or 0) > int(cen.get("refused", 0) or 0):
            bad.append("%s exceeds the refused population" % k)
    if bad:
        for b in bad[:8]:
            print("FAIL symbol-extents: " + b)
        return 1
    print("ok   symbol-extents: %d rows, %d continuation(s) (%d head, %d tail), %d byte(s), every repair "
          "applied" % (len(rows), counts.get("continuation", 0), cen.get("head_family", 0),
                       cen.get("tail_family", 0), cen.get("continuation_bytes", 0)))
    return 0


def self_check():
    bad = []
    rows = [{"va": 0x2000, "name": "FUN_00002000", "size": 0x20},   # closes on `ret`
            {"va": 0x2020, "name": "FUN_00002020", "size": 6},      # abuts: a split
            {"va": 0x2026, "name": "FUN_00002026", "size": 8},      # the row the split hands over to
            {"va": 0x3000, "name": "FUN_00003000", "size": 0x10},   # a head at its end
            {"va": 0x3100, "name": "FUN_00003100", "size": 0x10},   # a tail that closes into fill
            {"va": 0x3200, "name": "FUN_00003200", "size": 0x10},   # a `jmp` opens the tail: refused
            {"va": 0x4000, "name": "FUN_00004000", "size": 0x10}]   # ends on live code, nothing after
    heads = [{"va": 0x3010, "size": 3, "boundary": "start", "verdict": "island",
              "prologue": "bare-ret", "tail": "pad"}]
    blobs = {0x2000: b"\x90" * 0x1f + b"\xc3",
             0x2020: b"\x90" * 6,
             0x2026: b"\x90" * 7 + b"\xc3",
             0x3000: b"\x90" * 0x0f + b"\x5d",
             0x3010: b"\x90" * 2 + b"\xc3",
             0x3100: b"\x90" * 0x0f + b"\x5d",
             0x3110: b"\x90" * 3 + b"\xc3" + b"\xcc" * 4,
             0x3200: b"\x90" * 0x0f + b"\x5d",
             0x3210: b"\xe9" + b"\x00" * 4 + b"\xcc" * 3,
             0x4000: b"\x90" * 0x0f + b"\x5d"}

    def fake(blob, base):
        if not blob:
            return []
        if base == 0x2020:
            return [(0x2025, 1, "nop")]
        if base == 0x3210:
            return [(0x3210, 5, "jmp    0x0"), (0x3215, 1, "ret")]
        out = [(base, 1, "nop")]
        for i in range(1, len(blob)):
            if blob[i] == 0xC3:
                out.append((base + i, 1, "ret"))
                break
            if blob[i] == 0xE9:
                out.append((base + i, 5, "jmp    0x0"))
                break
        if out[-1][2] == "nop":
            out.append((base + len(blob) - 1, 1, "pop    ebp"))
        return out

    out, cen = measure(rows, heads, lambda va, size: blobs.get(va, b""), fake, text=(0x2000, 0x5000))
    verdicts = {r["row_va"]: r for r in out}
    if verdicts.get("0x00003000", {}).get("verdict") != "continuation" or \
            verdicts["0x00003000"]["reason"] != "head":
        bad.append("a row with a head at its end is a head continuation")
    if verdicts.get("0x00003000", {}).get("new_size") != 0x13:
        bad.append("the head continuation's new size is row + head")
    if verdicts.get("0x00003100", {}).get("reason") != "tail" or \
            verdicts["0x00003100"]["tail_bytes"] != 4:
        bad.append("a row whose own decode closes into fill is a tail continuation of 4 bytes")
    if verdicts.get("0x00003200", {}).get("reason") != "jump":
        bad.append("a `jmp` opening the tail refuses the read")
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
                                lambda va, size: b"", fake, known={"0x00003000": held}, text=(0, 0x9000))
        if not carried or carried[0]["verdict"] != "continuation" or cen3.get("continuation") != 1:
            bad.append("a repaired row is carried, not re-measured")
        if set(cen) - set(CENSUS):
            bad.append("census holds keys the header does not write: %s" % ", ".join(sorted(set(cen) - set(CENSUS))))
        if not render(back, cen2):
            bad.append("render returns nothing")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if bad:
        for b in bad:
            print("FAIL selftest: " + b)
        return 1
    print("ok   symbol-extents selftest: both families, the split, the `jmp` refusal, the carry, both "
          "round trips")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xbe", help="default.xbe (measure needs it)")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--write-boundaries", action="store_true")
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
                        null_trials=len(null_addresses))
    write_artifact(rows, cen, os.path.join(a.out, "symbol_extents.csv"))
    print("%d rows, %d continuation(s) (%d head, %d tail), %d byte(s); null %d/%d" % (
        len(rows), cen.get("continuation", 0), cen.get("head_family", 0), cen.get("tail_family", 0),
        cen.get("continuation_bytes", 0), cen.get("null_hits", 0), cen.get("null_trials", 0)))


def _known():
    rows, _ = read_artifact()
    return {r["row_va"]: r for r in rows if r.get("verdict") == "continuation"}


if __name__ == "__main__":
    main()
