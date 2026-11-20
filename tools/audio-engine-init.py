#!/usr/bin/env python3
"""How the engine builds its audio device: one body, one offset (U34's last residual).

`inventory/xboxaudio_chain.csv` counted the 115 `call [reg+0x7c]` sites and
`inventory/audio_slot_typing.csv` typed 24 of them -- and none of the typed ones dispatches the device,
because the device body (`XboxAudio_CreateDevice`) is installed in exactly one table, `XBoxAudioSubsystem`'s.
The residual those two artifacts could not close was *which object* the engine dispatches the device
through: the script names the variable (`Engine.u`: `Audio : audiosubsystem`) but never its byte offset,
and a field-site census cannot invent one.

It does not have to be invented. The engine *creates* the subsystem, and the creation is one body:

    key      the wide literal `ini:Engine.Engine.AudioDevice`, the config key `Default.ini` fills
    class    that key's value is `XboxAudio.XboxAudioSubsystem`, whose table is in the image
    store    the object is stored into a field of the engine -- the offset the question asked for
    device   immediately after the store, slot 31 (+0x7c) is dispatched *on the new object*

So this reads one body structurally (objdump instruction boundaries, the oracle the other
instruments use), follows its literals, its construct calls, its stores and its indirect dispatches,
names each dispatch from the table of the class the body just constructed, and then joins the store's
offset back to the census:

    store +0x34  ->  of the 115 sites, `join` rows count how many read a field at that offset

Two controls keep the claim honest. The body is not virtual -- no class table in the image installs it,
so it cannot be reached by dispatch and the offset cannot be a vtable slot in disguise -- and each caller
is read back: the engine's own init reads the same offset off the same object and dispatches an audio
slot on it, which is what makes the store's offset the subsystem pointer rather than a coincidence.

    python3 tools/audio-engine-init.py --xbe DUMP/default.xbe --ini DUMP/System/Default.ini \\
        -o inventory/audio_engine_init.csv
    python3 tools/audio-engine-init.py --check     # synthetic instructions, no dump, writes nothing

Addresses, symbol names, config keys and counts only: nothing here carries game data.
"""
import argparse
import csv
import importlib.util
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

INVENTORY = os.path.join(ROOT, "inventory")
FUNCTIONS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
NAMES = os.path.join(INVENTORY, "xbe_class_table_names.csv")
EDGES = os.path.join(INVENTORY, "class_table_parent_edges.csv")
CENSUS = os.path.join(INVENTORY, "xboxaudio_chain.csv")
TYPING = os.path.join(INVENTORY, "audio_slot_typing.csv")
OUT = os.path.join(INVENTORY, "audio_engine_init.csv")

# The key the engine reads, as the image holds it: UE2 condenses a config lookup into one literal.
KEY = "ini:Engine.Engine.AudioDevice"
DEVICE_SLOT = 31          # the slot `XboxAudio_CreateDevice` is installed at, dispatch offset +0x7c
HEADER = ["kind", "address", "value", "detail"]

PUSH = re.compile(r"^push 0x([0-9a-f]+)$")
CALLEE = re.compile(r"^call 0x([0-9a-f]+)$")
CALLI = re.compile(r"^call DWORD PTR \[(\w+)\+0x([0-9a-f]+)\]$")
STORE = re.compile(r"^mov DWORD PTR \[(\w+)\+0x([0-9a-f]+)\],(eax|0x0)$")
LOAD = re.compile(r"^mov (\w+),DWORD PTR \[(\w+)\+0x([0-9a-f]+)\]$")
LEA_BACK = re.compile(r"^lea (\w+),\[(\w+)-0x([0-9a-f]+)\]$")
FRAME = ("esi", "edi", "ebp", "ebx")

_CACHE = {}


def sibling(name):
    """A tool in `tools/` whose module name has a hyphen, imported by path."""
    if name not in _CACHE:
        path = os.path.join(ROOT, "tools", name + ".py")
        spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _CACHE[name] = module
    return _CACHE[name]


def read_csv(path):
    if not path or not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_symbols(path):
    out = {}
    for row in read_csv(path):
        try:
            out[int(row["address"], 16)] = (row.get("name") or "", int(row["size"] or 0))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def read_table_rows(path):
    """table_va -> row, keyed lowercase-hex so the two censuses join."""
    return {row.get("table_va", "").lower().lstrip("0x"): row for row in read_csv(path) if row.get("table_va")}


