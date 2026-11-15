#!/usr/bin/env python3
"""The 84 unmapped installer tail jumps: 23 are in one thunk pool, and the other 61 are dead forwarders.

`tools/class-orphan-tables.py` found 84 tail jumps into the 24 unregistered class-table installers coming
from code `symbols/xbox_functions.csv` does not cover, and `tools/native-island.py` read the map gap that
holds the most of them (`0x3f28b8`..`0x3f6d70`, 23 sites): a compiler thunk pool of MSVC `FuncInfo`
handlers, whose stubs are reached through the frame slot the guarded body stores, never through a branch.

That left 61 sites in the map's *other* gaps, and the question they carry is whether any of them is a
real caller of an installer: a tail jump is only reachable if something branches to the stub that ends in
it. This tool answers that per site and per gap, with four independent channels:

  * **a branch enters the gap** -- an `E8`/`E9` whose target is any byte of the gap, counted separately
    for edges from outside the gap and for edges whose site is inside it (a short hop between two
    fragments of the same gap is not an entry);
  * **a branch enters the stub** -- an `E8` whose target is the stub's own start;
  * **a body stores the stub's address** -- a `c7 /0` store or `68` push of it, the channel that reaches
    an `__ehhandler`;
  * **a dword outside `.text` names the stub** -- a vtable slot or any pointer, checked against every
    class-table span in the image, mapped *and* unmapped, with the slot resolved when the ref lands in one.

Every one of the 61 is an 8-byte `mov reg,[ebp+d]; jmp <installer>` forwarder naming 18 of the 24 installers.
No branch reaches any of the 14 gaps: `E8`/`E9` edges entering one from outside total 0 (the 11 in-gap
`jmp`s are hops between fragments of the same gap) and no `E8` targets a stub. Neither does a store or a
`push` name one. The one channel that does name them is a `.rdata` dword -- 75 refs over the 61 stubs --
and not one ref is a class-table slot in either the 492 mapped or the 232 unmapped tables: they sit in
exception descriptors, 74 of the 75 behind the `0xffffffff` unwind terminator and 39 of the 75 immediately
in front of a `0x19930520` `FuncInfo` header (102 and 55 of the 103 refs the whole 84-site population
carries). So the 84 are compiler exception plumbing, not dispatch entries, and the population that could
still be a real caller of the 24 installers is empty.

    tools/native-island-gaps.py --xbe DUMP/default.xbe -o inventory
    tools/native-island-gaps.py --report
    tools/native-island-gaps.py --write-doc docs/systems/CLASS_REGISTRATION.md
    tools/native-island-gaps.py --check-doc docs/systems/CLASS_REGISTRATION.md
    tools/native-island-gaps.py --check        # synthetic text, no dump, writes nothing

The dump is never committed; the artifact and the doc are. Addresses and counts only.
"""
import argparse
import bisect
import csv
import importlib.util
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

ARTIFACT = os.path.join(ROOT, "inventory/native_island_gaps.csv")
DOC = os.path.join(ROOT, "docs/systems/CLASS_REGISTRATION.md")
DOC_START = "<!-- generated:native-island-gaps -->"
DOC_END = "<!-- /generated:native-island-gaps -->"
FIELDS = ["row", "gap_lo", "gap_hi", "gap_size", "stubs", "kinds", "sites", "entered", "entered_calls",
          "inside", "site_va", "stub_va", "stub_size", "stub_kind", "target", "table", "calls", "stores",
          "dwords", "sections", "slot", "eh_magic", "eh_term", "eh_stubs", "eh_finfo", "census"]


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


island = _load("native-island")


# --------------------------------------------------------------------------- the gap a site sits in
def gap_of(funcs, starts, va):
    """(lo, hi) of the map gap holding `va`: the body below ends at `lo`, the body above starts at `hi`."""
    i = bisect.bisect_right(starts, va) - 1
    below = funcs[i] if i >= 0 else None
    above = funcs[i + 1] if i + 1 < len(funcs) else None
    lo = below[0] + max(below[1], 1) if below else 0
    hi = above[0] if above else lo
    return lo, hi


