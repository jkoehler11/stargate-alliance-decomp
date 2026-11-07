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

**A dump is physical and a pointer is linear**, so on this build a raw dword comparison finds nothing: a field
holding a heap string may point at `0xd0xxxxxx` while the bytes it names sit at physical `0x06xxxxxx`. `--cr3`
(given with `--refs`) translates each candidate string address through the page tables (`vamap.linear_for`) and
searches for *both* forms, which is what turns "10 copies of `hello`, no pointer to any of them" into an owner.

Measured: a 128 MiB image (`pmemsave 0 0x8000000`) is scanned in 0.1 s for one pattern -- the whole guest, in
less time than one 64 KiB page costs over the stub, which is what makes the coarse questions cheap. `--selftest`
proves the walk on a synthetic image, because CI may not have a dump.

A dump is guest data: it stays on the desktop, is never committed, and this tool prints addresses, counts and
class names only -- the same rule as `objects.py`'s string readers.

`--objects` answers the other question over the same file: a census of every class table's vtable over the whole
image, each hit screened by the words after it (`object_shape`), because a whole-image hit count would call a
`mov` that mentions a table an object. `--class SUBSTR` prints the hits for the matching classes with their header
words; the screen is a screen, and `objects.py`'s array walk stays the source of a whole-game object count.

**A script class carries its ancestor's table, and that is why `--objects` cannot see one (`--headers`).** A class
the build did not compile to C++ (`ucpp`) gets no table of its own when its package loads: `GameHud extends
Interaction` and `NotificationManager extends Object` are plain script classes, so their objects' first word is the
*ancestor's* table -- `Interaction`'s `0x0050c880`, which the census does report, for 19 objects that it can only
call "Interaction". Asking the image the other way settles it: group every object-shaped header by its first word
and test each unknown word for a slot block (a run of function addresses). Measured on a loaded `M00`: of 5,485
distinct anonymous words, **7** name a block with six or more of its first eight slots in `.text`, and those seven
are UTF-16 string data (`GSListObject...`); the rest are zeroes, small integers and stack words that pass the shape
screen by accident. So the resolver is not a table enumeration -- there is nothing to enumerate -- it is
`--fields`, which reads one object's pointer fields and names each target by the table the target carries.
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
OBJECT_INTEGER_MAX = 0x100000   # `ObjectInternalInteger` is a serial number: 0x8d34 at the title, 0xbb0d in a mission
CODE_LO, CODE_HI = 0x11000, 0x4190FC   # `.text` in this build's section table (VA 0x11000, vsize 0x4080fc)


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


