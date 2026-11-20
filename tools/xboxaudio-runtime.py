#!/usr/bin/env python3
"""The audio subsystem's run-time half: the per-frame pump, the two bodies it drives, and the
block of named counters the device registers when it is created (U34's remainder).

`inventory/audio_engine_init.csv` closed the *creation* side: `Engine_UnEngine_InitAudio` reads
`ini:Engine.Engine.AudioDevice`, builds the subsystem into `Engine.Audio` (`+0x34`) and dispatches
slot 31 on it. This reads what that object then does every frame, and it is all one shape: a body
whose work is gated by fields of the object it was handed.

    table    `XBoxAudioSubsystem`'s class table, slot 34 (+0x88) casts the pump
    pump     5,277 bytes, 1,466 instructions, 46 direct calls -- 15 of them DSound/XAudio entries
    counter  it opens a cycle counter: `rdtsc`, then `sub [0x7b5dfc + [this+0x18c]*4], tsclow`
    gates    three field tests decide whether a whole section of it runs at all
    files    two of the calls it gates are the streaming read path: a synchronous `CreateFileA`/
             `ReadFile` body, and the `GetOverlappedResult` body that calls it
    stats    the device body's own callee registers eight named counters in one group

**The stats are the interesting part and the claim needs its shape stated.** `0x0002e900` pushes a
wide literal, builds an `FString` from it, pushes a second wide literal, builds a second `FString`,
and calls `0x2530f0` on the engine's counter block (`0x7b5dd8`); the same first literal (`Audio`)
precedes every call and the second differs. The return lands in a field of the object. That is a
named-counter registration: a group, a name, and a per-instance handle, done once and guarded by a
global flag -- the same shape as Unreal's `FStat`. No string in the body says
"stat", so the rows below carry the evidence (group, name, store offset, the guard global) and the
word *registration* rather than a class name.

**The handles and the pump's `rdtsc` are two different mechanisms, and the census says so.** The
first reading of this body guessed that the cycle-counter array the pump writes (`0x007b5dfc`) was
the one the eight handles index into. Counting the absolute references to both globals refutes it:
`0x007b5dfc` is read from 106 sites in dozens of engine bodies (renderer, actor, spawn, level tick,
the D3D driver), which is an engine-wide cycle-scope array, while the block at `0x007b5dd8` is an
object whose address is passed as `this` into its own methods. The rows below carry both counts, so
the shape is stated and the guess is not.

    python3 tools/xboxaudio-runtime.py --image DUMP/default.xbe -o inventory/audio_runtime.csv
    python3 tools/xboxaudio-runtime.py --check     # synthetic instruction rows; no dump, writes nothing

Addresses, symbol names, byte counts and the counter names the module itself pushes: no game data.
"""
import argparse
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

INVENTORY = os.path.join(ROOT, "inventory")
FUNCTIONS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
TABLES = os.path.join(INVENTORY, "xbe_class_tables.csv")
NAMES = os.path.join(INVENTORY, "xbe_class_table_names.csv")
MODULES = os.path.join(INVENTORY, "function_modules.csv")
OUT = os.path.join(INVENTORY, "audio_runtime.csv")
HEADER = ["kind", "address", "value", "detail"]

CLASS = "XBoxAudioSubsystem"
PUMP_SLOT = 34            # +0x88
DEVICE_SLOT = 31          # +0x7c, the body audio_engine_init.csv named
REGISTER_API = 0x002530f0  # group, name, and a constant -> a handle, on the engine's counter block
COUNTER_BLOCK = 0x007b5dd8
COUNTER_ARRAY = 0x007b5dfc
WIDE_PUSH = re.compile(r"^push\s+0x([0-9a-f]+)$")
CALL = re.compile(r"^call\s+0x([0-9a-f]+)$")
VCALL = re.compile(r"^call\s+DWORD PTR \[(\w+)\+0x([0-9a-f]+)\]$")
STORE_EAX = re.compile(r"^mov\s+DWORD PTR \[(\w+)\+0x([0-9a-f]+)\],eax$")
FIELD_LOAD = re.compile(r"^mov\s+(\w+),\s*DWORD PTR \[(\w+)\+0x([0-9a-f]+)\]$")
API = ("_DirectSound", "_IDirectSound", "_XAudio", "_XWave", "_XVoice", "_XMedia",
       "_CreateFileA", "_ReadFile", "_SetFilePointer", "_CreateEventA", "_GetOverlappedResult",
       "_CloseHandle")
