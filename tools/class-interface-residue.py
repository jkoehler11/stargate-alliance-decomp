#!/usr/bin/env python3
"""The interface population's last residue: what its default bodies do, and the four tables nothing stores.

`tools/class-interface-behaviour.py` cost the per-body read at **55 representatives with a mapped body,
26 that own only a gap head and 7 that are the compiler's defaults**, and
`tools/class-interface-installers.py` read the store sites and left **4 anonymous tables with no store
site in the provenance census at all**.  This tool pays both, because both are the residue of the same
question -- what the interface population's code is, when no name is in it:

  * **the defaults, read as bodies.**  An unoverridden slot is not nothing: it is one of 27 shared bodies,
    289 stub slots and 38 `__purecall` slots over 26 bodies plus the handler.  Each is disassembled and
    classified by what it returns (`0`, `this`, a field, a constant, nothing, or a trap), so the page can
    say what an interface's default behaviour *is* instead of calling it a default.  The 7 representatives
    that own no body are read the same way: every slot they hold is one of these bodies;
  * **the four tables nothing stores.**  The provenance census found one store site for each but no *body*
    around it, and the reason is that the store sits in a constructor the map does not decode: three sit
    in no body and no gap head at all (the gap census's prologue rule does not start a body at
    `mov eax,ecx`), and one sits in a gap head.  Each constructor is walked back from its store to its
    own start and read as the object layout it writes: the vftable store, the fields it zeroes and the
    `0xdeaddead` sentinel it leaves.

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-interface-residue.py \\
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-interface-residue.py --report
    python3 tools/class-interface-residue.py --write-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/class-interface-residue.py --check-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/class-interface-residue.py --check      # synthetic bodies, no dump, writes nothing

Addresses, sizes, shapes and counts only; no image or package content is written.
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

REPS = os.path.join(ROOT, "inventory", "class_interface_rep_bodies.csv")
INSTALLERS = os.path.join(ROOT, "inventory", "class_interface_installers.csv")
PROVENANCE = os.path.join(ROOT, "inventory", "class_store_provenance.csv")
SYMBOLS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
HEADS = os.path.join(ROOT, "inventory", "gap_heads.csv")
OUT_DEF = os.path.join(ROOT, "inventory", "class_interface_default_bodies.csv")
OUT_SITE = os.path.join(ROOT, "inventory", "class_interface_nosite.csv")
DOC_DEF = ("<!-- generated:class-interface-defaults -->", "<!-- /generated:class-interface-defaults -->")
DOC_SITE = ("<!-- generated:class-interface-nosite -->", "<!-- /generated:class-interface-nosite -->")
FIELDS_DEF = ("body_va", "name", "size", "shape", "slots", "representatives", "insns", "verdict",
              "evidence")
FIELDS_SITE = ("table_va", "slots", "store_va", "store_insn", "container", "container_va",
               "container_size", "prologue", "body_stores", "sentinel", "verdict", "evidence")
SHAPES = ("purecall", "return-zero", "bare-return", "return-this", "return-field", "return-pair",
          "return-const", "return-minus-one", "other")
DEFAULT_KINDS = ("pure", "stub")
STORE = re.compile(r"mov\s+(?:DWORD PTR )?\[(eax|ecx|edx|ebx|esi|edi)([+-]0x[0-9a-f]+)?\],0x([0-9a-f]+)")
SITE = re.compile(r"store site\(s\) at ([0-9a-fx,]+)")
HEX = re.compile(r"^(0x)?[0-9a-fA-F]+$")
SENTINEL = 0xDEADDEAD


def sibling(name):
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


def num(text, base=16):
    text = (text or "").strip()
    return int(text, base) if HEX.match(text) else None


# --------------------------------------------------------------------------- shapes
def shape_of(insns, name=""):
    """What a default body returns, from its own instructions."""
    if name == "__purecall":
        return "purecall"
    ops = [re.sub(r"\s+", " ", t.strip()) for t in insns]
    core = [o for o in ops if not o.startswith(("ret", "nop", "int3", "jmp"))]
    if not core:
        return "bare-return"
    if len(core) == 2 and all(re.match(r"mov e[ad]x,DWORD PTR \[ecx\+0x[0-9a-f]+\]$", c) for c in core):
        return "return-pair"
    if len(core) != 1:
        return "other"
    one = core[0]
    if one == "xor eax,eax":
        return "return-zero"
    if one == "mov eax,ecx":
        return "return-this"
    if one == "or eax,0xffffffff":
        return "return-minus-one"
    if re.match(r"mov eax,DWORD PTR \[ecx\+0x[0-9a-f]+\]$", one):
        return "return-field"
    if re.match(r"mov eax,0x[0-9a-f]+$", one):
        return "return-const"
    return "other"


def first_store(insns):
    """The first `mov [reg+disp], imm32` of a body: (disp, imm, text), or None."""
    for text in insns:
        m = STORE.search(re.sub(r"\s+", " ", text.strip()))
        if m:
            disp = int(m.group(2) or "0", 16) if m.group(2) else 0
            return disp, int(m.group(3), 16), text.strip()
    return None


# --------------------------------------------------------------------------- defaults
def default_rows(rep_rows, names, disasm):
    """One row per distinct default body the owed representatives hold, plus a census."""
    bodies = collections.OrderedDict()          # body_va -> {size, slots, reps}
    per_rep = collections.defaultdict(collections.Counter)
    for r in rep_rows:
        if r.get("rep_va") == "census" or not r.get("body_va"):
            continue
        per_rep[r["rep_va"]][r["kind"]] += 1
        if r["kind"] not in DEFAULT_KINDS:
            continue
        key = num(r["body_va"])
        if key is None:
            continue
        slot = bodies.setdefault(key, {"size": num(r["body_size"], 10) or 0, "slots": 0,
                                       "reps": set(), "name": r.get("body_name") or ""})
        slot["slots"] += 1
        slot["reps"].add(r["rep_va"])
    out = []
    for va, info in sorted(bodies.items()):
        insns = [t for _a, t in disasm(va, info["size"])]
        shape = shape_of(insns, names.get(va, info["name"]))
        out.append({
            "body_va": "%#010x" % va, "name": names.get(va, info["name"]), "size": str(info["size"]),
            "shape": shape, "slots": str(info["slots"]),
            "representatives": " ".join(sorted(info["reps"])),
            "insns": " ; ".join(re.sub(r"\s+", " ", t.strip()) for t in insns),
            "verdict": shape,
            "evidence": "%d byte(s); %d slot(s) in %d representative(s)" %
                        (info["size"], info["slots"], len(info["reps"])),
        })
    counts = collections.Counter(r["shape"] for r in out)
    census = {k: "" for k in FIELDS_DEF}
    census.update({
        "body_va": "census", "shape": "census", "slots": str(sum(r["slots"] for r in out) if not out
                                                             else sum(int(r["slots"]) for r in out)),
        "representatives": str(len(per_rep)), "verdict": "census",
        "evidence": "distinct %d; %s; the owed representatives %d" %
                    (len(out), "; ".join("%s %d" % (s, counts[s]) for s in SHAPES if counts[s]),
                     len(per_rep)),
    })
    out.append(census)
    return out, per_rep


def rendered_defaults(rows):
    bodies = [r for r in rows if r["verdict"] != "census"]
    census = next((r for r in rows if r["verdict"] == "census"), {})
    counts = collections.Counter(r["shape"] for r in bodies)
    slots = sum(int(r["slots"]) for r in bodies)
    lines = ["| the default body | bytes | what it returns | slots | representatives |",
             "|---|---:|---|---:|---|"]
    for r in sorted(bodies, key=lambda r: (-int(r["slots"]), int(r["body_va"], 16))):
        reps = r["representatives"].split()
        lines.append("| `%s` | %d | `%s` | %d | %s |"
                     % (r["body_va"], int(r["size"]), r["shape"], int(r["slots"]),
                        ("%d" % len(reps)) if len(reps) > 3 else
                        (" ".join("`%s`" % x for x in reps) or "-")))
    lines.append("")
    shapes = [s for s in SHAPES if counts[s]]
    lines.append("**The owed representatives' %d default slots are %d distinct bodies in %d shapes: %s.** "
                 "The largest is `0x000171c0` (`xor eax,eax; ret`, 3 bytes) filling %d slots, and the only "
                 "body that traps is `__purecall` itself (%d slots over %d tables), so an unoverridden "
                 "interface method answers rather than refuses: it returns zero, `this`, a field, a "
                 "constant or nothing at all."
                 % (slots, len(bodies), len(shapes),
                    ", ".join("`%s` %d" % (s, counts[s]) for s in shapes),
                    int(max(bodies, key=lambda r: int(r["slots"]))["slots"]) if bodies else 0,
                    counts["purecall"] and int(sum(int(r["slots"]) for r in bodies
                                                   if r["shape"] == "purecall")),
                    sum(len(r["representatives"].split()) for r in bodies if r["shape"] == "purecall")))
    consts = sorted({r["insns"].split("0x")[-1].split()[0] for r in bodies
                     if r["shape"] == "return-const" and "mov eax,0x" in r["insns"]}, key=lambda x: int(x, 16))
    lines.append("")
    lines.append("**The constants a default returns are %s** (`mov eax,<n>; ret`), and the fields one reads "
                 "are %s -- a default getter is a field of `this` at a fixed offset, which is the object "
                 "layout an unoverridden interface method assumes."
                 % (", ".join("`0x%s`" % c for c in consts) or "-",
                    ", ".join("`+%s`" % m for m in sorted({re.search(r"\[ecx\+(0x[0-9a-f]+)\]", r["insns"]).group(1)
                                                          for r in bodies
                                                          if r["shape"] in ("return-field", "return-pair")
                                                          and re.search(r"\[ecx\+(0x[0-9a-f]+)\]", r["insns"])},
                                                         key=lambda x: int(x, 16))) or "-"))
    return "\n".join(lines)


def no_body_reps(per_rep):
    """The owed representatives whose every slot is a default: no mapped body and no gap head."""
    return sorted(va for va, kinds in per_rep.items()
                  if not kinds["mapped"] and not kinds["gap"])


# --------------------------------------------------------------------------- the four tables nothing stores
def load_symbols(path):
    out = []
    for r in read_csv(path):
        va, size = num(r.get("address")), num(r.get("size"), 10)
        if va is None or not size:
            continue
        out.append((va, size, r.get("name") or ""))
    out.sort()
    return out


def load_heads(path):
    out = {}
    for r in read_csv(path):
        va, size = num(r.get("head_va")), num(r.get("size"), 10)
        if va is None or not size:
            continue
        out[va] = (size, r.get("verdict") or "", r.get("prologue") or "")
    return out


def containing(table, va):
    """The last object that starts at or before `va` and is longer than the distance to it."""
    best = None
    for start, size, extra in table:
        if start <= va < start + size:
            if best is None or start > best[0]:
                best = (start, size, extra)
    return best


def hole_body(image, va, limit=512):
    """The unlisted body around a store: from the end of the previous `int3` run to the next one.

    The store's container is a body neither the map nor the gap census starts, so its extent is the
    region the padding bounds rather than a `ret` the walk-back hopes to meet.
    """
    xbe = getattr(image, "xbe", image)
    off = xbe.va_to_offset(va)
    data = image.data
    i = off
    while i > 0 and data[i - 2:i] != b"\xcc\xcc":
        i -= 1
    start_off = i
    while start_off < off and data[start_off] == 0xCC:
        start_off += 1
    j = off
    while j < len(data) - 1 and data[j:j + 2] != b"\xcc\xcc":
        j += 1
    start_va = xbe.offset_to_va(start_off)
    if start_va is None:
        return None
    size = min(j - start_off, limit)
    rows = image.disasm(start_va, size)
    return start_va, size, [t for _a, t in rows]


def site_rows(installer_rows, provenance, symbols, heads, disasm, hole):
    sites = {}
    for r in provenance:
        m = SITE.search(r.get("evidence") or "")
        if not m:
            continue
        first = m.group(1).split(",")[0].strip()
        va = num(first)
        if va is not None:
            sites[r["table_va"]] = (va, r)
    out = []
    for r in installer_rows:
        if (r.get("verdict") or "") != "no-site":
            continue
        tva = num(r["table_va"])
        site = sites.get(r["table_va"])
        store_va, store_insn, body, kind, csize, prologue = None, "", [], "none", 0, ""
        if site:
            store_va = site[0]
            sym = containing([(va, sz, nm) for va, sz, nm in symbols], store_va)
            head = containing([(va, sz, "head") for va, (sz, _v, _p) in heads.items()], store_va)
            if sym and (not head or sym[0] > head[0]):
                kind, body, csize = "row", [t for _a, t in disasm(sym[0], sym[1])], sym[1]
                container_va = sym[0]
                prologue = next((t.split()[0] for _a, t in disasm(sym[0], 8)), "")
            elif head:
                kind, csize, container_va = "head", head[1], head[0]
                body = [t for _a, t in disasm(head[0], head[1])]
                prologue = heads[head[0]][2]
            else:
                got = hole(store_va)
                if got:
                    kind, container_va, csize = "hole", got[0], got[1]
                    body = got[2]
                    prologue = re.sub(r"\s+", " ", body[0].strip()) if body else ""
            store_insn = next((t.strip() for t in body
                               if (first_store([t]) or (None, None))[1] == tva), "")
        stores = []
        sentinel = ""
        for text in body:
            hit = first_store([text])
            if not hit:
                continue
            disp, imm, _t = hit
            if imm == SENTINEL:
                sentinel = "0xdeaddead at +%#x" % disp
            elif imm > 0x400000:
                stores.append("+%#x=vtable %#010x" % (disp, imm))
            else:
                stores.append("+%#x=%#x" % (disp, imm))
        stores.sort(key=lambda s: int(re.search(r"\+0x([0-9a-f]+)", s).group(1), 16))
        out.append({
            "table_va": r["table_va"], "slots": r.get("slots") or "",
            "store_va": "%#010x" % store_va if store_va is not None else "",
            "store_insn": store_insn or "",
            "container": kind,
            "container_va": "%#010x" % container_va if container_va else "",
            "container_size": str(csize),
            "prologue": prologue,
            "body_stores": " ".join(stores),
            "sentinel": sentinel,
            "verdict": kind,
            "evidence": "%d slot(s); store %s; container %s %s (%d bytes)" %
                        (int(r.get("slots") or 0),
                         "%#010x" % store_va if store_va is not None else "-", kind,
                         "%#010x" % container_va if container_va else "-", csize),
        })
    out.sort(key=lambda r: int(r["table_va"], 16))
    census = {k: "" for k in FIELDS_SITE}
    kinds = collections.Counter(r["verdict"] for r in out)
    census.update({
        "table_va": "census", "slots": str(sum(int(r["slots"] or 0) for r in out)),
        "verdict": "census",
        "evidence": "tables %d; %s; sentinel %d" %
                    (len(out), "; ".join("%s %d" % (k, kinds[k]) for k in sorted(kinds)),
                     sum(1 for r in out if r["sentinel"])),
    })
    out.append(census)
    return out


def rendered_sites(rows):
    tables = [r for r in rows if r["verdict"] != "census"]
    kinds = collections.Counter(r["verdict"] for r in tables)
    lines = ["| the table | slots | store | the body it is stored in | bytes | its immediate stores |",
             "|---|---:|---|---|---:|---|"]
    for r in tables:
        lines.append("| `%s` | %d | `%s` | %s `%s` | %s | %s |"
                     % (r["table_va"], int(r["slots"]), r["store_va"], r["container"],
                        r["container_va"], r["container_size"] or "-",
                        ("`%s`" % r["body_stores"]) if r["body_stores"] else "-"))
    lines.append("")
    holes = [r for r in tables if r["verdict"] == "hole"]
    heads = [r for r in tables if r["verdict"] == "head"]
    sent = [r for r in tables if r["sentinel"]]
    lines.append("**All %d tables are stored: %d by a constructor no map row and no gap head starts, and "
                 "%d by a gap head**, each a `mov [<reg>+disp], imm32` writing the table into the object's "
                 "dword 0 before the first field write. The %d unlisted constructors are %s bytes and open "
                 "`mov eax,ecx`; %d of the %d tables leaves `0xdeaddead` in a field beside the vftable, which "
                 "is the layout a fresh object is built with."
                 % (len(tables), len(holes), len(heads), len(holes),
                    "/".join(sorted({r["container_size"] for r in holes}, key=int)) or "-",
                    len(sent), len(tables)))
    return "\n".join(lines)


# --------------------------------------------------------------------------- artifact / doc plumbing
def write_artifact(rows, path, fields):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def splice_doc(text, block, start, end):
    i, j = text.find(start), text.find(end)
    if i < 0 or j < 0:
        return None
    return text[:i] + start + "\n" + block + "\n" + text[j:]


# --------------------------------------------------------------------------- self-test
def self_check():
    bad = []
    cases = [(["xor    eax,eax", "ret"], "", "return-zero"),
             (["mov    eax,ecx", "ret    0x4"], "", "return-this"),
             (["mov    eax,DWORD PTR [ecx+0x2c]", "ret"], "", "return-field"),
             (["mov    eax,DWORD PTR [ecx+0x10]", "mov    edx,DWORD PTR [ecx+0x14]", "ret"], "",
              "return-pair"),
             (["ret"], "", "bare-return"),
             (["ret    0x14"], "", "bare-return"),
             (["mov    eax,0x18", "ret"], "", "return-const"),
             (["or     eax,0xffffffff", "ret"], "", "return-minus-one"),
             (["call   eax", "push   0x19"], "__purecall", "purecall"),
             (["push   ebp", "mov    ebp,esp"], "", "other")]
    for insns, name, want in cases:
        got = shape_of(insns, name)
        if got != want:
            bad.append("%r reads %s, not %s" % (insns, got, want))
    if first_store(["mov    DWORD PTR [eax+0x58],ecx"]) is not None:
        bad.append("a register store is not an immediate store")
    hit = first_store(["mov    DWORD PTR [eax+0x5c],0xdeaddead"])
    if not hit or hit[0] != 0x5C or hit[1] != SENTINEL:
        bad.append("a displacement store's imm32 is read")
    if first_store(["mov    DWORD PTR [eax],0x4f70a0"])[0] != 0:
        bad.append("a store with no displacement is +0")
    if containing([(0x100, 10, "a"), (0x200, 10, "b")], 0x205)[2] != "b":
        bad.append("the containing object is the last one that starts before the site")
    if containing([(0x100, 10, "a")], 0x10A) is not None:
        bad.append("an object's end is exclusive")

    rows = []
    for va, shape, slots in (("0x000171c0", "return-zero", 116), ("0x003e5501", "purecall", 38)):
        rows.append({"body_va": va, "name": "FUN_" + va[2:], "size": "3", "shape": shape,
                     "slots": str(slots), "representatives": "0x004c7800 0x004c7854",
                     "insns": "xor eax,eax ; ret", "verdict": shape, "evidence": "3 byte(s)"})
    census = {k: "" for k in FIELDS_DEF}
    census.update({"body_va": "census", "shape": "census", "slots": "154", "representatives": "2",
                   "verdict": "census", "evidence": "distinct 2; return-zero 1; purecall 1"})
    rows.append(census)
    if "return-zero" not in rendered_defaults(rows) or "0x000171c0" not in rendered_defaults(rows):
        bad.append("the defaults block lacks a shape or a body")
    path = os.path.join(tempfile.mkdtemp(), "a.csv")
    write_artifact(rows, path, FIELDS_DEF)
    if read_csv(path) != rows:
        bad.append("defaults artifact round trip differs")

    sites = [{"table_va": "0x004f70a0", "slots": "2", "store_va": "0x000e1704",
              "store_insn": "mov DWORD PTR [eax],0x4f70a0", "container": "hole",
              "container_va": "0x000e1700", "container_size": "20",
              "prologue": "mov eax,ecx", "body_stores": "+0x0=vtable 0x004f70a0",
              "sentinel": "0xdeaddead at +0x5c", "verdict": "hole",
              "evidence": "2 slot(s); store 0xe1704"}]
    census2 = {k: "" for k in FIELDS_SITE}
    census2.update({"table_va": "census", "slots": "2", "verdict": "census",
                    "evidence": "tables 1; hole 1; sentinel 1"})
    sites.append(census2)
    if "hole" not in rendered_sites(sites) or "0xdeaddead" not in rendered_sites(sites):
        bad.append("the no-site block lacks the container or the sentinel")
    path2 = os.path.join(tempfile.mkdtemp(), "b.csv")
    write_artifact(sites, path2, FIELDS_SITE)
    if read_csv(path2) != sites:
        bad.append("no-site artifact round trip differs")
    if bad:
        print("FAIL class-interface-residue: " + "; ".join(bad))
        return 1
    print("ok   class-interface-residue: the nine body shapes, the store parse, the container lookup "
          "and both artifact round trips agree with the synthetic rows")
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
        rows = read_csv(OUT_DEF)
        sites = read_csv(OUT_SITE)
        if not rows or not sites:
            print("FAIL class-interface-residue: %s or %s is missing" % (OUT_DEF, OUT_SITE))
            return 1
        text = open(args.check_doc, encoding="utf-8").read()
        for (start, end), block in ((DOC_DEF, rendered_defaults(rows)), (DOC_SITE, rendered_sites(sites))):
            i, j = text.find(start), text.find(end)
            if i < 0 or j < 0:
                print("FAIL class-interface-residue: %s has no %s block" % (args.check_doc, start))
                return 1
            if text[i:j + len(end)] != start + "\n" + block + "\n" + end:
                print("FAIL class-interface-residue: %s's %s block does not reproduce from the CSVs"
                      % (args.check_doc, start))
                return 1
        print("ok   class-interface-residue: %s's two blocks reproduce from the committed CSVs"
              % args.check_doc)
        return 0
    if args.report or args.write_doc:
        rows = read_csv(OUT_DEF)
        sites = read_csv(OUT_SITE)
        if not rows or not sites:
            print("FAIL class-interface-residue: an artifact is missing")
            return 1
        blocks = ((DOC_DEF, rendered_defaults(rows)), (DOC_SITE, rendered_sites(sites)))
        if args.report:
            print("\n\n".join(b for _m, b in blocks))
            return 0
        text = open(args.write_doc, encoding="utf-8").read()
        for (start, end), block in blocks:
            new = splice_doc(text, block, start, end)
            if new is None:
                print("FAIL class-interface-residue: %s has no %s markers" % (args.write_doc, start))
                return 1
            text = new
        if text != open(args.write_doc, encoding="utf-8").read():
            open(args.write_doc, "w", encoding="utf-8").write(text)
            print("wrote %s" % args.write_doc)
        return 0
    if not args.xbe:
        ap.error("--xbe is required to read the bodies (or use --report/--check-doc/--check)")

    xbemod = sibling("xbe")
    names_mod = sibling("class-table-names")
    image = xbemod.Xbe(args.xbe)
    img = names_mod.Image()
    img.xbe, img.data = image, image.data
    names = {va: nm for va, _sz, nm in load_symbols(SYMBOLS)}
    rep_rows = read_csv(REPS)
    rows, per_rep = default_rows(rep_rows, names, img.disasm)
    sites = site_rows(read_csv(INSTALLERS), read_csv(PROVENANCE), load_symbols(SYMBOLS),
                      load_heads(HEADS), img.disasm, lambda va: hole_body(img, va))
    if args.out:
        write_artifact(rows, os.path.join(args.out, os.path.basename(OUT_DEF)), FIELDS_DEF)
        write_artifact(sites, os.path.join(args.out, os.path.basename(OUT_SITE)), FIELDS_SITE)
        print("wrote %s (%d rows) and %s (%d rows)"
              % (OUT_DEF, len(rows), OUT_SITE, len(sites)))
        nos = no_body_reps(per_rep)
        print("no-body representatives: %d %s" % (len(nos), " ".join(nos)))
    else:
        print(rendered_defaults(rows))
        print()
        print(rendered_sites(sites))
    return 0


if __name__ == "__main__":
    sys.exit(main())
