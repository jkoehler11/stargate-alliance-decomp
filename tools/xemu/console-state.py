#!/usr/bin/env python3
"""Read a `Console` object's strings out of a whole-RAM dump: is a command in its history? (PLAN 7.3 / U15)

The console on the stock disc draws nothing, so "did that keypress reach it" is answered from RAM. The typed
line is `Console.TypedStr` (`0x02661b00 +0x44` on the boot that measured it) and a submit copies it into
`History[HistoryTop]` before clearing it (`Engine/Console.uc:259-272`). `Console.uc:10` declares
`var string TypedStr, History[16]` -- a *static* array, so the sixteen entries are sixteen 12-byte `FString`
structs laid out in the object itself, the first at `+0x50` and the last ending at `+0x110` (where `TypedStrPos`
was measured as 5 and `bTyping` as 1 on the receipt boot). That makes the history the receipt: a dump where a
`History` slot holds the text and `TypedStr` is empty is a delivered command, and a dump where it does not is a
keypress the guest ignored.

    python3 tools/xemu/console-state.py after.bin --cr3 0xf000
    python3 tools/xemu/console-state.py after.bin --cr3 0xf000 --history
    python3 tools/xemu/console-state.py after.bin --cr3 0xf000 --receipt "hist"

`pmemsave` writes *physical* memory while every pointer in it is *linear*, so `--cr3` (from `info registers`,
measured `0x0000f000`) is required to follow one; `--flat` disables the translation, which is what `--selftest`
uses. The tool prints addresses, offsets and the strings themselves -- console text the user typed is not game
data, but a dump still stays on the desktop.

Why not just search the text: a five-character word occurs several times in 128 MiB (a class name, an ini key,
the same buffer twice). Only a *field* of a `Console` object answers the question, so the walk is: every
four-aligned occurrence of the class table -> the words after it screen it as a `UObject` (`ramscan.object_shape`)
-> every four-aligned offset inside the object is decoded as an `FString` and kept only when Count/Max agree
with a NUL-terminated printable string at a translated address.

`--expect TEXT` exits 1 unless a `Console` object holds the text in any field; `--receipt TEXT` is the stronger
assertion -- the text is in a `History` slot *and* `TypedStr` is empty, which is what a submit leaves behind.
`--selftest` proves both on a synthetic image, because CI has no dump.
"""
import argparse
import os
import struct
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(TOOLS))
MAX_CHARS = 0x4000          # an FString's Max beyond this is not a console buffer; keeps a false positive out
FSTRING_SIZE = 12           # Data, Count, Max
TYPED_STR = 0x44            # `var string TypedStr` (measured 2026-11-06)
HISTORY = 0x50              # `var string History[16]`, immediately after TypedStr: 16 * 12 = 0xC0 bytes
HISTORY_SLOTS = 16


