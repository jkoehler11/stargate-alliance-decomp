#!/usr/bin/env python3
"""The bodies nothing names, censused as families -- against the one reference channel the census cannot see.

`tools/gap-heads.py` names a head by four channels, and all four point *into* the head from outside the
code: an `E8` call, an `E9` jump, a `68` push and a dword **outside `.text`**. That last qualifier is the
census's own choice -- a pointer in a table looks like that -- and it is also a blind spot, because `.text`
carries addresses too: a jump table laid behind a `switch`, a table of call addresses laid behind a
dispatch, and a `mov eax,<fn>` that hands a function pointer to a caller all spell a body's address with a
dword that never leaves the section.

So this read takes the population the census calls *named by no channel* and asks two questions:

  * **is the body referenced from inside `.text`**, and if so by what -- an instruction that carries the
    address as an operand, or a data word in the section (a table)? The site's owner is read from the
    function map's extents, so the answer is `the body <name> loads this address` or `the table inside
    <name> holds it`.
  * **what is the body a family of**, when nothing reaches it at all: the prologue shape the head rule
    already classifies, its size and terminator, the string a per-body read found in it
    (`inventory/gap_body_files.csv`), and the module of the nearest named row.

One family answers the naming question the rest cannot: the generated walker's keys are
`int[AU]<Class>exec<Func>` names, so the table states the class, and the copy takes `<Class>_lookup`
(numbered when two copies bind the same class). The derivation is a column of this artifact and also a
row of the symbol map: `--apply-symbols` writes the 199 rows the map never had (the map has no extent over a
gap head, so an insert is not a rename) and `--check-symbols` is the gate that keeps them there -- see
`--report`'s block in `docs/systems/CLASS_REGISTRATION.md`.

The controls are what makes the second question a measurement: the same `.text` dword read over the map's
own 16,xxx rows (a population whose names are known) says how often code takes its own body's address by
operand and how often a table holds it, and a random gap byte's address is the base rate that a reference
has to beat.

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/gap-body-families.py \\
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/gap-body-families.py --report
    python3 tools/gap-body-families.py --write-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/gap-body-families.py --check-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/gap-body-families.py --apply-symbols    # insert a symbols/xbox_functions.csv row per name
    python3 tools/gap-body-families.py --check-symbols   # every derived name is a map row (dump-free, in ci)
    python3 tools/gap-body-families.py --check          # synthetic, no dump, writes nothing

Addresses, names, string heads and counts only; no package or image content is written.
"""
import argparse
import bisect
import collections
import csv
import importlib.util
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

SYMBOLS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
HEADS = os.path.join(ROOT, "inventory", "gap_heads.csv")
BODIES = os.path.join(ROOT, "inventory", "gap_body_files.csv")
OUT = os.path.join(ROOT, "inventory", "gap_body_families.csv")
# The add's own record. The census re-read after the add no longer sees these bodies (they are map rows),
# so the derivation would leave the repository with the population -- this file is what the gate reads.
WALKERS = os.path.join(ROOT, "inventory", "gap_walker_names.csv")
WALK = ("body_va", "size", "table_va", "derived_name")
DOC = os.path.join(ROOT, "docs", "systems", "CLASS_REGISTRATION.md")
DOC_A = ("<!-- generated:gap-body-families -->", "<!-- /generated:gap-body-families -->")

BODY = ("body_va", "size", "prologue", "terminator", "string_head", "text_sites", "site_kind",
        "owner_kind", "owner_va", "owner_name", "owner_module", "slot_dest", "table_key", "table_va",
        "family", "near_module", "derived_name")
CENSUS = ("decoded", "unnamed", "unnamed_bytes", "islands_unnamed", "slot_movs", "slot_dests",
          "slot_run", "slot_lo", "slot_hi", "walkers", "walkers_silent", "walker_tables",
          "walkers_dup", "walker_entries", "walker_mixed", "walker_classes", "walker_unclassed",
          "derived_named", "derived_suffixed", "derived_map_clash", "with_text", "with_operand", "with_table", "operand_in_row",
          "table_in_row", "operand_in_head", "operand_in_gap", "table_in_head", "table_in_gap", "silent",
          "map_rows", "map_text", "map_operand", "map_table", "map_self_operand",
          "base_n", "base_hit", "mod_operand", "fam_silent_bytes", "thirteen_byte")

# An objdump listing line: `  4d1aa8:\t68 a8 1a 4e 00     \tpush   0x4e1aa8`.
OBJ_LINE = re.compile(r"^\s*([0-9a-f]+):\t([0-9a-f ]+?)\s*\t(.*)$")
TOKEN = re.compile(r"^([A-Za-z][A-Za-z0-9]*)_([A-Za-z0-9_]+)$")
# A global function-pointer slot: `mov DWORD PTR ds:0x630f34,0x11420`.
DEST = re.compile(r"DWORD PTR ds:(0x[0-9a-f]+),")
# An intrinsic-name key: `intA<Class>exec<Func>` or `intU<Class>exec<Func>` -- the letter says whether the
# class is an actor, and the class is what follows it.
CLASS_NAME = re.compile(r"int[AU](.+?)exec")
MAX_HEAD = 48
CENSUS_TEXT = ("mod_operand", "slot_lo", "slot_hi")
BASE_RATE = 2000


def read_csv(path):
    if not path or not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_symbols(path):
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


class Bodies:
    """Body extents (map rows and decoded gap heads) and the body a site falls inside."""

    def __init__(self, extents):
        self.extents = dict(extents)
        self.starts = sorted(self.extents)

    def at(self, va):
        i = bisect.bisect_right(self.starts, va) - 1
        if i < 0:
            return None
        start = self.starts[i]
        return start if start <= va < start + self.extents[start] else None


class Insns:
    """{instruction_va: (length, text)} -- the decoder's instruction boundaries, for operand ownership."""

    def __init__(self, table):
        self.table = dict(table)
        self.starts = sorted(self.table)

    def at(self, site):
        i = bisect.bisect_right(self.starts, site) - 1
        if i < 0:
            return None
        va = self.starts[i]
        n, text = self.table[va]
        return (va, n, text) if va <= site < va + n else None


