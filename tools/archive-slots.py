#!/usr/bin/env python3
"""The cooked package's saving archive: its vtable, and what each slot the record writers use writes.

`FUN_0003d160` and `FUN_0003d900` do not encode their own elements -- they hand each one to a slot of the archive
object they are passed (`+0x18` for an object reference, `+0x1c` for an `FName`) -- so the *file* width of an
element is the width of whatever those slots write. `docs/NATIVE_VM.md` has read both arrays as two compact indices
per element since the writers were first read; this tool measures the slots instead of assuming them:

* **the archive is found by its version pins, not by a name.** The constructor stores the version word `130` at
  `+4` and the licensee word `4` at `+0xc` -- the pair every payload in this dump carries -- right beside the
  `MOV [obj],<vtable>` store, so a scan for `C7 40 04 82 00 00 00` finds the one constructor and the store in its
  own body names the table;
* **each slot's body is classified by what it calls.** A `ret <n>` body is the no-op `Preload` (`+0x14`); a body
  that calls the archive's own `Serialize(ptr,len)` (`+4`) writes raw bytes; a body that hands the element to the
  proxied name table (`call [eax+0x20]`) and then writes the result through `FUN_000637e0` -- the compact
  encoder -- writes **one** compact field per element. Counting those calls is the whole measurement: two would
  be an `(index, number)` pair and one is a single index;
* **the bound that follows** is what the record's gap is judged against: an array of `n` names is a compact count
  plus at most five bytes each, and no parse of a twenty-byte middle can be a two-element name array.

    python3 tools/archive-slots.py --image $SG1_DUMP/default.xbe -o inventory
    python3 tools/archive-slots.py --report
    python3 tools/archive-slots.py --check

Reads the image and writes one inventory row per slot; no boot, no Ghidra round, no decompiled output in git.
"""
import argparse
import csv
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from xbe import Xbe  # noqa: E402

ROOT = os.path.dirname(HERE)
ARTIFACT = "archive_slots.csv"
COMPACT = 0x000637e0          # FUN_000637e0: FCompactIndex, built byte by byte through the 1-byte writer
SERIALIZE = 4                 # slot +4: Serialize(void*, int), the raw-bytes primitive
PINS = [(4, 130), (0x0c, 4)]  # ArVer / ArLicensee of every payload in this dump
SLOTS = [(4, "serialize"), (0x14, "preload"), (0x18, "reference"), (0x1c, "name")]
WINDOW = 96                   # bytes of a slot body read for its calls
COLUMNS = ["vtable", "pin_store", "offset", "slot", "target", "shape", "proxy_slot", "compact_calls",
           "ret_imm", "body_bytes", "ver", "licensee"]


class Blob:
    """A little address space: VA -> bytes, for the image and for the self-test's synthetic bodies."""

    def __init__(self, segments):
        self.segments = segments              # [(va, bytes)]

    def read(self, va, n):
        for base, buf in self.segments:
            if base <= va < base + len(buf):
                return buf[va - base:va - base + n]
        return b""

    def find(self, pattern):
        out = []
        for base, buf in self.segments:
            i = buf.find(pattern)
            while i >= 0:
                out.append(base + i)
                i = buf.find(pattern, i + 1)
        return out


def image_blob(path):
    x = Xbe(path)
    segs = [(s.va, x.data[s.foff:s.foff + min(s.fsize, s.vsize)]) for s in x.sections if s.vsize]
    return Blob(segs)


def u32(buf, off):
    return struct.unpack_from("<I", buf, off)[0]


def ctor_pins(blob, va):
    """The version/licensee words a constructor body stores, read from its own `C7 40 <disp> <imm32>` stores."""
    body = blob.read(va, WINDOW)
    found = {}
    for off in range(len(body) - 7):
        if body[off] == 0xC7 and body[off + 1] == 0x40:
            found.setdefault(body[off + 2], u32(body, off + 3))
    return found


def locate(blob):
    """The archive constructor: the one body storing the version pin, and the vtable store beside it."""
    out = []
    for va in blob.find(b"\xc7\x40\x04" + struct.pack("<I", PINS[0][1])):
        body = blob.read(va, WINDOW)
        pins = ctor_pins(blob, va)
        if any(pins.get(disp) != val for disp, val in PINS):
            continue
        table = None
        for off in range(len(body) - 6):
            if body[off] == 0xC7 and body[off + 1] == 0x00:      # MOV dword ptr [EAX], <vtable>
                table = u32(body, off + 2)
                break
        if table:
            out.append((va, table))
    return out


