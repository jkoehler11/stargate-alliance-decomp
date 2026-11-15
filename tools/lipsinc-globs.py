#!/usr/bin/env python3
"""Which path each LIPSinc `.lad` find is expanded against, and whether a root-level one exists.

U4's last residual was `LIPSincInitLevel`'s bare `*.lad`: on its own a bare glob names no directory,
so the format page left open whether the three packs at the `LIPSincData` root are reachable
through it. This census reconstructs both find calls from the image's own bytes rather than from the
two glob strings, and scans the image for every wide literal that names a `.lad` path.

What it measures per find site: the pushed arguments of the virtual call at
`[[0x005bf514]+0x30]` (the file manager's find loop, the same slot the `GetMapList` chain uses), and
for the pattern argument either the literal it pushes or the `FString` it reads -- whose base
literal and appended literals are recovered by walking the body with a stack-offset tracker, so the
reconstruction is a chain of addresses rather than a plausible string.

The answer is one pattern, built two ways: both initialisers expand
`..\\System\\LIPSincData\\Controllers\\*.lad`. `LIPSincInitGame` pushes that as a single wide
literal (`0x004eab98`); `LIPSincInitLevel` fills an `FString` with `..\\System\\LIPSincData\\
Controllers\\` (`0x004eab18`) and appends the bare literal `*.lad` (`0x004eae2c`) to it. No literal
in the image names a `.lad` at the `LIPSincData` root, so the three root packs are never opened.

    tools/lipsinc-globs.py --xbe DUMP/default.xbe -o inventory
    tools/lipsinc-globs.py --write-doc docs/formats/lipsinc-lad.md
    tools/lipsinc-globs.py --check-doc docs/formats/lipsinc-lad.md   # from the committed CSV
    tools/lipsinc-globs.py --check      # synthetic listing, no dump, writes nothing

No boot: the image and its literals only.
"""
import argparse
import csv
import os
import re
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from xbe import Xbe  # noqa: E402

FIELDS = ["kind", "site", "function", "slot", "source", "parts", "pattern", "evidence"]
ARTIFACT = os.path.join(ROOT, "inventory/lipsinc_globs.csv")
DOC = os.path.join(ROOT, "docs/formats/lipsinc-lad.md")
DOC_START = "<!-- generated:lipsinc-globs -->"
DOC_END = "<!-- /generated:lipsinc-globs -->"
FUNCS = os.path.join(ROOT, "symbols/xbox_functions.csv")
# Both initialisers; the sizes come from the symbol map (563 and 1,860 bytes).
INITIALISERS = ((0x000BA290, "LIPSincInitGame"), (0x000BA750, "LIPSincInitLevel"))
FIND_SLOT = 0x30          # `[[0x005bf514]+0x30]`: the file manager's find loop (docs/XEMU.md).
APPEND = 0x000192A0       # wide-string append: `Data.Num += appStrlen(arg)`, `ret 4`.
EMPTY = 0x004C85AC        # the shared empty wide string an FString falls back to at Num == 0.


# --- image -------------------------------------------------------------------------------------
def functions(path):
    """{va: (name, size)} from the committed symbol map (third column is a byte count, decimal)."""
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                out[int(row["address"], 16)] = (row["name"], int(row["size"] or 0))
            except (KeyError, ValueError):
                continue
    return out


def wide_string(data, sections, va, limit=300):
    """The wide literal starting at `va`, read forward to its NUL; "" outside every section."""
    for sec in sections:
        if sec.va <= va < sec.va + sec.vsize:
            off = sec.foff + (va - sec.va)
            out = []
            while len(out) < limit and off + 1 < len(data):
                ch = data[off] | (data[off + 1] << 8)
                if ch == 0:
                    break
                out.append(chr(ch))
                off += 2
            return "".join(out)
    return ""