def site_kind(insn, value):
    """`operand` when an instruction covers the site and spells the value, else `table`.

    An occurrence inside another instruction's displacement bytes is not a reference the code makes --
    it is a value that happens to sit there -- so it is the `table` bucket, not the operand one.
    """
    if insn is None:
        return "table"
    return "operand" if ("0x%x" % value) in insn[2] else "table"


def family_of(kinds):
    """The strongest channel a body has, in the order the reading trusts them."""
    for kind in (("operand", "row"), ("operand", "head"), ("operand", "gap"), ("table", "row"),
                 ("table", "head"), ("table", "gap")):
        if kind in kinds:
            return "%s-in-%s" % kind
    return "silent"


def module_of(name, modules):
    """The module a `<Module>_<File>` row belongs to, or '' -- the placement vote's own token."""
    m = TOKEN.match(name or "")
    if not m:
        return ""
    for known in modules:
        if m.group(1).lower() == known.lower():
            return known
    return ""


def objdump_bin():
    exe = shutil.which("objdump")
    if not exe:
        sys.exit("gap-body-families: objdump not found (the operand read needs binutils)")
    return exe


def sweep(image, index):
    """{instruction_va: (length, text)} from one linear objdump sweep of one section."""
    exe = objdump_bin()
    out = {}
    for s in image.sections:
        if s.index != index:
            continue
        blob = image.data[s.foff:s.foff + min(s.fsize, s.vsize)]
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
            fh.write(blob)
            path = fh.name
        try:
            listing = subprocess.run([exe, "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                                      "--adjust-vma=0x%x" % s.va, path],
                                     capture_output=True, text=True, check=False).stdout
        finally:
            os.unlink(path)
        for line in listing.splitlines():
            m = OBJ_LINE.match(line)
            if not m or ".byte" in m.group(3):
                continue
            out[int(m.group(1), 16)] = (len(m.group(2).split()), m.group(3))
    return out


def dword_sites(blob, base, want):
    """{value: [site_vas]} for the wanted values only -- one pass over the section, not one per value."""
    out = collections.defaultdict(list)
    n = len(blob) - 4
    for i in range(n + 1):
        v = int.from_bytes(blob[i:i + 4], "little")
        if v in want:
            out[v].append(base + i)
    return out


def population(head_rows, census):
    """The heads the census itself calls named by no channel, and its own count as the gate."""
    pop = []
    for r in head_rows:
        if r.get("row") != "head" or r.get("verdict") != "body":
            continue
        if any((r.get(k) or "0") not in ("0", "") for k in ("pointers", "imms", "calls", "jumps")):
            continue
        pop.append(r)
    want = census.get("unnamed")
    if want is not None and len(pop) != want:
        sys.exit("gap-body-families: %d bodies named by no channel, the census says %d -- read it again"
                 % (len(pop), want))
    return pop


def measure(image, names, sizes, head_rows, joined, modules):
    """The whole read: the population, the `.text` dword channel, the families, and both controls."""
    census_row = [r for r in head_rows if r.get("row") == "census"]
    census = {}
    if census_row:
        for key, value in census_row[0].items():
            if key in ("row",) or not value:
                continue
            try:
                census[key] = int(value)
            except ValueError:
                pass
    pop = population(head_rows, census)
    extents = {va: n for va, n in sizes.items() if n}
    for r in head_rows:
        if r.get("row") != "head":
            continue
        try:
            size = int(r.get("size") or 0)
        except ValueError:
            size = 0
        if size:
            extents.setdefault(int(r["head_va"], 16), size)
    bmap = Bodies(extents)

    exe_sec = None
    for s in image.sections:
        if s.executable and s.name == ".text":
            exe_sec = s
            break
    if exe_sec is None:
        sys.exit("gap-body-families: no .text section in the image")
    blob = image.data[exe_sec.foff:exe_sec.foff + min(exe_sec.fsize, exe_sec.vsize)]
    tva = exe_sec.va
    insns = Insns(sweep(image, exe_sec.index))

    pop_va = [int(r["head_va"], 16) for r in pop]
    map_rows = {va: n for va, n in sizes.items() if n}
    sites = dword_sites(blob, tva, set(pop_va) | set(map_rows))

    rows, fam_counts, fam_bytes, mod_counts = [], collections.Counter(), collections.Counter(), collections.Counter()
    walks = {}
    map_starts = sorted(names)
    order = [("operand", "row"), ("operand", "head"), ("operand", "gap"), ("table", "row"),
             ("table", "head"), ("table", "gap")]
    with_text = with_operand = with_table = 0
    for r in pop:
        va = int(r["head_va"], 16)
        size = int(r.get("size") or 0)
        kinds, best, dest = set(), None, None
        for site in sites.get(va, []):
            owner = bmap.at(site)
            if owner is None:
                kind = "gap"
                owner_name, owner_module = "", ""
            elif owner in map_rows:
                kind = "row"
                owner_name, owner_module = names.get(owner, ""), module_of(names.get(owner, ""), modules)
            else:
                kind = "head"
                owner_name, owner_module = "", ""
            what = site_kind(insns.at(site), va)
            kinds.add((what, kind))
            rank = order.index((what, kind))
            if best is None or rank < best[0]:
                insn = insns.at(site)
                m = DEST.search(insn[2]) if insn is not None else None
                best = (rank, what, kind, owner or 0, owner_name, owner_module)
                dest = int(m.group(1), 16) if m else None
        family = family_of(kinds)
        if family != "silent":
            with_text += 1
            mod_counts[(best[5] or "?") if best[2] == "row" else (best[2] or "?")] += 1
        if any(k[0] == "operand" for k in kinds):
            with_operand += 1
        if any(k[0] == "table" for k in kinds):
            with_table += 1
        fam_counts[family] += 1
        fam_bytes[family] += size
        walk = walker_table(image, insns.at(va))
        walks[va] = walk
        j = joined.get(va, {})
        rows.append({
            "body_va": "0x%08x" % va, "size": str(size),
            "prologue": r.get("prologue") or "", "terminator": r.get("close") or "",
            "string_head": (j.get("string_head") or "").replace("|", "/")[:MAX_HEAD],
            "text_sites": str(len(sites.get(va, []))),
            "site_kind": best[1] if best else "",
            "owner_kind": best[2] if best else "",
            "owner_va": "0x%08x" % best[3] if best and best[3] else "",
            "owner_name": best[4] if best else "",
            "owner_module": best[5] if best else "",
            "slot_dest": "0x%08x" % dest if dest else "",
            "table_key": walk[0],
            "table_va": "0x%08x" % walk[1] if walk[1] else "",
            "family": family,
            "near_module": nearest_module(va, names, modules, map_starts),
        })

    # The control: the same read over the map's own rows, which are rows because something names them.
    map_text = map_operand = map_table = map_self = 0
    for va in map_rows:
        got = sites.get(va) or []
        if not got:
            continue
        map_text += 1
        any_op = any(site_kind(insns.at(s), va) == "operand" for s in got)
        any_tb = any(site_kind(insns.at(s), va) == "table" for s in got)
        map_operand += 1 if any_op else 0
        map_table += 1 if any_tb else 0
        # A body that loads its own address is recursion, a `lea` of itself for a jump slot, or a table
        # entry it lays itself -- counted apart so the control is not inflated by self-reference.
        map_self += 1 if any(bmap.at(s) == va for s in got) else 0

    # The base rate: a random byte inside a gap is not a body, and its address is not a reference.
    rng = random.Random(15)
    los = [int(r["gap_lo"], 16) for r in head_rows
           if r.get("row") == "head" and r.get("gap_lo")]
    his = [int(r["gap_hi"], 16) for r in head_rows
           if r.get("row") == "head" and r.get("gap_hi")]
    base_hit = base_n = 0
    if los and his and max(his) > min(los):
        lo, hi = min(los), max(his)
        for _ in range(BASE_RATE):
            v = rng.randrange(lo, hi)
            base_n += 1
            base_hit += 1 if blob.find(struct.pack("<I", v)) >= 0 else 0

    walk = [r for r in rows if r["table_key"]]
    keys = collections.Counter(r["table_key"] for r in walk)
    tbl = {walks[va][1]: walks[va] for va in walks if walks[va][1]}
    taken = {n: v for v, n in names.items() if n}
    pairs = sorted((int(r["body_va"], 16),
                    one_class(tbl.get(int(r["table_va"], 16), (0, 0, 0, {}))[3])) for r in walk)
    derived = derive_names(pairs, taken)
    for r in rows:
        r["derived_name"] = derived.get(int(r["body_va"], 16), "")
    mixed = [g for g, got in tbl.items() if len([c for c in got[3] if c != "?"]) > 1]
    entries = sum(got[2] for got in tbl.values())
    islands = [r for r in head_rows if r.get("row") == "head" and r.get("verdict") == "island"
               and not any((r.get(k) or "0") not in ("0", "")
                           for k in ("pointers", "imms", "calls", "jumps"))]
    decoded = [r for r in head_rows if r.get("row") == "head" and r.get("verdict") in ("body", "island")]
    dests = sorted(int(r["slot_dest"], 16) for r in rows if r["slot_dest"])
    # The longest run of consecutive dword slots: `run` counts slots, so a span of 4 bytes is one slot.
    run = best_lo = best_hi = 0
    if dests:
        lo = prev = dests[0]
        for d in dests[1:]:
            if d - prev == 4:
                prev = d
            else:
                if (prev - lo) // 4 + 1 > run:
                    run, best_lo, best_hi = (prev - lo) // 4 + 1, lo, prev
                lo = prev = d
        if (prev - lo) // 4 + 1 > run:
            run, best_lo, best_hi = (prev - lo) // 4 + 1, lo, prev
    out = {
        "walkers": len(walk), "walkers_silent": sum(1 for r in walk if r["family"] == "silent"),
        "walker_tables": len(tbl), "walkers_dup": sum(1 for r in walk if keys[r["table_key"]] > 1),
        "walker_entries": entries, "walker_mixed": len(mixed),
        "walker_classes": len({one_class(got[3]) for got in tbl.values()} - {""}),
        "walker_unclassed": sum(1 for r in walk if not r["derived_name"]),
        "derived_named": len(derived),
        "derived_suffixed": sum(1 for n in derived.values() if not n.endswith("_lookup")),
        "derived_map_clash": sum(1 for _, cls in pairs if cls and (cls + "_lookup") in taken),
        "slot_movs": len(dests), "slot_dests": len(set(dests)), "slot_run": run,
        "slot_lo": "0x%08x" % best_lo if best_lo else "", "slot_hi": "0x%08x" % best_hi if best_hi else "",
        "unnamed": len(pop), "unnamed_bytes": sum(int(r.get("size") or 0) for r in pop),
        "decoded": len(decoded), "islands_unnamed": len(islands),
        "with_text": with_text, "with_operand": with_operand, "with_table": with_table,
        "operand_in_row": fam_counts.get("operand-in-row", 0),
        "table_in_row": fam_counts.get("table-in-row", 0),
        "operand_in_head": fam_counts.get("operand-in-head", 0),
        "operand_in_gap": fam_counts.get("operand-in-gap", 0),
        "table_in_head": fam_counts.get("table-in-head", 0),
        "table_in_gap": fam_counts.get("table-in-gap", 0),
        "silent": fam_counts.get("silent", 0),
        "map_rows": len(map_rows), "map_text": map_text, "map_operand": map_operand,
        "map_table": map_table, "map_self_operand": map_self,
        "base_n": base_n, "base_hit": base_hit,
        "mod_operand": " ".join("%s=%d" % (k, v) for k, v in mod_counts.most_common(8)),
        "fam_silent_bytes": fam_bytes.get("silent", 0),
        "thirteen_byte": sum(1 for r in pop if int(r.get("size") or 0) <= 13),
    }
    print("%d decoded heads, %d of them bodies nothing names (plus %d islands): %d referenced from inside .text "
          "(%d by an operand, %d by a table word), %d silent of any .text reference; "
          "map control %d of %d rows" % (out["decoded"], out["unnamed"], out["islands_unnamed"], with_text,
                                         with_operand, with_table, out["silent"], map_text, len(map_rows)))
    print("families: " + " ".join("%s=%d" % (k, v) for k, v in sorted(fam_counts.items())))
    return rows, out


def read_va(image, va, n):
    """Bytes at a VA from the section's file image, or b'' when the address is past the file-backed tail."""
    sec = image.section_for(va)
    if sec is None:
        return b""
    off = sec.foff + (va - sec.va)
    if off < 0 or off >= len(image.data):
        return b""
    return image.data[off:off + n]


def wide_at(image, va, limit=40):
    """The UTF-16 string at a VA, cut at its terminator -- the intrinsic name a table entry carries."""
    raw = read_va(image, va, 2 * limit)
    out = []
    for i in range(0, len(raw) - 1, 2):
        ch = raw[i:i + 2]
        if ch == b"\x00\x00":
            break
        try:
            c = ch.decode("utf-16-le")
        except UnicodeDecodeError:
            return ""
        if c < " " or c > "~":
            return ""
        out.append(c)
    return "".join(out) if len(out) >= 4 else ""


def walker_table(image, insn, limit=200):
    """The table a walker walks, read from the `mov eax,ds:<global>` it opens on.

    Returns (first_key, global_va, entries, classes): the pairs are `{UTF-16 name, handler}` records that
    the body compares its argument against, so the entries are the lookup's own keys and the class in them
    (`intA<Class>exec<Func>`) is the class whose natives the table binds.
    """
    if insn is None or "ds:0x" not in insn[2]:
        return "", 0, 0, {}
    m = re.search(r"ds:(0x[0-9a-f]+)", insn[2])
    if not m:
        return "", 0, 0, {}
    va = int(m.group(1), 16)
    first, entries, classes = "", 0, collections.Counter()
    for i in range(0, 8 * limit, 8):
        pair = read_va(image, va + i, 8)
        if len(pair) < 8:
            break
        ptr, handler = struct.unpack("<II", pair)
        if not ptr and not handler:
            break
        entries += 1
        name = wide_at(image, ptr) if ptr > 0x10000 else ""
        if not name:
            continue
        first = first or name
        cm = CLASS_NAME.match(name)
        classes[cm.group(1) if cm else "?"] += 1
    return first, (va if entries else 0), entries, dict(classes)


def one_class(classes):
    """The single class a table's keys name, or '' -- `int[AU]<Class>exec<Func>` in every entry."""
    named = [c for c in classes if c != "?"]
    return named[0] if len(named) == 1 else ""


def derive_names(pairs, taken):
    """{va: name} for the walkers -- `<Class>_lookup`, numbered by address when the base name is taken.

    `pairs` is `[(body_va, class)]` sorted by address and `taken` is `{name: va}` over the map's own rows,
    so a derivation that would collide with a symbol the build already carries is numbered instead of
    overwriting it. The numbering is by address, which makes the whole list reproducible from the artifact.
    """
    out, used = {}, dict(taken)
    for va, cls in pairs:
        if not cls:
            continue
        base, name, k = "%s_lookup" % cls, "%s_lookup" % cls, 1
        while used.get(name, va) != va:
            k += 1
            name = "%s_%d" % (base, k)
        used[name] = va
        out[va] = name
    return out


def write_walker_names(rows, path=None):
    """The add's own record, one row per derived name: address, size, the table it walks, the name.

    A census re-read after the add no longer sees these bodies -- they are map rows now, so they leave the
    gap population -- and a derivation that is only a column of the census would then be rewritten to empty.
    So the add records itself here, and the gate reads this file rather than the artifact's column.
    """
    path = path or WALKERS
    out = {}
    for r in read_csv(path):
        try:
            out[int(r["body_va"], 16)] = {k: r.get(k) or "" for k in WALK}
        except (KeyError, ValueError):
            continue
    for r in rows:
        name = (r.get("derived_name") or "").strip()
        if not name:
            continue
        try:
            va = int(r["body_va"], 16)
        except (KeyError, ValueError):
            continue
        out[va] = {"body_va": r["body_va"], "size": r.get("size") or "", "table_va": r.get("table_va") or "",
                   "derived_name": name}
    if not out:
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(WALK), lineterminator="\r\n")
        w.writeheader()
        for va in sorted(out):
            w.writerow(out[va])
    return len(out)


