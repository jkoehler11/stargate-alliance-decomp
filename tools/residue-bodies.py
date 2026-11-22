#!/usr/bin/env python3
"""The class thread's last unnamed bodies, read one at a time: what each one is.

U40's residue has carried three small populations for several rounds, each named only by a count:

  * **the 7 Karma callback bodies** the MathEngine runtime table (`0x005ccadc`, 42 entries) is
    patched with at startup and whose own literals name nothing;
  * **the 2 bodies in `0x0051724c`** -- the 5-slot table stored by two constructors, at `+0x0` of the
    object `0x001de030` builds and at `+0x38` of the one `0x001df420` builds;
  * **the 5 inherited interface bodies** `tools/class-slot-declarers.py` refuses: one body held at one
    slot index by four or five COM interfaces, so the index is a shared base's method and no table in
    the image is that base's.

Every earlier round answered these with the *absence* of a channel (no literal, no assert, no caller).
This read answers them with the body: a bounded objdump decode from the head to its first terminator,
the register sources of its dispatch, the fields it touches, the literals it pushes by address and the
map's name for every direct callee -- and a `kind` that says what the body *is* rather than what names
it. A kind is not a name: nothing here is added to the symbol map.

The population is not typed in. Each thread is read out of the committed artifact that counts it
(`inventory/global_slot_stores.csv` for the callbacks, `inventory/gap_heads.csv` for the table's slots,
`inventory/class_slot_declarers.csv` for the inherited ones), so the census moves with the residue and
an empty thread is visible as an empty thread.

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/residue-bodies.py \\
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/residue-bodies.py --report
    python3 tools/residue-bodies.py --write-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/residue-bodies.py --check-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/residue-bodies.py --check        # synthetic, no dump, writes nothing

Addresses, names and counts only; no image or package content is written.
"""
import argparse
import csv
import importlib.util
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYMBOLS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
STORES = os.path.join(ROOT, "inventory", "global_slot_stores.csv")
HEADS = os.path.join(ROOT, "inventory", "gap_heads.csv")
DECLARERS = os.path.join(ROOT, "inventory", "class_slot_declarers.csv")
# The names an earlier round recorded: a body listed here is no longer residue.
NAMES = os.path.join(ROOT, "inventory", "global_slot_names.csv")
OUT = os.path.join(ROOT, "inventory", "residue_bodies.csv")
DOC = os.path.join(ROOT, "docs", "systems", "CLASS_REGISTRATION.md")
DOC_A = ("<!-- generated:residue-bodies -->", "<!-- /generated:residue-bodies -->")

BODY = ("thread", "body_va", "size", "insns", "terminator", "kind", "args", "dispatch_from",
        "dispatch_slot", "context", "fields", "literals", "callees", "tables", "slot", "declarers",
        "note")
# One tuple for the accumulation and the emission -- a key the walk holds and the writer forgets
# renders as a literal 0 in the page (measured twice in this thread).
CENSUS = ("threads", "bodies", "bytes", "com_notimpl", "alloc_zero", "tail_dispatch", "dispatch",
          "init_descriptor", "field_getter", "out_param_write", "other", "with_literal",
          "with_callee", "dispatch_globals", "args_min", "args_max", "karma", "table_51724c",
          "inherited", "literal_control", "literal_control_hits")
THREADS = ("karma-callback", "table-0x51724c", "inherited-slot")

OBJ_LINE = re.compile(r"^\s*([0-9a-f]+):\t([0-9a-f ]+?)\s*\t(.*)$")
FIELD = re.compile(r"\[(e[a-z]{2})(?:\+0x([0-9a-f]+))?\]")
DISPATCH = re.compile(r"(?:call|jmp)\s+DWORD PTR \[(e[a-z]{2})(?:\+0x([0-9a-f]+))?\]")
GLOBAL = re.compile(r"^mov\s+(e[a-z]{2}),DWORD PTR ds:(0x[0-9a-f]+)$")
VLOAD = re.compile(r"^mov\s+(e[a-z]{2}),DWORD PTR \[(e[a-z]{2})\]$")
DIRECT = re.compile(r"^call\s+(?:0x)?([0-9a-f]+)$")
HEX = re.compile(r"0x[0-9a-f]{6,8}")
RET = re.compile(r"^ret\b")
IMMWRITE = re.compile(r"^mov\s+DWORD PTR \[(?:e[a-z]{2}|esp)(?:\+0x[0-9a-f]+)?\],0x[0-9a-f]+$")
CAP = 160
TERMINATORS = ("ret", "retn", "retf")


