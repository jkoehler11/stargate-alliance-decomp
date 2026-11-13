#!/usr/bin/env python3
"""Who calls the record writers, and what each call is handed.

`docs/NATIVE_VM.md` reads the class record's 20-byte middle as a second array of `FUN_0003d900`'s 12-byte element
grammar, and the reader `0x0003a800` calls that writer **once** -- so a second array of the same grammar would have to
be packed by some other body. A call site is the witness and its argument frame is the claim: this tool scans the
image's executable sections for `E8 rel32` whose target is one of the two record writers, then reconstructs the
arguments the call is handed.

* `FUN_0003d900` -- 12-byte records (one object reference plus two dwords), and
* `FUN_0003d160` -- a compact count and that many one- or two-byte pairs.

**The frame is stack-accounted, not read off the pushes nearest the call.** MSVC stages an argument of the *next*
call before it issues the current one: the push lands under the current call's own arguments, the callee pops only
what it was handed, and the caller's `add esp` skips only those -- so the earliest push of the run is still on the
stack when the next call executes. A window that reads only the pushes after the previous call therefore
under-reports the frame, and `0x3a8af` (which reads as "one argument, the archive the previous call returned") is
handed two. The arity comes from the caller's own cleanup (`add esp,imm` after the call, i.e. a cdecl callee) and
the arguments are the topmost `arity` dwords of the window's push accounting, in argument order.

The argument's writer is classified, not guessed: a `LEA reg,[base+disp]` is the member at that displacement, a
`CALL` means the callee's return value is passed on (the reader chains the two writers), and anything else is
reported as itself. A site counts only when a forward decode from a start offset in the window behind it lands
exactly on the call with a chain of instruction ends behind it -- the boundary rule `tools/vcall-receiver.py`
established, because a bare `E8 3d 09 03 00` also occurs inside displacements and immediates and a raw byte scan
therefore over-counts. Pushes left on the stack at a site are reported as that site's *staged* arguments.

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
AFTER_LIMIT = 4                          # instructions scanned past a call for the caller's cleanup
COLUMNS = ["writer", "target", "site", "function", "boundary", "args", "arg", "arg_kind", "member",
           "member_hex", "writer_insn", "pushes", "staged", "staged_member"]


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


def _esp_imm(blob, off):
    """The immediate of the `add esp,imm` the shared decoder names but does not record.

    `83 /0` carries a sign-extended imm8 and `81 /0` an imm32; both are the caller's own cleanup in this image, and
    a stack adjust of either width is what tells the arity.
    """
    if off < 0 or off + 6 > len(blob):
        return None
    if blob[off] == 0x83:
        return blob[off + 2]
    if blob[off] == 0x81:
        return struct.unpack_from("<I", blob, off + 2)[0]
    return None


def pushed_regs(insns):
    """The registers the window pushes, in push order."""
    return [insn.text.split()[1] for _off, insn in insns if insn.text.startswith("push ")]


def pending(insns, blob, vr):
    """The pushes still on the stack at the call, in push order, after the window's own cleanups.

    A cleanup removes its bytes off the top -- whether the caller writes the `add esp,imm` or the callee pops its own
    arguments (then nothing follows the call and the pushes in front of it are all its arguments). Whatever survives
    is what the call at the site is looking at.
    """
    out, skip = [], set()
    for off, insn in insns:
        if off in skip:
            continue
        if insn.text.startswith("push "):
            out.append(insn.text.split()[1])
        elif insn.text == "add esp,imm":
            drop = min(len(out), max(0, (_esp_imm(blob, off) or 0) // 4))
            if drop:
                out = out[:-drop]
        elif insn.kind == "call":
            cleaned, at = cleanup_after(blob, off + insn.length, vr)
            if at is not None:
                skip.add(at)
                drop = 0 if cleaned is None else min(len(out), cleaned // 4)
            else:
                drop = len(out)
            if drop:
                out = out[:-drop]
    return out


def cleanup_after(blob, off, vr):
    """(bytes, offset) of the `add esp,imm` a caller uses after a call, or (None, None)."""
    i = off
    for _ in range(AFTER_LIMIT):
        insn = vr.decode(blob, i)
        if insn is None or insn.length <= 0:
            return None, None
        if insn.text == "add esp,imm":
            return _esp_imm(blob, i), i
        if insn.text.startswith("push ") or insn.kind == "call":
            return None, None
        i += insn.length
    return None, None


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


def frame(insns, blob, site_off, vr, base_va):
    """(arguments in argument order, staged pushes left for the next call) at one call site.

    An argument is (reg, kind, member, writer text). The arity is the caller's own cleanup when it has one, so a
    push that a *later* call consumes is not counted here: it is reported as staged.
    """
    keep = pending(insns, blob, vr)
    cleaned, _at = cleanup_after(blob, site_off + 5, vr)
    arity = len(keep) if cleaned is None else min(len(keep), cleaned // 4)
    args = []
    for reg in reversed(keep[len(keep) - arity:]):        # cdecl pushes right to left
        kind, member, text = writer_of(insns, reg, base_va)
        args.append((reg, kind, member, text))
    return args, keep[:len(keep) - arity]


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
            args, staged_push = frame(insns, blob, off, vr, sec.va) if insns else ([], [])
            staged = [_stage_arg(insns, reg, sec.va) for reg in staged_push]
            chosen = next(((a[1], a[2], a[3]) for a in args if a[1] == "member"),
                          next(((a[1], a[2], a[3]) for a in args if a[1] != "no-writer"),
                               ("none", None, "")))
            staged_member = next(("0x%x" % a[2] for a in staged if a[1] == "member"), "")
            rows.append({"writer": writer, "target": "0x%08x" % target, "site": "0x%08x" % site,
                         "function": owner(functions, site), "boundary": int(bool(insns)),
                         "args": len(args),
                         "arg": args[0][0] if args else "",
                         "arg_kind": chosen[0],
                         "member": "" if chosen[1] is None else chosen[1],
                         "member_hex": "" if chosen[1] is None else "0x%x" % chosen[1],
                         "writer_insn": chosen[2],
                         "pushes": " | ".join(a[0] for a in args),
                         "staged": len(staged),
                         "staged_member": staged_member})
    rows.sort(key=lambda r: (r["writer"], r["site"]))
    return rows


def _stage_arg(insns, reg, base_va):
    """A staged push, classified the same way as an in-frame argument."""
    kind, member, text = writer_of(insns, reg, base_va)
    return (reg, kind, member, text)


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
            print("    arity: %s" % collections.Counter(r["args"] for r in proven).most_common(4))
        for r in proven:
            print("    %s in %s: %s arg(s) [%s], %s -> member %s (%s)%s"
                  % (r["site"], r["function"] or "?", r["args"], r["pushes"] or "-", r["arg_kind"],
                     r["member_hex"] or "-", r["writer_insn"] or "-",
                     ", leaves %s staged%s" % (r["staged"],
                                                " (%s)" % r["staged_member"] if r["staged_member"] else "")
                     if r["staged"] else ""))


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
    """Synthetic bodies: a plain member argument, a chained call, and an argument staged under the previous call."""
    vr = decoder()
    base = 0x1000
    body = bytearray(b"\x90" * 0x200)
    site_off = 0x120
    body[0x118:0x11e] = b"\x8d\x86\xd0\x04\x00\x00"    # lea eax,[esi+0x4d0]
    body[0x11e] = 0x50                                  # push eax
    body[0x11f] = 0x55                                  # push ebp
    site = base + site_off
    body[site_off] = 0xE8
    struct.pack_into("<i", body, site_off + 1, 0x3000 - (site + 5))
    body[site_off + 5:site_off + 8] = b"\x83\xc4\x08"    # add esp,0x8
    found = [(s, t, o) for s, t, o in candidates(bytes(body), base) if t == 0x3000]
    assert len(found) == 1 and found[0][2] == site_off, found
    insns = chain(bytes(body), site_off, vr)
    assert insns, "no chain lands on the call"
    args, staged = frame(insns, bytes(body), site_off, vr, base)
    assert not staged and len(args) == 2, (args, staged)
    assert args[0][1] == "no-writer" and args[1][1] == "member" and args[1][2] == 0x4D0, args

    # The reader's own shape: +0x4dc pushed under the call, so it belongs to the *next* call, not to this one.
    body[0x0f0:0x0f6] = b"\x8d\x96\xdc\x04\x00\x00"    # lea edx,[esi+0x4dc]
    body[0x0f6] = 0x52                                  # push edx
    body[0x0f7:0x0fd] = b"\x8d\x86\xd0\x04\x00\x00"    # lea eax,[esi+0x4d0]
    body[0x0fd] = 0x50                                  # push eax
    body[0x0fe] = 0x55                                  # push ebp
    first = 0x0ff
    body[first] = 0xE8
    struct.pack_into("<i", body, first + 1, 0x3100 - (base + first + 5))
    body[first + 5:first + 8] = b"\x83\xc4\x08"         # add esp,0x8  -> the +0x4dc push stays
    body[first + 8] = 0x50                              # push eax (the return value)
    second = first + 9
    body[second] = 0xE8
    struct.pack_into("<i", body, second + 1, 0x3200 - (base + second + 5))
    body[second + 5:second + 8] = b"\x83\xc4\x08"
    insns = chain(bytes(body), first, vr)
    args, staged = frame(insns, bytes(body), first, vr, base)
    assert len(args) == 2 and args[1][1] == "member" and args[1][2] == 0x4D0, args
    assert staged == ["edx"], staged
    insns = chain(bytes(body), second, vr)
    args, staged = frame(insns, bytes(body), second, vr, base)
    assert len(args) == 2 and not staged, (args, staged)
    assert args[0][1] == "call-result" and args[0][3] == "call 0x00003100", args
    assert args[1][1] == "member" and args[1][2] == 0x4DC, args

    # A `E8` inside an immediate is not a call site: the same bytes one byte later have no chain behind them.
    body[0x160] = 0xE8
    shifted = chain(bytes(body), 0x161, vr)
    assert shifted is None or not frame(shifted, bytes(body), 0x161, vr, base)[0][0][1] == "member", \
        "a shifted start must not produce a member"
    print("self-check ok: a member argument (+0x4d0), an argument staged under the previous call (+0x4dc),"
          " and a shifted start")
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
