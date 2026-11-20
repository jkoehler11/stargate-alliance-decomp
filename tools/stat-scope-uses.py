#!/usr/bin/env python3
"""Which sites index the engine's cycle-scope array with one of the audio module's eight ids.

`XboxAudio_RegisterCounters` (`0x0002e900`) registers eight named counters in group `Audio` and stores
each returned value at `+0x04`..`+0x20` of an embedded block (the device body hands it `lea ecx,[this+0x184]`).
The previous two bundles read those values as opaque *handles* and left "which method increments an audio
counter" open. They are not opaque: the registration returns an **index into the engine's slot array**, the
pointer the registry object holds at `0x7b5dd8 + 0x24` = `0x7b5dfc`, and a counter is accumulated by the
cycle-scope idiom around it:

    mov  eax,[this+0x1a0]        ; one of the eight ids
    mov  ecx,ds:0x7b5dfc         ; the array base
    lea  ecx,[ecx+eax*4]         ; the slot
    rdtsc
    sub  [ecx],eax               ; entry: subtract the start timestamp
    ...
    add  [ecx],eax               ; exit: add the end timestamp (the difference is the elapsed cycles)

So the increment is not a registry method and not a `Core` initializer: the module does it itself, and this
tool counts where. Every absolute reference to `0x7b5dfc` is one site; each site's index source is read out of
the instructions around it (a field of `this`, a global, an immediate or a stack argument), and a field whose
offset matches one of the eight ids is named.

    python3 tools/stat-scope-uses.py --xbe DUMP/default.xbe -o inventory/stat_scope_uses.csv
    python3 tools/stat-scope-uses.py --check     # no dump: the artifact against stat_registry.csv and the map

Addresses, offsets, names the module itself pushes and instruction text: no game data.
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

INVENTORY = os.path.join(ROOT, "inventory")
MAP = os.path.join(ROOT, "symbols", "xbox_functions.csv")
REGISTRY_CSV = os.path.join(INVENTORY, "stat_registry.csv")
MODULES = os.path.join(INVENTORY, "function_modules.csv")
OUT = os.path.join(INVENTORY, "stat_scope_uses.csv")

REGISTRY = 0x007b5dd8        # the engine's stat/cycle-scope object
ARRAY = 0x007b5dfc           # REGISTRY + 0x24: pointer to the slot array, indexed by a registered id
REGISTER = 0x002530f0        # the registration method
AUDIO_BODY = 0x0002e900      # XboxAudio_RegisterCounters
DEVICE_BODY = 0x0002fd70     # XboxAudio_CreateDevice, which passes the block's address in ecx
BLOCK_FALLBACK = 0x184       # `lea ecx,[esi+0x184]` before the registration call

HEADER = ["kind", "site", "body", "body_name", "module", "index_source", "audio_id", "op", "note"]

LEA_INDEX = re.compile(r"^lea\s+\w+\s*,\s*\[\s*\w+\+(e[a-d]x|e[sd]i|e[bp]p)\*4\s*\]")
LEA_INDEX_2 = re.compile(r"^lea\s+\w+\s*,\s*\[\s*(e[a-d]x|e[sd]i|e[bp]p)\*4\+")
LEA_DST = re.compile(r"^lea\s+(\w+)\s*,")
ARRAY_LOAD = re.compile(r"^(mov|lea)\s+\w+\s*,\s*(ds:0x0*7b5dfc|0x0*7b5dfc)$")
FIELD_LOAD = re.compile(r"^(?P<op>mov|movzx|movsx)\s+(?P<dst>\w+)\s*,\s*(?:DWORD PTR )?\[(?P<base>\w+)(?:\+(?P<disp>0x[0-9a-f]+))?\]$")
GLOBAL_LOAD = re.compile(r"^mov\s+(?P<dst>\w+)\s*,\s*(?:ds:)?(?P<va>0x[0-9a-f]+)$")
IMM_LOAD = re.compile(r"^mov\s+(?P<dst>\w+)\s*,\s*(?P<va>0x[0-9a-f]+)$")
ARITH = re.compile(r"^(?P<op>sub|add|mov|inc|dec)\s+(?:\w+\s*,\s*)?DWORD PTR \[(?P<reg>\w+)\](?:\s*,\s*(?P<src>.+))?$")
JUMP = re.compile(r"^jmp\s+(0x[0-9a-f]+)$")


def read_map():
    rows = []
    with open(MAP, newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append((int(row["address"], 16), row["name"], int(row["size"] or 0), row["module"].strip()))
    return sorted(rows)


def modules():
    out = {}
    with open(MODULES, newline="") as fh:
        for row in csv.DictReader(fh):
            out[int(row["address"], 16)] = row["module"].strip()
    return out


def objdump(blob, base):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
        fh.write(blob)
        temp = fh.name
    try:
        out = subprocess.run(["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                              "--adjust-vma=%d" % base, temp],
                             capture_output=True, text=True).stdout
    finally:
        os.unlink(temp)
    insns = []
    for line in out.splitlines():
        if line.count("\t") < 2:
            continue
        head, rest = line.split("\t", 1)
        try:
            va = int(head.strip().rstrip(":"), 16)
        except ValueError:
            continue
        text = re.sub(r"\s+", " ", rest.split("\t")[-1].strip())
        if text and not text.startswith("."):
            insns.append((va, text))
    insns.sort()
    return insns


def disasm(xbe, va, size):
    return objdump(xbe.read_va(va, size), va)


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


def covering(insns, site):
    i = None
    for j, (va, _t) in enumerate(insns):
        if va <= site:
            i = j
        else:
            break
    return i


def index_source(insns, i):
    """Classify the register that indexes the array, from the instructions around one array reference.

    Returns (source, idx_reg) where `source` is `field +0xNN of <base>`, `global 0xNNNN`, `immediate 0xNN`,
    `stack +0xNN`, `argument` or `unread`.
    """
    reg = None
    lea_at = None
    # prefer a `lea` after the array load (the usual `load base, load index, lea` order); the merged
    # streaming/regular tail puts a second `lea` from the other branch just before this one
    for j in list(range(i, min(len(insns), i + 6))) + list(range(i - 1, max(-1, i - 5), -1)):
        m = LEA_INDEX.match(insns[j][1]) or LEA_INDEX_2.match(insns[j][1])
        if m:
            reg, lea_at = m.group(1), j
            break
    if reg is None:
        return "unread", None, None
    for j in range(lea_at - 1, max(-1, lea_at - 9), -1):
        text = insns[j][1]
        m = re.match(r"^mov\s+(%s)\s*,\s*(.+)$" % re.escape(reg), text)
        if not m:
            continue
        src = m.group(2)
        f = FIELD_LOAD.match(text)
        if f:
            base = f.group("base")
            disp = f.group("disp") or "0x0"
            return "field %s of %s" % (disp, base), reg, (LEA_DST.match(insns[lea_at][1]).group(1) if LEA_DST.match(insns[lea_at][1]) else None)
        if src.startswith("ds:") or re.match(r"^0x[0-9a-f]{6,}$", src):
            va = int(src.replace("ds:", ""), 16)
            return "global 0x%08x" % va, reg, (LEA_DST.match(insns[lea_at][1]).group(1) if LEA_DST.match(insns[lea_at][1]) else None)
        if re.match(r"^0x[0-9a-f]{1,4}$", src):
            return "immediate %s" % src, reg, (LEA_DST.match(insns[lea_at][1]).group(1) if LEA_DST.match(insns[lea_at][1]) else None)
        if src.startswith("esp"):
            m2 = re.match(r"esp\+(0x[0-9a-f]+)", src)
            return "stack %s" % (m2.group(1) if m2 else "esp"), reg, None
        return "register %s" % src, reg, None
    return "unread", reg, None


def operation(insns, i, idx_reg=None, slot_reg=None):
    """What the site does with the slot, in the shapes the compiler emits for a profiled counter.

    The slot is never arithmetically updated in place for a cycle scope: the code loads the slot into a
    register, adds the `rdtsc` value (with a fixed `-12`), and stores it back — and the two streaming
    counters share one merged tail (`mov ecx,[eax]; inc ecx; mov [eax],ecx`) that both branches `jmp` into.
    So the classifier reads the whole block after the `lea`, follows one `jmp`, and names the shape:
    `sub tsc` (scope opened), `add tsc` (scope closed), `inc by 1` (a plain counter), `add`/`sub`, or
    `read only` when the site only samples the slot.
    """
    def block(start, hops=1):
        return insns[start:min(len(insns), start + 40)]

    win = block(i)
    texts = [t for _v, t in win]
    # registers that carry a timestamp: the tsc low dword is cached in a global and reloaded
    tsc_regs = set()
    for t in texts:
        m = re.match(r"^mov\s+(\w+)\s*,\s*(?:DWORD PTR )?(?:ds:)?0x0*5d65e8$", t)
        if m:
            tsc_regs.add(m.group(1))
    slot_read = set()
    if slot_reg:
        for t in texts:
            m = re.match(r"^mov\s+(\w+)\s*,\s*DWORD PTR \[%s\]$" % re.escape(slot_reg), t)
            if m:
                slot_read.add(m.group(1))
    has_tsc = any("rdtsc" in t for t in texts) or bool(tsc_regs)
    # scope opened: the timestamp is subtracted straight out of the slot
    for t in texts:
        m = re.match(r"^sub\s+DWORD PTR \[%s\],(\w+)$" % (re.escape(slot_reg) if slot_reg else r"\w+"), t)
        if m and (m.group(1) in tsc_regs or has_tsc):
            return "sub tsc"
    # the timestamp in a register: added (scope closed) or subtracted (scope opened) into the slot's value
    for t in texts:
        m = re.match(r"^(add|sub)\s+(\w+),\s*(\w+)$", t)
        if m and m.group(2) in slot_read and m.group(3) in tsc_regs:
            return "%s tsc" % m.group(1)
    # a plain counter, often behind a merged tail
    for j, t in enumerate(texts):
        m = re.match(r"^(inc|dec)\s+(\w+)$", t)
        if m:
            reg = m.group(2)
            stored = any(re.match(r"^mov\s+DWORD PTR \[%s\],%s$" % (re.escape(slot_reg or ""), re.escape(reg)), x)
                         or re.match(r"^mov\s+DWORD PTR \[%s\],%s$" % (re.escape(slot_reg or ""), re.escape(reg)),
                                     x) for x in texts[j + 1:j + 4])
            if reg in slot_read or stored or reg == slot_reg:
                return "%s by 1" % m.group(1)
    for t in texts:
        m = re.match(r"^(add|sub)\s+DWORD PTR \[%s\],\s*0x1$" % (re.escape(slot_reg) if slot_reg else r"\w+"), t)
        if m:
            return "%s by 1" % m.group(1)
    jm = [j for j, t in enumerate(texts) if JUMP.match(t)]
    if jm and i + jm[0] < len(insns) - 1:
        target = int(JUMP.match(texts[jm[0]]).group(1), 16)
        for k, (va, _t) in enumerate(insns):
            if va == target:
                return operation(insns, k, idx_reg, slot_reg)
    return "read only"


def audio_ids(xbe, funcs):
    """The eight registrations, as {offset in the block: name} with the block base read from the device body."""
    rows = list(csv.DictReader(open(REGISTRY_CSV, newline="")))
    regs = [r for r in rows if r["kind"] == "registration" and r["body"].lower() == "0x%08x" % AUDIO_BODY]
    block = BLOCK_FALLBACK
    device = next((f for f in funcs if f[0] == DEVICE_BODY), None)
    if device:
        insns = disasm(xbe, device[0], device[2])
        for i, (va, text) in enumerate(insns):
            if re.match(r"^call\s+0x0*%x$" % AUDIO_BODY, text):
                for back in range(i - 1, max(-1, i - 4), -1):
                    m = re.match(r"^lea\s+ecx\s*,\s*\[e\w+\+(0x[0-9a-f]+)\]$", insns[back][1])
                    if m:
                        block = int(m.group(1), 16)
                        break
    out = {}
    for r in regs:
        m = re.match(r"e\w+\+(0x[0-9a-f]+)$", r["store"].strip())
        if not m:
            continue
        out[block + int(m.group(1), 16)] = r["name"]
    return out, block


def build(args):
    from xbe import Xbe  # noqa: PLC0415

    xbe = Xbe(args.xbe)
    funcs = read_map()
    mods = modules()
    ids, block = audio_ids(xbe, funcs)
    if len(ids) != 8:
        raise SystemExit("expected the eight audio registrations in %s, read %d" % (REGISTRY_CSV, len(ids)))

    bases = [f[0] for f in funcs]
    rows = []
    loose = []
    per_id = {off: [] for off in ids}
    bodies = set()
    for site in dword_refs(xbe, ARRAY):
        j = None
        for k, b in enumerate(bases):
            if b <= site < b + funcs[k][2]:
                j = k
                break
        if j is None:
            loose.append(site)
            continue
        base, name, size, _m = funcs[j]
        insns = disasm(xbe, base, size)
        i = covering(insns, site)
        if i is None:
            loose.append(site)
            continue
        source, idx_reg, slot_reg = index_source(insns, i)
        op = operation(insns, i, idx_reg, slot_reg)
        hit = None
        m = re.match(r"^field (0x[0-9a-f]+) of ", source)
        if m:
            off = int(m.group(1), 16)
            if off in ids:
                hit = off
        rows.append({
            "kind": "scope-site", "site": "0x%08x" % site, "body": "0x%08x" % base, "body_name": name,
            "module": mods.get(base, "(unnamed)"), "index_source": source,
            "audio_id": "%s (+0x%02x)" % (ids[hit], hit) if hit is not None else "",
            "op": op, "note": "",
        })
        bodies.add(base)
        if hit is not None:
            per_id[hit].append((base, name, site))

    for off in sorted(ids):
        hits = per_id[off]
        rows.append({
            "kind": "id", "site": "0x%08x" % off, "body": "", "body_name": ids[off],
            "module": "", "index_source": "%d site(s)" % len(hits),
            "audio_id": ids[off], "op": "used" if hits else "never indexed",
            "note": "" if hits else "registered, and no site in the image indexes the array with it",
        })
    total = len([r for r in rows if r["kind"] == "scope-site"])
    rows.append({
        "kind": "census", "site": "0x%08x" % ARRAY, "body": "",
        "body_name": "%d site(s) over %d bod%s" % (total, len(bodies), "y" if len(bodies) == 1 else "ies"),
        "module": "", "index_source": "%d id(s) used of %d" % (sum(1 for o in per_id if per_id[o]), len(ids)),
        "audio_id": "", "op": "",
        "note": "%d reference(s) total, %d with no body, %d with no resolved index source; "
                "block base +0x%02x read from the device body"
                % (total + len(loose), len(loose), sum(1 for r in rows if r["kind"] == "scope-site"
                                                         and r["index_source"] == "unread"), block),
    })
    return rows


def write(rows, path):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def check(path):
    spans = {}
    for va, name, size, _m in read_map():
        spans.setdefault(name, []).append((va, size))
    regs = [r for r in csv.DictReader(open(REGISTRY_CSV, newline=""))
            if r["kind"] == "registration" and r["body"].lower() == "0x%08x" % AUDIO_BODY]
    want = {}
    for r in regs:
        m = re.match(r"e\w+\+(0x[0-9a-f]+)$", r["store"].strip())
        if m:
            want[BLOCK_FALLBACK + int(m.group(1), 16)] = r["name"]
    rows = list(csv.DictReader(open(path, newline="")))
    bad = []
    if len(want) != 8:
        bad.append("stat_registry.csv holds %d audio store offsets, expected 8" % len(want))
    ids = [r for r in rows if r["kind"] == "id"]
    if len(ids) != 8:
        bad.append("%d id rows, expected 8" % len(ids))
    seen = {}
    for r in ids:
        off = int(r["site"], 16)
        seen[off] = int(r["index_source"].split()[0])
        if want.get(off) != r["audio_id"]:
            bad.append("id 0x%08x is %r in the artifact and %r in stat_registry.csv"
                       % (off, r["audio_id"], want.get(off)))
        if r["body_name"] != r["audio_id"]:
            bad.append("id 0x%08x: name column %r does not match audio_id %r" % (off, r["body_name"], r["audio_id"]))
    if set(seen) != set(want):
        bad.append("id offsets differ from stat_registry.csv: %s"
                   % sorted("0x%02x" % o for o in set(seen) ^ set(want)))
    sites = [r for r in rows if r["kind"] == "scope-site"]
    for off, n in seen.items():
        got = len([r for r in sites if r["audio_id"].endswith("(+0x%02x)" % off)])
        if got != n:
            bad.append("id 0x%02x: %d site rows, id row says %d" % (off, got, n))
    for r in sites:
        if r["body_name"] not in spans:
            bad.append("%s: %s is not in the symbol map" % (r["body"], r["body_name"]))
            continue
        base = int(r["body"], 16)
        size = dict(spans[r["body_name"]]).get(base)
        if size is None:
            bad.append("%s: %s is not a start of %s" % (r["body"], r["body"], r["body_name"]))
            continue
        if not base <= int(r["site"], 16) < base + size:
            bad.append("%s: site %s is outside the body" % (r["body"], r["site"]))
        if not r["index_source"]:
            bad.append("%s: site %s carries no index source" % (r["body"], r["site"]))
    census = [r for r in rows if r["kind"] == "census"]
    if len(census) != 1:
        bad.append("%d census rows, expected 1" % len(census))
    else:
        note = census[0]["note"]
        m = re.match(r"^(\d+) reference\(s\) total, (\d+) with no body", note)
        if not m:
            bad.append("census note does not start with the two counts: %r" % note)
        else:
            total, loose = int(m.group(1)), int(m.group(2))
            if total != len(sites) + loose:
                bad.append("census says %d reference(s), the artifact lists %d sites and %d body-less"
                           % (total, len(sites), loose))
        used = sum(1 for off, n in seen.items() if n)
        if "%d id(s) used" % used not in census[0]["index_source"]:
            bad.append("census says %r, the id rows say %d used" % (census[0]["index_source"], used))
    if bad:
        for b in bad:
            print("FAIL stat-scope-uses: %s" % b)
        return 1
    print("ok stat-scope-uses: %d scope site(s), 8 ids, %d used, %d never indexed"
          % (len(sites), sum(1 for n in seen.values() if n), sum(1 for n in seen.values() if not n)))
    return 0


def selftest():
    """The index-source parser over synthetic instruction rows: no dump, no writes."""
    cases = [
        (["mov eax,DWORD PTR [esi+0x1a0]", "mov ecx,DWORD PTR ds:0x7b5dfc", "lea eax,[ecx+eax*4]"],
         2, "field 0x1a0 of esi"),
        (["mov eax,DWORD PTR [ebp+0x18c]", "mov ecx,DWORD PTR ds:0x7b5dfc", "lea ecx,[ecx+eax*4]"],
         2, "field 0x18c of ebp"),
        (["mov eax,ds:0x7b6090", "mov edx,DWORD PTR ds:0x7b5dfc", "lea ecx,[edx+eax*4]"],
         2, "global 0x007b6090"),
        (["mov eax,0x3", "mov ecx,DWORD PTR ds:0x7b5dfc", "lea ecx,[ecx+eax*4]"],
         2, "immediate 0x3"),
    ]
    bad = 0
    for texts, i, want in cases:
        insns = [(0x1000 + 4 * k, t) for k, t in enumerate(texts)]
        got, _reg, _slot = index_source(insns, i)
        if got != want:
            print("FAIL stat-scope-uses selftest: %r -> %r, expected %r" % (texts, got, want))
            bad += 1
    # the operation classifier: an add/sub after the `lea` that follows an `rdtsc` is a timestamp scope
    ops = [("mov ecx,ds:0x7b5dfc", "lea ecx,[ecx+eax*4]", "rdtsc", "sub DWORD PTR [ecx],eax")]
    insns = [(0x2000 + 4 * k, t) for k, t in enumerate(ops[0])]
    got = operation(insns, 1, "eax", "ecx")
    if got != "sub tsc":
        print("FAIL stat-scope-uses selftest: %r -> %r, expected 'sub tsc'" % (ops[0], got))
        bad += 1
    plain = [("mov ecx,ds:0x7b5dfc", "lea ecx,[ecx+eax*4]", "mov ecx,DWORD PTR [ecx]")]
    got = operation([(0x3000 + 4 * k, t) for k, t in enumerate(plain[0])], 1, "eax", "ecx")
    if got != "read only":
        print("FAIL stat-scope-uses selftest: %r -> %r, expected 'read only'" % (plain[0], got))
        bad += 1
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out", default=OUT)
    ap.add_argument("--check", nargs="?", const=OUT, help="validate the artifact without the dump")
    ap.add_argument("--selftest", action="store_true", help="parser only, no dump and no writes")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.check:
        return check(args.check)
    if not args.xbe:
        ap.error("--xbe is required unless --check or --selftest is given")
    rows = build(args)
    write(rows, args.out)
    for r in rows:
        if r["kind"] == "id":
            print("%-22s +0x%-4s %-52s %s" % (r["audio_id"], r["site"][2:], r["index_source"], r["op"]))
        elif r["kind"] == "census":
            print("%-22s %s %s" % ("census", r["body_name"], r["index_source"]))
        elif r["audio_id"]:
            print("%s %-24s %-22s %s" % (r["site"], r["body_name"], r["audio_id"], r["op"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())