# ---------------------------------------------------------------- reading the image

def wide(xbe, va, limit=160):
    """The NUL-terminated UTF-16 literal at `va`, or "" when the bytes are not one."""
    try:
        blob = xbe.read_va(va, limit * 2)
    except (KeyError, IndexError, ValueError):
        return ""
    text = blob.decode("utf-16-le", "replace").split("\x00")[0]
    if len(text) < 3 or len(text) >= limit - 1:
        return ""
    if any(not (0x20 <= ord(ch) <= 0x7e) for ch in text):
        return ""
    return text


def find_literal(xbe, needle):
    """The VA of `needle` as a UTF-16 literal, or None."""
    packed = needle.encode("utf-16-le")
    for sec in xbe.sections:
        try:
            blob = xbe.read_va(sec.va, sec.fsize)
        except (KeyError, IndexError, ValueError):
            continue
        i = blob.find(packed)
        if i >= 0:
            return sec.va + i
    return None


def code_sections(xbe, symbols):
    """The sections the symbol map calls code (every XBE section sets the executable flag)."""
    starts = sorted(symbols)
    out = []
    for sec in xbe.sections:
        covered = sum(1 for a in starts if sec.va <= a < sec.va + sec.vsize)
        if covered:
            out.append(sec)
    return out


def pushers(xbe, symbols, va):
    """Every `push imm32 == va` site, confirmed as an instruction start by the decoder."""
    needle = b"\x68" + struct.pack("<I", va)
    sites = []
    for sec in code_sections(xbe, symbols):
        blob = xbe.read_va(sec.va, sec.fsize)
        i = blob.find(needle)
        while i >= 0:
            sites.append(sec.va + i)
            i = blob.find(needle, i + 1)
    return sites


def body_of(xbe, symbols, va):
    """(start, size, name) of the body holding `va`: the map first, then the int3-padding rule."""
    cfs = sibling("class-flag-stores")
    table = sorted((a, s, n) for a, (n, s) in symbols.items())
    start, size, name, _how = cfs.body(xbe, table, [t[0] for t in table], va)
    return start, size, name


def disassemble(xbe, start, size):
    return sibling("class-flag-stores").objdump(xbe.read_va(start, size), start)


def scan_body(insns):
    """The structure of one body: literals pushed, construct calls, stores, clears, indirect dispatches.

    Every field is a list of `(va, ...)` tuples in address order, so a claim about "the store that
    follows the construct" can be checked against the instruction sequence rather than asserted.
    """
    facts = {"literal": [], "call": [], "store": [], "clear": [], "dispatch": [], "load": [], "lea": []}
    for i, (va, text) in enumerate(insns):
        m = PUSH.match(text)
        if m:
            facts["literal"].append((va, int(m.group(1), 16)))
            continue
        m = CALLEE.match(text)
        if m:
            facts["call"].append((va, int(m.group(1), 16)))
            continue
        m = CALLI.match(text)
        if m:
            facts["dispatch"].append((va, m.group(1), int(m.group(2), 16)))
            continue
        m = LEA_BACK.match(text)
        if m:
            facts["lea"].append((va, m.group(1), m.group(2), int(m.group(3), 16)))
            continue
        m = STORE.match(text)
        if m:
            kind = "store" if m.group(3) == "eax" else "clear"
            facts[kind].append((va, m.group(1), int(m.group(2), 16), m.group(3)))
            continue
        m = LOAD.match(text)
        if m:
            facts["load"].append((va, m.group(1), m.group(2), int(m.group(3), 16)))
    return facts


def construct_before(insns, va, window=6):
    """The call a store is the return value of: the last `call` within `window` instructions.

    Only `add esp`/`pop` may sit between them, which is what the construct-and-store idiom looks like.
    Returns (call_va, target) or (None, None).
    """
    idx = next((i for i, (iva, _t) in enumerate(insns) if iva == va), None)
    if idx is None:
        return None, None
    for i in range(idx - 1, max(-1, idx - window - 1), -1):
        _va, text = insns[i]
        if text.startswith("add esp") or text.startswith("pop ") or text.startswith("mov ecx,esp") \
                or text.startswith("mov DWORD PTR [ecx],"):
            continue
        m = CALLEE.match(text)
        if m:
            return insns[i][0], int(m.group(1), 16)
        return None, None
    return None, None