def load(name):
    """A sibling tool by module name -- `vamap` is only needed for `--cr3`."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, os.path.join(TOOLS, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def forward_pages(blob, cr3):
    """{linear page base: physical page base} -- one pass over the page tables, in the direction a field needs.

    `linear_pages` answers "which linear addresses name this physical page" (for a `--refs` window); reading a field
    is the other direction, one lookup per field, so it gets its own map rather than an inversion of that one.
    """
    vamap = load("vamap")
    pages = {}
    for index, pde in vamap.directory(blob, cr3):
        if not pde & 1:
            continue
        base = index << 22
        if pde & 0x80:
            start = pde & 0xFFC00000
            for page in range(start, start + 0x400000, 0x1000):
                pages[base | (page - start)] = page
        else:
            table = pde & 0xFFFFF000
            for slot in range(1024):
                pte = struct.unpack_from("<I", blob, table + slot * 4)[0]
                if pte & 1:
                    pages[base | (slot << 12)] = pte & 0xFFFFF000
    return pages


def physical_for(pages, va):
    """The dump address of a linear address, or None when the page is not mapped."""
    page = pages.get(va & 0xFFFFF000)
    return None if page is None else page | (va & 0xFFF)


def dword(blob, at):
    """One little-endian word, or None when it would leave the image."""
    return struct.unpack_from("<I", blob, at)[0] if 0 <= at <= len(blob) - 4 else None


def slots_look_like_a_table(blob, pages, table, count=8, need=6):
    """How many of `table`'s first `count` slots are code addresses -- a class table is a run of them.

    A table is a vtable: its slots point into `.text` (or are `0` for a slot this build never fills), so a block whose
    first eight words are mostly code is a table and one that holds text or counts is not. The range matters: a
    UTF-16 word like `0x00680020` (`"h"` then a NUL) sits *below* the image end and would pass a "somewhere in RAM"
    test, so the bound is `.text` itself (`0x11000`-`0x4190fc`, from `tools/xbe.py … sections`), not the load address.
    """
    base = physical_for(pages, table)
    if base is None:
        return 0
    hits = 0
    for slot in range(count):
        value = dword(blob, base + 4 * slot)
        if value is not None and CODE_LO <= value < CODE_HI:
            hits += 1
    return hits


def fields(blob, base, span, tables, pages):
    """[(offset, value, target, class_name)] -- every field of the object at `base` that names another object.

    `base` is a dump (physical) address; a field holds a *linear* one, so `pages` translates it. A field names an
    object when the target's first word is a *mapped address* and the words after it pass `object_shape` -- the same
    screen `--objects` applies to the image, applied to one object -- which is what keeps a pointer to a string, to a
    stack slot or to code out of the report. The class comes from the target's own first word, so a class the
    inventory does not know is reported as such instead of being dropped: that is how a `GameHud` would appear if it
    carried a table of its own.
    """
    out = []
    for offset in range(0, span, 4):
        value = dword(blob, base + offset)
        if value is None or value < 0x1000:
            continue
        target = physical_for(pages, value)
        if target is None or target + 0x18 > len(blob):
            continue
        if object_shape(blob, target) != "object":
            continue
        table = struct.unpack_from("<I", blob, target)[0]
        name = tables.get(table)
        if name is None:
            if physical_for(pages, table) is None:
                continue                     # not an address in this guest: a count, a flag, a string pointer
            name = "table %#010x, not in the inventory" % table
        out.append((offset, value, target, name))
    return out


def header_census(blob, tables):
    """(named, anonymous) -- every object-shaped header in the image, grouped by the table its first word names.

    `--objects` asks "where do these 492 tables appear"; this asks the image "what tables does it actually carry",
    which is the question a class with no inventory table raises. A header only needs `object_shape`, so an anonymous
    first word is a candidate and not a class table: the ones that survive `slots_look_like_a_table` are tables, and
    the rest are the shape screen's false positives (a zero, a small integer, a stack word).
    """
    counts, first = {}, {}
    for at in range(0x10000, len(blob) - 0x18, 4):
        if object_shape(blob, at) != "object":
            continue
        table = struct.unpack_from("<I", blob, at)[0]
        counts[table] = counts.get(table, 0) + 1
        first.setdefault(table, at)
    named = {t: c for t, c in counts.items() if t in tables}
    anonymous = {t: c for t, c in counts.items() if t not in tables}
    return named, anonymous, first


def linear_pages(blob, cr3):
    """{physical page: [linear page base, ...]} -- one pass over the page tables, so a ref search is not 33 lookups.

    `vamap.linear_for` walks the whole directory for a single address, which is right for one translation and far
    too slow for a window of 33 candidate starts per hit; this builds the inverse map once (137 present PDEs, 63 of
    them 4 MiB) and then every lookup is a dictionary hit.
    """
    pages = {}
    for linear, physical in forward_pages(blob, cr3).items():
        pages.setdefault(physical, []).append(linear)
    return pages


def pointer_forms(pages, addr):
    """Every value a field would hold to name physical `addr`: the physical form, then every linear alias."""
    forms = [addr]
    if pages:
        for linear in pages.get(addr & 0xFFFFF000, ()):
            forms.append(linear | (addr & 0xFFF))
    return forms


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
    struct.pack_into("<IIIIII", blob, obj, table, 0x8D34, 0, 0, 0, 0xFFFFFFFF)  # the measured header
    struct.pack_into("<I", blob, obj + field, text_at)  # TypedStr.Data
    blob[text_at:text_at + 12] = " hist".encode("utf-16-le") + b"\x00\x00"
    struct.pack_into("<IIIIII", blob, 0x3000, table, 0, 0, 0, 0, 0xFFFFFFFF)  # a bare mention: no integer
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
    # The integer is a serial number, not a flag: a mission's StargatePlayer carries 0xbb0d, which the first bound
    # (0x10000) rejected -- so the screen has to accept it, and still reject a number too large to be one.
    struct.pack_into("<IIIIII", blob, 0x3400, table, 0xBB0D, 0x7E57640, 0x9335D60, 0, 0xFFFFFFFF)
    raw = bytes(blob)
    assert object_shape(raw, 0x3400) == "object", "a late-created object (internal integer 0xbb0d) is an object"
    assert object_shape(raw, 0x3400 - 0) == "object" and object_shape(raw, obj) == "object"
    blob2 = bytearray(raw)
    struct.pack_into("<I", blob2, 0x3404, 0x7FFFFFFF)
    assert object_shape(bytes(blob2), 0x3400) == "not-object", "an integer that size is code, not an object"

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

    # A field that names an object is read through the translation map, and one that names a string is not: the target
    # has to carry a table *and* pass `object_shape`, and that screen is what keeps a `TypedStr` out of `--fields`.
    other = 0x2800
    struct.pack_into("<IIIIII", blob, other, table, 0x8D35, 0, 0, 0, 0xFFFFFFFF)
    struct.pack_into("<I", blob, obj + 0x140, other)                  # a field naming an object
    raw = bytes(blob)
    identity = {page: page for page in range(0, 0x4000, 0x1000)}      # linear == physical, for the test image
    rows = fields(raw, obj, 0x200, {table: "Engine.Console"}, identity)
    assert [row[:3] for row in rows] == [(0x140, other, other)], rows
    assert rows[0][3] == "Engine.Console", rows
    # A table is a run of code slots, and the block the shape screen guesses wrong is not one: on a loaded level seven
    # anonymous words name UTF-16 string data and every other anonymous word names nothing at all.
    code = 0x3000
    struct.pack_into("<IIIIIIII", blob, code, *[0x20000 + 4 * slot for slot in range(8)])
    raw = bytes(blob)
    assert slots_look_like_a_table(raw, identity, code) == 8
    assert slots_look_like_a_table(raw, identity, text_at) == 0, "a string block is not a class table"
    assert slots_look_like_a_table(raw, identity, 0x7FFF0000) == 0, "an unmapped table names no slots at all"
    print("ok   ramscan: the ref walk names Engine.Console at +0x%x for a string %d bytes into the match, the "
          "census separates an object from a table mentioned in code, the resolver picks the FString's copy "
          "over a decoy of the same text, and `--fields` names only the field that holds an object"
          % (field, 2))
    return 0


def object_shape(blob, hit, mapped=None):
    """'object' when the words after a class table look like a `UObject` header, else 'not-object'.

    Measured header at the Console default, `0x02661b00`: `vtable, 0x8d34, 0x230eaf0, 0x1ffc480, 0, 0xffffffff` --
    a small integer then two in-image pointers, so the third word is *not* a flag word (the earlier three-word rule
    called a real object 'not-object' because of it). The screen is therefore: vtable, a *plausible*
    `ObjectInternalInteger`, then two words that are each 0 or an address inside the guest. Code that merely mentions
    a class table (`mov eax, 0x50cb80`) fails on the integer, which is what the census needs.

    Two bounds are measured, and both of them were wrong once:

    * the integer is a **serial number**, not a flag. It reads `0x8d34` at the title and `0xbb0d` (47,885) on a
      loaded level -- the `StargatePlayer` object at physical `0x05aaf800` -- so the first bound (`0x10000`) made
      every mission class read zero. It is now `OBJECT_INTEGER_MAX`.
    * the words after the integer are **not** two in-image pointers, which is what an earlier revision claimed from
      the title's Console default (`0x230eaf0`, `0x1ffc480`). That object names `0x07e57640` and `0x09335d60`, one
      of them above the 128 MiB the dump covers and neither of them mapped, so a range test on them rejects a real
      object. What *does* separate the six hits on this table is the pair at +0x10: a zero, then `0xffffffff` --
      measured on all six (`0x05aaf800` has it, the five code/data mentions do not), and on the Console default.
    """
    if hit + 24 > len(blob):
        return "not-object"
    integer, second, third = struct.unpack_from("<III", blob, hit + 4)
    if not 0 < integer <= OBJECT_INTEGER_MAX:
        return "not-object"
    zero, tag = struct.unpack_from("<II", blob, hit + 16)
    if zero != 0 or tag != 0xFFFFFFFF:
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
    parser.add_argument("--cr3", type=lambda x: int(x, 0), default=None, metavar="ADDR",
                        help="page directory (`info registers`, measured 0xf000): adds the linear forms of a "
                             "pointer to --refs, and lets --objects accept header pointers into the mapped heap")
    parser.add_argument("--objects", action="store_true",
                        help="census every class table's vtable over the whole image instead of searching patterns: "
                             "an object the object array has dropped still shows up, and a class with no hit is a "
                             "statement about all of RAM rather than about the array's pages")
    parser.add_argument("--class", dest="class_name", metavar="SUBSTR",
                        help="with --objects: only classes whose name contains SUBSTR, printing each hit's header "
                             "words and whether it looks like a UObject")
    parser.add_argument("--objects-limit", type=int, default=40,
                        help="with --objects: print at most N classes (default 40)")
    parser.add_argument("--fields", type=lambda x: int(x, 0), default=None, metavar="ADDR",
                        help="every pointer field of the object at ADDR (a dump address, given physically or "
                             "linearly) that names another object, with the class the target's table gives; needs "
                             "--cr3, because a field holds a linear address and the dump is physical")
    parser.add_argument("--span", type=lambda x: int(x, 0), default=0x1000,
                        help="with --fields: how many bytes of the object to read (default 0x1000)")
    parser.add_argument("--headers", action="store_true",
                        help="group every object-shaped header in the image by the table its first word names, and "
                             "test each word the inventory does not know for a table-shaped slot block -- the "
                             "question a class with no inventory table raises")
    parser.add_argument("--headers-limit", type=int, default=12, help="with --headers: rows per list (default 12)")
    args = parser.parse_args()

    with open(args.dump, "rb") as handle:
        blob = handle.read()
    print("dump %s: %d KiB%s" % (args.dump, len(blob) // 1024,
                                 "" if not args.base else " from %#x" % args.base))
    fwd = pages = mapped = None
    if args.cr3 is not None:
        fwd = forward_pages(blob, args.cr3)
        pages = {}
        for linear, physical in fwd.items():
            pages.setdefault(physical, []).append(linear)
        mapped = lambda va: bool(fwd.get(va & 0xFFFFF000))  # noqa: E731 -- a dictionary hit per header word
    if args.fields is not None:
        if fwd is None:
            raise SystemExit("--fields needs --cr3: a field holds a linear address and the dump is physical")
        tables = class_names()
        if not tables:
            raise SystemExit("no class tables: %s and %s are missing" % (CLASS_NAMES, CLASS_TABLES))
        base = args.fields
        if object_shape(blob, base) != "object":        # given linearly (as the notes quote it) rather than physically
            translated = physical_for(fwd, base)
            if translated is None or object_shape(blob, translated) != "object":
                raise SystemExit("%#x is not an object header in this dump, physically or through CR3 %#x"
                                 % (base, args.cr3))
            print("object %#x is linear; its dump address is %#x" % (base, translated))
            base = translated
        table = struct.unpack_from("<I", blob, base)[0]
        print("object %#010x, table %#010x  %s"
              % (base, table, tables.get(table, "not in the inventory")))
        rows = fields(blob, base, args.span, tables, fwd)
        for offset, value, target, name in rows:
            print("  +%#06x -> %#010x (dump %#010x)  %s" % (offset, value, target, name))
        print("%d field(s) naming an object, over %d class(es)%s"
              % (len(rows), len({name for _, _, _, name in rows}),
                 "" if rows else ": no field of this object names another object"))
        return 0
    if args.headers:
        tables = class_names()
        if not tables:
            raise SystemExit("no class tables: %s and %s are missing" % (CLASS_NAMES, CLASS_TABLES))
        named, anonymous, first = header_census(blob, tables)
        print("%d object-shaped header(s) over %d KiB" % (sum(named.values()) + sum(anonymous.values()),
                                                          len(blob) // 1024))
        print("%d on a table the inventory names (%d distinct of %d), %d on a word it does not (%d distinct)"
              % (sum(named.values()), len(named), len(tables), sum(anonymous.values()), len(anonymous)))
        for table, count in sorted(named.items(), key=lambda kv: (-kv[1], kv[0]))[:args.headers_limit]:
            print("  %-34s %#010x  %3d  first %#010x" % (tables[table], table, count, first[table]))
        if fwd is None:
            print("no --cr3: the anonymous words are not tested for a slot block")
        else:
            scored = [(slots_look_like_a_table(blob, fwd, table), table, count)
                      for table, count in anonymous.items()]
            good = sorted([row for row in scored if row[0] >= 6], key=lambda row: (-row[2], row[1]))
            print("%d of %d anonymous word(s) name a block with six or more of its first eight slots in .text"
                  % (len(good), len(anonymous)))
            for hits, table, count in good[:args.headers_limit]:
                print("  table %#010x  %3d object(s)  first %#010x  code %d/8"
                      % (table, count, first[table], hits))
        return 0
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
                    print("  %#010x  %-10s %s" % (args.base + hit, object_shape(blob, hit, mapped), words))
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
                for value in pointer_forms(pages, start - args.base):
                    for ref in find_all(blob, struct.pack("<I", value), args.limit):
                        if ref % 4 == 0:
                            refs.append((ref, start - addr, value))
            if not refs:
                print("    no dword-aligned pointer to it, or to the %#x before it%s"
                      % (args.ref_window, "" if args.cr3 else " (no --cr3: physical forms only)"))
                continue
            for ref, delta, value in refs[:args.limit]:
                owner = owner_of(blob, ref, tables, args.owner_depth)
                where = "it" if not delta else "string %#010x (%+d)" % (args.base + addr + delta, delta)
                if pages is not None and value != start - args.base:
                    where += ", named as linear %#010x" % value
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
