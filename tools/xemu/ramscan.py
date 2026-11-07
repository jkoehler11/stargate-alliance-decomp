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

`--objects` answers the other question over the same file: a census of every class table's vtable over the whole
image, each hit screened by the words after it (`object_shape`), because a whole-image hit count would call a
`mov` that mentions a table an object. `--class SUBSTR` prints the hits for the matching classes with their header
words; the screen is a screen, and `objects.py`'s array walk stays the source of a whole-game object count.
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


def string_copies(blob, needle, tables=None, limit=16, depth=0x1000, addr_for=None):
    """[(copy)] for every UTF-16 copy of `needle`, each with the object that holds it, if any.

    A short word occurs several times in an image (a class name, an ini key, a log format), so a text hit alone
    does not say which copy a game object owns -- arming a watchpoint on the first one produced "8 hits, no stop".
    A script string is an `FString` field: a four-aligned word naming its first character, then `Count` (characters
    plus the NUL) and `Max` (>= Count). That signature is what picks the field out of the copies, and `owner_of`
    then names the object from its vtable.

    `addr_for(phys)` returns the CPU-visible (linear) addresses of a physical offset: `pmemsave` writes physical
    memory, while a field holds a linear pointer, so a caller with a dump and its page tables passes
    `vamap.linear_for`. Default: identity.
    """
    tables = tables or {}
    addr_for = addr_for or (lambda phys: [phys])
    out = []
    for hit in find_all(blob, needle, limit):
        start = hit
        # The match is inside the string: an FString's Data names the first character, so walk back to it.
        while start >= 2 and 0x20 <= blob[start - 2] <= 0x7E and blob[start - 1] == 0:
            start -= 2
        chars, at = 0, start
        while at + 1 < len(blob) and 0x20 <= blob[at] <= 0x7E and blob[at + 1] == 0:
            chars += 1
            at += 2
        copy = {"match": hit, "phys": start, "chars": chars, "addresses": addr_for(start),
                "field": None, "owner": None}
        for address in copy["addresses"]:
            pos = blob.find(struct.pack("<I", address))
            while pos >= 0:
                if pos % 4 == 0 and pos + 8 < len(blob):
                    count, capacity = struct.unpack_from("<II", blob, pos + 4)
                    if count == chars + 1 and count <= capacity <= 0x400:
                        copy["field"] = (pos, address, count, capacity)
                        copy["owner"] = owner_of(blob, pos, tables, depth)
                        break
                pos = blob.find(struct.pack("<I", address), pos + 1)
            if copy["field"]:
                break
        out.append(copy)
    return out


def selftest():
    """A synthetic image: one object, one known class table, one string, one pointer. Assert the walk names it.

    The tool's real proof is a dump, which CI cannot have (`AGENTS.md` forbids downloading the game), so the walk's
    arithmetic -- match inside the string, pointer to the string's first character, object found by its vtable -- is
    checked on bytes this function builds.
    """
    table, obj, field, text_at = 0x00500000, 0x1000, 0x120, 0x2000
    blob = bytearray(0x4000)
    struct.pack_into("<III", blob, obj, table, 0x8D34, 0)  # vtable, ObjectInternalInteger, a null pointer
    struct.pack_into("<I", blob, obj + field, text_at)  # TypedStr.Data
    blob[text_at:text_at + 12] = " hist".encode("utf-16-le") + b"\x00\x00"
    struct.pack_into("<IIII", blob, 0x3000, table, 0, 0, 0)  # a bare mention of the table: not a UObject
    raw = bytes(blob)

    hits = find_all(raw, "hist".encode("utf-16-le") + b"\x00\x00", 4)
    assert hits == [text_at + 2], hits  # the match is inside the string, not at its start
    refs = [r for r in find_all(raw, struct.pack("<I", text_at), 4) if r % 4 == 0]
    assert refs == [obj + field], refs
    owner = owner_of(raw, refs[0], {table: "Engine.Console"}, 0x1000)
    assert owner == (obj, field, "Engine.Console"), owner
    assert owner_of(raw, 0x3800, {table: "Engine.Console"}, 0x400) is None  # no vtable in reach: no owner
    assert census(raw, {table: "Engine.Console"}, 8, None) == [("Engine.Console", table, [obj, 0x3000])]
    assert object_shape(raw, obj) == "object" and object_shape(raw, 0x3000) == "not-object"

    # Which copy is the object's? A decoy string of the same text, with no FString field, sorts first by address;
    # the resolver has to hand back the field's copy and name the object, and must not mistake the decoy for one.
    decoy = 0x100
    blob[decoy:decoy + 12] = " hist".encode("utf-16-le") + b"\x00\x00"
    struct.pack_into("<II", blob, obj + field + 4, 6, 6)  # the FString's Count/Max beside its Data
    raw = bytes(blob)
    copies = string_copies(raw, "hist".encode("utf-16-le") + b"\x00\x00", {table: "Engine.Console"}, 4, 0x1000)
    assert [copy["phys"] for copy in copies] == [decoy, text_at], copies
    assert copies[0]["field"] is None and copies[0]["owner"] is None, copies[0]  # the decoy names no object
    assert copies[1]["field"] == (obj + field, text_at, 6, 6), copies[1]
    assert copies[1]["owner"] == (obj, field, "Engine.Console"), copies[1]
    # `pmemsave` is physical and the field holds a linear pointer: identity only holds when the two agree.
    moved = string_copies(raw, "hist".encode("utf-16-le") + b"\x00\x00", {table: "Engine.Console"}, 4, 0x1000,
                          addr_for=lambda phys: [phys + 0x1000])
    assert [copy["field"] for copy in moved] == [None, None], moved  # no field names the shifted address
    print("ok   ramscan: the ref walk names Engine.Console at +0x%x for a string %d bytes into the match, the "
          "census separates an object from a table mentioned in code, and the resolver picks the FString's copy "
          "over a decoy of the same text" % (field, 2))
    return 0


