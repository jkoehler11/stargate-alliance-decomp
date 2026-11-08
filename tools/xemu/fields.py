#!/usr/bin/env python3
"""Read a live `UObject`'s fields out of the guest: its class, its class default, and what the fields hold (PLAN 4.3, U15).

`names.py` names an object from its own `Class` pointer; this tool reads the *contents*. Two measurements make it
possible without a serialized layout: `UObject::Name` `+0x20` / `Class` `+0x24` (measured by `names.py --verify`), and
`UField::Next` `+0x28` -- the link `UStruct::Serialize` walks (`docs/NATIVE_VM.md`). Everything else is measured here:

* **the class's field chain** -- a `UStruct` holds a `Children` pointer to the first of its `UProperty`/`UFunction`
  objects, each chained through `Next`. The offset is not assumed: every dword in the class object is tried as the
  head and a candidate is only accepted when its chain links are objects whose own `Class` name ends in `Property`
  (or is `Function`/`State`), so two independent reads -- the object header and the name table -- have to agree.
* **the class default object** -- an instance whose `Class` pointer is the class itself. Found the same way, by
  scanning the class object for a pointer that is its own instance, and `--scan` sets how far that search runs
  because a live class object carries members past the `UStruct` header (measured: `+0x4e8`).
* **a class object's own members** -- `--members` walks the dwords from `--start` (default `0x400`) to `--size` and
  labels every one that resolves through the name table: an *instance of this class* is a default-object candidate,
  a target whose own class is a `*Property` (or `Function`/`State`/`ScriptStruct`) is a `Children` head and its
  `Next` chain is printed with it, and anything else is named for what it is. Each pointer found is also looked up in
  `GObjObjects`, because "allocated but not in the array" is a fact about the object graph and not a failed read.

A field's *value* is read as dwords, because a property's `Offset` is written by the linker and not serialized: the
walk reports what each dword holds at the same offset in the live object and in its class default, and resolves any
dword that is an object pointer. A `TArray` shows as a `(data, num, max)` triple, so an array of object pointers is
followed and a `FString` (`num` characters) is measured as a length -- the text itself is game state and stays
behind `--show-text`.

Usage, against a guest booted with `-gdb tcp::PORT` (`drive.py --gdb=PORT --linger=SECS` keeps one up):

    # what is GameHud, what is its class default, and which fields differ between them
    python3 tools/xemu/fields.py --port 12345 --xbe $SG1_DUMP/default.xbe --at 0x006be0d8 \
        --object 0x09332100 --properties --compare

    # an object's own field block, every pointer resolved to a class and a name
    python3 tools/xemu/fields.py --port 12345 --xbe ... --at 0x006be0d8 --object 0x096dc340

    # a *class* object's members past the UStruct header: `Children` heads and the default object
    python3 tools/xemu/fields.py --port 12345 --xbe ... --at 0x006be0d8 --object 0x01886c00 --members --size 0x800

    # which objects occupy the bytes a field window covers -- the question a `--diff` cannot answer
    python3 tools/xemu/fields.py --port 12345 --at 0x07730000 --near 0x0682ab00,0x09322100 --size 0x600

    # where a declared *scalar* default sits: `--find` takes the dwords the guest has to hold
    # (`0x3f800000` is 1.0f, the value three `GameHud` font scales declare) and prints their offsets
    python3 tools/xemu/fields.py --port 12345 --at 0x006be0d8 --object 0x0682ab00 --find 0x3f800000

Names are engine identifiers; field contents are game state, so nothing here is written to a file and text is only
printed with `--show-text`.
"""
import argparse
import bisect
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
NAMES_PY = os.path.join(HERE, "names.py")

NEXT_OFF = 0x28              # UField::Next, the link UStruct::Serialize walks (docs/NATIVE_VM.md)
NAME_OFF = 0x20              # UObject::Name, an FName index (names.py --verify)
CLASS_OFF = 0x24             # UObject::Class, a UClass object (names.py --verify)
OUTER_OFF = 0x18             # UObject::Outer (PLAN 4.3 census notes: a class object's `+0x18` names its package)
PROPERTY_SUFFIX = "Property"
FIELD_KINDS = ("Function", "State", "ScriptStruct")


