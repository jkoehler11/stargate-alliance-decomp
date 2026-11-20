#!/usr/bin/env python3
"""The XboxAudio module's unnamed chain, read by its call edges (PLAN 4.9 / U34).

`inventory/xdk_entry_callers.csv` found the game's DirectSound device -- a virtual of
`XBoxAudioSubsystem` at slot 31 (`+0x7c`) -- and the voice stream factory, and closed with the
observation that the module census places none of the bodies around them because *they call only
unnamed bodies*. This walks the chain instead of the module label.

**Population, defined by the calls rather than by a range.** A seed is an unnamed (`FUN_*`) body with a
direct call into the audio SDK -- any name matching `_DirectSound*`, `_IDirectSound*`, `_XVoice*`,
`_XAudio*`, `_XWave*`, `_XMedia*`, `_XHV*` or a `?…@XHVNamespace@@` mangled method. The chain is those
seeds plus the unnamed bodies that call or are called by a member, walked to a fixpoint and capped. A
callee stops the walk when most of its callers lie outside the chain: that is shared infrastructure (the
`vswprintf` assert wrapper `FUN_00057440` has 685 callers), and it is recorded as a `stop` row instead of
being counted as module code.

**Each member is read by its own edges.** The body's bytes are decoded by `objdump` (exact instruction
boundaries, the oracle `vcall-receiver.py` uses), and every target is joined to the symbol map: a direct
call, an indirect import thunk, a virtual dispatch offset, a `push` that lands in a data section (resolved
to the bytes there -- a format block, a GUID, a string) and `ret imm16` (the body's own stdcall width).
A displacement is never a type, so a `call [reg+0x7c]` row carries the offset and the register, not a
class.

**The slot-31 census** answers the other half of U34: every `call [reg+0x7c]` site in an executable
section, boundary-validated, with the body that holds it, its module, and which other audio-subsystem
slots (`+0x88` per-frame pump, `+0xc8` file stream) that same body dispatches -- an engine body that
drives all three is the client of the subsystem, not the subsystem itself.

Nothing here carries game data: addresses, byte counts, symbol names and a literal's first characters.

    python3 tools/xboxaudio-chain.py --image DUMP/default.xbe -o inventory/xboxaudio_chain.csv
    python3 tools/xboxaudio-chain.py --report
    python3 tools/xboxaudio-chain.py --check      # the committed artifact against the map; no dump
    python3 tools/xboxaudio-chain.py --apply-overrides   # fold xbe_overrides.csv names into the map

`--check` re-derives every claim the artifact makes about the *symbol map* (each body is that address at
that size, each named edge's target really is that name, the census row equals the rows) and writes
nothing, so it runs in `make ci` without the dump.
"""
import argparse
import collections
import csv
import importlib.util
import os
import re
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

ARTIFACT = "xboxaudio_chain.csv"
COLUMNS = ["kind", "body", "body_name", "body_size", "module", "note", "site", "form", "target",
           "target_name", "target_kind", "extra"]
CAP = 64
SHARED = 1                     # a callee with any caller outside the chain is shared, not module code
FUN = re.compile(r"^FUN_[0-9a-f]{8}$")
AUDIO_API = re.compile(r"^(_I?DirectSound|_XVoice|_XAudio|_XWave|_XMedia|_XHV)")
AUDIO_NAMES = re.compile(r"^(_I?DirectSound|_XVoice|_XAudio|_XWave|_XMedia|_XHV|\?.*@XHVNamespace@@)")
AUDIO_SLOTS = {0x7C: 31, 0x88: 34, 0xC8: 50}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def helpers():
    xbe_mod = _load("sg1_xbe", os.path.join(ROOT, "tools", "xbe.py"))
    vr = _load("sg1_vr", os.path.join(ROOT, "tools", "vcall-receiver.py"))
    vd = _load("sg1_vd", os.path.join(ROOT, "tools", "vcall-dispatch.py"))
    return xbe_mod.Xbe, vr, vd


def read_functions(path):
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                out.append((int(r["address"], 16), int(r["size"] or 0), r["name"]))
            except (KeyError, ValueError):
                continue
    out.sort()
    return out


def read_modules(path):
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            mod = (r.get("module") or "").strip()
            if mod and mod not in ("(unnamed)", "unattributed"):
                out[int(r["address"], 16)] = mod
    return out


