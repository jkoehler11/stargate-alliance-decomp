#!/usr/bin/env python3
"""Which class builds an interface object, and what the owed representatives' slots hold.

`tools/class-interface-behaviour.py` costed the per-body read the family census owed and read the
two channels a slot carries (the assert file its map name holds, and what the body calls); neither
names an interface, because a callee's file is the file of the code a slot *calls*. The two channels
the page says are left are **a member field that holds the object** and **a caller whose class is
known**, and this tool reads both, in one pass over the store sites the provenance census already
measured.

  * **the installer, one level in.** `inventory/class_store_provenance.csv` records the store site of
    every table. A site falls inside a body, and a body that also installs a class table is that
    class's constructor (`inventory/class_constructors.csv`) -- so a table stored by a constructor is
    stored *inside* a named class, which is the member-field channel read end to end. For the tables
    whose store body is not a constructor, the read goes one call out: which constructor bodies call
    the body that holds the store.
  * **the owed representatives, one slot at a time.** Every slot of the owed families is classified
    (`__purecall`, a stub of 8 bytes or less, a body the map has a row for, a U40 gap head, a dword
    outside the map), and each mapped body is named, priced, called, and located -- including whether
    another representative holds the same body, which is the interface-identity question the twins
    census answered for the gap heads only.

The store's `this` and the caller's `this` are kept apart: a constructor that calls a helper which
stores `[this+off]` builds the interface inside that class's constructor call graph, and whether the
store writes the constructor's own object or a sub-object the helper was handed is *not* decided
here. That distinction is what a name would need, and it is recorded as the residual.

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-interface-installers.py \\
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-interface-installers.py --report
    python3 tools/class-interface-installers.py --write-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/class-interface-installers.py --check-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/class-interface-installers.py --check      # synthetic rows, no dump, writes nothing

Addresses, slot indices, class names and counts only; no package or image content is written.
"""
import argparse
import collections
import csv
import importlib.util
import os
import re
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

PLAIN = os.path.join(ROOT, "inventory", "class_plain_tables.csv")
SHAPES = os.path.join(ROOT, "inventory", "class_interface_shapes.csv")
FAMILIES = os.path.join(ROOT, "inventory", "class_interface_families.csv")
PROVENANCE = os.path.join(ROOT, "inventory", "class_store_provenance.csv")
CTORS = os.path.join(ROOT, "inventory", "class_constructors.csv")
HEADS = os.path.join(ROOT, "inventory", "gap_heads.csv")
SYMBOLS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
OUT_INSTALLERS = os.path.join(ROOT, "inventory", "class_interface_installers.csv")
OUT_SLOTS = os.path.join(ROOT, "inventory", "class_interface_rep_bodies.csv")
DOC = os.path.join(ROOT, "docs", "systems", "CLASS_REGISTRATION.md")
DOC_A = ("<!-- generated:class-interface-installers -->", "<!-- /generated:class-interface-installers -->")
DOC_B = ("<!-- generated:class-interface-rep-bodies -->", "<!-- /generated:class-interface-rep-bodies -->")

FIELDS_A = ("table_va", "slots", "role", "family", "stores", "holders", "holder_class", "holder_callers",
            "caller_classes", "caller_forms", "place", "depth", "verdict", "evidence")
FIELDS_B = ("rep_va", "family", "slot", "body_va", "body_name", "body_size", "kind", "gap_verdict",
            "named", "callees", "callee_files", "literal_head", "shared_with", "verdict")
STUB_BYTES = 8
PURECALL = 0x003E5501
SITE = re.compile(r"store site\(s\) at ([0-9a-fx,]+)")
STORE_SITES = re.compile(r"(\d+) store site\(s\)")
TOKEN = re.compile(r"^([A-Za-z0-9]+)_")


