#!/usr/bin/env python3
"""Name the class that carries an object at each of the six field offsets (U8).

The 28 `.text` sites that dispatch `call [obj+0x4c]`/`[obj+0x50]` take their receiver out of a *field* of another
object (`inventory/vcall_field_owners.csv`), and the load decodes to one of six displacements -- `+0x4c` 12,
`+0x220` 8, `+0x48` 3, `+0x1c` 2, `+0x2c` 2, `+0x310` 1. Neither static route names the class of that object: a
class table holds virtuals only, no containing function and no direct caller is a slot target of any of the 492
tables, and the property stream carries names, kinds and types but no byte offsets, so it cannot turn `+0x220`
into a property either.

What a boot answers is the *layout* question, and it does not need to know which site runs: `GObjObjects` is a
live array of every `UObject`, an object's `+0x00` is its class's table and `+0x24` its `UClass`
(`tools/xemu/names.py`), and that `UClass`'s `+0x20` is its `FName` index. So for every live object that holds
*another live object* at one of the six offsets, both classes are reads -- the owner's, and the held object's.
A class that carries an object at `+0x220` is a candidate owner for the eight `[eax+0x220]` sites, and the held
class is what those sites dispatch the pair on. The offsets are a *partition* of the live heap, not a property
name: the module that reports `+0x220` says "some object of class X holds a live object here", which is the
claim U8 asks for, and it is falsifiable by a class that never appears.

Reads are page-at-a-time (`attempts=1`, the page-walk mode `objects.Guest` documents): a miss is an unmapped page,
not a dead stub, and the walk over ~100k objects is then a few dozen RSP round trips rather than one per object.

Usage, against a guest booted with `-gdb tcp::PORT` (`tools/xemu/drive.py --gdb=PORT --linger=SECS`):

    # which class holds a live object at the six sites' offsets, and what class it holds
    python3 tools/xemu/field-objects.py --port 12641 --xbe $SG1_DUMP/default.xbe \
        --csv $WORK/field-objects.csv

    # one offset, and the objects that hold it
    python3 tools/xemu/field-objects.py --port 12641 --xbe ... --offsets 0x220 --top 8
    python3 tools/xemu/field-objects.py --port 12641 --xbe ... --offsets 0x220 --holders

    # the offsets the 28 sites actually read, straight out of the committed artifact
    python3 tools/xemu/field-objects.py --port 12641 --xbe ... --from-csv inventory/vcall_field_owners.csv

    python3 tools/xemu/field-objects.py --selftest        # no boot, synthetic heap

Object addresses and counts are measurements; class names are engine identifiers this project already publishes.
Nothing here writes guest data: the artifact holds offsets, class names and counts.
"""
import argparse
import collections
import csv
import importlib.util
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
OBJECTS_PY = os.path.join(HERE, "objects.py")
NAMES_PY = os.path.join(HERE, "names.py")

OBJ_ARRAY = 0x006c21b0       # measured `UObject::GObjObjects` (docs/XEMU.md)
GNAMES = 0x006be0d8          # measured `GNames` (docs/XEMU.md)
SPAN = 0x10000               # one stub read per 64 KiB page
FIELDS = (0x4c, 0x220, 0x48, 0x1c, 0x2c, 0x310)   # inventory/vcall_field_owners.csv, 28 sites


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


objects = load("xemu_objects", OBJECTS_PY)
names_mod = load("xemu_names", NAMES_PY)
plausible_object = names_mod.plausible_object