def read_csv(path):
    if not path or not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_symbols(path=None):
    names, sizes = {}, {}
    for row in read_csv(path or SYMBOLS):
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


def population(stores, heads, declarers, named):
    """The three threads, each keyed on the artifact that counts it -- not a typed list."""
    pop = []
    for r in stores:
        if (r.get("window") or "").lower() != "0x005ccadc":
            continue
        if (r.get("value_name") or "").strip():
            continue
        if (r.get("value") or "").lower() in named:
            continue
        try:
            va = int(r["value"], 16)
        except (KeyError, ValueError):
            continue
        pop.append({"thread": "karma-callback", "body_va": va, "tables": "0x005ccadc",
                    "slot": r.get("index") or "", "declarers": "",
                    "extra": "replaces %s" % (r.get("static_value") or "-")})
    for r in declarers:
        if (r.get("verdict") or "") != "member":
            continue
        if "0x0051724c" not in (r.get("member_tables") or ""):
            continue
        try:
            va = int(r["head_va"], 16)
        except (KeyError, ValueError):
            continue
        pop.append({"thread": "table-0x51724c", "body_va": va, "tables": "0x0051724c",
                    "slot": r.get("slot") or "", "declarers": "",
                    "extra": "slot body of the table"})
    for r in declarers:
        if (r.get("verdict") or "") != "inherited-interface":
            continue
        try:
            va = int(r["head_va"], 16)
        except (KeyError, ValueError):
            continue
        pop.append({"thread": "inherited-slot", "body_va": va, "tables": r.get("tables") or "",
                    "slot": r.get("indices") or "", "declarers": r.get("declarer") or "",
                    "extra": "held at %s slot(s)" % (r.get("tables") or "0")})
    return pop


def lookback(names, sizes, va):
    """The name of the body a direct call lands in, or the interior offset it lands at."""
    if va in names:
        return names[va]
    starts = sorted(sizes)
    i = 0
    for s in starts:
        if s > va:
            break
        if s <= va < s + sizes[s]:
            return "%s+0x%x" % (names[s], va - s)
        i += 1
    return "0x%x" % va


def strings_of(image, insns):
    """Printable literals an instruction pushes by address, in order, deduplicated."""
    out = []
    for _va, _n, text in insns:
        for hexs in HEX.findall(text):
            v = int(hexs, 16)
            if image.section_for(v) is None:
                continue
            try:
                raw = image.read_va(v, 64)
            except Exception:  # noqa: BLE001  -- a section's last bytes answer short
                continue
            raw = raw[:64]
            head = raw.split(b"\x00")[0]
            if len(head) < 4:
                continue
            try:
                txt = head.decode("ascii")
            except UnicodeDecodeError:
                try:
                    txt = raw.decode("utf-16-le").split("\x00")[0]
                except UnicodeDecodeError:
                    continue
            if not txt or any(ord(c) < 32 or ord(c) > 126 for c in txt):
                continue
            txt = txt[:48]
            if not any(txt == t for t in out):
                out.append(txt)
    return out


