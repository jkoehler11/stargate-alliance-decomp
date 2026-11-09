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
  in `fields.py` answers the same question in about six minutes because it resolves a name per object. A page is
  16 KiB requests, and a request the stub cannot answer is re-read in 4 KiB sub-pages, because one unmapped byte
  used to make the whole page -- and every object in it -- read as absent (`sink12`: 9.7 s a page, 93 pages in 900 s).
* **The slots.** The offsets are known, so the values are two windows -- `+0x1000..+0x1010` covers the three
  adjacent sink fields and `+0x380..+0x390` covers `Pawn` -- and nothing about the class has to be walked again.
* **Newest first.** `GObjObjects` appends, so the row a level spawns at load sits at the *end* of the array
  (`sink1`: index 47,885 of 48,646). `--tail N` scans the last N entries from the end backwards, which is tens of
  page reads instead of 347 -- and it is a shortcut, not a census: zero rows from it never means "no row".
* **The slot gate needs no walk at all.** Whether `+0x1004` is a field on a row is a question about the row's own
  super chain, so the declaring class is matched by *name* along the chain (one page per link) instead of being
  looked up in `GObjObjects` -- the lookup was four full array passes per row.
* **The row's class is not the class you name.** A subclass instance carries its own class pointer, so a boot with
  no `StargatePlayer` row can still hold the player controller -- under whatever class the game actually spawned.
  `--class-from NAME` reads that class off the guest's own object (`PlayerController0`) instead of assuming it: one
  name-index lookup, the same page-cached header walk, and the class is printed with the row it selects.
* **A class pointer is not a name index.** `UObject::Class` names an object, not an `FName`, so the class name is
  that object's own `Name` -- read as an index it answers `?` for a class table the boot can name perfectly well
  (`sink13`, and every class in its report). `class_name` is one extra read per object.