def walker_population(rows, path=None):
    """The rows the add is checked against: the record, plus anything the artifact derives that is not in it."""
    path = path or WALKERS
    known = {r["body_va"]: r for r in read_csv(path)}
    out = list(known.values())
    for r in rows:
        if (r.get("derived_name") or "").strip() and r.get("body_va") not in known:
            out.append({k: r.get(k) or "" for k in WALK})
    return out


def derived_state(rows, symbols_path=None):
    """How many of the artifact's `derived_name` values are rows of the symbol map.

    Returns `(state, missing, misfiled)`. `present` is a derived name held at its own address with its own
    spelling; `missing` a derivation the map has no row for (the add); `misfiled` an address the map already
    holds under another name, which is a disagreement between two reads and never an insertion. `map_rows`
    and `after` frame the change the add would make to the map's own row count.
    """
    rows_in_map = read_csv(symbols_path or SYMBOLS)
    have, sizes = {}, {}
    for row in rows_in_map:
        try:
            va = int(row["address"], 16)
        except (KeyError, ValueError):
            continue
        have[va] = row.get("name") or ""
        try:
            sizes[va] = int(row.get("size") or 0)
        except ValueError:
            sizes[va] = 0
    state = {"present": 0, "missing": 0, "misfiled": 0, "map_rows": len(have), "after": len(have)}
    missing, misfiled = [], []
    for r in rows:
        name = (r.get("derived_name") or "").strip()
        if not name:
            continue
        try:
            va = int(r["body_va"], 16)
        except (KeyError, ValueError):
            continue
        if va not in have:
            state["missing"] += 1
            missing.append(r)
        elif have[va] == name and (not r.get("size") or sizes.get(va) == int(r["size"])):
            state["present"] += 1
        else:
            state["misfiled"] += 1
            misfiled.append(r)
    state["after"] = len(have) + state["missing"]
    return state, missing, misfiled