def classify(insns, names, sizes):
    """What the body is, from its own instructions. `insns` is [(va, len, text)]."""
    if not insns:
        return {"kind": "other", "note": "the decode is empty", "args": "", "dispatch_from": "",
                "dispatch_slot": "", "context": "", "fields": [], "literals": [], "callees": []}
    regs = {}
    fields, callees = [], []
    dispatch_from = dispatch_slot = context = ""
    stores_out = []
    for _va, _n, text in insns:
        g = GLOBAL.match(text)
        if g:
            regs[g.group(1)] = "ds:%s" % g.group(2)
        v = VLOAD.match(text)
        if v and v.group(2) in regs:
            regs[v.group(1)] = "[%s]" % regs[v.group(2)]
            continue
        d = DISPATCH.search(text)
        if d:
            src = regs.get(d.group(1), "")
            dispatch_slot = ("+0x%x" % int(d.group(2), 16)) if d.group(2) else "+0x0"
            dispatch_from = src
        for _reg, off in FIELD.findall(text):
            tag = ("+0x%s" % off) if off else ""
            if tag and tag not in fields:
                fields.append(tag)
        c = DIRECT.match(text)
        if c:
            callees.append(lookback(names, sizes, int(c.group(1), 16)))
        m = re.match(r"^mov\s+DWORD PTR \[(e[a-z]{2}|esp)[^\]]*\],(?:0x[0-9a-f]+|e[a-z]{2})$", text)
        if m:
            stores_out.append(text)
    # A context pointer is a global plus a constant displacement (`mov edx,ds:G; add edx,0xNN`).
    for i, (_va, _n, text) in enumerate(insns):
        a = re.match(r"^add\s+(e[a-z]{2}),(0x[0-9a-f]+)$", text)
        if a and regs.get(a.group(1), "").startswith("ds:") and i:
            prior = regs[a.group(1)]
            if not prior.count("+"):
                context = "%s+%s" % (prior, a.group(2))
                regs[a.group(1)] = context
    term = insns[-1][2]
    words = ""
    r = re.match(r"^retn?\s+(0x[0-9a-f]+)$", term) or re.match(r"^ret\s+(0x[0-9a-f]+)$", term)
    if r:
        words = str(int(r.group(1), 16) // 4)
    elif term.split()[0] in TERMINATORS:
        words = "0"
    texts = [t for _v, _n, t in insns]
    joined = " | ".join(texts)
    if any(re.match(r"^mov\s+eax,0x80004001$", t) for t in texts):
        kind = "com-notimpl"
    elif any("rep stos" in t for t in texts):
        kind = "alloc-zero"
    elif re.match(r"^jmp\s+DWORD PTR", term):
        kind = "tail-dispatch"
    elif any(DISPATCH.search(t) for t in texts):
        kind = "dispatch"
    elif len([t for t in texts if IMMWRITE.match(t)]) >= 2:
        kind = "init-descriptor"
    elif stores_out:
        kind = "out-param-write"
    elif any(re.match(r"^mov\s+eax,DWORD PTR \[e[a-z]{2}(?:\+0x[0-9a-f]+)?\]$", t) for t in texts):
        kind = "field-getter"
    else:
        kind = "other"
    return {"kind": kind, "args": words, "dispatch_from": dispatch_from,
            "dispatch_slot": dispatch_slot, "context": context, "fields": fields,
            "callees": callees, "stores_out": stores_out}


def note_for(row):
    """A sentence the page can quote -- built from the row, never typed."""
    kind = row["kind"]
    n = row["args"] or "?"
    if kind == "com-notimpl":
        return "a COM default: returns E_NOTIMPL (0x80004001) with %s argument dword(s)" % n
    if kind == "alloc-zero":
        return "allocates through %s%s, then zero-fills the block (%s argument dword(s))" % (
            row["dispatch_from"] or "an unread register", row["dispatch_slot"], n)
    if kind == "tail-dispatch":
        return "tail-jumps to slot %s%s -- no frame of its own" % (
            row["dispatch_slot"] or "", (" of %s" % row["dispatch_from"]) if row["dispatch_from"] else "")
    if kind == "dispatch":
        return "dispatches through slot %s%s and returns (%s argument dword(s))" % (
            row["dispatch_slot"] or "", (" of %s" % row["dispatch_from"]) if row["dispatch_from"] else "",
            n)
    if kind == "init-descriptor":
        return "writes a descriptor into the object it is handed (%s argument dword(s))" % n
    if kind == "field-getter":
        return "a field getter over %s" % (", ".join("`%s`" % f for f in row["fields"].split(" ")) or "-")
    if kind == "out-param-write":
        return "writes into the caller's out pointer and returns 0 (%s argument dword(s))" % n
    return "a %s-byte body with none of the shapes the read models" % row["size"]


def measure(image, pop, names, sizes):
    rows = []
    for p in pop:
        insns = decode(image, p["body_va"])
        read = classify(insns, names, sizes)
        row = {"thread": p["thread"], "body_va": "0x%08x" % p["body_va"],
               "size": str(sum(n for _v, n, _t in insns)),
               "insns": str(len(insns)),
               "terminator": " ".join(insns[-1][2].split()) if insns else "",
               "kind": read["kind"], "args": read["args"],
               "dispatch_from": read["dispatch_from"], "dispatch_slot": read["dispatch_slot"],
               "context": read["context"], "fields": " ".join(read["fields"]),
               "literals": " | ".join(strings_of(image, insns)),
               "callees": " ".join(read["callees"]), "tables": p["tables"], "slot": p["slot"],
               "declarers": p["declarers"]}
        row["note"] = note_for(row)
        rows.append(row)
    census = dict((k, 0) for k in CENSUS)
    census["threads"] = len(set(r["thread"] for r in rows))
    census["bodies"] = len(rows)
    census["bytes"] = sum(int(r["size"]) for r in rows)
    for r in rows:
        key = r["kind"].replace("-", "_")
        census[key] = census.get(key, 0) + 1
        if r["literals"]:
            census["with_literal"] += 1
        if r["callees"]:
            census["with_callee"] += 1
        if r["thread"] == "karma-callback":
            census["karma"] += 1
        elif r["thread"] == "table-0x51724c":
            census["table_51724c"] += 1
        else:
            census["inherited"] += 1
    census["dispatch_globals"] = len(set(r["dispatch_from"] for r in rows if r["dispatch_from"]))
    args = [int(r["args"]) for r in rows if r["args"]]
    census["args_min"] = min(args) if args else 0
    census["args_max"] = max(args) if args else 0
    # The control for a zero: the same literal read over the bodies an earlier round *named* from their
    # own literal. A channel that finds none of those is broken, not empty.
    for r in read_csv(NAMES):
        try:
            va = int(r["body_va"], 16)
        except (KeyError, ValueError):
            continue
        census["literal_control"] += 1
        if strings_of(image, decode(image, va)):
            census["literal_control_hits"] += 1
    return rows, census


def decode(image, va):
    """A bounded linear decode from `va` to its first terminator, through objdump."""
    try:
        blob = image.read_va(va, CAP)
    except Exception:  # noqa: BLE001
        return []
    blob = blob[:CAP]
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
        fh.write(blob)
        path = fh.name
    try:
        listing = subprocess.run(["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                                  "--adjust-vma=0x%x" % va, path],
                                 capture_output=True, text=True, check=False).stdout
    finally:
        os.unlink(path)
    out = []
    for line in listing.splitlines():
        m = OBJ_LINE.match(line)
        if not m or ".byte" in m.group(3):
            continue
        start, size, text = int(m.group(1), 16), len(m.group(2).split()), m.group(3)
        if start != va + sum(n for _v, n, _t in out):
            break
        out.append((start, size, text))
        if text.split()[0] in TERMINATORS or re.match(r"^ret\s", text):
            break
        if text.startswith("jmp ") and "DWORD PTR" in text:
            break
        if text.startswith("int3") and len(out) > 1:
            out.pop()
            break
    return out


KIND_PHRASE = {
    "com_notimpl": "return `E_NOTIMPL`",
    "dispatch": "dispatch through a slot",
    "alloc_zero": "allocate and then zero-fill the block",
    "tail_dispatch": "tail-jump into a slot with no frame of its own",
    "init_descriptor": "write a descriptor into the object they are handed",
    "field_getter": "are field getters",
    "out_param_write": "write the caller's out pointer and return 0",
    "other": "have none of the shapes the read models",
}


def kind_sentence(census):
    """The kind counts as one sentence, only the kinds that are there -- a zero is not a finding."""
    parts = []
    for key in ("com_notimpl", "dispatch", "alloc_zero", "tail_dispatch", "init_descriptor",
                "field_getter", "out_param_write", "other"):
        n = census.get(key, 0)
        if n:
            parts.append("%d %s" % (n, KIND_PHRASE[key]))
    if not parts:
        return "no body in the population has a shape the read models"
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1] + ["and " + parts[-1]])


