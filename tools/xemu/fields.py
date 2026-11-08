#!/usr/bin/env python3
"""Read a live `UObject`'s fields out of the guest: its class, its class default, and what the fields hold (PLAN 4.3, U15).

`names.py` names an object from its own `Class` pointer; this tool reads the *contents*. Two measurements make it
possible without a serialized layout: `UObject::Name` `+0x20` / `Class` `+0x24` (measured by `names.py --verify`), and
`UField::Next` `+0x28` -- the link `UStruct::Serialize` walks (`docs/NATIVE_VM.md`). Everything else is measured here:

* **the class's field chain** -- a `UStruct` holds a `Children` pointer to the first of its `UProperty`/`UFunction`
  objects, each chained through `Next`. The offset is not assumed: every dword in the class object is tried as the
  head and a candidate is only accepted when its chain links are objects whose own `Class` name ends in `Property`
  (or is `Function`/`State`), so two independent reads -- the object header and the name table -- have to agree.
* **the class default object** -- an instance whose `Class` pointer is the class itself. Found by scanning the
  class object for a pointer that is its own instance, and `--scan` sets how far that search runs because a live
  class object carries members past the `UStruct` header (measured: `+0x4e8`). A class that has **no** such
  pointer has no default to diff: on `XboxClient` (`0x00dcea00`) the object array holds exactly one object of
  that class -- the live row itself -- so `--compare` says so and `--instances` is the filter that measures it.
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

    # the bytes *beside* one of those hits: `--find` says a dword is there, `--bytes` shows its neighbours
    python3 tools/xemu/fields.py --port 12345 --at 0x006be0d8 --object 0x0682ab00 --bytes 0x1780,0x100

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
SUPER_OFF = 0x28             # UStruct::Super on this build (measured 2026-11-08: GameHud -> Interaction -> Interactions -> Object)
NAME_OFF = 0x20              # UObject::Name, an FName index (names.py --verify)
CLASS_OFF = 0x24             # UObject::Class, a UClass object (names.py --verify)
OUTER_OFF = 0x18             # UObject::Outer (PLAN 4.3 census notes: a class object's `+0x18` names its package)
OFFSET_AT = 0x48             # UProperty::Offset, written by the linker, never serialized (measured 2026-11-08:
                             # `SkipMatineeTextString` +0x48 = 0x258, `DialogueFontScale` = 0x168,
                             # `NotificationFontScale` = 0x19c -- see docs/NATIVE_PROPERTIES.md §10)
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


def declared_objects(r, cls, limit=400):
    """[(index, address, kind, name)] for every object whose `Outer` is `cls` -- what that class declares.

    A class object does not link its fields (`+0x28` is `Super`, measured 2026-11-08), and a `UProperty` is an
    ordinary object whose `Outer` names the class that declares it -- so the object array *is* the field list.
    Ask it by `Outer`: that dword comes out of the cached 64 KiB page, one array pass, no stub round trip.
    Asking by *name* instead costs a round trip per object, because `object()` resolves the class name with a
    direct `u32(cls + Name)` before it looks at the object's own name -- 48,646 of those did not finish in five
    minutes on a live stub (measured 2026-11-08), while this answered 158 `GameHud` objects in about 80 s.
    """
    out = []
    for index, addr in walk_headers(r):
        if r.try_u32(addr + OUTER_OFF) != cls:
            continue
        got = r.object(addr)
        if not got:
            continue
        out.append((index, addr, got[0], got[1]))
        if len(out) >= limit:
            break
    return out


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
    """The class default object: an instance whose own `Class` pointer is `cls`.

    `None` is a measurement, not a failure: a class object that holds no pointer to an instance of itself has
    no default row this way (measured 2026-11-08 on `XboxClient` `0x00dcea00`, whose only instance in
    `GObjObjects` is the live row). Use `--instances` to see how many rows the class actually has.
    """
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


def _float_note(value):
    """A dword's float reading, when it has one worth printing.

    `1` is the commonest dword an object holds and reads as 1.4e-45, an object pointer reads as a denormal, so a
    magnitude gate is what keeps the annotation from printing a "float" beside every field.
    """
    if value == 0:
        return ""
    f = struct.unpack("<f", struct.pack("<I", value))[0]
    if not (0.0001 <= abs(f) <= 1e7):
        return ""
    if abs(f - int(f)) < 1e-6:
        return "%d.0f" % int(f)
    return "%g" % f


def flat_census(r, addr, off, length, step=0x100):
    """[(start, zero, flat, pointer, other)] per `step`-byte window: where an object's field block stops.

    `--near` bounds an object by its *neighbours*, so any allocation the object array does not hold -- a compressed
    script buffer, a texture mip chain -- reads as part of the object and inflates its extent (measured: the
    `GameHud` default's bytes turn from zeros into deflate-shaped dwords at +0x1800, but the next *object* is at
    +0x5500). A field block is made of zeros, small integers/floats and object pointers, so counting those per
    window shows the boundary: a window full of `other` is not this object's field data.
    """
    rows = []
    for start in range(off, off + length, step):
        zero = flat = pointer = other = unreadable = 0
        for o in range(start, min(start + step, off + length), 4):
            value = r.try_u32(addr + o)
            if value is None:
                unreadable += 1
            elif value == 0:
                zero += 1
            elif r.object(value) is not None:
                pointer += 1
            elif _float_note(value) or value < 0x100000:
                flat += 1
            else:
                other += 1
        rows.append((start, zero, flat, pointer, other, unreadable))
    return rows


def find_prop(chain, name):
    """The chain member declared as `name`, matched case-insensitively: (addr, name, kind) or None.

    A field chain is the class's declaration order, so a property's *name* is readable even though its value slot
    is not: the name lives in the `UProperty` object, and the object is in the chain.
    """
    want = (name or "").lower()
    for addr, fname, kind in chain:
        if (fname or "").lower() == want:
            return addr, fname, kind
    return None


def find_declared(r, cls, name, limit=0x1400, depth=8):
    """`([(class_addr, class_kind, class_name)], [(class_addr, member_off, addr, name, kind)], match)`.

    A class object's declared fields are the objects its member dwords point at, and each one names its *declaring*
    class in `Outer` -- so a name match is checked against `Outer` instead of trusted, which is what keeps a
    `Function` with the same name (`UITextBox.EatStr`) from passing for a property. Inheritance is `Super` at
    `+0x2c`, walked here because the `Children`-shape scan cannot see a parent's fields: `UITextBox`'s own chain is
    one link, so `DisplayText` (a `UIWidget` field) is not in it (measured 2026-11-08).

    `limit` must cover the member area the class object uses: `GameHud`'s fields run to `+0x63c`-class offsets, so
    the default window is `0x1400`, not the `0x400` header a `DefaultObject` search wanted.
    """
    want = (name or "").lower()
    seen, hits, found = [], [], None
    while cls and depth > 0:
        got = r.object(cls)
        if not got:
            break
        seen.append((cls, got[0], got[1]))
        for off in range(NEXT_OFF, limit, 4):
            value = r.try_u32(cls + off)
            if value is None:
                break
            obj = r.object(value)
            if not obj or not (obj[0] or "").endswith(PROPERTY_SUFFIX):
                continue
            if (obj[1] or "").lower() != want:
                continue
            if r.try_u32(value + OUTER_OFF) != cls:
                continue                      # a same-named field of some other class, not this one's
            hits.append((cls, off, value, obj[1], obj[0]))
            if found is None:
                found = (cls, off, value, obj[1], obj[0])
        nxt = r.try_u32(cls + SUPER_OFF)
        if not nxt or nxt in [a for a, _k, _n in seen]:
            break
        cls = nxt
        depth -= 1
    return seen, hits, found


def prop_offset(r, prop, at=OFFSET_AT):
    """The dword at `at` (default `+0x48`) inside a `UProperty` object: the linker's `Offset`.

    Nothing in the package serializes `UProperty::Offset` -- the linker writes it into the live objects -- so `at`
    is measured, not assumed. It is `+0x48` on this build, from three witnesses read on one boot: the
    `SkipMatineeTextString` `StrProperty` holds `0x258` there (the `FString` `'Press A to Skip'` this project
    read off `GameHud`'s class default), `DialogueFontScale` holds `0x168` and `NotificationFontScale` `0x19c`
    (the two config scales, measured off the same object's bytes). Pass `at` only to re-test the position.
    """
    return r.try_u32(prop + at)


def dump_window(r, addr, off, length):
    """[(offset, value, note)] for a raw byte window of the object: the bytes *beside* a value.

    `--find` answers whether a scalar is there and `walk_fields` follows pointers, arrays and text -- neither shows
    the dwords in between, which is where a layout question lives (three `1.0`s two dwords apart, an `FString`
    header next to a float). Nothing is resolved through the object graph: this prints what the guest holds, so the
    reading is the same whether or not a pointer happens to translate.
    """
    rows = []
    for o in range(off, off + length, 4):
        value = r.try_u32(addr + o)
        if value is None:
            rows.append((o, None, "unreadable"))
            break
        note = " ".join(x for x in (_float_note(value), _ascii_note(value)) if x)
        rows.append((o, value, note))
    return rows


def _ascii_note(value):
    raw = struct.pack("<I", value)
    if all(0x20 <= b < 0x7f for b in raw):
        return "'%s'" % raw.decode("ascii")
    return ""


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


def find_runs(hits):
    """Maximal runs of a dword repeated at consecutive offsets: [(value, offset, count)].

    One `1.0` somewhere in a field block says little -- `1` is the commonest dword an object holds -- but three
    *adjacent* dwords of the same value is a declaration order no allocator invents. `GameHud` declares
    `DialogueFontScale`/`NotificationFontScale`/`InteractionFontScale`, so the signature to look for is a run of
    three `0x3f800000`, and reading that off a bare offset list is guesswork.
    """
    by_value = {}
    for off, value in hits:
        by_value.setdefault(value, []).append(off)
    runs = []
    for value, offs in sorted(by_value.items()):
        start = prev = None
        count = 0
        for off in sorted(offs):
            if start is not None and off == prev + 4:
                count += 1
            else:
                if count >= 2:
                    runs.append((value, start, count))
                start, count = off, 1
            prev = off
        if start is not None and count >= 2:
            runs.append((value, start, count))
    return sorted(runs, key=lambda run: (run[1], run[0]))


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


def read_window(r, addr, length):
    """[(offset, value, note)] at an absolute guest address, each dword named when it is an object pointer.

    `--object` walks an object's fields and `--bytes` a window inside one, so neither can read a bare address --
    and that is what following a `TArray` element needs, because a `(data, num, max)` triple names its data
    pointer and not an object. Each dword is resolved through the object graph first (class and name), and
    anything else gets the float/ascii reading `--bytes` prints. A resolving dword costs one stub round trip, so
    keep the window small.
    """
    rows = []
    for o in range(0, length, 4):
        value = r.try_u32(addr + o)
        if value is None:
            rows.append((o, None, "unreadable"))
            break
        got = r.object(value) if value else None
        if got:
            note = "object `%s`%s" % (got[0], " (%s)" % got[1] if got[1] else "")
        else:
            note = " ".join(x for x in (_float_note(value), _ascii_note(value)) if x)
        rows.append((o, value, note))
    return rows


def cmd_read(r, args):
    """Hexdump an absolute address, so an array element or a bare slot can be followed without a fake object."""
    addr = args.read[0]
    length = args.read[1] if len(args.read) > 1 else 0x40
    print("  raw window %#010x..%#010x:" % (addr, addr + length))
    for o, value, note in read_window(r, addr, length):
        if value is None:
            print("    %#010x unreadable" % (addr + o))
            break
        print("    %#010x +%#05x %#010x %12d  %s" % (addr + o, o, value, value, note))


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
    if args.children:
        kids = declared_objects(r, addr, args.limit)
        print("  %d object(s) outered to %#010x (what this class declares):" % (len(kids), addr))
        for index, target, kind, name in kids:
            print("    index %-7d %#010x %-18s `%s`" % (index, target, kind or "?", name or "?"))
        if not kids:
            print("    none: no object in GObjObjects carries this class as its `Outer`")
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
    if args.prop_name:
        seen, hits, found = find_declared(r, cls, args.prop_name, args.scan)
        print("  super chain of class %#010x: %s" % (
            cls, " -> ".join("`%s` %#010x" % (n or "?", a) for a, _k, n in seen) or "none"))
        print("  %d declared `%s` propert%s with `Outer` = its class:" % (
            len(hits), args.prop_name, "y" if len(hits) == 1 else "ies"))
        for klass, off, addr2, fname, kind in hits:
            print("    class %#010x member +%#05x -> %s `%s` at %#010x" % (klass, off, kind, fname, addr2))
        if not found:
            print("  no property named `%s` in the chain: name it from the object array instead" %
                  args.prop_name)
        else:
            klass, mob, addr2, fname, kind = found
            slot = prop_offset(r, addr2, args.prop_at)
            target = args.prop_in or addr
            print("  `%s` is %s at %#010x; dword at +%#x is %s" % (
                fname, kind, addr2, args.prop_at,
                "unreadable" if slot is None else "%#x" % slot))
            if slot is not None:
                print("  value slot %#x read out of %#010x:" % (slot, target))
                for o, value, note in dump_window(r, target, slot, 0x10):
                    if value is None:
                        print("    +%#05x unreadable" % (o,))
                        break
                    print("    +%#05x %#010x %12d  %s" % (o, value, value, note))
    if args.properties:
        off, chain = find_field_chain(r, cls, args.scan)
        print("  field chain head at class+%#x: %d fields" % (off or 0, len(chain)) if off
              else "  no field chain found in the class object's first %#x bytes" % args.scan)
        pb = args.prop_bytes
        for i, (a, fname, kind) in enumerate(chain):
            print("    %3d %#010x %-18s %s" % (i, a, kind, fname))
            if pb:
                poff = pb[0]
                plen = pb[1] if len(pb) > 1 else 0x20
                for o, value, note in dump_window(r, a, poff, plen):
                    if value is None:
                        print("          +%#05x unreadable" % (o,))
                        break
                    print("          +%#05x %#010x %12d  %s" % (o, value, value, note))
        if args.prop_value:
            got = find_prop(chain, args.prop_value)
            if not got:
                print("  no field named `%s` in the chain (%d names)" % (args.prop_value, len(chain)))
            else:
                a, fname, kind = got
                slot = prop_offset(r, a, args.prop_at)
                target = args.prop_in or addr
                print("  `%s` is %s at %#010x; dword at +%#x is %s"
                      % (fname, kind, a, args.prop_at,
                         "unreadable" if slot is None else "%#x" % slot))
                if slot is not None:
                    print("  value slot %#x read out of %#010x:" % (slot, target))
                    for o, value, note in dump_window(r, target, slot, 0x10):
                        if value is None:
                            print("    +%#05x unreadable" % (o,))
                            break
                        print("    +%#05x %#010x %12d  %s" % (o, value, value, note))
    default = None
    if args.compare:
        default = find_default(r, cls, args.scan)
        if default:
            print("  class default %#010x" % default)
        else:
            print("  class default not found: no dword in the class object's first %#x bytes is an instance of"
                  " it" % args.scan)
            print("               ask `--instances`: it counts the objects whose Class is this class")
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
        runs = find_runs(hits)
        if runs:
            print("  %d run(s) of one value at consecutive offsets:" % len(runs))
            for value, start, count in runs:
                print("    %#010x x%d at +%#05x..+%#05x" % (value, count, start, start + 4 * (count - 1)))
        # A scalar question does not need the field walk, and the walk is what makes a wide window expensive:
        # every dword is resolved as an object and then as a possible array head, so a 0x1c00 read over a
        # window whose tail is a neighbour's bytes ran past ten minutes without printing anything. `--find`
        # alone is a page read; `--show-text`/`--diff`/`--compare` keep the walk they need.
        if not (args.show_text or args.diff or args.compare):
            return
    if args.flat is not None:
        off = 0 if args.flat < 0 else args.flat
        end = size if args.flat < 0 else min(off + 0x1000, size)
        rows = flat_census(r, addr, off, end - off, 0x100)
        print("  field census per 0x100 window, +%#05x..+%#05x of %#010x:" % (off, end, addr))
        print("    %-8s %6s %6s %8s %6s %s" % ("window", "zero", "flat", "pointer", "other", "reading"))
        for start, zero, flatc, pointer, other, unreadable in rows:
            note = "unreadable" if unreadable else ("field" if other <= 8 else "NOT field data")
            print("    +%#05x %6d %6d %8d %6d %s" % (start, zero, flatc, pointer, other, note))
        return
    if args.bytes:
        off = args.bytes[0]
        length = args.bytes[1] if len(args.bytes) > 1 else 0x80
        rows = dump_window(r, addr, off, length)
        print("  raw window +%#05x..+%#05x of %#010x:" % (off, off + length, addr))
        for o, value, note in rows:
            if value is None:
                print("    +%#05x unreadable" % (o,))
                break
            print("    +%#05x %#010x %12d  %s" % (o, value, value, note))
        return
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
    # A declared property is named by `Outer`, not by a chain's shape. `Text` is declared on the child, a
    # `Function` of the same name sits beside it (census15's one-link "chain"), and `DisplayText` only exists on
    # the parent that `Super` (+0x2c) reaches -- the exact `UITextBox`/`UIWidget` case of U15.
    parent, child = 0x01001000, 0x01002000
    textp, dispp, eatf = 0x02000300, 0x02000400, 0x02000500
    mem4 = dict(mem)
    mem4[child + SUPER_OFF] = parent
    mem4[parent + SUPER_OFF] = 0
    mem4[child + 0x600] = textp
    mem4[child + 0x63c] = eatf
    mem4[parent + 0x340] = dispp
    mem4[textp + OUTER_OFF] = child
    mem4[dispp + OUTER_OFF] = parent
    mem4[eatf + OUTER_OFF] = child
    objs4 = dict(objs)
    objs4.update({parent: ("Class", "UIWidget"), child: ("Class", "UITextBox"),
                  textp: ("StrProperty", "Text"), dispp: ("StrProperty", "DisplayText"),
                  eatf: ("Function", "Text")})
    r4 = FakeReader(mem4, objs4)
    _seen, hits, found = find_declared(r4, child, "Text")
    if not found or found[2] != textp or found[0] != child:
        print("selftest FAIL: find_declared named `Text` as %r" % (found,))
        return 1
    if len(hits) != 1:
        print("selftest FAIL: find_declared accepted %d `Text` rows; the same-named `Function` passed" %
              len(hits))
        return 1
    _seen, _hits, found = find_declared(r4, child, "DisplayText")
    if not found or found[2] != dispp or found[0] != parent:
        print("selftest FAIL: find_declared did not reach `DisplayText` through `Super` (%r)" % (found,))
        return 1
    _seen, _hits, found = find_declared(r4, child, "NoSuchField")
    if found is not None:
        print("selftest FAIL: find_declared invented `NoSuchField`")
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
    mem3[default + 0x48] = 0x3f800000
    found = find_dwords(r3, default, 0x600, {0x3f800000})
    if [off for off, _v in found] != [0x40, 0x44, 0x48]:
        print("selftest FAIL: find_dwords read %r for a declared float" % (found,))
        return 1
    # Three adjacent `1.0`s are the signature of three consecutive Float declarations; the run is what makes
    # a value a layout, so `--find` prints it instead of leaving three offsets to be read as a coincidence.
    runs = find_runs(found)
    if runs != [(0x3f800000, 0x40, 3)]:
        print("selftest FAIL: find_runs read %r for three adjacent floats" % (runs,))
        return 1
    split = find_dwords(r3, default, 0x600, {0x3f800000, 0x00000001})
    if [run for run in find_runs(split) if run[0] == 0x3f800000] != [(0x3f800000, 0x40, 3)]:
        print("selftest FAIL: a second needle broke the first one's run: %r" % (find_runs(split),))
        return 1
    # The bytes beside a value: a float prints as a float, a small integer does not print as a denormal, and an
    # unreadable tail stops the window instead of inventing zeros.
    mem3[default + 0x200] = 0x00000004
    mem3[default + 0x204] = 0x3f800000
    mem3[default + 0x208] = 0
    rows = dump_window(r3, default, 0x200, 0xc)
    if [o for o, _v, _n in rows] != [0x200, 0x204, 0x208]:
        print("selftest FAIL: dump_window read %r for a three-dword window" % (rows,))
        return 1
    if rows[1][2] != "1.0f":
        print("selftest FAIL: dump_window annotated %r for 0x3f800000" % (rows[1][2],))
        return 1
    if rows[0][2]:
        print("selftest FAIL: dump_window called the integer 4 a float: %r" % (rows[0][2],))
        return 1
    class BriefReader(object):
        def __init__(self, mem):
            self.mem = mem

        def try_u32(self, addr):
            return self.mem.get(addr)

    # A field block ends where the flat data ends: a window of zeros/ints is field data, a window of dwords that
    # resolve to nothing is not, and that boundary is what `--near` cannot see (it only knows objects).
    census = flat_census(r3, default, 0x200, 0x10, 0x10)
    if census[0][1] != 2 or census[0][2] != 2 or census[0][4] != 0:
        print("selftest FAIL: flat_census miscounted a flat window: %r" % (census[0],))
        return 1
    mem3[default + 0x210] = 0x6dd00119
    mem3[default + 0x214] = 0x1e006200
    census = flat_census(r3, default, 0x210, 0x8, 0x8)
    if census[0][4] != 2:
        print("selftest FAIL: flat_census read %r for two non-field dwords" % (census[0],))
        return 1
    brief = BriefReader({0x1000: 0x41c00000})
    tail = dump_window(brief, 0x1000, 0, 0x8)
    if tail[-1][1] is not None or len(tail) != 2:
        print("selftest FAIL: dump_window padded an unreadable tail: %r" % (tail,))
        return 1
    # The property-offset route: the chain names the field, the property object holds the linker's `Offset`, and
    # the value sits at that offset in the instance. A name the chain does not declare must read nothing rather
    # than fall through to the class object.
    mem3[default + 0x258] = 0x05050505      # the already-measured `SkipMatineeTextString` slot
    mem3[prop2 + OFFSET_AT] = 0x258         # the linker's `Offset` (`+0x48`), calibrated on that measurement
    chain2 = [(prop1, "Health", "IntProperty"), (prop2, "SkipMatineeTextString", "StrProperty")]
    got = find_prop(chain2, "skipmatineetextstring")
    if not got or got[0] != prop2:
        print("selftest FAIL: find_prop read %r for a case-folded name" % (got,))
        return 1
    if find_prop(chain2, "TextFontScale") is not None:
        print("selftest FAIL: find_prop invented a field the chain does not name")
        return 1
    slot = prop_offset(r3, got[0])          # no `at`: the measured `+0x48` default has to be the one used
    if slot != 0x258:
        print("selftest FAIL: prop_offset read %r for the calibrated dword" % (slot,))
        return 1
    row = dump_window(r3, default, slot, 4)
    if row[0][1] != 0x05050505:
        print("selftest FAIL: the value at a property's Offset did not read back: %r" % (row,))
        return 1
    # A class's declared objects are the ones outered to it -- and only those. `Outer` reads from the cached page,
    # so the scan is one array pass; an object outered to another class must not appear.
    mem3[mid + OUTER_OFF] = cls
    mem3[after + OUTER_OFF] = 0x02000000
    mem3[default + OUTER_OFF] = 0x02000000
    kids = declared_objects(r3, cls)
    if [a for _i, a, _c, _n in kids] != [mid]:
        print("selftest FAIL: declared_objects returned %r" % ([a for _i, a, _c, _n in kids],))
        return 1
    mem[0x05000000] = default              # a bare address holding an object pointer
    mem[0x05000004] = 0x3f800000           # and a float beside it
    rows = dict((o, (value, note)) for o, value, note in read_window(r, 0x05000000, 8))
    if "Default__FakeClass" not in rows[0][1]:
        print("selftest FAIL: --read did not name the object a bare address holds: %r" % (rows[0][1],))
        return 1
    if "1.0f" not in rows[4][1]:
        print("selftest FAIL: --read did not annotate a float slot: %r" % (rows[4][1],))
        return 1
    print("ok   fields: a class member past 0x400 resolves, the old bound missed it, the instance walk"
          " filters the object array by Class, a field window names the neighbour it crosses into, a declared"
          " name is matched by `Outer` and reached through `Super`, it reaches its value slot through the"
          " linker's `Offset`, and `--read` names the object a bare address holds")
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
    ap.add_argument("--prop-bytes", type=lambda v: [int(x, 0) for x in v.split(",")], default=None,
                    help="OFFSET,LENGTH: with --properties, dump that dword window of every field object "
                         "(UProperty::Offset is one of them; the package serializes none of it)")
    ap.add_argument("--prop-value", default=None,
                    help="with --properties: the declared name whose value slot to read (e.g. TextFontScale)")
    ap.add_argument("--prop-at", type=lambda v: int(v, 0), default=OFFSET_AT,
                    help="the dword position inside a UProperty object that holds `Offset` (default %#x, the "
                         "position three witnesses agree on; override only to re-test it)" % OFFSET_AT)
    ap.add_argument("--prop-name", default=None,
                    help="name a declared property by walking the class object's members and its `Super` chain")
    ap.add_argument("--prop-in", type=lambda v: int(v, 0), default=None,
                    help="with --prop-value: read the value out of this instance instead of --object")
    ap.add_argument("--instances", action="store_true",
                    help="with --object <class object>: list every object in GObjObjects whose own Class is it")
    ap.add_argument("--locate", action="store_true",
                    help="with --instances: which class-object dwords in +0x28..--size hold one of those "
                         "addresses (the default-object back-pointer, found by address not by class)")
    ap.add_argument("--children", action="store_true",
                    help="with --object <class object>: list the objects whose `Outer` is it (what the class "
                         "declares) -- one page-cached array pass, where a name-first walk costs a stub round "
                         "trip per object")
    ap.add_argument("--diff", type=lambda v: int(v, 0), default=None,
                    help="diff --object's pointer/array fields against this object, field by field")
    ap.add_argument("--limit", type=int, default=200, help="with --diff, how many differing offsets to print")
    ap.add_argument("--compare", action="store_true", help="find the class default and diff against it")
    ap.add_argument("--show-text", action="store_true", help="print array bytes that read as text (game state)")
    ap.add_argument("--find", type=lambda v: [int(x, 0) for x in v.split(",")], default=None,
                    help="comma-separated dwords; print the offsets in the object that hold one (a scalar default)")
    ap.add_argument("--bytes", type=lambda v: [int(x, 0) for x in v.split(",")], default=None,
                    help="OFFSET,LENGTH: hexdump that raw window of the object (the bytes beside a value)")
    ap.add_argument("--flat", type=lambda v: int(v, 0), default=None,
                    help="OFFSET: per-window census of zeros/flats/pointers/other from OFFSET --set-size"
                         " bytes (-1 = the whole --size), so a field block's end is visible")
    ap.add_argument("--read", type=lambda v: [int(x, 0) for x in v.split(":")], default=None,
                    help="ADDR[:LEN]: hexdump that absolute guest address (an array element or a bare slot); "
                         "every dword that resolves is named as an object")
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
    if args.object is None and not args.near and not args.read:
        ap.error("--object is required unless --selftest, --near or --read is given")

    guest = names.Guest(args.host, args.port)
    try:
        data, num = names.name_table(guest, args)
        odata, onum = names.object_array(guest, args)
        r = Reader(guest, names.Names(guest, data, num), odata, onum)
        if args.near:
            cmd_near(r, args)
        elif args.read:
            cmd_read(r, args)
        else:
            cmd_object(r, args)
    finally:
        guest.close()


if __name__ == "__main__":
    sys.exit(main())