* **`Outer` is a pointer too.** The row's outer column resolved `nm[outer]`, i.e. an object address read as an
  `FName` index, so every outer printed `?` (`sink15`: `outer 0x06b40000 ?` while the same boot names that
  object `M00`. `object_name` resolves the outer object's own `Name`, one read.
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
import time

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
SPAN = 0x10000                      # one cached page: the unit an object-header walk is cheap in
CHUNK = 0x4000                      # the request size inside a page: 4 requests, measured 0.2 s of a 64 KiB page
FINE = 0x1000                       # the sub-page a failed chunk is re-read in, so one hole costs 4 KiB, not 64

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


def die(text, code=4):
    """A named stop: the read says what ended it, instead of an empty traceback or a silent wait."""
    print("FAIL " + text)
    raise SystemExit(code)


class Pages(object):
    """One cached page per region the walk touches: the difference between a walk in pages and a walk in objects.

    A walk that takes minutes needs to be able to say two things about a run that never finishes: whether the
    guest is still there, and how far it got. `probe` is an address known to be mapped (the name table's own
    data), re-read after a page that had holes -- a guest that has died takes the stub with it, and those reads
    fail, whereas an unmapped page inside the array is ordinary and must keep the walk going. `deadline` is the
    wall-clock bound; both raise a message instead of returning nothing.

    **A 64 KiB request that crosses one unmapped byte fails whole, and `sink12` paid for that twice.** The stub
    answers `E` for a request that touches memory that is not mapped, so a page holding a mapped object *and* a hole
    answered nothing for either -- and the retry path reconnected, interrupted and slept for each attempt, which
    `page()` then repeated three times. That is the 9.7 s a page cost in `sink12`'s tail walk (93 pages in the 900 s
    deadline), and it hid objects as well as time: the row in the mapped half read as absent. Two changes: a failed
    16 KiB chunk is re-read in 4 KiB sub-pages and only the sub-pages that do not answer are marked missing (`u32`
    answers None for an address inside one, and the bytes around it still read), and every request is
    `attempts=1` -- an `E` is a verdict about *memory*, not about the stub, so it is not worth a reconnect.
    """

    PROGRESS_EVERY = 64

    def __init__(self, guest, probe=None, progress=False, deadline=None):
        self.guest = guest
        self.buf = {}
        self.reads = 0
        self.probe = probe
        self.progress = progress
        self.deadline = deadline
        self.started = time.monotonic()
        self.lost = 0              # pages that had at least one unmapped 4 KiB sub-page
        self.walked = 0
        self.requests = 0          # stub requests made by page reads
        self.unanswered = 0        # requests the stub could not answer (unmapped, not dead)
        self.split = 0             # chunks that failed and were re-read as 4 KiB sub-pages
        self.spent = 0.0           # seconds inside `page()`
        self.slowest = (0.0, 0, 0)  # (seconds, page base, holes) of the worst page so far

    def _check(self):
        if self.deadline and time.monotonic() - self.started > self.deadline:
            die("the read passed its %.0fs deadline after %d page read(s): the walk over %d objects is the cost, "
                "and a boot that has to be held this long is not the boot to measure on. `--deadline` raises the "
                "bound, `--row ADDR` skips the walk" % (self.deadline, self.reads, self.walked))

    def _read(self, addr, size):
        """One request, one attempt: an `E` here means "not mapped", and the stub is still answering."""
        self.requests += 1
        try:
            got = self.guest.read(addr, size, chunk=size, attempts=1)
        except (IOError, OSError):
            self.unanswered += 1
            return None
        if got is None or len(got) != size:
            self.unanswered += 1
            return None
        return got

    def page(self, addr):
        self._check()
        base = addr & ~(SPAN - 1)
        if base not in self.buf:
            started = time.monotonic()
            data = bytearray(SPAN)
            missing = set()
            for off in range(0, SPAN, CHUNK):
                got = self._read(base + off, CHUNK)
                if got is not None:
                    data[off:off + CHUNK] = got
                    continue
                self.split += 1
                for j in range(CHUNK // FINE):
                    at = off + j * FINE
                    sub = self._read(base + at, FINE)
                    if sub is None:
                        missing.add(at // FINE)
                    else:
                        data[at:at + FINE] = sub
            self.buf[base] = (bytes(data), missing)
            self.reads += 1
            if missing:
                self.lost += 1
            took = time.monotonic() - started
            self.spent += took
            if took > self.slowest[0]:
                self.slowest = (took, base, len(missing))
            if self.probe is not None and missing:
                # A hole is ordinary; a hole on a page whose probe *also* fails is a guest that has gone away.
                try:
                    self.guest.read(self.probe, 4, attempts=1)
                except (IOError, OSError):
                    die("the stub stopped answering at page %#010x: %#010x, which read at the start, no longer "
                        "does -- the guest is gone (look for xemu's own exit line), so the row cannot be found "
                        "and no slot is read" % (base, self.probe))
            if self.progress and self.reads % self.PROGRESS_EVERY == 0:
                sys.stderr.write("walk: %d page read(s), %d request(s), %d unanswered, %d hole page(s), %.0fs\n"
                                 % (self.reads, self.requests, self.unanswered, self.lost,
                                    time.monotonic() - self.started))
                sys.stderr.flush()
        return base, self.buf[base]

    def u32(self, addr):
        base, (buf, missing) = self.page(addr)
        off = addr - base
        if off < 0 or off + 4 > len(buf):
            return None
        if (off // FINE) in missing or ((off + 3) // FINE) in missing:
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


def walk(guest, pages, odata, onum, off, value, tail=None):
    """[(array index, address)] for every object whose header dword at `off` is `value`.

    Two questions share this walk -- "rows of class C" (`off=CLASS_OFF`) and "the object named N"
    (`off=NAME_OFF`) -- and both are a dword compare against headers that cost one stub read per 64 KiB page,
    not one per object. `off` may be a tuple of offsets, compared in one pass: the pages are cached, so a second
    dword costs no read, and `rows_holding` needs both (`+0x00` holds a class *table*, `+0x24` a `UClass`).

    `tail` scans only the last N entries, from the end backwards: the array appends, so a level's freshly
    spawned objects are the newest ones, and the row `sink1` held was index 47,885 of 48,646 -- 761 from the
    end, against 347 page reads for the whole pass. Matches come back newest-first. A tail scan is a
    *shortcut, not a census*: `[]` from it says the row is not among the newest N objects, never that it is
    absent, and the caller prints which of the two it is holding.
    """
    first = 0 if tail is None else max(0, onum - tail)
    pages.walked = onum - first
    offs = tuple(off) if isinstance(off, (tuple, list)) else (off,)
    raw = b""
    got = 0
    while got < onum * 4:                       # one bulk read: the array is ~190 KiB
        chunk = guest.read(odata + got, min(SPAN, onum * 4 - got))
        raw += chunk
        got += len(chunk)
    values = struct.unpack("<%dI" % onum, raw[:onum * 4])
    out = []
    for i in range(onum - 1, first - 1, -1):
        ptr = values[i]
        if plausible_object(ptr) and any(pages.u32(ptr + o) == value for o in offs):
            out.append((i, ptr))
    return out


def rows_of_class(guest, pages, odata, onum, klass, tail=None):
    """[(array index, address)] for every object whose own `Class` pointer is `klass` (newest first)."""
    return walk(guest, pages, odata, onum, CLASS_OFF, klass, tail=tail)


def rows_holding(guest, pages, odata, onum, table, tail=None):
    """[(array index, address)] for every object that *opens* with `table`, or carries it at `+0x24`.

    A class **table** -- the `.rdata` vtable `inventory/xbe_class_table_names.csv` names, `Console` `0x0050cb80`
    -- is the object's **first** dword: the typed-into `Console` opens `00 50 cb 80 ...` (`hud-link.py`, a
    whole-RAM dump). `+0x24` is `UObject::Class`, which is a `UClass` *object* (`names.py --verify`), so a walk
    that screens `+0x24` for a table address cannot match a live object at all: `hudsay3` walked all 48,646
    entries of a loaded `M00` for `0x0050cb80` and read zero `Console` rows, while `ghud1`'s census of another
    level boot names one at index 36,148. Both dwords are compared per page, so the second test is free.
    """
    return walk(guest, pages, odata, onum, (0x00, CLASS_OFF), table, tail=tail)


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


def rows_of_name(guest, pages, odata, onum, idx, tail=None):
    """[(array index, address)] for every object whose own `Name` index is `idx` (newest first)."""
    return walk(guest, pages, odata, onum, NAME_OFF, idx, tail=tail)


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


def chain_names(pages, nm, chain):
    """The class names along a super chain, its own first -- one page per link.

    This is the slot gate without an array walk: "is `+0x1004` a field on this row" is a question about the
    row's chain, and the chain already names its classes. `class_named` answered the same question by searching
    `GObjObjects` for the declaring class object, which is 347 page reads *per slot*.
    """
    return [nm[pages.u32(k + NAME_OFF)] or "?" for k in chain]


def class_name(pages, nm, obj):
    """The *class* name of an object: `UObject::Class` is a class object, and that object's own `Name` is the index.

    `nm[pages.u32(obj + CLASS_OFF)]` was the first version, and it read a class *pointer* as an `FName` index -- so
    every class came back `?` on a boot whose class table is named (`sink13`: `row 0x08842800 class ?`, and the live
    `GameHud` at `+0x1008` printed `class ?` beside its own name). A class object is named after its class, the same
    rule `class_named` uses, so this is one extra read per object and no new assumption -- and the class object's
    own `Class` is the metaclass, which is not what a caller wants here.
    """
    cls = pages.u32(obj + CLASS_OFF)
    if not plausible_object(cls):
        return None
    idx = pages.u32(cls + NAME_OFF)
    return nm[idx] if idx is not None else None


def object_name(pages, nm, obj):
    """An object's own `Name`, resolved from the object pointer -- one read, and the report's `Outer` column.

    `UObject::Outer` is a pointer to an object, not an `FName` index, so `nm[outer]` asks the name table for entry
    number 0x06b40000 and answers `?` for an outer the boot can name perfectly well (`sink15`: the row's outer
    `0x06b40000`, the `M00` object, printed `?` on a row whose class was already named). This is the same bug
    `class_name` had one column over, and the same one-read fix.
    """
    if not plausible_object(obj):
        return None
    idx = pages.u32(obj + NAME_OFF)
    return nm[idx] if idx is not None else None


def describe(pages, nm, value, defaults):
    """(verdict, text) for one slot: `live`, `default`, `null` or `unreadable`, plus its class and name."""
    if value is None:
        return "unreadable", "the window did not read"
    if value == 0:
        return "null", "0"
    if not plausible_object(value):
        return "unreadable", "%#010x is not an object address" % value
    cname = class_name(pages, nm, value)
    idx = pages.u32(value + NAME_OFF)
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
    ap.add_argument("--tail", type=int, default=None, metavar="N",
                    help="scan only the last N object-array entries, newest first: the row a level spawns at "
                         "load sits at the end (the whole array is 347 page reads). A shortcut, not a census")
    ap.add_argument("--deadline", type=float, default=900.0,
                    help="seconds the walk may take before it stops and names itself (default 900; 0 disables); "
                         "a boot held this long is the boot where xemu has already been seen to die mid-read")
    ap.add_argument("--progress", action="store_true",
                    help="a line per %d page reads on stderr, so a minutes-long walk is visible while it runs"
                         % Pages.PROGRESS_EVERY)
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
        pages = Pages(guest, probe=data, progress=args.progress, deadline=args.deadline)
        if args.tail is not None:
            if args.tail <= 0:
                ap.error("--tail wants a positive entry count")
            print("tail scan: the last %d of %d array entries, newest first (`GObjObjects` appends, so the row a "
                  "level spawns at load is at the end) -- a zero here says the row is not among them, not that "
                  "it is absent" % (min(args.tail, onum), onum))
        klass = args.klass
        if args.class_from:
            idx = name_index(nm, args.class_from)
            if idx is None:
                raise SystemExit("no `%s` in this boot's %d-name table: --class-from names an object's own name, "
                                 "and the table it would have to be in does not hold it"
                                 % (args.class_from, num))
            # A named object is not necessarily a new one, so this walk is the whole array even under --tail.
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
            rows = rows_of_class(guest, pages, odata, onum, klass, tail=args.tail)
        print("object array %#010x: data=%#010x num=%d, %s; %d row(s) of class %#010x"
              % (args.objects, odata, onum,
                 "scanned all" if args.tail is None else "scanned the last %d (newest first)" % min(args.tail, onum),
                 len(rows), klass))
        if not rows and args.tail is not None:
            print("no row in the newest %d objects: drop `--tail` to walk all %d before calling the row absent"
                  % (min(args.tail, onum), onum))
        seen = []
        for index, row in rows:
            cls = pages.u32(row + CLASS_OFF)
            outer = pages.u32(row + OUTER_OFF)
            own = pages.u32(row + NAME_OFF)
            cname = class_name(pages, nm, row)
            oname = object_name(pages, nm, outer)
            print()
            print("row %#010x%s class `%s` outer %#010x `%s` name `%s`"
                  % (row, "" if index is None else " (index %d)" % index, cname or "?",
                     outer or 0, oname or "?", nm[own] if own is not None else "?"))
            verdicts = {}
            chain = super_chain(pages, cls)
            names_on_chain = chain_names(pages, nm, chain)
            for offset, prop, decl in SLOTS:
                if decl not in names_on_chain:
                    # The slot's address is only a field on a row whose class chain declares it. Reading it
                    # anywhere else returns whatever object occupies that offset -- often a plausible
                    # pointer, which is exactly how a neighbouring heap block gets reported as a live HUD.
                    verdicts[prop] = "n/a"
                    print("  +%#06x %-20s %-10s declared on `%s`, which is not on this row's chain (%s)"
                          % (offset, prop, "n/a", decl, " -> ".join(names_on_chain)))
                    continue
                value = pages.u32(row + offset)
                verdict, text = describe(pages, nm, value, args.default)
                verdicts[prop] = verdict
                print("  +%#06x %-20s %-10s %s  (%s)"
                      % (offset, prop, verdict, text, decl))
            seen.append(verdicts)
        print()
        print("%d page read(s), %d request(s) (%d unanswered, %d re-read in 4 KiB sub-pages, %d page(s) with a hole), "
              "%.1fs in reads (slowest page %#010x: %.1fs, %d hole(s)), %.0fs total"
              % (pages.reads, pages.requests, pages.unanswered, pages.split, pages.lost, pages.spent,
                 pages.slowest[1], pages.slowest[0], pages.slowest[2], time.monotonic() - pages.started))
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

    def read(self, addr, size, chunk=None, attempts=None):
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
    # `Outer` is a pointer to an object, not an `FName` index: the row's outer is a `M00` object, and the report
    # must print that object's own name (`sink15` printed `?` for `0x06b40000`, which the boot names `M00`).
    struct.pack_into("<I", image, at(0x6300) + NAME_OFF, 3)
    struct.pack_into("<I", image, at(0x6300) + CLASS_OFF, at(0x5200))
    struct.pack_into("<I", image, row + OUTER_OFF, at(0x6300))
    struct.pack_into("<III", image, row + 0x1004, live, default, 0)
    struct.pack_into("<I", image, row + 0x38c, at(0x6200))

    # A class *table* is the object's first dword (`hud-link.py`), and `+0x24` holds the `UClass` *object*, so a
    # walk that screens `+0x24` for a table address matches nothing -- `hudsay3` walked 48,646 objects for
    # `Console`'s table and read zero. The live HUD opens with the table here, and `rows_holding` finds it.
    struct.pack_into("<I", image, live, 0x0050cb80)

    guest = FakeGuest(bytes(image))
    nm = names.Names(guest, table_at, num)
    pages = Pages(guest)
    assert nm[1] == "GameHud" and nm[4] == "Default__GameHud", (nm[1], nm[4])
    rows = rows_of_class(guest, pages, data_at, 4, at(0x5100))
    assert rows == [(3, player), (2, row)], rows          # newest first
    # `--tail N` scans the last N entries only, and says nothing about the ones it did not read.
    assert rows_of_class(guest, pages, data_at, 4, at(0x5100), tail=1) == [(3, player)]
    assert rows_of_class(guest, pages, data_at, 4, at(0x5100), tail=4) == [(3, player), (2, row)]
    assert pages.walked == 4, pages.walked
    # The same array, keyed on a class *table*: `+0x24` holds no table address, so only the first dword matches.
    assert rows_of_class(guest, pages, data_at, 4, 0x0050cb80) == [], rows_of_class(guest, pages, data_at, 4, 0x0050cb80)
    assert rows_holding(guest, pages, data_at, 4, 0x0050cb80) == [(1, live)]
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
    # The gate is answered by the chain's own names, so a slot read costs no array walk at all.
    assert chain_names(pages, nm, super_chain(pages, at(0x5300))) == ["SGPlayerController", "StargatePlayer"]
    assert chain_names(pages, nm, super_chain(pages, at(0x5100))) == ["StargatePlayer"]
    assert pages.u32(row + 0x1004) == live and pages.u32(row + 0x100c) == 0
    live_verdict, live_text = describe(pages, nm, live, [])
    assert live_verdict == "live" and "`GameHud`" in live_text, live_text
    # A class pointer is not a name index: the class's own `Name` is what the report prints (`sink13` printed `?`
    # for a `GameHud` object it had just named, and for the row's own class).
    assert class_name(pages, nm, live) == "GameHud"
    assert class_name(pages, nm, row) == "StargatePlayer"
    assert class_name(pages, nm, at(0x6200)) is None            # not an object at all
    # The report's `Outer` column is resolved the same way, from the outer object's own `Name`. Read as an index
    # the pointer asks the name table for entry 0x6300 and gets nothing, which is what `sink15` printed.
    assert object_name(pages, nm, pages.u32(row + OUTER_OFF)) == "M00"
    assert nm[pages.u32(row + OUTER_OFF)] is None
    assert object_name(pages, nm, 0) is None and object_name(pages, nm, at(0x6200)) is None
    assert "class `GameHud`" in live_text, live_text
    default_verdict, _ = describe(pages, nm, default, [default])
    assert default_verdict == "default", default_verdict
    assert describe(pages, nm, 0, [])[0] == "null"
    assert describe(pages, nm, base - 4, [])[0] == "unreadable"
    # A read that would never finish names itself instead of waiting: the deadline, then the probe that says the
    # guest is gone. Both print their message and exit 4 -- the code `drive.py` reads as a named stop, not a crash.
    import io
    def stopped(pages, guest, data=0x4000, count=6):
        saved = sys.stdout
        sys.stdout = io.StringIO()
        try:
            walk(guest, pages, at(data), count, CLASS_OFF, at(0x5100))
        except SystemExit as exc:
            text = sys.stdout.getvalue()
            assert exc.code == 4, exc.code
            return text
        finally:
            sys.stdout = saved
        raise AssertionError("the walk finished; it should have stopped")

    overdue = FakeGuest(bytes(image))
    assert "deadline" in stopped(Pages(overdue, deadline=1e-9), overdue)
    # **A hole costs 4 KiB, not the whole page.** The image ends 0x8500 bytes into the page at `base`, so the last
    # 4 KiB sub-page is not there: an object in the mapped part still reads and is still found, and the address in
    # the hole answers None -- against the old cache, the one failed 64 KiB request made the whole page absent.
    holed = bytearray(base + 0x8500)
    struct.pack_into("<III", holed, at(0x3000), at(0x4000), 2, 2)
    struct.pack_into("<II", holed, at(0x4000), at(0x4100), at(0x8100))
    for obj in (at(0x4100), at(0x8100)):
        struct.pack_into("<I", holed, obj + CLASS_OFF, at(0x5100))
    hole_guest = FakeGuest(bytes(holed))
    hole_pages = Pages(hole_guest)
    assert rows_of_class(hole_guest, hole_pages, at(0x4000), 2, at(0x5100)) == [(0, at(0x4100))]
    assert hole_pages.u32(at(0x4100) + CLASS_OFF) == at(0x5100)
    assert hole_pages.u32(at(0x8400)) is None                  # inside the sub-page that is not mapped
    assert hole_pages.split == 2, hole_pages.split             # the two chunks past the end of the image
    assert hole_pages.lost == 1, hole_pages.lost               # one page with a hole, not one dead page
    assert hole_pages.requests == 12, hole_pages.requests      # 4 chunks + 8 sub-pages: no reconnect churn
    assert hole_pages.unanswered == 10, hole_pages.unanswered  # 2 chunks + the 8 sub-pages past the image
    # The hole is a verdict about memory; a probe that fails on the same page is a guest that has gone away.
    gone = FakeGuest(bytes(holed))
    probe_pages = Pages(gone, probe=0x7FFF0000, deadline=0)
    assert "stopped answering" in stopped(probe_pages, gone, 0x4000, 2)
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
    print("ok   sink-slots: the walk finds the row by class (newest first, `--tail` bounded), `--class-from` "
          "reads the class off a named object, the slot gate is the chain's own names, the marks separate live "
          "from class default, a page with a hole still reads the objects around it, and %d names is below the "
          "%d level floor" % (MENU_NAMES, LEVEL_FLOOR))

    return 0


if __name__ == "__main__":
    sys.exit(main())
