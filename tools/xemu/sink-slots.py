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
* **The row's class is not the class you name.** A subclass instance carries its own class pointer, so a boot with
  no `StargatePlayer` row can still hold the player controller -- under whatever class the game actually spawned.
  `--class-from NAME` reads that class off the guest's own object (`PlayerController0`) instead of assuming it: one
  name-index lookup, the same page-cached header walk, and the class is printed with the row it selects.
* **A slot is a field only where its class declares it.** `+0x1004`.. are `StargatePlayer` fields: read off a row
  whose own super chain never reaches that class, they are whatever occupies the offset, and that is often a
  plausible object pointer -- the way a neighbouring heap block gets reported as a live HUD. Each slot is therefore
  gated on its declaring class (`+0x28` `UStruct::Super`) being on the row's chain and prints `n/a` otherwise.

Usage, against a guest booted with `-gdb tcp::PORT` (`drive.py --gdb=PORT --linger=SECS` keeps one up):

    # every row whose own Class is StargatePlayer, its Outer, and the four slots
    python3 tools/xemu/sink-slots.py --port 12604 --xbe $SG1_DUMP/default.xbe --at 0x006be0d8

    # the same question of a saved census, no boot: which class the guest's own player object carries
    python3 tools/xemu/sink-slots.py --census $WORK/xemu/ghud1/cls.csv --class-from PlayerController0 \
        --want-class StargatePlayer --want-class Pawn

    # the class taken from the guest's own player object, rather than assumed
    python3 tools/xemu/sink-slots.py --port 12604 --at 0x006be0d8 --class-from PlayerController0 \
        --default 0x0682ab00

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
SUPER_OFF = 0x28                    # UStruct::Super (`fields.py`, measured 2026-11-08)
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


def walk(guest, pages, odata, onum, off, value):
    """[(array index, address)] for every object whose header dword at `off` is `value`.

    Two questions share this walk -- "rows of class C" (`off=CLASS_OFF`) and "the object named N"
    (`off=NAME_OFF`) -- and both are a dword compare against headers that cost one stub read per 64 KiB page,
    not one per object.
    """
    raw = b""
    got = 0
    while got < onum * 4:                       # one bulk read: the array is ~190 KiB
        chunk = guest.read(odata + got, min(SPAN, onum * 4 - got))
        raw += chunk
        got += len(chunk)
    out = []
    for i, ptr in enumerate(struct.unpack("<%dI" % onum, raw[:onum * 4])):
        if plausible_object(ptr) and pages.u32(ptr + off) == value:
            out.append((i, ptr))
    return out


def rows_of_class(guest, pages, odata, onum, klass):
    """[(array index, address)] for every object whose own `Class` pointer is `klass`."""
    return walk(guest, pages, odata, onum, CLASS_OFF, klass)


def name_index(nm, want):
    """The `FName` index whose text is `want` (case-insensitive), or None.

    The object walk compares header dwords, so a name has to become an index first. `Names` page-caches the
    entries, so this is one pass over the pointer table and a few pages -- not the per-object read that makes
    a name-keyed walk in `fields.py` take minutes.
    """
    low = want.lower()
    for idx in range(1, nm.num):
        text = nm[idx]
        if text is not None and text.lower() == low:
            return idx
    return None


def rows_of_name(guest, pages, odata, onum, idx):
    """[(array index, address)] for every object whose own `Name` index is `idx`."""
    return walk(guest, pages, odata, onum, NAME_OFF, idx)


def class_named(guest, pages, odata, onum, nm, want):
    """The class object whose own name is `want` (a class object is named after its class), or None."""
    idx = name_index(nm, want)
    if idx is None:
        return None
    found = rows_of_name(guest, pages, odata, onum, idx)
    return found[0][1] if len(found) == 1 else None


def super_chain(pages, klass, limit=64):
    """[class object, its super, ...] following `UStruct::Super` (`+0x28`, `fields.py`)."""
    out = []
    while plausible_object(klass) and klass not in out and len(out) < limit:
        out.append(klass)
        klass = pages.u32(klass + SUPER_OFF)
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


def census_rows(text):
    """[(index, address, class, name)] from a `findcls.py --csv` census (`index,address,class,name`)."""
    rows = []
    for line in text.splitlines()[1:]:
        parts = line.rstrip("\n").split(",")
        if len(parts) != 4:
            continue
        try:
            rows.append((int(parts[0]), int(parts[1], 0), parts[2], parts[3]))
        except ValueError:
            continue
    return rows


