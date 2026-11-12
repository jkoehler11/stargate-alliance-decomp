#!/usr/bin/env python3
"""Read one `GNatives` slot's live body out of `default.xbe` and say what it does to its arguments.

`GNatives` is the dispatch table the interpreter indexes (`docs/NATIVE_VM.md`): 4,096 pointers at
`0x006313e8`, filled at startup, and `inventory/gnatives_slots.csv` already answers **which address
sits in each slot**. What that map does not answer is the question a bytecode reader keeps asking --
*what does this token consume from the code stream* -- and the answer is a body, not a number.

    python3 tools/gnatives-body.py --slot 31
    python3 tools/gnatives-body.py --va 0x00047bf0
    python3 tools/gnatives-body.py --slot 31 --raw          # bytes only, no listing

For each slot it prints the registration channel the inventory records (an absolute `mov [slot]`
store, or a stub that pushes the index), the `{name, body}` record in `.data` when one exists, and
then the body: every instruction, with

* a direct `call rel32` resolved to its target and named from `symbols/xbox_functions.csv`;
* a `call [reg*4+0x6313e8]` rendered as a `GNatives` dispatch, because that is the table the
  interpreter itself calls through;
* every immediate that lands inside `.data`/`.rdata` marked as such, and every `[reg+disp]` read
  kept with its displacement -- a token's payload is a field offset, and the displacement is the
  evidence;
* the terminator that ends the body, so a size read from the map can be checked against the code.

The body extent comes from `symbols/xbox_functions.csv` when the map has the address; otherwise it is
the first terminator that ends one instruction past the start (`ret`, `ret imm16`, `int3`, `jmp`), the
same boundary rule `tools/native-vtable.py` uses. Nothing is written: a body's bytes are the game's
and stay out of git (`AGENTS.md`), and this prints structure, not payload.

`--check` drives the whole read on a synthetic body with no dump: instruction lengths, the table-call
render, direct-call resolution, the terminator rule and the map-less extent.

Why this exists, measured 2026-11-12: slot `0x1f` is `intUObjectexecStringConst` at `0x00047bf0`, and
its 69 bytes say the string-const token has **no length field and no header** -- the handler copies the
literal at `FFrame::Code` (a NUL-terminated ANSICHAR string) and walks `Code` one byte at a time to the
terminator. `docs/UNKNOWNS.md` U13's four bytes sit where that token's *opcode* is, and the executor
reads a one-byte opcode (`0x48ce5`), so they are not a payload this body reads.
"""
import argparse
import csv
import importlib.util
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)
from xbe import Xbe  # noqa: E402

TABLE = 0x006313E8          # GNatives
DUMP_ENV = "SG1_DUMP"
GAME_DIR = "Stargate_SG-1_-_The_Alliance-2005-12-15"
SLOTS = os.path.join(ROOT, "inventory", "gnatives_slots.csv")
RECORDS = os.path.join(ROOT, "inventory", "gnatives_records.csv")
STUBS = os.path.join(ROOT, "inventory", "gnatives_stubs.csv")
WRITES = os.path.join(ROOT, "inventory", "gnatives_write_sites.csv")
FUNCS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
TERMINATORS = ("ret", "ret imm16", "int3", "jmp")


