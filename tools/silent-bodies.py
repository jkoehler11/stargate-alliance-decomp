#!/usr/bin/env python3
"""The 427 bodies nothing in the image names: read at their own bytes, not at another slot index.

`tools/gap-body-families.py` leaves **427 bodies (52,485 bytes) silent** -- no `.text` dword spells
their address as an operand or a table word, and the head census's four channels (call, jump, push
immediate, pointer outside `.text`) had already named none of them. Every earlier round tried another
*reference* channel and came back empty, so this read stops looking for a reference and reads the four
things a body says about itself:

  * **where it sits** -- the padding before it and the body that ends at its first byte. MSVC pads
    between functions, so an unpadded head whose predecessor's last instruction is not a terminator is
    not a function at all: it is the rest of the named function above it, and the map's extent stopped
    short. That is a fact about the *map*, not a missing name.
  * **whether it is alone** -- the same bytes at another address. A helper the compiler emitted once per
    translation unit is byte-identical at each of them, and those copies are the map's duplicate
    population under another name.
  * **what it says** -- the literal it pushes: an assert message is not a symbol, but it does say which
    subsystem's code this is.
  * **what it does** -- decoded to its terminator, with the callees the map names and the fields it
    touches.

And it reads the one reference channel still unread, the **relative** table: a `switch` table holds
offsets from the table's own base, never absolute addresses, so a body whose only reference is such an
entry would look silent to every absolute scan. The read counts the entries that resolve onto the
population and the ones that sit in a run of at least three consecutive entries resolving into one
function, because one resolving dword among unrelated bytes is the base rate -- 497 of the map's 16,935
rows are exactly that.

The population is read from `inventory/gap_body_families.csv` (its `silent` rows), so the census moves
with the residue.

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/silent-bodies.py \\
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/silent-bodies.py --report
    python3 tools/silent-bodies.py --write-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/silent-bodies.py --check-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/silent-bodies.py --check        # synthetic, no dump, writes nothing

Addresses, names and counts only; no image content is written.
"""
import argparse
import bisect
import collections
import csv
import hashlib
import importlib.util
import os
import re
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYMBOLS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
FAMILIES = os.path.join(ROOT, "inventory", "gap_body_families.csv")
HEADS = os.path.join(ROOT, "inventory", "gap_heads.csv")
BODIES = os.path.join(ROOT, "inventory", "gap_body_files.csv")
OUT = os.path.join(ROOT, "inventory", "silent_bodies.csv")
DOC = os.path.join(ROOT, "docs", "systems", "CLASS_REGISTRATION.md")
DOC_A = ("<!-- generated:silent-bodies -->", "<!-- /generated:silent-bodies -->")

ROW = ("body_va", "size", "prologue", "terminator", "boundary", "prev_va", "prev_name", "prev_kind",
       "prev_size", "prev_last", "verdict", "owner", "copies", "copy_modules", "callees", "callee_names",
       "string_head", "note")
CENSUS = ("bodies", "bytes", "continuation", "continuation_bytes", "copy", "copy_bytes", "unique",
          "unique_bytes", "copy_groups", "max_copies", "padded", "unpadded", "tiny13", "with_callee",
          "without_callee",
          "with_string", "reach_outside", "map_rows", "map_reach_outside", "rel_hits", "rel_in_run",
          "map_rel_control", "rel_sites")

OBJ_LINE = re.compile(r"^\s*([0-9a-f]+):\t([0-9a-f ]+?)\s*\t(.*)$")
TERM = ("ret", "jmp", "int3", "hlt", "iret")
REL_LIMIT = 0x40000          # a switch entry's offset is bounded by the function's own size
REL_WINDOW = 0x1000          # entries of one table resolve into one function
MIN_COPY = 8                 # below this, identical bytes are not evidence of a copied body
MAX_NOTE = 60


def read_csv(path):
    if not path or not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_symbols(path=SYMBOLS):
    names, sizes = {}, {}
    for row in read_csv(path):
        try:
            va = int(row["address"], 16)
        except (KeyError, ValueError):
            continue
        names[va] = row.get("name") or ""
        try:
            sizes[va] = int(row.get("size") or 0)
        except ValueError:
            sizes[va] = 0
    return names, sizes


