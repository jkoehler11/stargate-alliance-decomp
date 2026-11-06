#!/usr/bin/env python3
"""Find and walk `UObject::GObjObjects` in a running guest: the object-level assertion channel (PLAN 7.3, U15).

A pixel assertion says a frame changed; the console keeps no output to read (`docs/UNKNOWNS.md` U15); and the
`Console` instance this project wanted to inspect had to be found without an address, because nothing in the
tree had located the engine's object array. This tool locates it and walks it, which turns "which objects is
the game holding right now" into a read: the `Console`, the `PlayerController`, the loaded `Level`, and any
actor class a regression test cares about.

How the array is found, and why this shape:

* UE2's `GObjObjects` is a `TArray<UObject*>` -- three dwords, `Data`, `Num`, `Max` -- living in `.data`.
  `.data` is mostly *uninitialised* in the image (`0x00226bd4` bytes of virtual size against `0x00034b9c` of
  file bytes), so the array reads as zeros in `default.xbe` and only exists in the guest. The image cannot
  name this address; the guest can.
* The scan therefore reads `.data` out of the guest and scores every 4-byte-aligned triple by shape: a `Num` in
  the thousands, a `Max` at or above it, and a `Data` pointer that is aligned and inside guest RAM. The shape
  alone is a guess, so a candidate is only reported as **validated** when the objects it points at carry
  vtables from the image's class-table set (`inventory/xbe_class_tables.csv`) -- that is the check that makes
  it evidence rather than a coincidence.
* Walking is one bulk read of the span the object pointers occupy (they are packed into the object heap) and a
  lookup of each object's first dword; a per-object read would be one RSP round trip each, which for 100k
  objects is minutes of latency for bytes that are contiguous on the guest's side anyway.

**Read in virtual mode.** The stub's physical mode (`Qqemu.PhyMemMode:1`) is the wrong channel for an address
taken from the XBE or from `.data`: use `interrupt()` without `phys_mode()`, as here. Every read is retried on
a fresh connection -- roughly every other read raises `odd hex length from stub`, and a probe that treats that
as data records zeros and concludes the guest is unmapped (`references/xemu-boot.md`).

Usage, against a guest booted with `-gdb tcp::PORT` (`drive.py --gdb=PORT --linger=SECS` keeps one up):

    # find the array, and say which candidates validate
    python3 tools/xemu/objects.py --port 12345 --xbe $SG1_DUMP/default.xbe --find

    # what the array holds, as a class histogram, and one class's instances
    python3 tools/xemu/objects.py --port 12345 --walk 0x5f1234 --top 25
    python3 tools/xemu/objects.py --port 12345 --walk 0x5f1234 --class Console --strings

    # the text an object *points at*: UE2 keeps a `string` as (Data, Count), so `--strings` cannot see it
    python3 tools/xemu/objects.py --port 12345 --walk 0x6c21b0 --class Console --fstrings --object-size 0x800

    # what an object *points at*: every field that is itself an object address, named by its target's class
    python3 tools/xemu/objects.py --port 12345 --walk 0x6c21b0 --class PlayerController --refs --object-size 0x800

    # one object by address, seconds not minutes (an address --refs printed), and what it points at
    python3 tools/xemu/objects.py --port 12345 --object 0x42a1b40 --object-size 0x800 --refs --fstrings

    # ...and one step further: the FString pairs inside the `Console` that pointer reaches (`TypedStr`,
    # `History[16]`), which is the difference between "the key was never delivered" and "the command ran"
    python3 tools/xemu/objects.py --port 12345 --walk 0x6c21b0 --class PlayerController --refs \
        --follow Console --object-size 0x800

`--class` answers "is there an object whose class is X", which is the wrong question for an engine that keeps
one pointer per player to an interface object: a `Console` that exists only as `Player.Console` is still a
`Console` instance, so the filter does find it -- unless the instance's vtable is not the table the class name was
resolved from, which is what `--refs` is for. It prints the object's own fields that point at another walked
object, so `Console` shows up as a field of the `Player`/`PlayerController` rather than as a class-table hit.

Guest data stays out of the repository: addresses, counts and class names are measurements, the strings an
object holds are game state. `--strings`/`--fstrings` print them to the terminal and never write a file.
"""
import argparse
import importlib.util
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
MEM = os.path.join(HERE, "mem.py")
XBE_PY = os.path.join(REPO, "tools", "xbe.py")
CLASS_TABLES = os.path.join(REPO, "inventory", "xbe_class_tables.csv")
CLASS_NAMES = os.path.join(REPO, "inventory", "xbe_class_table_names.csv")