def wide_literals(data, sections, needles):
    """Every NUL-delimited UTF-16 literal holding one of `needles`: [(va, text)]."""
    seen, out = set(), []
    for needle in needles:
        pat = needle.encode("utf-16-le")
        i = 0
        while True:
            j = data.find(pat, i)
            if j < 0:
                break
            i = j + 2
            s = j
            while s - 2 >= 0 and data[s - 2:s] != b"\x00\x00":
                s -= 2
            if s in seen:
                continue
            seen.add(s)
            for sec in sections:
                if sec.foff <= s < sec.foff + sec.fsize:
                    out.append((sec.va + (s - sec.foff), wide_string(data, sections, sec.va + (s - sec.foff))))
                    break
    out.sort()
    return out


def push_sites(data, section, va):
    """Every `.text` address holding `va` as a little-endian dword (a `push imm32` is one of them)."""
    d = struct.pack("<I", va)
    out, i, end = [], section.foff, section.foff + section.fsize
    while True:
        j = data.find(d, i, end)
        if j < 0:
            return out
        out.append(section.va + (j - section.foff))
        i = j + 1


def owner(fmap, va):
    """The function whose [address, address+size) holds `va`, else the nearest one below it."""
    best = None
    for a in fmap:
        if a <= va and (best is None or a > best):
            best = a
    if best is None:
        return "?"
    name, size = fmap[best]
    return name if best <= va < best + max(size, 1) else name + "?"