FILE_API = ("_CreateFileA", "_ReadFile", "_SetFilePointer", "_GetOverlappedResult",
            "_CreateEventA")
# The band the DirectSound/XAudio import thunks sit in: every SDK entry this module calls
# (`_DirectSoundCreate@12` 0x004a20e0, `_IDirectSoundBuffer_Stop@4` 0x004a04f2, ...) is inside it,
# and the module's own bodies are all below it.
SDK_BAND = (0x0049f000, 0x004a3000)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- pure helpers (self-tested)

def wide_at(read, va, limit=64):
    """A wide literal at `va`, stopping at the first NUL unit.

    The image's wide pool packs the strings back to back (`PlaySound\\0Audio\\0`), so a reader that
    just strips non-printables from a run of units joins two of them into one name -- which is how
    an earlier artifact came to quote `PlaySoundAudio` for a body that pushes `PlaySound`.
    """
    try:
        raw = read(va, limit * 2)
    except Exception:
        return None
    out = []
    for i in range(0, len(raw) - 1, 2):
        unit = raw[i] | (raw[i + 1] << 8)
        if unit == 0:
            break
        if not 31 < unit < 127:
            return None
        out.append(chr(unit))
    text = "".join(out)
    return text if len(text) >= 3 else None


def guards(rows, this_reg="ebp", field_window=3, lead=48):
    """Field tests that gate work: `mov r,[this+d]; test r,r; je ...` in the body's first 24 insns.

    Only the entry guards are wanted; a later `test` on a computed value is a branch, not a gate
    on the object's state.
    """
    out = []
    for i, (site, text) in enumerate(rows[:lead]):
        mt = FIELD_LOAD.match(text)
        if not mt or mt.group(2) != this_reg:
            continue
        reg, disp = mt.group(1), int(mt.group(3), 16)
        window = rows[i + 1:i + 1 + field_window]
        want = re.compile(r"^test\s+%s\s*,\s*%s$" % (reg, reg))
        if any(want.match(t.strip()) for _, t in window):
            out.append((site, disp))
    return out


def pairs(literals, calls):
    """Group/name pairs: the wide-literal pushes in order, two per registration call.

    A registration pushes the group, then the name; anything else in between is the `FString`
    constructor both of them go through. Returning None means the counts disagree, which the
    caller reports instead of pairing the wrong two strings.
    """
    if not calls or len(literals) != 2 * len(calls):
        return None
    return [(literals[2 * i][2], literals[2 * i + 1][2]) for i in range(len(calls))]


def store_after(rows, start, this_reg, stop=None):
    """The first `mov [this+d],eax` after a call: where the call's return was kept.

    A registration's store sits a few instructions later, after the next pair's string is pushed and
    before its constructor call -- so the scan runs to the next registration, not to the next call.
    Anything else is a return the body drops.
    """
    for site, text in rows[start:]:
        if stop is not None and site >= stop:
            return None, None
        mt = STORE_EAX.match(text)
        if mt and mt.group(1) == this_reg:
            return int(mt.group(2), 16), site
        if CALL.match(text) or VCALL.match(text):
            return None, None
    return None, None


def api_calls(rows, name_of):
    """Direct calls into the audio/file SDK, by resolved name."""
    out = []
    for site, text in rows:
        mt = CALL.match(text)
        if not mt:
            continue
        name = name_of(int(mt.group(1), 16))
        if name and name.startswith(API):
            out.append((site, int(mt.group(1), 16), name))
    return out