def load(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, os.path.join(TOOLS, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def u32(blob, offset):
    if offset < 0 or offset + 4 > len(blob):
        raise IndexError(offset)
    return struct.unpack_from("<I", blob, offset)[0]


def field_at(blob, pos, translate):
    """The FString at `pos` if its words are a Data pointer/Count/Max triple, else None.

    `translate(va)` returns the physical address a linear pointer names (identity with `--flat`). The string
    itself is the check: `Count` characters, the last one the NUL, all printable, and `Max >= Count` -- a run of
    integers that happens to look like a length still fails on the bytes at the address it names. An empty
    string (`Count` 0 or 1) decodes to None, so "no field at that offset" is also "that string is empty".
    """
    try:
        data, count, capacity = struct.unpack_from("<III", blob, pos)
    except struct.error:
        return None
    if not data or not 1 <= count <= MAX_CHARS or count > capacity or capacity > MAX_CHARS:
        return None
    physical = translate(data)
    if physical is None or physical + count * 2 > len(blob):
        return None
    raw = blob[physical:physical + count * 2]
    if raw[-2:] != b"\x00\x00":
        return None
    try:
        text = raw[:-2].decode("utf-16-le")
    except UnicodeDecodeError:
        return None
    if any(ord(char) < 0x20 and char != "\t" for char in text):
        return None
    return {"data": data, "count": count, "capacity": capacity, "text": text}


def strings_in(blob, base, depth, translate):
    """[(offset, field)] for every FString-shaped field of the object at `base`, in offset order."""
    out = []
    for offset in range(0, depth, 4):
        found = field_at(blob, base + offset, translate)
        if found:
            out.append((offset, found))
    return out


def history(blob, base, translate):
    """[(index, field)] for the populated slots of `History[16]`, which is inline at `base + 0x50`."""
    out = []
    for index in range(HISTORY_SLOTS):
        found = field_at(blob, base + HISTORY + index * FSTRING_SIZE, translate)
        if found:
            out.append((index, found))
    return out


def objects(blob, tables, match, limit):
    """[(base, class_name)] -- four-aligned occurrences of a matching class table that pass `object_shape`."""
    ramscan = load("ramscan")
    wanted = {table: name for table, name in tables.items() if match.lower() in name.lower()}
    out = []
    for table, name in sorted(wanted.items(), key=lambda item: item[1]):
        at = blob.find(struct.pack("<I", table))
        while at >= 0 and len(out) < limit:
            if at % 4 == 0 and ramscan.object_shape(blob, at) == "object":
                out.append((at, name))
            at = blob.find(struct.pack("<I", table), at + 1)
    return out


def selftest():
    """A synthetic image: one Console object with TypedStr, History[0] and History[1], and HistoryTop after them."""
    class_table, object_base = 0x100, 0x200
    blob = bytearray(0x4000)
    struct.pack_into("<IIIIII", blob, object_base, class_table, 1, 0, 0, 0, 0xFFFFFFFF)
    # TypedStr, and two history slots at exactly the stride the receipt boot measured.
    for offset, text, capacity, where in ((TYPED_STR, " stat fps", 16, 0x1000),
                                          (HISTORY, " hist", 6, 0x2000),
                                          (HISTORY + FSTRING_SIZE, " showdebug", 11, 0x3000)):
        struct.pack_into("<III", blob, object_base + offset, where, len(text) + 1, capacity)
        blob[where:where + len(text) * 2 + 2] = text.encode("utf-16-le") + b"\x00\x00"
    # HistoryTop, immediately after History[15] -- 16 * 12 bytes from +0x50.
    struct.pack_into("<I", blob, object_base + HISTORY + HISTORY_SLOTS * FSTRING_SIZE, 2)
    blob = bytes(blob)

    found = objects(blob, {class_table: "Engine.Console"}, "Console", 8)
    assert found == [(object_base, "Engine.Console")], found
    fields = strings_in(blob, object_base, 0x200, lambda va: va)
    offsets = [offset for offset, _field in fields]
    assert offsets == [TYPED_STR, HISTORY, HISTORY + FSTRING_SIZE], offsets
    assert fields[0][1]["text"] == " stat fps" and fields[1][1]["text"] == " hist", fields
    assert field_at(blob, object_base + TYPED_STR + 4, lambda va: va) is None, "a Count word must not read as a field"
    slots = history(blob, object_base, lambda va: va)
    assert [(index, field["text"]) for index, field in slots] == [(0, " hist"), (1, " showdebug")], slots
    assert u32(blob, object_base + HISTORY + HISTORY_SLOTS * FSTRING_SIZE) == 2, "HistoryTop sits past History[15]"
    # The receipt: 'hist' in a slot with TypedStr populated is *not* a receipt; clear TypedStr and it is.
    receipt = lambda text: any(text in field["text"] for _index, field in slots) \
        and field_at(blob, object_base + TYPED_STR, lambda va: va) is None
    assert not receipt("hist"), "TypedStr still holds the line: the submit has not run yet"
    cleared = bytearray(blob)
    struct.pack_into("<III", cleared, object_base + TYPED_STR, 0, 0, 0)
    cleared = bytes(cleared)
    survived = history(cleared, object_base, lambda va: va)
    assert [(index, field["text"]) for index, field in survived] == [(0, " hist"), (1, " showdebug")], survived
    assert field_at(cleared, object_base + TYPED_STR, lambda va: va) is None, "cleared TypedStr reads as empty"
    print("selftest: 1 object, TypedStr + 2 history slots at +0x%02x/+0x%02x stride %d, HistoryTop past slot 15, "
          "receipt needs the line cleared -- ok" % (TYPED_STR, HISTORY, FSTRING_SIZE))
    return 0


def main():
    if "--selftest" in sys.argv[1:]:
        return selftest()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dump", help="RAM image written by `monitor=pmemsave` (address == file offset)")
    parser.add_argument("--cr3", type=lambda v: int(v, 0), default=None,
                        help="CR3 from `info registers` (measured 0xf000), to translate the linear pointers")
    parser.add_argument("--flat", action="store_true",
                        help="no translation: pointers are physical addresses (a synthetic image, --selftest)")
    parser.add_argument("--class", dest="match", default="Console", metavar="SUBSTR",
                        help="class-table name substring to look for (default: Console)")
    parser.add_argument("--depth", type=lambda v: int(v, 0), default=0x200,
                        help="bytes of the object to decode as fields (default: 0x200)")
    parser.add_argument("--limit", type=int, default=8, help="stop after N objects (default: 8)")
    parser.add_argument("--expect", metavar="TEXT", default=None,
                        help="exit 1 unless some Console object holds this text")
    parser.add_argument("--history", action="store_true",
                        help="print TypedStr and the populated History[16] slots by their measured offsets")
    parser.add_argument("--receipt", metavar="TEXT", default=None,
                        help="exit 0 only if TEXT is in a History slot *and* TypedStr is empty (a submitted command)")
    parser.add_argument("--quiet", action="store_true", help="print only the verdict and the matching field")
    args = parser.parse_args()

    with open(args.dump, "rb") as handle:
        blob = handle.read()
    if args.flat:
        translate = lambda va: va if 0 <= va < len(blob) else None
    else:
        if not args.cr3:
            parser.error("--cr3 is required to translate a linear pointer (or --flat for a synthetic image)")
        vamap = load("vamap")

        def translate(va):
            mapped = vamap.physical_for(blob, args.cr3, va)
            return mapped[0][0] if mapped else None

    found = objects(blob, load("ramscan").class_names(), args.match, args.limit)
    print("%s: %d byte(s), %d %s object(s)" % (os.path.basename(args.dump), len(blob), len(found), args.match))
    if not found:
        print("  no %s object whose class table is in the census: nothing to decode" % args.match)
        return 1 if args.expect or args.receipt else 0

    matched = False
    delivered = False
    for base, name in found:
        fields = strings_in(blob, base, args.depth, translate)
        print("  %s at %#010x: %d string field(s)" % (name, base, len(fields)))
        for offset, field in fields:
            hit = args.expect is not None and args.expect in field["text"]
            matched = matched or hit
            if args.quiet and args.expect is not None and not hit:
                continue
            print("    +%#05x -> %#010x  Count/Max %d/%d%s  %r"
                  % (offset, field["data"], field["count"], field["capacity"],
                     "  <= %s" % args.expect if hit else "", field["text"]))
        if args.history or args.receipt:
            line = field_at(blob, base + TYPED_STR, translate)
            slots = history(blob, base, translate)
            print("    History[%d] at +%#05x (stride %d): %d populated; TypedStr at +%#05x: %s"
                  % (HISTORY_SLOTS, HISTORY, FSTRING_SIZE, len(slots), TYPED_STR,
                     "empty" if line is None else repr(line["text"])))
            for index, field in slots:
                print("      [%d] -> %#010x  Count/Max %d/%d  %r"
                      % (index, field["data"], field["count"], field["capacity"], field["text"]))
            if args.receipt is not None:
                hits = [(index, field) for index, field in slots if args.receipt in field["text"]]
                ok = bool(hits) and line is None
                delivered = delivered or ok
                print("    receipt %r: %s" % (args.receipt,
                      "History[%d] holds it and TypedStr is empty -- delivered" % hits[0][0] if ok else
                      ("History[%d] holds it but TypedStr is not empty -- not submitted yet" % hits[0][0]
                       if hits else "not in any History slot -- never submitted")))

    if args.receipt is not None:
        print("%s: receipt %r %s" % (os.path.basename(args.dump), args.receipt,
                                     "DELIVERED" if delivered else "NOT delivered"))
        return 0 if delivered else 1
    if args.expect is None:
        return 0
    print("%s: %r %s" % (os.path.basename(args.dump), args.expect,
                         "is in a Console field" if matched else "is NOT in any Console field"))
    return 0 if matched else 1


if __name__ == "__main__":
    sys.exit(main())
