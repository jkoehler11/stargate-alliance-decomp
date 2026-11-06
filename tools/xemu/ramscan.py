#!/usr/bin/env python3
"""Scan a guest RAM dump for bytes, then for the pointers that name their owner (PLAN 7.3 / U15).

The GDB stub answers ~2 packets a second, so pattern-scanning the 123 MB heap through it costs ~16 minutes and a
second pass for a second pattern costs another 16 (`objects.py --find-text`; measured 130 KiB/s at one 64 KiB page
per request, 2026-11-06). `drive.py --step='monitor=pmemsave 0 0x8000000 ram.bin'` writes the whole RAM to a host
file *inside* the same boot -- a second QMP client after the harness exits gets no greeting, so the dump cannot be
taken afterwards -- and this tool does the same searches on that file in seconds.

    python3 tools/xemu/ramscan.py ram.bin --text hist
    python3 tools/xemu/ramscan.py ram.bin --dword 0x5b12040
    python3 tools/xemu/ramscan.py ram.bin --text hist --refs

`--text` searches the UE2 FString form (UTF-16LE plus the NUL terminator, so `hist` does not match inside
`History`); `--ascii` searches an 8-bit NUL-terminated copy; `--dword` searches a value, which is how a hit turns
into an owner. With `--refs`, every dword-aligned pointer to a hit is walked back to the nearest address whose
first word is a known class table -- that address is an object, `UObject`'s vtable is its first field, and the
table names the class -- so the output reads "the pointer sits at +0x… inside an object of class X". A text
pattern matches *inside* a string, so `--ref-window` (default 0x40) also accepts a pointer to any even address
before the hit: when the match is not the string's first character, that address is the FString's `Data`.

Measured: a 128 MiB image (`pmemsave 0 0x8000000`) is scanned in 0.1 s for one pattern -- the whole guest, in
less time than one 64 KiB page costs over the stub, which is what makes the coarse questions cheap. `--selftest`
proves the walk on a synthetic image, because CI may not have a dump.

A dump is guest data: it stays on the desktop, is never committed, and this tool prints addresses, counts and
class names only -- the same rule as `objects.py`'s string readers.
"""
import argparse
import os
import struct
import sys
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(TOOLS))
CLASS_NAMES = os.path.join(REPO, "inventory", "xbe_class_table_names.csv")
CLASS_TABLES = os.path.join(REPO, "inventory", "xbe_class_tables.csv")


def class_names():
    """({table_va: 'Package.Class'}) from the committed artifacts, the same two `objects.py` reads."""
    names = {}
    for path, key in ((CLASS_NAMES, "class"), (CLASS_TABLES, None)):
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as handle:
            head = handle.readline().rstrip("\n").split(",")
            col = {name: i for i, name in enumerate(head)}
            for line in handle:
                row = line.rstrip("\n").split(",")
                if len(row) <= col["table_va"] or not row[col["table_va"]]:
                    continue
                if key and (len(row) <= col[key] or not row[col[key]]):
                    continue
                table = int(row[col["table_va"]], 16)
                if table not in names and (not key or row[col[key]]):
                    names[table] = row[col[key]] if key else "table %#x" % table
    return names


def find_all(blob, pattern, limit):
    """[(offset)] of every occurrence of `pattern`, up to `limit`."""
    out = []
    start = 0
    while len(out) < limit:
        at = blob.find(pattern, start)
        if at < 0:
            break
        out.append(at)
        start = at + 1
    return out


def owner_of(blob, offset, tables, depth):
    """(object_base, field_offset, class_name) for the object holding a pointer at `offset`, or None.

    A `UObject`'s first field is its vtable, which is a class table from the XBE, so walking back one word at a
    time to the nearest known table finds the object boundary without needing `GObjObjects` (which a dump taken in
    a different state may not carry the same way). `depth` bounds the walk: `Console.TypedStr` and `History[16]`
    are inside the first kilobyte of a class that declares them early.
    """
    for back in range(0, depth, 4):
        base = offset - back
        if base < 0:
            break
        table = struct.unpack_from("<I", blob, base)[0]
        if table in tables:
            return base, back, tables[table]
    return None