def cache_row(rows):
    """The once-only side: the globals a registration body reads, and the stride it copies them at.

    A second subsystem instance must not register the same names again, so the ids are cached in a
    global array and copied from it -- which is why the body's own returns are not all stored where a
    single instance would put them.
    """
    globals_seen = []
    for _site, text in rows:
        mt = re.match(r"^mov\s+\w+,(?:DWORD PTR )?\w+:0x([0-9a-f]+)$", text)
        if mt and "fs:" not in text and "gs:" not in text:
            va = int(mt.group(1), 16)
            if va >= 0x400000:          # a .data global, not a frame or segment offset
                globals_seen.append(va)
    if len(globals_seen) < 2:
        return None
    return ("cache", "0x%08x" % min(globals_seen),
            ", ".join("0x%08x" % g for g in sorted(set(globals_seen))),
            "the .data globals the body reads in its own right: the once-only flag, and on the "
            "second-instance path the cached ids and their count")


def dword_refs(xbe, va):
    """Every aligned dword in a loaded section that spells `va`: an absolute reference to it."""
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


def insn_at(xbe, funcs, va, bases=None):
    """The decoded instruction that covers `va`, in the body the map places there."""
    import bisect
    bases = bases if bases is not None else sorted(funcs)
    i = bisect.bisect_right(bases, va) - 1
    owner = None
    if i >= 0:
        base = bases[i]
        name, size = funcs[base]
        if base <= va < base + size:
            owner = (base, name, size)
    if not owner:
        return None, None
    best = None
    for site, text in disasm(xbe, owner[0], owner[2]):
        if site <= va:
            best = (site, text)
        else:
            break
    return owner, best


def counter_ref_rows(xbe, funcs, modules):
    """Who else reads the counter block and its array, and from which module.

    The previous reading called `0x007b5dfc` "the counter array the eight handles index into". An
    absolute-reference census says otherwise: the same array is read by engine bodies with nothing to
    do with audio (renderer, actor, spawn, level tick, the D3D driver), which is what an engine-wide
    cycle-scope array looks like, and the block at `0x007b5dd8` is an object whose address is passed
    as `this` to its own methods. Counting the references is what separates the two.
    """
    out = []
    bases = sorted(funcs)
    for va, label in ((COUNTER_BLOCK, "block"), (COUNTER_ARRAY, "array")):
        grouped = {}
        loose = []
        for site in dword_refs(xbe, va):
            owner, insn = insn_at(xbe, funcs, site, bases)
            if not owner:
                loose.append(site)
                continue
            grouped.setdefault(owner, []).append((site, insn[1] if insn else "?"))
        for (base, name, size), hits in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
            site, text = hits[0]
            out.append(("counter-ref", "0x%08x" % va,
                        "%s@0x%08x (%d site%s)" % (name, base, len(hits),
                                                   "" if len(hits) == 1 else "s"),
                        "%s (%d bytes, %s): %d reference(s), first at 0x%08x `%s`"
                        % (name, size, modules.get(base, "unattributed"), len(hits), site, text)))
        if loose:
            out.append(("counter-ref", "0x%08x" % va, "%d site(s) with no body" % len(loose),
                        "references inside a body the function map does not cover: %s"
                        % ", ".join("0x%08x" % s for s in loose[:8])))
    return out


def ref_census(rows, funcs, modules):
    """One line per target: sites, bodies, and how many of those bodies are not audio module code."""
    out = []
    for va, label in ((COUNTER_BLOCK, "block"), (COUNTER_ARRAY, "array")):
        local = [r for r in rows if r[0] == "counter-ref" and r[1] == "0x%08x" % va]
        sites = 0
        bodies = 0
        audio = 0
        for r in local:
            text = r[3]
            if "no body" in r[2]:
                sites += int(r[2].split()[0])
                continue
            bodies += 1
            sites += int(r[2].rsplit("(", 1)[1].split()[0])
            if "XboxAudio" in text:
                audio += 1
        out.append(("ref-census", "0x%08x" % va, "%s" % label,
                    "%d site(s) over %d bod%s, %d of them XboxAudio"
                    % (sites, bodies, "y" if bodies == 1 else "ies", audio)))
    return out