def slot_body(blob, target):
    """The slot's first instructions up to its own terminator, capped at WINDOW bytes."""
    body = blob.read(target, WINDOW)
    end = len(body)
    for off in range(len(body) - 3):
        if body[off] == 0xC3:                                    # ret
            end = off + 1
            break
        if body[off] == 0xC2 and struct.unpack_from("<H", body, off + 1)[0] <= 0x40:
            end = off + 3                                        # ret imm16 (a byte inside an immediate is not one)
            break
    return body[:end]


def classify(blob, target):
    """What one slot writes: its ret width, the proxy slot it calls, and how many compact fields per element."""
    body = slot_body(blob, target)
    ret_imm = None
    if len(body) >= 3 and body[-3] == 0xC2:
        ret_imm = struct.unpack_from("<H", body, len(body) - 2)[0]
    elif body.endswith(b"\xc3"):
        ret_imm = 0
    compact = 0
    proxy = None
    for off in range(len(body) - 5):
        if body[off] == 0xE8 and (target + off + 5 + struct.unpack_from("<i", body, off + 1)[0]) == COMPACT:
            compact += 1
        if body[off] == 0xFF and body[off + 1] == 0x50:          # call [eax+disp8]: the proxy's own slot
            proxy = body[off + 2]
    if ret_imm is not None and ret_imm and not body[:-3].strip(b"\xcc"):
        shape = "noop-ret%d" % ret_imm
    elif compact == 1:
        shape = "one-compact-field"
    elif compact == 2:
        shape = "two-compact-fields"
    elif compact == 0 and body.endswith(b"\xc2"):
        shape = "raw-bytes"
    elif compact == 0:
        shape = "other"
    else:
        shape = "%d-compact-fields" % compact
    return {"shape": shape, "compact_calls": compact, "proxy_slot": proxy, "ret_imm": ret_imm,
            "body_bytes": len(body), "body": body.hex(" ")}


def measure(blob):
    rows = []
    for ctor, table in locate(blob):
        pins = ctor_pins(blob, ctor)
        for off, name in SLOTS:
            target = u32(blob.read(table + off, 4), 0)
            if not target:
                continue
            got = classify(blob, target)
            rows.append({"vtable": "0x%08x" % table, "pin_store": "0x%08x" % ctor, "offset": "0x%02x" % off,
                         "slot": name, "target": "0x%08x" % target, "shape": got["shape"],
                         "proxy_slot": "" if got["proxy_slot"] is None else "0x%02x" % got["proxy_slot"],
                         "compact_calls": got["compact_calls"], "ret_imm": got["ret_imm"],
                         "body_bytes": got["body_bytes"], "ver": pins.get(4, ""), "licensee": pins.get(0x0c, "")})
    # The version pin is not unique: six constructors in this image store 130/4, so the table is named by the
    # slots it carries, not by the pin alone. Report every candidate and let the shapes separate them.
    rows.sort(key=lambda r: (r["vtable"], r["offset"]))
    return rows


def write(rows, outdir):
    path = os.path.join(outdir, ARTIFACT)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in COLUMNS})
    return path


def report(rows):
    tables = sorted({r["vtable"] for r in rows})
    print("tables whose constructor stores the 130/4 pin pair beside a vtable store: %d" % len(tables))
    for t in tables:
        mine = {r["slot"]: r for r in rows if r["vtable"] == t}
        print("  %s (pin store %s, version %s, licensee %s)" % (t, mine["name"]["pin_store"],
                                                               mine["name"]["ver"], mine["name"]["licensee"]))
        for off in ("serialize", "preload", "reference", "name"):
            r = mine.get(off)
            if r is None:
                continue
            print("    %s %-9s -> %s  %-18s compact=%s proxy=%s ret=%s"
                  % (r["offset"], r["slot"], r["target"], r["shape"], r["compact_calls"],
                     r["proxy_slot"] or "-", r["ret_imm"] if r["ret_imm"] is not None else "-"))
    # The pin is not enough: several archive classes in this image carry version 130 / licensee 4. The one the
    # class-record writers are handed is the one whose two element slots are *different* functions that each
    # route the element through the proxied name table -- a shared stub cannot be both a reference and a name.
    live = []
    for t in tables:
        mine = {r["slot"]: r for r in rows if r["vtable"] == t}
        if "reference" in mine and "name" in mine:
            if (mine["reference"]["target"] != mine["name"]["target"]
                    and mine["reference"]["compact_calls"] == mine["name"]["compact_calls"] == 1):
                live.append(t)
    print("tables that serialise a reference and a name as two different one-field writers: %d (%s)"
          % (len(live), ", ".join(live) or "none"))
    for t in live:
        mine = {r["slot"]: r for r in rows if r["vtable"] == t}
        print("  %s: an object reference is one compact field (proxy %s) and an FName is one compact field "
              "(proxy %s) -- so an element is 1-5 bytes on disk and an array of n names spans at most 1 + 5*n "
              "bytes" % (t, mine["reference"]["proxy_slot"], mine["name"]["proxy_slot"]))
    return 0