RAM_LO = 0x00010000          # the image's load address; nothing meaningful below it
RAM_HI = 0x08000000          # 128 MiB guest: a pointer above this is not a guest object
ARRAY_MIN = 512              # fewer objects than this is not the engine's object array
ARRAY_MAX = 1 << 20


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mem = load("xemu_mem", MEM)


class Guest:
    """A retrying virtual-memory reader: one RSP round trip per chunk, one fresh connection per bad read."""

    def __init__(self, host, port, retries=4):
        self.host, self.port, self.retries = host, port, retries
        self.g = self._connect()
        self.reads = 0

    def _connect(self):
        g = mem.Gdb(self.host, self.port)
        g.interrupt()
        return g

    def read(self, addr, size, chunk=0x4000):
        for attempt in range(self.retries):
            try:
                out = self.g.read(addr, size, chunk=chunk)
                self.reads += 1
                return out
            except (IOError, OSError):
                self.g.close()
                time.sleep(0.2 * (attempt + 1))
                self.g = self._connect()
        raise IOError("read %#x+%#x failed after %d attempts" % (addr, size, self.retries))

    def u32(self, addr):
        return struct.unpack("<I", self.read(addr, 4))[0]

    def close(self):
        self.g.close()


def image_sections(xbe_path):
    """[(name, va, vsize)] from the XBE's own section table, through the repo's reader -- no hard-coded ranges."""
    xbe = load("xbe_reader", XBE_PY)
    img = xbe.Xbe(xbe_path)
    return [(s.name, s.va, s.vsize) for s in img.sections]


def section(sections, name):
    for got, va, vsize in sections:
        if got == name:
            return va, vsize
    raise SystemExit("no section named %s in the image" % name)


def plausible_pointer(value):
    return RAM_LO <= value < RAM_HI and value % 4 == 0


def array_candidates(blob, base):
    """[(addr, data, num, max)] for every 4-byte-aligned TArray-shaped triple in the blob."""
    out = []
    for off in range(0, len(blob) - 12, 4):
        data, num, cap = struct.unpack_from("<III", blob, off)
        if not (ARRAY_MIN <= num <= ARRAY_MAX):
            continue
        if not (num <= cap <= num + (1 << 18)):
            continue
        if not plausible_pointer(data):
            continue
        out.append((base + off, data, num, cap))
    return out


def class_names():
    """({table_va: 'Package.Class'}, {table_va: slots}) from the two committed artifacts."""
    names, sizes = {}, {}
    if os.path.exists(CLASS_NAMES):
        with open(CLASS_NAMES, "r", encoding="utf-8") as fh:
            head = fh.readline().rstrip("\n").split(",")
            col = {name: i for i, name in enumerate(head)}
            for line in fh:
                row = line.rstrip("\n").split(",")
                if len(row) <= col["class"] or not row[col["table_va"]]:
                    continue
                names[int(row[col["table_va"]], 16)] = row[col["class"]]
    if os.path.exists(CLASS_TABLES):
        with open(CLASS_TABLES, "r", encoding="utf-8") as fh:
            head = fh.readline().rstrip("\n").split(",")
            col = {name: i for i, name in enumerate(head)}
            for line in fh:
                row = line.rstrip("\n").split(",")
                if len(row) <= col["slots"] or not row[col["table_va"]]:
                    continue
                try:
                    sizes[int(row[col["table_va"]], 16)] = int(row[col["slots"]])
                except ValueError:
                    pass
    return names, sizes


