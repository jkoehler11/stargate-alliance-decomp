#!/usr/bin/env python3
"""Pair every class table in a running guest with the class name its own objects carry (U8).

The class table a class installs is named in `inventory/xbe_class_table_names.csv` from a **registration
literal**: the constructor pushes a string and the store that follows it installs a table. That reading has a
known failure mode -- the literal window is 96 bytes wide, so a site can pick up a *neighbour's* push -- and one
row of the artifact contradicts the subclass tree because of it (`WaterTexture`; `docs/NATIVE_CLASS_TREE.md`).
The standing answer to "which side is wrong" was one object read, and this is it.

The read needs no layout beyond the two fields `names.py` already measured: an object's `+0x00` is its class's
table, `+0x24` is its `UClass`, and the class object's `+0x20` is its `FName`. So a walk over `GObjObjects`
pairs **table -> class name from the build itself** -- no literal, no window, no tree. Every class has a default
object in the array, so every table that is in use gets at least one row; a table whose objects disagree names a
class hierarchy the static name disagrees with, which is exactly the WaterTexture question.

`--csv` writes `table_va,class,objects`, sorted by table, for the committed artifact. Class names are engine
identifiers (this project already publishes them); object addresses and counts are measurements; nothing here
writes guest data.

Usage, against a guest booted with `-gdb tcp::PORT` (`tools/xemu/drive.py --gdb=PORT --linger=SECS`):

    python3 tools/xemu/class-tables-live.py --port 12381 --xbe $SG1_DUMP/default.xbe --csv $WORK/ct.csv
    python3 tools/xemu/class-tables-live.py --port 12381 --xbe ... --table 0x004cfba0 --table 0x004cfe68
    python3 tools/xemu/class-tables-live.py --selftest        # no boot

**Read in virtual mode** (no `phys_mode`): the array addresses come from the image's `.data`, which is not a
physical address. Page reads are retried, because this stub fails roughly every other one.

Measured (2026-11-10, title screen): the name array is `0x006be0d8`, the object array `0x006c21b0` -- both
`names.py`'s own measured defaults, so `--find` is not needed and a wrong address is a loud error rather than an
empty walk.
"""
import argparse
import importlib.util
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
OBJECTS_PY = os.path.join(HERE, "objects.py")
NAMES_PY = os.path.join(HERE, "names.py")

GNAMES = 0x006be0d8          # measured `GNames` TArray (docs/XEMU.md)
OBJ_ARRAY = 0x006c21b0       # measured `UObject::GObjObjects` (docs/XEMU.md)
SPAN = 0x10000               # one stub read per 64 KiB page of object headers


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


objects = load("xemu_objects", OBJECTS_PY)
names_mod = load("xemu_names", NAMES_PY)
Guest = objects.Guest
plausible_object = names_mod.plausible_object


def read_array(guest, addr, what, minimum):
    """(data, num) for a `TArray` header at `addr`, or SystemExit naming what did not read."""
    data, num, cap = struct.unpack("<III", guest.read(addr, 12))
    ok = plausible_object(data) and minimum <= num <= (1 << 20) and num <= cap
    print("%s %#010x: data=%#010x num=%d max=%d%s"
          % (what, addr, data, num, cap, "" if ok else "  <-- does not read as an array"))
    if not ok:
        raise SystemExit("the %s does not read at %#x: nothing to measure" % (what, addr))
    return data, num


def walk(guest, nm, odata, onum, span=SPAN, log=print):
    """Rows of (table_va, class_name, objects) for every object in the array.

    One page read per 64 KiB page holding an object; the class name of each distinct `UClass` costs one dword
    read (its `Name` index) plus the page-cached `FNameEntry`, and there are a few hundred of those.
    """
    ptrs = [p for p in struct.unpack("<%dI" % onum, guest.read(odata, onum * 4)) if plausible_object(p)]
    pages = {}
    for p in ptrs:
        pages.setdefault(p & ~(span - 1), []).append(p)
    log("walking %d of %d object(s) over %d page(s)" % (len(ptrs), onum, len(pages)))

    pairs = {}                    # table -> {class object address: objects}
    missing = 0
    for page in sorted(pages):
        buf = None
        for _ in range(3):        # a failed page is the stub's ordinary answer, not a hole
            try:
                buf = guest.read(page, span)
                break
            except IOError:
                continue
        if buf is None or len(buf) < span:
            missing += len(pages[page])
            continue
        for p in pages[page]:
            off = p - page
            if off < 0 or off + names_mod.CLASS_OFF + 4 > len(buf):
                missing += 1      # a header that runs off the page: one read, and the next run retries it
                continue
            table = struct.unpack_from("<I", buf, off)[0]
            klass = struct.unpack_from("<I", buf, off + names_mod.CLASS_OFF)[0]
            if table < 0x1000 or not plausible_object(klass):
                missing += 1
                continue
            inner = pairs.setdefault(table, {})
            inner[klass] = inner.get(klass, 0) + 1
    log("read %d object header(s), %d unreadable" % (len(ptrs) - missing, missing))

    labels = {}

    def class_name(klass):
        if klass in labels:
            return labels[klass]
        text = None
        for _ in range(4):
            try:
                idx = guest.u32(klass + names_mod.NAME_OFF)
            except IOError:
                continue
            text = nm[idx]
            if text:
                break
        labels[klass] = text
        return text

    rows = []
    for table, counts in pairs.items():
        for klass, count in counts.items():
            rows.append((table, class_name(klass) or ("<unreadable %#x>" % klass), count))
    rows.sort()
    return rows, len(ptrs), missing


