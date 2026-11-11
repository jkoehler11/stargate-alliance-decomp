#!/usr/bin/env python3
"""Name a class table from the constructor the live `UClass` object carries (U8's last literal-free route).

`inventory/xbe_class_table_names.csv` names 466 of the 492 class tables from a **registration literal**.
`tools/class-table-parents.py` added a third edge -- the classes that call a table's installing constructor --
and reproduced all 466, named five more and left **seventeen constrained and two with no named caller**
(`docs/UNKNOWNS.md` U8). Those seventeen cannot be read from a literal: their installers' callers are native
engine classes with no row in `inventory/classes.csv`, so there is no super to read.

This reads the other edge. Every registered class has one `UClass` object in the object array even when nothing
instantiates it, and that object's constructor field points at the C++ body that installed the table. So:

* the object array is walked once; an object whose `+0x00` is the `Class` table (`0x004d4870`) **is** a class
  object (1,004 of them at the title, `inventory/live_class_tables.csv`);
* its `+0x20` is its own `FName` -- the class name, the same field `names.py --verify` measured;
* its body is scanned for a dword equal to a constructor in `inventory/class_constructors.csv`, and the offset
  it lands on is the field's own offset, measured rather than assumed.

Scored against the 466 names the literal already has, this is a check with a known answer: an instrument that
cannot reproduce them cannot be trusted to name the other nineteen. The committed CSV is
`class,object_va,installer_va,field_offset,form,table_va,committed,verdict` for every class object that
carries a known constructor -- class names, no guest data.

Measured (2026-11-10, a booted M00 with 48,646 objects): **1,059** objects carry the `Class` table and
**not one** of them carries any of the 1,011 constructors/destructors `class_constructors.csv` holds, so this
read cannot even reproduce the 466 names it already has. The field route is closed for this build -- the
constructor lives in the registration code, not in the `UClass` instance (`docs/UNKNOWNS.md` U8) -- and the
tool is kept as the instrument that measured it.

Read in virtual mode, one 64 KiB page read per page (the stub fails roughly every other page read, so a page
is retried three times and a miss is counted, not fatal). Object windows come from the gap to the next object
in the array, so a scan cannot walk into its neighbour.

    python3 tools/xemu/class-ctor-field.py --port 12391 --xbe $SG1_DUMP/default.xbe \
        --csv $WORK/ctor-field.csv
    python3 tools/xemu/class-ctor-field.py --check        # synthetic, no boot, writes nothing
"""
import argparse
import csv
import importlib.util
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

GNAMES = 0x006be0d8          # measured `GNames` TArray (docs/XEMU.md)
OBJ_ARRAY = 0x006c21b0       # measured `UObject::GObjObjects` (docs/XEMU.md)
CLASS_TABLE = 0x004d4870     # measured: the table 1,004 objects of class `Class` carry at the title
SPAN = 0x10000               # one stub read per 64 KiB page
MAX_OBJECT = 0x800           # a class object is 0x520 bytes (`xbe_class_table_names.csv`); clamp the window
MIN_OBJECT = 0x100


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


objects = load("xemu_objects", os.path.join(HERE, "objects.py"))
names_mod = load("xemu_names", os.path.join(HERE, "names.py"))
Guest = objects.Guest
plausible_object = names_mod.plausible_object


def addr(text):
    """An address cell from a committed CSV, as an int (`0x004c78c0`, `0x00017050`, or plain decimal)."""
    if isinstance(text, int):
        return text
    text = text.strip()
    if not text:
        return None
    return int(text, 16) if text.lower().startswith("0x") else int(text)


def read_installers(path):
    """constructor VA -> (table_va, form) from `inventory/class_constructors.csv`."""
    out = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("form") not in ("ctor", "dtor"):
                continue
            va, table = addr(row["function_va"]), addr(row["table_va"])
            if va is None or table is None:
                continue
            out[va] = (table, row["form"])
    return out


def read_committed(path):
    """table_va -> class name from `inventory/xbe_class_table_names.csv` (empty string when unnamed)."""
    out = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            out[addr(row["table_va"])] = (row.get("class") or "").strip()
    return out


def read_focus(path):
    """table_va -> verdict, for the rows the caller-edge could not name (`constrained`, `no-caller`)."""
    out = {}
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            verdict = (row.get("verdict") or "").strip()
            if verdict in ("constrained", "no-caller"):
                out[addr(row["table_va"])] = verdict
    return out