class Heap(object):
    """The guest's object heap, read one 64 KiB block per RSP *request*, with misses recorded.

    Two measured reasons for this shape. `mem.Gdb.read` chunks at 16 KiB by default, so a 64 KiB page is four
    round trips: a first pass at that shape moved 9.3 MB in 233 s (~40 KB/s), which over a 200 MB heap is hours.
    One request per 64 KiB block is what `PacketSize=0x20020` allows. And an unmapped block answers `E` for the
    whole request, so a failed block is re-read at half the size before it is recorded as missing -- `attempts=1`
    throughout, because over an object array a gap is the stub's ordinary answer, not a dead stub.
    """

    SIZES = (0x10000, 0x8000, 0x4000, 0x2000, 0x1000)

    def __init__(self, guest):
        self.guest = guest
        self.bufs = dict((size, {}) for size in self.SIZES)
        self.requests = 0
        self.split = 0

    def _fill(self, size, base):
        if base in self.bufs[size]:
            return self.bufs[size][base]
        try:
            buf = self.guest.read(base, size, chunk=size, attempts=1)
        except (IOError, OSError):
            buf = None
        self.requests += 1
        if buf is None and size > self.SIZES[-1]:
            self.split += 1
            half = size // 2
            self._fill(half, base)
            self._fill(half, base + half)
            self.bufs[size][base] = None       # the block itself answered `E`: its halves are the record
            return None
        self.bufs[size][base] = buf
        return buf

    def prefetch(self, addrs, log=print, every=50):
        """Read every block those addresses fall in, in address order, with progress -- the walk itself is then
        memory-resident and cannot look hung."""
        blocks = sorted(set(a & ~(self.SIZES[0] - 1) for a in addrs))
        start = time.time()
        for i, base in enumerate(blocks, 1):
            self._fill(self.SIZES[0], base)
            if i % every == 0 or i == len(blocks):
                log("  read %d/%d block(s), %.1f MB requested, %.0f s elapsed"
                    % (i, len(blocks), i * self.SIZES[0] / 1e6, time.time() - start))
        return len(blocks)

    def u32(self, addr):
        for size in self.SIZES:
            base = addr & ~(size - 1)
            buf = self._fill(size, base) if base not in self.bufs[size] else self.bufs[size][base]
            if buf is None:
                continue
            off = addr - base
            if off + 4 <= len(buf):
                return struct.unpack_from("<I", buf, off)[0]
        return None

    def missing(self):
        return sum(1 for b in self.bufs[self.SIZES[0]].values() if b is None)



def read_array(guest, addr, what, minimum):
    """(data, num) for a `TArray` header at `addr`, or SystemExit naming what did not read."""
    data, num, cap = struct.unpack("<III", guest.read(addr, 12))
    ok = plausible_object(data) and minimum <= num <= (1 << 20) and num <= cap
    print("%s %#010x: data=%#010x num=%d max=%d%s"
          % (what, addr, data, num, cap, "" if ok else "  <-- does not read as an array"))
    if not ok:
        raise SystemExit("the %s does not read at %#x: nothing to measure" % (what, addr))
    return data, num


def offsets_from_csv(path):
    """The distinct `field_offset` values of `inventory/vcall_field_owners.csv`, in file order."""
    seen = []
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            value = int(row["field_offset"], 0)
            if value not in seen:
                seen.append(value)
    return tuple(seen)


def analyse(heap, ptrs, label_of, offsets, log=print):
    """Rows of (offset, owner_class, held_class, sites, owner_objects) over the live object array.

    `heap.u32(addr)` is one dword of the guest's memory (or None where the page did not read), `ptrs` the array's
    plausible object addresses and `label_of(addr)` the class name an object carries. The denominator per
    (offset, owner_class) is the number of that class's live objects whose field dword read at all, so a row with
    3 sites on 4 objects means one object of that class does not hold an object there -- not that the field is
    absent from the class.
    """
    live = set(ptrs)
    rows = collections.Counter()
    holders = collections.defaultdict(set)     # (offset, owner_class, held_class) -> object addresses
    denom = collections.Counter()              # (offset, owner_class) -> objects whose dword read
    nulls = collections.Counter()              # (offset, owner_class) -> dword was 0
    outside = collections.Counter()            # (offset, owner_class) -> dword set, not a live object
    unread = 0
    for p in ptrs:
        klass = heap.u32(p + names_mod.CLASS_OFF)
        if klass is None:
            unread += 1
            continue
        owner = label_of(klass)
        for off in offsets:
            value = heap.u32(p + off)
            if value is None:
                continue
            denom[(off, owner)] += 1
            if value == 0:
                nulls[(off, owner)] += 1
                continue
            if value not in live:
                outside[(off, owner)] += 1
                continue
            held_klass = heap.u32(value + names_mod.CLASS_OFF)
            held = label_of(held_klass) if held_klass is not None else "<unreadable %#x>" % value
            rows[(off, owner, held)] += 1
            holders[(off, owner, held)].add(p)
    log("walked %d object(s), %d with an unreadable header" % (len(ptrs), unread))
    out = []
    for (off, owner, held), sites in rows.items():
        out.append((off, owner, held, sites, denom[(off, owner)]))
    out.sort(key=lambda r: (r[0], -r[3], r[1], r[2]))
    stats = {"nulls": nulls, "outside": outside, "denom": denom, "unread": unread, "holders": holders}
    return out, stats