def summarise(rows, log=print):
    tables = sorted(set(r[0] for r in rows))
    split = [t for t in tables if len(set(r[1] for r in rows if r[0] == t)) > 1]
    log("%d distinct class table(s) over %d row(s); %d table(s) carry more than one class name"
        % (len(tables), len(rows), len(split)))
    return tables, split


def cmd_rows(rows, want, log=print):
    """Print the rows for a set of tables, with the class names and the object count."""
    for table in want:
        got = [r for r in rows if r[0] == table]
        if not got:
            log("  table %#010x: no object in the array carries it" % table)
            continue
        for _, label, count in got:
            log("  table %#010x: %-40s %6d object(s)" % (table, label, count))


def write_csv(rows, path):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("table_va,class,objects\n")
        for table, label, count in rows:
            fh.write("%#010x,%s,%d\n" % (table, label, count))
    print("wrote %d row(s) to %s" % (len(rows), path))


class FakeGuest(object):
    """A flat zero-filled address space with poke/read: `--selftest` only."""

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


def selftest():
    """A synthetic world: two tables, one of them shared by two classes; and an object at a page edge."""
    page = 0x01000000
    g = FakeGuest()
    names = {1: "Alpha", 2: "Beta", 3: "Gamma"}
    objs = {
        page + 0x100: (0x004cfba0, 0x02000000),      # table A, class Alpha
        page + 0x200: (0x004cfba0, 0x02000000),      # table A, class Alpha again
        page + 0x300: (0x004cfba0, 0x02000040),      # table A, class Beta -- a contradicting row
        page + 0x400: (0x004cfe68, 0x02000080),      # table B, class Gamma
        page + SPAN - 0x2c: (0x004cfe68, 0x02000080),  # a header whose last field is the page's last dword
    }
    for addr, (table, klass) in objs.items():
        g.poke(addr, struct.pack("<I", table) + b"\x00" * (names_mod.CLASS_OFF - 4)
               + struct.pack("<I", klass) + b"\x00" * 8)
    for klass, idx in ((0x02000000, 1), (0x02000040, 2), (0x02000080, 3)):
        g.poke(klass + names_mod.NAME_OFF, struct.pack("<I", idx))
    # the name array: data, num, max, then one pointer per index to a page of `FNameEntry` records
    name_page = 0x03000000
    ptrs = [0] * 8
    for idx, text in names.items():
        entry_va = name_page + 0x100 * idx
        ptrs[idx] = entry_va
        g.poke(entry_va, struct.pack("<III", idx, 0, 0) + text.encode("utf-16-le") + b"\x00\x00")
    g.poke(name_page, b"".join(struct.pack("<I", p) for p in ptrs))
    ptr_list = page + 0x8000
    g.poke(ptr_list, b"".join(struct.pack("<I", a) for a in objs))
    nm = names_mod.Names(g, name_page, 8)
    rows, walked, missing = walk(g, nm, ptr_list, len(objs), log=lambda *a: None)
    got = {(table, label): count for table, label, count in rows}
    want = {(0x004cfba0, "Alpha"): 2, (0x004cfba0, "Beta"): 1, (0x004cfe68, "Gamma"): 2}
    if got != want or walked != 5 or missing != 0:
        print("selftest FAILED: walked=%r missing=%r rows=%r" % (walked, missing, sorted(got.items())))
        return 1
    print("selftest ok: %d object(s), %d row(s), one table carrying two class names" % (walked, len(rows)))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=None, help="xemu's GDB stub port")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--xbe", default=None, help="default.xbe of the dump (for the section table)")
    ap.add_argument("--names", type=lambda v: int(v, 0), default=GNAMES, help="GNames TArray (measured)")
    ap.add_argument("--objects", type=lambda v: int(v, 0), default=OBJ_ARRAY, help="UObject::GObjObjects")
    ap.add_argument("--table", type=lambda v: int(v, 0), action="append", default=None,
                    help="print only these tables (repeatable)")
    ap.add_argument("--csv", default=None, metavar="PATH", help="write table_va,class,objects here")
    ap.add_argument("--selftest", action="store_true", help="check the walk with a fake guest, no boot")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.port is None or not args.xbe:
        ap.error("--port and --xbe are required unless --selftest is given")

    sections = objects.image_sections(args.xbe)
    print("image sections: " + ", ".join("%s %#x+%#x" % row for row in sections))
    guest = Guest(args.host, args.port)
    try:
        gdata, gnum = read_array(guest, args.names, "name array", 128)
        odata, onum = read_array(guest, args.objects, "object array", 512)
        nm = names_mod.Names(guest, gdata, gnum)
        rows, walked, missing = walk(guest, nm, odata, onum)
        tables, split = summarise(rows)
        print("%d name-array page read(s), %d guest read(s)" % (nm.page_reads, guest.reads))
        if args.table:
            print("\nrequested table(s):")
            cmd_rows(rows, args.table)
        if split:
            print("\ntables carrying more than one class name (a table inherited by a class whose own "
                  "constructor installs nothing):")
            for table in split:
                print("  %#010x" % table)
                cmd_rows(rows, [table])
        if args.csv:
            write_csv(rows, args.csv)
        return 0
    finally:
        guest.close()


if __name__ == "__main__":
    sys.exit(main())