def read_array(guest, at, what, minimum):
    """(data, num) for a `TArray` header at `at`, or SystemExit naming what did not read."""
    data, num, cap = struct.unpack("<III", guest.read(at, 12))
    ok = plausible_object(data) and minimum <= num <= (1 << 20) and num <= cap
    print("%s %#010x: data=%#010x num=%d max=%d%s"
          % (what, at, data, num, cap, "" if ok else "  <-- does not read as an array"))
    if not ok:
        raise SystemExit("the %s does not read at %#x: nothing to measure" % (what, at))
    return data, num


class Pages(object):
    """A 64 KiB page cache over the stub: every class body and header comes out of one read per page."""

    def __init__(self, guest, span=SPAN):
        self.guest, self.span, self.buf, self.reads, self.misses = guest, span, {}, 0, 0

    def page(self, base):
        if base in self.buf:
            return self.buf[base]
        blob = None
        for _ in range(3):                     # a failed page is the stub's ordinary answer, not a hole
            try:
                blob = self.guest.read(base, self.span)
                break
            except (IOError, OSError):
                continue
        self.reads += 1
        if blob is None or len(blob) < self.span:
            self.misses += 1
            blob = None
        self.buf[base] = blob
        return blob

    def window(self, at, size):
        """`size` bytes at `at` from the page cache, or None when one of its pages does not read."""
        out = []
        pos, end = at, at + size
        while pos < end:
            base = pos & ~(self.span - 1)
            blob = self.page(base)
            if blob is None:
                return None
            take = min(end, base + self.span) - pos
            out.append(blob[pos - base:pos - base + take])
            pos += take
        return b"".join(out)


def class_objects(ptrs, pages, table=CLASS_TABLE, log=print):
    """The `UClass` objects of an array walk: (object_va, name_index) for every object carrying `table`."""
    found, missing = [], 0
    for at in ptrs:
        head = pages.window(at, names_mod.CLASS_OFF + 4)
        if head is None:
            missing += 1
            continue
        carried = struct.unpack_from("<I", head, 0)[0]
        if carried != table:
            continue
        found.append((at, struct.unpack_from("<I", head, names_mod.NAME_OFF)[0]))
    log("%d object(s) carry the `Class` table %#x; %d header(s) unreadable"
        % (len(found), CLASS_TABLE, missing))
    return found


def scan(body, installers):
    """Every (installer_va, offset) in a class object's body, aligned and then unaligned as a fallback."""
    hits = []
    for off in range(0, len(body) - 3, 4):
        va = struct.unpack_from("<I", body, off)[0]
        if va in installers:
            hits.append((va, off))
    return hits


def field_windows(ptrs):
    """object_va -> window size, from the gap to the next object in the array (clamped to the class size)."""
    ordered = sorted(set(ptrs))
    out = {}
    for i, at in enumerate(ordered):
        gap = ordered[i + 1] - at if i + 1 < len(ordered) else MAX_OBJECT
        out[at] = max(MIN_OBJECT, min(MAX_OBJECT, gap))
    return out


def read(ptr_list, nm, pages, installers, committed, table=CLASS_TABLE, log=print):
    """Rows of (class, object_va, installer_va, offset, form, table_va, committed, verdict)."""
    slots = class_objects(ptr_list, pages, table, log=log)
    windows = field_windows(ptr_list)
    rows, unreadable, silent = [], 0, []
    labels = {}
    for at, idx in slots:
        if idx not in labels:
            text = nm[idx]
            labels[idx] = text
        name = labels[idx]
        body = pages.window(at, windows[at])
        if body is None:
            unreadable += 1
            continue
        hits = scan(body, installers)
        if not hits:
            silent.append((at, name))
            continue
        for va, off in hits:
            table, form = installers[va]
            known = committed.get(table, "")
            if not known:
                verdict = "unnamed"
            elif known == name:
                verdict = "agrees"
            else:
                verdict = "differs"
            rows.append((name or ("<unreadable %#x>" % idx), at, va, off, form, table, known, verdict))
    rows.sort(key=lambda r: (r[5], r[0]))
    log("%d hit(s) over %d class object(s); %d body unreadable; %d carries no known constructor"
        % (len(rows), len(slots), unreadable, len(silent)))
    return rows, silent


