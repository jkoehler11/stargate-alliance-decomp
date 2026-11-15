#!/usr/bin/env python3
"""What supplies the primitive-cast index: one byte of the bytecode stream, read and stepped by the handler.

`GNatives[57]` is `0x00040be0`, the 28-byte body the symbol map names `Native_57` and the record block names
`intUObjectexecPrimitiveCast`. The previous two rounds found the 33 conversions, the 306-entry array they are read
from and the second table (`0x00630fe0`, 255 entries, same stub fill) they are installed into; the open half was
*what supplies the index* at that table's only dispatch site, `jmp dword [reg*4+0x630fe0]`. This tool answers it,
and both halves of the answer are falsifiable:

* **The index is a byte in the bytecode stream.** The body is nine instructions and has **no `E8` caller in the
  image** -- the earlier note's "the call site is one `E8` away" was a guess, and the byte scan answers zero
  because the handler is reached through its `GNatives` slot, not by a direct call. It loads the frame
  (`[esp+4]`), takes the frame's cursor field (`+0xc`), reads **one byte** through it, advances that cursor past
  the byte, loads the frame's `+0x8` field into `ecx`, replaces its own first argument with the frame and
  **tail-jumps** through the table with the byte as the index. So the conversion is selected by the operand the
  compiler emitted after the cast opcode -- it is not a compile-time table, and the VM reads no second dword.
* **The corpus emits exactly the installed slots.** Walking every payload that disassembles end to end, the
  `PrimitiveCast` opcode carries 10,529 operand bytes over 24 distinct values, **all of them inside the installed
  57-90 range and none at slot 70** (the one slot no installer fills). The byte is the slot index: the operand
  population and the fill population are the same numbering, which is what makes each conversion name reachable
  from a bytecode offset.
* **Each cast body is itself a handler, and re-dispatches.** `ByteToInt` (slot 58, `0x00040c00`) takes the frame's
  cursor again, reads the *next* byte, builds a temp of the **source** width, calls
  `[GNatives+byte*4]` on the nested expression to fill it, and stores the converted value through the `ecx` the
  dispatcher handed it. So `0x39 <slot>` is followed by a whole expression, and the slot names the pair -- which
  is why there are 33 bodies rather than one cast with two type operands.

    python3 tools/cast-dispatch.py --xbe DUMP/default.xbe --system DUMP/System -o inventory   # the artifact
    python3 tools/cast-dispatch.py --check          # dump-free, in `make ci`
    python3 tools/cast-dispatch.py --report         # the counts, from the artifact

`inventory/cast_operands.csv` is one row per slot: the conversion's name, the body the installer stores, the
thunk's store site, whether an installer fills it at all, and how many times the shipped scripts emit it. The
`--check` is dump-free and joins the artifact against `inventory/opcode_usage.csv` -- the operand tokens must add
up to that table's `PrimitiveCast` total, so a parser change that moves the count fails the gate instead of the
next document.

Two things this tool does **not** claim: that the byte is a *type* token (it is a slot into the conversion table,
and the nested expression's own opcode is the next byte), and that the nine installed conversions the corpus never
emits are unreachable -- they are reachable, just unused by this build's scripts.
"""
import argparse
import collections
import csv
import glob
import importlib.util
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from xbe import Xbe  # noqa: E402