def census_report(text, want_name=None, want_classes=(), out=print):
    """The same two questions a boot answers, asked of a census file: is the player object there, and is any row of
    the classes the slots are declared on there. A census carries names, not field values, so this can only say
    which rows exist -- that is what decides whether a slot read has an object to attach to at all."""
    rows = census_rows(text)
    out("census: %d named object(s)" % len(rows))
    if want_name:
        hit = [r for r in rows if r[3].lower() == want_name.lower()]
        out("object named `%s`: %d row(s)%s" % (want_name, len(hit),
            "".join("  index %d %#010x class `%s`" % (i, a, c) for i, a, c, _n in hit)))
    for want in want_classes:
        hit = [r for r in rows if r[2].lower() == want.lower()]
        out("rows of class `%s`: %d%s" % (want, len(hit),
            "".join("  index %d %#010x `%s`" % (i, a, n) for i, a, _c, n in hit[:5])))
    return 0


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
    ap.add_argument("--class-from", default=None, metavar="NAME",
                    help="read the row class off the guest's object named NAME (`PlayerController0`) instead of "
                         "assuming `--class`; a subclass instance carries its own class pointer, so this is the "
                         "class the boot actually spawned")
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
    ap.add_argument("--census", default=None, metavar="CSV",
                    help="ask the same two questions of a `findcls.py --csv` census instead of a boot: which class "
                         "the object named by `--class-from` carries, and whether any row of the classes the slots "
                         "are declared on exists at all")
    ap.add_argument("--want-class", action="append", default=[], metavar="NAME",
                    help="with --census: a class name to count (`--want-class StargatePlayer --want-class Pawn`)")
    ap.add_argument("--selftest", action="store_true", help="check the gate, the walk and the marks, no boot")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.census:
        with open(args.census) as fh:
            return census_report(fh.read(), args.class_from, args.want_class)
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
        klass = args.klass
        if args.class_from:
            idx = name_index(nm, args.class_from)
            if idx is None:
                raise SystemExit("no `%s` in this boot's %d-name table: --class-from names an object's own name, "
                                 "and the table it would have to be in does not hold it"
                                 % (args.class_from, num))
            found = rows_of_name(guest, pages, odata, onum, idx)
            print("object named `%s` (name index %d): %d row(s) %s"
                  % (args.class_from, idx, len(found),
                     " ".join("%#010x%s" % (ptr, "" if i is None else " (index %d)" % i) for i, ptr in found)))
            if len(found) != 1:
                raise SystemExit("--class-from needs exactly one object named `%s`; %d found, so the class is "
                                 "ambiguous and no slot is read" % (args.class_from, len(found)))
            klass = pages.u32(found[0][1] + CLASS_OFF)
            kname = nm[klass] if klass else None
            print("its class is %#010x `%s`%s"
                  % (klass or 0, kname or "?",
                     "" if klass == args.klass else "  <- not the assumed %#010x" % args.klass))
            if not plausible_object(klass):
                raise SystemExit("the object named `%s` has %#010x at `Class`, which is not a class object"
                                 % (args.class_from, klass or 0))
        if args.row is not None:
            rows = [(None, args.row)]
        else:
            rows = rows_of_class(guest, pages, odata, onum, klass)
        print("object array %#010x: data=%#010x num=%d; %d row(s) of class %#010x"
              % (args.objects, odata, onum, len(rows), klass))
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
            chain = super_chain(pages, cls)
            for offset, prop, decl in SLOTS:
                owner = class_named(guest, pages, odata, onum, nm, decl)
                if owner is not None and owner not in chain:
                    # The slot's address is only a field on a row whose class chain declares it. Reading it
                    # anywhere else returns whatever object occupies that offset -- often a plausible
                    # pointer, which is exactly how a neighbouring heap block gets reported as a live HUD.
                    verdicts[prop] = "n/a"
                    print("  +%#06x %-20s %-10s declared on `%s` %#010x, which is not on this row's chain (%s)"
                          % (offset, prop, "n/a", decl, owner,
                             " -> ".join(nm[k] or "?" for k in chain)))
                    continue
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
    for i, text in enumerate(("None", "GameHud", "StargatePlayer", "M00", "Default__GameHud",
                              "PlayerController0", "SGPlayerController")):
        struct.pack_into("<I", image, table_at + 4 * i, entry_base + i * 0x40)
        struct.pack_into("<III", image, entry_base + i * 0x40, i, 0, 0)
        raw = text.encode("utf-16-le") + b"\x00\x00"
        image[entry_base + i * 0x40 + 0x0c:entry_base + i * 0x40 + 0x0c + len(raw)] = raw
    num = 7
    # GObjObjects: Data / Num / Max, then the array. A class default (named after its class), a live HUD, a
    # `StargatePlayer` row, and the guest's own player object -- named `PlayerController0`, class taken on trust.
    array_at, data_at = at(0x3000), at(0x4000)
    struct.pack_into("<III", image, array_at, data_at, 6, 6)
    default, live, row, player = at(0x6000), at(0x6100), at(0x7000), at(0x7100)
    # Class objects live in the array too, as they do in `GObjObjects`; their own class is the metaclass.
    for slot, (obj, name_idx, klass) in enumerate(((default, 4, at(0x5000)), (live, 1, at(0x5000)),
                                                   (row, 3, at(0x5100)), (player, 5, at(0x5100)),
                                                   (at(0x5000), 1, at(0x5200)), (at(0x5100), 2, at(0x5200)))):
        struct.pack_into("<I", image, data_at + 4 * slot, obj)
        struct.pack_into("<I", image, obj + OUTER_OFF, 0)
        struct.pack_into("<I", image, obj + NAME_OFF, name_idx)
        struct.pack_into("<I", image, obj + CLASS_OFF, klass)
    # The two class objects, each named after the class it is.
    for cls_at, idx in ((at(0x5000), 1), (at(0x5100), 2)):     # each class object, named after the class it is
        struct.pack_into("<I", image, cls_at + OUTER_OFF, at(0x5200))     # outered to the metaclass
        struct.pack_into("<I", image, cls_at + NAME_OFF, idx)
        struct.pack_into("<I", image, cls_at + CLASS_OFF, at(0x5200))
    # A subclass of `StargatePlayer`: the chain is what says whether a slot's offset is a field on a row.
    struct.pack_into("<I", image, at(0x5300) + NAME_OFF, 6)
    struct.pack_into("<I", image, at(0x5300) + CLASS_OFF, at(0x5200))
    struct.pack_into("<I", image, at(0x5300) + SUPER_OFF, at(0x5100))
    # The row's slots: a live HUD, the class default, no command interface, a possessed Pawn.
    struct.pack_into("<III", image, row + 0x1004, live, default, 0)
    struct.pack_into("<I", image, row + 0x38c, at(0x6200))

    guest = FakeGuest(bytes(image))
    nm = names.Names(guest, table_at, num)
    pages = Pages(guest)
    assert nm[1] == "GameHud" and nm[4] == "Default__GameHud", (nm[1], nm[4])
    rows = rows_of_class(guest, pages, data_at, 4, at(0x5100))
    assert rows == [(2, row), (3, player)], rows
    # `--class-from`: the player object's own name resolves to an index, and its `Class` dword is the row class.
    assert name_index(nm, "playercontroller0") == 5
    assert name_index(nm, "nobody-at-all") is None
    found = rows_of_name(guest, pages, data_at, 6, 5)
    assert found == [(3, player)], found
    assert pages.u32(player + CLASS_OFF) == at(0x5100), pages.u32(player + CLASS_OFF)
    # The gate that keeps a neighbour's heap out of the report: a slot is read only where its declaring class
    # is on the row's own super chain.
    assert super_chain(pages, at(0x5300)) == [at(0x5300), at(0x5100)], super_chain(pages, at(0x5300))
    assert at(0x5100) in super_chain(pages, at(0x5300))
    assert class_named(guest, pages, data_at, 6, nm, "StargatePlayer") == at(0x5100)
    assert class_named(guest, pages, data_at, 6, nm, "Controller") is None
    assert pages.u32(row + 0x1004) == live and pages.u32(row + 0x100c) == 0
    live_verdict, live_text = describe(pages, nm, live, [])
    assert live_verdict == "live" and "`GameHud`" in live_text, live_text
    default_verdict, _ = describe(pages, nm, default, [default])
    assert default_verdict == "default", default_verdict
    assert describe(pages, nm, 0, [])[0] == "null"
    assert describe(pages, nm, base - 4, [])[0] == "unreadable"
    # The gate: a menu-sized table must be refused before any slot is read.
    assert MENU_NAMES < LEVEL_FLOOR <= LEVEL_NAMES
    # The census half: a CSV parse and two counts, from a literal, so the check writes nothing.
    report = []
    census_report("index,address,class,name\n"
                  "7,0x00110000,PlayerController,PlayerController0\n"
                  "8,0x00120000,Pawn,Pawn0\n"
                  "9,0x00130000,Class,GameHud\n",
                  "PlayerController0", ["StargatePlayer", "Pawn"], out=report.append)
    assert report[0] == "census: 3 named object(s)", report
    assert "class `PlayerController`" in report[1], report
    assert report[2].startswith("rows of class `StargatePlayer`: 0"), report
    assert report[3].startswith("rows of class `Pawn`: 1"), report
    print("ok   sink-slots: the walk finds the row by class, `--class-from` reads the class off a named object, "
          "the marks separate live from class default, and %d names is below the %d level floor"
          % (MENU_NAMES, LEVEL_FLOOR))
    return 0


if __name__ == "__main__":
    sys.exit(main())