def object_shape(blob, hit):
    """'object' when the words after a class table look like a `UObject` header, else 'not-object'.

    Measured header at the Console default, `0x02661b00`: `vtable, 0x8d34, 0x230eaf0, 0x1ffc480, 0, 0xffffffff` --
    a small integer then two in-image pointers, so the third word is *not* a flag word (the earlier three-word rule
    called a real object 'not-object' because of it). The screen is therefore: vtable, a small positive
    `ObjectInternalInteger`, then two words that are each 0 or an address inside the image. Code that merely
    mentions a class table (`mov eax, 0x50cb80`) fails on the integer, which is what the census needs.
    """
    if hit + 16 > len(blob):
        return "not-object"
    integer, second, third = struct.unpack_from("<III", blob, hit + 4)
    if not 0 < integer <= 0x10000:
        return "not-object"
    for word in (second, third):
        if word and not 0x10000 <= word < len(blob):
            return "not-object"
    return "object"


def census(blob, tables, limit, only):
    """[(class_name, table, [four-aligned occurrences])] -- every class table's vtable, over the whole image.

    `objects.py` can only see what `GObjObjects` points at (295 pages of 18,880 KiB in the measured states); this
    walks the image itself, so an object the array has dropped still shows up here, and a class with *no* hit is a
    statement about all 128 MiB rather than about the array's pages.
    """
    if only:
        wanted = {table: name for table, name in tables.items() if only.lower() in name.lower()}
        if not wanted:
            raise SystemExit("no class table matches %r" % only)
    else:
        wanted = tables
    out = []
    for table, name in sorted(wanted.items(), key=lambda kv: kv[1]):
        hits = [h for h in find_all(blob, struct.pack("<I", table), limit) if h % 4 == 0]
        if hits:
            out.append((name, table, hits))
    return out


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
    parser.add_argument("--objects", action="store_true",
                        help="census every class table's vtable over the whole image instead of searching patterns: "
                             "an object the object array has dropped still shows up, and a class with no hit is a "
                             "statement about all of RAM rather than about the array's pages")
    parser.add_argument("--class", dest="class_name", metavar="SUBSTR",
                        help="with --objects: only classes whose name contains SUBSTR, printing each hit's header "
                             "words and whether it looks like a UObject")
    parser.add_argument("--objects-limit", type=int, default=40,
                        help="with --objects: print at most N classes (default 40)")
    args = parser.parse_args()

    with open(args.dump, "rb") as handle:
        blob = handle.read()
    print("dump %s: %d KiB%s" % (args.dump, len(blob) // 1024,
                                 "" if not args.base else " from %#x" % args.base))
    if args.objects or args.class_name:
        tables = class_names()
        if not tables:
            raise SystemExit("no class tables: %s and %s are missing" % (CLASS_NAMES, CLASS_TABLES))
        rows = census(blob, tables, args.limit, args.class_name)
        total = sum(len(hits) for _, _, hits in rows)
        print("%d class table(s), %d four-aligned occurrence(s) in %d KiB"
              % (len(tables), total, len(blob) // 1024))
        rows.sort(key=lambda row: (-len(row[2]), row[0]))
        if args.class_name:
            for name, table, hits in rows:
                print("\n%s  table %#010x  %d hit(s)" % (name, table, len(hits)))
                for hit in hits:
                    words = " ".join("%#x" % struct.unpack_from("<I", blob, hit + k)[0]
                                     for k in range(0, 0x18, 4))
                    print("  %#010x  %-10s %s" % (args.base + hit, object_shape(blob, hit), words))
        else:
            for name, table, hits in rows[:args.objects_limit]:
                print("  %-34s %#010x  %3d  %s%s"
                      % (name, table, len(hits), " ".join("%#x" % (args.base + h) for h in hits[:5]),
                         " ..." if len(hits) > 5 else ""))
        return 0
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