def extents(names, sizes, head_rows):
    """{va: (kind, size)} -- every body the two artifacts give an extent, map rows first."""
    out = {}
    for va, n in sizes.items():
        if n:
            out[va] = ("row", n)
    for r in head_rows:
        if r.get("row") != "head":
            continue
        try:
            va = int(r["head_va"], 16)
            n = int(r.get("size") or 0)
        except (KeyError, ValueError):
            continue
        if n:
            out.setdefault(va, ("head", n))
    return out


def decode(blob, base):
    """[(va, length, text)] from one objdump pass over `blob`, decoded from its first byte.

    A body is decoded from its own head, never from a linear sweep of the section: objdump wraps an
    instruction longer than seven bytes onto a second line, so a sweep's byte count is short and every
    later boundary in the section drifts.
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
        out.append((int(m.group(1), 16), len(m.group(2).split()), m.group(3)))
    return out


def outside_occurrences(image, want):
    """{value: count} for the wanted dwords, over every section but `.text`.

    A pointer in a table lives outside the code section; this is the census's own reach channel and the
    control the population is compared against.
    """
    hits = collections.Counter()
    if not want:
        return hits
    for s in image.sections:
        if s.name == ".text":
            continue
        blob = image.data[s.foff:s.foff + min(s.fsize, s.vsize)]
        for i in range(len(blob) - 3):
            v = int.from_bytes(blob[i:i + 4], "little")
            if v in want:
                hits[v] += 1
    return hits


def relative_targets(blob, base):
    """{target: [site_vas]} for section-relative tables: an entry is the target minus its own site."""
    out = collections.defaultdict(list)
    for i in range(len(blob) - 3):
        v = struct.unpack_from("<i", blob, i)[0]
        if 0 < v < REL_LIMIT:
            out[base + i + v].append(base + i)
    return out


def site_targets(rel):
    """{site: target} over every resolving entry -- one lookup for the run test."""
    out = {}
    for target, sites in rel.items():
        for site in sites:
            out[site] = target
    return out


def in_run(site_map, target, need=3):
    """Is a resolving entry part of a table: `need` consecutive entries (four bytes apart) resolving
    into the same function? One dword that happens to resolve is the base rate, not a table."""
    for site, got in site_map.items():
        if abs(got - target) > REL_WINDOW:
            continue
        chain = 1
        step = site + 4
        while chain < need and abs(site_map.get(step, -REL_LIMIT) - target) <= REL_WINDOW:
            chain += 1
            step += 4
        if chain >= need:
            return True
    return False


def bytes_at(image, va, size):
    b = image.read_va(va, size)
    return b if b is not None and len(b) == size else b""


def note_for(insns_of_body):
    """What a copy does, from its own instructions -- only the shapes the copies show.

    The float rules read the SSE mnemonics; the two structural rules read the paired `mov` forms, and
    they are counted separately because a `movss` store is an `xmm` destination and a plain `mov` one is
    not -- counting both in one column read every float body as a struct copy.
    """
    texts = [t for _va, _n, t in insns_of_body]
    if not texts or len(texts) > 45:
        return ""
    low = " ".join(texts)
    if "fsqrt" in low and "divss" in low:
        return "three-float normalize (sqrt + divide)"
    mul = low.count("mulss")
    add = low.count("addss")
    sub = low.count("subss")
    sse_stores = len(re.findall(r"movss\s+DWORD PTR \[", low))
    if mul >= 6 and sub >= 3 and sse_stores >= 3:
        return "three-float cross product"
    if sub >= 3 and add == 0 and sse_stores >= 3:
        return "three-float subtract"
    if mul >= 3 and add >= 2 and sse_stores == 0:
        return "three-float dot product"
    if add >= 3 and mul >= 3 and sse_stores >= 3:
        return "three-float add scaled"
    if mul >= 3 and sse_stores >= 3 and add == 0 and sub == 0:
        return "three-float scale in place"
    if re.search(r"call\s+DWORD PTR \[[a-z0-9]+\+0x[0-9a-f]+\]", low):
        return "virtual-slot wrapper"
    loads = len(re.findall(r"\bmov\s+[a-z0-9]+,DWORD PTR \[e[a-z]+\+?0?x?[0-9a-f]*\]", low))
    stores = len(re.findall(r"\bmov\s+DWORD PTR \[e[a-z]+\+?0?x?[0-9a-f]*\],[a-z0-9]+", low))
    if loads >= 4 and stores >= 4 and abs(loads - stores) <= 4:
        return "struct copy (%d dwords)" % stores
    if stores >= 3 and "xor" in low and loads == 0:
        return "zero-init (%d fields)" % stores
    if len(texts) == 1 and texts[0].split()[0] in TERM:
        return "bare terminator"
    return ""


def module_of(name):
    m = re.match(r"^([A-Za-z][A-Za-z0-9]*)_([A-Za-z0-9_]+)$", name or "")
    return m.group(1) if m else ""


def measure(image, families, names, sizes, head_rows, joined):
    pop = [r for r in families if (r.get("family") or "") == "silent"]
    ext = extents(names, sizes, head_rows)
    starts = sorted(ext)
    text = None
    for s in image.sections:
        if s.name == ".text":
            text = s
            break
    if text is None:
        sys.exit("silent-bodies: no .text section in the image")
    tblob = image.data[text.foff:text.foff + min(text.fsize, text.vsize)]
    rel = relative_targets(tblob, text.va)
    sites = site_targets(rel)

    pop_va = [int(r["body_va"], 16) for r in pop]
    want = set(pop_va) | set(va for va, n in sizes.items() if n)
    outside = outside_occurrences(image, want)

    # Byte-identical copies over every body the artifacts know, so a copy's twin may be a map row.
    byhash = collections.defaultdict(list)
    for va in sorted(ext):
        b = bytes_at(image, va, ext[va][1])
        if b:
            byhash[(ext[va][1], hashlib.sha1(b).hexdigest())].append(va)

    head_at = {}
    for r in head_rows:
        if r.get("row") == "head":
            try:
                head_at[int(r["head_va"], 16)] = r
            except (KeyError, ValueError):
                continue

    rows = []
    cen = collections.Counter()
    groups = set()
    for r in pop:
        va = int(r["body_va"], 16)
        size = int(r["size"] or 0)
        b = bytes_at(image, va, size)
        head = head_at.get(va, {})
        padded = (head.get("boundary") or "").startswith("pad:")
        i = bisect.bisect_left(starts, va) - 1
        prev_va = starts[i] if i >= 0 else 0
        prev = ext.get(prev_va)
        prev_last, continuation = "", False
        if prev and prev_va + prev[1] == va:
            prev_insns = decode(bytes_at(image, prev_va, prev[1]), prev_va)
            prev_last = (prev_insns[-1][2] if prev_insns else "")[:MAX_NOTE]
            if prev_insns and prev_insns[-1][0] + prev_insns[-1][1] == va \
                    and prev_insns[-1][2].split()[0] not in TERM:
                continuation = True
        copies = []
        if b and size >= MIN_COPY:
            copies = [c for c in byhash.get((size, hashlib.sha1(b).hexdigest()), []) if c != va]
        group = tuple(sorted([va] + copies))
        modules = sorted(set(m for m in (module_of(names.get(c, "")) for c in group) if m))
        if copies:
            groups.add(group)
        callees, callee_names = 0, []
        for k in range(len(b) - 5):
            if b[k] == 0xE8:
                tgt = va + k + 5 + struct.unpack_from("<i", b, k + 1)[0]
                if tgt in names:
                    callees += 1
                    if names[tgt] and len(callee_names) < 2:
                        callee_names.append(names[tgt])
        body_insns = decode(b, va)
        mg = joined.get(va, {})
        string = (mg.get("string_head") or "").replace("|", "/")[:48]
        if continuation:
            verdict = "continuation"
            cen["continuation"] += 1
            cen["continuation_bytes"] += size
        elif copies:
            verdict = "copy"
            cen["copy"] += 1
            cen["copy_bytes"] += size
        else:
            verdict = "unique"
            cen["unique"] += 1
            cen["unique_bytes"] += size
        cen["bodies"] += 1
        cen["bytes"] += size
        cen["padded" if padded else "unpadded"] += 1
        if size <= 13:
            cen["tiny13"] += 1
        cen["with_callee" if callees else "without_callee"] += 1
        if string:
            cen["with_string"] += 1
        if outside.get(va):
            cen["reach_outside"] += 1
        if rel.get(va):
            cen["rel_hits"] += 1
            if in_run(sites, va):
                cen["rel_in_run"] += 1
        rows.append({
            "body_va": "0x%08x" % va, "size": str(size),
            "prologue": r.get("prologue") or "", "terminator": r.get("terminator") or "",
            "boundary": (head.get("boundary") or ""),
            "prev_va": "0x%08x" % prev_va if prev_va else "",
            "prev_name": names.get(prev_va, "") if prev_va in names else
                        ("" if not prev_va else "0x%08x" % prev_va),
            "prev_kind": prev[0] if prev else "",
            "prev_size": str(prev[1]) if prev else "",
            "prev_last": prev_last,
            "verdict": verdict,
            "owner": ("0x%08x" % prev_va if continuation else ""),
            "copies": str(len(copies) + 1) if copies else "1",
            "copy_modules": " ".join(modules),
            "callees": str(callees), "callee_names": " ".join(callee_names),
            "string_head": string,
            "note": note_for(body_insns) if copies else "",
        })
    cen["copy_groups"] = len(groups)
    cen["max_copies"] = max([int(r["copies"]) for r in rows if r["verdict"] == "copy"] or [1])
    cen["map_rows"] = sum(1 for n in sizes.values() if n)
    cen["map_reach_outside"] = sum(1 for va, n in sizes.items() if n and outside.get(va))
    cen["map_rel_control"] = sum(1 for va, n in sizes.items() if n and rel.get(va))
    cen["rel_sites"] = sum(len(v) for v in rel.values())
    return rows, dict(cen)


def pct(a, b):
    return (100.0 * a / b) if b else 0.0


def render(rows, cen):
    def n(k):
        return int(cen.get(k, 0) or 0)

    lines = []
    lines.append(
        "**%d bodies, %d bytes, and three answers rather than one** -- the residue `tools/gap-body-families.py` "
        "calls silent, read at its own bytes (`inventory/silent_bodies.csv`)." % (n("bodies"), n("bytes")))
    lines.append("")
    lines.append(
        "**%d are the continuation of the named row above them**: no padding between the two and the row's "
        "last instruction is not a terminator, so the extents are one function's code and the map's size "
        "column stops short (%d bytes of it). **%d are a second address of a body the artifacts already "
        "hold** -- %d byte-identical groups of at least %d bytes, from a %d-address body down to pairs -- and "
        "**%d (%d bytes) are unique**: no twin, no padding-free predecessor, and nothing anywhere in the "
        "image that spells their address." % (n("continuation"), n("continuation_bytes"), n("copy"),
                                              n("copy_groups"), MIN_COPY, n("max_copies"), n("unique"),
                                              n("unique_bytes")))
    lines.append("")
    if n("bodies"):
        lines.append(
            "**The population is unreachable by any absolute reference, and the control says the channel "
            "works**: the address of **%d of %d** of the population occurs somewhere outside `.text`, against "
            "**%d of %d** map rows (%.1f%%) -- and the same read finds a resolving section-relative table "
            "entry for **%d** of the population against **%d of %d** map rows (%.1f%%), of which **%d** sit "
            "in a run of three consecutive entries resolving into one function. So a relative `switch` table "
            "is not the missing channel either: one resolving dword among unrelated bytes is the base rate, "
            "not a table." % (
                n("reach_outside"), n("bodies"), n("map_reach_outside"), n("map_rows"),
                pct(n("map_reach_outside"), n("map_rows")), n("rel_hits"), n("map_rel_control"),
                n("map_rows"), pct(n("map_rel_control"), n("map_rows")), n("rel_in_run")))
    lines.append("")
    cont = sorted((r for r in rows if r["verdict"] == "continuation"), key=lambda r: -int(r["size"]))
    if cont:
        lines.append("| the continuation | bytes | the row whose extent stops before it | the row's last instruction |")
        lines.append("|---|---:|---|---|")
        for r in cont:
            lines.append("| `%s` | %s | `%s` %s | `%s` |" % (
                r["body_va"], r["size"], r["owner"],
                "`%s`" % r["prev_name"] if r["prev_name"] else "", r["prev_last"] or "-"))
        lines.append("")
        epilogue = [r for r in cont if r["prev_last"].startswith(("pop ", "add    esp"))]
        rest = [r for r in cont if r not in epilogue]
        biggest = max(rest, key=lambda r: int(r["size"])) if rest else None
        if epilogue:
            lines.append(
                "**%d of the %d stop the row on an epilogue sequence** (`pop ebp`, `pop ebx`, `add esp,N`) "
                "and the continuation opens on the `ret` that closes it, so the row is short by a terminator "
                "and the head rule read the leftover as a body. The other %d stop mid-expression, and the "
                "largest of those is `%s`, **%s bytes**, continuing a %s-byte `%s` row." % (
                    len(epilogue), len(cont), len(rest),
                    biggest["body_va"] if biggest else "-", biggest["size"] if biggest else "-",
                    biggest["prev_size"] if biggest else "-",
                    biggest["prev_name"] if biggest else "-"))
            lines.append("")
    copies = sorted((r for r in rows if r["verdict"] == "copy"),
                    key=lambda r: (-int(r["copies"]), r["body_va"]))
    if copies:
        groups = collections.OrderedDict()
        for r in copies:
            groups.setdefault((int(r["size"]), r["note"]), []).append(r)
        lines.append("| bytes | what the body does | bodies here | addresses each | one of them |")
        lines.append("|---:|---|---:|---:|---|")
        for (size, note), members in sorted(groups.items(), key=lambda kv: (-int(kv[1][0]["copies"]), -kv[0][0])):
            lines.append("| %d | %s | %d | %s | `%s` |" % (
                size, note or "not one of the shapes read", len(members), members[0]["copies"],
                members[0]["body_va"]))
        lines.append("")
        shared = collections.Counter(r["note"] for r in copies if r["note"])
        if shared:
            lines.append(
                "**The copied shapes are one vector-math header**: %s -- the float3 operators a `static` "
                "inline in a header compiles once per object file and inlines at every call site, which is "
                "exactly why the out-of-line copies are the bodies nothing reaches." % (
                    ", ".join("%s %d" % (k, v) for k, v in shared.most_common())))
            lines.append("")
    uniq = [r for r in rows if r["verdict"] == "unique"]
    if uniq:
        shapes = collections.Counter(r["prologue"] for r in uniq if r["prologue"])
        top = ", ".join("`%s` %d" % (k, v) for k, v in shapes.most_common(8))
        lines.append(
            "**The %d unique bodies are helper-shaped, not class-shaped**: %s, and %d of the %d population "
            "are 13 bytes or shorter. **%d call at least one body the map names** and %d call none, so the "
            "larger part of the population reaches the engine's own functions while no table reaches it. "
            "**%d push a printable literal**, and it is an assert message rather than a symbol -- "
            "`[LIPSinc]: LIPSincShutdownEditor`, `Unpopulated box!`, `FlyingPathNode:` -- so the literal "
            "channel places the subsystem and never the name, which is why nothing here is added to the "
            "symbol map." % (
                n("unique"), top or "no shape", n("tiny13"), n("bodies"), n("with_callee"),
                n("without_callee"), n("with_string")))
        lines.append("")
    lines.append(
        "**What this does not do.** It writes no name: a body nothing references has no owner to take one "
        "from, and the copies are `FUN_` on both sides. It does not correct the **%d** short rows either -- "
        "that moves **%d** bytes of extents and every artifact cut from them, so it is its own bundle -- and "
        "the interface-held bodies stay a separate count in `docs/UNKNOWNS.md` U40." % (
            n("continuation"), n("continuation_bytes")))
    return "\n".join(lines)


def splice_doc(path, block, marks):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(marks[0]), text.find(marks[1])
    if i < 0 or j < 0:
        return None
    return text[:i] + marks[0] + "\n" + block + "\n" + text[j:]


def read_artifact(path):
    rows, cen = [], None
    for r in read_csv(path):
        if (r.get("verdict") or "") == "census":
            cen = {}
            for k in CENSUS:
                try:
                    cen[k] = int(r.get(k) or 0)
                except ValueError:
                    cen[k] = 0
        else:
            rows.append(r)
    return rows, cen


def write_artifact(rows, cen, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(ROW) + list(CENSUS), lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(dict((k, r.get(k, "")) for k in ROW))
        w.writerow(dict([("body_va", "census"), ("verdict", "census")]
                        + [(k, cen.get(k, 0)) for k in CENSUS]))


def self_check():
    """Synthetic: the three verdicts, the reach and relative channels, the copy shapes, the render and
    both artifact round trips."""
    bad = []
    names = {0x2000: "Core_UnFunc", 0x2200: "Engine_KPhysic"}
    sizes = {0x2000: 20, 0x2200: 16}
    heads = [{"row": "head", "head_va": "0x00001014", "size": "6", "boundary": "pad:3"},
             {"row": "head", "head_va": "0x00001030", "size": "2", "boundary": "after:row"},
             {"row": "head", "head_va": "0x00001050", "size": "9", "boundary": "pad:5"}]
    ext = extents(names, sizes, heads)
    if ext.get(0x1014) != ("head", 6) or ext.get(0x2000) != ("row", 20):
        bad.append("a head and a row both carry an extent")

    class S:
        def __init__(self, index, va, blob, name):
            self.index, self.va, self.foff, self.fsize, self.vsize = index, va, 0, len(blob), len(blob)
            self.name = name

    image = type("I", (), {})()
    blob = struct.pack("<III", 0x2000, 0x9999, 0x1030)
    image.sections = [S(0, 0x1000, b"", ".text"), S(1, 0x9000, blob, ".data")]
    image.data = blob
    hits = outside_occurrences(image, {0x2000, 0x1014})
    if hits.get(0x2000) != 1 or hits.get(0x1014):
        bad.append("an outside-.text dword is counted per address")
    rel = relative_targets(struct.pack("<iiii", 0x100, 0xfc, 0xf8, 0x1f0000), 0x100)
    if 0x200 not in rel or 0x1f0100 in rel:
        bad.append("a section-relative entry resolves to base + value, and an out-of-range one does not")
    sites = site_targets(rel)
    if in_run(sites, 0x200) is not True:
        bad.append("three consecutive entries resolving into one function are a run")
    if in_run(site_targets({0x300: [0x1000]}), 0x300) is not False:
        bad.append("a single entry is not a run")
    if "normalize" not in note_for([(0, 4, "fld DWORD PTR [esp]"), (4, 2, "fsqrt"),
                                    (6, 6, "divss xmm1,xmm0")]):
        bad.append("a sqrt-and-divide body is a vector normalize")
    note = note_for([(0, 2, "mov eax,DWORD PTR [ecx+0x4]"), (3, 2, "mov DWORD PTR [edx+0x4],eax"),
                     (6, 2, "mov eax,DWORD PTR [ecx+0x8]"), (9, 2, "mov DWORD PTR [edx+0x8],eax"),
                     (12, 2, "mov eax,DWORD PTR [ecx+0xc]"), (15, 2, "mov DWORD PTR [edx+0xc],eax"),
                     (18, 2, "mov eax,DWORD PTR [ecx+0x10]"), (21, 2, "mov DWORD PTR [edx+0x10],eax")])
    if "struct copy" not in note:
        bad.append("paired loads and stores are a struct copy")
    rows = [
        {"body_va": "0x00001014", "size": "6", "prologue": "bare-ret", "terminator": "ret",
         "boundary": "pad:3", "prev_va": "0x00001000", "prev_name": "Core_UnFunc", "prev_kind": "row",
         "prev_size": "20", "prev_last": "pop ebp", "verdict": "continuation", "owner": "0x00001000", "copies": "1",
         "copy_modules": "", "callees": "0", "callee_names": "", "string_head": "",
         "note": ""},
        {"body_va": "0x00001030", "size": "12", "prologue": "self-return", "terminator": "ret",
         "boundary": "after:row", "prev_va": "0x00001014", "prev_name": "", "prev_kind": "head",
         "prev_size": "6", "prev_last": "ret", "verdict": "copy", "owner": "", "copies": "3", "copy_modules": "Core Engine",
         "callees": "0", "callee_names": "", "string_head": "",
         "note": "struct copy (4 dwords)"},
        {"body_va": "0x00001050", "size": "9", "prologue": "arg-load", "terminator": "ret",
         "boundary": "pad:5", "prev_va": "0x00001030", "prev_name": "0x00001030", "prev_kind": "head",
         "prev_size": "12", "prev_last": "ret", "verdict": "unique", "owner": "", "copies": "1", "copy_modules": "",
         "callees": "2", "callee_names": "Core_UnFunc Alloc", "string_head": "..\\Src\\Foo.cpp",
         "note": ""},
    ]
    cen = dict((k, 0) for k in CENSUS)
    cen.update({"bodies": 3, "bytes": 27, "continuation": 1, "continuation_bytes": 6, "copy": 1,
                "copy_bytes": 12, "unique": 1, "unique_bytes": 9, "copy_groups": 1, "max_copies": 3,
                "padded": 2,
                "unpadded": 1, "tiny13": 2, "with_callee": 1, "without_callee": 2, "with_string": 1,
                "reach_outside": 0, "map_rows": 2, "map_reach_outside": 1, "rel_hits": 0,
                "rel_in_run": 0, "map_rel_control": 1, "rel_sites": 2})
    block = render(rows, cen)
    if "| `0x00001014` | 6 | `0x00001000` `Core_UnFunc` | `pop ebp` |" not in block:
        bad.append("the continuation table names the row and its last instruction")
    if "| 12 | struct copy (4 dwords) | 1 | 3 | `0x00001030` |" not in block:
        bad.append("the copy table carries its bytes, address count and shape")
    if "`arg-load` 1" not in block:
        bad.append("the unique shapes are counted from the rows")
    path = os.path.join(tempfile.mkdtemp(), "silent.csv")
    try:
        write_artifact(rows, cen, path)
        back, back_cen = read_artifact(path)
        if len(back) != 3 or back_cen["continuation"] != 1 or back_cen["copy_groups"] != 1:
            bad.append("the artifact round-trips its rows and its census row")
        if render(back, back_cen) != block:
            bad.append("the render reproduces from the written artifact")
    finally:
        os.unlink(path)
        os.rmdir(os.path.dirname(path))
    if set(cen) - set(CENSUS):
        bad.append("every accumulated key is written")
    if bad:
        print("FAIL silent-bodies: " + "; ".join(bad))
        return 1
    print("ok   silent-bodies: the three verdicts, the reach and relative channels, the two copy shapes, "
          "the render and both artifact round trips")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.check_doc:
        rows, cen = read_artifact(OUT)
        if not rows or not cen:
            print("FAIL silent-bodies: %s is missing or has no census row" % OUT)
            return 1
        block = render(rows, cen)
        text = open(args.check_doc, encoding="utf-8").read()
        i, j = text.find(DOC_A[0]), text.find(DOC_A[1])
        if i < 0 or j < 0:
            print("FAIL silent-bodies: %s has no %s block" % (args.check_doc, DOC_A[0]))
            return 1
        if text[i:j + len(DOC_A[1])] != DOC_A[0] + "\n" + block + "\n" + DOC_A[1]:
            print("FAIL silent-bodies: %s's block does not reproduce from the committed CSV"
                  % args.check_doc)
            return 1
        print("ok   silent-bodies: %s reproduces from the committed CSV" % args.check_doc)
        return 0
    if args.report or args.write_doc:
        rows, cen = read_artifact(OUT)
        if not rows or not cen:
            print("FAIL silent-bodies: %s is missing or has no census row" % OUT)
            return 1
        block = render(rows, cen)
        if args.report:
            print(block)
            return 0
        new = splice_doc(args.write_doc, block, DOC_A)
        if new is None:
            print("FAIL silent-bodies: %s has no %s markers" % (args.write_doc, DOC_A[0]))
            return 1
        if new != open(args.write_doc, encoding="utf-8").read():
            open(args.write_doc, "w", encoding="utf-8").write(new)
            print("wrote %s (%s)" % (args.write_doc, DOC_A[0]))
        return 0
    if not args.xbe:
        ap.error("--xbe is required to read the image (or use --report/--check-doc/--check)")
    spec = importlib.util.spec_from_file_location("xbe", os.path.join(ROOT, "tools", "xbe.py"))
    xbemod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(xbemod)
    image = xbemod.Xbe(args.xbe)
    names, sizes = load_symbols()
    joined = {}
    for r in read_csv(BODIES):
        try:
            joined[int(r["body_va"], 16)] = r
        except (KeyError, ValueError):
            continue
    rows, cen = measure(image, read_csv(FAMILIES), names, sizes, read_csv(HEADS), joined)
    if len(rows) != cen.get("bodies"):
        print("FAIL silent-bodies: %d rows, the census says %d" % (len(rows), cen.get("bodies")))
        return 1
    out = os.path.join(args.out, os.path.basename(OUT)) if args.out else OUT
    write_artifact(rows, cen, out)
    print("wrote %s (%d rows: %d continuation, %d copy, %d unique)" % (
        out, len(rows), cen.get("continuation", 0), cen.get("copy", 0), cen.get("unique", 0)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