def summarise(rows, focus, log=print):
    """Score the read against the names it already has, then report the rows it can name for the first time."""
    offsets = {}
    for row in rows:
        offsets[row[3]] = offsets.get(row[3], 0) + 1
    if offsets:
        log("field offset(s): " + ", ".join("%#x x%d" % (o, n)
                                            for o, n in sorted(offsets.items(), key=lambda kv: -kv[1])))
    for verdict in ("agrees", "differs", "unnamed"):
        got = [r for r in rows if r[7] == verdict]
        log("%-8s %d row(s)" % (verdict, len(got)))
        if verdict in ("differs", "unnamed"):
            for r in got:
                log("  table %#010x: field says %-28s committed %s"
                    % (r[5], r[0], r[6] or "(none)"))
    if focus:
        log("the %d table(s) the caller edge could not name:" % len(focus))
        for table in sorted(focus):
            got = [r for r in rows if r[5] == table]
            if got:
                for r in got:
                    log("  %#010x (%s): %-28s via %#x at +%#x"
                        % (table, focus[table], r[0], r[2], r[3]))
            else:
                log("  %#010x (%s): no class object carries its constructor" % (table, focus[table]))


def write_csv(rows, path):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("class,object_va,installer_va,field_offset,form,table_va,committed,verdict\n")
        for name, at, va, off, form, table, known, verdict in rows:
            fh.write("%s,%#010x,%#08x,%#04x,%s,%#010x,%s,%s\n"
                     % (name, at, va, off, form, table, known, verdict))
    print("wrote %d row(s) to %s" % (len(rows), path))


def text_pointers(body, lo, hi):
    """Every 4-aligned dword in a body that lands in the image's code range: (offset, va)."""
    out = []
    for off in range(0, len(body) - 3, 4):
        va = struct.unpack_from("<I", body, off)[0]
        if lo <= va < hi:
            out.append((off, va))
    return out


def probe(ptr_list, nm, pages, lo, hi, installers, committed, want=None, log=print):
    """What a `UClass` object carries instead: every code pointer, by offset, over the whole array."""
    slots = class_objects(ptr_list, pages, log=log)
    windows = field_windows(ptr_list)
    sizes = sorted(set(windows[at] for at, _ in slots))
    log("window size(s) over the class objects: " + ", ".join("%#x" % s for s in sizes[:8])
        + (" ..." if len(sizes) > 8 else ""))
    offsets, values, rows = {}, {}, []
    silent = 0
    for at, idx in slots:
        name = nm[idx]
        body = pages.window(at, windows[at])
        if body is None:
            silent += 1
            continue
        hits = text_pointers(body, lo, hi)
        if not hits:
            silent += 1
            continue
        for off, va in hits:
            offsets[off] = offsets.get(off, 0) + 1
            values.setdefault(off, {}).setdefault(va, 0)
            values[off][va] += 1
            rows.append((name or "", at, off, va))
        if want and name == want:
            log("`%s` at %#x: %d code pointer(s)" % (name, at, len(hits)))
            for off, va in hits:
                got = installers.get(va)
                log("  +%#05x %#010x %s" % (off, va, ("installs table %#010x (%s)" % got) if got else ""))
    log("%d class object(s) carry no code pointer, %d do; %d distinct offset(s)"
        % (silent, len(slots) - silent, len(offsets)))
    for off, count in sorted(offsets.items(), key=lambda kv: -kv[1])[:12]:
        distinct = values[off]
        named = [va for va in distinct if va in installers]
        log("  +%#05x: %4d class object(s), %3d distinct pointer(s), %d of them a known constructor"
            % (off, count, len(distinct), len(named)))
        for va, n in sorted(distinct.items(), key=lambda kv: -kv[1])[:4]:
            got = installers.get(va)
            log("      %#010x x%-4d %s" % (va, n, ("installs %#010x" % got[0]) if got else ""))
    return rows


def write_probe_csv(rows, path):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("class,object_va,field_offset,pointer_va\n")
        for name, at, off, va in rows:
            fh.write("%s,%#010x,%#04x,%#010x\n" % (name, at, off, va))
    print("wrote %d row(s) to %s" % (len(rows), path))


class FakeGuest(object):
    """A flat zero-filled address space with poke/read: `--check` only."""

    def __init__(self):
        self.mem = {}
        self.reads = 0

    def poke(self, va, blob):
        for i, byte in enumerate(blob):
            self.mem[va + i] = byte

    def read(self, addr, size, chunk=0x4000):
        self.reads += 1
        return bytes(self.mem.get(addr + i, 0) for i in range(size))

    def u32(self, addr):
        return struct.unpack("<I", self.read(addr, 4))[0]