def insert_symbols(rows, path=None, missing=None):
    """Write one `address,name,size,module` row per missing derivation, at the census's own address and size.

    The module is empty: the name states a class, not a translation unit. Existing lines are copied verbatim
    -- the map is a published artifact and a re-serialisation would rewrite rows this read never measured --
    so only the inserted lines are new. The result is sorted by address, which is the map's own rule, and a
    derivation whose address the map already holds is left alone.
    """
    path = path or SYMBOLS
    if missing is None:
        _, missing, _ = derived_state(rows, path)
    with open(path, newline="", encoding="utf-8") as fh:
        lines = fh.readlines()
    if not lines:
        raise SystemExit("FAIL gap-body-families: %s is empty" % path)
    nl = "\r\n" if lines[0].endswith("\r\n") else "\n"
    header, body = lines[0], [l for l in lines[1:] if l.strip()]
    held = set()
    for line in body:
        try:
            held.add(int(line.split(",", 1)[0], 16))
        except ValueError:
            continue
    added = []
    for r in sorted(missing, key=lambda r: int(r["body_va"], 16)):
        va = int(r["body_va"], 16)
        if va in held:
            continue
        held.add(va)
        added.append("0x%08x,%s,%d,%s%s" % (va, r["derived_name"], int(r["size"]), "", nl))
    if added:
        merged = sorted([(int(l.split(",", 1)[0], 16), l) for l in body + added])
        with open(path, "w", newline="", encoding="utf-8") as fh:
            fh.write(header)
            for _, line in merged:
                fh.write(line if line.endswith(nl) else line + nl)
    return len(added)