def summarise(rows, stats, offsets, top=8, log=print):
    """One block per offset: how many classes hold a live object, and where the dwords that do not go."""
    for off in offsets:
        block = [r for r in rows if r[0] == off]
        if not block:
            log("\n%#04x: no live object holds a live object here" % off)
            continue
        classes = sorted(set(r[1] for r in block))
        sites = sum(r[3] for r in block)
        log("\n%#04x: %d site(s) over %d owner class(es), %d held class(es)"
            % (off, sites, len(classes), len(set(r[2] for r in block))))
        for off2, owner, held, count, total in block[:top]:
            log("   %-38s holds %-34s %4d of %4d object(s)" % (owner, held, count, total))


def write_csv(rows, path):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("offset,owner_class,held_class,sites,owner_objects\n")
        for off, owner, held, sites, total in rows:
            fh.write("%#06x,%s,%s,%d,%d\n" % (off, owner, held, sites, total))
    print("wrote %d row(s) to %s" % (len(rows), path))


class FakeHeap(object):
    """A dict-backed dword space: `--selftest` only, so the analysis is exercised without a boot."""

    def __init__(self, words):
        self.words = words

    def u32(self, addr):
        return self.words.get(addr)


def selftest():
    """A synthetic heap where the answer is known: two owners, one held class, and the two non-answers."""
    ALPHA, BETA, GAMMA, DELTA = 0x02000000, 0x02000040, 0x02000080, 0x020000c0
    a1, a2, b1, b2, g1, d1 = 0x01000100, 0x01000200, 0x01000300, 0x01000400, 0x01000500, 0x01000600
    words = {}
    for obj, klass in ((a1, ALPHA), (a2, ALPHA), (b1, BETA), (b2, BETA), (g1, GAMMA), (d1, DELTA)):
        words[obj + names_mod.CLASS_OFF] = klass
    words[a1 + 0x220] = g1          # Alpha holds a Gamma
    words[a2 + 0x220] = d1          # Alpha holds a Delta
    words[b1 + 0x220] = 0           # Beta's slot is empty
    words[b2 + 0x220] = 0x00fff000  # ... and one dword that is not a live object
    words[a1 + 0x4c] = b1           # Alpha holds a Beta at the other offset
    for obj in (a2, b1, b2, g1, d1):   # a real heap maps every dword; the empty slots read as zero
        words[obj + 0x4c] = 0
    labels = {ALPHA: "Alpha", BETA: "Beta", GAMMA: "Gamma", DELTA: "Delta"}
    heap = FakeHeap(words)
    ptrs = [a1, a2, b1, b2, g1, d1]
    rows, stats = analyse(heap, ptrs, labels.get, (0x220, 0x4c), log=lambda *a: None)
    got = {(off, owner, held): (sites, total) for off, owner, held, sites, total in rows}
    want = {
        (0x220, "Alpha", "Gamma"): (1, 2),
        (0x220, "Alpha", "Delta"): (1, 2),
        (0x4c, "Alpha", "Beta"): (1, 2),
    }
    if got != want:
        print("FAIL selftest: rows\n  got  %r\n  want %r" % (sorted(got.items()), sorted(want.items())))
        return 1
    # Beta's two objects both read a dword at +0x220: one zero, one not a live object -- neither is a row.
    if stats["nulls"][(0x220, "Beta")] != 1 or stats["outside"][(0x220, "Beta")] != 1:
        print("FAIL selftest: Beta's non-answers are %r/%r, want one zero and one outside"
              % (stats["nulls"][(0x220, "Beta")], stats["outside"][(0x220, "Beta")]))
        return 1
    if stats["denom"][(0x220, "Beta")] != 2:
        print("FAIL selftest: Beta's denominator is %d, want 2" % stats["denom"][(0x220, "Beta")])
        return 1
    # An object whose class object does not read is skipped, not counted as a holder of anything.
    words2 = dict(words)
    del words2[a1 + names_mod.CLASS_OFF]
    rows2, stats2 = analyse(FakeHeap(words2), ptrs, labels.get, (0x220,), log=lambda *a: None)
    got2 = {(owner, held): (sites, total) for _, owner, held, sites, total in rows2}
    if stats2["unread"] != 1 or got2.get(("Alpha", "Delta")) != (1, 1):
        print("FAIL selftest: an unreadable class header was not skipped (%r, %r)"
              % (stats2["unread"], sorted(got2.items())))
        return 1
    print("ok selftest: 3 row(s) of 5 candidate (offset, owner, held) triples, both non-answers counted, "
          "an unreadable header skipped")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=None, help="xemu's GDB stub port")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--xbe", default=None, help="default.xbe of the dump (for the section table)")
    ap.add_argument("--names", type=lambda v: int(v, 0), default=GNAMES, help="GNames TArray (measured)")
    ap.add_argument("--array", type=lambda v: int(v, 0), default=OBJ_ARRAY, help="UObject::GObjObjects")
    ap.add_argument("--offsets", default=None,
                    help="comma list of field offsets (default: the six the 28 sites read)")
    ap.add_argument("--from-csv", default=None, metavar="PATH",
                    help="take the offsets from a vcall_field_owners.csv instead of the default six")
    ap.add_argument("--top", type=int, default=8, help="rows printed per offset (default 8)")
    ap.add_argument("--holders", action="store_true", help="print the object addresses behind each printed row")
    ap.add_argument("--csv", default=None, metavar="PATH",
                    help="write offset,owner_class,held_class,sites,owner_objects here")
    ap.add_argument("--selftest", action="store_true", help="check the analysis with a fake heap, no boot")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    try:
        sys.stdout.reconfigure(line_buffering=True)   # a background run's log is read while it works
    except AttributeError:
        pass
    if args.port is None or not args.xbe:
        ap.error("--port and --xbe are required unless --selftest is given")

    offsets = FIELDS
    if args.from_csv:
        offsets = offsets_from_csv(args.from_csv)
        print("offsets from %s: %s" % (args.from_csv, ", ".join("%#x" % o for o in offsets)))
    if args.offsets:
        offsets = tuple(int(v, 0) for v in args.offsets.split(","))
    print("field offsets: " + ", ".join("%#x" % o for o in offsets))

    objects.image_sections(args.xbe)
    guest = objects.Guest(args.host, args.port)
    try:
        gdata, gnum = read_array(guest, args.names, "name array", 128)
        odata, onum = read_array(guest, args.array, "object array", 512)
        nm = names_mod.Names(guest, gdata, gnum)
        ptrs = [p for p in struct.unpack("<%dI" % onum, guest.read(odata, onum * 4))
                if plausible_object(p)]
        print("object array holds %d of %d plausible object(s)" % (len(ptrs), onum))
        if not ptrs:
            raise SystemExit("no plausible object pointer in the array: nothing to measure")
        lo, hi = min(ptrs), max(ptrs)
        print("object heap spans %#010x..%#010x (%d block(s) of %#x)"
              % (lo, hi, (hi - lo) // SPAN + 1, SPAN))
        heap = Heap(guest)
        wanted = list(ptrs)
        for p in ptrs:
            wanted.extend(p + off for off in offsets)
        blocks = heap.prefetch(wanted)
        print("%d block(s) requested in %d RSP call(s), %d split, %d block(s) unmapped"
              % (blocks, heap.requests, heap.split, heap.missing()))
        labels = {}

        def label_of(klass):
            """The class name a `UClass` carries, cached: a few hundred of these, one name-array page each."""
            if klass in labels:
                return labels[klass]
            text = None
            for _ in range(3):
                try:
                    idx = guest.u32(klass + names_mod.NAME_OFF)
                except IOError:
                    continue
                text = nm[idx]
                if text:
                    break
            labels[klass] = text or ("<unreadable %#x>" % klass)
            return labels[klass]

        rows, stats = analyse(heap, ptrs, label_of, offsets)
        print("%d guest read(s) total" % guest.reads)
        summarise(rows, stats, offsets, args.top)
        if args.holders:
            print("\nholders (object address per printed row):")
            for key in sorted(stats["holders"], key=lambda k: (-len(stats["holders"][k]), k)):
                addrs = sorted(stats["holders"][key])
                print("  %#04x %-38s %-34s %d: %s"
                      % (key[0], key[1], key[2], len(addrs),
                         " ".join("%#x" % a for a in addrs[:6]) + (" ..." if len(addrs) > 6 else "")))
        if args.csv:
            write_csv(rows, args.csv)
        return 0
    finally:
        guest.close()


if __name__ == "__main__":
    sys.exit(main())
