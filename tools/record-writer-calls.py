#!/usr/bin/env python3
"""Who calls the record writers, and which member each call is handed.

`docs/NATIVE_VM.md` reads the class record's 20-byte middle as a second array of `FUN_0003d900`'s 12-byte element
grammar, and the reader `0x0003a800` calls that writer **once** -- so a second array of the same grammar would have to
be packed by some other body. A call site is the witness, and its argument is the member: this tool scans the image's
executable sections for `E8 rel32` whose target is one of the two record writers, then decodes **backwards** to the
instruction that last wrote the register it pushes.

* `FUN_0003d900` -- 12-byte records (one object reference plus two dwords), and
* `FUN_0003d160` -- a compact count and that many one- or two-byte pairs.

The argument's writer is classified, not guessed: a `LEA reg,[base+disp]` is the member at that displacement, a
`CALL` means the callee's return value is passed on (the reader chains the two writers), and anything else is
reported as itself. A site counts only when a forward decode from a start offset in the window behind it lands
exactly on the call with a chain of instruction ends behind it -- the boundary rule `tools/vcall-receiver.py`
established, because a bare `E8 3d 09 03 00` also occurs inside displacements and immediates and a raw byte scan
therefore over-counts.

    python3 tools/record-writer-calls.py --image $SG1_DUMP/default.xbe -o inventory
    python3 tools/record-writer-calls.py --report
    python3 tools/record-writer-calls.py --check        # synthetic bytes, no dump

Reads the image and `symbols/xbox_functions.csv`; no boot, no Ghidra round, no decompiled body.
"""
import argparse
import collections
import csv
import importlib.util
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from xbe import Xbe  # noqa: E402

ROOT = os.path.dirname(HERE)
ARTIFACT = "record_writer_calls.csv"
WRITERS = {0x3D900: "record", 0x3D160: "pairs"}
WINDOW = 48                              # how far behind a call a start offset is tried
MIN_CHAIN = 2                            # a one-instruction "chain" proves nothing
MAX_ARGS = 3                             # pushes inspected as the call's arguments
COLUMNS = ["writer", "target", "site", "function", "boundary", "arg", "arg_kind", "member", "member_hex",
           "writer_insn", "pushes"]