def nearest_module(va, names, modules, starts=None):
    """The module of the nearest named row at or below the body -- placement, never a name."""
    starts = starts if starts is not None else sorted(names)
    i = bisect.bisect_right(starts, va) - 1
    if i < 0:
        return ""
    best = starts[i]
    return module_of(names.get(best, ""), modules) or (names.get(best, "") or "")


def write_artifact(rows, census, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=BODY + CENSUS)
        w.writeheader()
        w.writerows(rows)
        row = {k: "" for k in BODY + CENSUS}
        row["family"] = "census"
        for key, value in census.items():
            row[key] = str(value)
        w.writerow(row)


def read_artifact(path):
    rows, census = [], {}
    for r in read_csv(path):
        if r.get("family") == "census":
            for key in CENSUS:
                if r.get(key) not in (None, ""):
                    census[key] = r[key] if key in CENSUS_TEXT else int(r[key])
        else:
            rows.append({k: r.get(k) or "" for k in BODY})
    return rows, census


FAMILIES = (("operand-in-row", "a named body's instruction loads the address"),
            ("operand-in-head", "an unnamed gap body's instruction loads it"),
            ("operand-in-gap", "an instruction inside no body at all loads it"),
            ("table-in-row", "a data word inside a named body (a jump or dispatch table)"),
            ("table-in-head", "a data word inside an unnamed body"),
            ("table-in-gap", "a data word in a gap and inside no body at all"),
            ("silent", "nothing in `.text` spells the address"))


