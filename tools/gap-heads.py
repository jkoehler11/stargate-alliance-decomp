#!/usr/bin/env python3
"""The map's gaps, censused: every head a pad-before plus prologue test finds, and what names it.

`symbols/xbox_functions.csv` covers 3,445,170 of `.text`'s 4,227,324 bytes, so **783,490 bytes sit in
9,987 gaps** between consecutive rows. Only two things were ever read inside them: the 84 installer tail
jumps (`tools/native-island-gaps.py`) and the bodies a *table* names (`native-census.py`). The rest was
described, not measured -- the previous bundle's note guessed "~1,800 decodable heads, mostly one-`ret`
islands inside data and alignment". This tool replaces the guess with a census and a control.

A **head** is an address in a gap that satisfies two things a function start satisfies in this build:

* **a boundary in front of it** -- a run of `0xCC`/`0x90` fill, which is how MSVC separates bodies here,
  or the gap's own start (the row above hands over). A head inside a run whose predecessor is not a pad
  is not read: the bytes before it are code, so it is a block of that code, not a boundary.
* **an MSVC prologue shape** -- the same table `tools/native-census.py` classifies the 883 registration
  bodies with (`8b ff` hotpatch, `55 8b ec` frame, `64 a1` SEH, an argument load, `sub esp`, `ret imm16`,
  a push, `mov eax`).

A head becomes a **body** when a linear decode from it closes on an unconditional terminator *with a tail
behind it* (`native-census.sweep_body`, whose five tails are `pad`/`edge`/`align`/`table`/`bytes`). It is
an **island** when that close is one or two instructions or four bytes (the `33 c0 c2 08 00` stub a vtable
slot holds); `no-close` and `undecoded` are the refusals.

The census is only worth quoting with its controls, and they are measured in the same run:

* **precision, from a null population.** The same rule over 3.2 MB of the image's *data* sections
  (`.rdata`, `.data`, XNET, DSOUND) finds pad-before prologue-shaped bytes and **zero** bodies: a decode
  from a prologue in data does not close on a terminator with a tail. The gaps yield 3.1 bodies/KB.
* **recall, on the population that is known to be bodies.** The same two tests over the 11,784 map rows in
  `.text` pass on 9,640 (81.8%), so the rule is a **lower bound**: 1,833 rows start behind a pad on an
  opening the prologue table does not name (an XDK library body's `mov ecx,<global>`, most of them).
* **the naming channel's own base rate.** A dword outside `.text` names a *random* gap byte 1.1% of the
  time (22 of 2,000) and a map row's start 24.1% (481 of 2,000), so "a pointer names this head" is
  evidence only against those numbers, not on its own.

The last axis is what the head rule cannot answer: a body that nothing names is still a body. Each head
therefore carries the four channels that reach one -- an `E8` call, an `E9` jump, a `68` push, and a dword
outside `.text` (with its class-table slot resolved when the ref lands in one, mapped or unmapped).

    tools/gap-heads.py --dump "$SG1_DUMP" -o inventory     # measure (needs the image)
    tools/gap-heads.py --report                            # the table, from the artifact
    tools/gap-heads.py --write-doc docs/SYMBOLS.md
    tools/gap-heads.py --check-doc docs/SYMBOLS.md
    tools/gap-heads.py --check                             # dump-free gate (in `make ci`)
    tools/gap-heads.py --selftest                          # synthetic bodies, no dump

**A body that abuts its neighbour with no fill merges into it**, and that is measured rather than hidden:
`inner_starts` counts the terminators inside a run that the next instruction follows with a strong body
opening (`mov r,imm32`, `push imm32`, a call, a prologue). `0x00418c30` is five 21-byte bodies the decode
reads as one 131-byte run, so the run count is a floor.

The dump is never committed; the artifact and the page are. Addresses and counts only.
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

ARTIFACT = os.path.join(ROOT, "inventory", "gap_heads.csv")
FUNCTIONS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
NAMES = os.path.join(ROOT, "inventory", "xbe_class_table_names.csv")
DOC = os.path.join(ROOT, "docs", "SYMBOLS.md")
DOC_START = "<!-- generated:gap-heads -->"
DOC_END = "<!-- /generated:gap-heads -->"
GAME = "Stargate_SG-1_-_The_Alliance-2005-12-15"

COLS = ["row", "head_va", "gap_lo", "gap_hi", "gap_size", "boundary", "prologue", "verdict", "size",
        "insns", "close", "tail", "inner_starts", "calls", "jumps", "imms", "pointers", "slots",
        "slot_class", "slot_index", "census"]

# A tail shape's name as `native-census.tail_shape` writes it; the five kinds, concatenated with `+`.
TAILS = ("edge", "pad", "align", "table", "bytes")


_MODS = {}


def _load(name):
    """Import a sibling tool once: `strong_opening` asks for the prologue table at every terminator."""
    if name not in _MODS:
        spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MODS[name] = mod
    return _MODS[name]


def _art(path=ARTIFACT):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- the map's gaps
def map_table(funcs):
    """[(start, end, name)] sorted, from `symbols/xbox_functions.csv`."""
    out = [(int(f["address"], 16), int(f["address"], 16) + int(f.get("size") or 0), f.get("name") or "")
           for f in funcs]
    out.sort()
    return out


def gaps_of(table, lo, hi):
    """[(gap_lo, gap_hi)] -- the bytes of `[lo, hi)` no row covers, in order."""
    rows = [t for t in table if lo <= t[0] < hi]
    if not rows:
        return []
    out, prev_end = [], rows[0][1]
    for a, b, _n in rows[1:]:
        if a > prev_end:
            out.append((prev_end, a))
        prev_end = max(prev_end, b)
    if prev_end < hi:
        out.append((prev_end, hi))
    return out


# --------------------------------------------------------------------------- the head rule
def walk(decode, tail_shape, terminator_kind, pro, blob, limit, va):
    """(size, insns, close, tail, inner_starts) for the run at `blob[0]`, or (None, insns, why, None, inner)."""
    i, n, inner, prev, start0 = 0, 0, 0, None, None
    while i < limit:
        ins = decode(blob[i:limit], 0)
        if ins is None or ins.length <= 0:
            return None, n, "undecoded", None, inner
        start0 = i
        i += ins.length
        n += 1
        prev = ins.text
        kind = terminator_kind(blob, start0, prev)
        if kind:
            shape = tail_shape(blob, i, limit, va)
            if shape:
                return i, n, kind, shape, inner
            # A terminator with code behind it is walked past -- either an early return inside this
            # body or the neighbour's start with no fill between them. Only the second is a body the
            # head rule cannot see, and a strong opening at the next instruction is what separates them.
            if i < limit and strong_opening(pro, blob, i):
                inner += 1
    return None, n, "no-close", None, inner


def strong_opening(pro, blob, i):
    """Does the instruction at `i` open a body: `mov r,imm32`, `push imm32`, a call, or a prologue?"""
    if i >= len(blob):
        return False
    op = blob[i]
    if 0xB8 <= op <= 0xBF or op == 0x68 or op == 0xE8:
        return True
    return pro(blob[i:i + 8]) != "other"


def scan(decode, nc, lo, hi, blob):
    """Every head in `blob` between `lo` and `hi`: heads, walked from the region's first byte."""
    out, off, first = [], 0, True
    while off < len(blob):
        preceded = first or (off >= 1 and blob[off - 1] in (0xCC, 0x90))
        if preceded:
            pr = nc.prologue(blob[off:off + 8])
            if pr != "other":
                size, n, close, tail, inner = walk(decode, nc.tail_shape, nc.terminator_kind, nc.prologue,
                                                    blob[off:], len(blob) - off, lo + off)
                pad = 0
                k = off - 1
                while k >= 0 and blob[k] in (0xCC, 0x90):
                    pad += 1
                    k -= 1
                out.append({"head_va": lo + off, "boundary": "start" if first and not pad else "pad:%d" % pad,
                            "prologue": pr, "size": size, "insns": n, "close": close, "tail": tail,
                            "inner_starts": inner})
                off += (size if size else 1)
                first = False
                continue
        first = False
        off += 1
    return out