def self_check():
    """Synthetic bodies: a ctor with the pins and a table store, a no-op Preload, a name and a reference slot."""
    seg = bytearray(0x400)
    base = 0x100000
    # constructor at base+0: MOV [EAX+4],130 / MOV [EAX+0xc],4 / MOV [EAX],0x100200 / RET
    body = bytearray(b"\xc7\x40\x04" + struct.pack("<I", 130) + b"\xc7\x40\x0c" + struct.pack("<I", 4))
    body += b"\xc7\x00" + struct.pack("<I", base + 0x200) + b"\xc3"
    seg[0:len(body)] = body
    # vtable at base+0x200: +4 serialize, +0x14 no-op ret 8, +0x18 reference, +0x1c name
    slots = {4: base + 0x300, 0x14: base + 0x340, 0x18: base + 0x360, 0x1c: base + 0x3a0}
    for off, va in slots.items():
        seg[0x200 + off:0x200 + off + 4] = struct.pack("<I", va)
    raw = b"\x8b\x51\x48\x03\xd0\xc2\x08\x00"                          # Serialize: raw bytes, ret 8
    seg[0x300:0x300 + len(raw)] = raw
    seg[0x340:0x343] = b"\xc2\x08\x00"                                    # Preload: a no-op ret 8
    # reference: push [eax+0x24] result through the compact encoder
    ref = b"\x8b\x4e\x4c\x8b\x01\x50\xff\x50\x24\x50\x56\xe8" + struct.pack("<i", COMPACT - (base + 0x360 + 16)) + b"\xc2\x04\x00"
    seg[0x360:0x360 + len(ref)] = ref
    # name: same shape, proxy slot +0x20 -- one compact field, and nothing else
    name = b"\x8b\x4e\x4c\x8b\x01\x52\xff\x50\x20\x50\x56\xe8" + struct.pack("<i", COMPACT - (base + 0x3a0 + 16)) + b"\xc2\x04\x00"
    seg[0x3a0:0x3a0 + len(name)] = name
    blob = Blob([(base, bytes(seg))])
    found = locate(blob)
    assert found == [(base, base + 0x200)], found
    got = {r["slot"]: r for r in measure(blob)}
    assert got["preload"]["shape"] == "noop-ret8", got["preload"]
    assert got["preload"]["ret_imm"] == 8, got["preload"]
    assert got["name"]["shape"] == "one-compact-field", got["name"]
    assert got["name"]["compact_calls"] == 1 and got["name"]["proxy_slot"] == "0x20", got["name"]
    assert got["reference"]["compact_calls"] == 1 and got["reference"]["proxy_slot"] == "0x24", got["reference"]
    print("self-check ok: the pins find the ctor, its store names the table, and the name slot writes one field")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--image", help="the dump's default.xbe")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--report", action="store_true", help="print the committed artifact")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump")
    args = ap.parse_args()
    if args.check:
        return self_check()
    path = os.path.join(args.out, ARTIFACT)
    if args.report:
        if not os.path.isfile(path):
            sys.exit("%s missing -- run with --image first" % path)
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        return report(rows)
    if not args.image:
        sys.exit("--image is required unless --report or --check is given")
    rows = measure(image_blob(args.image))
    if not rows:
        sys.exit("no constructor stores the version/licensee pair -- the locator found nothing")
    out = write(rows, args.out)
    print("wrote %s (%d rows)" % (out, len(rows)))
    report(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
