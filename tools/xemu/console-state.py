#!/usr/bin/env python3
"""Read a `Console` object's strings out of a whole-RAM dump: is a command in its history? (PLAN 7.3 / U15)

The console on the stock disc draws nothing, so "did that keypress reach it" was answered from RAM
(`ramscan.py --text`). The typed line is an `FString` field of one object -- `Engine.Console`, measured at
`0x02661b00 +0x44` -- and the submit path copies it into `History[HistoryTop]` before clearing it
(`Engine/Console.uc:259-272`). That makes the history the receipt: a dump where `History` holds the text is a
delivered command, and a dump where it does not is a keypress the guest ignored.

    python3 tools/xemu/console-state.py after.bin --cr3 0xf000
    python3 tools/xemu/console-state.py after.bin --cr3 0xf000 --expect "stat fps"
    python3 tools/xemu/console-state.py after.bin --cr3 0xf000 --class Console --depth 0x200

`pmemsave` writes *physical* memory while every pointer in it is *linear*, so `--cr3` (from `info registers`,
measured `0x0000f000`) is required to follow one; `--flat` disables the translation, which is what `--selftest`
uses. The tool prints addresses, offsets and the strings themselves -- console text the user typed is not game
data, but a dump still stays on the desktop.

Why not just search the text: a five-character word occurs several times in 128 MiB (a class name, an ini key,
the same buffer twice). Only a *field* of a `Console` object answers the question, so the walk is: every
four-aligned occurrence of the class table -> the words after it screen it as a `UObject` (`ramscan.object_shape`)
-> every four-aligned offset inside the object is decoded as an `FString` and kept only when Count/Max agree
with a NUL-terminated printable string at a translated address.

`--expect TEXT` exits 1 when no `Console` object holds it, so a boot's dump can be asserted on without a person
reading the output. `--selftest` proves the decoder on a synthetic image, because CI has no dump.
"""
import argparse
import os
import struct
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(TOOLS))
MAX_CHARS = 0x4000          # an FString's Max beyond this is not a console buffer; keeps a false positive out


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
    integers that happens to look like a length still fails on the bytes at the address it names.
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
    """A synthetic image: one Console object, its TypedStr and its History[0], decoded with no translation."""
    class_table, object_base = 0x100, 0x200
    blob = bytearray(0x4000)
    struct.pack_into("<IIIIII", blob, object_base, class_table, 1, 0, 0, 0, 0xFFFFFFFF)
    for offset, text, capacity, where in ((0x44, " hist", 6, 0x1000), (0x50, " stat fps", 16, 0x2000)):
        struct.pack_into("<III", blob, object_base + offset, where, len(text) + 1, capacity)
        blob[where:where + len(text) * 2 + 2] = text.encode("utf-16-le") + b"\x00\x00"
    blob = bytes(blob)
    found = objects(blob, {class_table: "Engine.Console"}, "Console", 8)
    assert found == [(object_base, "Engine.Console")], found
    fields = strings_in(blob, object_base, 0x200, lambda va: va)
    offsets = [offset for offset, _field in fields]
    assert offsets == [0x44, 0x50], offsets
    assert fields[0][1]["text"] == " hist" and fields[1][1]["text"] == " stat fps", fields
    assert field_at(blob, object_base + 0x48, lambda va: va) is None, "a Count word must not read as a field"
    print("selftest: 1 object, 2 fields (+0x44 ' hist', +0x50 ' stat fps'), 1 rejected alignment -- ok")
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
                        help="bytes of the object to decode as fields (default 0x200)")
    parser.add_argument("--limit", type=int, default=8, help="stop after N objects (default 8)")
    parser.add_argument("--expect", metavar="TEXT", default=None,
                        help="exit 1 unless some Console object holds this text (a delivered command)")
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
        return 1 if args.expect else 0

    matched = False
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
    if args.expect is None:
        return 0
    print("%s: %r %s" % (os.path.basename(args.dump), args.expect,
                         "is in a Console string field" if matched else "is NOT in any Console string field"))
    return 0 if matched else 1


if __name__ == "__main__":
    sys.exit(main())
