#!/usr/bin/env python3
"""The four xvoice voice-mode classes: their vtables, and how each opens its own device.

`docs/systems/AUDIO.md` closes the poll at `XboxAudio_PollDeviceChanges`, which asks
`_XGetDeviceChanges@12` twice and starts or stops a channel -- but the four channels are devices of the
XHV voice engine, and the engine's modes were the last audio classes with no reading. Their methods are
library code matched into the image by `tools/native-method-notes.py` (CVoiceChatMode 23,
CVoiceMailMode 12, CLoopbackMode 4, CBaseMode 4, CInactiveMode 1), and their classes are C++ ones, so
the dispatch is a real vtable and not a UE2 class table:

    # the four tables are contiguous in .rdata, 27 slots each
    table 00540570  27 slots  CInactiveMode   (slots 2,5,10,11,12 = CBaseMode/CInactiveMode bodies)
    table 005405e0  27 slots  CVoiceMailMode
    table 00540658  27 slots  CVoiceChatMode
    table 005406c8  27 slots  CLoopbackMode

A run of consecutive dwords that each point into a code section is a candidate table; the class whose
named methods occupy the most slots owns it, and a slot whose target is another mode class's body is
inheritance, which is what makes the base table legible. `DSoundInitialize` is declared `QAE` --
public, *not* virtual -- so it is absent from every table and is reached by a direct call from the
mode's own `AdditionalInitialize` (a `E8 rel32` scan, not a dword scan: library bodies are not dword
addresses anywhere in the image).

Each table row also records the three instructions before the mode's `_DirectSoundCreate` call, so
"does a mode pick a device?" is answered by the code and not by the displacement.

    python3 tools/xvoice-modes.py --xbe DUMP/default.xbe -o inventory/xvoice_mode_vtables.csv
    python3 tools/xvoice-modes.py --check      # no dump: the committed artifact against the map

Addresses, slot numbers and names only: nothing here carries game data.
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

MAP = os.path.join(ROOT, "symbols", "xbox_functions.csv")
OUT = os.path.join(ROOT, "inventory", "xvoice_mode_vtables.csv")

CLASSES = ["CBaseMode", "CInactiveMode", "CVoiceChatMode", "CVoiceMailMode", "CLoopbackMode"]
# the four tables belong to the concrete modes: CBaseMode is only ever inherited from, so it has none
CONCRETE = ["CInactiveMode", "CVoiceChatMode", "CVoiceMailMode", "CLoopbackMode"]
CODE_SECTIONS = (".text", "XNET", "XONLINE", "D3D", "D3DX", "XGRPH", "DSOUND", "XPP")
DATA_SECTIONS = (".rdata", ".data", "DOLBY", ".data1")
DSOUND_CREATE = "_DirectSoundCreate@12"

HEADER = ["kind", "class", "table", "slots", "object", "object_span", "installer", "installer_va",
          "additional_init", "dsound_body", "dsound_size", "dsound_caller", "create_setup", "slot",
          "target", "target_name", "target_class", "shared_with_base", "note"]


def read_map():
    rows = []
    with open(MAP, newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append((int(row["address"], 16), row["name"], int(row["size"] or 0), row["module"].strip()))
    rows.sort()
    return rows


def class_of(name):
    m = re.search(r"@(C[A-Za-z]+)@XHVNamespace@@", name)
    return m.group(1) if m else ""


def method_of(name):
    if not name.startswith("?"):
        return ""
    return name[1:].split("@", 1)[0]


def virtual_of(name):
    """MSVC 'U' in the second field says virtual; 'Q' says public non-virtual."""
    m = re.search(r"@@([A-Z])([A-Z])E", name)
    return bool(m and m.group(1) == "U")


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


def rel32_callers(xbe, code_secs, target):
    """(site_va, caller_va) for every `E8 rel32` that lands on `target`."""
    want = target - 5
    hits = []
    for sec in code_secs:
        blob = xbe.read_va(sec.va, min(sec.vsize, sec.fsize))
        start = 0
        while True:
            i = blob.find(b"\xe8", start)
            if i < 0 or i + 5 > len(blob):
                break
            rel = struct.unpack_from("<i", blob, i + 1)[0]
            if sec.va + i + rel == want:
                hits.append(sec.va + i)
            start = i + 1
    return hits


def build(args):
    from xbe import Xbe  # noqa: PLC0415

    xbe = Xbe(args.xbe)
    table = read_map()
    by_va = {va: (name, size, module) for va, name, size, module in table}
    code_secs = [s for s in xbe.sections if s.name in CODE_SECTIONS]
    data_secs = [s for s in xbe.sections if s.name in DATA_SECTIONS]

    def in_code(va):
        return any(s.va <= va < s.va + min(s.vsize, s.fsize) for s in code_secs)

    methods = {cls: [] for cls in CLASSES}
    for va, name, size, module in table:
        cls = class_of(name)
        if cls in methods:
            methods[cls].append((va, name))
    addr = {va: (class_of(n), n) for cls, rows in methods.items() for va, n in rows}

    # ---- candidate tables: runs of consecutive code pointers in a data section
    tables = {}
    for sec in data_secs:
        blob = xbe.read_va(sec.va, min(sec.vsize, sec.fsize))
        run = []
        for off in range(0, len(blob) - 3, 4):
            d = struct.unpack_from("<I", blob, off)[0]
            if in_code(d):
                run.append((sec.va + off, d))
                continue
            if len(run) >= 4:
                counts = {}
                for _a, d2 in run:
                    if d2 in addr:
                            counts.setdefault(addr[d2][0], []).append(d2)
                if counts:
                    # every concrete mode overrides ValidateSet; the base class leaves it to a shared
                    # stub, so the class whose ValidateSet is installed owns the table
                    val = [c for c in counts
                           if any(method_of(n) == "ValidateSet" for va, n in methods[c]
                                  if va in {d for _a, d in run})]
                    owner = val[0] if len(val) == 1 else max(counts, key=lambda c: (len(counts[c]), c))
                    if owner not in tables:
                        tables[owner] = run
            run = []

    rows = []
    objects = {}
    base_run = tables.get("CInactiveMode", [])
    base_targets = {d for _a, d in base_run}
    for cls in CONCRETE:
        run = tables.get(cls)
        if not run:
            print("no table for %s" % cls, file=sys.stderr)
            continue
        table_va = run[0][0]

        goal = dict(methods[cls])
        add_init = next((va for va, n in goal.items() if method_of(n) == "AdditionalInitialize"), 0)
        dsound = next((va for va, n in goal.items() if method_of(n) == "DSoundInitialize"), 0)

        # the static object whose first dword is the table (the modes are singletons in .data)
        obj = 0
        for sec in data_secs:
            blob = xbe.read_va(sec.va, min(sec.vsize, sec.fsize))
            i = blob.find(struct.pack("<I", table_va))
            if i >= 0 and sec.va + i != table_va:
                obj = sec.va + i
                break
        if obj:
            objects[cls] = obj

        # installer: the code dword that spells the table's address
        installer = ""
        installer_va = 0
        pat = struct.pack("<I", table_va)
        for sec in code_secs:
            blob = xbe.read_va(sec.va, min(sec.vsize, sec.fsize))
            i = blob.find(pat)
            if i < 0:
                continue
            at = sec.va + i
            installer_va = at
            for va, name, size, module in table:
                if va <= at < va + size:
                    installer = name
                    installer_va = va
                    break
            break

        # the caller of the non-virtual DSoundInitialize, and its create call's setup
        caller = ""
        dsound_size = 0
        create_setup = ""
        if dsound:
            dsound_size = by_va.get(dsound, ("", 0, ""))[1]
            sites = rel32_callers(xbe, code_secs, dsound)
            if sites:
                site = sites[0]
                for va, name, size, module in table:
                    if va <= site < va + size:
                        cls_c = class_of(name)
                        caller = "%s::%s" % (cls_c, method_of(name)) if cls_c else name
                        break
            insns = objdump(xbe.read_va(dsound, dsound_size), dsound)
            thunk = None
            for va, name, size, module in table:
                if name.startswith(DSOUND_CREATE):
                    thunk = va
                    break
            for i, (va, text) in enumerate(insns):
                m = re.search(r"call\s+0x([0-9a-f]+)$", text)
                if m and thunk and int(m.group(1), 16) == thunk:
                    window = [t for _v, t in insns[max(0, i - 10):i]
                              if t.startswith("push") or t.startswith("lea") or t.startswith("xor eax,eax")]
                    prev = window[-4:]
                    create_setup = " ; ".join(prev)
                    break

        rows.append({
            "kind": "table", "class": cls, "table": "0x%08x" % table_va, "slots": len(run),
            "object": "0x%08x" % obj if obj else "", "object_span": "",
            "installer": installer, "installer_va": "0x%08x" % installer_va if installer_va else "",
            "additional_init": "0x%08x" % add_init if add_init else "",
            "dsound_body": "0x%08x" % dsound if dsound else "",
            "dsound_size": dsound_size, "dsound_caller": caller, "create_setup": create_setup,
            "slot": "", "target": "", "target_name": "", "target_class": "",
            "shared_with_base": "",
            "note": "%d named slots%s" % (sum(1 for _a, d in run if d in addr),
                                          "" if installer else "; object statically initialised, no code dword spells the table"),
        })
        for i, (_a, d2) in enumerate(run):
            tcls, tname = addr.get(d2, ("", ""))
            rows.append({
                "kind": "slot", "class": cls, "table": "0x%08x" % table_va, "slots": len(run),
                "object": "", "object_span": "",
                "installer": "", "installer_va": "", "additional_init": "", "dsound_body": "",
                "dsound_size": "", "dsound_caller": "", "create_setup": "",
                "slot": i, "target": "0x%08x" % d2,
                "target_name": method_of(tname) if tname else "",
                "target_class": tcls,
                "shared_with_base": "yes" if (cls != "CInactiveMode" and i < len(base_run)
                                                and base_run[i][1] == d2) else "",
                "note": "" if tname else "unnamed in the map",
            })
    # span: how far the next mode object starts -- the mode's own size in .data
    order = sorted(objects.items(), key=lambda kv: kv[1])
    span = {}
    for i, (cls, va) in enumerate(order):
        if i + 1 < len(order):
            span[cls] = order[i + 1][1] - va
    for r in rows:
        if r["kind"] == "table" and r["class"] in span:
            r["object_span"] = "0x%x" % span[r["class"]]
    return rows


def write(rows, path):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def check(path):
    """No dump: the artifact must agree with the map and with itself."""
    table = read_map()
    by_name = {n: (va, size) for va, n, size, _m in table}
    rows = list(csv.DictReader(open(path, newline="")))
    methods_named = {}
    for va, name, _size, _m in table:
        cls = class_of(name)
        if cls in CLASSES:
            methods_named[(cls, method_of(name))] = va
    bad = []
    table_rows = [r for r in rows if r["kind"] == "table"]
    if sorted(r["class"] for r in table_rows) != sorted(CONCRETE):
        bad.append("table rows name %s, expected %s" % ([r["class"] for r in table_rows], CONCRETE))
    for r in table_rows:
        slots = int(r["slots"])
        if slots < 20 or slots > 40:
            bad.append("%s: %d slots is not a mode vtable" % (r["class"], slots))
        if r["installer"] and r["installer"] not in by_name:
            bad.append("%s: installer %s not in the map" % (r["class"], r["installer"]))
        if r["dsound_caller"]:
            parts = r["dsound_caller"].split("::")
            if len(parts) != 2 or (parts[0], parts[1]) not in methods_named:
                bad.append("%s: DSoundInitialize caller %s is not a mode method" % (r["class"], r["dsound_caller"]))
    for r in table_rows:
        slots = [x for x in rows if x["kind"] == "slot" and x["class"] == r["class"]]
        if [int(x["slot"]) for x in slots] != list(range(int(r["slots"]))):
            bad.append("%s: slots are not 0..%d" % (r["class"], int(r["slots"]) - 1))
        for x in slots:
            if x["target_name"]:
                key = (x["target_class"], x["target_name"])
                if key not in methods_named:
                    bad.append("%s slot %s: %s::%s is not a mode method"
                               % (r["class"], x["slot"], x["target_class"], x["target_name"]))
                elif methods_named[key] != int(x["target"], 16):
                    bad.append("%s slot %s: %s::%s is at 0x%08x"
                               % (r["class"], x["slot"], x["target_class"], x["target_name"],
                                  methods_named[key]))
    if bad:
        for b in bad:
            print("FAIL xvoice-modes: %s" % b)
        return 1
    print("ok xvoice-modes: %d tables, %d slots, every named slot resolves in the map"
          % (len(table_rows), len([r for r in rows if r["kind"] == "slot"])))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out", default=OUT)
    ap.add_argument("--check", nargs="?", const=OUT, help="validate an artifact without the dump")
    args = ap.parse_args()
    if args.check:
        return check(args.check)
    if not args.xbe:
        ap.error("--xbe is required unless --check is given")
    rows = build(args)
    write(rows, args.out)
    tables = [r for r in rows if r["kind"] == "table"]
    print("wrote %s: %d tables, %d slot rows" % (args.out, len(tables), len(rows) - len(tables)))
    for r in tables:
        print("  %-14s table %s %2s slots  installer %-40s dsound %s <- %s"
              % (r["class"], r["table"], r["slots"], r["installer"] or "-", r["dsound_body"], r["dsound_caller"] or "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