GNATIVES = 0x006313E8
CAST_TABLE = 0x00630FE0
CAST_OPCODE = 0x39
DISPATCHER = "intUObjectexecPrimitiveCast"
BODY_BYTES = 28
COLUMNS = ["slot", "name", "body", "installer", "installed", "tokens", "payloads"]
EXEC = re.compile(r"^int\w*?exec(\w+)$")


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def addr(text):
    """`0x630fe0`, `0x00630FE0` and `630fe0` are one address; compare integers, never the text."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return int(text, 16)
    except ValueError:
        return None


def short(record_name):
    """`intUObjectexecByteToInt` -> `ByteToInt` (the record block names each handler this way)."""
    m = EXEC.match((record_name or "").strip())
    return m.group(1) if m else ""


def decode(raw):
    """(code_off, object_off, table_va) from the 28-byte dispatcher, or `ValueError` naming the instruction.

    Every check below is a byte of the body, not a decompiler's opinion: the point of the tool is that the index's
    provenance is readable from nine instructions, so a body that is not that shape must fail loudly rather than
    be described by a guess.
    """
    if len(raw) < BODY_BYTES:
        raise ValueError("body is %d bytes, expected %d" % (len(raw), BODY_BYTES))
    if raw[0:4] != b"\x8b\x44\x24\x04":
        raise ValueError("does not start `mov eax,[esp+4]` (the frame argument)")
    if raw[4:6] != b"\x8b\x48":
        raise ValueError("second instruction is not `mov ecx,[eax+disp8]`")
    code_off = raw[6]
    if raw[7:10] != b"\x0f\xb6\x11":
        raise ValueError("third instruction is not `movzx edx,byte [ecx]`")
    if raw[10] != 0x41:
        raise ValueError("the cursor is not advanced by a one-byte `inc ecx`")
    if raw[11:13] != b"\x89\x48" or raw[13] != code_off:
        raise ValueError("the advanced cursor is not stored back to the field it was read from")
    if raw[14:16] != b"\x8b\x48":
        raise ValueError("the frame field handed to the conversion in ecx is not loaded")
    object_off = raw[16]
    if raw[17:21] != b"\x89\x44\x24\x04":
        raise ValueError("the frame is not written over the body's own first argument")
    if raw[21:24] != b"\xff\x24\x95":
        raise ValueError("does not end `jmp dword [reg*4+disp32]`")
    table_va = struct.unpack_from("<I", raw, 24)[0]
    # The body's own end: a run of two `0xcc` bounds it (a single `int3` may be a jump table's filler). Only the
    # two bytes at the size the symbol map states are checked -- past them the next body begins, and reading that
    # far would make the decoder disagree with the map over a neighbour's first instruction.
    if len(raw) > BODY_BYTES and raw[BODY_BYTES:BODY_BYTES + 2] != b"\xcc\xcc":
        raise ValueError("the body is not bounded by int3 padding at %d bytes" % BODY_BYTES)
    return {"code_off": code_off, "object_off": object_off, "table_va": table_va, "operand_bytes": 1}


def dispatcher(fill_rows):
    """The dispatcher's body address, from the `GNatives` installer row that fills its slot."""
    hits = [r for r in fill_rows
            if (r.get("record_name") or "").strip() == DISPATCHER and addr(r.get("table")) == GNATIVES]
    if len(hits) != 1:
        raise SystemExit("native_fill_tables.csv has %d `%s` rows in GNatives, expected 1"
                         % (len(hits), DISPATCHER))
    return addr(hits[0]["body"]), int(hits[0]["slot"])


def installers(fill_rows):
    """slot -> {name, body, installer} for the rows installed into the conversion table."""
    out = {}
    for r in fill_rows:
        if addr(r.get("table")) != CAST_TABLE:
            continue
        slot = int(r["slot"])
        out[slot] = {"name": short(r["record_name"]), "body": r["body"].strip(),
                     "installer": (r.get("store_site") or "").strip()}
    return out


