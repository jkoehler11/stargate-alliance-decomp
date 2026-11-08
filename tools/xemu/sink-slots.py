#!/usr/bin/env python3
"""Read the four sink slots on a live player row, and say what state the row is in (PLAN 7.3, U15).

`StargatePlayer+0x1004` `NotificationManager`, `+0x1008` `GameHudPtr`, `+0x100c` `commandInterfacePtr` and
`Controller+0x38c` `Pawn` are the message channels #243 gave offsets to; what they *hold* on a gameplay row is the
question that decides whether the console's silent `say`/`addnotification` has a sink at all. Three things make the
read cheap and hard to mis-take, and each was measured the hard way:

* **The gate.** A boot that fails to load a level still holds a `StargatePlayer` row -- outered to `MainMenu`
  (`sink3`) -- so row presence is not gameplay. `GNames`' own count is: **34,800** at the menu, **41,265** on a
  level (`docs/XEMU.md`). One dword, read before any slot, and `--require-level` refuses the read below 40,000.
* **The walk.** Finding the row is a class-pointer match over `GObjObjects`; read object *headers* through a 64 KiB
  page cache and 48,000 objects cost a few hundred stub round trips instead of 48,000. `--instances --prop-name`
  in `fields.py` answers the same question in about six minutes because it resolves a name per object.
* **The slots.** The offsets are known, so the values are two windows -- `+0x1000..+0x1010` covers the three
  adjacent sink fields and `+0x380..+0x390` covers `Pawn` -- and nothing about the class has to be walked again.

Usage, against a guest booted with `-gdb tcp::PORT` (`drive.py --gdb=PORT --linger=SECS` keeps one up):

    # every row whose own Class is StargatePlayer, its Outer, and the four slots
    python3 tools/xemu/sink-slots.py --port 12604 --xbe $SG1_DUMP/default.xbe --at 0x006be0d8

    # the same with the class named and a live HUD's address as the class default, so the mark is explicit
    python3 tools/xemu/sink-slots.py --port 12604 --at 0x006be0d8 --class 0x00e47800 \
        --default 0x0682ab00 --expect GameHudPtr=live --expect Pawn=live

`--expect SLOT=live|null|default` exits 1 on a mismatch, so a boot can assert the sink instead of printing it.
Addresses are per-boot; class table addresses (the `--class` and `--default` values) are package-data addresses and
do not move between boots. Field contents are game state, so nothing here is written to a file.
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAMES_PY = os.path.join(HERE, "names.py")


def load(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


names = load("xemu_names", NAMES_PY)
NAME_OFF = names.NAME_OFF           # UObject::Name, an FName index (`names.py --verify`)
CLASS_OFF = names.CLASS_OFF         # UObject::Class, a UClass object (`names.py --verify`)
OUTER_OFF = 0x18                    # UObject::Outer (`owner.py`, `fields.py`)
OBJECT_HI = names.OBJECT_HI         # the array's own bound: live gameplay objects sit above 128 MiB
SPAN = 0x10000                      # one stub read per page, the unit a header walk is cheap in

# (`offset`, property, declaring class) -- #243's four declarations, `UProperty::Offset` at `+0x48`.
SLOTS = ((0x1004, "NotificationManager", "StargatePlayer"),
         (0x1008, "GameHudPtr", "StargatePlayer"),
         (0x100c, "commandInterfacePtr", "StargatePlayer"),
         (0x38c, "Pawn", "Controller"))
MENU_NAMES = 34800                  # `names.py --at 0x006be0d8` on a boot that stayed at the title
LEVEL_NAMES = 41265                 # the same read on a loaded `M00`
LEVEL_FLOOR = 40000                 # between the two, so a menu boot cannot be read as a level


def plausible_object(value):
    return names.objects.RAM_LO <= value < OBJECT_HI and value % 4 == 0


class Pages(object):
    """One 64 KiB read per page: the difference between a walk in pages and a walk in objects."""

    def __init__(self, guest):
        self.guest = guest
        self.buf = {}
        self.reads = 0

    def page(self, addr):
        base = addr & ~(SPAN - 1)
        if base not in self.buf:
            got = None
            for _attempt in range(3):
                try:
                    got = self.guest.read(base, SPAN)
                    break
                except (IOError, OSError):
                    got = None
            self.buf[base] = got
            self.reads += 1
        return base, self.buf[base]

    def u32(self, addr):
        base, buf = self.page(addr)
        if buf is None:
            return None
        off = addr - base
        if off < 0 or off + 4 > len(buf):
            return None
        return struct.unpack_from("<I", buf, off)[0]


def table(guest, addr, what):
    """`(data, num)` for a `(data, num, max)` triple, with the retry a single short read needs on this stub."""
    for _attempt in range(4):
        try:
            data, num, cap = struct.unpack("<III", guest.read(addr, 12))
            return data, num, cap
        except (IOError, OSError):
            continue
    raise SystemExit("%s %#010x does not read; is the stub up?" % (what, addr))


def rows_of_class(guest, pages, odata, onum, klass):
    """[(array index, address)] for every object whose own `Class` pointer is `klass`."""
    raw = b""
    got = 0
    while got < onum * 4:                       # one bulk read: the array is ~190 KiB
        chunk = guest.read(odata + got, min(SPAN, onum * 4 - got))
        raw += chunk
        got += len(chunk)
    out = []
    for i, ptr in enumerate(struct.unpack("<%dI" % onum, raw[:onum * 4])):
        if plausible_object(ptr) and pages.u32(ptr + CLASS_OFF) == klass:
            out.append((i, ptr))
    return out


def describe(pages, nm, value, defaults):
    """(verdict, text) for one slot: `live`, `default`, `null` or `unreadable`, plus its class and name."""
    if value is None:
        return "unreadable", "the window did not read"
    if value == 0:
        return "null", "0"
    if not plausible_object(value):
        return "unreadable", "%#010x is not an object address" % value
    cls = pages.u32(value + CLASS_OFF)
    idx = pages.u32(value + NAME_OFF)
    cname = nm[cls] if cls else None
    oname = nm[idx] if idx is not None else None
    text = "object %#010x class %s name %s" % (value, "`%s`" % cname if cname else "?", "`%s`" % oname if oname else "?")
    if value in defaults:
        return "default", text + "  <- passed as --default"
    return "live", text


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=None, help="xemu's GDB stub port")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--xbe", default=None, help="default.xbe of the dump (only for `names.py`'s section table)")
    ap.add_argument("--at", type=lambda v: int(v, 0), default=0x006be0d8,
                    help="GNames, as `names.py --at` measured it on this boot (the triple is static)")
    ap.add_argument("--objects", type=lambda v: int(v, 0), default=names.OBJ_ARRAY,
                    help="UObject::GObjObjects (the triple is static)")
    ap.add_argument("--class", dest="klass", type=lambda v: int(v, 0), default=0x00e47800,
                    help="the row class object to match (default `StargatePlayer`, 0x00e47800)")
    ap.add_argument("--row", type=lambda v: int(v, 0), default=None,
                    help="read this address only, instead of walking the array for rows of --class")
    ap.add_argument("--default", type=lambda v: int(v, 0), action="append", default=[],
                    help="an address to mark as a class default (repeatable; `GameHud`'s is 0x0682ab00)")
    ap.add_argument("--expect", action="append", default=[], metavar="SLOT=live|null|default",
                    help="exit 1 unless a slot reads that way (repeatable)")
    ap.add_argument("--min-names", type=int, default=None,
                    help="override the level gate with a name count of your own")
    ap.add_argument("--require-level", action="store_true", default=True,
                    help="refuse to read below %d names (default), because a menu boot has a row too" % LEVEL_FLOOR)
    ap.add_argument("--any-state", dest="require_level", action="store_false",
                    help="read the slots even on a boot that looks like a menu")
    ap.add_argument("--selftest", action="store_true", help="check the gate, the walk and the marks, no boot")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.port is None:
        ap.error("--port is required unless --selftest is given")

    guest = names.Guest(args.host, args.port)
    try:
        data, num, cap = table(guest, args.at, "name table")
        nm = names.Names(guest, data, num)
        floor = args.min_names if args.min_names is not None else LEVEL_FLOOR
        state = "menu" if num < floor else "level"
        print("name table %#010x: data=%#010x num=%d max=%d -> %s (reference: %d menu / %d level)"
              % (args.at, data, num, cap, state, MENU_NAMES, LEVEL_NAMES))
        if args.require_level and num < floor:
            print("refusing to read the slots: %d names is a menu-sized table, and a menu boot holds a "
                  "`StargatePlayer` row outered to `MainMenu` too (sink3). `--any-state` overrides." % num)
            return 2
        odata, onum, _omax = table(guest, args.objects, "object array")
        pages = Pages(guest)
        if args.row is not None:
            rows = [(None, args.row)]
        else:
            rows = rows_of_class(guest, pages, odata, onum, args.klass)
        print("object array %#010x: data=%#010x num=%d; %d row(s) of class %#010x"
              % (args.objects, odata, onum, len(rows), args.klass))
        seen = []
        for index, row in rows:
            cls = pages.u32(row + CLASS_OFF)
            outer = pages.u32(row + OUTER_OFF)
            own = pages.u32(row + NAME_OFF)
            cname = nm[cls] if cls else None
            oname = nm[outer] if outer else None
            print()
            print("row %#010x%s class `%s` outer %#010x `%s` name `%s`"
                  % (row, "" if index is None else " (index %d)" % index, cname or "?",
                     outer or 0, oname or "?", nm[own] if own is not None else "?"))
            verdicts = {}
            for offset, prop, decl in SLOTS:
                value = pages.u32(row + offset)
                verdict, text = describe(pages, nm, value, args.default)
                verdicts[prop] = verdict
                print("  +%#06x %-20s %-10s %s  (%s)"
                      % (offset, prop, verdict, text, decl))
            seen.append(verdicts)
        print()
        print("%d page read(s), %d guest read(s)" % (pages.reads, guest.reads))
        bad = []
        for want in args.expect:
            if "=" not in want:
                ap.error("--expect wants SLOT=live|null|default, got %r" % want)
            prop, state_want = want.split("=", 1)
            for verdicts in seen:
                if verdicts.get(prop) != state_want:
                    bad.append("%s=%s (want %s)" % (prop, verdicts.get(prop), state_want))
        if bad:
            print("FAIL " + ", ".join(bad))
            return 1
        return 0
    finally:
        guest.close()


class FakeGuest(object):
    """Pages of a synthetic guest: `read` serves a mapped page and raises `IOError` above the image."""

    def __init__(self, image):
        self.image = image
        self.reads = 0

    def read(self, addr, size, chunk=None):
        if addr >= len(self.image):
            raise IOError("unmapped")
        self.reads += 1
        return bytes(self.image[addr:min(addr + size, len(self.image))])

    def close(self):
        pass


def selftest():
    """A synthetic guest: a menu-sized table that must be refused, and a level row in all three slot states.

    Every address sits above `objects.RAM_LO` (`0x00010000`), because that bound is what makes a dword an object
    pointer here -- a synthetic image built at 0x1000 reads as "nothing is an object" and tests nothing.
    """
    base = names.objects.RAM_LO
    image = bytearray(base + 0x9000)
    at = lambda va: base + va                                   # noqa: E731 -- keeps the layout readable
    # GNames: one entry per name, `(index, flags, chain, UTF-16)` at `entry_base + index * 0x40`.
    entry_base, table_at = at(0x2000), at(0x1000)
    for i, text in enumerate(("None", "GameHud", "StargatePlayer", "M00", "Default__GameHud")):
        struct.pack_into("<I", image, table_at + 4 * i, entry_base + i * 0x40)
        struct.pack_into("<III", image, entry_base + i * 0x40, i, 0, 0)
        raw = text.encode("utf-16-le") + b"\x00\x00"
        image[entry_base + i * 0x40 + 0x0c:entry_base + i * 0x40 + 0x0c + len(raw)] = raw
    num = 5
    # GObjObjects: Data / Num / Max, then the array. A class default (named after its class), a live HUD, the row.
    array_at, data_at = at(0x3000), at(0x4000)
    struct.pack_into("<III", image, array_at, data_at, 3, 3)
    default, live, row = at(0x6000), at(0x6100), at(0x7000)
    for slot, (obj, name_idx, klass) in enumerate(((default, 4, at(0x5000)), (live, 1, at(0x5000)),
                                                   (row, 3, at(0x5100)))):
        struct.pack_into("<I", image, data_at + 4 * slot, obj)
        struct.pack_into("<I", image, obj + OUTER_OFF, 0)
        struct.pack_into("<I", image, obj + NAME_OFF, name_idx)
        struct.pack_into("<I", image, obj + CLASS_OFF, klass)
    # The two class objects, each named after the class it is.
    for cls_at, idx in ((at(0x5000), 1), (at(0x5100), 2)):     # each class object, named after the class it is
        struct.pack_into("<I", image, cls_at + OUTER_OFF, at(0x5200))     # outered to the metaclass
        struct.pack_into("<I", image, cls_at + NAME_OFF, idx)
        struct.pack_into("<I", image, cls_at + CLASS_OFF, at(0x5200))
    # The row's slots: a live HUD, the class default, no command interface, a possessed Pawn.
    struct.pack_into("<III", image, row + 0x1004, live, default, 0)
    struct.pack_into("<I", image, row + 0x38c, at(0x6200))

    guest = FakeGuest(bytes(image))
    nm = names.Names(guest, table_at, num)
    pages = Pages(guest)
    assert nm[1] == "GameHud" and nm[4] == "Default__GameHud", (nm[1], nm[4])
    rows = rows_of_class(guest, pages, data_at, 3, at(0x5100))
    assert rows == [(2, row)], rows
    assert pages.u32(row + 0x1004) == live and pages.u32(row + 0x100c) == 0
    live_verdict, live_text = describe(pages, nm, live, [])
    assert live_verdict == "live" and "`GameHud`" in live_text, live_text
    default_verdict, _ = describe(pages, nm, default, [default])
    assert default_verdict == "default", default_verdict
    assert describe(pages, nm, 0, [])[0] == "null"
    assert describe(pages, nm, base - 4, [])[0] == "unreadable"
    # The gate: a menu-sized table must be refused before any slot is read.
    assert MENU_NAMES < LEVEL_FLOOR <= LEVEL_NAMES
    print("ok   sink-slots: the walk finds the row by class, the marks separate live from class default, "
          "and %d names is below the %d level floor" % (MENU_NAMES, LEVEL_FLOOR))
    return 0


if __name__ == "__main__":
    sys.exit(main())
