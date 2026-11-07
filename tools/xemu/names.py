#!/usr/bin/env python3
"""Locate `GNames` -- the engine's name table -- in a running guest, and read names out of it (PLAN 4.3, U15).

`UObject::Name` is an `FName`, an index into a global table, so an object's class and a `UFunction`'s identity are
unreadable until that table is found. In UE2 the name system is an array of `FNameEntry*` with a count and a capacity
(`Data`, `Num`, `Max`), and entries are `+0x00` index / `+0x04` flags / `+0x08` hash chain / `+0x0c` UTF-16 name --
the shape the property work already measured from `FName::StaticInit` (`0x0005c080`, 4,096-slot hash at
`0x006ba0c8`, allocation `2*len+0xe`, `docs/NATIVE_PROPERTIES.md`).

`.data` is mostly uninitialised in the image (`0x00226bd4` virtual against `0x00034b9c` file bytes), so the array
reads as zeros on disk and exists only in the guest. `--find` therefore reads `.data` out of a booted guest and
scores every aligned `(Data, Num, Max)` triple by shape -- and then *validates* it, because a triple alone is a
guess: entry `i` must carry index `i` (the table is positional) and its name must be printable text, and indices
`0..16` must be the seventeen reserved names the XBE initialiser registers (`0` `None`, `1` `ByteProperty` ...
`16` `PointerProperty`, `inventory/property_types.csv`). Seventeen exact strings at seventeen fixed indices is not
a coincidence a scan can produce.

`--verify` closes the loop the other way, and measures two header fields rather than one. A `UClass` object's own
vtable is the `Class` table, so a header dword that points at an object on that table *is* `UObject::Class` -- no
layout assumed. The name offset is then the dword of that class object which resolves, through the name table, to the
class name the instance's vtable already carries: two independent committed artifacts agreeing over hundreds of
objects. **Measured (2026-11-07): `Class` `+0x24`, `Name` `+0x20`** (an `FName` index), with `Outer` `+0x18` as
`owner.py` measured it -- so the header reads `Outer`, `Name`, `Class` in that order.

`--census` uses the pair: it names every object's class from its own `Class` pointer, which is the only way to name a
*script* class -- `GameHud extends Interaction` carries `Interaction`'s table, so no vtable names it.

**An entry can end on a page boundary, so the read has to stop there.** Entries are allocated `2*len+0xe` bytes,
which fills a page exactly: index 5's block runs `0x95ffc0..0x95ffff` and the next page is unmapped, so the fixed
206-byte `FNameEntry` read ran into nothing and failed whole -- the fault that left five `Core` classes unlabelled in
the first full census and 21 of the name array's first 64 slots unreadable in `--find`. `read_prefix` reads one page
at a time and keeps what it got, and `entry` parses that prefix (measured 2026-11-07;
`--selftest` reproduces it without a guest).

Usage, against a guest booted with `-gdb tcp::PORT` (`drive.py --gdb=PORT --linger=SECS` keeps one up):

    # where is the array, and does the shape validate?
    python3 tools/xemu/names.py --port 12345 --xbe $SG1_DUMP/default.xbe --find

    # the reserved block, and any index at all (an FName index from a dump, a loader record, a header field)
    python3 tools/xemu/names.py --port 12345 --xbe ... --at 0x006c8a10 --index 0-16
    python3 tools/xemu/names.py --port 12345 --xbe ... --at 0x006c8a10 --index 357,2014,308,17040,2757,6823,2427

    # measure UObject::Name and UObject::Class, and cross-check against the class defaults
    python3 tools/xemu/names.py --port 12345 --xbe ... --at 0x006be0d8 --verify

    # name every object's class from its Class pointer (the read a vtable census cannot do)
    python3 tools/xemu/names.py --port 12345 --xbe ... --at 0x006be0d8 --census --top 20

    # the same walk, but keep every row: one walk then answers any class question
    python3 tools/xemu/names.py --port 12345 --xbe ... --at 0x006be0d8 --census --csv classes.csv

**Measured (2026-11-07), so start here.** In a guest at the title the array is `0x006be0d8`
(`data=0x01f30000` num=34,800 max=37,732), immediately after the name system's `Initialized` dword at
`0x006be0c8`. `--find` is kept because its negative is the measurement: only `GObjObjects` resolves out of
4,496 shape-passing triples, since 21 of the name array's first 64 slot pointers fail to read back and a
prefilter counts a swallowed retry as a null.

Names are engine identifiers (class, function and property names), which this project already publishes; game state
lives in FStrings, not in this table, and nothing here writes a file.
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
OBJECTS_PY = os.path.join(HERE, "objects.py")
PROPERTY_TYPES = os.path.join(REPO, "inventory", "property_types.csv")

GNAMES_MIN = 128             # the initialiser registers 325 names before the title, so fewer is not this table
GNAMES_MAX = 1 << 20
OBJ_ARRAY = 0x006c21b0       # measured `UObject::GObjObjects` (docs/XEMU.md)
NAME_OFF = 0x20              # measured `UObject::Name`, an FName index, by `--verify` (2026-11-07)
CLASS_OFF = 0x24             # measured `UObject::Class`, a UClass object, by `--verify` (2026-11-07)
HASH_TABLE = 0x006ba0c8      # the 4,096-slot name hash FName::StaticInit clears (docs/NATIVE_PROPERTIES.md)
NAME_AT = 0x0c               # FNameEntry: index / flags / chain / UTF-16 name
NAME_MAX = 96


def load(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


objects = load("xemu_objects", OBJECTS_PY)
Guest = objects.Guest
plausible_pointer = objects.plausible_pointer

# `objects.py`'s bound is the guest's 128 MiB, but the stub reads *linear* addresses and this guest's object array
# holds 2,835 entries just above that (0x08000000..0x10000000) which read back as ordinary objects -- `StaticMesh`
# assets and a `Class` at 0x082ff600 among them. A census on the physical bound drops them silently, and they are
# where several live gameplay objects sit, so the object array gets the wider window (measured 2026-11-07).
OBJECT_HI = 0x10000000


def plausible_object(value):
    return objects.RAM_LO <= value < OBJECT_HI and value % 4 == 0


def read_section(guest, va, size, chunk=0x10000):
    """Read a whole section out of the guest in stub-sized chunks."""
    out = bytearray()
    done = 0
    while done < size:
        take = min(chunk, size - done)
        out += guest.read(va + done, take)
        done += take
    return bytes(out)


def read_prefix(guest, va, size, page=0x10000):
    """Read up to `size` bytes at `va`, one page per request, and stop where memory stops.

    xemu's stub serves mapped pages only: a request that crosses into an unmapped one fails *whole*, and
    `Guest.read` turns that into `IOError`, which every caller here reads as "this entry is not there". An
    `FNameEntry` is allocated `2*len+0xe` bytes, so its block can end exactly on a page boundary -- index 5's
    runs `0x95ffc0..0x95ffff` -- and the fixed 206-byte read then answered nothing at all. Read the first page's
    share, keep it, and treat a later failure as the end of the block: a truncated prefix is parsed, and a
    prefix with no NUL-terminated name still reads as absent (`wide_name`).
    """
    out = bytearray()
    while len(out) < size:
        at = va + len(out)
        want = min(size - len(out), page - (at % page))
        while True:
            try:
                out += guest.read(at, want)
                break
            except IOError:
                if not out:
                    if want <= 4:
                        raise
                    want = max(4, want // 2)
                elif want <= 4:
                    return bytes(out)
                else:
                    want = max(4, want // 2)
    return bytes(out)


def wide_name(raw, off=NAME_AT, limit=NAME_MAX):
    """The UTF-16 name at `off`, or None when the bytes are not a terminated printable identifier."""
    chars = []
    pos = off
    while pos + 1 < len(raw) and len(chars) < limit:
        ch = struct.unpack_from("<H", raw, pos)[0]
        if ch == 0:
            break
        if not (0x20 <= ch < 0x7f):
            return None
        chars.append(chr(ch))
        pos += 2
    else:
        return None
    if pos >= len(raw) - 1 and len(chars) >= limit:
        return None
    if not chars:
        return None
    return "".join(chars)


def entry(guest, ptr, want_name=True):
    """(index, flags, chain, name) for the FNameEntry at `ptr`, or None when it does not read as one."""
    if not plausible_pointer(ptr):
        return None
    try:
        raw = read_prefix(guest, ptr, NAME_AT + 2 * NAME_MAX + 2)
    except IOError:
        return None
    if len(raw) < NAME_AT + 4:
        return None
    idx, flags, chain = struct.unpack_from("<III", raw, 0)
    text = wide_name(raw) if want_name else ""
    if text is None:
        return None
    return idx, flags, chain, text


def reserved_names():
    """{index: name} for the 17 reserved registrations, from the committed artifact."""
    out = {}
    if os.path.exists(PROPERTY_TYPES):
        with open(PROPERTY_TYPES, "r", encoding="utf-8") as fh:
            head = fh.readline().rstrip("\n").split(",")
            col = {name: i for i, name in enumerate(head)}
            for line in fh:
                row = line.rstrip("\n").split(",")
                if len(row) <= max(col["name_index"], col["name"]):
                    continue
                try:
                    out[int(row[col["name_index"]])] = row[col["name"]]
                except ValueError:
                    pass
    return out


def candidates(blob, base):
    """[(addr, data, num, max)] for every 4-aligned TArray-shaped triple in the blob."""
    out = []
    for off in range(0, len(blob) - 12, 4):
        data, num, cap = struct.unpack_from("<III", blob, off)
        if not (GNAMES_MIN <= num <= GNAMES_MAX):
            continue
        if not (num <= cap <= num + (1 << 20)):
            continue
        if not plausible_pointer(data):
            continue
        out.append((base + off, data, num, cap))
    return out


def score(guest, data, num, take=48, reserved=None):
    """(index_ok, name_ok, checked, {slot: name}) for one candidate, reading its first `take` entries.

    The table is positional: `Names[i]->Index == i`. A candidate that fails that on any slot it can read is not
    the name array, so the scan can stop on the first three it checks instead of paying for all 48.
    """
    take = min(num, take)
    try:
        raw = guest.read(data, take * 4)
    except IOError:
        return 0, 0, 0, {}
    ptrs = struct.unpack("<%dI" % take, raw)
    good_idx = good_name = checked = 0
    sample = {}
    for slot, ptr in enumerate(ptrs):
        if not plausible_pointer(ptr):
            continue
        got = entry(guest, ptr)
        checked += 1
        if got is None:
            continue
        idx, _flags, _chain, text = got
        if idx == slot:
            good_idx += 1
            good_name += 1
            sample[slot] = text
        if checked >= 8 and good_idx < checked - 2:
            break
    return good_idx, good_name, checked, sample


def reserved_hits(guest, data, reserved, limit=17):
    """(matched, checked, [(index, expected, got)]) against the 17 reserved names."""
    rows = []
    matched = checked = 0
    for idx in sorted(reserved)[:limit]:
        try:
            ptr = guest.u32(data + idx * 4)
        except IOError:
            continue
        got = entry(guest, ptr)
        checked += 1
        text = got[3] if got else None
        rows.append((idx, reserved[idx], text))
        if text == reserved[idx]:
            matched += 1
    return matched, checked, rows


def cmd_find(guest, sections, args):
    reserved = reserved_names()
    print("reserved names from inventory/property_types.csv: %d (expect %s at 0, %s at 1, %s at 16)"
          % (len(reserved), reserved.get(0), reserved.get(1), reserved.get(16)))
    for name in args.section:
        try:
            va, vsize = objects.section(sections, name)
        except SystemExit:
            print("  section %-8s absent from the image" % name)
            continue
        blob = read_section(guest, va, vsize)
        zero = blob.count(0)
        print("\nsection %-8s va=%#010x size=%#x  %d bytes read, %d zero (%.1f%%)"
              % (name, va, vsize, len(blob), zero, 100.0 * zero / max(1, len(blob))))
        cands = candidates(blob, va)
        print("  %d triple(s) pass the (Data, Num, Max) shape" % len(cands))
        scored = []
        for addr, data, num, cap in cands:
            gi, gn, checked, sample = score(guest, data, num, reserved=reserved)
            if checked == 0 or gi < checked // 2:
                continue
            scored.append((gi, gn, checked, addr, data, num, cap, sample))
        scored.sort(key=lambda row: (-row[0], row[5]))
        if not scored:
            print("  none of them reads as a positional name array (entry i does not carry index i)")
        for gi, gn, checked, addr, data, num, cap, sample in scored[:args.top]:
            rh, rc, rows = reserved_hits(guest, data, reserved)
            print("  candidate %#010x  data=%#010x num=%d max=%d  index %d/%d  names %d/%d  reserved %d/%d"
                  % (addr, data, num, cap, gi, checked, gn, checked, rh, rc))
            if rows:
                print("    reserved: " + ", ".join("%d=%s" % (i, g or "?") for i, e, g in rows[:17]))
            if sample:
                first = [sample[i] for i in sorted(sample)[:8]]
                print("    first names: " + ", ".join(repr(n) for n in first))
            if rh == rc and rc >= 2:
                print("    VALIDATED: %d/%d reserved names at their own indices -- this is `GNames`" % (rh, rc))


def parse_indices(text):
    out = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.extend(range(int(lo, 0), int(hi, 0) + 1))
        else:
            out.append(int(part, 0))
    return out


def cmd_index(guest, args):
    data, num, cap = (struct.unpack("<III", guest.read(args.at, 12)))
    print("table at %#010x: data=%#010x num=%d max=%d" % (args.at, data, num, cap))
    for idx in parse_indices(args.index):
        if not (0 <= idx < num):
            print("  %6d  out of range" % idx)
            continue
        ptr = guest.u32(data + idx * 4)
        got = entry(guest, ptr)
        if got is None:
            print("  %6d  entry %#x does not read as an FNameEntry" % (idx, ptr))
        else:
            print("  %6d  %#010x  flags=%#x  %r" % (idx, ptr, got[1], got[3]))


def class_name_map(sections):
    names, _sizes = objects.class_names()
    return names


def name_table(guest, args):
    """(data, num) for the name array at --at, read once."""
    data, num, cap = struct.unpack("<III", guest.read(args.at, 12))
    print("name table %#010x: data=%#010x num=%d max=%d" % (args.at, data, num, cap))
    return data, num


def object_array(guest, args):
    """(data, num) for UObject::GObjObjects."""
    odata, onum, omax = struct.unpack("<III", guest.read(args.objects, 12))
    print("object array %#010x: data=%#010x num=%d max=%d" % (args.objects, odata, onum, omax))
    if not (objects.RAM_LO <= odata < objects.RAM_HI) or onum < 512:
        raise SystemExit("the object array does not read: nothing to measure")
    return odata, onum


class Names(object):
    """FName index -> text, with the whole FNameEntry* array read once.

    One stub round-trip per lookup is what makes an object walk slow: 34,800 pointers are 136 KiB, four
    bulk reads, and they turn a per-index read into a memory access.
    """

    def __init__(self, guest, data, num):
        self.guest = guest
        self.num = num
        self.table = b""
        got = 0
        while got < num * 4:
            chunk = guest.read(data + got, min(0x4000, num * 4 - got))
            self.table += chunk
            got += len(chunk)
        self.cache = {}

    def __getitem__(self, idx):
        if idx in self.cache:
            return self.cache[idx]
        text = None
        if 0 < idx < self.num:
            ptr = struct.unpack_from("<I", self.table, idx * 4)[0]
            got = entry(self.guest, ptr)
            text = got[3] if got else None
        self.cache[idx] = text
        return text


def cmd_verify(guest, args):
    """Measure `UObject::Name` and `UObject::Class` in a live guest: two tests, neither of them circular.

    The class pointer is found without knowing any layout: a UClass object's own vtable is the `Class`
    table, so a header dword that points at an object on that table *is* `Class`. The name offset is then
    the dword of that class object which resolves, through `GNames`, to the class name the instance's own
    vtable already carries -- agreement between two independent artifacts (the vtable table and the name
    table) over hundreds of objects, so one wrong field cannot produce it.

    A class *default* object is also named after its class, which makes CDO agreement a second, weaker
    signal: it holds for the defaults in a sample and never for their instances, so an offset scoring a
    handful of hits -- rather than one per object -- is not `Name`.
    """
    data, num = name_table(guest, args)
    names = class_name_map(None)
    class_tables = set(t for t, n in names.items() if n == "Class")
    odata, onum = object_array(guest, args)
    nm = Names(guest, data, num)

    take = min(onum, args.sample * 64)
    ptrs = [p for p in struct.unpack("<%dI" % take, guest.read(odata, take * 4))
            if plausible_pointer(p)]
    stride = max(1, len(ptrs) // args.sample)
    heads = []
    for ptr in ptrs[::stride][:args.sample]:
        try:
            heads.append((ptr, guest.read(ptr, 0x50)))
        except IOError:
            continue
    OFF = list(range(0x04, 0x50, 4))
    print("sampled %d object(s)" % len(heads))
    if not heads or not class_tables:
        print("  no objects or no `Class` table in inventory/xbe_class_table_names.csv: nothing to measure")
        return

    # 1. the Class pointer: a header dword pointing at an object whose own vtable is the `Class` table
    # Count every offset independently and only then pick the winner: several fields point at a `UClass`
    # object (`Outer` is one -- a function's outer is its class), so a loop that stops at the first match
    # per object splits the count between them and makes the true offset look like a minority.
    ptr_hits = {}
    vals_of = {}
    for ptr, head in heads:
        vals = {off: struct.unpack_from("<I", head, off)[0] for off in OFF}
        vals_of[ptr] = vals
        for off in OFF:
            v = vals[off]
            if not (objects.RAM_LO <= v < objects.RAM_HI):
                continue
            try:
                if guest.u32(v) in class_tables:
                    ptr_hits[off] = ptr_hits.get(off, 0) + 1
            except IOError:
                continue
    print("\nclass pointer (a header dword on the `Class` table):")
    for off in sorted(ptr_hits, key=lambda o: -ptr_hits[o])[:4]:
        print("  +%#04x points at a UClass object for %d of %d object(s)" % (off, ptr_hits[off], len(heads)))
    if not ptr_hits:
        print("  no header dword points at a UClass object: no verification")
        return
    class_off = max(ptr_hits, key=ptr_hits.get)
    if ptr_hits[class_off] < len(heads):
        print("  note: %d object(s) have no class pointer at that offset" % (len(heads) - ptr_hits[class_off]))
    classes = dict((ptr, vals[class_off]) for ptr, vals in vals_of.items()
                   if objects.RAM_LO <= vals[class_off] < objects.RAM_HI)

    # 2. the name offset: the class object's name must equal the instance's vtable class name
    name_hits = {}
    known = 0
    for ptr, head in heads:
        vt = struct.unpack_from("<I", head, 0)[0]
        want = names.get(vt)
        if not want:
            continue
        known += 1
        try:
            phead = guest.read(classes[ptr], 0x50)
        except (IOError, KeyError):
            continue
        for off in OFF:
            d = struct.unpack_from("<I", phead, off)[0]
            if nm[d] == want:
                name_hits[off] = name_hits.get(off, 0) + 1
    print("\nname (the class object's own name, against the instance's vtable class):")
    for off in sorted(name_hits, key=lambda o: -name_hits[o])[:4]:
        print("  +%#04x names the instance's class for %d of %d object(s) with a known class table"
              % (off, name_hits[off], known))
    if not name_hits:
        print("  no dword of a class object resolved to that class's name")
        return
    name_off = max(name_hits, key=name_hits.get)
    print("\nUObject header measured here: Class +%#04x, Name +%#04x (Outer +0x18, tools/xemu/owner.py)"
          % (class_off, name_off))

    # 3. cross-check: a class default object is named after its class
    cdo = 0
    for ptr, head in heads:
        vt = struct.unpack_from("<I", head, 0)[0]
        if names.get(vt) and nm[struct.unpack_from("<I", head, name_off)[0]] == names[vt]:
            cdo += 1
    print("cross-check: %d of %d sampled object(s) are named after their class (class defaults)" % (cdo, len(heads)))


def cmd_census(guest, args):
    """Name every object's class from its `Class` pointer, over the whole object array.

    This is the read a vtable census cannot do: `GameHud extends Interaction` carries `Interaction`'s
    table, so a script class has no table of its own, while its objects still point at a `UClass` object
    whose name *is* the script class. Objects are read one 64 KiB page per page that holds one, which is
    the same choice `objects.py`'s walk makes -- per-object reads cost a stub round-trip each.
    """
    data, num = name_table(guest, args)
    nm = Names(guest, data, num)
    odata, onum = object_array(guest, args)
    ptrs = [p for p in struct.unpack("<%dI" % onum, guest.read(odata, onum * 4))
            if plausible_object(p)]
    span = 0x10000
    pages = {}
    for p in ptrs:
        pages.setdefault(p & ~(span - 1), []).append(p)
    print("walking %d of %d object(s) over %d page(s)" % (len(ptrs), onum, len(pages)))
    class_of = {}
    name_of = {}
    where = {}
    walked = 0
    missing = 0
    for page in sorted(pages):
        buf = None
        for attempt in range(3):
            try:
                buf = guest.read(page, span)
                break
            except IOError:
                continue
        if buf is None:
            missing += len(pages[page])
            continue
        for p in pages[page]:
            off = p - page
            if off < 0 or off + NAME_OFF + 4 > len(buf):
                missing += 1
                continue
            klass = struct.unpack_from("<I", buf, off + CLASS_OFF)[0]
            idx = struct.unpack_from("<I", buf, off + NAME_OFF)[0]
            class_of[klass] = class_of.get(klass, 0) + 1
            name_of[idx] = name_of.get(idx, 0) + 1
            where.setdefault(klass, []).append(p)
            walked += 1
    print("read %d object header(s), %d unreadable" % (walked, missing))

    by_class = {}
    unnamed = 0
    addresses = {}
    for klass, count in class_of.items():
        # The class object's own name is *one* read, and a single short read is what the `--find` sweep
        # already measured on this stub: 21 of the name array's first 64 slots failed to read back and
        # counted as absent. Retry it, or a flaky page turns a whole class into "<unreadable>" (14,459
        # objects over two classes in the first full-array run).
        label = None
        for attempt in range(4):
            try:
                idx = guest.u32(klass + NAME_OFF)
            except IOError:
                continue
            label = nm[idx]
            if label:
                break
        if not label:
            unnamed += count
            label = "<unreadable %#x>" % klass
        by_class[label] = by_class.get(label, 0) + count
        addresses.setdefault(label, []).extend(where[klass])
    print("%d distinct class(es) over %d object(s), %d object(s) in an unreadable class"
          % (len([b for b in by_class if not b.startswith("<")]), walked, unnamed))
    rows = sorted(by_class.items(), key=lambda kv: -kv[1])
    if args.csv:
        with open(args.csv, "w", encoding="utf-8") as fh:
            fh.write("class,objects\n")
            for label, count in rows:
                fh.write("%s,%d\n" % (label, count))
        print("\nwrote all %d class row(s) to %s" % (len(rows), args.csv))
    if args.grep:
        needles = [n for n in args.grep.lower().split(",") if n]
        rows = [r for r in rows if any(n in r[0].lower() for n in needles)]
        print("\nclasses matching %r (count):" % args.grep)
        for label, count in rows[:args.top]:
            print("  %-40s %6d" % (label, count))
        if args.addresses:
            print("\naddresses (class, address):")
            for label, count in rows:
                addrs = addresses.get(label, [])
                for p in addrs[:args.limit]:
                    print("  %-40s %#010x" % (label, p))
                if len(addrs) > args.limit:
                    print("  %-40s ... %d more" % (label, len(addrs) - args.limit))
        return
    print("\n%-40s %6s" % ("class", "objects"))
    for label, count in rows[:args.top]:
        print("  %-40s %6d" % (label, count))
    print("\ntop object names:")
    for idx, count in sorted(name_of.items(), key=lambda kv: -kv[1])[:8]:
        print("  %-40s %6d" % (nm[idx] or "<unreadable>", count))


class PageGuest:
    """A guest with 64 KiB pages that are either mapped or not, like the stub: a request over an unmapped
    byte fails whole, which is what made a boundary-terminated entry read as absent."""

    def __init__(self, base, pages):
        self.base = base
        self.page = 0x10000
        self.mem = {p: bytearray(self.page) for p in pages}
        self.reads = 0

    def poke(self, va, blob):
        off = va - self.base
        page, start = self.base + (off - off % self.page), off % self.page
        self.mem[page][start:start + len(blob)] = blob

    def read(self, addr, size, chunk=0x4000):
        self.reads += 1
        got = bytearray()
        while len(got) < size:
            at = addr + len(got)
            off = at - self.base if at >= self.base else -1
            page = self.base + (off - off % self.page) if off >= 0 else None
            if page not in self.mem:
                raise IOError("read %#x+%#x runs into unmapped memory" % (addr, size))
            inside = off % self.page
            take = min(size - len(got), self.page - inside)
            got += self.mem[page][inside:inside + take]
        return bytes(got)

    def u32(self, addr):
        return struct.unpack("<I", self.read(addr, 4))[0]


def selftest():
    """The boundary case, without a guest: a complete entry whose block ends on a page edge must still resolve."""
    base = 0x00600000
    guest = PageGuest(base, [base])          # exactly one mapped 64 KiB page, like the five `Core` entries
    text = "ObjectProperty"
    blob = struct.pack("<III", 5, 0, 4) + text.encode("utf-16-le") + b"\0\0"
    ptr = base + 0xFFC0                        # the last 64 bytes of the page; past the boundary is unmapped
    guest.poke(ptr, blob)
    try:
        guest.read(ptr, NAME_AT + 2 * NAME_MAX + 2)  # the old fixed read
    except IOError:
        pass
    else:
        print("selftest FAIL: the fake guest served a read past its last page")
        return 1
    got = entry(guest, ptr)
    if got != (5, 0, 4, text):
        print("selftest FAIL: a boundary entry read as %r" % (got,))
        return 1
    # an entry that is not at a boundary still reads, and a truncated one with no terminator is still absent
    ptr2 = base + 0x0800
    guest.poke(ptr2, struct.pack("<III", 103, 0, 0) + "ByteProperty".encode("utf-16-le") + b"\0\0")
    if entry(guest, ptr2) != (103, 0, 0, "ByteProperty"):
        print("selftest FAIL: an aligned entry stopped resolving")
        return 1
    guest.poke(base + 0xFFE0, struct.pack("<III", 103, 0, 0) + "ByteProper".encode("utf-16-le"))  # no NUL
    if entry(guest, base + 0xFFE0) is not None:
        print("selftest FAIL: a truncated entry was read as an entry")
        return 1
    print("ok   names: an entry whose block ends on a page boundary resolves (%d = %s)" % (got[0], text))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=None, help="xemu's GDB stub port")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--xbe", default=None, help="default.xbe of the dump (for the section table)")
    ap.add_argument("--selftest", action="store_true",
                    help="check the page-boundary read with a fake guest, no boot needed")
    ap.add_argument("--find", action="store_true", help="scan the writable sections for the name array")
    ap.add_argument("--section", action="append", default=None, metavar="NAME",
                    help="section to scan (default .data; repeatable)")
    ap.add_argument("--at", type=lambda v: int(v, 0), default=None, help="the array's own address, once known")
    ap.add_argument("--index", default=None, metavar="LIST",
                    help="name indices to resolve: `0-16` or `357,2014,308`")
    ap.add_argument("--verify", action="store_true",
                    help="resolve object class-name fields through the table at --at")
    ap.add_argument("--objects", type=lambda v: int(v, 0), default=OBJ_ARRAY, help="UObject::GObjObjects")
    ap.add_argument("--hash", type=lambda v: int(v, 0), default=None,
                    help="read the 4,096-slot name hash at this address and report bucket occupancy")
    ap.add_argument("--sample", type=int, default=96, help="objects to sample in --verify")
    ap.add_argument("--census", action="store_true",
                    help="name every object's class from its Class pointer (walks the whole array)")
    ap.add_argument("--grep", default=None, metavar="SUBSTR",
                    help="with --census, print only classes whose name contains one of these comma-separated "
                         "substrings")
    ap.add_argument("--top", type=int, default=12, help="rows to print")
    ap.add_argument("--csv", default=None, metavar="PATH",
                    help="with --census, write the whole class histogram (class,objects) to this file, so one "
                         "walk answers every class question instead of one walk per --grep")
    ap.add_argument("--addresses", action="store_true",
                    help="with --census --grep, print the matching objects' addresses as well as their counts")
    ap.add_argument("--limit", type=int, default=40, help="with --addresses, rows per class")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.port is None or not args.xbe:
        ap.error("--port and --xbe are required unless --selftest is given")
    if args.section is None:
        args.section = [".data"]

    sections = objects.image_sections(args.xbe)
    print("image sections: " + ", ".join("%s %#x+%#x" % row for row in sections))
    guest = Guest(args.host, args.port)
    try:
        if args.find:
            cmd_find(guest, sections, args)
        if args.hash:
            occupied = 0
            deepest = 0
            ptrs = 0
            for bucket in range(4096):
                try:
                    p = guest.u32(args.hash + bucket * 4)
                except IOError:
                    continue
                if not plausible_pointer(p):
                    continue
                occupied += 1
                depth = 0
                while plausible_pointer(p) and depth < 64:
                    got = entry(guest, p, want_name=False)
                    if got is None:
                        break
                    ptrs += 1
                    depth += 1
                    p = got[2]
                deepest = max(deepest, depth)
            print("\nname hash at %#010x: %d of 4,096 buckets occupied, %d entries walked, deepest chain %d"
                  % (args.hash, occupied, ptrs, deepest))
        if args.at is not None and args.index:
            print()
            cmd_index(guest, args)
        if args.at is not None and args.verify:
            print()
            cmd_verify(guest, args)
        if args.at is not None and args.census:
            print()
            cmd_census(guest, args)
        print("\n%d guest read(s)" % guest.reads)
    finally:
        guest.close()


if __name__ == "__main__":
    sys.exit(main())