def render(rows, census, state=None):
    by_fam = collections.defaultdict(list)
    for r in rows:
        by_fam[r["family"]].append(r)
    lines = ["| family | bodies | bytes | what the reference is |", "|---|---|---|---|"]
    for key, what in FAMILIES:
        got = by_fam.get(key, [])
        if not got:
            continue
        lines.append("| `%s` | %d | %d | %s |" % (key, len(got), sum(int(r["size"]) for r in got), what))
    lines.append("")
    if census.get("mod_operand"):
        lines.append("The modules the `operand-in-row` owners belong to: %s (the `?` entries are owners the "
                     "map has no name for, and the two `head`/`gap` entries are the other two families)."
                     % census["mod_operand"])
        lines.append("")
    if census.get("walkers"):
        keys = sorted({r["table_key"] for r in rows if r["table_key"]})
        lines.append(
            "**%d of these bodies are the same generated walker: `mov eax,ds:<global>`, walk the "
            "`{UTF-16 name, handler}` pairs the global points at, compare the argument against each name.** "
            "The tables they walk are the intrinsic-name tables, and the first name in each one says so -- "
            "%s%s -- so the family is a name-keyed intrinsic lookup, one copy per table. The %d tables hold "
            "%d entries and **every one of them names exactly one class** (%d tables state two or more), "
            "which is the name a copy does not carry: the table it walks says whose natives it binds. "
            "%d of the %d copies share a table with another copy, and **%d of the %d copies are named by "
            "nothing at all** -- the largest single family in the residue, and the reason a body count "
            "alone could not explain it."
            % (census["walkers"], ", ".join("`%s`" % k for k in keys[:4]),
               " ..." if len(keys) > 4 else "", census["walker_tables"], census["walker_entries"],
               census.get("walker_mixed", 0), census["walkers_dup"], census["walkers"],
               census["walkers_silent"], census["walkers"]))
        lines.append("")
    if census.get("derived_named"):
        unclassed = census.get("walker_unclassed", 0)
        clash = census.get("derived_map_clash", 0)
        lines.append(
            "**The family is nameable, and the grammar that names it is `int[AU]<Class>exec<Func>` -- the "
            "class follows a letter that says whether it is an actor, and the census read only the `intA` "
            "half.** Over both prefixes every one of the %d tables %s and **%d of the %d copies take a name**: "
            "`<Class>_lookup`, the class the table states, with **%d numbered** (`_2` by ascending address) "
            "because that many copies bind a class another copy already holds. %s %s"
            % (census["walker_tables"],
               "states one class (%d distinct, none stating two)" % census["walker_classes"]
               if not census.get("walker_mixed") else
               "states two classes or more in %d cases" % census["walker_mixed"],
               census["derived_named"], census["walkers"], census["derived_suffixed"],
               "%d base name%s already %s in the map, so a derivation is numbered rather than allowed "
               "to overwrite a symbol the build carries." % (clash, "" if clash == 1 else "s",
                                                            "exists" if clash == 1 else "exist")
               if clash else "No base name collides with a symbol the map already carries.",
               "%d copies take no name." % unclassed if unclassed else
               "The list is the artifact's `derived_name` column."))
        lines.append("")
        if state is None:
            state, _, _ = derived_state(rows)
        if state["missing"]:
            lines.append(
                "**The list is not yet in the symbol map.** `derived_state` reads **%d present, %d missing, "
                "%d misfiled** against `symbols/xbox_functions.csv`; `--apply-symbols` writes the %d missing "
                "rows at the census's own address and size (module empty: a name states a class, not a "
                "translation unit), taking the map from %d to %d rows."
                % (state["present"], state["missing"], state["misfiled"], state["missing"],
                   state["map_rows"], state["after"]))
        else:
            lines.append(
                "**The add is made, and it is an insert: %d of %d names are rows of the symbol map.** "
                "`derived_state` reads **%d present, %d missing, %d misfiled** against "
                "`symbols/xbox_functions.csv` -- one row per copy, at the census's own address and size, "
                "module empty because a name states a class and not a translation unit -- and the map goes "
                "**%d -> %d rows**, which is why the add is a symbols cascade and not a column write. "
                "`--check-symbols` is the dump-free gate in `make ci`: a derivation that leaves the map, or "
                "an address the map already holds under another name, fails the build."
                % (state["present"], state["present"] + state["misfiled"], state["present"],
                   state["missing"], state["misfiled"], state["map_rows"] - state["missing"],
                   state["map_rows"]))
        lines.append("")
    walk = read_csv(WALKERS)
    if walk and not census.get("derived_named"):
        lines.append(
            "**The %d names this read derived are rows of the symbol map, and the record outlives the "
            "population.** The add (`--apply-symbols`) wrote them into `symbols/xbox_functions.csv` at the "
            "census's own address and size, and `inventory/gap_walker_names.csv` is that add's own record. The "
            "census re-read after the add no longer sees these bodies -- they are map rows, not gap heads, so "
            "they have left this population -- which is why the `derived_name` column above now reads empty "
            "while the class each copy binds is still on the record. `--check-symbols` is the dump-free gate "
            "in `make ci`: a recorded name that leaves the map, or an address the map holds under another "
            "name or size, fails the build." % len(walk))
        lines.append("")
    if census.get("slot_movs"):
        lines.append(
            "**%d of these bodies are stored into a global function-pointer slot** (`mov DWORD PTR "
            "ds:<global>,<body>`), and **%d** of the %d slots are consecutive dword globals at "
            "`%s`..`%s` -- one static array of %d function pointers, written entry by entry by the "
            "registration body at startup.** The remaining %d are singletons spread over %d distinct "
            "globals. A body reachable only through that array cannot be called by a name in code, and "
            "nothing in the image spells the array's own meaning, so the census places them and does not "
            "name them -- what it can now say is which %d of the %d bodies nothing names are entries of it."
            % (census["slot_movs"], census["slot_run"], census["slot_dests"], census["slot_lo"],
               census["slot_hi"], census["slot_run"], census["slot_movs"] - census["slot_run"],
               census["slot_dests"], census["slot_run"], census["unnamed"]))
        lines.append("")
    lines.append("| gap body | size | family | owner | the string in it |")
    lines.append("|---|---|---|---|---|")
    shown = sorted(rows, key=lambda r: (r["family"] == "silent", -int(r["size"])))[:40]
    for r in shown:
        owner = (r["owner_name"] or "") if r["owner_name"] else (r["owner_va"] or r["near_module"])
        lines.append("| `%s` | %s | `%s` | %s | %s |" % (
            r["body_va"], r["size"], r["family"], owner or "-", r["string_head"] or "-"))
    if len(rows) > len(shown):
        lines.append("| ... | | | %d more | |" % (len(rows) - len(shown)))
    lines.append("")
    lines.append(
        "**Of the %d decoded heads, %d bodies and %d bytes nothing names -- and %d islands besides.** The "
        "census's pointer "
        "channel looks for a dword *outside* the section, so a switch table laid behind a body and a "
        "`mov eax,<fn>` that hands a pointer to a caller are invisible to it by construction. Read inside, "
        "**%d** of the %d are referenced from `.text` -- **%d** by an instruction that carries the address "
        "and **%d** by a data word -- and **%d** (%d bytes) are spelled nowhere in the section at all."
        % (census["decoded"], census["unnamed"], census["unnamed_bytes"], census["islands_unnamed"],
           census["with_text"], census["unnamed"],
           census["with_operand"], census["with_table"], census["silent"], census["fam_silent_bytes"]))
    lines.append("")
    lines.append(
        "**The control is the map's own rows: %d of its %d bodies are spelled inside `.text`, %d by an "
        "operand and %d by a table word** (%d of them by an operand inside their own span, which is "
        "recursion or a self-laid table and counted apart). A row is in the map because something names "
        "it -- a symbol, a slot, a `push` -- and half of them also handle their own address in code, so "
        "the channel is a floor on this population too. A random gap byte's address is spelled in `.text` "
        "**%d of %d** times (%.1f%%), and no family below is smaller than that rate."
        % (census["map_text"], census["map_rows"], census["map_operand"], census["map_table"],
           census["map_self_operand"], census["base_hit"], census["base_n"],
           100.0 * census["base_hit"] / max(1, census["base_n"])))
    lines.append("")
    stalls = [r for r in rows if r["family"] == "silent" and int(r["size"]) <= 13]
    if stalls:
        lines.append(
            "**%d of the silent bodies are 13 bytes or shorter**, and the heads' own prologue column says "
            "what they open on: a `ret`, a `xor eax,eax; ret`, a `mov eax,[ecx+d]`. A short body with no "
            "reference anywhere is the shape of a virtual method an unregistered class's table holds, or "
            "of a CRT stub reached through a pointer the image spells in a form these four channels do "
            "not read." % len(stalls))
        lines.append("")
    return "\n".join(lines)


def splice_doc(path, block, marks):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(marks[0]), text.find(marks[1])
    if i < 0 or j < 0:
        return None
    return text[:i] + marks[0] + "\n" + block + "\n" + text[j:]