def check():
    """A synthetic world: two class objects, one carrying its constructor at +0x2c, one without."""
    page = 0x01000000
    g = FakeGuest()
    names = {1: "Alpha", 2: "Class"}
    ctor = 0x00011000
    installers = {ctor: (0x004c0000, "ctor")}
    committed = {0x004c0000: "Alpha"}
    objects_at = [page + 0x100, page + 0x700, page + 0xd00]
    for at, idx, extra in ((objects_at[0], 1, ctor), (objects_at[1], 2, 0), (objects_at[2], 1, 0)):
        body = struct.pack("<I", CLASS_TABLE) + b"\x00" * (names_mod.NAME_OFF - 4)
        body += struct.pack("<I", idx) + b"\x00" * (0x2c - names_mod.NAME_OFF - 4)
        body += struct.pack("<I", extra) + b"\x00" * 0x100
        g.poke(at, body)
    name_page = 0x03000000
    ptrs = [0] * 8
    for idx, text in names.items():
        entry_va = name_page + 0x100 * idx
        ptrs[idx] = entry_va
        g.poke(entry_va, struct.pack("<III", idx, 0, 0) + text.encode("utf-16-le") + b"\x00\x00")
    g.poke(name_page, b"".join(struct.pack("<I", p) for p in ptrs))
    nm = names_mod.Names(g, name_page, 8)
    pages = Pages(g)
    rows, silent = read(objects_at, nm, pages, installers, committed, log=lambda *a: None)
    want = [("Alpha", objects_at[0], ctor, 0x2c, "ctor", 0x004c0000, "Alpha", "agrees")]
    if rows != want or len(silent) != 2:
        print("check FAILED: rows=%r silent=%d" % (rows, len(silent)))
        return 1
    print("check ok: one class object's constructor found at +0x2c, two objects carry none")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=None, help="xemu's GDB stub port")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--xbe", default=None, help="default.xbe of the dump (for the section table)")
    ap.add_argument("--names", type=lambda v: int(v, 0), default=GNAMES, help="GNames TArray (measured)")
    ap.add_argument("--objects", type=lambda v: int(v, 0), default=OBJ_ARRAY, help="UObject::GObjObjects")
    ap.add_argument("--class-table", type=lambda v: int(v, 0), default=CLASS_TABLE,
                    help="the table a `UClass` object carries (measured)")
    ap.add_argument("--constructors", default=os.path.join(REPO, "inventory", "class_constructors.csv"),
                    help="constructor VA -> table, from the static read")
    ap.add_argument("--committed", default=os.path.join(REPO, "inventory", "xbe_class_table_names.csv"),
                    help="the names this read is scored against")
    ap.add_argument("--parents", default=os.path.join(REPO, "inventory", "class_table_parents.csv"),
                    help="the caller-edge rows, for the report of the tables it could not name")
    ap.add_argument("--csv", default=None, metavar="PATH", help="write the row CSV here")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no boot, writes nothing")
    args = ap.parse_args()
    if args.check:
        return check()
    if args.port is None or not args.xbe:
        ap.error("--port and --xbe are required unless --check is given")

    installers = read_installers(args.constructors)
    committed = read_committed(args.committed)
    focus = read_focus(args.parents)
    print("%d constructor(s) to match, %d committed name(s), %d table(s) to settle"
          % (len(installers), len(committed), len(focus)))

    sections = objects.image_sections(args.xbe)
    print("image sections: " + ", ".join("%s %#x+%#x" % row for row in sections))
    guest = Guest(args.host, args.port)
    try:
        gdata, gnum = read_array(guest, args.names, "name array", 128)
        odata, onum = read_array(guest, args.objects, "object array", 512)
        nm = names_mod.Names(guest, gdata, gnum)
        pages = Pages(guest)
        ptrs = [p for p in struct.unpack("<%dI" % onum, guest.read(odata, onum * 4)) if plausible_object(p)]
        print("%d plausibly object-pointer(s) of %d slot(s)" % (len(ptrs), onum))
        rows, silent = read(ptrs, nm, pages, installers, committed, args.class_table)
        print("%d page read(s) (%d unreadable), %d guest read(s)"
              % (pages.reads, pages.misses, guest.reads))
        summarise(rows, focus)
        if args.csv:
            write_csv(rows, args.csv)
        return 0
    finally:
        guest.close()


if __name__ == "__main__":
    sys.exit(main())