def verdict(h):
    """`body`, `island`, or the refusal -- the three populations the page counts."""
    if not h["size"]:
        return "undecoded" if h["close"] == "undecoded" else "no-close"
    if h["insns"] <= 2 or h["size"] <= 4:
        return "island"
    return "body"


# --------------------------------------------------------------------------- the reference channels
def channels(xbe, raw, secs, blobs, tva, heads, spans, names):
    """Fill calls/jumps/imms/pointers/slots on every head, then report the per-channel counts."""
    body_vas = {h["head_va"] for h in heads}
    calls, jumps = collections.Counter(), collections.Counter()
    text = blobs[".text"]
    for i in range(len(text) - 4):
        op = text[i]
        if op in (0xE8, 0xE9):
            rel = struct.unpack("<i", text[i + 1:i + 5])[0]
            t = tva + i + 5 + rel
            if t in body_vas:
                (calls if op == 0xE8 else jumps)[t] += 1
    imms = set()
    for i in range(len(text) - 5):
        if text[i] == 0x68:
            t = struct.unpack("<I", text[i + 1:i + 5])[0]
            if t in body_vas:
                imms.add(t)
    outside = b"".join(blobs[s] for s in secs)
    starts = [s[0] for s in spans]
    count = collections.Counter()
    for h in heads:
        va = h["head_va"]
        h["calls"] = calls.get(va, 0)
        h["jumps"] = jumps.get(va, 0)
        h["imms"] = 1 if va in imms else 0
        pat = struct.pack("<I", va)
        refs, pos, slot = [], outside.find(pat), None
        while pos != -1 and pos + 4 <= len(outside):
            refs.append(pos)
            pos = outside.find(pat, pos + 1)
        h["pointers"] = len(refs)
        h["slots"], h["slot_class"], h["slot_index"] = 0, "", ""
        for o in refs:
            ref = off_to_va(secs, o)
            i = bisect.bisect_right(starts, ref) - 1
            if i < 0:
                continue
            tva_, sn, kind = spans[i]
            if tva_ <= ref < tva_ + 4 * sn:
                h["slots"] += 1
                if slot is None:
                    slot = (names.get(tva_, ""), (ref - tva_) // 4, kind)
        if slot:
            h["slot_class"], h["slot_index"] = slot[0], slot[1]
        count["pointer"] += 1 if refs else 0
        count["slot"] += 1 if h["slots"] else 0
        count["branch"] += 1 if (h["calls"] or h["jumps"]) else 0
        count["imm"] += 1 if h["imms"] else 0
        count["any"] += 1 if (refs or h["imms"] or h["calls"] or h["jumps"]) else 0
    return count


def off_to_va(secs, o):
    acc = 0
    for name in secs:
        size = len(_RAW[name])
        if o < acc + size:
            return _SECVA[name] + (o - acc)
        acc += size
    return 0


# The concatenated data sections, set by `measure()` and read by `off_to_va` (one global, no threading).
_RAW, _SECVA = {}, {}


def table_spans():
    """Every class-table span in the image, mapped and unmapped, with the class name where one is known."""
    spans, names = [], {}
    for path, key, kind in (("inventory/xbe_class_tables.csv", "slots", "mapped"),
                            ("inventory/native_vtable_shared.csv", "table_slots", "unmapped")):
        fp = os.path.join(ROOT, path)
        if not os.path.isfile(fp):
            continue
        for r in csv.DictReader(open(fp, newline="")):
            if r.get("table_va") and r.get(key):
                spans.append((int(r["table_va"], 16), int(r[key]), kind))
    if os.path.isfile(NAMES):
        for r in csv.DictReader(open(NAMES, newline="")):
            if r.get("table_va"):
                names[int(r["table_va"], 16)] = r.get("class") or ""
    spans.sort()
    return spans, names


# --------------------------------------------------------------------------- the measurement
def measure(args):
    xbe = _load("xbe")
    nc = _load("native-census")
    X = xbe.Xbe(args.xbe)
    decode = nc._decoder()
    table = map_table(_art(FUNCTIONS))
    secs = [s.name for s in X.sections]
    raw = open(args.xbe, "rb").read()
    blobs = {s.name: raw[s.foff:s.foff + s.fsize] for s in X.sections}
    tva = [s for s in X.sections if s.name == ".text"][0].va
    vsize = [s for s in X.sections if s.name == ".text"][0].vsize
    text = blobs[".text"]
    lo, hi = tva, tva + vsize
    gaps = gaps_of(table, lo, hi)
    print("text %#x..%#x, %d gaps, %d gap bytes" % (lo, hi, len(gaps), sum(b - a for a, b in gaps)))

    heads = []
    for gs, ge in gaps:
        if ge - gs < 4:
            continue
        blob = X.read_va(gs, ge - gs)
        for h in scan(decode, nc, gs, ge, blob):
            h["gap_lo"], h["gap_hi"], h["gap_size"] = gs, ge, ge - gs
            heads.append(h)
    spans, names = table_spans()
    for name in secs:
        _RAW[name], _SECVA[name] = blobs[name], [s for s in X.sections if s.name == name][0].va
    ch = channels(xbe, raw, [s for s in secs if s != ".text"], blobs, tva, heads, spans, names)

    # the controls: the same two tests over the rows the map already places
    ctl = collections.Counter()
    for a, b, _n in [t for t in table if lo <= t[0] < hi]:
        pad = a > lo and text[a - 1 - lo] in (0xCC, 0x90)
        pr = nc.prologue(text[a - lo:a - lo + 8])
        ctl["both" if (pad and pr != "other") else ("pad" if pad else ("prologue" if pr != "other" else "neither"))] += 1
        size, n, close, tail, _i = walk(decode, nc.tail_shape, nc.terminator_kind, nc.prologue,
                                        text[a - lo:b - lo], b - a, a)
        ctl["decode" if size == b - a else ("short" if size else "noclose")] += 1
    # the null population: pad-before prologue-shaped bytes in the data sections
    for name in (".rdata", ".data", "XNET", "DSOUND"):
        if name not in blobs:
            continue
        sec = [s for s in X.sections if s.name == name][0]
        found = scan(decode, nc, sec.va, sec.va + sec.vsize, blobs[name])
        ctl["null_" + name] = sum(1 for f in found if f["size"])
        ctl["nullheads_" + name] = len(found)
    # base rates for the pointer channel
    import random
    random.seed(7)
    body_vas = {h["head_va"] for h in heads}
    allgap = [va for gs, ge in gaps for va in range(gs, ge)]
    nonbody = [va for va in allgap if va not in body_vas]
    outside = b"".join(blobs[s] for s in secs if s != ".text")
    for label, pool, n in (("base_gap", nonbody, 2000), ("base_row", [t[0] for t in table if lo <= t[0] < hi], 2000)):
        sample = random.sample(pool, min(n, len(pool)))
        hit = sum(1 for va in sample if struct.pack("<I", va) in outside)
        ctl[label] = hit
        ctl[label + "_n"] = len(sample)

    rows = []
    for h in sorted(heads, key=lambda x: x["head_va"]):
        v = verdict(h)
        rows.append({"row": "head", "head_va": "0x%08x" % h["head_va"],
                     "gap_lo": "0x%08x" % h["gap_lo"], "gap_hi": "0x%08x" % h["gap_hi"],
                     "gap_size": h["gap_size"], "boundary": h["boundary"], "prologue": h["prologue"],
                     "verdict": v, "size": h["size"] or "", "insns": h["insns"],
                     "close": h["close"] or "", "tail": h["tail"] or "",
                     "inner_starts": h["inner_starts"], "calls": h["calls"], "jumps": h["jumps"],
                     "imms": h["imms"], "pointers": h["pointers"], "slots": h["slots"],
                     "slot_class": h["slot_class"], "slot_index": h["slot_index"]})
    vc = collections.Counter(r["verdict"] for r in rows)
    bodies = [r for r in rows if r["verdict"] in ("body", "island")]
    meta = collections.OrderedDict()
    meta["text_lo"], meta["text_hi"] = lo, hi
    meta["gaps"] = len(gaps)
    meta["gap_bytes"] = sum(b - a for a, b in gaps)
    meta["gaps_under_8"] = sum(1 for a, b in gaps if b - a < 8)
    meta["heads"] = len(rows)
    meta["bodies"] = vc["body"]
    meta["islands"] = vc["island"]
    meta["no_close"] = vc["no-close"]
    meta["undecoded"] = vc["undecoded"]
    meta["body_bytes"] = sum(int(r["size"]) for r in bodies)
    meta["body_bytes_pct"] = int(round(100.0 * meta["body_bytes"] / max(meta["gap_bytes"], 1)))
    meta["inner_starts"] = sum(int(r["inner_starts"]) for r in rows)
    meta["bodies_with_inner"] = sum(1 for r in rows if int(r["inner_starts"]))
    meta["named_pointer"] = ch["pointer"]
    meta["named_slot"] = ch["slot"]
    meta["named_branch"] = ch["branch"]
    meta["named_imm"] = ch["imm"]
    meta["named_any"] = ch["any"]
    meta["unnamed"] = len(bodies) - ch["any"]
    meta["map_rows"] = sum(v for k, v in ctl.items() if k in ("both", "pad", "prologue", "neither"))
    meta["map_rows_head"] = ctl["both"]
    meta["map_rows_pad_only"] = ctl["pad"]
    meta["map_rows_prologue_only"] = ctl["prologue"]
    meta["map_rows_decode"] = ctl["decode"]
    for name in (".rdata", ".data", "XNET", "DSOUND"):
        if "nullheads_" + name in ctl:
            meta["null_heads_" + name.strip(".").lower()] = ctl["nullheads_" + name]
            meta["null_bodies_" + name.strip(".").lower()] = ctl["null_" + name]
    meta["base_gap"], meta["base_gap_n"] = ctl["base_gap"], ctl["base_gap_n"]
    meta["base_row"], meta["base_row_n"] = ctl["base_row"], ctl["base_row_n"]
    meta["tables"] = len(spans)
    meta["slot_classes"] = len({r["slot_class"] for r in rows if r["slot_class"]})
    rows.append({"row": "census", "census": ";".join("%s=%s" % (k, v) for k, v in meta.items())})
    assert len(rows) == len(heads) + 1, (len(rows), len(heads))
    print("heads %d: %d bodies, %d islands, %d no-close, %d undecoded; %d body bytes (%d%% of the gaps)"
          % (meta["heads"], meta["bodies"], meta["islands"], meta["no_close"], meta["undecoded"],
             meta["body_bytes"], meta["body_bytes_pct"]))
    print("named: pointer %d, table slot %d, branch %d, push %d; any %d, none %d"
          % (meta["named_pointer"], meta["named_slot"], meta["named_branch"], meta["named_imm"],
             meta["named_any"], meta["unnamed"]))
    print("controls: map rows head-rule %d of %d; decode-to-size %d; null bodies %s; base %.1f%% gap vs %.1f%% rows"
          % (meta["map_rows_head"], meta["map_rows"], meta["map_rows_decode"],
             {k: v for k, v in meta.items() if k.startswith("null_bodies_")},
             100.0 * meta["base_gap"] / max(meta["base_gap_n"], 1),
             100.0 * meta["base_row"] / max(meta["base_row_n"], 1)))
    return rows, meta


# --------------------------------------------------------------------------- the artifact and the page
def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in COLS})