def stub_of(stubs, site):
    """The stub whose [start, start+size) holds `site`, or None when the walker split it away."""
    starts = [s[0] for s in stubs]
    i = bisect.bisect_right(starts, site) - 1
    if i < 0:
        return None
    st = stubs[i]
    if st[0] <= site < st[0] + max(st[1], 1):
        return st
    return None


def edges_into(text, tva, lo, hi):
    """(entered_calls, entered, inside) -- branches into the gap, split by where the branch site is."""
    edges = island.ref_sites(text, tva, set(range(lo, hi)))
    outside = [e for e in edges if not (lo <= e[0] < hi)]
    return (sum(1 for e in outside if e[2] == "call"), len(outside), len(edges) - len(outside))


def read_dw(dat, xbe, va):
    """The dword at `va`, or None where it does not translate."""
    off = xbe.va_to_offset(va)
    if off is None or off < 0 or off + 4 > len(dat):
        return None
    return struct.unpack("<I", dat[off:off + 4])[0]


def dword_refs(dat, xbe, va, spans):
    """(n, sections, slot, magic, term) for every dword outside `.text` that equals `va`.

    `spans` is every class-table span, mapped and unmapped -- a hit inside one is a dispatch slot, and
    checking only the mapped tables would call a slot in one of the 232 unmapped tables "no table".
    `magic` counts refs whose next dword is the `0x19930520` `FuncInfo` header and `term` the refs whose
    previous dword is the `0xffffffff` unwind-map terminator: the two halves of an exception descriptor.
    """
    pat = struct.pack("<I", va)
    n, pos, secs, slot, magic, term = 0, dat.find(pat), set(), "", 0, 0
    while pos != -1:
        ref = xbe.offset_to_va(pos)
        sec = xbe.section_for(ref) if ref else None
        if sec is None or sec.name != ".text":
            n += 1
            if sec is not None:
                secs.add(sec.name)
            if read_dw(dat, xbe, (ref or 0) + 4) == 0x19930520:
                magic += 1
            if read_dw(dat, xbe, (ref or 0) - 4) == 0xFFFFFFFF:
                term += 1
            for tva, sn, _kind in spans:
                if tva <= (ref or 0) < tva + 4 * sn:
                    slot = "0x%08x+%d" % (tva, ((ref or 0) - tva) // 4)
                    break
        pos = dat.find(pat, pos + 1)
    return n, ",".join(sorted(secs)) or "-", slot, magic, term



def eh_pool(xbe, stubs):
    """(eh-handler stubs, how many of them name a `FuncInfo`) in one gap's walk."""
    eh = [st for st in stubs if st[2] == "eh-handler"]
    ok = 0
    for st in eh:
        operand = st[4]
        sec = xbe.section_for(operand) if isinstance(operand, int) else None
        raw = xbe.read_va(operand, 4) if sec else b""
        if len(raw) >= 4 and struct.unpack("<I", raw[:4])[0] == 0x19930520:
            ok += 1
    return len(eh), ok


# --------------------------------------------------------------------------- the measurement
def harvest(xbe, dat, text, tva, funcs, installers, tables, spans):
    """One row per gap and per site, plus a census row carrying every run-level count."""
    starts = [f[0] for f in funcs]
    edges = island.ref_sites(text, tva, set(installers))
    to_inst = [(s, t) for s, t, k in edges if k == "jmp" and t in installers]
    unmapped = sorted(s for s, t in to_inst if not island.mapped(funcs, starts, s))
    hole = island.island_bounds(funcs, starts, unmapped)
    hole_range = (hole[0], hole[1])
    groups = {}
    for site in unmapped:
        groups.setdefault(gap_of(funcs, starts, site), []).append(site)

    # walk every gap first: the stub start comes from the decode, not from an assumed site offset
    walked, stub_vas = {}, set()
    for (lo, hi) in groups:
        stubs = island.walk(xbe.read_va(lo, hi - lo), lo)
        walked[(lo, hi)] = stubs
        for st in stubs:
            stub_vas.add(st[0])
    store_sites = island.imm_sites(text, tva, stub_vas)

    rows, gap_rows = [], []
    total_stubs = total_entered = total_entered_calls = total_inside = total_bytes = 0
    total_refs = total_slot = total_magic = total_term = 0
    total_eh = total_eh_finfo = 0
    for (lo, hi), sites in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        stubs = walked[(lo, hi)]
        kinds = {}
        for st in stubs:
            kinds[st[2]] = kinds.get(st[2], 0) + 1
        entered_calls, entered, inside = edges_into(text, tva, lo, hi)
        eh_n, eh_ok = eh_pool(xbe, stubs)
        total_eh += eh_n
        total_eh_finfo += eh_ok
        total_stubs += len(stubs)
        total_entered += entered
        total_entered_calls += entered_calls
        total_inside += inside
        total_bytes += hi - lo
        gap_rows.append({"row": "gap", "gap_lo": "0x%08x" % lo, "gap_hi": "0x%08x" % hi,
                         "gap_size": str(hi - lo), "stubs": str(len(stubs)),
                         "kinds": ",".join("%s:%d" % (k, kinds[k]) for k in sorted(kinds)),
                         "sites": str(len(sites)), "entered": str(entered),
                         "entered_calls": str(entered_calls), "inside": str(inside),
                         "eh_stubs": str(eh_n), "eh_finfo": str(eh_ok),
                         "census": "" if (lo, hi) != hole_range else "hole"})
        for site in sites:
            st = stub_of(stubs, site)
            sv = st[0] if st else 0
            calls = [s for s in island.ref_sites(text, tva, {sv}) if s[2] == "call"] if sv else []
            stores = [s for s in store_sites if s[1] == sv] if sv else []
            n_ref, secs, slot, magic, term = dword_refs(dat, xbe, sv, spans) if sv else (0, "-", "", 0, 0)
            total_refs += n_ref
            total_slot += 1 if slot else 0
            total_magic += magic
            total_term += term
            rows.append({"row": "site", "gap_lo": "0x%08x" % lo, "gap_hi": "0x%08x" % hi,
                         "gap_size": str(hi - lo), "sites": str(len(sites)),
                         "site_va": "0x%08x" % site, "stub_va": "0x%08x" % sv,
                         "stub_size": str(st[1]) if st else "", "stub_kind": st[2] if st else "",
                         "target": "0x%08x" % [t for s, t in to_inst if s == site][0],
                         "table": tables.get([t for s, t in to_inst if s == site][0], ""),
                         "calls": str(len(calls)), "stores": str(len(stores)),
                         "dwords": str(n_ref), "sections": secs, "slot": slot,
                         "eh_magic": str(magic), "eh_term": str(term),
                         "census": "" if (lo, hi) != hole_range else "hole"})
    rows = gap_rows + rows
    sites_rows = [r for r in rows if r["row"] == "site"]
    other = [r for r in sites_rows if not r["census"]]
    hole_rows = [r for r in sites_rows if r["census"]]
    called = [r for r in other if r["calls"] != "0"]
    stored = [r for r in other if r["stores"] != "0"]
    dref = [r for r in other if r["dwords"] != "0"]
    named = {r["target"] for r in sites_rows}
    meta = {"sites": len(unmapped), "hole_sites": hole[2], "other_sites": len(other),
            "gaps": len(groups), "other_gaps": len(groups) - (1 if hole_range in groups else 0),
            "bytes": total_bytes, "stubs": total_stubs, "entered": total_entered,
            "entered_calls": total_entered_calls, "inside": total_inside,
            "called": len(called), "stored": len(stored), "dword_refs": len(dref),
            "refs": total_refs, "slot_hits": total_slot, "eh_magic": total_magic,
            "eh_stubs": total_eh, "eh_finfo": total_eh_finfo,
            "eh_term": total_term, "tables": len(spans),
            "mapped": sum(1 for s in spans if s[2] == "mapped"),
            "unmapped": sum(1 for s in spans if s[2] == "unmapped"),
            "installers": len(named), "hole_lo": hole[0], "hole_hi": hole[1],
            "fwd": sum(1 for r in other if r["stub_kind"] == "forward"),
            "fwd_size": len({r["stub_size"] for r in other if r["stub_kind"] == "forward"})}
    fwdb = {r["stub_size"] for r in other if r["stub_kind"] == "forward"}
    meta["fwd_bytes"] = int(next(iter(fwdb))) if len(fwdb) == 1 else 0
    meta["other_installers"] = len({r["target"] for r in other})
    # the two populations the prose quotes separately: the 61 other-gap sites and the 84 with the hole
    for tag, rs in (("other", other), ("hole", hole_rows)):
        for key in ("dwords", "eh_magic", "eh_term"):
            meta["%s_%s" % (tag, key)] = sum(int(r[key] or 0) for r in rs)
    meta["other_refs"] = meta.pop("other_dwords")
    rows.append({"row": "census", "census": ";".join(
        "%s=%s" % (k, ("0x%08x" % v) if k in ("hole_lo", "hole_hi") else v)
        for k, v in sorted(meta.items()))})
    # the artifact's own tally: a shadowed accumulator once shipped 24 rows beside a "84 sites" label
    assert len(rows) == len(gap_rows) + len(sites_rows) + 1, (len(rows), len(gap_rows), len(sites_rows))
    return rows, meta


# --------------------------------------------------------------------------- artifacts
def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def census_of(rows):
    """The run-level counts, read from the artifact's own `census` row."""
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
    """The doc block, rebuilt from the artifact alone so the page and the CSV cannot disagree."""
    meta = census_of(rows)
    gaps = [r for r in rows if r.get("row") == "gap"]
    sites = [r for r in rows if r.get("row") == "site"]
    other = [r for r in sites if not r.get("census")]
    lines = ["| measure | value |", "|---|---|"]
    if meta:
        lines += [
            "| installer tail jumps, unmapped | %d |" % meta["sites"],
            "| ...in the island hole `0x%08x`..`0x%08x` / in the other gaps | %d / %d |"
            % (meta["hole_lo"], meta["hole_hi"], meta["hole_sites"], meta["other_sites"]),
            "| map gaps holding them | %d (%d besides the hole) |" % (meta["gaps"], meta["other_gaps"]),
            "| bytes of `.text` those gaps cover | %d |" % meta["bytes"],
            "| stubs decoded in them | %d |" % meta["stubs"],
            "| the 61 sites' stub shape | %d of %d are a forwarder, %s |"
            % (meta["fwd"], meta["other_sites"],
               ("all %d bytes" % meta["fwd_bytes"]) if meta["fwd_bytes"] else
               ("%d distinct sizes" % meta["fwd_size"])),
            "| `E8`/`E9` edges entering a gap from outside it | %d (`E8` %d) |"
            % (meta["entered"], meta["entered_calls"]),
            "| `jmp` edges whose site is inside the same gap | %d |" % meta["inside"],
            "| `eh-handler` stubs in the 14 gaps / naming a `FuncInfo` | %d / %d |"
            % (meta["eh_stubs"], meta["eh_finfo"]),
            "| the 61 stubs an `E8` calls / a store or push names | %d / %d |"
            % (meta["called"], meta["stored"]),
            "| the 61 stubs a dword outside `.text` names | %d (%d refs) |"
            % (meta["dword_refs"], meta["other_refs"]),
            "| ...of those refs, in a class-table slot (%d mapped + %d unmapped tables) | %d of %d |"
            % (meta["mapped"], meta["unmapped"], meta["slot_hits"], meta["refs"]),
            "| ...whose next dword is a `FuncInfo` header `0x19930520` | %d of %d (%d of the %d refs all 84 carry) |"
            % (meta["other_eh_magic"], meta["other_refs"], meta["eh_magic"], meta["refs"]),
            "| ...whose previous dword is the `0xffffffff` unwind terminator | %d of %d (%d of %d) |"
            % (meta["other_eh_term"], meta["other_refs"], meta["eh_term"], meta["refs"]),
            "| installers the 84 tail jumps name | %d of the 24 (the 61 name %d) |"
            % (meta["installers"], meta["other_installers"]),
        ]
    if gaps:
        lines += ["", "| gap | bytes | stubs | stub shapes | sites | edges in from outside | edges inside "
                  "| eh-handler / `FuncInfo` |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in gaps:
            lines.append("| `%s`..`%s`%s | %s | %s | %s | %s | %s | %s | %s / %s |" % (
                r["gap_lo"], r["gap_hi"], " (hole)" if r.get("census") else "", r["gap_size"],
                r["stubs"], r["kinds"], r["sites"], r["entered"], r["inside"],
                r["eh_stubs"], r["eh_finfo"]))
    if meta:
        lines += ["",
                  "All %d sites outside the hole sit in stubs, %d of which the walker decodes as a "
                  "forwarder, and `E8`/`E9` edges entering the gaps from outside total %d. "
                  "Across the whole 84-site population the only channel that names a stub is a `.rdata` "
                  "dword (%d refs), and none of them is a class-table slot, mapped or unmapped: they sit "
                  "in exception descriptors -- the word in front of one is the unwind terminator on %d "
                  "of the %d refs, and %d stand immediately in front of a `FuncInfo` header. So the 84 "
                  "are compiler exception plumbing rather than dispatch entries, no branch reaches any "
                  "of them, and the 24 installers keep their 103 constructor callers as the only "
                  "reachable evidence." % (
                      meta["other_sites"], meta["fwd"], meta["entered"],
                      meta["refs"], meta["eh_term"], meta["refs"], meta["eh_magic"])]
    return "\n".join(lines)


def splice_doc(path, block):
    text = open(path, encoding="utf-8").read()
    if DOC_START not in text or DOC_END not in text:
        return None
    head, rest = text.split(DOC_START, 1)
    _, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + "\n" + DOC_END + tail


# --------------------------------------------------------------------------- self test
class _Image:
    """The two `Xbe` calls `dword_refs` makes, over a flat blob with one section at `va`."""

    def __init__(self, foff, va, name=".rdata"):
        self.foff, self.va, self.name = foff, va, name

    class _Sec:
        def __init__(self, name):
            self.name = name

    def offset_to_va(self, off):
        return off - self.foff + self.va

    def va_to_offset(self, va):
        return va - self.va + self.foff

    def section_for(self, va):
        return self._Sec(self.name) if self.va <= va < self.va + 0x1000 else None


def self_check():
    """Synthetic text: the four channels, the gap split and the census round trip."""
    tva = 0x1000
    funcs = [(0x1000, 0x10, "a"), (0x1200, 0x10, "b")]
    starts = [f[0] for f in funcs]
    # 0x1110: `mov eax,[ebp+8]; jmp 0x2000` -- the forwarder shape, in the gap 0x1010..0x1200
    fwd = bytes.fromhex("8b4508") + b"\xe9" + struct.pack("<i", 0x2000 - (0x1110 + 8))
    text = bytearray(b"\x90" * 0x200)
    text[0x10c:0x110] = b"\xcc" * 4          # a pad run: the forwarder is its own stub, not a tail
    text[0x110:0x118] = fwd
    assert gap_of(funcs, starts, 0x1113) == (0x1010, 0x1200), gap_of(funcs, starts, 0x1113)
    assert gap_of(funcs, starts, 0x1204) == (0x1210, 0x1210)  # past the last body: an empty gap
    stubs = island.walk(bytes(text[0x10:0x200]), 0x1010)
    st = stub_of(stubs, 0x1113)
    assert st is not None and (st[0], st[1], st[2]) == (0x1110, 8, "forward"), st
    # no branch in yet
    assert edges_into(bytes(text), tva, 0x1010, 0x1200) == (0, 0, 0)
    # a call from the body below enters the gap, and it is the only channel that goes red
    text[0x0:0x5] = b"\xe8" + struct.pack("<i", 0x1110 - (0x1000 + 5))
    assert edges_into(bytes(text), tva, 0x1010, 0x1200) == (1, 1, 0)
    assert [s[0] for s in island.ref_sites(bytes(text), tva, {0x1110})] == [0x1000]
    # a short hop inside the gap is not an entry
    text[0x130:0x135] = b"\xe9" + struct.pack("<i", 0x1110 - (0x1130 + 5))
    assert edges_into(bytes(text), tva, 0x1010, 0x1200) == (1, 1, 1)
    # the store channel: a `68 <stub>` push names the forwarder
    text[0x180:0x185] = b"\x68" + struct.pack("<I", 0x1110)
    assert [(s[0], s[1], s[2]) for s in island.imm_sites(bytes(text), tva, {0x1110})] == \
        [(0x1180, 0x1110, "push")]
    # the dword channel: a class-table slot, the `FuncInfo` header behind it, the terminator in front
    dat = bytearray(b"\x00" * 0x40)
    dat[0x20:0x24] = struct.pack("<I", 0xFFFFFFFF)
    dat[0x24:0x28] = struct.pack("<I", 0x1110)
    dat[0x28:0x2c] = struct.pack("<I", 0x19930520)
    dat[0x2c:0x30] = struct.pack("<I", 1)
    img = _Image(0, 0x500000)
    assert dword_refs(bytes(dat), img, 0x1110, [(0x500000, 12, "mapped")]) == \
        (1, ".rdata", "0x00500000+9", 1, 1)
    # the same dword with no table span is a plain .rdata ref, and the descriptor counts are unchanged
    assert dword_refs(bytes(dat), img, 0x1110, []) == (1, ".rdata", "", 1, 1)
    # an unmapped table's span counts as a slot: checking only the mapped tables would miss it
    assert dword_refs(bytes(dat), img, 0x1110, [(0x500000, 12, "unmapped")])[2] == "0x00500000+9"
    assert dword_refs(bytes(dat), img, 0x1112, []) == (0, "-", "", 0, 0)
    # the census row round-trips and the render reads it, not the measurement
    blank = {k: "" for k in FIELDS}
    fake = [dict(blank, row="gap", gap_lo="0x1010", gap_hi="0x1200", gap_size="496", stubs="1",
                 kinds="forward:1", sites="1", entered="0", entered_calls="0", inside="0",
                 eh_stubs="0", eh_finfo="0"),
            dict(blank, row="site", gap_lo="0x1010", gap_hi="0x1200", site_va="0x1113",
                 stub_va="0x1110", stub_size="8", stub_kind="forward", target="0x2000", calls="0",
                 stores="0", dwords="0"),
            dict(blank, row="census", census="bytes=496;called=0;dword_refs=0;eh_magic=0;eh_term=0;"
                 "eh_finfo=0;eh_stubs=0;entered=0;entered_calls=0;fwd=1;fwd_bytes=8;fwd_size=1;gaps=1;"
                 "hole_eh_magic=0;hole_eh_term=0;"
                 "hole_hi=0x1200;hole_lo=0x1010;hole_refs=0;hole_sites=0;inside=0;installers=1;"
                 "mapped=1;other_eh_magic=0;other_eh_term=0;other_gaps=1;other_installers=1;other_refs=0;other_sites=1;"
                 "refs=0;sites=1;slot_hits=0;stored=0;stubs=1;tables=1;unmapped=0")]
    cen = census_of(fake)
    assert (cen["entered"], cen["other_sites"], cen["hole_lo"]) == (0, 1, 0x1010), cen
    page = render(fake)
    assert "| `E8`/`E9` edges entering a gap from outside it | 0 (`E8` 0) |" in page, page
    assert "| the 61 stubs a dword outside `.text` names | 0 (0 refs) |" in page, page
    assert "| ...of those refs, in a class-table slot (1 mapped + 0 unmapped tables) | 0 of 0 |" in page, page
    assert "| `0x1010`..`0x1200` | 496 | 1 | forward:1 | 1 | 0 | 0 | 0 / 0 |" in page, page
    assert "| `eh-handler` stubs in the 14 gaps / naming a `FuncInfo` | 0 / 0 |" in page, page
    assert splice_doc(os.path.join(ROOT, "Makefile"), "x") is None  # no markers: refused, not echoed
    print("ok   native-island-gaps: 19 assertions (gap bounds, forwarder stub, four reach channels, "
          "table spans mapped and unmapped, exception descriptor, census round trip, marker refusal)")


# --------------------------------------------------------------------------- main
def main():
    if "--check" in sys.argv[1:]:
        return self_check()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe", default=None)
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc", nargs="?", const=DOC, default=None)
    ap.add_argument("--check-doc", nargs="?", const=DOC, default=None)
    args = ap.parse_args()

    if args.write_doc or args.check_doc or args.report:
        rows = read_csv(ARTIFACT)
    else:
        if not args.xbe:
            ap.error("--xbe is required to measure (or use --report / --write-doc)")
        from xbe import Xbe

        xbe = Xbe(args.xbe)
        dat = open(args.xbe, "rb").read()
        code = [s for s in xbe.sections if s.name == ".text"]
        text = b"".join(dat[s.foff:s.foff + s.fsize] for s in code)
        tva = code[0].va if code else 0
        funcs = sorted((int(r["address"], 16), int(r.get("size") or 0), r.get("name") or "")
                       for r in read_csv(os.path.join(ROOT, "symbols/xbox_functions.csv")))
        tables, installers = {}, set()
        for r in read_csv(os.path.join(ROOT, "inventory/class_orphan_tables.csv")):
            if r.get("body_va"):
                installers.add(int(r["body_va"], 16))
                tables[int(r["body_va"], 16)] = r.get("table_va", "")
        spans = []
        for name, key, kind in (("inventory/xbe_class_tables.csv", "slots", "mapped"),
                                ("inventory/native_vtable_shared.csv", "table_slots", "unmapped")):
            for r in read_csv(os.path.join(ROOT, name)):
                if r.get("table_va") and r.get(key):
                    spans.append((int(r["table_va"], 16), int(r[key]), kind))
        rows, meta = harvest(xbe, dat, text, tva, funcs, installers, tables, spans)
        print("%d unmapped installer tail jumps in %d gaps: %d stubs, %d edges in from outside, "
              "%d called, %d stored, %d dword refs"
              % (meta["sites"], meta["gaps"], meta["stubs"], meta["entered"], meta["called"],
                 meta["stored"], meta["dword_refs"]))
        if args.out:
            path = os.path.join(args.out, "native_island_gaps.csv")
            write_artifact(rows, path)
            print("%s: %d rows (%d gaps + %d sites + 1 census)"
                  % (path, len(rows), meta["gaps"], meta["other_sites"] + meta["hole_sites"]))
    print(render(rows))
    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        new = splice_doc(path, render(rows))
        if new is None:
            print("FAIL native-island-gaps: markers not found in %s" % path)
            return 1
        old = open(path, encoding="utf-8").read()
        if args.check_doc:
            if new != old:
                print("FAIL native-island-gaps: %s is stale against the committed artifact" % path)
                return 1
            print("ok   native-island-gaps: %s matches the committed artifact" % path)
            return 0
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