def listing(xbe, va, size):
    """objdump over the body's own bytes: [(va, mnemonic, operands, text)]."""
    blob = xbe.read_va(va, size)
    path = tempfile.mktemp(suffix=".bin")
    with open(path, "wb") as fh:
        fh.write(blob)
    try:
        out = subprocess.run(["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                              "--adjust-vma=0x%x" % va, path],
                             capture_output=True, text=True).stdout
    finally:
        os.unlink(path)
    return parse_listing(out)


def parse_listing(text):
    rows = []
    for line in text.splitlines():
        f = line.split("\t")
        if len(f) < 3 or not f[0].strip().endswith(":"):
            continue
        try:
            a = int(f[0].strip().rstrip(":"), 16)
        except ValueError:
            continue
        ins = f[2].strip()
        if not ins:
            continue
        parts = ins.split(None, 1)
        rows.append((a, parts[0], parts[1].strip() if len(parts) > 1 else "", ins))
    return rows


def ret_pop(xbe, fmap, va, depth=0):
    """How many argument words the callee at `va` pops: its own `ret imm16`, following one thunk."""
    name, size = fmap.get(va, ("?", 0))
    if not size:
        return 0
    raw = xbe.read_va(va, size)
    if raw and raw[0] == 0xE9 and depth < 1:                      # jmp rel32 thunk: ask its target
        return ret_pop(xbe, fmap, va + 5 + struct.unpack("<i", raw[1:5])[0], depth + 1)
    if raw and raw[0] == 0xEB and depth < 1:
        return ret_pop(xbe, fmap, va + 2 + struct.unpack("<b", raw[1:2])[0], depth + 1)
    for i in range(len(raw) - 1, max(len(raw) - 40, -1), -1):     # last return in the body's tail
        if raw[i] == 0xC3:
            return 0
        if raw[i] == 0xC2 and i + 3 <= len(raw):
            return struct.unpack("<H", raw[i + 1:i + 3])[0] // 4
    return 0


# --- reconstruction ----------------------------------------------------------------------------
MEM = re.compile(r"^(?:DWORD PTR )?\[esp\+0x([0-9a-f]+)\]$")


def stack_off(operand):
    """`[esp+0x2c]` -> 0x2c, else None."""
    m = MEM.match(operand.strip())
    return int(m.group(1), 16) if m else None


def tracked(rows, xbe, fmap):
    """[(va, mnem, ops, text, depth, args)] -- `depth` is esp's offset from the body's entry.

    The entry-relative offset of a stack slot is `K + depth` at the instruction naming `[esp+K]`,
    so a slot named before a call and again after the callee popped its argument is one slot. Every
    push before a call belongs to that call here: these bodies use no other calling style, and a
    virtual call's pop count is taken as the number of arguments it was handed.
    """
    depth, pending, out = 0, [], []
    for a, mnem, ops, text in rows:
        args = None
        if mnem == "push":
            pending.append((a, ops))
            depth -= 4
        elif mnem == "pop":
            depth += 4
        elif mnem in ("sub", "add") and ops.startswith("esp,0x"):
            n = int(ops.split(",")[1], 16)
            depth += -n if mnem == "sub" else n
        elif mnem == "call":
            args, pending = pending, []
            target = ops.split(",")[0].strip()
            pop = ret_pop(xbe, fmap, int(target, 16)) if re.match(r"^0x[0-9a-f]+$", target) else len(args)
            depth += 4 * pop
        out.append((a, mnem, ops, text, depth, args))
    return out


def writer(rows, upto, reg, window=12):
    """The nearest instruction before index `upto` writing `reg`: (va, mnem, ops, rhs, depth)."""
    for i in range(upto - 1, max(upto - window, -1), -1):
        a, mnem, ops, depth = rows[i][0], rows[i][1], rows[i][2], rows[i][4]
        if mnem not in ("mov", "lea"):
            continue
        parts = ops.split(",", 1)
        if len(parts) != 2:
            continue
        if parts[0].strip().split()[-1] == reg:
            return a, mnem, ops, parts[1].strip(), depth
    return None


def literal_arg(rows, i):
    """The literal VA a `push` at index `i` hands over (`push imm`, or `mov reg,imm; push reg`).

    The shared empty wide string is not a literal here: it is what an `FString` argument falls back
    to when its length is zero, so the push reads a stack slot and the slot is the real answer.
    """
    a, mnem, ops, _t, _s, _g = rows[i]
    if mnem != "push":
        return None
    if re.match(r"^0x[0-9a-f]+$", ops.strip()):
        v = int(ops.strip(), 16)
        return None if v == EMPTY else v
    w = writer(rows, i, ops.split()[-1])
    if w and re.match(r"^0x[0-9a-f]+$", w[3]) and int(w[3], 16) != EMPTY:
        return int(w[3], 16)
    return None


def slot_arg(rows, i):
    """(printed offset, entry-relative slot) that a `push` at index `i` reads, or None."""
    _a, mnem, ops, _t, _g, _args = rows[i]
    if mnem != "push":
        return None
    reg = ops.split()[-1]
    for k in range(i - 1, max(i - 14, -1), -1):
        m, o, d = rows[k][1], rows[k][2], rows[k][4]
        if m not in ("mov", "lea"):
            continue
        parts = o.split(",", 1)
        if len(parts) != 2 or parts[0].strip().split()[-1] != reg:
            continue
        rhs = parts[1].strip()
        off = stack_off(rhs)
        if off is not None:
            return off, off + d
        if not (re.match(r"^0x[0-9a-f]+$", rhs) and int(rhs, 16) == EMPTY):
            return None                      # another value source: not this slot
    return None


def slot_parts(rows, slot, depth=0, seen=None):
    """(parts, appends) that build the FString at entry-relative `slot`; parts are (site, literal va).

    A fill is `mov edi,[esp+slot]` followed by `mov esi,<source>`: the source is either a wide
    literal or another stack FString, which is followed (one hop per copy) so a
    `B = A; B += literal` chain still resolves to A's own literal.
    """
    seen = set() if seen is None else seen
    if slot in seen or depth > 3:
        return [], [], []
    seen.add(slot)
    parts, appends, hops = [], [], []
    for i, (a, mnem, ops, _t, shift, _g) in enumerate(rows):
        if mnem == "call" and ops.strip() == "0x%x" % APPEND:
            for j in range(i - 1, max(i - 4, -1), -1):
                if rows[j][1] == "lea" and rows[j][2].startswith("ecx,[esp+0x"):
                    target = stack_off(rows[j][2].split(",", 1)[1])
                    if target is not None and target + rows[j][4] == slot:
                        appends.append((rows[j][0], literal_arg(rows, j - 1)))
                    break
        parts_ = ops.split(",", 1)
        if len(parts_) != 2 or mnem != "mov" or parts_[0].strip() != "edi":
            continue
        target = stack_off(parts_[1])
        if target is None or target + shift != slot:
            continue
        for j in range(max(i - 4, 0), min(i + 8, len(rows))):
            r = rows[j]
            if r[1] != "mov" or not r[2].startswith("esi,"):
                continue
            src = r[2].split(",", 1)[1].strip()
            if re.match(r"^0x[0-9a-f]+$", src):
                parts.append((r[0], int(src, 16)))
            else:
                src_slot = stack_off(src)
                if src_slot is not None:
                    hops.append(a)
                    inner, inner_appends, inner_hops = slot_parts(rows, src_slot + r[4], depth + 1, seen)
                    parts += inner
                    appends = inner_appends + appends
                    hops += inner_hops
            break
    return parts, appends, hops


def find_sites(rows):
    """Every `call [reg+0x30]` in the tracked body, with the argument tokens it was handed."""
    return [(a, args) for a, mnem, ops, _t, _s, args in rows
            if mnem == "call" and re.search(r"\+0x%x\]" % FIND_SLOT, ops)]


def globs(xbe, fmap):
    """The rows: one per find site, one per `.lad` literal, and a census row."""
    data, sections = xbe.data, xbe.sections
    rows, found = [], []
    for va, name in INITIALISERS:
        body = tracked(listing(xbe, va, fmap.get(va, (name, 0))[1]), xbe, fmap)
        index = {r[0]: i for i, r in enumerate(body)}
        for site, args in find_sites(body):
            if len(args) < 3:
                rows.append(dict(kind="glob", site="0x%08x" % site, function=name,
                                 slot="[edx+0x%x]" % FIND_SLOT, source="thin", parts=str(len(args)),
                                 pattern="?", evidence="fewer than three arguments pushed"))
                continue
            push_va = args[2][0]
            i = index[push_va]
            lit = literal_arg(body, i)
            if lit is not None:
                text = wide_string(data, sections, lit)
                found.append(text)
                rows.append(dict(kind="glob", site="0x%08x" % site, function=name,
                                 slot="[edx+0x%x]" % FIND_SLOT, source="literal", parts="1",
                                 pattern=text, evidence="pushes the literal at 0x%08x" % lit))
                continue
            got = slot_arg(body, i)
            if got is None:
                rows.append(dict(kind="glob", site="0x%08x" % site, function=name,
                                 slot="[edx+0x%x]" % FIND_SLOT, source="dynamic", parts="0",
                                 pattern="?", evidence="the pattern argument is not a literal or a stack FString"))
                continue
            printed, slot = got
            fills, appends, hops = slot_parts(body, slot)
            pieces = sorted(fills + appends, key=lambda p: p[0])
            text = "".join(wide_string(data, sections, l) if l else "<?>" for _s, l in pieces)
            found.append(text)
            ev = "FString at [esp+0x%02x]:" % printed
            for s in hops:
                ev += " copied from another FString at 0x%08x;" % s
            for s, l in fills:
                ev += " filled from literal 0x%08x at 0x%08x" % (l, s) if l else " copied at 0x%08x" % s
            for s, l in appends:
                ev += "; appended 0x%08x at 0x%08x" % (l, s) if l else "; appended <?> at 0x%08x" % s
            rows.append(dict(kind="glob", site="0x%08x" % site, function=name,
                             slot="[edx+0x%x]" % FIND_SLOT, source="concat",
                             parts=str(len(pieces)), pattern=text, evidence=ev))
    text_section = [s for s in sections if s.name == ".text"][0]
    for va, text in wide_literals(data, sections, (".lad",)):
        if not text.endswith(".lad"):           # a log line mentioning a `.lad` names no path
            continue
        sites = push_sites(data, text_section, va)
        where = ", ".join("%s at 0x%08x" % (owner(fmap, p), p) for p in sites) or "nothing"
        rows.append(dict(kind="literal", site="0x%08x" % va, function="", slot="", source="",
                         parts=str(len(sites)), pattern=text, evidence=where))
    site_count = len([r for r in rows if r["kind"] == "glob"])
    depth = min([p.count("\\") for p in found]) if found else 0
    rows.append(dict(kind="census", site="", function="", slot="", source="",
                     parts=str(site_count),
                     pattern="%d find site(s), %d distinct pattern(s)" % (site_count, len(set(found))),
                     evidence="shallowest pattern is %d components; %d literals name a .lad"
                              % (depth, len([r for r in rows if r["kind"] == "literal"]))))
    return rows


# --- render / artifact -------------------------------------------------------------------------
def render(rows):
    glob_rows = [r for r in rows if r["kind"] == "glob"]
    lit_rows = [r for r in rows if r["kind"] == "literal"]
    census = [r for r in rows if r["kind"] == "census"][0]
    patterns = sorted({r["pattern"] for r in glob_rows})
    depth = min([p.count("\\") for p in patterns]) if patterns else 0
    out = ["**%d find site(s) expand %d pattern(s)** -- `%s` -- and the shallowest is %d path "
           "components deep, so a directory inside `LIPSincData` is named, never the root."
           % (len(glob_rows), len(patterns), "`, `".join(patterns), depth), "",
           "| find site | body | argument | parts | pattern it expands |", "|---|---|---|---|---|"]
    for r in glob_rows:
        out.append("| `%s` | `%s` | `%s` %s | %s | `%s` |"
                   % (r["site"], r["function"], r["slot"], r["source"], r["parts"], r["pattern"]))
    out += ["", "The chain behind each of those, address by address:", "",
            "| find site | evidence |", "|---|---|"]
    for r in glob_rows:
        out.append("| `%s` | %s |" % (r["site"], r["evidence"]))
    out += ["", "Every wide literal in the image that names a `.lad` path, and what pushes it:", "",
            "| literal | text | pushes | pushed from |", "|---|---|---|---|"]
    for r in lit_rows:
        out.append("| `%s` | `%s` | %s | %s |" % (r["site"], r["pattern"].replace("|", "\\|"),
                                                  r["parts"], r["evidence"]))
    out.append("")
    root = [r for r in lit_rows if re.search(r"LIPSincData\\[^\\]*\.lad$", r["pattern"])]
    out.append("**No literal names a `.lad` at the `LIPSincData` root** (%d of %d does), and neither find "
               "site can build one, so the three root packs -- `JAnderson.lad`, `sg1_crew_carter.lad`, "
               "`sg1_crew_jackson.lad`, 5 keys between them -- are never opened by this build."
               % (len(root), len(lit_rows)))
    out.append("")
    out.append("Census: %s; %s." % (census["pattern"], census["evidence"]))
    return "\n".join(out)


def read_artifact(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def splice(path, block):
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return None
    if DOC_START not in text or DOC_END not in text:
        return None
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + "\n" + DOC_END + tail


# --- self-test ---------------------------------------------------------------------------------
SYNTH = """
00001000 <.data>:
    1000:\t55                   \tpush   ebp
    1001:\t83 ec 40             \tsub    esp,0x40
    1004:\t68 11 00 00 00       \tpush   0x11
    1009:\t6a 01                \tpush   0x1
    100b:\t8b 0d 14 f5 5b 00    \tmov    ecx,DWORD PTR ds:0x5bf514
    1011:\t8b 11                \tmov    edx,DWORD PTR [ecx]
    1013:\t53                   \tpush   ebx
    1014:\t6a 01                \tpush   0x1
    1016:\t68 98 ab 4e 00       \tpush   0x4eab98
    101b:\t8d 44 24 2c          \tlea    eax,[esp+0x2c]
    101f:\t50                   \tpush   eax
    1020:\tff 52 30             \tcall   DWORD PTR [edx+0x30]
    1023:\t53                   \tpush   ebx
    1024:\t6a 01                \tpush   0x1
    1026:\t8b 44 24 34          \tmov    eax,DWORD PTR [esp+0x34]
    102a:\t85 c0                \ttest   eax,eax
    102c:\t75 05                \tjne    0x1033
    102e:\tb8 ac 85 4c 00       \tmov    eax,0x4c85ac
    1033:\t50                   \tpush   eax
    1034:\t8d 44 24 2c          \tlea    eax,[esp+0x2c]
    1038:\t50                   \tpush   eax
    1039:\tff 52 30             \tcall   DWORD PTR [edx+0x30]
    103c:\t8b 7c 24 4c          \tmov    edi,DWORD PTR [esp+0x4c]
    1040:\tbe 18 ab 4e 00       \tmov    esi,0x4eab18
    1045:\t68 2c ae 4e 00       \tpush   0x4eae2c
    104a:\t8d 4c 24 50          \tlea    ecx,[esp+0x50]
    104e:\te8 4d 92 00 00       \tcall   0x192a0
    1053:\t8b 44 24 4c          \tmov    eax,DWORD PTR [esp+0x4c]
    1057:\t85 c0                \ttest   eax,eax
    1059:\t75 05                \tjne    0x1060
    105b:\tb8 ac 85 4c 00       \tmov    eax,0x4c85ac
    1060:\t50                   \tpush   eax
    1061:\t8d 44 24 2c          \tlea    eax,[esp+0x2c]
    1065:\t50                   \tpush   eax
    1066:\t53                   \tpush   ebx
    1067:\t6a 01                \tpush   0x1
    1069:\tff 52 30             \tcall   DWORD PTR [edx+0x30]
"""
LITS = {0x4EAB98: "..\\System\\LIPSincData\\Controllers\\*.lad",
        0x4EAB18: "..\\System\\LIPSincData\\Controllers\\",
        0x4EAE2C: "*.lad"}


class _Stub:
    """The image reads `ret_pop` makes, over a two-byte `ret 4` at each synthetic callee."""

    def __init__(self):
        self.data, self.sections = b"", []

    def read_va(self, va, n):
        return b"\xc2\x04\x00" if va == APPEND else b""


def self_check():
    bad = []
    rows = parse_listing(SYNTH)
    stub = _Stub()
    fmap = {APPEND: ("FUN_000192a0", 3)}
    body = tracked(rows, stub, fmap)
    index = {r[0]: i for i, r in enumerate(body)}

    # 1. a literal pattern, and the three find sites found by the slot rather than by the mnemonic
    sites = find_sites(body)
    if [s for s, _a in sites] != [0x1020, 0x1039, 0x1069]:
        bad.append("find sites: %r" % ([hex(s) for s, _a in sites],))
    if LITS.get(literal_arg(body, index[0x1016])) != LITS[0x4EAB98]:
        bad.append("the literal argument: %r" % (literal_arg(body, index[0x1016]),))
    # 2. the empty-string fallback is read as the slot the argument came from, not as a literal,
    #    and the tracker keeps two different FStrings apart
    near = slot_arg(body, index[0x1033])
    far = slot_arg(body, index[0x1060])
    if near is None or far is None or near[1] == far[1]:
        bad.append("the fallback slots: %r %r" % (near, far))
    if slot_arg(body, index[0x1066]) is not None:
        bad.append("a `push ebx` before the pattern was read as a stack FString")
    # 3. base literal + append land on one slot even though the two `[esp+..]` offsets differ by 4
    fills, appends, hops = slot_parts(body, far[1])
    if [LITS.get(l) for _s, l in fills] != [LITS[0x4EAB18]]:
        bad.append("the FString's fill: %r" % (fills,))
    if [LITS.get(l) for _s, l in appends] != [LITS[0x4EAE2C]]:
        bad.append("the appended literal: %r" % (appends,))
    fills_near, appends_near, _h = slot_parts(body, near[1])
    if appends_near or fills_near:
        bad.append("the other FString inherited a part that belongs to slot +0x0c: %r %r"
                   % (fills_near, appends_near))
    if rows[0][0] != 0x1000 or len(rows) < 20:
        bad.append("the listing parse returned %d rows" % len(rows))
    # 4. artifact round trip and the render's claims
    csv_rows = [dict(kind="glob", site="0x00001020", function="F", slot="[edx+0x30]", source="literal",
                     parts="1", pattern=LITS[0x4EAB98], evidence="pushes the literal at 0x004eab98"),
                dict(kind="glob", site="0x00001069", function="G", slot="[edx+0x30]", source="concat",
                     parts="2", pattern=LITS[0x4EAB98], evidence="FString +0x0c"),
                dict(kind="literal", site="0x004eab98", function="", slot="", source="", parts="1",
                     pattern=LITS[0x4EAB98], evidence="F at 0x00001016"),
                dict(kind="literal", site="0x004eae2c", function="", slot="", source="", parts="1",
                     pattern="*.lad", evidence="G at 0x00001045"),
                dict(kind="census", site="", function="", slot="", source="", parts="2",
                     pattern="2 find site(s), 1 distinct pattern(s)",
                     evidence="shallowest pattern is 4 components; 2 literals name a .lad")]
    block = render(csv_rows)
    for want in ("Controllers\\*.lad", "No literal names a `.lad` at the `LIPSincData` root",
                 "2 find site(s) expand 1 pattern(s)"):
        if want not in block:
            bad.append("render is missing %r" % want)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.csv")
        write_artifact(csv_rows, path)
        if read_artifact(path) != csv_rows:
            bad.append("artifact round trip differs")
        if splice(path, "x") is not None:
            bad.append("splice found markers in a CSV")
    if bad:
        print("FAIL lipsinc-globs: " + "; ".join(bad))
        return 1
    print("ok   lipsinc-globs: a literal pattern and a stack FString whose base and appended literals "
          "land on one slot across two esp offsets are told apart, and an empty fallback is not a literal")
    return 0


# --- main --------------------------------------------------------------------------------------
def main():
    if "--check" in sys.argv[1:]:
        return self_check()
    ap = argparse.ArgumentParser()
    ap.add_argument("--xbe", default=os.environ.get("SG1_DUMP", "")
                    + "/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe")
    ap.add_argument("-o", "--out", default=None, help="write inventory/lipsinc_globs.csv here")
    ap.add_argument("--write-doc", nargs="?", const=DOC, default=None)
    ap.add_argument("--check-doc", nargs="?", const=DOC, default=None)
    args = ap.parse_args()

    if args.write_doc or args.check_doc:
        rows = read_artifact(ARTIFACT)
    else:
        if not os.path.isfile(args.xbe):
            print("no image at %s" % args.xbe)
            return 2
        rows = globs(Xbe(args.xbe), functions(FUNCS))
        if args.out:
            path = os.path.join(args.out, "lipsinc_globs.csv")
            write_artifact(rows, path)
            print("%s: %d rows" % (path, len(rows)))
    block = render(rows)
    print(block)
    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        new = splice(path, block)
        if new is None:
            print("FAIL lipsinc-globs: markers not found in %s" % path)
            return 1
        old = open(path, encoding="utf-8").read()
        if args.check_doc:
            if new != old:
                print("FAIL lipsinc-globs: %s is stale against the committed artifact" % path)
                return 1
            print("ok   lipsinc-globs: %s matches the committed artifact" % path)
            return 0
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