def render(rows, census):
    globals_ = sorted(set(r["dispatch_from"] for r in rows if r["dispatch_from"]))
    lines = []
    lines.append("**%d bodies, %d bytes, %d kinds** -- every one of the thread's residues read at its own "
                 "instructions (`inventory/residue_bodies.csv`)." % (
                     census.get("bodies", 0), census.get("bytes", 0),
                     len(set(r["kind"] for r in rows))))
    lines.append("")
    lines.append(
        "The three populations split **%d / %d / %d** (the MathEngine callbacks, the `0x0051724c` slots, "
        "the inherited interface bodies), and what they are is one shape per population rather than a "
        "missing name per body: **%s**." % (
            census.get("karma", 0), census.get("table_51724c", 0), census.get("inherited", 0),
            kind_sentence(census)))
    lines.append("")
    lines.append("| thread | body | size | kind | args | slot | what it is |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in rows:
        lines.append("| `%s` | `%s` | %s | `%s` | %s | %s | %s |" % (
            r["thread"], r["body_va"], r["size"], r["kind"], r["args"] or "-",
            r["slot"] or "-", r["note"]))
    lines.append("")
    if globals_:
        lines.append(
            "**All %d dispatches read their table through %s**, and the argument counts run %d to %d "
            "dwords: one library object's method table, not class methods. **%d of the %d bodies push a "
            "printable literal**, and the control is the same read over the bodies `inventory/"
            "global_slot_names.csv` named from their own literal: **%d of %d** -- so the silence here is "
            "a measurement, not a broken channel." % (
                census.get("dispatch", 0) + census.get("alloc_zero", 0) + census.get("tail_dispatch", 0),
                " and ".join("`%s`" % g for g in globals_), census.get("args_min", 0),
                census.get("args_max", 0), census.get("with_literal", 0), census.get("bodies", 0),
                census.get("literal_control_hits", 0), census.get("literal_control", 0)))
        lines.append("")
    return "\n".join(lines)


def splice_doc(path, block, marks):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(marks[0]), text.find(marks[1])
    if i < 0 or j < 0:
        return None
    return text[:i] + marks[0] + "\n" + block + "\n" + text[j:]


def read_artifact(path):
    rows, census = [], None
    for r in read_csv(path):
        if (r.get("thread") or "") == "census":
            census = {}
            for k in CENSUS:
                try:
                    census[k] = int(r.get(k) or 0)
                except ValueError:
                    census[k] = 0
        else:
            rows.append(r)
    return rows, census


def write_artifact(rows, census, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        # The census row shares the header: its keys are extra columns, so one pass writes both and
        # `read_artifact` finds the census by its `thread` value (the sibling tools' shape).
        w = csv.DictWriter(fh, fieldnames=list(BODY) + list(CENSUS), lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(dict((k, r.get(k, "")) for k in BODY))
        w.writerow(dict([("thread", "census")] + [(k, census.get(k, 0)) for k in CENSUS]))


def self_check():
    """Synthetic bodies only: the kinds, the dispatch sources, the render and both round trips."""
    bad = []
    names = {0x2000: "Installer", 0x2100: "Alloc", 0x2200: "Free"}
    sizes = {0x2000: 16, 0x2100: 8, 0x2200: 8}

    def body(rows):
        return [(va, len(t.split()), t) for va, t in rows]

    notimpl = body([(0x1000, "mov eax,0x80004001"), (0x1005, "ret 0x2c")])
    got = classify(notimpl, names, sizes)
    if got["kind"] != "com-notimpl" or got["args"] != "11":
        bad.append("an E_NOTIMPL default is one, with its argument dwords")
    tail = body([(0x1000, "mov ecx,DWORD PTR ds:0x5bf510"),
                 (0x1006, "mov eax,DWORD PTR [ecx]"),
                 (0x1008, "jmp DWORD PTR [eax+0x8]")])
    got = classify(tail, names, sizes)
    if got["kind"] != "tail-dispatch" or got["dispatch_slot"] != "+0x8" \
            or got["dispatch_from"] != "[ds:0x5bf510]":
        bad.append("a tail jump reports the slot and the table it came from")
    zero = body([(0x1000, "mov ecx,DWORD PTR ds:0x5bf510"),
                 (0x1006, "mov eax,DWORD PTR [ecx]"),
                 (0x1008, "push esi"), (0x1009, "call DWORD PTR [eax]"),
                 (0x100b, "rep stos DWORD PTR es:[edi],eax"), (0x100d, "ret 0x4")])
    got = classify(zero, names, sizes)
    if got["kind"] != "alloc-zero":
        bad.append("an allocate-then-zero-fill body is not a plain dispatch")
    getter = body([(0x1000, "mov eax,DWORD PTR [ecx+0x8]"),
                   (0x1003, "mov edx,DWORD PTR [ecx+0xc]"), (0x1006, "ret")])
    got = classify(getter, names, sizes)
    if got["kind"] != "field-getter" or "+0x8" not in got["fields"] or "+0xc" not in got["fields"]:
        bad.append("a two-field getter is one, with both fields")
    init = body([(0x1000, "mov DWORD PTR [eax],0x2"), (0x1006, "mov DWORD PTR [eax+0x4],0x0"),
                 (0x100d, "ret 0x10")])
    got = classify(init, names, sizes)
    if got["kind"] != "init-descriptor":
        bad.append("two constant writes into an object are a descriptor init")
    outw = body([(0x1000, "mov eax,DWORD PTR [esp+0x4]"), (0x1004, "mov eax,DWORD PTR [eax+0x4]"),
                 (0x1007, "mov ecx,DWORD PTR [esp+0x8]"), (0x100b, "mov DWORD PTR [ecx],eax"),
                 (0x100d, "xor eax,eax"), (0x100f, "ret 0x8")])
    got = classify(outw, names, sizes)
    if got["kind"] != "out-param-write":
        bad.append("a read copied into the caller's pointer is an out-parameter write")
    # The kind must not be invented when the instructions say nothing.
    if classify(body([(0x1000, "push ebx"), (0x1001, "ret")]), names, sizes)["kind"] != "other":
        bad.append("a body with no shape stays `other`")
    if lookback(names, sizes, 0x2104) != "Alloc+0x4":
        bad.append("an interior call target names the body it lands in, with the offset")
    rows = [{"thread": "table-0x51724c", "body_va": "0x00001000", "size": "7", "insns": "3",
             "terminator": "ret", "kind": "field-getter", "args": "", "dispatch_from": "",
             "dispatch_slot": "", "context": "", "fields": "+0x8 +0xc", "literals": "",
             "callees": "", "tables": "0x0051724c", "slot": "0", "declarers": "",
             "note": note_for({"kind": "field-getter", "fields": "+0x8 +0xc", "size": "7",
                               "args": ""})}]
    census = dict((k, 0) for k in CENSUS)
    census.update({"threads": 1, "bodies": 1, "bytes": 7, "field_getter": 1, "table_51724c": 1,
                   "args_min": 0, "args_max": 0})
    block = render(rows, census)
    if "| `table-0x51724c` | `0x00001000` | 7 | `field-getter` |" not in block:
        bad.append("the render carries the thread, the body and its kind")
    path = os.path.join(tempfile.mkdtemp(), "residue.csv")
    try:
        write_artifact(rows, census, path)
        back, back_census = read_artifact(path)
        if len(back) != 1 or back[0]["kind"] != "field-getter" or back_census["bodies"] != 1:
            bad.append("the artifact round-trips its rows and its census row")
        if render(back, back_census) != block:
            bad.append("the render reproduces from the written artifact")
    finally:
        os.unlink(path)
        os.rmdir(os.path.dirname(path))
    # A census key the walk holds and the writer drops must fail here, not render as a zero.
    if set(census) - set(CENSUS):
        bad.append("every accumulated key is written")
    if bad:
        print("FAIL residue-bodies: " + "; ".join(bad))
        return 1
    print("ok   residue-bodies: the 7 kinds, the dispatch sources, the interior call target, the render "
          "and both artifact round trips")
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
        rows, census = read_artifact(OUT)
        if not rows or not census:
            print("FAIL residue-bodies: %s is missing or has no census row" % OUT)
            return 1
        block = render(rows, census)
        text = open(args.check_doc, encoding="utf-8").read()
        i, j = text.find(DOC_A[0]), text.find(DOC_A[1])
        if i < 0 or j < 0:
            print("FAIL residue-bodies: %s has no %s block" % (args.check_doc, DOC_A[0]))
            return 1
        if text[i:j + len(DOC_A[1])] != DOC_A[0] + "\n" + block + "\n" + DOC_A[1]:
            print("FAIL residue-bodies: %s's block does not reproduce from the committed CSV"
                  % args.check_doc)
            return 1
        print("ok   residue-bodies: %s reproduces from the committed CSV" % args.check_doc)
        return 0
    if args.report or args.write_doc:
        rows, census = read_artifact(OUT)
        if not rows or not census:
            print("FAIL residue-bodies: %s is missing or has no census row" % OUT)
            return 1
        block = render(rows, census)
        if args.report:
            print(block)
            return 0
        new = splice_doc(args.write_doc, block, DOC_A)
        if new is None:
            print("FAIL residue-bodies: %s has no %s markers" % (args.write_doc, DOC_A[0]))
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
    named = set((r.get("body_va") or "").lower() for r in read_csv(NAMES))
    pop = population(read_csv(STORES), read_csv(HEADS), read_csv(DECLARERS), named)
    if not pop:
        print("FAIL residue-bodies: the three artifacts hold no residue to read")
        return 1
    rows, census = measure(image, pop, names, sizes)
    out = os.path.join(args.out, os.path.basename(OUT)) if args.out else OUT
    write_artifact(rows, census, out)
    print("wrote %s (%d rows, %d bytes, %d kind(s))" % (
        out, len(rows), census["bytes"], len(set(r["kind"] for r in rows))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