def offset_after(insns, va, window=10):
    """Loads of a field and indirect dispatches around `va`.

    Forward from the call for the uses, and a short look back for the interior-pointer idiom
    (`lea ecx,[esi-0x28]; call` -- the engine is a sub-object of the frame, so a load at +0xc of
    `esi` is the engine's +0x34), which is why the displacement is adjusted by the lea's delta.
    """
    idx = next((i for i, (iva, _t) in enumerate(insns) if iva == va), None)
    loads, dispatch, adj = [], [], 0
    if idx is None:
        return loads, dispatch, adj
    for i in range(max(0, idx - 4), idx):
        m = LEA_BACK.match(insns[i][1])
        if m and m.group(2) in FRAME:
            adj = int(m.group(3), 16)
    for i in range(idx + 1, min(len(insns), idx + window + 1)):
        _va, text = insns[i]
        m = LEA_BACK.match(text)
        if m:
            adj = int(m.group(3), 16)
            continue
        m = LOAD.match(text)
        if m:
            loads.append((m.group(2), int(m.group(3), 16) + adj))
            continue
        m = CALLI.match(text)
        if m:
            dispatch.append((insns[i][0], m.group(1), int(m.group(2), 16)))
        if text.startswith("ret"):
            break
    return loads, dispatch, adj


# ---------------------------------------------------------------- config and class tables

def ini_value(text, section, key):
    """`key=value` inside `[section]`, or "" -- the ini is the only place the class name lives."""
    current = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            continue
        if current is not None and current.lower() == section.lower() and "=" in line:
            name, value = line.split("=", 1)
            if name.strip().lower() == key.lower():
                return value.strip()
    return ""


def class_table(names, class_name):
    """(table_va, row) for a class name, case-insensitively -- the ini and the build disagree on case."""
    for row in read_csv(names):
        if (row.get("class") or "").lower() == class_name.lower():
            try:
                return int(row["table_va"], 16), row
            except (KeyError, TypeError, ValueError):
                continue
    return None, None


def parent_table(edges, table_va):
    """(parent_table_va or None, parent class name) from the parent-edge census."""
    for row in read_csv(edges):
        try:
            if int(row["table_va"], 16) == table_va:
                parent = row.get("parent_va") or ""
                return (int(parent, 16) if parent.startswith("0x") else None), row.get("parent_class") or ""
        except (KeyError, TypeError, ValueError):
            continue
    return None, ""


def slots_of(blob, count):
    """[(slot, target)] from a table's bytes; a short read keeps only the slots it holds."""
    return [(i, struct.unpack_from("<I", blob, 4 * i)[0]) for i in range(count) if 4 * (i + 1) <= len(blob)]


# ---------------------------------------------------------------- the measurement

def row(kind, address, value, detail):
    return {"kind": kind, "address": address, "value": value, "detail": detail}