def self_check():
    bad = []
    extents = {0x1000: 0x20, 0x1100: 0x10, 0x1200: 0x40}
    b = Bodies(extents)
    if b.at(0x1000) != 0x1000 or b.at(0x101F) != 0x1000:
        bad.append("a site inside a body is that body")
    if b.at(0x1020) is not None or b.at(0x10FF) is not None:
        bad.append("a site in the hole between bodies belongs to no body")
    ins = Insns({0x2000: (5, "mov    eax,0x1200"), 0x2005: (3, "ret")})
    if site_kind(ins.at(0x2001), 0x1200) != "operand":
        bad.append("a site inside an instruction that spells the value is an operand")
    if site_kind(ins.at(0x2001), 0x1201) != "table":
        bad.append("an instruction that spells another value does not carry this one")
    if site_kind(ins.at(0x2005), 0x1200) != "table":
        bad.append("a site no instruction covers is a table word")
    if site_kind(None, 0x1200) != "table":
        bad.append("a site outside every instruction is a table word")
    if family_of({("operand", "row"), ("table", "row")}) != "operand-in-row":
        bad.append("an operand in a row is the strongest family")
    if family_of({("table", "gap")}) != "table-in-gap":
        bad.append("a table word in no body is its own family")
    if family_of(set()) != "silent":
        bad.append("no reference is silent")
    mods = {"Engine", "Core"}
    if module_of("Core_Src_UnClass", mods) != "Core":
        bad.append("a module-file name votes for its module")
    if module_of("FUN_00011000", mods) != "":
        bad.append("an unnamed body votes for no module")
    if module_of("ucpp_Register_GoauldDrone", mods) != "":
        bad.append("a name whose prefix is not a module votes for none")
    if one_class({"ShadowManager": 3}) != "ShadowManager":
        bad.append("a table whose keys name one class states it")
    if one_class({"ShadowManager": 2, "BotBase": 1}) != "":
        bad.append("a table whose keys name two classes states none")
    if one_class({"?": 2}) != "":
        bad.append("a table whose keys name no class states none")
    m = CLASS_NAME.match("intUSGOrderexecDrawIcon")
    if not m or m.group(1) != "SGOrder":
        bad.append("the class grammar reads the intU half as well as the intA half")
    got = derive_names([(0x1000, "Alpha"), (0x1100, "Alpha"), (0x1200, "Beta")],
                       {"Beta_lookup": 0x9999})
    if got != {0x1000: "Alpha_lookup", 0x1100: "Alpha_lookup_2", 0x1200: "Beta_lookup_2"}:
        bad.append("a derivation is numbered by address and never overwrites a map symbol")
    if derive_names([(0x1000, "")], {}) != {}:
        bad.append("a copy whose table states no class takes no name")
    blob = struct.pack("<I", 0x1200) + b"\x00" * 8 + struct.pack("<I", 0x9999)
    got = dword_sites(blob, 0x1000, {0x1200})
    if got != {0x1200: [0x1000]}:
        bad.append("the wanted-value scan reports the site, not the value alone")
    if dword_sites(blob, 0x1000, {0x9999}) != {0x9999: [0x100C]}:
        bad.append("an unaligned occurrence is still a reference")
    rows = [{"body_va": "0x00001000", "size": "16", "prologue": "global-load-eax", "terminator": "ret",
             "string_head": "", "text_sites": "1", "site_kind": "operand", "owner_kind": "row",
             "owner_va": "0x00001100", "owner_name": "Core_Src_UnFooo", "owner_module": "Core",
             "slot_dest": "0x00630000", "table_key": "", "table_va": "", "family": "operand-in-row",
             "near_module": "Core", "derived_name": ""},
            {"body_va": "0x00002000", "size": "9", "prologue": "bare-ret", "terminator": "ret",
             "string_head": "ReadFile beyond EOF", "text_sites": "0", "site_kind": "", "owner_kind": "",
             "owner_va": "", "owner_name": "", "owner_module": "", "slot_dest": "",
             "table_key": "intAShadowManager", "table_va": "0x00630010", "family": "silent",
             "near_module": "Core", "derived_name": "ShadowManager_lookup"}]
    census = {"decoded": 3, "unnamed": 2, "unnamed_bytes": 25, "islands_unnamed": 1, "walkers": 1,
              "walkers_silent": 1, "walker_tables": 1, "walkers_dup": 0, "walker_entries": 1,
              "walker_mixed": 0, "walker_classes": 1, "walker_unclassed": 0, "derived_named": 1,
              "derived_suffixed": 0, "derived_map_clash": 0, "slot_movs": 1,
              "slot_dests": 1, "slot_run": 1, "slot_lo": "0x00630000", "slot_hi": "0x00630000", "with_text": 1, "with_operand": 1, "with_table": 0,
              "operand_in_row": 1, "table_in_row": 0, "operand_in_head": 0, "operand_in_gap": 0,
              "table_in_head": 0,
              "table_in_gap": 0, "silent": 1, "map_rows": 100, "map_text": 60, "map_operand": 40,
              "map_table": 30, "map_self_operand": 5, "base_n": 1000, "base_hit": 10,
              "mod_operand": "Core=1", "fam_silent_bytes": 9, "thirteen_byte": 1}
    text = render(rows, census)
    for want in ("`operand-in-row`", "Core_Src_UnFooo", "ReadFile beyond EOF", "**Of the 3 decoded heads",
                 "**1 of these bodies are stored into a global function-pointer slot**", "`0x00630000`..`0x00630000`",
                 "`intAShadowManager`", "The family is nameable", "1 of the 1 copies take a name",
                 "2 bodies and 25 bytes", "and 1 islands besides",
                 "**1** of the 2 are referenced", "40 by an operand", "10 of 1000"):
        if want not in text:
            bad.append("render lacks %r" % (want,))
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "f.csv")
    write_artifact(rows, census, path)
    back, back_c = read_artifact(path)
    if back != rows:
        bad.append("the artifact round trip differs")
    if back_c != census:
        bad.append("the census row does not round trip")
    # The add: one derivation already a row, one not, one conflicting -- then the insert and its idempotence.
    smap = os.path.join(tmp, "xbox_functions.csv")
    with open(smap, "wb") as fh:
        fh.write(b"address,name,size,module\r\n0x00001000,Kept,4,\r\n"
                 b"0x00002000,ShadowManager_lookup,9,\r\n")
    sym_rows = [{"body_va": "0x00002000", "size": "9", "derived_name": "ShadowManager_lookup"},
                {"body_va": "0x00003000", "size": "5", "derived_name": "Alpha_lookup"}]
    st, miss, mis = derived_state(sym_rows, smap)
    if (st["present"], st["missing"], st["misfiled"], st["map_rows"], st["after"]) != (1, 1, 0, 2, 3):
        bad.append("derived_state reads the present, missing and row counts off a map")
    if len(miss) != 1 or mis:
        bad.append("derived_state hands back the missing row and no misfiled one")
    st2, _, mis2 = derived_state([{"body_va": "0x00001000", "size": "4", "derived_name": "Other"}], smap)
    if st2["misfiled"] != 1 or len(mis2) != 1:
        bad.append("an address the map holds under another name is misfiled, not missing")
    if insert_symbols(sym_rows, smap) != 1:
        bad.append("insert_symbols writes one row for one missing derivation")
    want = (b"address,name,size,module\r\n0x00001000,Kept,4,\r\n"
            b"0x00002000,ShadowManager_lookup,9,\r\n0x00003000,Alpha_lookup,5,\r\n")
    if open(smap, "rb").read() != want:
        bad.append("the insert keeps every existing line verbatim and sorts the new row in")
    if derived_state(sym_rows, smap)[0]["missing"] != 0:
        bad.append("the inserted rows are map rows on the second read")
    if insert_symbols(sym_rows, smap) != 0:
        bad.append("the insert is idempotent")
    wpath = os.path.join(tmp, "gap_walker_names.csv")
    if write_walker_names(sym_rows, wpath) != 2:
        bad.append("the add records one row per derived name")
    if len(walker_population([], wpath)) != 2:
        bad.append("the record is the population the gate checks once the census has re-read")
    if walker_population(sym_rows, wpath)[0]["derived_name"] != "ShadowManager_lookup":
        bad.append("a derivation the record already holds is not duplicated")
    if "The add is made" not in render(rows, census, {"present": 1, "missing": 0, "misfiled": 0,
                                                      "map_rows": 101, "after": 101}):
        bad.append("the render says the add is made when every derivation is a row")
    if "not yet in the symbol map" not in render(rows, census, {"present": 0, "missing": 1, "misfiled": 0,
                                                                "map_rows": 100, "after": 101}):
        bad.append("the render says the list is unwritten when a derivation has no row")
    if bad:
        print("FAIL gap-body-families: " + "; ".join(bad))
        return 1
    print("ok   gap-body-families: the body lookup, the instruction ownership, all six families, the module "
          "token, the wanted-value scan, the derivation's add (state, insert, idempotence, verbatim lines), "
          "the render and both artifact round trips agree with the synthetic rows")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply-symbols", action="store_true",
                    help="insert a symbols/xbox_functions.csv row per derived name (no dump)")
    ap.add_argument("--check-symbols", action="store_true",
                    help="every derived name is a map row, with its own spelling, and none is misfiled (no dump)")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.check_symbols or args.apply_symbols:
        rows, census = read_artifact(OUT)
        if not rows or not census:
            print("FAIL gap-body-families: %s is missing or has no census row" % OUT)
            return 1
        population = walker_population(rows)
        state, missing, misfiled = derived_state(population)
        if misfiled:
            print("FAIL gap-body-families: %d derived address(es) the map holds under another name or size, "
                  "first %s (%s)" % (len(misfiled), misfiled[0]["body_va"], misfiled[0]["derived_name"]))
            return 1
        if args.apply_symbols:
            recorded = write_walker_names(rows)
            added = insert_symbols(population, None, missing)
            print("inserted %d rows into %s and recorded %d names in %s (%d present, %d missing, %d misfiled)"
                  % (added, os.path.relpath(SYMBOLS, ROOT), recorded, os.path.relpath(WALKERS, ROOT),
                     state["present"], state["missing"], state["misfiled"]))
            return 0
        if state["missing"]:
            print("FAIL gap-body-families: %d derived name(s) are not map rows, first %s (%s)"
                  % (state["missing"], missing[0]["body_va"], missing[0]["derived_name"]))
            return 1
        print("ok   gap-body-families: all %d derived names are rows of symbols/xbox_functions.csv at the "
              "recorded address and size, none misfiled (map %d rows)"
              % (state["present"], state["map_rows"]))
        return 0
    if args.check_doc:
        rows, census = read_artifact(OUT)
        if not rows or not census:
            print("FAIL gap-body-families: %s is missing or has no census row" % OUT)
            return 1
        block = render(rows, census)
        text = open(args.check_doc, encoding="utf-8").read()
        i, j = text.find(DOC_A[0]), text.find(DOC_A[1])
        if i < 0 or j < 0:
            print("FAIL gap-body-families: %s has no %s block" % (args.check_doc, DOC_A[0]))
            return 1
        if text[i:j + len(DOC_A[1])] != DOC_A[0] + "\n" + block + "\n" + DOC_A[1]:
            print("FAIL gap-body-families: %s's block does not reproduce" % args.check_doc)
            return 1
        print("ok   gap-body-families: %s reproduces from the committed CSV" % args.check_doc)
        return 0
    if args.report or args.write_doc:
        rows, census = read_artifact(OUT)
        if not rows or not census:
            print("FAIL gap-body-families: %s is missing or has no census row" % OUT)
            return 1
        block = render(rows, census)
        if args.report:
            print(block)
            return 0
        new = splice_doc(args.write_doc, block, DOC_A)
        if new is None:
            print("FAIL gap-body-families: %s has no %s markers" % (args.write_doc, DOC_A[0]))
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
    names, sizes = load_symbols(SYMBOLS)
    head_rows = read_csv(HEADS)
    joined = {}
    for r in read_csv(BODIES):
        try:
            joined[int(r["body_va"], 16)] = r
        except (KeyError, ValueError):
            continue
    modules = {row.get("module") for row in read_csv(SYMBOLS) if row.get("module")}
    modules.update({"Engine", "Core", "IpDrv", "Gui", "D3DDrv", "XboxDrv", "XboxAudio", "XboxLive",
                    "Gameplay", "Fire", "stargategame", "stargatevehicles"})
    rows, census = measure(image, names, sizes, head_rows, joined, modules)
    out = os.path.join(args.out, os.path.basename(OUT)) if args.out else OUT
    write_artifact(rows, census, out)
    print("wrote %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