def selftest():
    """A synthetic image: one object, one known class table, one string, one pointer. Assert the walk names it.

    The tool's real proof is a dump, which CI cannot have (`AGENTS.md` forbids downloading the game), so the walk's
    arithmetic -- match inside the string, pointer to the string's first character, object found by its vtable -- is
    checked on bytes this function builds.
    """
    table, obj, field, text_at = 0x00500000, 0x1000, 0x120, 0x2000
    blob = bytearray(0x4000)
    struct.pack_into("<I", blob, obj, table)  # UObject's first field is its vtable
    struct.pack_into("<I", blob, obj + field, text_at)  # TypedStr.Data
    blob[text_at:text_at + 12] = " hist".encode("utf-16-le") + b"\x00\x00"
    raw = bytes(blob)

    hits = find_all(raw, "hist".encode("utf-16-le") + b"\x00\x00", 4)
    assert hits == [text_at + 2], hits  # the match is inside the string, not at its start
    refs = [r for r in find_all(raw, struct.pack("<I", text_at), 4) if r % 4 == 0]
    assert refs == [obj + field], refs
    owner = owner_of(raw, refs[0], {table: "Engine.Console"}, 0x1000)
    assert owner == (obj, field, "Engine.Console"), owner
    assert owner_of(raw, 0x3000, {table: "Engine.Console"}, 0x1000) is None  # a pointer with no object is None
    print("ok   ramscan: the ref walk names Engine.Console at +0x%x for a string %d bytes into the match"
          % (field, 2))
    return 0


def main():
    if "--selftest" in sys.argv[1:]:
        return selftest()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dump", help="RAM image written by `monitor=pmemsave` (address == file offset)")
    parser.add_argument("--text", action="append", default=[], metavar="TEXT",
                        help="search TEXT as a UE2 FString payload (UTF-16LE plus its NUL terminator); repeatable")
    parser.add_argument("--ascii", action="append", default=[], metavar="TEXT",
                        help="search TEXT as an 8-bit NUL-terminated string; repeatable")
    parser.add_argument("--dword", action="append", default=[], metavar="ADDR",
                        help="search a little-endian dword (hex); repeatable")
    parser.add_argument("--refs", action="store_true",
                        help="for each hit, also find the dword-aligned pointers to it and name the object each "
                             "one sits in")
    parser.add_argument("--owner-depth", type=lambda x: int(x, 0), default=0x1000,
                        help="how far back from a pointer to look for its object's vtable (default 0x1000)")
    parser.add_argument("--ref-window", type=lambda x: int(x, 0), default=0x40,
                        help="also accept a pointer to any even address up to N bytes before the hit: a text "
                             "pattern matches inside a string, so the FString's Data names the first character, "
                             "not the match (default 0x40)")
    parser.add_argument("--limit", type=int, default=16, help="stop after N hits per pattern (default 16)")
    parser.add_argument("--base", type=lambda x: int(x, 0), default=0,
                        help="address the dump starts at, printed in the output (default 0)")
    args = parser.parse_args()

    with open(args.dump, "rb") as handle:
        blob = handle.read()
    print("dump %s: %d KiB%s" % (args.dump, len(blob) // 1024,
                                 "" if not args.base else " from %#x" % args.base))
    patterns = [("UTF-16 %r + NUL" % t, t.encode("utf-16-le") + b"\x00\x00") for t in args.text]
    patterns += [("ASCII %r + NUL" % t, t.encode("ascii") + b"\x00") for t in args.ascii]
    patterns += [("dword %s" % d, struct.pack("<I", int(d, 0))) for d in args.dword]
    if not patterns:
        raise SystemExit("nothing to search: give --text, --ascii or --dword")

    tables = class_names() if args.refs else {}
    if args.refs and not tables:
        raise SystemExit("no class tables: %s and %s are missing" % (CLASS_NAMES, CLASS_TABLES))
    started = time.time()
    for label, pattern in patterns:
        hits = find_all(blob, pattern, args.limit)
        print("\n%s: %d hit(s)" % (label, len(hits)))
        for hit in hits:
            addr = args.base + hit
            print("  at %#010x" % addr)
            if not args.refs:
                continue
            starts = [addr - k for k in range(0, args.ref_window + 1, 2) if addr - k >= 0]
            refs = []
            for start in starts:
                for ref in find_all(blob, struct.pack("<I", start), args.limit):
                    if ref % 4 == 0:
                        refs.append((ref, start - addr))
            if not refs:
                print("    no dword-aligned pointer to it, or to the %#x before it" % args.ref_window)
                continue
            for ref, delta in refs[:args.limit]:
                owner = owner_of(blob, ref, tables, args.owner_depth)
                where = "it" if not delta else "string %#010x (%+d)" % (args.base + addr + delta, delta)
                if owner:
                    base, back, name = owner
                    print("    pointer at %#010x -> %s in object %#010x +%#05x  %s"
                          % (args.base + ref, where, args.base + base, back, name))
                else:
                    print("    pointer at %#010x -> %s, no class table in the %#x before it"
                          % (args.base + ref, where, args.owner_depth))
    print("\n%d pattern(s) in %.1fs" % (len(patterns), time.time() - started))
    return 0


if __name__ == "__main__":
    sys.exit(main())