def census_of(rows):
    for r in rows:
        if r.get("row") == "census" and r.get("census"):
            out = {}
            for pair in r["census"].split(";"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    out[k] = int(v, 16) if v.startswith("0x") else int(v)
            return out
    return {}


def render(rows):
    """The page's block, rebuilt from the artifact alone so the prose and the table cannot disagree."""
    m = census_of(rows)
    heads = [r for r in rows if r.get("row") == "head"]
    gaps = collections.Counter(r["gap_lo"] for r in heads)
    lines = ["| measure | value |", "|---|---|"]
    if m:
        lines += [
            "| `.text` | `0x%08x`..`0x%08x` (%d bytes) |" % (m["text_lo"], m["text_hi"], m["text_hi"] - m["text_lo"]),
            "| gaps between map rows / bytes they cover | %d / %d (%d gaps under 8 bytes) |"
            % (m["gaps"], m["gap_bytes"], m["gaps_under_8"]),
            "| heads the pad-before + prologue rule finds | %d |" % m["heads"],
            "| ...decode to a body / are a 1-2 instruction island | %d / %d |" % (m["bodies"], m["islands"]),
            "| ...refused (`no-close` / `undecoded`) | %d / %d |" % (m["no_close"], m["undecoded"]),
            "| bytes the bodies cover | %d (%.1f%% of the gap bytes) |" % (m["body_bytes"], m["body_bytes_pct"]),
            "| runs holding an inner strong opening (a merged neighbour) | %d in %d runs |"
            % (m["inner_starts"], m["bodies_with_inner"]),
            "| named by a dword outside `.text` | %d |" % m["named_pointer"],
            "| ...of those, in a class-table slot (%d tables, %d named classes) | %d |"
            % (m["tables"], m["slot_classes"], m["named_slot"]),
            "| named by an `E8`/`E9` branch | %d |" % m["named_branch"],
            "| named by a `68` push | %d |" % m["named_imm"],
            "| named by any channel / by none | %d / %d |" % (m["named_any"], m["unnamed"]),
            "| control: map rows passing the head rule / decoding to their size | %d of %d / %d |"
            % (m["map_rows_head"], m["map_rows"], m["map_rows_decode"]),
            "| control: the same rule over `.rdata`, `.data`, XNET, DSOUND (%d head-shaped runs, 3.3 MB of data) | %d / %d / %d / %d bodies |"
            % (m.get("null_heads_rdata", 0) + m.get("null_heads_data", 0) + m.get("null_heads_xnet", 0)
               + m.get("null_heads_dsound", 0),
               m.get("null_bodies_rdata", 0), m.get("null_bodies_data", 0), m.get("null_bodies_xnet", 0),
               m.get("null_bodies_dsound", 0)),
            "| base rate: a dword outside `.text` names a random gap byte / a map row's start | %d of %d (%.1f%%) / %d of %d (%.1f%%) |"
            % (m["base_gap"], m["base_gap_n"], 100.0 * m["base_gap"] / max(m["base_gap_n"], 1),
               m["base_row"], m["base_row_n"], 100.0 * m["base_row"] / max(m["base_row_n"], 1)),
        ]
    top = gaps.most_common(8)
    if top:
        lines += ["", "| gap | heads | bodies | first head |", "|---|---:|---:|---|"]
        for g, n in top:
            row = [r for r in heads if r["gap_lo"] == g]
            nb = sum(1 for r in row if r["verdict"] in ("body", "island"))
            lines.append("| `%s`..`%s` | %d | %d | `%s` |" % (row[0]["gap_lo"], row[0]["gap_hi"], n, nb,
                                                             min(r["head_va"] for r in row)))
    if m:
        lines += ["", "%d of the %d bodies are the body of a class-table slot the map already names, and %d "
                  "are named by no channel at all: nothing calls them, nothing points at them, and the map "
                  "has no row for them." % (m["named_slot"], m["bodies"], m["unnamed"])]
    return "\n".join(lines)


def splice(path, block):
    text = open(path, encoding="utf-8").read()
    if DOC_START not in text or DOC_END not in text:
        return None
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + "\n" + DOC_END + tail


# --------------------------------------------------------------------------- the gate
def check():
    """Dump-free: the rows against the map, and the census row against the rows."""
    if not os.path.isfile(ARTIFACT):
        sys.exit("gap-heads: %s is missing" % os.path.relpath(ARTIFACT, ROOT))
    rows = _art()
    if list(rows[0].keys()) != COLS:
        sys.exit("gap-heads: --check: the table's columns are %r, the tool writes %r" % (list(rows[0].keys()), COLS))
    m = census_of(rows)
    for key in ("text_lo", "text_hi", "gaps", "heads", "bodies", "islands", "no_close", "undecoded"):
        if key not in m:
            sys.exit("gap-heads: --check: the census row carries no %s" % key)
    heads = [r for r in rows if r["row"] == "head"]
    table = map_table(_art(FUNCTIONS))
    gaps = gaps_of(table, m["text_lo"], m["text_hi"])
    bad = []
    if len(heads) != m["heads"]:
        bad.append("%d head rows for a census that says %d" % (len(heads), m["heads"]))
    vc = collections.Counter(r["verdict"] for r in heads)
    for key, name in (("bodies", "body"), ("islands", "island"), ("no_close", "no-close"),
                      ("undecoded", "undecoded")):
        if vc[name] != m[key]:
            bad.append("census says %s %d, the rows hold %d" % (key, m[key], vc[name]))
    total = sum(int(r["size"]) for r in heads if r["size"])
    if total != m["body_bytes"]:
        bad.append("the rows' sizes sum to %d, the census says %d" % (total, m["body_bytes"]))
    if len(gaps) != m["gaps"]:
        bad.append("the map holds %d gaps in .text, the census says %d" % (len(gaps), m["gaps"]))
    starts = [t[0] for t in table]
    for r in heads:
        va = int(r["head_va"], 16)
        if not (m["text_lo"] <= va < m["text_hi"]):
            bad.append("%s is outside .text" % r["head_va"])
            continue
        i = bisect.bisect_right(starts, va) - 1
        if i >= 0 and table[i][0] <= va < table[i][1]:
            bad.append("%s is inside the map row %s" % (r["head_va"], table[i][2]))
        if not r["boundary"].startswith("pad") and r["boundary"] != "start":
            bad.append("%s: boundary %r is not a pad or the gap's start" % (r["head_va"], r["boundary"]))
        v = r["verdict"]
        size = int(r["size"] or 0)
        if v in ("body", "island"):
            if not size or r["close"] not in ("ret", "ret-imm", "jmp", "jmp-ind"):
                bad.append("%s: %s with no close" % (r["head_va"], v))
            if r["tail"] and any(p not in TAILS for p in r["tail"].split("+")):
                bad.append("%s: tail %r is not one of %s" % (r["head_va"], r["tail"], "|".join(TAILS)))
            isl = int(r["insns"]) <= 2 or size <= 4
            if (v == "island") != isl:
                bad.append("%s: %s on %s bytes / %s instructions" % (r["head_va"], v, r["size"], r["insns"]))
        elif size:
            bad.append("%s: %s carries a size" % (r["head_va"], v))
    if bad:
        sys.exit("gap-heads: --check: " + "; ".join(bad[:6]))
    print("gap-heads: %d heads in %d gaps -- %d bodies, %d islands, %d no-close, %d undecoded"
          % (m["heads"], m["gaps"], m["bodies"], m["islands"], m["no_close"], m["undecoded"]))
    return 0


# --------------------------------------------------------------------------- the self test
def selftest():
    nc = _load("native-census")
    decode = nc._decoder()
    blob = (b"\xcc\xcc"
            + b"\x55\x8b\xec\x83\xec\x10\xc3"          # frame body
            + b"\xcc" * 4
            + b"\xc2\x04\x00"                           # the one-instruction island a slot holds
            + b"\xcc" * 2
            + b"\x51\x56\xc3"                           # a push-reg body: three instructions, so not an island
            + b"\xcc")
    heads = scan(decode, nc, 0x1000, 0x1000 + len(blob), blob)
    assert [h["prologue"] for h in heads] == ["frame", "empty-return", "push-reg"], heads
    assert verdict(heads[0]) == "body" and heads[0]["size"] == 7, heads[0]
    assert verdict(heads[1]) == "island" and heads[1]["size"] == 3, heads[1]
    assert heads[0]["boundary"] == "pad:2", heads[0]
    # a prologue-shaped run that never closes is refused, not called a body
    junk = b"\x90\x90" + b"\x55\x8b\xec" + b"\x90" * 40
    j = scan(decode, nc, 0x2000, 0x2000 + len(junk), junk)
    assert j and verdict(j[0]) == "no-close", j
    # an abutting neighbour: a `ret` then `mov ecx,imm32` is a body boundary with no fill, and the run
    # must say so -- `0x00418c30` is five 21-byte bodies the decode reads as one 131-byte run.
    abut = b"\x55\x8b\xec\xc3" + b"\xb9" + struct.pack("<I", 0x7b9ba8) + b"\xc3" + b"\xcc"
    a = scan(decode, nc, 0x3000, 0x3000 + len(abut), abut)
    assert len(a) == 1 and a[0]["inner_starts"] == 1 and a[0]["size"] == 10, a
    # the gaps: a placed row splits its neighbours, and an overlap does not open one
    table = [(0x1000, 0x1010, "a"), (0x1020, 0x1030, "b"), (0x1030, 0x1040, "c"), (0x1050, 0x1060, "d")]
    assert gaps_of(table, 0x1000, 0x1060) == [(0x1010, 0x1020), (0x1040, 0x1050)], gaps_of(table, 0x1000, 0x1060)
    # the census round trip: render reads the census row, not the measurement
    blank = {k: "" for k in COLS}
    fake = [dict(blank, row="head", head_va="0x1007", gap_lo="0x1000", gap_hi="0x1010", gap_size="16",
                 boundary="pad:2", prologue="frame", verdict="body", size="7", insns="4", close="ret",
                 tail="pad", inner_starts="0", calls="0", jumps="0", imms="0", pointers="2", slots="1",
                 slot_class="Camera", slot_index="7"),
            dict(blank, row="census", census="text_lo=0x1000;text_hi=0x2000;gaps=1;gap_bytes=16;gaps_under_8=0;"
                 "heads=1;bodies=1;islands=0;no_close=0;undecoded=0;body_bytes=7;body_bytes_pct=44;"
                 "inner_starts=0;bodies_with_inner=0;named_pointer=1;named_slot=1;named_branch=0;named_imm=0;"
                 "named_any=1;unnamed=0;map_rows=10;map_rows_head=8;map_rows_pad_only=1;map_rows_prologue_only=1;"
                 "map_rows_decode=9;null_heads_rdata=212;null_bodies_rdata=0;base_gap=22;base_gap_n=2000;"
                 "base_row=481;base_row_n=2000;tables=724;slot_classes=1")]
    m = census_of(fake)
    assert (m["heads"], m["bodies"], m["base_gap_n"]) == (1, 1, 2000), m
    page = render(fake)
    assert "| heads the pad-before + prologue rule finds | 1 |" in page, page
    assert "| `0x1000`..`0x1010` | 1 | 1 | `0x1007` |" in page, page
    assert splice(os.path.join(ROOT, "Makefile"), "x") is None   # no markers: refused, not echoed
    print("ok   gap-heads: 9 assertions (head rule, island, refusal, merged neighbour, gaps, census round trip, "
          "marker refusal)")
    return 0


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("--dump")
    ap.add_argument("-o", "--out")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc", nargs="?", const=DOC, default=None)
    ap.add_argument("--check-doc", nargs="?", const=DOC, default=None)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.check:
        return check()
    if args.report or args.write_doc or args.check_doc:
        rows = _art()
    else:
        if not args.xbe:
            if not args.dump:
                ap.error("--xbe or --dump is required to measure")
            args.xbe = os.path.join(args.dump, GAME, "default.xbe")
        if not os.path.isfile(args.xbe):
            sys.exit("gap-heads: %s is not a file" % args.xbe)
        rows, _m = measure(args)
        if args.out:
            os.makedirs(args.out, exist_ok=True)
            path = os.path.join(args.out, "gap_heads.csv")
            write_artifact(rows, path)
            print("wrote %s (%d head rows + 1 census)" % (path, len(rows) - 1))
    if args.report or args.write_doc or args.check_doc:
        print(render(rows))
    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        new = splice(path, render(rows))
        if new is None:
            print("FAIL gap-heads: markers not found in %s" % path)
            return 1
        old = open(path, encoding="utf-8").read()
        if args.check_doc:
            if new != old:
                print("FAIL gap-heads: %s is stale against the committed artifact" % path)
                return 1
            print("ok   gap-heads: %s matches the committed artifact" % path)
            return 0
        if new != old:
            open(path, "w", encoding="utf-8").write(new)
            print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