def measure(image, ini_path, functions_path, names_path, edges_path, typing_path):
    from xbe import Xbe  # late, so --check needs no dump
    xbe = Xbe(image)
    symbols = read_symbols(functions_path)

    ini = ""
    if ini_path and os.path.isfile(ini_path):
        with open(ini_path, encoding="utf-8", errors="replace") as fh:
            ini = fh.read()
    device_path = ini_value(ini, "Engine.Engine", "AudioDevice")

    key_va = find_literal(xbe, KEY)
    if key_va is None:
        raise SystemExit("%r is not a literal in %s -- is this the right image?" % (KEY, image))
    sites = pushers(xbe, symbols, key_va)
    if not sites:
        raise SystemExit("the key at 0x%08x is pushed nowhere: no body reads it" % key_va)
    site = sites[0]
    start, size, name = body_of(xbe, symbols, site)
    insns = disassemble(xbe, start, size)
    facts = scan_body(insns)

    # The store sizes we care about: what the body writes into its own frame.
    stores = [(va, reg, disp) for va, reg, disp, value in facts["store"] if reg in FRAME and value == "eax"]
    # The audio store is the one after the key is read -- not simply the first, which would make the
    # artifact's own claim depend on the order the compiler emitted two constructs in.
    key_push = next((va for va, target in facts["literal"] if target == key_va), None)
    audio_store = next((s for s in stores if key_push is None or s[0] > key_push), stores[0] if stores else None)
    first_store = audio_store

    rows = [row("body", "0x%08x" % start,
               "%s (%d bytes)" % (name, size),
               "the body that pushes %r -- it builds the subsystem, it does not read one" % KEY)]

    # what the body reads before building: the guard
    guard = [l for l in facts["load"] if l[2] in FRAME]
    if guard:
        rows.append(row("guard", "0x%08x" % guard[0][0], "+0x%x" % guard[0][3],
                        "the field tested before anything is built: a null there is what the body fills"))

    for va, target in facts["literal"]:
        text = wide(xbe, target)
        if not text:
            continue
        rows.append(row("literal", "0x%08x" % va, text,
                        "wide literal pushed in the body" if text != KEY else
                        "the config key: UE2 condenses a lookup into `ini:Section.Key`"))

    if device_path:
        rows.append(row("class", "", device_path,
                        "Default.ini [Engine.Engine] AudioDevice -- the class the key resolves to; "
                        "the image holds no such string, which is why a string search cannot find this path"))

    # Each construct the body makes from a literal, and the field it lands in.
    for va, reg, disp, _value in facts["store"]:
        call_va, target = construct_before(insns, va)
        callee = symbols.get(target, ("", 0))[0] if target else ""
        rows.append(row("store", "0x%08x" % va, "+0x%x" % disp,
                        "the return of %s lands here" %
                        (("`%s` (0x%08x)" % (callee or "?", target)) if target else "a call not in the window")))

    # Indirect dispatches, named from the table of the class the body constructed most recently.
    built = []
    for va, reg, disp, _value in facts["store"]:
        call_va, target = construct_before(insns, va)
        literal = None
        for lva, ltarget in facts["literal"]:
            if lva < va and wide(xbe, ltarget):
                literal = (lva, wide(xbe, ltarget))
        built.append((va, disp, call_va, literal))

    table_va, table_row = (None, None)
    class_name = device_path.split(".", 1)[1] if "." in device_path else device_path
    if class_name:
        table_va, table_row = class_table(names_path, class_name)

    for va, reg, disp in facts["dispatch"]:
        owner = None
        for sva, sdisp, _cva, literal in built:
            if sva < va:
                owner = (sdisp, literal)
        detail = "slot %d of the object built into +0x%x" % (disp // 4, owner[0]) if owner else \
            "slot %d -- the object is not one this body built" % (disp // 4)
        value = "+0x%x" % disp
        # Only the object built from the config key is the ini's class, so only its slots have a table
        # in the image to be read from; the voice object is a script class and has no native table.
        if owner and table_va is not None and first_store is not None and owner[0] == first_store[2]:
            try:
                blob = xbe.read_va(table_va, 4 * 56)
                target = dict(slots_of(blob, 56)).get(disp // 4)
            except (KeyError, IndexError):
                target = None
            if target:
                value = "%s -> 0x%08x %s" % ("+0x%x" % disp, target, symbols.get(target, ("?", 0))[0])
                detail += "; %s's own table installs that" % class_name
        rows.append(row("dispatch", "0x%08x" % va, value, detail))

    for va, reg, disp, _value in facts["clear"]:
        rows.append(row("clear", "0x%08x" % va, "+0x%x" % disp,
                        "the field is zeroed on the failure path"))

    # The calls in the body that are not stores: the edges the reader needs to see.
    for va, target in facts["call"]:
        callee, csize = symbols.get(target, ("0x%08x" % target, 0))
        rows.append(row("call", "0x%08x" % va, callee, "%d bytes" % csize if csize else "not in the map"))

    # Callers, and the read-back that decides what the store's offset is.
    audio_disp = first_store[2] if first_store else None
    for cva in callers(xbe, symbols, start):
        cstart, csize, cname = body_of(xbe, symbols, cva)
        cinsns = disassemble(xbe, cstart, csize)
        rows.append(row("caller", "0x%08x" % cva, cname,
                        "the engine's own body: it calls the init and then uses its product"))
        loads, dispatch, adj = offset_after(cinsns, cva, window=14)
        hit = [l for l in loads if audio_disp is not None and l[1] == audio_disp]
        if hit and dispatch:
            detail = "the caller reads the offset the store wrote and dispatches slot %d (+0x%x) on it" \
                     % (dispatch[0][2] // 4, dispatch[0][2])
            if table_va is not None:
                try:
                    blob = xbe.read_va(table_va, 4 * 56)
                    target = dict(slots_of(blob, 56)).get(dispatch[0][2] // 4)
                except (KeyError, IndexError):
                    target = None
                if target:
                    detail += ": 0x%08x %s from the same table" % (target, symbols.get(target, ("?", 0))[0])
            rows.append(row("readback", "0x%08x" % cva, "+0x%x" % audio_disp, detail))

    # Is the body virtual? A body no table installs cannot be a slot, so its offset cannot be a vtable one.
    installed = 0
    for trow in read_csv(names_path):
        try:
            tva, count = int(trow["table_va"], 16), 56
        except (KeyError, TypeError, ValueError):
            continue
        try:
            blob = xbe.read_va(tva, 4 * count)
        except (KeyError, IndexError):
            continue
        if any(target == start for _slot, target in slots_of(blob, count)):
            installed += 1
    rows.append(row("vtable", "", str(installed),
                    "class tables in the image that install the body -- 0 is what makes it a non-virtual "
                    "member function, reached by `call`, not by dispatch"))

    # The join: how many of the census sites read a field at the offset the engine wrote.
    typing = read_csv(typing_path)
    if audio_disp is not None:
        field_rows = [r for r in typing if (r.get("root") or "") == "field"]
        same = [r for r in field_rows if (r.get("field_disp") or "") == "+0x%x" % audio_disp]
        rows.append(row("join", "+0x%x" % audio_disp, str(len(same)),
                        "of %d census sites, those that read a field at the offset the engine writes: "
                        "0 means no site dispatches the device through the engine's own field" % len(typing)))
    return rows, {"body": start, "name": name, "size": size, "site": site, "audio_disp": audio_disp,
                  "table": table_va, "class": class_name, "installed": installed, "key_va": key_va}


def callers(xbe, symbols, target):
    """Every `call rel32` to `target`, confirmed as an instruction start by the decoder."""
    out = []
    for sec in code_sections(xbe, symbols):
        blob = xbe.read_va(sec.va, sec.fsize)
        for i in range(len(blob) - 5):
            if blob[i] != 0xE8:
                continue
            rel = struct.unpack_from("<i", blob, i + 1)[0]
            if sec.va + i + 5 + rel != target:
                continue
            va = sec.va + i
            start, size, _name = body_of(xbe, symbols, va)
            if start is None:
                continue
            insns = disassemble(xbe, start, size)
            if any(iva == va and CALLEE.match(text) and int(CALLEE.match(text).group(1), 16) == target
                   for iva, text in insns):
                out.append(va)
    return sorted(out)


def report(rows, ctx):
    print("init body 0x%08x %s, %d bytes; it pushes the key at 0x%08x"
          % (ctx["body"], ctx["name"], ctx["size"], ctx["key_va"]))
    if ctx["audio_disp"] is not None:
        print("the subsystem is stored at +0x%x of the engine%s"
              % (ctx["audio_disp"], (" and %s's table installs the device at slot 31" % ctx["class"])
                 if ctx["table"] else ""))
    print("class tables that install the body: %d (non-virtual)" % ctx["installed"])
    kinds = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print("  rows: " + ", ".join("%s %d" % kv for kv in sorted(kinds.items())))
    for r in rows:
        if r["kind"] in ("store", "dispatch", "readback", "join"):
            print("  %-9s %-9s %-44s %s" % (r["kind"], r["address"], r["value"][:44], r["detail"][:96]))


# ---------------------------------------------------------------- selftest

SYNTHETIC = [
    (0x1000, "push esi"),
    (0x1001, "mov esi,ecx"),
    (0x1003, "mov eax,DWORD PTR [esi+0x34]"),
    (0x1006, "test eax,eax"),
    (0x1008, "jne 0x1080"),
    (0x1010, "push 0x400000"),
    (0x1015, "call 0x9000"),
    (0x101a, "add esp,0x4"),
    (0x101d, "mov DWORD PTR [esi+0x34],eax"),
    (0x1023, "mov edx,DWORD PTR [eax]"),
    (0x1025, "mov ecx,eax"),
    (0x1027, "call DWORD PTR [edx+0x7c]"),
    (0x1030, "call 0x9100"),
    (0x1035, "mov DWORD PTR [esi+0x34],0x0"),
    (0x103b, "ret"),
]
SYNTHETIC_INI = "[Engine.Engine]\r\nRenderDevice=D3DDrv.D3DRenderDevice\r\nAudioDevice=XboxAudio.XboxAudioSubsystem\r\n"


def selftest():
    bad = []
    facts = scan_body(SYNTHETIC)
    if [d for _va, _r, d, _v in facts["store"]] != [0x34]:
        bad.append("stores read as %r" % (facts["store"],))
    if [d for _va, _r, d, _v in facts["clear"]] != [0x34]:
        bad.append("clears read as %r" % (facts["clear"],))
    if [d for _va, _r, d in facts["dispatch"]] != [0x7C]:
        bad.append("dispatches read as %r" % (facts["dispatch"],))
    call_va, target = construct_before(SYNTHETIC, 0x101D)
    if (call_va, target) != (0x1015, 0x9000):
        bad.append("the construct before a store read as %r" % ((call_va, target),))
    # a call further back than the window is not a store's source
    far = [(0x3000, "call 0x9100"), (0x3005, "add esp,0x4"), (0x3008, "push eax"), (0x3009, "push ebx"),
           (0x300a, "push ecx"), (0x300b, "push edx"), (0x300c, "push esi"),
           (0x300d, "mov DWORD PTR [esi+0x3c],eax")]
    call_va, target = construct_before(far, 0x300D, window=3)
    if call_va is not None:
        bad.append("a call outside the window took a store's source: %r" % ((call_va, target),))
    loads, dispatch, _adj = offset_after(SYNTHETIC, 0x101D, window=4)
    if (loads, [d for _va, _r, d in dispatch]) != ([], [0x7C]):
        bad.append("the window after a store read as %r" % ((loads, dispatch),))
    # the interior-pointer idiom: lea esi,[esi-0x28] then a load at +0xc is the engine's +0x34
    tail = [(0x2000, "lea ecx,[esi-0x28]"), (0x2004, "call 0x1000"),
            (0x2009, "mov ecx,DWORD PTR [esi+0xc]"), (0x200c, "mov edx,DWORD PTR [ecx]"),
            (0x200e, "push edi"), (0x200f, "call DWORD PTR [edx+0x80]"), (0x2012, "ret")]
    loads, dispatch, adj = offset_after(tail, 0x2004, window=8)
    if adj != 0x28 or [d for _b, d in loads] != [0x34]:
        bad.append("the interior-pointer read-back read as %r %r" % (adj, loads))
    if [d for _va, _r, d in dispatch] != [0x80]:
        bad.append("the read-back's dispatch read as %r" % (dispatch,))
    if ini_value(SYNTHETIC_INI, "Engine.Engine", "AudioDevice") != "XboxAudio.XboxAudioSubsystem":
        bad.append("the ini read as %r" % ini_value(SYNTHETIC_INI, "Engine.Engine", "AudioDevice"))
    if ini_value(SYNTHETIC_INI, "Engine.Engine", "Missing") != "":
        bad.append("a missing key answered %r" % ini_value(SYNTHETIC_INI, "Engine.Engine", "Missing"))
    # a class lookup that ignores case (the ini says `XboxAudio`, the build's table says `XBoxAudio`)
    tables = {(0x4D21F8): "XBoxAudioSubsystem"}
    if [t for t, n in tables.items() if n.lower() == "xboxaudiosubsystem"] != [0x4D21F8]:
        bad.append("the case-insensitive class lookup lost the table")
    blob = struct.pack("<4I", 1, 2, 3, 0x2FD70)
    if dict(slots_of(blob, 4))[3] != 0x2FD70:
        bad.append("slot decode read as %r" % (slots_of(blob, 4),))
    if dict(slots_of(blob[:8], 4)) != {0: 1, 1: 2}:
        bad.append("a short table read past its bytes: %r" % (slots_of(blob[:8], 4),))
    for line in bad:
        print("FAIL " + line)
    if bad:
        raise SystemExit(1)
    print("selftest ok: stores, clears, dispatches, the construct-before-store rule, the interior-pointer "
          "read-back, the ini lookup and the slot decode")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("--ini", help="the dump's System/Default.ini: the only place the class name lives")
    ap.add_argument("--functions", default=FUNCTIONS)
    ap.add_argument("--names", default=NAMES)
    ap.add_argument("--edges", default=EDGES)
    ap.add_argument("--typing", default=TYPING)
    ap.add_argument("-o", "--out", default=OUT)
    ap.add_argument("--check", action="store_true", help="synthetic self-test; no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        selftest()
        return 0
    if not args.xbe:
        ap.error("--xbe is required")
    rows, ctx = measure(args.xbe, args.ini, args.functions, args.names, args.edges, args.typing)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=HEADER, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    report(rows, ctx)
    print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
