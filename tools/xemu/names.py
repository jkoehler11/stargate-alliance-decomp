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

`--verify` closes the loop the other way: walk `UObject::GObjObjects`, take objects whose first dword is a table in
`inventory/xbe_class_table_names.csv` (so their class name is known without the name table), and ask which dword of
the object header resolves, through the candidate table, to exactly that class name. The offset that agrees is
`UObject::Name`; `+0x18` is the measured `Outer` (`tools/xemu/owner.py`), so a class default object should also name
its class through `Outer` -> `Name`.

Usage, against a guest booted with `-gdb tcp::PORT` (`drive.py --gdb=PORT --linger=SECS` keeps one up):

    # where is the array, and does the shape validate?
    python3 tools/xemu/names.py --port 12345 --xbe $SG1_DUMP/default.xbe --find

    # the reserved block, and any index at all (an FName index from a dump, a loader record, a header field)
    python3 tools/xemu/names.py --port 12345 --xbe ... --at 0x006c8a10 --index 0-16
    python3 tools/xemu/names.py --port 12345 --xbe ... --at 0x006c8a10 --index 357,2014,308,17040,2757,6823,2427

    # does an object's Name resolve to the class the class table already names?
    python3 tools/xemu/names.py --port 12345 --xbe ... --at 0x006c8a10 --verify

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


def read_section(guest, va, size, chunk=0x10000):
    """Read a whole section out of the guest in stub-sized chunks."""
    out = bytearray()
    done = 0
    while done < size:
        take = min(chunk, size - done)
        out += guest.read(va + done, take)
        done += take
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
        raw = guest.read(ptr, NAME_AT + 2 * NAME_MAX + 2)
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


def cmd_verify(guest, args):
    data, num, cap = struct.unpack("<III", guest.read(args.at, 12))
    print("name table %#010x: data=%#010x num=%d max=%d" % (args.at, data, num, cap))
    names = class_name_map(None)
    odata, onum, omax = struct.unpack("<III", guest.read(args.objects, 12))
    print("object array %#010x: data=%#010x num=%d max=%d" % (args.objects, odata, onum, omax))
    if not (objects.RAM_LO <= odata < objects.RAM_HI) or onum < 512:
        print("the object array does not read: no verification")
        return
    take = min(onum, args.sample * 64)
    raw = guest.read(odata, take * 4)
    ptrs = [p for p in struct.unpack("<%dI" % take, raw) if plausible_pointer(p)]

    cache = {}
    def name_of(idx):
        if idx in cache:
            return cache[idx]
        try:
            got = entry(guest, guest.u32(data + idx * 4))
        except IOError:
            got = None
        cache[idx] = got[3] if got else None
        return cache[idx]

    stride = max(1, len(ptrs) // args.sample)
    sampled = known = 0
    hits = {}
    inrange = {}
    examples = []
    for ptr in ptrs[::stride][:args.sample]:
        try:
            head = guest.read(ptr, 0x40)
        except IOError:
            continue
        sampled += 1
        vt = struct.unpack_from("<I", head, 0)[0]
        want = names.get(vt)
        if not want:
            continue
        known += 1
        for off in range(4, 0x40, 4):
            d = struct.unpack_from("<I", head, off)[0]
            if not (0 < d < num):
                continue
            inrange[off] = inrange.get(off, 0) + 1
            if name_of(d) == want:
                hits[off] = hits.get(off, 0) + 1
                if len(examples) < 6:
                    examples.append((ptr, off, want))
    print("sampled %d object(s), %d whose first dword names a class in inventory/xbe_class_table_names.csv"
          % (sampled, known))
    if not known:
        print("  no object reached a named class table: nothing to verify")
        return
    for off in sorted(hits, key=lambda o: -hits[o])[:4]:
        print("  +%#04x = class-name index for %d of %d known-class objects (%d slots in range)"
              % (off, hits[off], known, inrange.get(off, 0)))
    if not hits:
        print("  no header dword resolved to the object's own class name")
        print("  offsets holding an in-range index at all: %s"
              % ", ".join("+%#04x:%d" % (o, inrange[o]) for o in sorted(inrange)[:8]))
    for ptr, off, want in examples:
        print("  e.g. object %#010x (+%#04x -> %r)" % (ptr, off, want))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, required=True, help="xemu's GDB stub port")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--xbe", required=True, help="default.xbe of the dump (for the section table)")
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
    ap.add_argument("--top", type=int, default=6, help="candidates to print")
    args = ap.parse_args()
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
        print("\n%d guest read(s)" % guest.reads)
    finally:
        guest.close()


if __name__ == "__main__":
    sys.exit(main())