def read_tables(path, names_path):
    tables, names = [], {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                try:
                    tables.append((int(r["table_va"], 16), int(r["slots"])))
                except (KeyError, ValueError):
                    continue
        tables.sort()
    if os.path.exists(names_path):
        with open(names_path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                cls = (r.get("class") or "").strip()
                if cls:
                    try:
                        names[int(r["table_va"], 16)] = cls
                    except (KeyError, ValueError):
                        continue
    return tables, names


def owner(spans, va):
    best = None
    for addr, size, name in spans:
        if addr > va:
            break
        if size and va < addr + size:
            best = (addr, name, size)
    return best


def name_at(spans, exact, va):
    if va in exact:
        return exact[va]
    held = owner(spans, va)
    if held:
        return "%s+0x%x" % (held[1], va - held[0])
    return ""


def dword_refs(xbe, va):
    needle = struct.pack("<I", va)
    hits = []
    for s in xbe.sections:
        if s.fsize <= 0:
            continue
        blob = xbe.data[s.foff:s.foff + s.fsize]
        start = 0
        while True:
            i = blob.find(needle, start)
            if i < 0:
                break
            if i % 4 == 0:
                hits.append(s.va + i)
            start = i + 1
    return hits


def slot_of(tables, va):
    for table_va, slots in tables:
        if table_va <= va < table_va + 4 * slots:
            return table_va, slots, (va - table_va) // 4
    return None


def _chain(blob, site_off, vr, window=48, minimum=2):
    """Does a run of instructions end exactly on `site_off`, with at least `minimum` behind it?"""
    for back in range(window, 5, -1):
        start = site_off - back
        if start < 0:
            continue
        i = start
        count = 0
        while i < site_off:
            insn = vr.decode(blob, i)
            if insn is None or insn.length <= 0:
                break
            i += insn.length
            count += 1
        if i == site_off and count >= minimum:
            return True
    return False


def call_census(xbe, vr, wanted):
    """target va -> [call sites], every boundary-validated `E8` in an executable section."""
    sites = collections.defaultdict(list)
    for sec in xbe.sections:
        if not sec.executable or sec.fsize <= 0:
            continue
        blob = xbe.data[sec.foff:sec.foff + sec.fsize]
        for i in range(0, len(blob) - 5):
            if blob[i] != 0xE8:
                continue
            site = sec.va + i
            rel = struct.unpack_from("<i", blob, i + 1)[0]
            tgt = (site + 5 + rel) & 0xFFFFFFFF
            if tgt not in wanted:
                continue
            if _chain(blob, i, vr) is None:
                continue
            sites[tgt].append(site)
    return sites


def disasm(xbe, va, size):
    """[(offset, text, width)] for the body's own bytes, via objdump's own decode."""
    blob = xbe.read_va(va, size)
    fd, path = tempfile.mkstemp(suffix=".bin")
    os.write(fd, blob)
    os.close(fd)
    try:
        out = subprocess.run(["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                              "--adjust-vma=0x%x" % va, path],
                             capture_output=True, text=True).stdout
    finally:
        os.remove(path)
    insns = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        addr, rest = line.split("\t", 1)
        try:
            a = int(addr.strip().rstrip(":"), 16)
        except ValueError:
            continue
        if not (va <= a < va + size):
            continue
        fields = rest.split("\t")
        insns.append((a - va, fields[-1].strip()))
    return insns


def describe_literal(xbe, va):
    """What a pushed value is, when it lands in a data section."""
    if not any(s.contains(va) for s in xbe.sections):
        return ""
    try:
        raw = xbe.read_va(va, 32)
    except (KeyError, IndexError):
        return ""
    if len(raw) >= 8:
        wide = raw.decode("utf-16-le", "ignore")
        wide = "".join(ch for ch in wide if 32 <= ord(ch) < 127)
        if len(wide) >= 4:
            return "wide %r" % wide
    printable = "".join(chr(b) for b in raw if 32 <= b < 127)
    if len(printable) >= 4 and 32 <= raw[0] < 127:
        return "ascii %r" % printable
    return "bytes " + " ".join("%02x" % b for b in raw[:8])


def edges_of(xbe, spans, exact, body, size):
    """Every call, dispatch, literal and stdcall width the body's own decode shows."""
    edges = []
    for off, text in disasm(xbe, body, size):
        site = body + off
        if text.startswith("call") or text.startswith("jmp"):
            m = re.match(r"^(?:call|jmp)\s+0x([0-9a-f]+)$", text)
            if m:
                tgt = int(m.group(1), 16)
                edges.append({"site": site, "form": text.split()[0], "target": tgt,
                              "name": name_at(spans, exact, tgt), "kind": "call"})
                continue
            m = re.search(r"\[([a-z0-9]+)\+0x([0-9a-f]+)\]", text)
            if m:
                edges.append({"site": site, "form": "vcall", "target": int(m.group(2), 16),
                              "name": " +0x%s of [%s]" % (m.group(2), m.group(1)), "kind": "vcall",
                              "reg": m.group(1)})
                continue
            m = re.search(r"ds:0x([0-9a-f]+)", text)
            if m:
                tgt = int(m.group(1), 16)
                edges.append({"site": site, "form": "import", "target": tgt,
                              "name": name_at(spans, exact, tgt), "kind": "call"})
                continue
        m = re.match(r"^push\s+0x([0-9a-f]+)$", text)
        if m:
            lit = int(m.group(1), 16)
            what = describe_literal(xbe, lit)
            if what:
                edges.append({"site": site, "form": "literal", "target": lit, "name": what,
                              "kind": "data"})
        m = re.match(r"^ret\s+0x([0-9a-f]+)$", text)
        if m:
            edges.append({"site": site, "form": "ret", "target": int(m.group(1), 16),
                          "name": "stdcall %d args bytes" % int(m.group(1), 16), "kind": "width"})
        elif text == "ret":
            edges.append({"site": site, "form": "ret", "target": 0, "name": "cdecl", "kind": "width"})
    return edges


def audio_slots_of(xbe, vr, vd, body, size):
    """Which of the three audio-subsystem slots the body dispatches, by displacement."""
    saved = dict(vd.SLOTS)
    vd.SLOTS.clear()
    vd.SLOTS.update(AUDIO_SLOTS)
    try:
        blob = xbe.read_va(body, size)
        found = {}
        for site, slot, disp, reg in vd.scan(blob, body):
            if _chain(blob, site - body, vr):
                found[disp] = (site, reg)
        return found
    finally:
        vd.SLOTS.clear()
        vd.SLOTS.update(saved)


def read_chain_named(path):
    """Addresses an override already names *for this chain*: naming a member must not delete it from the walk."""
    out = set()
    if not path or not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if "audio chain" in (r.get("evidence") or ""):
                try:
                    out.add(int(r["address"], 16))
                except (KeyError, ValueError):
                    continue
    return out


def build_members(xbe, vr, vd, spans, exact, sizes, sites, api_names, edges_cache, chain_named):
    """Seeds are unnamed bodies with a call site into an audio API; the chain is their unnamed neighbours."""
    by_name = {name: addr for addr, name in exact.items()}
    api = {by_name[n] for n in api_names if n in by_name}
    members, role, stops = collections.OrderedDict(), {}, {}
    text = {}

    def is_game_code(va):
        """Only `.text` is the game's own code; the library sections are SDK code even when unnamed."""
        if va not in text:
            sec = xbe.section_for(va)
            text[va] = sec.name if sec else ""
        return text[va] == ".text"

    def edges(va):
        if va not in edges_cache:
            edges_cache[va] = edges_of(xbe, spans, exact, va, sizes[va]) if sizes.get(va) else []
        return edges_cache[va]

    callers_of = {}
    for va, body_sites in sites.items():
        for site in body_sites:
            held = owner(spans, site)
            if held:
                callers_of.setdefault(va, set()).add(held[0])

    for api_va in sorted(api):
        for site in sites.get(api_va, []):
            held = owner(spans, site)
            if not held:
                continue
            body, name, _size = held
            if body in members or not is_game_code(body):
                continue
            if not (FUN.match(name) or body in chain_named):
                continue
            members[body] = None
            role[body] = "seed: calls %s" % exact.get(api_va, "0x%08x" % api_va)

    frontier = list(members)
    capped = False
    while frontier and not capped:
        nxt = []
        for va in frontier:
            cands = [(e["target"], "callee") for e in edges(va)
                     if e["kind"] == "call" and e["form"] in ("call", "jmp")]
            cands += [(caller, "caller") for caller in callers_of.get(va, ())]
            for other, how in cands:
                if other in members or other in stops or not sizes.get(other):
                    continue
                if not (FUN.match(exact.get(other, "")) or other in chain_named) or not is_game_code(other):
                    continue
                outside = [c for c in callers_of.get(other, ()) if c not in members and c != va]
                if how == "callee" and len(outside) >= SHARED:
                    stops[other] = (va, len(outside))
                    continue
                if len(members) >= CAP:
                    capped = True
                    break
                members[other] = None
                role[other] = "%s of 0x%08x" % (how, va)
                nxt.append(other)
            if capped:
                break
        frontier = nxt
    return members, role, stops, callers_of


def measure(image, symbols, modules_path, tables_path, names_path, overrides_path=None):
    Xbe, vr, vd = helpers()
    xbe = Xbe(image)
    spans = read_functions(symbols)
    exact = {addr: name for addr, _size, name in spans}
    sizes = {addr: size for addr, size, _name in spans}
    modules = read_modules(modules_path)
    tables, tnames = read_tables(tables_path, names_path)
    sites = call_census(xbe, vr, set(sizes))
    api_names = sorted(n for n in exact.values() if AUDIO_API.match(n))
    chain_named = read_chain_named(overrides_path)
    edges_cache = {}
    members, role, stops, callers_of = build_members(
        xbe, vr, vd, spans, exact, sizes, sites, api_names, edges_cache, chain_named)

    def body_edges(va):
        if va not in edges_cache:
            edges_cache[va] = edges_of(xbe, spans, exact, va, sizes[va]) if sizes.get(va) else []
        return edges_cache[va]


    rows = []
    for va in members:
        size = sizes[va]
        refs = dword_refs(xbe, va)
        slot = ""
        for ref in refs:
            hit = slot_of(tables, ref)
            if hit:
                tva, count, index = hit
                slot = "%s +0x%x (slot %d of %d)" % (tnames.get(tva, "table 0x%08x" % tva),
                                                     index * 4, index, count)
                break
        args = ""
        for e in edges_of(xbe, spans, exact, va, size):
            if e["kind"] == "width":
                args = e["name"]
        rows.append({"kind": "body", "body": "0x%08x" % va, "body_name": exact.get(va, ""),
                     "body_size": size, "module": modules.get(va, ""), "note": role.get(va, ""),
                     "site": "", "form": "", "target": "", "target_name": "", "target_kind": "",
                     "extra": "callers=%d refs=%d %s%s" % (len(sites.get(va, [])), len(refs),
                                                          args, (" " + slot) if slot else "")})
        for e in body_edges(va):
            rows.append({"kind": "edge", "body": "0x%08x" % va, "body_name": exact.get(va, ""),
                         "body_size": size, "module": modules.get(va, ""), "note": "",
                         "site": "0x%08x" % e["site"], "form": e["form"],
                         "target": "0x%08x" % e["target"],
                         "target_name": e["name"], "target_kind": e["kind"],
                         "extra": modules.get(e["target"], "") if e["kind"] == "call" else ""})
    for va, (from_va, outside) in sorted(stops.items()):
        rows.append({"kind": "stop", "body": "0x%08x" % va, "body_name": exact.get(va, ""),
                     "body_size": sizes.get(va, 0), "module": modules.get(va, ""),
                     "note": "callee of 0x%08x, %d callers outside the chain" % (from_va, outside),
                     "site": "", "form": "", "target": "", "target_name": "", "target_kind": "",
                     "extra": ""})

    # slot-31 census: every `call [reg+0x7c]` in an executable section, boundary-validated.
    saved = dict(vd.SLOTS)
    vd.SLOTS.clear()
    vd.SLOTS[0x7C] = 31
    slot_rows = []
    try:
        for sec in xbe.sections:
            if not sec.executable or sec.fsize <= 0:
                continue
            blob = xbe.data[sec.foff:sec.foff + sec.fsize]
            for site, slot, disp, reg in vd.scan(blob, sec.va):
                if not _chain(blob, site - sec.va, vr):
                    continue
                held = owner(spans, site)
                body_va, body_name, body_size = held if held else (0, "", 0)
                slot_rows.append((body_va, body_name, body_size, sec.name, site, reg))
    finally:
        vd.SLOTS.clear()
        vd.SLOTS.update(saved)
    for body_va, body_name, body_size, secname, site, reg in slot_rows:
        slots = audio_slots_of(xbe, vr, vd, body_va, body_size) if body_size else {}
        others = sorted("+0x%x" % d for d in slots if d != 0x7C)
        rows.append({"kind": "slot31", "body": "0x%08x" % body_va, "body_name": body_name,
                     "body_size": body_size, "module": modules.get(body_va, ""), "note": secname,
                     "site": "0x%08x" % site, "form": "call [%s+0x7c]" % reg, "target": "0x004d2274",
                     "target_name": "XBoxAudioSubsystem +0x7c", "target_kind": "member"
                     if body_va in members else "outside",
                     "extra": ("also dispatches " + ",".join(others)) if others else ""})

    bodies = [r for r in rows if r["kind"] == "body"]
    edges = [r for r in rows if r["kind"] == "edge"]
    lib = [e for e in edges if e["target_kind"] == "call" and AUDIO_NAMES.match(
        e["target_name"] or "")]
    in_module = [r for r in rows if r["kind"] == "slot31" and r["module"] == "XboxAudio"]
    multi = [r for r in rows if r["kind"] == "slot31" and r["extra"]]
    rows.append({"kind": "census", "body": "", "body_name": "chain",
                 "body_size": sum(int(b["body_size"]) for b in bodies), "module": "",
                 "note": "", "site": "", "form": "", "target": "",
                 "target_name": "api=%d bodies=%d edges=%d library_edges=%d stops=%d "
                                "slot31_sites=%d slot31_in_module=%d slot31_multi=%d %s"
                                % (len(api_names), len(bodies), len(edges), len(lib), len(stops),
                                   len(slot_rows), len(in_module), len(multi),
                                   "capped" if len(bodies) >= CAP else "closed"),
                 "target_kind": "", "extra": ""})
    return rows


def report(rows):
    for r in rows:
        if r["kind"] == "body":
            print("%s %-32s %4s B  %-9s %-28s %s"
                  % (r["body"], r["body_name"][:32], r["body_size"], r["module"] or "-",
                     r["note"][:28], r["extra"]))
            for e in rows:
                if e["kind"] == "edge" and e["body"] == r["body"]:
                    print("    %s %-7s %-44s %s" % (e["site"][2:], e["form"],
                                                    (e["target_name"] or "")[:44], e["target"]))
        elif r["kind"] == "stop":
            print("stop %s %-32s %s" % (r["body"], r["body_name"][:32], r["note"]))
    print()
    for r in rows:
        if r["kind"] == "slot31":
            print("slot31 %s %-34s %-9s %-6s %-22s %s"
                  % (r["site"], r["body_name"][:34], r["module"] or "-", r["note"],
                     r["target_kind"], r["extra"]))
    for r in rows:
        if r["kind"] == "census":
            print(r["target_name"])


def check(path, symbols):
    problems = []
    if not os.path.isfile(path):
        return ["missing artifact %s" % path]
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != COLUMNS:
            return ["%s: columns are %r, expected %r" % (path, reader.fieldnames, COLUMNS)]
        rows = list(reader)
    spans = read_functions(symbols)
    sizes = {addr: size for addr, size, _n in spans}
    exact = {addr: name for addr, _s, name in spans}
    census = [r for r in rows if r["kind"] == "census"]
    if len(census) != 1:
        problems.append("%s: %d census rows, expected 1" % (path, len(census)))
    for r in rows:
        if r["kind"] == "census":
            continue
        if r["kind"] not in ("body", "edge", "stop", "slot31"):
            problems.append("%s: unknown kind %r" % (path, r["kind"]))
            continue
        try:
            body = int(r["body"], 16)
        except ValueError:
            problems.append("%s: unparsable body %r" % (path, r["body"]))
            continue
        if r["kind"] == "slot31" and body == 0:
            # a dispatch site in a hole between named bodies: the census keeps the site and says so
            if r["body_name"]:
                problems.append("%s: slot31 row at %s has no body but names %r"
                                % (path, r["site"], r["body_name"]))
            continue
        if body not in sizes:
            problems.append("%s: body 0x%08x is not in the symbol map" % (path, body))
            continue
        if r["body_name"] and r["body_name"] != exact[body]:
            problems.append("%s: body 0x%08x is %s, row says %s"
                            % (path, body, exact[body], r["body_name"]))
        if r["kind"] != "slot31" and str(r["body_size"]) != str(sizes[body]):
            problems.append("%s: body 0x%08x size %s, map says %s"
                            % (path, body, r["body_size"], sizes[body]))
        if r["kind"] == "edge":
            try:
                site = int(r["site"], 16)
            except ValueError:
                problems.append("%s: unparsable site %r" % (path, r["site"]))
                continue
            if not (body <= site < body + sizes.get(body, 0)):
                problems.append("%s: site 0x%08x outside body 0x%08x" % (path, site, body))
            if r["target_kind"] == "call":
                tgt = int(r["target"], 16)
                want = exact.get(tgt) or (owner(spans, tgt) or ("", "", 0))[1]
                if want and r["target_name"] and not r["target_name"].startswith(want):
                    problems.append("%s: edge 0x%08x target 0x%08x is %r, row says %r"
                                    % (path, site, tgt, want, r["target_name"]))
        if r["kind"] == "slot31" and r["form"] and "+0x7c" not in r["form"]:
            problems.append("%s: slot31 row at %s names %r" % (path, r["site"], r["form"]))
    bodies = [r for r in rows if r["kind"] == "body"]
    if census:
        if "bodies=%d" % len(bodies) not in census[0]["target_name"]:
            problems.append("%s: census says %r, artifact holds %d body rows"
                            % (path, census[0]["target_name"], len(bodies)))
        if "api=" not in census[0]["target_name"] or "slot31_sites=" not in census[0]["target_name"]:
            problems.append("%s: census row is missing its counts" % path)
    if not bodies:
        problems.append("%s: no body rows" % path)
    return problems


def apply_overrides(overrides, symbols):
    """Fold every named override row into the function map, names only (the record stays the overrides file).

    `XbePost` installs the same rows on a Ghidra import, so this is the dump-free way to keep the two files
    agreeing; it never renames a body that already carries a non-`FUN_` name.
    """
    with open(overrides, newline="", encoding="utf-8") as fh:
        named = {}
        for r in csv.DictReader(fh):
            name = (r.get("name") or "").strip()
            if name and not FUN.match(name):
                named[r["address"].lower()] = name
    renamed = kept = 0
    rows = []
    with open(symbols, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames
        for row in reader:
            want = named.get(row["address"].lower())
            if want and FUN.match(row["name"]):
                row["name"] = want
                renamed += 1
            elif want:
                kept += 1
            rows.append(row)
    with open(symbols, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(rows)
    print("%d name(s) folded into %s, %d already named" % (renamed, symbols, kept))
    return 0


def selftest():
    vr = helpers()[1]
    blob = bytes.fromhex("558bec83ec10e8f5ffffff")     # push ebp; mov ebp,esp; sub esp,0x10; call
    assert _chain(blob, 9, vr) is True
    stub = bytes.fromhex("8b4508e8") + b"\x00" * 8     # mov eax,[ebp+8]; call with nothing behind it
    assert _chain(stub, 10, vr) is None
    assert AUDIO_API.match("_DirectSoundCreate@12")
    assert AUDIO_API.match("?DSoundInitialize@CVoiceChatMode@XHVNamespace@@QAEJXZ")
    assert not AUDIO_API.match("FUN_0002ae00")
    assert not AUDIO_API.match("_CreateThread@24")
    assert FUN.match("FUN_0002ae00") and not FUN.match("XboxAudio_CreateDevice")
    print("selftest ok: chain boundary test, audio API matcher, unnamed-body matcher")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--image", help="the dump's default.xbe")
    ap.add_argument("--symbols", default=os.path.join(ROOT, "symbols", "xbox_functions.csv"))
    ap.add_argument("--modules", default=os.path.join(ROOT, "inventory", "function_modules.csv"))
    ap.add_argument("--tables", default=os.path.join(ROOT, "inventory", "xbe_class_tables.csv"))
    ap.add_argument("--table-names", default=os.path.join(ROOT, "inventory", "xbe_class_table_names.csv"))
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory", ARTIFACT))
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply-overrides", action="store_true",
                    help="fold the committed overrides into --symbols, names only; no dump")
    ap.add_argument("--overrides", default=os.path.join(ROOT, "symbols", "xbe_overrides.csv"))
    args = ap.parse_args()

    path = args.out if args.out.endswith(".csv") else os.path.join(args.out, ARTIFACT)
    if args.apply_overrides:
        return apply_overrides(args.overrides, args.symbols)
    if args.check:
        problems = check(path, args.symbols)
        if problems:
            for p in problems:
                print("FAIL xboxaudio-chain: %s" % p)
            return 1
        print("xboxaudio-chain: %s OK" % ARTIFACT)
        return 0
    if args.report:
        with open(path, newline="", encoding="utf-8") as fh:
            report(list(csv.DictReader(fh)))
        return 0
    if not args.image:
        ap.error("--image is required unless --check/--report")
    rows = measure(args.image, args.symbols, args.modules, args.tables, args.table_names,
                   args.overrides)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print("%d rows -> %s" % (len(rows), path))
    report(rows)
    return 0

if __name__ == "__main__":
    sys.exit(main())