def load(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


names = load("xemu_names", NAMES_PY)


class Reader(object):
    """Object reads over one guest, with the name array read once."""

    def __init__(self, guest, table, objects_data=None, objects_num=0):
        self.guest = guest
        self.names = table
        self.objects_data = objects_data
        self.objects_num = objects_num
        self._classes = {}
        self._pages = {}

    def u32(self, addr):
        return struct.unpack("<I", self.guest.read(addr, 4))[0]

    def page(self, addr):
        """The 64 KiB page holding `addr`, read once, or None when that page will not read whole.

        A 0x600-byte object is 1,536 dwords, and one stub round trip per dword is what made a field-by-field
        diff outlast a 300 s timeout. The stub serves 64 KiB pages (PacketSize 0x20020), so a walk reads two
        pages and parses them locally; a page that fails falls back to single dwords, because a failed wide
        read is a statement about the *request*, not about the address (the `FNameEntry` page trap).
        """
        base = addr & ~0xffff
        if base not in self._pages:
            self._pages[base] = self.try_read(base, 0x10000)
        return base, self._pages[base]

    def try_u32(self, addr):
        base, blob = self.page(addr)
        off = addr - base
        if blob is not None and off + 4 <= len(blob):
            return struct.unpack_from("<I", blob, off)[0]
        try:
            return self.u32(addr)
        except (IOError, OSError):
            return None

    def try_read(self, addr, size):
        try:
            return self.guest.read(addr, size)
        except (IOError, OSError):
            return None

    def text(self, idx):
        try:
            return self.names[idx]
        except (IOError, OSError, IndexError):
            return None

    def object(self, addr):
        """(class name, object name) for a live object, or None when the dword is not one."""
        if not names.plausible_object(addr):
            return None
        cls = self.try_u32(addr + CLASS_OFF)
        if cls is None:
            return None
        try:
            cname = self.text(self.u32(cls + NAME_OFF))
        except (IOError, OSError):
            return None
        if not cname:
            return None
        return cname, self.text(self.try_u32(addr + NAME_OFF))

    def class_of(self, addr):
        got = self.object(addr)
        return got[0] if got else None

    def array_at(self, data, num, cap):
        """Kind of a `TArray`-shaped triple: ('objects', [...]) / ('text', bytes) / ('bytes', None)."""
        if not names.plausible_object(data) or not (0 < num <= 0x10000) or num > cap:
            return None, None
        first = min(num, 8)
        blob = self.try_read(data, first * 4 if first else 4)
        if blob is None:
            return None, None
        if num >= 1 and first:
            ptrs = [struct.unpack_from("<I", blob, i * 4)[0] for i in range(first)]
            if all(names.plausible_object(p) for p in ptrs) and self.object(ptrs[0]):
                out = []
                for p in ptrs:
                    got = self.object(p)
                    out.append(got[0] if got else "?")
                return "objects", out
        # An `FString`'s `num` counts *characters*, so the probe has to ask for two bytes each: reading `num`
        # bytes truncated "Press A to Skip" to "Press A " and made a declared default look like live text.
        blob = self.try_read(data, max(4, min(num * 2, 128)))
        if blob:
            if blob[:1] == b"\x00" or blob[:2] == b"\xff\xfe":
                return "text", blob
            if all(32 <= c < 127 or c in (9, 10, 13, 0) for c in blob):
                return "text", blob
        return "bytes", None


def scan_members(r, cls, start=0x400, size=0x600):
    """[(offset, text)] for every dword in `[start, size)` of a class object that reads as an object pointer.

    A class object's own members sit past the `UStruct` header, and two of them answer questions a header scan
    cannot: `Children` (the head of the field chain) and `DefaultObject` (a live instance of the class itself).
    Neither offset is assumed here -- each dword is resolved through the name table, and what its target says about
    itself decides the label:

    * `instance of this class` -- the target's own `Class` pointer is the class being scanned, so it is a default
      object (or a second instance allocated beside it);
    * `field chain of N` -- the target's class ends in `Property`, or is `Function`/`State`/`ScriptStruct`, so the
      row is a candidate `Children` head and its `Next` chain is printed;
    * `object` -- anything else, named for what it is.

    A dword that does not resolve is skipped, not guessed at, so the rows are the measurements and the gaps are
    the negative.
    """
    rows = []
    off = start
    while off + 4 <= size:
        value = r.try_u32(cls + off)
        if value is None:
            break
        got = r.object(value)
        if not got:
            off += 4
            continue
        kind, oname = got
        if r.try_u32(value + CLASS_OFF) == cls:
            # The target's own `Class` is the class being scanned, so it is *an* instance of it. Only a
            # default object is also named after the class, so say which half a row measured rather than
            # letting every instance read as a `DefaultObject` candidate.
            cname = r.text(r.try_u32(cls + NAME_OFF))
            note = "name matches the class" if (oname and cname and oname == cname) else (
                "name `%s`, not the class's" % oname)
            rows.append((off, "instance of this class: `%s` at %#010x (%s)" % (oname or "?", value, note)))
        elif kind.endswith(PROPERTY_SUFFIX) or kind in FIELD_KINDS:
            chain, addr, seen = [], value, set()
            while addr and addr not in seen and len(chain) < 512:
                seen.add(addr)
                inner = r.object(addr)
                if not inner or not (inner[0].endswith(PROPERTY_SUFFIX) or inner[0] in FIELD_KINDS):
                    break
                chain.append(inner[1] or "?")
                addr = r.try_u32(addr + NEXT_OFF)
                if addr is None:
                    break
            rows.append((off, "field chain of %d: %s" % (
                len(chain), ", ".join(chain[:8]) + (" ..." if len(chain) > 8 else ""))))
        else:
            rows.append((off, "object `%s`%s at %#010x" % (kind, " (%s)" % oname if oname else "", value)))
        off += 4
    return rows


def array_membership(guest, data, num, targets, chunk=0x10000):
    """{target: index} for the targets that appear in `GObjObjects`, and `None` for the ones that do not.

    The array is a contiguous pointer block, so it reads in chunks where a `min..max` span does not (the span is
    mostly unmapped and one failed request kills the whole read). A chunk that will not read is skipped and counted
    as no evidence, which is the honest answer: an object above a hole is not "absent", it is unmeasured.
    """
    hits = dict((t, None) for t in targets)
    whole = num * 4
    done = 0
    while done < whole:
        take = min(chunk, whole - done)
        try:
            blob = guest.read(data + done, take)
        except (IOError, OSError):
            done += take
            continue
        for i in range(len(blob) // 4):
            value = struct.unpack_from("<I", blob, i * 4)[0]
            if value in hits and hits[value] is None:
                hits[value] = (done + i * 4) // 4
        done += take
    return hits


def walk_headers(r, page=0x10000):
    """[(index, address)] for every plausible object pointer in `GObjObjects`, in array order.

    The array reads in chunks and each 64 KiB page of the heap reads **once**, with every header in it parsed
    locally. Per-object reads are what made the named census 13 minutes: latency, not bytes. This is the walk
    every other question is a filter over -- a class filter (`walk_instances`) and an address-order view
    (`span_rows`) are two readings of the same list, so a second question costs a local pass, not a second walk.
    """
    hits = []
    whole = r.objects_num * 4
    done = 0
    while done < whole:
        take = min(page, whole - done)
        blob = r.try_read(r.objects_data + done, take)
        if blob is None:
            done += take
            continue
        for i in range(len(blob) // 4):
            value = struct.unpack_from("<I", blob, i * 4)[0]
            if names.plausible_object(value):
                hits.append(((done + i * 4) // 4, value))
        done += take
    return hits


def walk_instances(r, cls, page=0x10000):
    """[(index, address)] for every object in `GObjObjects` whose own `Class` pointer is `cls`.

    A default object is an object like any other: nothing in the class object has to point at it, and its
    own `Class` is the only thing that says which class it belongs to. So the question "how many instances
    of this class are there" is a filter over the whole object array, asked over `walk_headers` -- and the
    class dword reads out of the page cache, so the filter costs no extra stub round trip.
    """
    return [(index, addr) for index, addr in walk_headers(r, page)
            if r.try_u32(addr + CLASS_OFF) == cls]


def span_rows(r, hits, targets, size):
    """[(target, prev, rows)]: which objects occupy `[target, target+size)` in address order.

    An object's *extent* is not stored anywhere this tool can read -- a `UObject` header carries no size, and
    the class's `PropertiesSize` offset is not measured -- but the object array is address-ordered evidence
    for it anyway: the next allocation above an object starts where that object ended (or later), so
    `next - target` is an **upper bound** on the object's size, and every dword in a window past that bound
    belongs to a different object rather than to a field.

    That is the question a `--diff` over `--size` cannot answer: a sparse heap padded with recyclable gaps
    makes a window read as "this object's fields" when the tail of it is the neighbour's header, its
    property records or its own field block. `rows` names the occupier of every byte from `target` on, so a
    field offset can be checked against a boundary instead of assumed to be inside.
    """
    ordered = sorted(hits, key=lambda t: t[1])
    addrs = [a for _i, a in ordered]
    out = []
    for target in targets:
        pos = bisect.bisect_left(addrs, target)
        rows, end = [], target + size
        for index, addr in ordered[pos:]:
            if addr >= end:
                break
            got = r.object(addr)
            rows.append((index, addr, got[0] if got else "?", got[1] if got else "?"))
        prev = None
        if pos:
            index, addr = ordered[pos - 1]
            got = r.object(addr)
            prev = (index, addr, got[0] if got else "?", got[1] if got else "?")
        out.append((target, prev, rows))
    return out


def find_field_chain(r, cls, limit=0x400):
    """(offset of `Children`, [(addr, name, kind)]) measured from the class object itself.

    A candidate head is accepted only when its whole chain is objects whose `Class` name ends in `Property` (or is a
    known field kind) -- the header read and the name table have to agree over every link, so a stray pointer cannot
    pass. Ties go to the longer, purer chain.
    """
    best = (None, [])
    for cand in range(NEXT_OFF, limit, 4):
        head = r.try_u32(cls + cand)
        if head is None or not names.plausible_object(head):
            continue
        chain, addr, seen = [], head, set()
        while addr and addr not in seen and len(chain) < 512:
            seen.add(addr)
            got = r.object(addr)
            if not got:
                break
            kind, oname = got
            if not (kind.endswith(PROPERTY_SUFFIX) or kind in FIELD_KINDS):
                break
            chain.append((addr, oname, kind))
            addr = r.try_u32(addr + NEXT_OFF)
            if addr is None:
                break
        if len(chain) > len(best[1]):
            best = (cand, chain)
    return best


def find_default(r, cls, limit=0x400):
    """The class default object: an instance whose own `Class` pointer is `cls`."""
    for cand in range(NEXT_OFF, limit, 4):
        ptr = r.try_u32(cls + cand)
        if ptr is None or not names.plausible_object(ptr):
            continue
        if r.try_u32(ptr + CLASS_OFF) == cls:
            return ptr
    return None


def walk_fields(r, addr, size):
    """[(offset, text)] for every dword of the object that reads as an object pointer, an array or text."""
    rows = []
    off = 0
    while off + 4 <= size:
        value = r.try_u32(addr + off)
        if value is None:
            break
        got = r.object(value)
        if got:
            rows.append((off, "object `%s`%s at %#010x" % (got[0], " (%s)" % got[1] if got[1] else "", value)))
        elif off + 12 <= size:
            data = value
            num = r.try_u32(addr + off + 4)
            cap = r.try_u32(addr + off + 8)
            if num is not None and cap is not None:
                kind, detail = r.array_at(data, num, cap)
                if kind == "objects":
                    rows.append((off, "array of %d objects: %s" % (num, ", ".join(detail[:6]))))
                elif kind == "text":
                    body = detail.decode("utf-16-le", "replace").split("\x00")[0] if SHOW_TEXT[0] else ""
                    rows.append((off, "FString of %d characters" % (num, ) + (
                        ": %r" % body if SHOW_TEXT[0] else "")))
        off += 4
    return rows


SHOW_TEXT = [False]


def find_dwords(r, addr, size, needles):
    """[(offset, value)] for every dword of the object that equals one of `needles`.

    `walk_fields` follows pointers, arrays and text -- it cannot see a scalar, so a declared default like
    `GameHud`'s three `1.0` font scales has no fingerprint in its output. The *value* is the only thing the guest
    stores for a `Float`/`Int`, and a class default object carries the declared value, so searching the field block
    for a known dword is how a scalar default is checked without a property-offset table (the linker writes
    `UProperty::Offset`, nothing serializes it).
    """
    hits = []
    off = 0
    while off + 4 <= size:
        value = r.try_u32(addr + off)
        if value is None:
            break
        if value in needles:
            hits.append((off, value))
        off += 4
    return hits


def cmd_near(r, args):
    """Which object owns each byte of a field window, read off the object array's address order."""
    hits = walk_headers(r)
    if not hits:
        raise SystemExit("GObjObjects read as no objects: check --objects")
    for target, prev, rows in span_rows(r, hits, args.near, args.size):
        got = r.object(target)
        print("object %#010x: class `%s`%s" % (target, got[0] if got else "?",
                                               " name `%s`" % got[1] if got and got[1] else ""))
        if prev:
            print("  below    index %-7d %#010x  -%#05x  class `%s` name `%s`" % (
                prev[0], prev[1], target - prev[1], prev[2], prev[3] or "?"))
        for index, addr, cname, oname in rows:
            print("  %s index %-7d %#010x  +%#05x  class `%s` name `%s`" % (
                "self " if addr == target else "cross", index, addr, addr - target,
                cname, oname or "?"))
        others = rows[1:]
        if others:
            print("  extent at most %#x: %d byte(s) of the window from +%#x belong to %d other object(s)" % (
                others[0][1] - target, args.size - (others[0][1] - target), others[0][1] - target,
                len(others)))
        else:
            print("  no other object starts inside the window: all %#x bytes are this object's or a gap"
                  % args.size)


def cmd_object(r, args):
    addr = args.object
    got = r.object(addr)
    if not got:
        raise SystemExit("%#x does not read as an object (Class +%#x)" % (addr, CLASS_OFF))
    cls_name, oname = got
    cls = r.u32(addr + CLASS_OFF)
    print("object %#010x: class `%s`%s" % (addr, cls_name, " name `%s`" % oname if oname else ""))
    print("  class object %#010x" % cls)
    if args.instances:
        found = walk_instances(r, addr)
        print("  %d object(s) whose own Class pointer is %#010x:" % (len(found), addr))
        for index, target in found:
            got = r.object(target)
            outer = r.try_u32(target + OUTER_OFF)
            ogot = r.object(outer) if outer else None
            print("    index %-7d %#010x  name `%s`  outer %#010x%s" % (
                index, target, got[1] if got and got[1] else "?",
                outer or 0, " (`%s`)" % ogot[1] if ogot and ogot[1] else ""))
        if args.locate:
            # Which of those addresses the class object itself holds -- by *address membership*, not by a
            # class test: `--members`' "an instance of this class" is `Class == this class`, which a
            # superclass's class object also answers, so it cannot name a default object.
            by_addr = {target: index for index, target in found}
            held = []
            for off in range(NEXT_OFF, args.size, 4):
                value = r.try_u32(addr + off)
                if value in by_addr:
                    held.append((off, by_addr[value], value))
            print("  %d class-object dword(s) in +%#x..+%#x hold one of them:" % (
                len(held), NEXT_OFF, args.size))
            for off, index, value in held:
                print("    +%#05x index %-7d %#010x" % (off, index, value))
            if not held:
                print("    none: nothing past the `UStruct` header points at an instance of this class")
        return
    if args.diff:
        a = dict(walk_fields(r, addr, args.size))
        b = dict(walk_fields(r, args.diff, args.size))
        offsets = sorted(set(a) | set(b))
        differ = [o for o in offsets if a.get(o) != b.get(o)]
        print("  %d of %d pointer/array fields differ from %#010x:" % (len(differ), len(offsets), args.diff))
        for o in differ[:args.limit]:
            print("    +%#05x %s" % (o, a.get(o, "no pointer")))
            print("             %s" % b.get(o, "no pointer"))
        return
    if args.properties:
        off, chain = find_field_chain(r, cls, args.scan)
        print("  field chain head at class+%#x: %d fields" % (off or 0, len(chain)) if off
              else "  no field chain found in the class object's first %#x bytes" % args.scan)
        for i, (a, fname, kind) in enumerate(chain):
            print("    %3d %#010x %-18s %s" % (i, a, kind, fname))
    default = None
    if args.compare:
        default = find_default(r, cls, args.scan)
        print("  class default %s" % ("%#010x" % default if default else "not found"))
    size = args.size
    if args.members:
        rows = scan_members(r, cls, args.start, size)
        print("  %d object pointer(s) in class+%#x..%#x:" % (len(rows), args.start, size))
        for off, text in rows:
            print("    +%#05x %s" % (off, text))
        targets = [r.try_u32(cls + off) for off, _t in rows]
        targets = [t for t in targets if t]
        if targets:
            hits = array_membership(r.guest, r.objects_data, r.objects_num, targets)
            inside = ["%#010x at index %d" % (t, i) for t, i in hits.items() if i is not None]
            outside = ["%#010x" % t for t, i in hits.items() if i is None]
            print("  in GObjObjects (%d objects): %s" % (r.objects_num, ", ".join(inside) if inside else "none"))
            print("  not in GObjObjects: %s" % (", ".join(outside) if outside else "none"))
        return
    if args.find:
        needles = set(args.find)
        hits = find_dwords(r, addr, size, needles)
        print("  %d dword(s) in %#x bytes equal one of %d requested value(s):" % (len(hits), size, len(needles)))
        for needle in args.find:
            offs = [off for off, value in hits if value == needle]
            if offs:
                shown = ", ".join("+%#05x" % o for o in offs[:24])
                print("    %#010x at %s%s" % (needle, shown,
                                              " (+%d more)" % (len(offs) - 24) if len(offs) > 24 else ""))
            else:
                print("    %#010x nowhere in the object" % (needle,))
    rows = walk_fields(r, addr, size)
    if default:
        drows = dict(walk_fields(r, default, size))
        print("  %d pointer/array fields differ from the class default:" % sum(
            1 for off, _t in rows if drows.get(off) != _t))
        for off, text in rows:
            if drows.get(off) != text:
                print("    +%#05x live: %s" % (off, text))
                print("             default: %s" % drows.get(off, "same shape, different dword"))
    else:
        print("  %d pointer/array fields in %d bytes:" % (len(rows), size))
        for off, text in rows:
            print("    +%#05x %s" % (off, text))


class FakeReader(object):
    """A dict-backed reader with the four methods the scans need, so their rules are testable without a boot."""

    def __init__(self, mem, objs, objects_data=0, objects_num=0):
        self.mem = mem
        self.objs = objs
        self.objects_data = objects_data
        self.objects_num = objects_num

    def try_u32(self, addr):
        return self.mem.get(addr, 0)          # a real guest serves every dword of an allocated object

    def try_read(self, addr, size):
        return b"".join(struct.pack("<I", self.mem.get(addr + o, 0)) for o in range(0, size, 4))

    def text(self, idx):
        return None

    def object(self, addr):
        return self.objs.get(addr)

    array_at = Reader.array_at          # the text probe's own rule, reused so `--show-text` is covered too


def selftest():
    """The two rules, without a guest: a member past 0x400 is found and labelled, and the old bound hid it."""
    cls = 0x01000000
    prop1, prop2, fn = 0x02000000, 0x02000100, 0x02000200
    default = 0x03000000
    mem = {
        cls + 0x30: prop1,                 # a `Children` head inside the header scan
        prop1 + NEXT_OFF: prop2,
        prop2 + NEXT_OFF: 0,
        cls + 0x19c: fn,                   # the shape that won the old scan: a two-link `Next` tail of Functions
        fn + NEXT_OFF: 0,
        cls + 0x4e8: default,              # `DefaultObject`, past the old 0x400 bound
        default + CLASS_OFF: cls,          # the default's own Class pointer is the class itself
    }
    objs = {
        prop1: ("IntProperty", "Health"),
        prop2: ("ObjectProperty", "Owner"),
        fn: ("Function", "NotifyLevelChange"),
        default: ("FakeClass", "Default__FakeClass"),
    }
    r = FakeReader(mem, objs)
    rows = dict(scan_members(r, cls, 0x400, 0x600))
    if not any(off == 0x4e8 for off in rows):
        print("selftest FAIL: a member past 0x400 was not read")
        return 1
    if "instance of this class" not in rows[0x4e8]:
        print("selftest FAIL: the default object was labelled %r" % rows[0x4e8])
        return 1
    if not any(off == 0x30 for off in dict(scan_members(r, cls, 0x28, 0x400))):
        print("selftest FAIL: the field chain inside the header was not read")
        return 1
    off, chain = find_field_chain(r, cls, 0x800)
    if off != 0x30 or [c[1] for c in chain] != ["Health", "Owner"]:
        print("selftest FAIL: find_field_chain read %#x over %r" % (off or 0, [c[1] for c in chain]))
        return 1
    if find_default(r, cls, 0x400) is not None:
        print("selftest FAIL: the old 0x400 bound found the default object it was supposed to miss")
        return 1
    if find_default(r, cls, 0x800) != default:
        print("selftest FAIL: the wide bound did not find the default object at +0x4e8")
        return 1
    # The instance walk: an object array with three entries, one of them a second instance of the class.
    # The page read is what makes it cheap, so the fake has to serve bytes rather than single dwords.
    arr, other = 0x04000000, 0x03001000
    mem2 = dict(mem)
    mem2[arr], mem2[arr + 4], mem2[arr + 8] = default, other, 0
    mem2[other + CLASS_OFF] = 0x02000000      # some other class
    r2 = FakeReader(mem2, objs, arr, 3)
    found = walk_instances(r2, cls)
    if [a for _i, a in found] != [default]:
        print("selftest FAIL: the instance walk returned %r" % ([a for _i, a in found],))
        return 1
    if [i for i, _a in found] != [0]:
        print("selftest FAIL: the instance walk lost the array index (%r)" % ([i for i, _a in found],))
        return 1
    # The span rule: an object whose 0x600 field window runs into the next allocation at +0x300. That is the
    # GameHud question -- a window that reads as "property records" when the tail belongs to a neighbour.
    mid, after = 0x03002000, 0x03002300
    mem3 = dict(mem2)
    mem3[arr], mem3[arr + 4], mem3[arr + 8] = default, mid, after
    mem3[mid + CLASS_OFF] = cls
    mem3[after + CLASS_OFF] = 0x02000000
    objs3 = dict(objs)
    objs3.update({mid: ("GameHud", "GameHud"), after: ("ObjectProperty", "Canvas")})
    r3 = FakeReader(mem3, objs3, arr, 3)
    spans = span_rows(r3, walk_headers(r3), [mid], 0x600)
    if not spans:
        print("selftest FAIL: span_rows returned nothing")
        return 1
    _target, prev, rows = spans[0]
    if [a for _i, a, _c, _n in rows] != [mid, after]:
        print("selftest FAIL: span_rows saw %r in the window" % ([a for _i, a, _c, _n in rows],))
        return 1
    if rows[1][2] != "ObjectProperty" or rows[1][3] != "Canvas":
        print("selftest FAIL: span_rows did not name the occupier past the boundary")
        return 1
    if prev is None or prev[1] != default:
        print("selftest FAIL: span_rows lost the object below the target")
        return 1
    # An `FString` is characters, not bytes: `--show-text` has to print past the eighth one.
    text = "Press A to Skip\x00"
    buf = 0x03100000
    raw = text.encode("utf-16-le")
    for i in range(0, len(raw), 4):
        mem3[buf + i] = struct.unpack("<I", raw[i:i + 4])[0]
    mem3[default + 0x100] = buf
    mem3[default + 0x104] = len(text)
    mem3[default + 0x108] = len(text)
    SHOW_TEXT[0] = True
    shown = dict(walk_fields(r3, default, 0x200))
    SHOW_TEXT[0] = False
    if "Skip" not in shown.get(0x100, ""):
        print("selftest FAIL: --show-text printed %r for a 16-character FString" % (shown.get(0x100),))
        return 1
    # A scalar default: `--find` has to see a dword `walk_fields` never reports.
    mem3[default + 0x40] = 0x3f800000
    mem3[default + 0x44] = 0x3f800000
    found = find_dwords(r3, default, 0x600, {0x3f800000})
    if [off for off, _v in found] != [0x40, 0x44]:
        print("selftest FAIL: find_dwords read %r for a declared float" % (found,))
        return 1
    print("ok   fields: a class member past 0x400 resolves, the old bound missed it, the instance walk"
          " filters the object array by Class, and a field window names the neighbour it crosses into")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=None, help="xemu's GDB stub port")
    ap.add_argument("--xbe", default=None, help="default.xbe of the dump (for the section table)")
    ap.add_argument("--at", type=lambda v: int(v, 0), default=None,
                    help="GNames array address, as tools/xemu/find_names.py measured it on this boot")
    ap.add_argument("--objects", type=lambda v: int(v, 0), default=names.OBJ_ARRAY, help="GObjObjects address")
    ap.add_argument("--object", type=lambda v: int(v, 0), default=None, help="the UObject to read")
    ap.add_argument("--size", type=lambda v: int(v, 0), default=0x600, help="bytes of the object to walk")
    ap.add_argument("--members", action="store_true",
                    help="walk a class object's own dwords as pointers: `Children` heads and the default object")
    ap.add_argument("--start", type=lambda v: int(v, 0), default=0x400,
                    help="with --members, the first offset to read (default 0x400, past the UStruct header)")
    ap.add_argument("--scan", type=lambda v: int(v, 0), default=0x400,
                    help="how far --properties/--compare search for the field chain and the default object")
    ap.add_argument("--selftest", action="store_true",
                    help="check the member scan with a fake reader, no boot needed")
    ap.add_argument("--properties", action="store_true", help="measure and print the class's field chain")
    ap.add_argument("--instances", action="store_true",
                    help="with --object <class object>: list every object in GObjObjects whose own Class is it")
    ap.add_argument("--locate", action="store_true",
                    help="with --instances: which class-object dwords in +0x28..--size hold one of those "
                         "addresses (the default-object back-pointer, found by address not by class)")
    ap.add_argument("--diff", type=lambda v: int(v, 0), default=None,
                    help="diff --object's pointer/array fields against this object, field by field")
    ap.add_argument("--limit", type=int, default=200, help="with --diff, how many differing offsets to print")
    ap.add_argument("--compare", action="store_true", help="find the class default and diff against it")
    ap.add_argument("--show-text", action="store_true", help="print array bytes that read as text (game state)")
    ap.add_argument("--find", type=lambda v: [int(x, 0) for x in v.split(",")], default=None,
                    help="comma-separated dwords; print the offsets in the object that hold one (a scalar default)")
    ap.add_argument("--near", type=lambda v: [int(x, 0) for x in v.split(",")], default=None,
                    help="for each comma-separated address, which objects occupy [ADDR, ADDR+--size)")
    args = ap.parse_args()
    SHOW_TEXT[0] = args.show_text
    if args.selftest:
        return selftest()
    if args.port is None:
        ap.error("--port is required: this tool reads a live guest")
    if args.at is None:
        ap.error("--at is required: pass the GNames address find_names.py measured on this boot")
    if args.object is None and not args.near:
        ap.error("--object is required unless --selftest or --near is given")

    guest = names.Guest(args.host, args.port)
    try:
        data, num = names.name_table(guest, args)
        odata, onum = names.object_array(guest, args)
        r = Reader(guest, names.Names(guest, data, num), odata, onum)
        if args.near:
            cmd_near(r, args)
        else:
            cmd_object(r, args)
    finally:
        guest.close()


if __name__ == "__main__":
    sys.exit(main())