def disassembler():
    """`tools/uscript-dis.py` as a module: its table and walker are the corpus half of this measurement."""
    path = os.path.join(ROOT, "tools", "uscript-dis.py")
    spec = importlib.util.spec_from_file_location("uscript_dis", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def corpus(system_dir, opcode=CAST_OPCODE):
    """operand byte -> (tokens, payloads) over every payload whose script disassembles end to end."""
    dis = disassembler()
    table = dis.Table()
    tokens, payloads = collections.Counter(), collections.Counter()
    stats = {"seen": 0, "walked": 0}
    for path in sorted(glob.glob(os.path.join(system_dir, "*.u"))):
        for nodes in dis._payload_trees(path, table, stats):
            here = set()

            def visit(items):
                for node in items:
                    # `PrimitiveCast` is `bytes:1,expr`: the parser keeps the operand byte and the child expression,
                    # so the byte that indexes the conversion table is `operands[0]` and nothing has to be re-read.
                    if node["opcode"] == opcode and node["operands"]:
                        tokens[node["operands"][0]] += 1
                        here.add(node["operands"][0])
                    visit(node["children"])
            visit(nodes)
            for byte in here:
                payloads[byte] += 1
    return tokens, payloads, stats


def measure(xbe_path, system_dir, fill_csv):
    fill_rows = read_csv(fill_csv)
    body_va, slot = dispatcher(fill_rows)
    image = Xbe(xbe_path)
    facts = decode(image.read_va(body_va, BODY_BYTES + 8))
    if facts["table_va"] != CAST_TABLE:
        raise SystemExit("the dispatcher jumps through %#x, not the conversion table %#x"
                         % (facts["table_va"], CAST_TABLE))
    slots = installers(fill_rows)
    tokens, payloads, stats = corpus(system_dir)
    lo, hi = min(slots), max(slots)
    # The uninstalled hole inside the range gets a row of its own: "no installer" and "never emitted" are two
    # different facts and the second is only interesting because of the first.
    span = sorted(set(range(lo, hi + 1)) | set(slots) | set(tokens))
    rows = []
    for s in span:
        inst = slots.get(s)
        rows.append({
            "slot": s,
            "name": inst["name"] if inst else "",
            "body": inst["body"] if inst else "",
            "installer": inst["installer"] if inst else "",
            "installed": 1 if inst else 0,
            "tokens": tokens.get(s, 0),
            "payloads": payloads.get(s, 0),
        })
    census = {
        "slot": slot, "body": body_va, "table": CAST_TABLE, "code_off": facts["code_off"],
        "object_off": facts["object_off"], "installed": len(slots), "emitted": len(tokens),
        "tokens": sum(tokens.values()), "walked": stats["walked"], "seen": stats["seen"],
        "stray": sorted(set(tokens) - set(slots)), "unused": sorted(s for s in slots if s not in tokens),
        "lo": lo, "hi": hi, "body_bytes": BODY_BYTES,
    }
    return rows, census


def write(path, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("wrote %s (%d slots)" % (path, len(rows)))


def report(rows, census):
    if census.get("body"):
        print("dispatcher: GNatives slot %d = %#x, %d bytes; cursor at frame+%#x, frame+%#x into ecx"
              % (census["slot"], census["body"], census["body_bytes"], census["code_off"],
                 census["object_off"]))
        print("table %#x: %d installed slots, %d-%d" % (census["table"], census["installed"],
                                                        census["lo"], census["hi"]))
        print("corpus: %d payloads walked of %d; %d operand tokens over %d distinct slots"
              % (census["walked"], census["seen"], census["tokens"], census["emitted"]))
        print("  outside the installed range: %d" % len(census["stray"]))
        if census["stray"]:
            print("  stray operands: %s" % ", ".join(str(s) for s in census["stray"]))
    unused = [r for r in rows if r["installed"] and not r["tokens"]]
    print("  installed but never emitted: %d (%s)" % (len(unused),
                                                      ", ".join(r["name"] for r in unused)))
    hole = [r for r in rows if not r["installed"] and census["lo"] <= r["slot"] <= census["hi"]]
    for r in hole:
        print("  slot %d has no installer and %d tokens" % (r["slot"], r["tokens"]))
    for r in rows:
        print("  %3d %-16s %-9s %6d %6d%s"
              % (r["slot"], r["name"] or "-", r["body"] or "-", r["tokens"], r["payloads"],
                 "" if r["installed"] else "  (no installer)"))


def check(out="inventory"):
    """Dump-free: the decoder on synthetic bodies, then the committed artifact against the opcode census."""
    problems = []

    def body(code_off, object_off, table_va):
        return (b"\x8b\x44\x24\x04" + b"\x8b\x48" + bytes([code_off]) + b"\x0f\xb6\x11" + b"\x41"
                + b"\x89\x48" + bytes([code_off]) + b"\x8b\x48" + bytes([object_off])
                + b"\x89\x44\x24\x04" + b"\xff\x24\x95" + struct.pack("<I", table_va)
                + b"\xcc" * 8)

    good = body(0x10, 0x4, 0x00700000)
    try:
        facts = decode(good)
    except ValueError as exc:
        problems.append("decoder refused a body of the documented shape: %s" % exc)
        facts = {}
    if facts and (facts["code_off"], facts["object_off"], facts["table_va"]) != (0x10, 0x4, 0x00700000):
        problems.append("decoder read %r from a body that states 0x10/0x4/0x700000" % facts)

    # The store-back field is the claim that makes the cursor a cursor: a body that advances one field and writes
    # another must be refused, or the tool would describe a two-field read as a walk.
    split = bytearray(good)
    split[13] = 0x11
    try:
        decode(bytes(split))
        problems.append("decoder accepted a cursor that is advanced but stored back to a different field")
    except ValueError:
        pass
    # A body whose first instruction is not the frame load must be refused too.
    try:
        decode(b"\x55" + good[1:])
        problems.append("decoder accepted a body that does not load the frame argument first")
    except ValueError:
        pass

    rows = read_csv(os.path.join(ROOT, out, "cast_operands.csv"))
    if [r for r in rows if not r["installed"]]:
        hole = [int(r["slot"]) for r in rows if not r["installed"]]
        if len(hole) > 1:
            problems.append("%d slots inside the range carry no installer, expected one: %s"
                            % (len(hole), hole))
    for r in rows:
        slot = int(r["slot"])
        if r["installed"] == "1":
            if not r["name"] or not addr(r["body"]):
                problems.append("slot %d is installed but names no body" % slot)
            if not addr(r["installer"]):
                problems.append("slot %d is installed but names no store site" % slot)
        elif int(r["tokens"]):
            problems.append("slot %d has no installer and %s emitted tokens" % (slot, r["tokens"]))
    slots = [int(r["slot"]) for r in rows]
    if not slots:
        problems.append("cast_operands.csv is empty")
    elif slots != list(range(min(slots), max(slots) + 1)):
        gaps = sorted(set(range(min(slots), max(slots) + 1)) - set(slots))
        problems.append("cast_operands.csv skips slots %s; every slot in the range gets a row" % gaps)

    usage = os.path.join(ROOT, out, "opcode_usage.csv")
    if os.path.exists(usage):
        want = [r for r in read_csv(usage) if addr(r["opcode"]) == CAST_OPCODE]
        if not want:
            problems.append("opcode_usage.csv has no %#04x row" % CAST_OPCODE)
        else:
            total = int(want[0]["tokens"])
            got = sum(int(r["tokens"]) for r in rows)
            if got != total:
                problems.append("the artifact holds %d operand tokens, opcode_usage.csv %d for %#04x"
                                % (got, total, CAST_OPCODE))
    if problems:
        for p in problems:
            print("FAIL cast-dispatch: %s" % p)
        return 1
    print("ok   cast-dispatch: %d slots, %d operand tokens, none on an uninstalled slot"
          % (len(rows), sum(int(r["tokens"]) for r in rows)))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xbe", default="")
    ap.add_argument("--system", default="", help="the System directory (the .u packages)")
    ap.add_argument("--fill", default=os.path.join(ROOT, "inventory", "native_fill_tables.csv"))
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check(args.out)
    if args.report:
        rows = read_csv(os.path.join(ROOT, "inventory", "cast_operands.csv"))
        rows = [dict(r, slot=int(r["slot"]), tokens=int(r["tokens"]), payloads=int(r["payloads"]),
                     installed=int(r["installed"])) for r in rows]
        # `--report` reads the artifact only, so it says what the CSV says and no dispatcher line it cannot rebuild.
        report(rows, {})
        return 0
    if not args.xbe or not args.system:
        ap.error("--xbe and --system are required unless --check or --report is given")
    rows, census = measure(args.xbe, args.system, args.fill)
    write(os.path.join(args.out, "cast_operands.csv"), rows)
    report(rows, census)
    return 0


if __name__ == "__main__":
    sys.exit(main())