def validate(guest, data, num, vtables, sample=64):
    """(ok, note) for a candidate: do the objects it points at carry class-table vtables?"""
    take = min(num, sample)
    try:
        blob = guest.read(data, take * 4)
    except IOError as exc:
        return False, "pointer array unreadable (%s)" % exc
    ptrs = [p for p in struct.unpack("<%dI" % take, blob) if p]
    if len(ptrs) < take // 2:
        return False, "only %d of %d slots populated" % (len(ptrs), take)
    seen = 0
    for ptr in ptrs:
        if not plausible_pointer(ptr):
            continue
        try:
            vt = guest.u32(ptr)
        except IOError:
            continue
        if vt in vtables:
            seen += 1
    ok = seen >= max(8, len(ptrs) // 2)
    return ok, "%d of %d sampled objects carry a class-table vtable" % (seen, len(ptrs))


def fstring_scan(guest, raw, limit=24, budget=96):
    """[(offset, count, text)] for FString-shaped `(Data, Count)` pairs inside a blob.

    A UE2 `string` is a pointer to a NUL-terminated TCHAR buffer plus a count that includes the terminator,
    so the characters are *not* in the object: `--strings` reads the object's own bytes and cannot see them,
    which is why a `Console`'s `History[16]` reads as sixteen integers. This resolves each plausible pair and
    keeps only the ones whose target really is text, which makes a delivered console command visible.

    Both widths are tried: this build's `TCHAR` is UTF-16, but a count of bytes plus a NUL is also what a
    narrower build writes, and the check is on content either way. At most `budget` reads are spent, because
    every one is an RSP round trip and a wrong pair fails slowly.
    """
    out, reads = [], 0
    for off in range(0, len(raw) - 8, 4):
        ptr, count = struct.unpack_from("<II", raw, off)
        if not plausible_pointer(ptr) or not (2 <= count <= 256):
            continue
        if reads >= budget:
            break
        reads += 1
        try:
            blob = guest.read(ptr, count * 2)
        except IOError:
            continue
        text = None
        if len(blob) >= count and blob[count - 1] == 0 and \
                all(0x20 <= b < 0x7F for b in blob[:count - 1]):
            text = blob[:count - 1].decode("ascii")
        if text is None and len(blob) >= count * 2:
            wide = blob[:count * 2]
            body = wide[:count * 2 - 2]
            if wide[count * 2 - 2:] == b"\x00\x00" and body[1::2] == b"\x00" * (count - 1) and \
                    all(0x20 <= b < 0x7F for b in body[0::2]):
                text = body.decode("utf-16-le")
        if text:
            out.append((off, count, text))
            if len(out) >= limit:
                break
    print("  %d FString-shaped pair(s) read, %d carried text" % (reads, len(out)))
    return out


def read_object(guest, addr, size, want_strings, want_fstrings, label="", names=None, want_refs=False):
    """What one object holds, given only its address -- no array walk, so this is seconds instead of minutes."""
    try:
        raw = guest.read(addr, size)
    except IOError as exc:
        print("object %#x: unreadable (%s)" % (addr, exc))
        return False
    print("\nobject %#x%s, %#x bytes:" % (addr, " (" + label + ")" if label else "", size))
    if want_refs:
        # Naming a target needs only its *own* first dword, so this surveys one object in seconds where the
        # array walk that would name it from the pointer array takes minutes.
        print("  fields that are guest pointers, named by the target's own vtable:")
        found = 0
        for off in range(0, len(raw) - 4, 4):
            target = struct.unpack_from("<I", raw, off)[0]
            if not plausible_pointer(target):
                continue
            try:
                vblob = guest.read(target, 4)
            except IOError:
                continue
            if len(vblob) < 4:            # a truncated read is not a class: skip rather than unpack short
                continue
            vt = struct.unpack("<I", vblob)[0]
            label_of = (names or {}).get(vt)
            if label_of is None:
                label_of = ("class table %08x" % vt) if vt in (names or {}) else "not a known class table (%08x)" % vt
            found += 1
            print("    +%#05x -> %#010x  %s" % (off, target, label_of))
        if not found:
            print("    none: no field in the first %#x bytes holds a guest pointer" % size)
    if want_strings:
        for off, kind, text in mem.strings_in(raw)[:40]:
            print("  +%#05x %-5s %r" % (off, kind, text))
    if want_fstrings or not want_strings:
        print("  FString-shaped (Data, Count) pairs inside it -- a UE2 `string` is a pointer plus a count, so")
        print("  `--strings` cannot see a `TypedStr` or a `History` entry:")
        pairs = fstring_scan(guest, raw)
        for off, count, text in pairs:
            print("    +%#05x count=%-4d %r" % (off, count, text))
        if not pairs:
            print("    none carried text (every slot empty: no command held and none delivered)")
    return True


def walk(guest, addr, vtables, names, sizes, top, want_class, want_strings, object_size, want_fstrings=False,
         want_refs=False, follow=""):
    data, num, cap = struct.unpack("<III", guest.read(addr, 12))
    if not plausible_pointer(data) or not (ARRAY_MIN <= num <= ARRAY_MAX) or cap < num:
        raise SystemExit("%#x does not hold a plausible object array (data=%#x num=%d max=%d)"
                         % (addr, data, num, cap))
    print("array %#x: data=%#x num=%d max=%d" % (addr, data, num, cap))
    blob = guest.read(data, num * 4)
    ptrs = [p for p in struct.unpack("<%dI" % num, blob) if plausible_pointer(p)]
    print("%d object pointers, %d null or implausible" % (len(ptrs), num - len(ptrs)))
    if not ptrs:
        return 0
    # The objects sit in the heap, so read the 64 KiB pages they occupy -- not the span from the lowest to the
    # highest, which can include tens of megabytes of unrelated allocation. One page is one RSP packet at the
    # stub's own PacketSize=0x20020: measured, a 16 KiB chunking of a 25 MB span takes long enough to look hung.
    pages = sorted({p & ~0xFFFF for p in ptrs})
    print("%d objects over %d 64 KiB pages (%#x..%#x)" % (len(ptrs), len(pages), pages[0], pages[-1]))
    heap = {}
    gap = 0
    for page in pages:
        try:
            heap[page] = guest.read(page, 0x10000, chunk=0x10000)
        except IOError:
            gap += 0x10000
    counts, instances, unknown, classof = {}, {}, {}, {}
    for ptr in ptrs:
        base = ptr & ~0xFFFF
        chunk = heap.get(base)
        if chunk is None:
            continue
        vt = struct.unpack_from("<I", chunk, ptr - base)[0]
        label = names.get(vt) or ("table_%08x" % vt if vt in vtables else "not a class table: %08x" % vt)
        counts[label] = counts.get(label, 0) + 1
        instances.setdefault(label, []).append(ptr)
        classof[ptr] = label
        if vt not in names:
            unknown[vt] = unknown.get(vt, 0) + 1
    total = sum(counts.values())
    print("%d of %d objects resolved; %d unreadable bytes in their pages" % (total, len(ptrs), gap))
    print("")
    print("| objects | class |")
    print("|---:|---|")
    for label, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]:
        print("| %d | %s |" % (count, label))
    if len(counts) > top:
        print("| ... | %d more classes |" % (len(counts) - top))
    if unknown:
        print("\n%d distinct unnamed vtables; the largest:" % len(unknown))
        for vt, count in sorted(unknown.items(), key=lambda kv: -kv[1])[:10]:
            print("  %#010x  %d objects%s" % (vt, count,
                                              "" if vt in vtables else "  (not a class table either)"))
    if want_class:
        hits = []
        for label, addrs in instances.items():
            if want_class.lower() in label.lower():
                hits.extend((label, a) for a in addrs)
        print("\n%d instance(s) matching %r:" % (len(hits), want_class))
        for label, a in hits[:40]:
            print("  %#010x  %s" % (a, label))
        if want_strings or want_fstrings:
            for label, a in hits[:4]:
                size = object_size or 0x1000
                try:
                    raw = guest.read(a, size)
                except IOError as exc:
                    print("  %#x: unreadable (%s)" % (a, exc))
                    continue
                print("\n  strings inside %s at %#x (guest state, not written):" % (label, a))
                for off, kind, text in mem.strings_in(raw)[:40]:
                    print("    +%#05x %-5s %r" % (off, kind, text))
                if want_fstrings:
                    print("  FStrings those bytes point at (%#x bytes read):" % size)
                    for off, count, text in fstring_scan(guest, raw):
                        print("    +%#05x count=%-4d %r" % (off, count, text))
        if want_refs:
            # The class filter matches an instance's *own* vtable against a table named for the class, so an
            # object the engine creates through a subclass or a table that never got a name is invisible to it.
            # Its owner still holds a pointer, so read the owner's fields instead of the class list.
            size = object_size or 0x800
            for label, a in hits[:8]:
                try:
                    raw = guest.read(a, size)
                except IOError as exc:
                    print("  %#x: unreadable (%s)" % (a, exc))
                    continue
                print("\n  fields of %s at %#x that are object addresses (%#x bytes):" % (label, a, size))
                fields = []
                for off in range(0, len(raw) - 4, 4):
                    target = struct.unpack_from("<I", raw, off)[0]
                    if target in classof:
                        fields.append((off, target))
                        print("    +%#05x -> %#010x  %s" % (off, target, classof[target]))
                if not fields:
                    print("    none: no field in the first %#x bytes points at a walked object" % size)
                    continue
                if not follow:
                    continue
                # Reading the *owner's* field answers "where is the instance"; only the instance's own bytes
                # answer "what does it hold". A `Console` is created from its class default at viewport
                # creation and keeps `TypedStr`/`History` inline, so following the pointer one step is the
                # difference between an address and a delivered command.
                seen = set()
                for off, target in fields:
                    if target in seen or follow.lower() not in classof[target].lower():
                        continue
                    seen.add(target)
                    print("\n  following +%#05x:" % off)
                    read_object(guest, target, size, False, True, classof[target])
    return total


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=12345, help="xemu's -gdb tcp::PORT")
    ap.add_argument("--xbe", help="default.xbe, for --find's .data range (or pass --data)")
    ap.add_argument("--data", metavar="ADDR:SIZE", help="range to scan for the array (default: the image's .data)")
    ap.add_argument("--find", action="store_true", help="scan for TArray-shaped triples and validate them")
    ap.add_argument("--walk", metavar="ADDR", help="walk the object array at ADDR (hex)")
    ap.add_argument("--class", dest="want_class", help="with --walk: list instances whose class name matches")
    ap.add_argument("--strings", action="store_true", help="with --class: print strings inside those objects")
    ap.add_argument("--fstrings", action="store_true",
                    help="with --class: also resolve UE2 (Data, Count) FString pairs and print their text")
    ap.add_argument("--refs", action="store_true",
                    help="with --class: print the instances' own fields that point at a walked object, named by class")
    ap.add_argument("--object", metavar="ADDR",
                    help="read one object at ADDR (hex) and print what it holds; no array walk, so it takes "
                         "seconds -- use it on an address --refs printed, even after that run's stub is gone; "
                         "add --refs to name the guest pointers it holds, or --fstrings for its TypedStr-shaped "
                         "pairs")
    ap.add_argument("--follow", metavar="CLASS",
                    help="with --class and --refs: also read the bytes of each ref target whose class matches "
                         "CLASS and print the UE2 FString pairs they hold (a Console's TypedStr/History)")
    ap.add_argument("--object-size", type=lambda x: int(x, 0), default=0, help="bytes to read per object for --strings")
    ap.add_argument("--top", type=int, default=25, help="rows in the class histogram")
    ap.add_argument("--all", action="store_true", help="with --find: validate every candidate, not just the first 8")
    args = ap.parse_args()

    names, sizes = class_names()
    vtables = set(sizes)
    if not vtables:
        raise SystemExit("no class tables: %s is missing or empty" % CLASS_TABLES)
    print("%d class tables loaded (%d named)" % (len(vtables), len(names)))

    base = size = 0
    if args.find:
        if args.data:
            base, size = (int(x, 0) for x in args.data.split(":"))
        elif args.xbe:
            base, size = section(image_sections(args.xbe), ".data")
        else:
            raise SystemExit("--find needs --xbe or --data")

    guest = Guest(args.host, args.port)
    try:
        if args.find:
            print("reading .data %#x..%#x (%d KiB) out of the guest" % (base, base + size, size // 1024))
            blob = guest.read(base, size)
            cands = array_candidates(blob, base)
            print("%d TArray-shaped candidate(s)" % len(cands))
            good = 0
            for addr, data, num, cap in (cands if args.all else cands[:8]):
                ok, note = validate(guest, data, num, vtables)
                good += 1 if ok else 0
                print("  %s %#010x  data=%#010x num=%d max=%d  %s"
                      % ("VALID  " if ok else "rejected", addr, data, num, cap, note))
            if good:
                print("\n%d candidate(s) validated; walk one with --walk <addr>" % good)
            else:
                print("\nno candidate validated -- the array is not a 12-byte TArray in .data, and the next "
                      "split is the block form (UE1 kept pointer blocks, and this fork may)")
        if args.object:
            read_object(guest, int(args.object, 0), args.object_size or 0x800, args.strings, args.fstrings,
                        "", names, args.refs)
        if args.walk:
            walk(guest, int(args.walk, 0), vtables, names, sizes, args.top,
                 args.want_class, args.strings, args.object_size, args.fstrings, args.refs, args.follow or "")
    finally:
        guest.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