def counter_row(rows, this_reg="ebp", window=14):
    """The cycle-counter bookkeeping: an `rdtsc` whose low dword is subtracted into a counter.

    The row names the array, the field that indexes it, and the subtraction. Whether that array is
    the one the registrations hand handles into is a *separate* question, and the `counter-ref`
    census below answers it: `0x007b5dfc` is read by engine bodies with nothing to do with audio,
    so the pump's `rdtsc` block is the engine's shared cycle-scope idiom, not one of the eight.
    """
    for i, (site, text) in enumerate(rows):
        if not text.startswith("rdtsc"):
            continue
        back = rows[max(0, i - window):i]
        fwd = rows[i + 1:i + 1 + window]
        array = None
        index_field = None
        stride = None
        sub = None
        for _s, t in back:
            mt = re.match(r"^mov\s+(\w+),DWORD PTR ds:0x([0-9a-f]+)$", t)
            if mt:
                array = int(mt.group(2), 16)
            mt = re.match(r"^mov\s+(\w+),DWORD PTR \[%s\+0x([0-9a-f]+)\]$" % this_reg, t)
            if mt:
                index_field = int(mt.group(2), 16)
            mt = re.match(r"^lea\s+\w+,\[\w+\+\w+\*([24])\]$", t)
            if mt:
                stride = int(mt.group(1))
        for _s, t in fwd:
            mt = re.match(r"^sub\s+DWORD PTR \[(\w+)\],(\w+)$", t)
            if mt:
                sub = (t, mt.group(1))
                break
        if not (array and index_field and sub):
            continue
        return ("counter", "0x%08x" % site, "rdtsc",
                "cycle bookkeeping: 0x%08x + [this+0x%02x]*%d is a counter slot, and the timestamp "
                "is subtracted into it (`%s`)"
                % (array, index_field, stride or 4, sub[0]))
    return None


# ---------------------------------------------------------------- image side

