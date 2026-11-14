#!/usr/bin/env python3
"""Which bodies in the image nothing references, and what a dead copy of the class ctors is.

The previous note closed on "the dead pair `0x0003c430`/`0x0003bd30` against the live pair
`0x0003af60`/`0x0003a5f0` -- a byte-level diff of the two bodies says what the linker kept a copy for".
This tool answers that and the general question behind it: **a body is unreachable when no byte of the
image points at it**, counting all three ways this build points at code --

  * a relative `E8` call or `E9` jump inside `.text` (a direct call, a tail-jump thunk),
  * a dword anywhere in any section equal to the start (a vtable slot, a registration table, a `push imm32`).

`symbols/xbox_functions.csv` is the population, plus the bodies that start at the image's own `int3`
padding and carry no map entry -- the rule that found `0x0003c430` in the first place. A body whose
every inbound edge comes from a body that is itself unreachable dies too, so the answer is a fixpoint
rather than a single in-degree count: `0x0003bd30` has two callers and is still dead, because both of
them are inside the dead set (`0x0003c3f2` in the State copy, `0x0003c45c` in the Class copy).

Reachability is not the same question as "is this code": a body the map names at an odd address and
nothing references may be a definition Ghidra invented over data. The census reports the start kind
(`map` or `pad`) and the alignment so those are visible, and it never calls a body *cut* -- only
unreferenced.

    python3 tools/native-unreachable.py --xbe DUMP/default.xbe -o inventory
    python3 tools/native-unreachable.py --report            # the counts and the family table
    python3 tools/native-unreachable.py --trace 0x3c430     # one body's installs, base call, edges
    python3 tools/native-unreachable.py --check             # synthetic, no dump, writes nothing

The dump is never committed; the artifact and the doc are.
"""
import argparse
import bisect
import collections
import csv
import os
import re
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join("inventory", "native_unreachable.csv")
MAP_PATH = os.path.join("symbols", "xbox_functions.csv")
BLOCK_MARK = ("<!-- generated:native-unreachable -->", "<!-- /generated:native-unreachable -->")
COLUMNS = ("kind", "at", "name", "detail", "evidence")

PAD = 0xCC
RUN_MIN = 2            # a padding run shorter than this is a stray byte inside a body
RUN_GAP = 0x400        # two unreachable bodies this close belong to the same run
BODY_WINDOW = 0x900    # how much of a body `--trace` disassembles

# The pair the previous note nominated, plus the base each one calls. Two of these are in the symbol
# map, two are found by the padding rule alone, and the map's own names for them are the shape rule's
# guess (`FUN_*`), so the rows carry what the bodies install and call rather than a class name.
FAMILY = [
    (0x0003AF60, "registration ctor (493 call sites)"),
    (0x0003C430, "the copy the previous note nominated"),
    (0x0003A5F0, "base the registration ctor calls"),
    (0x0003BD30, "base the nominated copy calls"),
    (0x0003C3D0, "sibling copy: installs the State table"),
]

TABLE_STORE = re.compile(r"mov\s+DWORD PTR \[[a-z]{2,3}\],\s*(0x4[0-9a-f]{5})\b")
BASE_CALL = re.compile(r"call\s+(0x[0-9a-f]+)")
ZERO_STORE = re.compile(r"mov\s+(?:DWORD|WORD) PTR \[[a-z]{2,3}\+(0x[0-9a-f]+)\],(?:edi|di)\b")
# `UState`'s constructor's whole body outside the table install: six stores that clear the block at
# +0x80..+0x94. It is the signature that tells an inlined State ctor from an out-of-line one.
ZERO_BLOCK = {0x80, 0x84, 0x88, 0x8C, 0x90, 0x94}


# --------------------------------------------------------------------------- image

def load_image(path):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from xbe import Xbe

    return Xbe(path)


def text_section(xbe):
    for s in xbe.sections:
        if s.name == ".text":
            return s
    raise SystemExit("no .text section")