def decoder():
    """`tools/vcall-receiver.py` is the project's 32-bit length decoder; import it without duplicating it."""
    spec = importlib.util.spec_from_file_location("sg1_vcall_receiver", os.path.join(HERE, "vcall-receiver.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def candidates(blob, base_va):
    """Every `E8 rel32` in the blob, as (site_va, target_va, file_offset)."""
    out = []
    for i in range(0, len(blob) - 4):
        if blob[i] != 0xE8:
            continue
        site = base_va + i
        rel = struct.unpack_from("<i", blob, i + 1)[0]
        out.append((site, (site + 5 + rel) & 0xFFFFFFFF, i))
    return out


def chain(blob, site_off, vr):
    """The instruction run that ends exactly on the call site, or None.

    The window is tried from its far end inwards, so the longest clean decode wins: a short chain that happens to
    land on the site is what a wrong-length decode of the same bytes produces.
    """
    for back in range(WINDOW, 5, -1):
        start = site_off - back
        if start < 0:
            continue
        insns, i = [], start
        while i < site_off:
            insn = vr.decode(blob, i)
            if insn is None or insn.length <= 0:
                break
            insns.append((i, insn))
            i += insn.length
        if i == site_off and len(insns) >= MIN_CHAIN:
            return insns
    return None


def pushed_regs(insns):
    """The registers pushed immediately before the call, closest first."""
    out = []
    for _off, insn in reversed(insns):
        if insn.text.startswith("push "):
            out.append(insn.text.split()[1])
            if len(out) == MAX_ARGS:
                break
        elif out:
            break
    return out


def call_target(off, insn, base_va):
    """A `rel` call's absolute target, from the raw immediate the decoder printed."""
    try:
        rel = int(insn.text.rsplit(" ", 1)[-1], 16)
    except ValueError:
        return base_va + off
    if rel >= (1 << 31):
        rel -= 1 << 32
    return (base_va + off + insn.length + rel) & 0xFFFFFFFF


def writer_of(insns, reg, base_va):
    """What last wrote `reg` before the call: (kind, member, text)."""
    for off, insn in reversed(insns):
        if reg not in (insn.dests or []):
            continue
        if insn.kind and insn.kind.startswith("lea") and insn.disp is not None:
            return "member", insn.disp & 0xFFFFFFFF, insn.text
        if insn.kind == "call":
            return "call-result", None, "call 0x%08x" % call_target(off, insn, base_va)
        return "other", None, insn.text
    return "no-writer", None, ""


def arg_of(insns, base_va):
    """The pushed argument that is a member load, else the first argument's classification."""
    first = None
    for reg in pushed_regs(insns):
        kind, member, text = writer_of(insns, reg, base_va)
        if kind == "member":
            return reg, kind, member, text
        if first is None and kind != "no-writer":
            first = (reg, kind, member, text)
    return first if first else ("", "none", None, "")


def read_functions(path=None):
    """(address, size, name) for every named function, so a call site gets its owner."""
    path = path or os.path.join(ROOT, "symbols", "xbox_functions.csv")
    out = []
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                out.append((int(r["address"], 16), int(r["size"] or 0), r["name"]))
            except (KeyError, ValueError):
                continue
    out.sort()
    return out


def owner(functions, va):
    best = ""
    for addr, size, name in functions:
        if addr > va:
            break
        if size and va < addr + size:
            best = name
    return best


def measure(image, functions, vr):
    xbe = Xbe(image)
    rows = []
    for sec in xbe.sections:
        if not sec.executable or sec.fsize <= 0:
            continue
        blob = xbe.data[sec.foff:sec.foff + sec.fsize]
        for site, target, off in candidates(blob, sec.va):
            writer = WRITERS.get(target)
            if not writer:
                continue
            insns = chain(blob, off, vr)
            arg, kind, member, text = arg_of(insns, sec.va) if insns else ("", "none", None, "")
            rows.append({"writer": writer, "target": "0x%08x" % target, "site": "0x%08x" % site,
                         "function": owner(functions, site), "boundary": int(bool(insns)),
                         "arg": arg, "arg_kind": kind,
                         "member": "" if member is None else member,
                         "member_hex": "" if member is None else "0x%x" % member,
                         "writer_insn": text,
                         "pushes": " | ".join(reversed(pushed_regs(insns))) if insns else ""})
    rows.sort(key=lambda r: (r["writer"], r["site"]))
    return rows


def report(rows):
    for r in rows:
        if r["boundary"] not in ("", None):
            r["boundary"] = int(r["boundary"])
        if r["member"] not in ("", None):
            r["member"] = int(r["member"])
    print("call sites into the two record writers: %d" % len(rows))
    for writer in ("record", "pairs"):
        mine = [r for r in rows if r["writer"] == writer]
        proven = [r for r in mine if r["boundary"]]
        print("  %s: %d sites, %d with an instruction chain landing on the call"
              % (writer, len(mine), len(proven)))
        if proven:
            print("    arguments: %s" % collections.Counter(r["arg_kind"] for r in proven).most_common(4))
            print("    members: %s"
                  % collections.Counter(r["member_hex"] for r in proven if r["member_hex"]).most_common(8))
        for r in proven:
            print("    %s in %s: %s pushed, %s -> member %s (%s) [%s]"
                  % (r["site"], r["function"] or "?", r["arg"] or "-", r["arg_kind"],
                     r["member_hex"] or "-", r["writer_insn"] or "-", r["pushes"] or "-"))


def sweep(args):
    vr = decoder()
    rows = measure(args.image, read_functions(), vr)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
        print("wrote %s: %d rows" % (path, len(rows)))
    report(rows)
    return 0


def self_check():
    """Synthetic bodies: one call handed a member, and one handed the previous call's result."""
    vr = decoder()
    base, target = 0x1000, 0x3000
    body = bytearray(b"\x90" * 0x200)
    site_off = 0x120
    body[0x118:0x11e] = b"\x8d\x86\xd0\x04\x00\x00"    # lea eax,[esi+0x4d0]
    body[0x11e] = 0x50                                  # push eax
    body[0x11f] = 0x55                                  # push ebp
    site = base + site_off
    body[site_off] = 0xE8
    struct.pack_into("<i", body, site_off + 1, target - (site + 5))
    found = [(s, t, o) for s, t, o in candidates(bytes(body), base) if t == target]
    assert len(found) == 1 and found[0][2] == site_off, found
    insns = chain(bytes(body), site_off, vr)
    assert insns, "no chain lands on the call"
    arg, kind, member, text = arg_of(insns, base)
    assert kind == "member" and member == 0x4D0 and arg == "eax", (arg, kind, member, text)
    assert "0x4d0" in text, text
    assert pushed_regs(insns) == ["ebp", "eax"], pushed_regs(insns)

    # The same call, but the argument register is the previous call's return value.
    stub_off = 0x100
    body[stub_off] = 0xE8
    struct.pack_into("<i", body, stub_off + 1, 0x3100 - (base + stub_off + 5))
    body[0x105] = 0x50                                  # push eax (the return value)
    body[0x106] = 0x55                                  # push ebp
    site2_off = 0x107
    body[site2_off] = 0xE8
    struct.pack_into("<i", body, site2_off + 1, 0x3200 - (base + site2_off + 5))
    insns = chain(bytes(body), site2_off, vr)
    arg, kind, member, text = arg_of(insns, base)
    assert kind == "call-result" and member is None, (arg, kind, member, text)
    assert text == "call 0x00003100", text

    # A `E8` inside an immediate is not a call site: the same bytes one byte later have no chain behind them.
    body[0x160] = 0xE8
    shifted = chain(bytes(body), 0x161, vr)
    assert shifted is None or arg_of(shifted, base)[1] != "member", "a shifted start must not produce a member"
    print("self-check ok: a member argument (+0x4d0), a call-result argument, and a shifted start")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--image", help="default.xbe")
    ap.add_argument("-o", "--out", help="artifact directory")
    ap.add_argument("--report", action="store_true", help="print the committed artifact's tables")
    ap.add_argument("--check", action="store_true", help="self-test on synthetic bytes, no dump")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.report:
        path = os.path.join(ROOT, "inventory", ARTIFACT)
        with open(path, newline="", encoding="utf-8") as fh:
            report(list(csv.DictReader(fh)))
        return 0
    if not args.image:
        ap.error("--image is required (or --check / --report)")
    return sweep(args)


if __name__ == "__main__":
    sys.exit(main())