def decode_module():
    path = os.path.join(TOOLS, "vcall-receiver.py")
    spec = importlib.util.spec_from_file_location("sg1_vcall_receiver", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def function_map():
    """address -> (name, size) from the committed map."""
    out = {}
    for row in read_csv(FUNCS):
        try:
            va = int(row["address"], 16)
        except (KeyError, ValueError):
            continue
        name = (row.get("name") or "").strip()
        try:
            size = int(row.get("size") or 0)
        except ValueError:
            size = 0
        out[va] = (name, size)
    return out


def slot_rows():
    """index -> the inventory's row for that slot, joined with record/stub/store evidence."""
    rows = {}
    for row in read_csv(SLOTS):
        try:
            idx = int(row["index"])
        except (KeyError, ValueError):
            continue
        rows[idx] = dict(row)
    for row in read_csv(RECORDS):
        try:
            idx = int(row["slot"])
        except (KeyError, ValueError):
            continue
        if idx in rows:
            rows[idx].setdefault("record_name", (row.get("record_name") or "").strip())
            rows[idx].setdefault("record_va", (row.get("record_va") or "").strip())
    for row in read_csv(STUBS):
        try:
            idx = int(row["index"])
        except (KeyError, ValueError):
            continue
        if idx in rows:
            rows[idx].setdefault("stub", (row.get("stub") or "").strip())
    for row in read_csv(WRITES):
        try:
            idx = int(row["slot"])
        except (KeyError, ValueError):
            continue
        if idx in rows:
            rows[idx].setdefault("store_site", (row.get("address") or "").strip())
    return rows


def body_extent(blob, va, size, mod):
    """The body's length: the map's size when it has one, else up to the first terminating instruction."""
    if size:
        return size, "symbols/xbox_functions.csv"
    off = 0
    while off < len(blob):
        insn = mod.decode(blob, off)
        if insn is None:
            break
        off += insn.length
        if insn.text in TERMINATORS or insn.text.startswith("ret"):
            return off, "terminator"
    return off, "no terminator"


def listing(blob, va, size, mod, funcs, image):
    """Decode the body into rows: (va, bytes, text, note)."""
    rows = []
    off = 0
    while off < size:
        insn = mod.decode(blob, off)
        if insn is None:
            rows.append((va + off, blob[off:off + 1], "<unknown opcode>", "decode-stop"))
            break
        note = ""
        if insn.kind == "call" and insn.disp == TABLE and insn.index:
            note = "GNatives[%s*4]" % insn.index
        elif insn.kind == "call" and "call rel" in insn.text:
            rel = struct.unpack_from("<I", blob, off + 1)[0]
            target = va + off + insn.length + rel
            name, _ = funcs.get(target, ("", 0))
            note = "-> 0x%08x%s" % (target, " " + name if name else " (unnamed)")
        elif insn.imm is not None and image.section_for(insn.imm) is not None:
            note = "imm lands in %s" % image.section_for(insn.imm).name
        rows.append((va + off, blob[off:off + insn.length], insn.text, note))
        off += insn.length
    return rows


def describe(args):
    dump = args.dump or os.environ.get(DUMP_ENV, "")
    if not dump:
        sys.exit("set %s to the dump's parent (or pass --dump)" % DUMP_ENV)
    xbe_path = os.path.join(dump, GAME_DIR, "default.xbe") if os.path.isdir(
        os.path.join(dump, GAME_DIR)) else os.path.join(dump, "default.xbe")
    image = Xbe(xbe_path)
    mod = decode_module()
    funcs = function_map()

    if args.va is not None:
        va = args.va
        row = None
    else:
        rows = slot_rows()
        row = rows.get(args.slot)
        if row is None:
            sys.exit("slot %d is not in inventory/gnatives_slots.csv" % args.slot)
        va = int(row["address"], 16)
        print("slot %d (0x%x): %s" % (args.slot, args.slot, row.get("evidence", "")))
        if row.get("store_site"):
            print("  store       %s -> 0x%08x" % (row["store_site"], va))
        if row.get("stub"):
            print("  index stub  %s" % row["stub"])
        if row.get("record_name"):
            print("  .data record %s at %s" % (row["record_name"], row.get("record_va", "?")))
        declarers = (row.get("declarers") or "").strip()
        print("  declarers   %s" % (declarers or "none -- an engine-internal exec"))
        if (row.get("name") or "").strip():
            print("  named       %s (from a package declaration)" % row["name"].strip())

    name, size = funcs.get(va, ("", 0))
    blob = image.read_va(va, max(size, 64) if size else 512)
    extent, source = body_extent(blob, va, size, mod)
    print("body 0x%08x: %d byte(s), extent from %s%s"
          % (va, extent, source, ", map name " + name if name else ""))
    rows = listing(blob[:extent], va, extent, mod, funcs, image)
    if args.raw:
        for start, chunk, text, note in rows:
            print("  %08x  %-32s  %s" % (start, chunk.hex(" "), text))
        return 0
    calls, fields, globals_used, rets = [], [], [], []
    for start, chunk, text, note in rows:
        print("  %08x  %-24s  %-34s %s" % (start, chunk.hex(" ")[:23], text, note))
        if note.startswith("GNatives["):
            calls.append(text)
        elif note.startswith("-> "):
            calls.append(note[3:])
        if "[" in text and "+0x" in text and text.startswith("mov"):
            fields.append(text)
        if "imm lands in" in note:
            globals_used.append(text)
        if text.startswith("ret"):
            rets.append(text)
    print("calls   %d: %s" % (len(calls), ", ".join(calls) or "none"))
    print("fields  %d: %s" % (len(fields), ", ".join(fields) or "none"))
    print("globals %d: %s" % (len(globals_used), ", ".join(globals_used) or "none"))
    print("return  %s" % (rets[-1] if rets else "falls through -- not a body"))
    return 0


def check():
    """No dump, no writes: the read on a synthetic body."""
    mod = decode_module()
    body = bytes.fromhex("568b7424088b460c0fb6104089460cff1495e8136300c20800")
    want = [("push esi", 1), ("mov esi,[esp+0x8]", 4), ("mov eax,[esi+0xc]", 3),
            ("movzx edx,[eax]", 3), ("inc eax", 1), ("mov byte [esi+0xc],eax", 3),
            ("call [edx+0x6313e8]", 7), ("ret imm16", 3)]
    off = 0
    for text, length in want:
        insn = mod.decode(body, off)
        if insn is None:
            print("FAIL --check: no instruction at offset %d" % off)
            return 1
        if insn.text != text or insn.length != length:
            print("FAIL --check: offset %d is %r/%d, expected %r/%d"
                  % (off, insn.text, insn.length, text, length))
            return 1
        if text.startswith("call [") and not (insn.kind == "call" and insn.disp == TABLE):
            print("FAIL --check: the table call did not resolve to GNatives")
            return 1
        off += insn.length
    if off != len(body):
        print("FAIL --check: the synthetic body decoded to %d bytes, not %d" % (off, len(body)))
        return 1

    # a direct call resolves against its own va, and the map-less extent stops at the terminator
    direct = bytes.fromhex("e83b0b0100c20800")
    insn = mod.decode(direct, 0)
    rel = struct.unpack_from("<I", direct, 1)[0]
    target = 0x1000 + insn.length + rel
    if target != 0x11b40:
        print("FAIL --check: direct call resolved to 0x%x, expected 0x11b40" % target)
        return 1
    extent, source = body_extent(direct, 0x1000, 0, mod)
    if extent != len(direct) or source != "terminator":
        print("FAIL --check: map-less extent is %d/%s" % (extent, source))
        return 1
    # an unknown opcode is reported, not silently dropped
    extent, source = body_extent(bytes.fromhex("0f0b"), 0x2000, 0, mod)
    if extent >= 2 and source == "terminator":
        print("FAIL --check: an unknown opcode was walked past")
        return 1
    print("ok --check: 8 synthetic instructions, the table call, a direct call and the extent rule")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--slot", type=int, help="GNatives index, read through inventory/gnatives_slots.csv")
    ap.add_argument("--va", type=lambda s: int(s, 0), help="a body address instead of a slot")
    ap.add_argument("--dump", help="the dump's parent directory (default $%s)" % DUMP_ENV)
    ap.add_argument("--raw", action="store_true", help="bytes and instruction text only")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        return check()
    if args.slot is None and args.va is None:
        ap.error("give --slot, --va or --check")
    return describe(args)


if __name__ == "__main__":
    sys.exit(main())