def sibling(name):
    """A hyphenated sibling tool, imported by path (the repo names its tools with hyphens)."""
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"),
                                                  os.path.join(ROOT, "tools", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


def load_ctors(path):
    """{body_va: {"classes": set, "forms": set}} for every body that installs a class table."""
    out = collections.defaultdict(lambda: {"classes": set(), "forms": set()})
    for row in read_csv(path):
        try:
            va = int(row["function_va"], 16)
        except (KeyError, ValueError):
            continue
        out[va]["forms"].add(row.get("form") or "")
        if row.get("class"):
            out[va]["classes"].add(row["class"])
    return out


def gap_heads(path):
    out = {}
    for row in read_csv(path):
        try:
            out[int(row["head_va"], 16)] = row.get("verdict") or ""
        except (KeyError, ValueError):
            continue
    return out


class Bodies:
    """The map's body extents, and the body a site falls inside."""

    def __init__(self, sizes):
        self.sizes = sizes
        self.starts = sorted(v for v in sizes if sizes[v])

    def at(self, va):
        lo, hi, best = 0, len(self.starts) - 1, None
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.starts[mid] <= va:
                best = self.starts[mid]
                lo = mid + 1
            else:
                hi = mid - 1
        if best is None or va >= best + self.sizes[best]:
            return None
        return best


def e8_targets(blob, base, lo=0, hi=None):
    """{target_va: [site_va, ...]} for every `E8 rel32` in `blob` (a .text slice starting at `lo`)."""
    out = collections.defaultdict(list)
    hi = len(blob) if hi is None else hi
    i = blob.find(b"\xe8", lo)
    while i >= 0 and i + 5 <= hi:
        rel = struct.unpack_from("<i", blob, i + 1)[0]
        out[base + i + 5 + rel].append(base + i)
        i = blob.find(b"\xe8", i + 1)
    return out


THIS_MOVE = re.compile(r"^mov([a-z]{2,3}),ecx$")
ECX_SET = re.compile(r"^(?:mov|lea)\s+ecx,(.+)$")
MEM = re.compile(r"^\[?([a-z]{2,3})(?:([+-])0x([0-9a-f]+))?\]?$")


def this_register(insns):
    """The register a body parks its `this` in (`mov esi,ecx` at entry), or `ecx` itself.

    A ctor receives its object in `ecx` and keeps it in the first register the body copies it to.
    Only a `mov` counts: a `push ecx` saves the argument without naming the object.
    """
    for _at, text in insns[:20]:
        m = THIS_MOVE.match(text.replace(" ", ""))
        if m:
            return m.group(1)
    return "ecx"


def ecx_place(insns, site, this_reg):
    """What the caller loads into `ecx` before the call at `site`: this, a member, or a stack temporary."""
    idx = next((k for k, (at, _t) in enumerate(insns) if at == site), None)
    if idx is None:
        return ""
    for at, text in reversed(insns[max(0, idx - 10):idx]):
        m = ECX_SET.match(text.replace("  ", " ").strip())
        if not m:
            continue
        op = m.group(1).replace(" ", "").replace("DWORDPTR", "").replace("BYTEPTR", "")
        op = re.sub(r"^(dword|byte|word)ptr", "", op, flags=re.I)
        if "dwordptr" in m.group(1).replace(" ", "").lower():
            op = op.replace("dwordptr", "")
        mem = MEM.match(op)
        if not mem:
            return op[:20] or "?"
        reg, sign, disp = mem.group(1), mem.group(2), mem.group(3)
        if reg == "esp":
            return "stack"
        if this_reg and reg == this_reg:
            return "this" if not disp else "this%s0x%s" % (sign, disp)
        return "member(%s)" % (reg if not disp else "%s%s0x%s" % (reg, sign, disp))
    return "?"

def parse_sites(evidence, expected=None):
    """The store site addresses a provenance `evidence` sentence names."""
    m = SITE.search(evidence or "")
    if not m:
        return []
    try:
        return [int(x, 16) for x in m.group(1).split(",")]
    except ValueError:
        return []


def installer_verdict(holders, depth1, caller_forms):
    """The five answers, in the order that makes the nearest class win.

    A store body called only from a destructor is `dtor-caller`, not `ctor-caller`: a destructor resets
    a member's vtable before destroying it, so that edge says the interface is a member too but says it
    from the teardown path.
    """
    if depth1:
        return "ctor-holder"
    if caller_forms.get("ctor"):
        return "ctor-caller"
    if caller_forms:
        return "dtor-caller"
    if holders:
        return "unnamed-holder"
    return "no-site"


# --------------------------------------------------------------------------- artifact A
def harvest_installers(plain, prov, ctors, bodies, calls, shapes, fams, disasm=None, sizes=None):
    out = []
    for r in plain:
        tva = r["table_va"]
        p = prov.get(tva, {})
        sites = parse_sites(p.get("evidence") or "")
        holders = sorted({h for h in (bodies.at(s) for s in sites) if h})
        holder_classes, caller_classes, holder_callers = set(), set(), set()
        caller_forms, places = collections.Counter(), []
        cache = {}
        for h in holders:
            holder_classes |= ctors.get(h, {}).get("classes", set())
            for site in calls.get(h, []):
                c = bodies.at(site)
                if c is None or not ctors.get(c, {}).get("classes"):
                    continue
                caller_classes |= ctors[c]["classes"]
                holder_callers.add(c)
                for form in ctors[c]["forms"]:
                    caller_forms[form or "?"] += 1
                if disasm is not None and ctors[c]["forms"] & {"ctor"}:
                    if c not in cache:
                        cache[c] = list(disasm(c, (sizes or {}).get(c) or (site - c + 6)))
                    place = ecx_place(cache[c], site, this_register(cache[c]))
                    if place and place not in places:
                        places.append(place)
        verdict = installer_verdict(holders, bool(holder_classes), caller_forms)
        shape = shapes.get(tva, {})
        fam = fams.get(tva, {})
        out.append({
            "table_va": tva,
            "slots": r.get("slots") or "",
            "role": shape.get("role", ""),
            "family": shape.get("family", ""),
            "stores": str(len(sites)),
            "holders": str(len(holders)),
            "holder_class": " ".join(sorted(holder_classes)),
            "holder_callers": " ".join("%#010x" % h for h in sorted(holder_callers)),
            "caller_classes": " ".join(sorted(caller_classes)),
            "caller_forms": " ".join("%s:%d" % kv for kv in sorted(caller_forms.items())),
            "place": " ".join(sorted(places, key=lambda x: (x == "stack", x))[:3]),
            "depth": "1" if holder_classes else ("2" if caller_classes else ""),
            "verdict": verdict,
            "evidence": "%d store site(s) in %d body(ies); holder class %s; caller class %s; ecx %s"
                        % (len(sites), len(holders), " ".join(sorted(holder_classes)) or "-",
                           " ".join(sorted(caller_classes)) or "-",
                           " ".join(sorted(places)[:3]) or "-"),
        })
    tallies = collections.Counter(x["verdict"] for x in out)
    out.append({
        "table_va": "census", "slots": str(sum(int(x["slots"] or 0) for x in out)),
        "role": "", "family": "",
        "stores": str(sum(int(x["stores"]) for x in out)),
        "holders": str(sum(int(x["holders"]) for x in out)),
        "holder_class": str(sum(1 for x in out if x["holder_class"])),
        "holder_callers": str(sum(1 for x in out if x["holder_callers"])),
        "caller_classes": str(sum(1 for x in out if x["caller_classes"])),
        "caller_forms": "", "place": "", "depth": "",
        "verdict": "census",
        "evidence": "the 232 interface tables: %d ctor-holder, %d ctor-caller, %d dtor-caller, "
                    "%d unnamed-holder, %d no-site"
                    % (tallies["ctor-holder"], tallies["ctor-caller"], tallies["dtor-caller"],
                       tallies["unnamed-holder"], tallies["no-site"]),
    })
    return out


# --------------------------------------------------------------------------- artifact B
def harvest_slots(fams, bodies, names, sizes, heads, image, literals):
    owed = [f for f in fams if (f.get("read_owed") or "") == "yes"]
    owner = collections.defaultdict(set)
    vectors = {}
    for f in owed:
        tva = int(f["rep_va"], 16)
        n = int(f.get("rep_slots") or 0)
        vec = [struct.unpack("<I", image.read_va(tva + 4 * k, 4))[0] for k in range(n)]
        vectors[tva] = vec
        for k, va in enumerate(vec):
            if va in sizes and (sizes.get(va) or 0) > STUB_BYTES and va != PURECALL:
                owner[va].add(tva)
    out = []
    for tva in sorted(vectors):
        fam = next(f for f in owed if int(f["rep_va"], 16) == tva)
        for k, va in enumerate(vectors[tva]):
            name = names.get(va, "")
            if va == PURECALL or name == "__purecall":
                kind, verdict = "pure", "pure"
            elif va not in sizes:
                if va in heads:
                    kind, verdict = "gap", "gap-head"
                else:
                    kind, verdict = "outside", "outside"
            elif (sizes.get(va) or 0) <= STUB_BYTES:
                kind, verdict = "stub", "stub"
            else:
                kind = "mapped"
                verdict = "mapped-named" if name and not name.startswith(("FUN_", "thunk_")) else "mapped-unnamed"
            callees, files, lits = [], [], []
            if kind == "mapped":
                blob = image.read_va(va, sizes[va]) or b""
                for tgt in e8_targets(blob, va).keys():
                    cname = names.get(tgt, "")
                    if not cname:
                        continue
                    if cname not in callees:
                        callees.append(cname)
                    tok = TOKEN.match(cname)
                    if tok and tok.group(1) not in files:
                        files.append(tok.group(1))
                body = image.read_va(va, sizes[va] or 0) or b""
                lits = literals(va, body)
            out.append({
                "rep_va": "%#010x" % tva,
                "family": fam.get("family", ""),
                "slot": str(k),
                "body_va": "%#010x" % va,
                "body_name": name,
                "body_size": str(sizes.get(va, "")) if va in sizes else "",
                "kind": kind,
                "gap_verdict": heads.get(va, "") if kind == "gap" else "",
                "named": "yes" if verdict == "mapped-named" else ("no" if kind == "mapped" else ""),
                "callees": " ".join(callees[:6]),
                "callee_files": " ".join(files[:4]),
                "literal_head": (lits[0][:60] if lits else ""),
                "shared_with": " ".join("%#010x" % t for t in sorted(owner.get(va, set()) - {tva})),
                "verdict": verdict,
            })
    kinds = collections.Counter(x["kind"] for x in out)
    distinct = {x["body_va"] for x in out if x["kind"] == "mapped"}
    shared = {x["body_va"] for x in out if x["shared_with"]}
    reps_owed = len({x["rep_va"] for x in out})
    out.append({
        "rep_va": "census", "family": "", "slot": str(len(out)),
        "body_va": "", "body_name": "", "body_size": "",
        "kind": "census", "gap_verdict": "", "named": "", "callees": "", "callee_files": "",
        "literal_head": "",
        "shared_with": "%d distinct mapped body(ies); %d held by two or more representatives"
                       % (len(distinct), len(shared)),
        "verdict": "the %d owed representatives: %s" % (reps_owed, ", ".join("%s %d" % kv for kv in sorted(kinds.items()))),
    })
    return out


# --------------------------------------------------------------------------- rendering
def counts(rows, key):
    return collections.Counter(r[key] for r in rows)


def render_installers(rows, fams):
    body = [r for r in rows if r["table_va"] != "census"]
    owed = {f["rep_va"] for f in fams if (f.get("read_owed") or "") == "yes"}
    v = counts(body, "verdict")
    lines = ["| verdict | tables | anonymous | of the %d representatives | what it means |" % len(owed),
             "|---|---|---|---|---|"]
    meaning = {
        "ctor-holder": "the store itself sits in a body that installs a class table",
        "ctor-caller": "the store body is called by a body that installs a class table, and a ctor is one",
        "dtor-caller": "only destructors call the store body: the teardown path, not the build",
        "unnamed-holder": "neither the store body nor any caller installs a class table",
        "no-site": "the provenance census names no store site",
    }
    for key in ("ctor-holder", "ctor-caller", "dtor-caller", "unnamed-holder", "no-site"):
        sel = [r for r in body if r["verdict"] == key]
        lines.append("| `%s` | %d | %d | %d | %s |"
                     % (key, len(sel), sum(1 for r in sel if r["role"] == "anonymous"),
                        sum(1 for r in sel if r["table_va"] in owed), meaning[key]))
    holders = [r for r in body if r["verdict"] == "ctor-holder"]
    callers = [r for r in body if r["verdict"] == "ctor-caller"]
    anon_callers = [r for r in callers if r["role"] == "anonymous"]
    anon_reached = [r for r in body if r["role"] == "anonymous"
                    and r["verdict"] in ("ctor-holder", "ctor-caller")]
    lines.append("")
    lines.append("**%d of the 232 interface tables are stored inside a class constructor and %d of those are the "
                 "member-held tables the field already names, so that channel is the member-field channel read "
                 "end to end; %d more are stored by a body a constructor calls, %d of them anonymous and %d owed a "
                 "per-body read.**"
                 % (len(holders), sum(1 for r in holders if r["role"] == "member"), len(callers),
                    len(anon_callers), sum(1 for r in callers if r["table_va"] in owed)))
    lines.append("")
    if anon_callers:
        lines.append("| table | slots | how it is stored | the class that builds it | what the constructor puts "
                     "in `ecx` |")
        lines.append("|---|---|---|---|---|")
        for r in sorted(anon_reached, key=lambda r: (-int(r["slots"] or 0), r["table_va"])):
            lines.append("| `%s`%s | %s | `%s` | %s | %s |"
                         % (r["table_va"], " (owed)" if r["table_va"] in owed else "", r["slots"],
                            r["verdict"], (r["holder_class"] or r["caller_classes"]).replace(" ", ", "),
                            r["place"] or "-"))
    return "\n".join(lines)


def render_slots(rows):
    body = [r for r in rows if r["rep_va"] != "census"]
    census = next((r for r in rows if r["rep_va"] == "census"), {})
    kinds = counts(body, "kind")
    distinct = collections.defaultdict(set)
    for r in body:
        if r["kind"] == "mapped":
            distinct[r["body_va"]]
    lines = ["| what a representative's slot holds | slots | distinct bodies |", "|---|---|---|"]
    for key in ("mapped", "gap", "outside", "stub", "pure"):
        lines.append("| `%s` | %d | %s |"
                     % (key, kinds[key], len({r['body_va'] for r in body if r['kind'] == key})
                        if key != "outside" else len({r['body_va'] for r in body if r['kind'] == key})))
    lines.append("")
    lines.append("**The %d owed representatives hold %d slots: %d mapped over %d distinct bodies, %d U40 gap "
                 "heads, %d outside the map, %d stubs and %d purecalls; %d mapped slots carry a name the map "
                 "holds and %d bodies are held by two or more representatives.**"
                 % (len({r["rep_va"] for r in body}), len(body), kinds["mapped"],
                    len({r["body_va"] for r in body if r["kind"] == "mapped"}), kinds["gap"], kinds["outside"],
                    kinds["stub"], kinds["pure"],
                    sum(1 for r in body if r["verdict"] == "mapped-named"),
                    len({r["body_va"] for r in body if r["shared_with"]})))
    shared = collections.defaultdict(lambda: {"reps": set(), "name": "", "slots": set()})
    for r in body:
        if r["shared_with"]:
            shared[r["body_va"]]["reps"].add(r["rep_va"])
            shared[r["body_va"]]["reps"].update(r["shared_with"].split())
            shared[r["body_va"]]["name"] = r["body_name"]
            shared[r["body_va"]]["slots"].add(r["slot"])
    if shared:
        lines.append("")
        lines.append("| body | name | representatives that hold it |")
        lines.append("|---|---|---|")
        for va in sorted(shared):
            lines.append("| `%s` | %s | %s |"
                         % (va, shared[va]["name"] or "-",
                            " ".join("`%s`" % x for x in sorted(shared[va]["reps"]))))
    return "\n".join(lines)


def write_artifact(rows, path, fields):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def splice_doc(path, block, marks):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(marks[0]), text.find(marks[1])
    if i < 0 or j < 0:
        return None
    return text[:i] + marks[0] + "\n" + block + "\n" + text[j:]


def block_from_csv(path, render, extra=None):
    rows = read_csv(path)
    if not rows:
        return None
    return render(rows, extra) if extra is not None else render(rows)


# --------------------------------------------------------------------------- self-test
def self_check():
    bad = []
    sizes = {0x1000: 0x20, 0x1100: 0x10, 0x1200: 0x40}
    b = Bodies(sizes)
    if b.at(0x1000) != 0x1000 or b.at(0x101f) != 0x1000:
        bad.append("a site inside a body is that body")
    if b.at(0x1020) is not None or b.at(0x10ff) is not None:
        bad.append("a site in the hole between bodies belongs to no body")
    if b.at(0x1200 + 0x3f) != 0x1200 or b.at(0x1240) is not None:
        bad.append("a body ends at its own size")
    if parse_sites("4 store site(s) at 0x16df0,0x19218") != [0x16DF0, 0x19218]:
        bad.append("the store sites are read out of the evidence sentence")
    if parse_sites("no table here") != []:
        bad.append("a provenance row with no site list yields none")
    if installer_verdict([0x10], True, {"ctor": 1}) != "ctor-holder":
        bad.append("the ctor that stores wins over the ctor that calls")
    if installer_verdict([0x10], False, {"ctor": 1}) != "ctor-caller":
        bad.append("a store body called by a ctor is the second depth")
    if installer_verdict([0x10], False, {"dtor": 1}) != "dtor-caller":
        bad.append("a store body only a destructor calls is the teardown path")
    if installer_verdict([0x10], False, {"ctor": 1, "dtor": 1}) != "ctor-caller":
        bad.append("one ctor caller is enough when a dtor also calls it")
    if installer_verdict([0x10], False, {}) != "unnamed-holder":
        bad.append("a store body with no class anywhere is unnamed")
    if installer_verdict([], False, {}) != "no-site":
        bad.append("a table with no store site has no installer")
    calls = e8_targets(b"\xe8\x00\x00\x00\x00\x90\xe8\xf0\xff\xff\xff", 0x2000)
    if dict(calls) != {0x2005: [0x2000], 0x1FFB: [0x2006]}:
        bad.append("E8 targets are the next instruction plus the displacement")
    rows_a = []
    for tva, v, cls in (("0x004c0001", "ctor-holder", "A"), ("0x004c0002", "ctor-caller", ""),
                        ("0x004c0003", "dtor-caller", ""), ("0x004c0004", "unnamed-holder", ""),
                        ("0x004c0005", "no-site", "")):
        rows_a.append({"table_va": tva, "slots": "2", "role": "anonymous", "family": "0x1", "stores": "1",
                       "holders": "1", "holder_class": cls, "holder_callers": "0x00000001" if cls else "",
                       "caller_classes": "B" if v == "ctor-caller" else "",
                       "caller_forms": "ctor:1" if v == "ctor-caller" else "",
                       "place": "this+0x10" if v == "ctor-caller" else "",
                       "depth": "1" if cls else "",
                       "verdict": v, "evidence": ""})
    rows_a.append({"table_va": "census", "slots": "9", "role": "", "family": "", "stores": "4", "holders": "4",
                   "holder_class": "1", "holder_callers": "1", "caller_classes": "1", "caller_forms": "1",
                   "place": "", "depth": "",
                   "verdict": "census", "evidence": ""})
    fams = [{"read_owed": "yes", "rep_va": r["table_va"], "family": "0x1"} for r in rows_a[:-1]]
    text = render_installers(rows_a, fams)
    for want in ("ctor-holder", "ctor-caller", "dtor-caller", "representative"):
        if want not in text:
            bad.append("installer render lacks %r" % (want,))
    rows_b = []
    for k, (kind, verdict, shared) in enumerate((("mapped", "mapped-unnamed", "0x004c0002"),
                                                 ("gap", "gap-head", ""), ("stub", "stub", ""),
                                                 ("pure", "pure", ""), ("outside", "outside", ""),
                                                 ("mapped", "mapped-named", ""))):
        rows_b.append({"rep_va": "0x004c0001" if k < 5 else "0x004c0002", "family": "0x1", "slot": str(k),
                       "body_va": "0x0010000" if kind == "mapped" else "%#010x" % (0x20000 + k),
                       "body_name": "Engine_UnChan" if verdict == "mapped-named" else "",
                       "body_size": "32" if kind == "mapped" else "", "kind": kind,
                       "gap_verdict": "body" if kind == "gap" else "", "named": "",
                       "callees": "", "callee_files": "", "literal_head": "", "shared_with": shared,
                       "verdict": verdict})
    rows_b.append({"rep_va": "census", "family": "", "slot": "6", "body_va": "", "body_name": "",
                   "body_size": "", "kind": "census", "gap_verdict": "", "named": "", "callees": "",
                   "callee_files": "", "literal_head": "", "shared_with": "", "verdict": ""})
    text = render_slots(rows_b)
    for want in ("`mapped`", "`gap`", "representatives that hold it"):
        if want not in text:
            bad.append("slot render lacks %r" % (want,))
    tmp = tempfile.mkdtemp()
    for rows, fields, name in ((rows_a, FIELDS_A, "a.csv"), (rows_b, FIELDS_B, "b.csv")):
        path = os.path.join(tmp, name)
        write_artifact(rows, path, fields)
        if read_csv(path) != rows:
            bad.append("%s round trip differs" % name)
    if bad:
        print("FAIL class-interface-installers: " + "; ".join(bad))
        return 1
    print("ok   class-interface-installers: the body lookup, the site parse, the five installer verdicts, "
          "the ecx placement, the five slot kinds and both artifact round trips agree with the synthetic rows")
    return 0


# --------------------------------------------------------------------------- main
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
        blocks = []
        for path, render, marks, extra in ((OUT_INSTALLERS, render_installers, DOC_A, read_csv(FAMILIES)),
                                           (OUT_SLOTS, render_slots, DOC_B, None)):
            block = block_from_csv(path, render, extra)
            if block is None:
                print("FAIL class-interface-installers: %s is missing" % path)
                return 1
            blocks.append((block, marks))
        text = open(args.check_doc, encoding="utf-8").read()
        for block, marks in blocks:
            i, j = text.find(marks[0]), text.find(marks[1])
            if i < 0 or j < 0:
                print("FAIL class-interface-installers: %s has no %s block" % (args.check_doc, marks[0]))
                return 1
            if text[i:j + len(marks[1])] != marks[0] + "\n" + block + "\n" + marks[1]:
                print("FAIL class-interface-installers: %s's %s block does not reproduce"
                      % (args.check_doc, marks[0]))
                return 1
        print("ok   class-interface-installers: both blocks of %s reproduce from the committed CSVs"
              % args.check_doc)
        return 0
    if args.report or args.write_doc:
        blocks = [(block_from_csv(OUT_INSTALLERS, render_installers, read_csv(FAMILIES)), DOC_A),
                  (block_from_csv(OUT_SLOTS, render_slots), DOC_B)]
        for block, marks in blocks:
            if block is None:
                print("FAIL class-interface-installers: %s is missing" % marks[0])
                return 1
        if args.report:
            print("\n\n".join(b for b, _ in blocks))
            return 0
        for block, marks in blocks:
            new = splice_doc(args.write_doc, block, marks)
            if new is None:
                print("FAIL class-interface-installers: %s has no %s markers" % (args.write_doc, marks[0]))
                return 1
            if new != open(args.write_doc, encoding="utf-8").read():
                open(args.write_doc, "w", encoding="utf-8").write(new)
                print("wrote %s (%s)" % (args.write_doc, marks[0]))
        return 0
    if not args.xbe:
        ap.error("--xbe is required to read the image (or use --report/--check-doc/--check)")

    xbemod = sibling("xbe")
    evidence_mod = sibling("class-ctor-evidence")
    image = xbemod.Xbe(args.xbe)
    names, sizes = load_symbols(SYMBOLS)
    bodies = Bodies(sizes)
    text = next(s for s in image.sections if s.name == ".text")
    calls = e8_targets(image.data[text.foff:text.foff + text.fsize], text.va)
    in_text = lambda va: text.va <= va < text.va + text.fsize

    def literals(va, blob):
        return evidence_mod.literals(image, blob, va, in_text)

    shapes = {r["table_va"]: r for r in read_csv(SHAPES)}
    fams = {r["rep_va"]: r for r in read_csv(FAMILIES)}
    fam_rows = read_csv(FAMILIES)
    plain = [r for r in read_csv(PLAIN) if (r.get("verdict") or "").startswith("interface")]
    prov = {r["table_va"]: r for r in read_csv(PROVENANCE)}

    names_mod = sibling("class-table-names")
    img = names_mod.Image()
    img.xbe, img.data = image, image.data
    img.size_of = lambda va: sizes.get(va)
    rows_a = harvest_installers(plain, prov, load_ctors(CTORS), bodies, calls, shapes, fams,
                                img.disasm, sizes)
    rows_b = harvest_slots(fam_rows, bodies, names, sizes, gap_heads(HEADS), image, literals)
    if args.out:
        pa = os.path.join(args.out, os.path.basename(OUT_INSTALLERS))
        pb = os.path.join(args.out, os.path.basename(OUT_SLOTS))
        write_artifact(rows_a, pa, FIELDS_A)
        write_artifact(rows_b, pb, FIELDS_B)
        print("wrote %s (%d rows) and %s (%d rows)" % (pa, len(rows_a), pb, len(rows_b)))
    else:
        print(render_installers(rows_a, fam_rows))
        print()
        print(render_slots(rows_b))
    return 0


if __name__ == "__main__":
    sys.exit(main())