def objdump(blob, base):
    """[(va, text)] from objdump, an independent decoder over a byte window."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
        fh.write(blob)
        temp = fh.name
    try:
        out = subprocess.run(["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                              "--adjust-vma=%d" % base, temp],
                             capture_output=True, text=True).stdout
    finally:
        os.unlink(temp)
    insns = []
    for line in out.splitlines():
        if line.count("\t") < 2:
            continue
        head, rest = line.split("\t", 1)
        try:
            va = int(head.strip().rstrip(":"), 16)
        except ValueError:
            continue
        text = re.sub(r"\s+", " ", rest.split("\t")[-1].strip())
        if text and not text.startswith("."):
            insns.append((va, text))
    insns.sort()
    return insns


# --------------------------------------------------------------------------- population

def read_map(path):
    rows = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            rows.append((int(r["address"], 16), r["name"], int(r["size"] or 0)))
    rows.sort()
    return rows


def pad_starts(blob, base, map_starts):
    """Starts of bodies the map does not name, from the image's own `int3` padding.

    A body begins immediately after a run of `0xcc`; the run is what separates two bodies when neither
    has a map entry. Two bytes is the floor, so a filler byte inside a body is not a boundary, and a
    start that lands inside a mapped body (whose size is trustworthy) is dropped.
    """
    spans = sorted(map_starts)
    out = []
    i, n = 0, len(blob)
    while i < n:
        if blob[i] != PAD:
            i += 1
            continue
        j = i
        while j < n and blob[j] == PAD:
            j += 1
        if j - i >= RUN_MIN and j < n:
            va = base + j
            k = bisect.bisect_right(spans, va) - 1
            inside = k >= 0 and spans[k] < va < map_starts[spans[k]][1] + spans[k]
            if not inside and va not in map_starts:
                out.append(va)
        i = j
    return out


def population(xbe, map_rows):
    """[(va, name, size, kind)] sorted, with pad bodies sized to the next start."""
    map_starts = {va: (name, size) for va, name, size in map_rows}
    text = text_section(xbe)
    pads = pad_starts(xbe.read_va(text.va, text.vsize), text.va, map_starts)
    pop = [(va, name, size, "map") for va, name, size in map_rows]
    for va in pads:
        pop.append((va, "-", None, "pad"))
    pop.sort()
    out = []
    for i, (va, name, size, kind) in enumerate(pop):
        if size is None:
            size = (pop[i + 1][0] - va) if i + 1 < len(pop) else 0
        out.append((va, name, size, kind))
    return out, text


# --------------------------------------------------------------------------- edges

def edges(xbe, text, pop):
    """{target: [(site, kind, owner)]} over every section, and the site count by kind."""
    starts = [p[0] for p in pop]
    size_of = {p[0]: p[2] for p in pop}

    def owner(va):
        i = bisect.bisect_right(starts, va) - 1
        if i < 0:
            return None
        a = starts[i]
        size = size_of[a]
        return a if size and va < a + size else None

    inbound = collections.defaultdict(list)
    counts = collections.Counter()
    for sec in xbe.sections:
        blob = xbe.read_va(sec.va, sec.vsize)
        n = len(blob)
        for i in range(n - 3):
            v = blob[i] | (blob[i + 1] << 8) | (blob[i + 2] << 16) | (blob[i + 3] << 24)
            if v in size_of:
                where = "data" if sec.name != text.name else "imm"
                inbound[v].append((sec.va + i, where, owner(sec.va + i)))
                counts[where] += 1
        if sec.name != text.name:
            continue
        for i in range(n - 5):
            op = blob[i]
            if op not in (0xE8, 0xE9):
                continue
            rel = struct.unpack_from("<i", blob, i + 1)[0]
            tgt = (sec.va + i + 5 + rel) & 0xFFFFFFFF
            if tgt in size_of:
                kind = "call" if op == 0xE8 else "jmp"
                inbound[tgt].append((sec.va + i, kind, owner(sec.va + i)))
                counts[kind] += 1
    return inbound, counts


def external(inbound, target):
    """Edges into `target` from outside its own body, and how many of those are self-references."""
    own = [e for e in inbound.get(target, []) if e[2] == target]
    outside = [e for e in inbound.get(target, []) if e[2] != target]
    return outside, len(own)


def unreachable(pop, inbound, entry):
    """Starts with no outside edge, plus every body whose callers are all in that set (fixpoint)."""
    starts = [p[0] for p in pop]
    seeds = [a for a in starts if a != entry and not external(inbound, a)[0]]
    dead = set(seeds)
    changed = True
    while changed:
        changed = False
        for a in starts:
            if a in dead:
                continue
            srcs = external(inbound, a)[0]
            if srcs and all(s[2] is not None and s[2] in dead for s in srcs):
                dead.add(a)
                changed = True
    return dead


def runs_of(dead, pop):
    runs, cur = [], []
    prev = None
    for va, name, size, kind in pop:
        if va not in dead:
            continue
        if prev is not None and va - prev <= RUN_GAP:
            cur.append(va)
        else:
            if cur:
                runs.append(cur)
            cur = [va]
        prev = va
    if cur:
        runs.append(cur)
    runs.sort(key=lambda r: (-len(r), r[0]))
    return runs


# --------------------------------------------------------------------------- family

def family_rows(xbe, pop, inbound, dead):
    """Per-body: what it installs, what it calls as a base, the block it zeroes, and its edges."""
    starts = [p[0] for p in pop]
    size_of = {p[0]: p[2] for p in pop}
    rows = []
    for va, label in FAMILY:
        i = bisect.bisect_right(starts, va) - 1
        kind = "map" if i >= 0 and starts[i] == va else "pad"
        size = size_of.get(va, 0)
        insns = objdump(xbe.read_va(va, min(max(size, 16), BODY_WINDOW)), va)
        installs = [m.group(1) for _, t in insns for m in [TABLE_STORE.search(t)] if m]
        base = None
        for _, t in insns:
            m = BASE_CALL.search(t)
            if m and int(m.group(1), 16) != va:
                base = m.group(1)
                break
        zeros = [int(m.group(1), 16) for _, t in insns for m in [ZERO_STORE.search(t)] if m]
        outside, own = external(inbound, va)
        detail = "installs=%s;base=%s;start=%s;size=0x%x;in=%d;self=%d;zeros=%s;verdict=%s" % (
            "/".join(installs) or "-", base or "-", kind, size, len(outside), own,
            ("the +0x80 block" if ZERO_BLOCK <= set(zeros) else "%d stores" % len(zeros)),
            "unreachable" if va in dead else "reachable")
        ev = "; ".join("0x%06x %s from %s" % (s, k, ("0x%06x" % o) if o else "outside-any-body")
                       for s, k, o in outside[:4]) or "no outside edge in any section"
        rows.append({"kind": "family", "at": "0x%06x" % va, "name": label, "detail": detail,
                     "evidence": ev})
    return rows


# --------------------------------------------------------------------------- measure

def measure(xbe_path):
    xbe = load_image(xbe_path)
    map_rows = read_map(MAP_PATH)
    pop, text = population(xbe, map_rows)
    inbound, counts = edges(xbe, text, pop)
    dead = unreachable(pop, inbound, xbe.entry)
    runs = runs_of(dead, pop)
    named = [p for p in pop if p[0] in dead and not p[1].startswith("FUN_")]
    pad_n = sum(1 for p in pop if p[0] in dead and p[3] == "pad")
    aligned = sum(1 for p in pop if p[0] in dead and p[0] % 4 == 0)

    rows = [{"kind": "census", "at": "-", "name": "-",
             "detail": "population=%d;map=%d;pad=%d;dead_map=%d;dead_pad=%d;seeds=%d;closure=%d;"
                       "named=%d;aligned=%d;runs=%d;entry=0x%06x"
                       % (len(pop), len(map_rows), len(pop) - len(map_rows),
                          sum(1 for p in pop if p[0] in dead and p[3] == "map"),
                          sum(1 for p in pop if p[0] in dead and p[3] == "pad"),
                          len([p for p in pop if p[0] in dead and not external(inbound, p[0])[0]]),
                          len(dead), len(named), aligned, len(runs), xbe.entry or 0),
             "evidence": "edges: %d call, %d jmp, %d data dword, %d immediate"
                         % (counts["call"], counts["jmp"], counts["data"], counts["imm"])}]
    for run in runs:
        rows.append({"kind": "run", "at": "0x%06x" % run[0], "name": "-",
                     "detail": "bodies=%d;aligned=%d;extent=0x%06x-0x%06x"
                               % (len(run), sum(1 for a in run if a % 4 == 0), run[0], run[-1]),
                     "evidence": "unreachable bodies within 0x%x of each other" % RUN_GAP})
    rows.extend(family_rows(xbe, pop, inbound, dead))
    for va, name, size, kind in pop:
        if va not in dead:
            continue
        outside, own = external(inbound, va)
        rows.append({"kind": "body", "at": "0x%06x" % va, "name": name,
                     "detail": "start=%s size=0x%x in=%d self=%d aligned=%s"
                               % (kind, size, len(outside), own, "yes" if va % 4 == 0 else "no"),
                     "evidence": "; ".join("0x%06x %s from %s" % (s, k, ("0x%06x" % o) if o else "outside-any-body")
                                           for s, k, o in outside[:3]) or "no edge in any section"})
    return {"rows": rows, "dead": dead, "pop": pop, "runs": runs, "inbound": inbound, "xbe": xbe}


# --------------------------------------------------------------------------- render

def _counts(rows):
    census = next(r for r in rows if r["kind"] == "census")
    out = {}
    for part in census["detail"].split(";"):
        key, _, value = part.partition("=")
        out[key.strip()] = int(value) if value.strip().lstrip("-").isdigit() else value.strip()
    return out


def render(rows):
    c = _counts(rows)
    family = [r for r in rows if r["kind"] == "family"]
    runs = [r for r in rows if r["kind"] == "run"]
    fam = {}
    for r in family:
        d = {}
        for part in r["detail"].split(";"):
            if "=" in part:
                k, _, v = part.partition("=")
                d[k] = v
        fam[r["at"]] = d

    reg = fam.get("0x03af60", {})
    copy = fam.get("0x03c430", {})
    reg_base = fam.get("0x03a5f0", {})
    copy_base = fam.get("0x03bd30", {})
    sib = fam.get("0x03c3d0", {})

    lines = []
    lines.append("**%d of the image's %d bodies are unreachable, and the copy the previous note nominated is one "
                 "of them.** The population is the %d bodies `symbols/xbox_functions.csv` names plus the %d that "
                 "start at the image's own `int3` padding and carry no map entry; an edge is a relative `E8` call, "
                 "a relative `E9` jump, or a dword in any section equal to the start. %d bodies have no outside "
                 "edge at all, and %d more lose theirs once bodies whose callers are all in that set are followed "
                 "to a fixpoint -- **%d in total (%d the map names, %d found only by padding), in %d runs, %d of "
                 "them carrying a real name and %d starting on a 4-byte boundary.** Both facts about a start are "
                 "kept per row: a body the map names at an odd address that nothing references may be a definition "
                 "Ghidra invented over data, and this measurement does not separate that from dead code."
                 % (c["closure"], c["population"], c["map"], c["pad"], c["seeds"],
                    c["closure"] - c["seeds"], c["closure"], c["dead_map"], c["dead_pad"],
                    c["runs"], c["named"], c["aligned"]))
    lines.append("")
    if len(runs):
        biggest = runs[0]
        bd = {}
        for part in biggest["detail"].split(";"):
            k, _, v = part.partition("=")
            bd[k] = v
        lines.append("**The unreachable bodies are not scattered: they form %d runs, and the biggest single run is "
                     "%s bodies over %s, of which %s start on a 4-byte boundary.** A run is a set of unreachable "
                     "bodies no more than `0x400` apart; a run that starts and ends inside live code is what a "
                     "dropped translation unit looks like, and a run whose starts are mostly off a 4-byte boundary "
                     "is more likely the map's own definitions over data -- which is why each run row carries both "
                     "numbers."
                     % (c["runs"], bd.get("bodies", "?"), bd.get("extent", "?"), bd.get("aligned", "?")))
        lines.append("")
    lines.append("**The nominated pair is a second compilation of the class constructor family, with the State "
                 "constructor inlined into it.** Read from the bodies themselves:")
    lines.append("")
    lines.append("| body | what it is | installs | base call | zeroes | outside edges |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for r in family:
        d = fam[r["at"]]
        lines.append("| `%s` | %s | `%s` | `%s` | %s | %s |"
                     % (r["at"], r["name"], d.get("installs", "-"), d.get("base", "-"),
                        d.get("zeros", "-"), d.get("in", "?")))
    lines.append("")
    lines.append("Two things follow from the table. **First, the copy installs the same class table as the "
                 "registration ctor (`%s`) and sets the same flags, but its base call goes to `%s`, which installs "
                 "`%s` -- the Struct table -- while the live ctor's base call goes to `%s` and installs `%s`, the "
                 "State table.** The block each body clears separates the two readings: the copy carries %s, the "
                 "sibling at `%s` -- which installs the State table itself -- carries %s, the live State ctor "
                 "carries %s, and the live Class ctor carries %s. A Class ctor that clears the block its State base "
                 "clears, with the State table install dropped, is the State ctor inlined into it, and then the base "
                 "call can only go to the class below State."
                 % (copy.get("installs", "?"), copy.get("base", "?"), copy_base.get("installs", "?"),
                    reg.get("base", "?"), reg_base.get("installs", "?"),
                    copy.get("zeros", "?"), "`0x%06x`" % FAMILY[4][0], sib.get("zeros", "?"),
                    reg_base.get("zeros", "?"), reg.get("zeros", "?")))
    lines.append("")
    lines.append("**Second, nothing references it: the copy has %s outside edges and its base has %s, and both of "
                 "those base edges come from inside the same unreachable set** (`0x03c3f2` in the State copy, "
                 "`0x03c45c` in the Class copy). So the reference graph is closed under the family: no call, no jump "
                 "and no dword in any section enters it. It is therefore not a copy the linker kept *for* a "
                 "reference -- nothing can call it. Whether it survived as an unswept body or lost a COMDAT pick is "
                 "not decidable from the image; that it is unreachable is."
                 % (copy.get("in", "?"), copy_base.get("in", "?")))
    lines.append("")
    return "\n".join(lines)


def splice(path, block):
    with open(path) as fh:
        text = fh.read()
    begin, end = BLOCK_MARK
    i = text.find(begin)
    j = text.find(end)
    if i < 0 or j < 0:
        sys.exit("FAIL markers not found in %s" % path)
    return text[:i + len(begin)] + "\n" + block + text[j:]


# --------------------------------------------------------------------------- entry points

def check():
    """Synthetic bytes and a synthetic graph: the padding rule, the fixpoint and the render contract."""
    n = 0
    # a body starts after >=2 padding bytes; a single 0xcc does not make a boundary
    blob = bytes([0x55, 0xCC, 0xCC, 0x8B, 0xEC, 0xCC, 0x90, 0x33, 0xC0])
    assert pad_starts(blob, 0x1000, {}) == [0x1003], pad_starts(blob, 0x1000, {})
    assert pad_starts(bytes([0x55, 0xCC, 0x8B, 0xEC]), 0x1000, {}) == []
    n += 2
    # a start inside a mapped body is not a start, and a mapped start is not reported twice
    blob2 = bytes([0x55, 0xCC, 0xCC, 0x8B, 0xEC])
    assert pad_starts(blob2, 0x1000, {0x1000: ("f", 5)}) == []
    assert pad_starts(blob2, 0x1000, {0x1003: ("g", 2)}) == []
    n += 2
    # an edge into a body from itself is not an outside edge
    inbound = {0x1000: [(0x1002, "call", 0x1000), (0x2000, "call", 0x2000)]}
    pop = [(0x1000, "a", 0x20, "map"), (0x2000, "b", 0x20, "map")]
    assert external(inbound, 0x1000) == ([(0x2000, "call", 0x2000)], 1)
    n += 1
    assert unreachable(pop, inbound, 0x9999) == {0x1000, 0x2000}, unreachable(pop, inbound, 0x9999)
    n += 1
    # a body with a live caller stays alive, and the entry point is never a seed
    inbound2 = {0x1000: [(0x0500, "call", None)]}
    assert unreachable(pop, inbound2, 0x1000) == {0x2000}, unreachable(pop, inbound2, 0x1000)
    n += 1
    # the fixpoint is transitive: a dead caller kills its callee, which kills the callee's callee --
    # unless an owner outside any known body points at it, which is unknown and therefore left alive
    pop3 = [(0x1000, "a", 0x20, "map"), (0x2000, "b", 0x20, "map"), (0x3000, "c", 0x20, "map")]
    inbound3 = {0x3000: [(0x2000, "call", 0x2000)], 0x1000: [(0x3000, "call", 0x3000)]}
    assert unreachable(pop3, inbound3, 0x9999) == {0x1000, 0x2000, 0x3000}
    inbound3[0x1000].append((0x0500, "call", None))
    assert unreachable(pop3, inbound3, 0x9999) == {0x2000, 0x3000}
    n += 2
    # the family regexes
    m = TABLE_STORE.search("mov DWORD PTR [esi],0x4d4870")
    assert m and m.group(1) == "0x4d4870"
    assert ZERO_STORE.search("mov WORD PTR [esi+0x94],di").group(1) == "0x94"
    assert BASE_CALL.search("call 0x3bd30").group(1) == "0x3bd30"
    n += 3
    # render over a hand-made census: the counts come from the rows, so --check-doc can rebuild it
    rows = [{"kind": "census", "at": "-", "name": "-",
             "detail": "population=10;map=9;pad=1;dead_map=3;dead_pad=1;seeds=3;closure=4;named=1;aligned=2;"
                       "runs=1;entry=0x11000",
             "evidence": "-"},
            {"kind": "run", "at": "0x1000", "name": "-",
             "detail": "bodies=4;aligned=2;extent=0x1000-0x1050", "evidence": "-"}]
    for va, label in FAMILY:
        rows.append({"kind": "family", "at": "0x%06x" % va, "name": label,
             "detail": "installs=0x4d4870;base=0x3bd30;start=map;size=0x10;in=0;self=0;zeros=the +0x80 block;verdict=unreachable",
                     "evidence": "-"})
    text = render(rows)
    assert "4 of the image's 10 bodies" in text, text[:200]
    assert "0x4d4870" in text and "0x1000-0x1050" in text and "(3 the map names, 1 found only" in text
    n += 3
    print("native-unreachable: %d synthetic assertions pass" % n)
    return 0


def trace(measured, va):
    xbe = measured["xbe"]
    starts = [p[0] for p in measured["pop"]]
    size_of = {p[0]: p[1] for p in measured["pop"]}
    i = bisect.bisect_right(starts, va) - 1
    if i < 0 or starts[i] != va:
        start = starts[i] if i >= 0 else None
        print("0x%06x is not a body start; body holding it: %s"
              % (va, ("0x%06x (%s)" % (start, "map" if start else "?")) if start else "none"))
        return 1
    outside, own = external(measured["inbound"], va)
    print("body 0x%06x size 0x%x kind=%s dead=%s self-edges=%d"
          % (va, size_of[va], next(p[3] for p in measured["pop"] if p[0] == va),
             va in measured["dead"], own))
    for s, k, o in outside:
        print("  in: 0x%06x %-4s from %s" % (s, k, ("0x%06x" % o) if o else "outside-any-body"))
    if not outside:
        print("  in: none")
    for a, t in objdump(xbe.read_va(va, min(max(size_of[va], 16), BODY_WINDOW)), va)[:60]:
        print("  %06x: %s" % (a, t))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out", default=CSV_PATH)
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--trace", help="one body start, hex")
    args = ap.parse_args(argv)

    if args.check:
        return check()

    if args.xbe:
        measured = measure(args.xbe)
        if args.out:
            with open(args.out, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\r\n")
                w.writeheader()
                w.writerows([{k: (r.get(k) if r.get(k) is not None else "-") for k in COLUMNS}
                             for r in measured["rows"]])
            print("wrote %s (%d rows)" % (args.out, len(measured["rows"])))
        if args.trace:
            return trace(measured, int(args.trace, 16))
        rows = [{k: (r.get(k) if r.get(k) is not None else "-") for k in COLUMNS}
                for r in measured["rows"]]
        block = render(rows)
        if args.report:
            print(block)
        if args.write_doc:
            new = splice(args.write_doc, block)
            with open(args.write_doc, "w") as fh:
                fh.write(new)
            print("wrote %s" % args.write_doc)
        return 0

    with open(CSV_PATH, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if args.write_doc:
        new = splice(args.write_doc, render(rows))
        with open(args.write_doc, "w") as fh:
            fh.write(new)
        print("wrote %s" % args.write_doc)
        return 0
    if args.check_doc:
        want = render(rows).strip()
        with open(args.check_doc) as fh:
            text = fh.read()
        i = text.find(BLOCK_MARK[0])
        j = text.find(BLOCK_MARK[1])
        if i < 0 or j < 0:
            sys.exit("FAIL markers not found in %s" % args.check_doc)
        have = text[i + len(BLOCK_MARK[0]):j].strip()
        if have != want:
            sys.exit("FAIL %s: generated block does not match the committed CSV" % args.check_doc)
        print("native-unreachable: %s matches the committed CSV" % args.check_doc)
        return 0
    if args.report:
        print(render(rows))
        return 0
    sys.exit("nothing to do: give --xbe, --check, --check-doc or --report")


if __name__ == "__main__":
    sys.exit(main())