def disasm(xbe, addr, size):
    blob = xbe.read_va(addr, size)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
        fh.write(blob)
        path = fh.name
    try:
        text = subprocess.run(["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                               "--adjust-vma=0x%x" % addr, path],
                              capture_output=True, text=True).stdout
    finally:
        os.unlink(path)
    out = []
    for line in text.splitlines():
        mt = re.match(r"\s*([0-9a-f]+):\t((?:[0-9a-f]{2} )+)\s*\t?(.*)$", line)
        if mt:
            out.append((int(mt.group(1), 16), mt.group(3).strip()))
    return out


def rows(path):
    """CSV rows as dicts -- every inventory artifact here carries a header."""
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def functions():
    out = {}
    with open(FUNCTIONS) as fh:
        for r in csv.reader(fh):
            if len(r) < 3:
                continue
            try:
                out[int(r[0], 16)] = (r[1], int(r[2]))
            except ValueError:
                continue
    return out


def table_slot(table_va, slot, xbe):
    """Slot `slot` of the class table at `table_va` -- the dword there is the body's address."""
    raw = xbe.read_va(table_va + 4 * slot, 4)
    if len(raw) < 4:
        return None
    va = struct.unpack("<I", raw)[0]
    return va if 0x10000 <= va < 0x00600000 else None


def find_table(read_csv, tables=TABLES, names=NAMES):
    """The table whose `class` column is this build's audio subsystem, and its slot count.

    `inventory/xbe_class_tables.csv` carries the slots and the install stores but no class name;
    the name lives in `xbe_class_table_names.csv`, keyed by the same table address.
    """
    slots = {}
    for r in read_csv(tables):
        try:
            slots[int(r["table_va"], 16)] = int(r["slots"])
        except (KeyError, ValueError):
            continue
    for r in read_csv(names):
        if (r.get("class") or "").strip().lower() != CLASS.lower():
            continue
        va = int(r["table_va"], 16)
        if va in slots:
            return va, slots[va]
    raise SystemExit("%s: no named table in %s" % (CLASS, os.path.basename(names)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image")
    ap.add_argument("--tables", default=TABLES)
    ap.add_argument("--names", default=NAMES)
    ap.add_argument("-o", "--out", default=OUT)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    if args.check:
        return selftest()

    if not args.image:
        raise SystemExit("--image is required (the dump's default.xbe)")

    xbe_mod = _load("sg1_xbe", os.path.join(ROOT, "tools", "xbe.py"))
    xbe = xbe_mod.Xbe(args.image)
    funcs = functions()

    def name_of(va):
        row = funcs.get(va)
        return row[0] if row else None

    table, table_slots = find_table(rows, args.tables, args.names)
    if table_slots <= PUMP_SLOT:
        raise SystemExit("%s's table has %d slots, no slot %d" % (CLASS, table_slots, PUMP_SLOT))
    pump = table_slot(table, PUMP_SLOT, xbe)
    device = table_slot(table, DEVICE_SLOT, xbe)
    if not pump or not device:
        raise SystemExit("slot 34 or 31 of the %s table is not a code pointer" % CLASS)

    out = []
    pump_rows = disasm(xbe, pump, funcs[pump][1])
    pump_calls = [(site, int(m.group(1), 16)) for site, t in pump_rows if (m := CALL.match(t))]
    pump_lits = []
    for site, text in pump_rows:
        mt = WIDE_PUSH.match(text)
        if mt:
            s = wide_at(xbe.read_va, int(mt.group(1), 16))
            if s:
                pump_lits.append((site, int(mt.group(1), 16), s))
    vcalls = [(site, m.group(1), int(m.group(2), 16)) for site, t in pump_rows
              if (m := VCALL.match(t))]
    sdk_named = api_calls(pump_rows, name_of)
    band = [(site, t) for site, t in pump_calls if SDK_BAND[0] <= t < SDK_BAND[1]]

    out.append(("pump", "0x%08x" % pump,
                "%s (%d bytes, %d insns)" % (funcs[pump][0], funcs[pump][1], len(pump_rows)),
                "slot %d (+0x88) of %s's table 0x%08x: the per-frame path; %d direct calls "
                "(%d into the SDK's import band 0x%08x-0x%08x, %d of them named), %d indirect, "
                "%d wide literals"
                % (PUMP_SLOT, CLASS, table, len(pump_calls), len(band), SDK_BAND[0], SDK_BAND[1],
                   len(sdk_named), len(vcalls), len(pump_lits))))

    for site, disp in guards(pump_rows):
        gated = ""
        for csite, target in pump_calls:
            if 0 < csite - site <= 24:
                gated = "0x%08x %s" % (target, name_of(target) or "FUN_*")
                break
        out.append(("guard", "0x%08x" % site, "+0x%02x" % disp,
                    "a null at [this+0x%02x] skips %s"
                    % (disp, ("0x%08x, which the map names %s" % (int(gated.split()[0], 16),
                                                                  gated.split()[1]))
                       if gated else "the rest of the body")))

    counter = counter_row(pump_rows, "ebp")
    if counter:
        out.append(counter)
    for site, va, text in pump_lits:
        out.append(("literal", "0x%08x" % site, text,
                    "wide literal at 0x%08x, pushed by the pump" % va))

    # The streaming read path, found from the pump's own calls: the bodies it reaches that talk to
    # the file API. `_CreateFileA`/`_ReadFile`/`_GetOverlappedResult` are the read, not the playback.
    seen_file = {}
    queue = [(site, target, "the pump") for site, target in pump_calls]
    for _depth in range(2):          # the read body is one call past the completion body
        nxt = []
        for site, target, caller in queue:
            if target in seen_file or target not in funcs:
                continue
            sub = disasm(xbe, target, funcs[target][1])
            sub_api = [a for a in api_calls(sub, name_of) if a[2].startswith(FILE_API)]
            subs = [(s, int(m.group(1), 16)) for s, t in sub if (m := CALL.match(t))]
            if not sub_api:
                nxt.extend((s, t, "0x%08x" % target) for s, t in subs)
                continue
            seen_file[target] = True
            out.append(("file", "0x%08x" % target,
                        "%s (%d bytes)" % (funcs[target][0], funcs[target][1]),
                        "called at 0x%08x by %s; the map names it %s; file API: %s"
                        % (site, caller, funcs[target][0],
                           ", ".join(sorted({n for _, _, n in sub_api})))))
            nxt.extend((s, t, "0x%08x" % target) for s, t in subs)
        queue = nxt

    # The registration block: the device body's own callee whose literals pair up as group/name.
    dev_rows = disasm(xbe, device, funcs[device][1])
    dev_calls = [int(m.group(1), 16) for _, t in dev_rows if (m := CALL.match(t))]
    for target in dict.fromkeys(dev_calls):
        if target not in funcs:
            continue
        body = disasm(xbe, target, funcs[target][1])
        lits = []
        for site, text in body:
            mt = WIDE_PUSH.match(text)
            if mt:
                s = wide_at(xbe.read_va, int(mt.group(1), 16))
                if s:
                    lits.append((site, int(mt.group(1), 16), s))
        calls = [(site, int(m.group(1), 16)) for site, t in body if (m := CALL.match(t))]
        reg_calls = [c for c in calls if c[1] == REGISTER_API]
        paired = pairs(lits, reg_calls)
        if not paired:
            continue
        this_reg = "esi"
        if not any(re.match(r"^mov\s+esi\s*,\s*ecx$", t) for _s, t in body):
            this_reg = "edi"
        index = {site: n for n, (site, _t) in enumerate(body)}
        for i, (site, _target) in enumerate(reg_calls):
            group, name = paired[i]
            stop = reg_calls[i + 1][0] if i + 1 < len(reg_calls) else None
            disp, store_site = store_after(body, index[site] + 1, this_reg, stop)
            out.append(("register", "0x%08x" % site, "%s/%s" % (group, name),
                        "the group and the name are pushed as wide literals in that order; the "
                        "return is stored at +0x%02x (0x%08x)" % (disp, store_site)
                        if disp is not None else
                        "the group and the name are pushed as wide literals in that order; the "
                        "return is not stored in the body's own this"))
        out.append(("register-body", "0x%08x" % target,
                    "%s (%d bytes)" % (funcs[target][0], funcs[target][1]),
                    "%d registrations, two literals each; counter block 0x%08x"
                    % (len(paired), COUNTER_BLOCK)))
        cache = cache_row(body)
        if cache:
            out.append(cache)

    # Who else reads the block and the array the registration hands handles into.
    modules = {}
    for r in rows(MODULES):
        try:
            modules[int(r["address"], 16)] = (r.get("module") or "unattributed")
        except (KeyError, ValueError):
            continue
    refs = counter_ref_rows(xbe, funcs, modules)
    out.extend(refs)
    out.extend(ref_census(refs, funcs, modules))

    out.append(("census", "", "pump",
                "pump=%d insns %d direct %d sdk-band %d indirect %d literals; guards=%d; "
                "file=%d; registrations=%d; registers=%d"
                % (len(pump_rows), len(pump_calls), len(band), len(vcalls), len(pump_lits),
                   len(guards(pump_rows)), sum(1 for r in out if r[0] == "file"),
                   len(reg_rows(out)), len({r[2] for r in reg_rows(out)}))))

    if args.report:
        for r in out:
            print("%-13s %-12s %-42s %s" % r)
        return 0

    with open(args.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(out)
    print("wrote %s (%d rows)" % (os.path.relpath(args.out, ROOT), len(out)))
    return 0


def reg_rows(rows):
    return [r for r in rows if r[0] == "register"]


def selftest():
    """Synthetic instruction rows: the pool boundary, the gate rule, the pair rule, the store rule."""
    pool = "PlaySound".encode("utf-16-le") + b"\x00\x00" + "Audio".encode("utf-16-le")
    base = 0x4d1f60

    def read(va, size):
        off = va - base
        return pool[off:off + size] if off >= 0 else b""

    assert wide_at(read, 0x4d1f60) == "PlaySound", wide_at(read, 0x4d1f60)
    assert wide_at(read, 0x4d1f60 + 20) == "Audio"
    assert wide_at(read, 0x99999) is None

    body = [(0x100, "mov eax,DWORD PTR [ebp+0x98]"),
            (0x103, "test eax,eax"),
            (0x105, "je 0x900"),
            (0x10b, "mov ecx,DWORD PTR [ebp+0x16c]"),
            (0x111, "test ecx,ecx"),
            (0x113, "je 0x200"),
            (0x119, "mov edx,DWORD PTR [ecx+0x4]"),
            (0x11c, "test edx,edx"),
            (0x11e, "je 0x200")]
    assert guards(body) == [(0x100, 0x98), (0x10b, 0x16c)], guards(body)

    lits = [(1, 0, "Audio"), (2, 0, "PlaySound"), (3, 0, "Audio"), (4, 0, "Update")]
    assert pairs(lits, [(10, 1), (20, 1)]) == [("Audio", "PlaySound"), ("Audio", "Update")]
    assert pairs(lits, [(10, 1)]) is None
    assert pairs(lits[:3], [(10, 1), (20, 1)]) is None

    seq = [(0x200, "call 0x2530f0"),
           (0x205, "mov DWORD PTR [esi+0x10],eax"),
           (0x208, "call 0x2530f0")]
    assert store_after(seq, 1, "esi") == (0x10, 0x205)
    assert store_after(seq, 1, "edi") == (None, None)
    # the store that belongs to the *next* registration is not this one's
    assert store_after(seq, 1, "esi", stop=0x208) == (0x10, 0x205)
    assert store_after(seq, 2, "esi") == (None, None)
    # a return that is dropped before the next call is not the next call's store
    seq2 = [(0x200, "call 0x2530f0"), (0x205, "push 0x0"), (0x207, "call 0x1b260"),
            (0x20a, "mov DWORD PTR [esi+0x4],eax")]
    assert store_after(seq2, 1, "esi") == (None, None), store_after(seq2, 1, "esi")

    spaced = [(0x300, "mov    eax,DWORD PTR [ebp+0x98]"), (0x303, "test   eax,eax"),
              (0x305, "je 0x400")]
    assert guards(spaced) == [(0x300, 0x98)], guards(spaced)

    pump = [(0x500, "mov eax,DWORD PTR [ebp+0x18c]"),
            (0x506, "mov ecx,DWORD PTR ds:0x7b5dfc"),
            (0x50c, "lea ecx,[ecx+eax*4]"),
            (0x50f, "rdtsc"),
            (0x511, "mov DWORD PTR ds:0x5d65ec,edx"),
            (0x517, "sub DWORD PTR [ecx],edx")]
    row = counter_row(pump)
    assert row and "+0x18c" in row[3] and "0x007b5dfc" in row[3] and "sub" in row[3], row

    names = {0x4a20e0: "_DirectSoundCreate@12", 0x33e2cd: "_CreateFileA@28", 0x574d0: "FUN_000574d0"}
    assert [n for _, _, n in api_calls([(1, "call 0x4a20e0"), (2, "call 0x574d0")], names.get)] \
        == ["_DirectSoundCreate@12"]

    # The reference census counts sites from the row text it wrote, and a body row is one body.
    synth = [("counter-ref", "0x007b5dfc", "FUN_00000010 (3 sites)",
              "FUN_00000010 (40 bytes, Engine): first at 0x00000020 `mov eax,ds:0x7b5dfc`"),
             ("counter-ref", "0x007b5dfc", "XboxAudio_Foo (1 site)",
              "XboxAudio_Foo (60 bytes, XboxAudio): first at 0x00031000 `mov ecx,DWORD PTR ds:0x7b5dfc`"),
             ("counter-ref", "0x007b5dfc", "2 site(s) with no body",
              "references inside a body the function map does not cover: 0x0008953c, 0x00093294"),
             ("counter-ref", "0x007b5dd8", "FUN_000002e9 (2 sites)",
              "FUN_000002e9 (691 bytes, XboxAudio): first at 0x0002ea74 `mov ecx,0x7b5dd8`")]
    cen = {r[1]: r for r in ref_census(synth, {}, {})}
    assert cen["0x007b5dfc"][3] == "6 site(s) over 2 bodies, 1 of them XboxAudio", cen["0x007b5dfc"]
    assert cen["0x007b5dd8"][3] == "2 site(s) over 1 body, 1 of them XboxAudio", cen["0x007b5dd8"]

    print("xboxaudio-runtime: self-test ok (pool boundary, gates, pairs, stores, SDK filter, refs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
