#!/usr/bin/env python3
"""Read a `.lad` body as frames of fixed 32-byte records, and measure the frame header (docs/UNKNOWNS.md U4).

`tools/lipsinc-index.py` established the container: a header, a bone-name table, a 32-byte bind pose per bone and then
an animation payload that is 96-99% of every file. It left the payload's shape open, and its own census showed why:
the block a record carries recurs at gaps of 32, 37, 64, 128 and 192, so the stream is not one uniform array.

This tool walks the body and reads the gap. A record is 32 bytes whose first word is the constant `2`. Between record
runs the stream carries a five-byte **frame header** -- the same `u32 2` followed by a `u8` **equal to the channel
count** -- and a frame holds exactly `channel_count` records. That is the whole of `37 = 32 + 5`: a five-byte frame
header, not a longer record. The walk is a measurement, not a stride assumption: it consumes only what it can
justify, and reports the bytes it could not consume as `coverage_bytes` rather than rounding them away.

    tools/lad-frames.py FILE.lad [...]                          # one line per file
    tools/lad-frames.py --dir "$SG1_DUMP/.../LIPSincData" -o inventory
    tools/lad-frames.py --check                                 # dump-free, validates the committed CSV

It measures; it does not interpret. What the seven floats in a record mean is still U4.
"""
import argparse
import csv
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV = "lipsinc_frames.csv"
COLUMNS = ["path", "bytes", "channels", "lead_byte", "frames", "records", "records_min", "records_max",
           "short_frames", "empty_frames", "record_bytes", "frame_header_bytes",
           "weight_minus_one", "middle_triples", "coverage_bytes", "trailer_names"]
RECORD = 32
HEADER = 5


def read_name(d, off):
    """A length byte (counting the NUL) then the string -- the same codec as the name table."""
    n = d[off]
    if n == 0 or n > 0x40:
        raise ValueError("length %d at %#x is not a name length" % (n, off))
    raw = d[off + 1:off + 1 + n]
    if not raw.endswith(b"\0"):
        raise ValueError("name at %#x is not NUL-terminated" % off)
    text = raw[:-1].decode("latin1")
    if not all(32 <= ord(c) < 127 for c in text):
        raise ValueError("non-printable name at %#x" % off)
    return text, off + 1 + n


def trailer_start(d, table_end):
    """Offset of the three-name trailer, walked backwards; the same probe as lipsinc-index.py."""
    probe, names = len(d), []
    while len(names) < 3:
        for start in range(max(table_end, probe - 0x40), probe):
            try:
                text, end = read_name(d, start)
            except (ValueError, IndexError):
                continue
            if end == probe:
                names.insert(0, text)
                probe = start
                break
        else:
            raise ValueError("no three-name trailer")
    return probe, names


def walk(d, start, end, channels):
    """Consume the body from `start` to `end` as frame headers plus records.

    A frame is `u32 2, u8 channel_count` followed by up to `channel_count` 32-byte records whose own first word is 2.
    Nothing is consumed that the stream does not justify: the loop stops where the pattern stops and the caller
    reports the remainder.
    """
    i = start
    lead = None
    if i < end and struct.unpack_from("<I", d, i)[0] != 2:
        lead, i = d[i], i + 1
    frames = []
    while i + HEADER <= end and struct.unpack_from("<I", d, i)[0] == 2:
        count = d[i + 4]
        i += HEADER
        records = 0
        while records < count and i + RECORD <= end and struct.unpack_from("<I", d, i)[0] == 2:
            records += 1
            i += RECORD
        frames.append((count, records))
        if records < count:
            break
    return lead, frames, i


def measure(path):
    d = open(path, "rb").read()
    a, b = struct.unpack_from("<II", d, 0)
    channels, off = d[8], 9
    bones = []
    for _ in range(channels):
        name, off = read_name(d, off)
        bones.append(name)
    end, names = trailer_start(d, off)
    lead, frames, stop = walk(d, off, end, channels)
    floats = []
    i = off + (1 if lead is not None else 0)
    for count, records in frames:
        i += HEADER
        for _ in range(records):
            floats.append(struct.unpack_from("<7f", d, i + 4))
            i += RECORD
    weights = sum(1 for v in floats if abs(v[6] + 1.0) < 1e-6)
    triples = len(set(struct.pack("<3f", v[3], v[4], v[5]) for v in floats))
    return {
        "path": path,
        "bytes": len(d),
        "channels": channels,
        "lead_byte": ("" if lead is None else "0x%02x" % lead),
        "frames": len(frames),
        "records": sum(r for _, r in frames),
        "records_min": (min((r for _, r in frames), default="")),
        "records_max": (max((r for _, r in frames), default="")),
        "short_frames": sum(1 for c, r in frames if r != c),
        "empty_frames": sum(1 for c, r in frames if r == 0),
        "record_bytes": RECORD,
        "frame_header_bytes": HEADER,
        "weight_minus_one": weights,
        "middle_triples": triples,
        "coverage_bytes": end - stop,
        "trailer_names": ";".join(names),
        "_frames": frames,
        "_header_u32": "%d,%d" % (a, b),
    }


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def synthetic():
    """A body the walker must read exactly: a lead byte, two frames, then a three-name trailer.

    The trailer's byte count is returned with it so the caller can say where the walk has to stop: the last frame is
    short, so the walk ends there and the trailer is what is left.
    """
    out = bytearray(b"\x14")
    for count, records in ((3, 3), (3, 2)):
        out += struct.pack("<I", 2) + bytes([count])
        for k in range(records):
            out += struct.pack("<I", 2) + struct.pack("<7f", k, k, k, 0, 0, 0, -1.0)
    trailer = 0
    for name in ("BN-Head", "undefined", "undefined"):
        out += bytes([len(name) + 1]) + name.encode() + b"\0"
        trailer += len(name) + 2
    return bytes(out), trailer


def check():
    """Dump-free: the committed CSV has to describe a stream that closes, frame by frame."""
    path = os.path.join(ROOT, "inventory", CSV)
    rows = read_csv(path)
    if not rows:
        print("FAIL lipsinc-frames: %s is empty" % path)
        return 1
    bad = []
    for r in rows:
        frames, records = int(r["frames"]), int(r["records"])
        ch = int(r["channels"])
        lo, hi = int(r["records_min"]), int(r["records_max"])
        if frames < 1 or records < 1:
            bad.append("%s: no frames" % r["path"])
        if int(r["record_bytes"]) != RECORD or int(r["frame_header_bytes"]) != HEADER:
            bad.append("%s: sizes drifted" % r["path"])
        coverage = int(r["coverage_bytes"])
        if coverage < 0 or coverage >= int(r["bytes"]):
            bad.append("%s: %d body bytes unread is not a residue" % (r["path"], coverage))
        if hi > ch:
            bad.append("%s: a frame holds %d records for %d channels" % (r["path"], hi, ch))
        if int(r["empty_frames"]):
            bad.append("%s: %s empty frames" % (r["path"], r["empty_frames"]))
        if not (lo <= records / frames <= hi):
            bad.append("%s: %d records over %d frames is outside [%d,%d]" % (r["path"], records, frames, lo, hi))
    synth, trailer = synthetic()
    lead, frames, stop = walk(synth, 0, len(synth), 3)
    if lead != 0x14 or frames != [(3, 3), (3, 2)] or stop != len(synth) - trailer:
        bad.append("selftest: synthetic body read as lead=%r frames=%r stop=%d" % (lead, frames, stop))
    # and it has to stop where the pattern stops: one byte flipped into the frame header ends the walk
    broken = synth[:1] + b"\x07" + synth[2:]
    lead2, frames2, stop2 = walk(broken, 0, len(broken), 3)
    if frames2 or stop2 != 1:
        bad.append("selftest: a broken header still walked (frames=%r)" % (frames2,))
    if bad:
        for b in bad:
            print("FAIL lipsinc-frames: " + b)
        return 1
    print("ok   lipsinc-frames: %d .lad bodies, %d frames, %d records of %d bytes under a %d-byte header"
          % (len(rows), sum(int(r["frames"]) for r in rows), sum(int(r["records"]) for r in rows), RECORD, HEADER))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dir", help="directory of .lad files (walked recursively)")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true", help="validate the committed CSV without the dump")
    args = ap.parse_args()
    if args.check:
        return check()
    files = list(args.files)
    if args.dir:
        for base, _, names in os.walk(args.dir):
            files += [os.path.join(base, n) for n in names if n.lower().endswith(".lad")]
    if not files:
        ap.error("no .lad files given (pass paths or --dir)")
    rows = [measure(f) for f in sorted(files)]
    if args.dir:
        root = os.path.abspath(args.dir)
        for r in rows:
            r["path"] = os.path.relpath(os.path.abspath(r["path"]), root)
    if args.json:
        print(json.dumps([{k: v for k, v in r.items() if not k.startswith("_")} for r in rows], indent=2))
        return 0
    os.makedirs(args.out, exist_ok=True)
    dest = os.path.join(args.out, CSV)
    with open(dest, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in COLUMNS})
    print("wrote %s (%d files)" % (dest, len(rows)))
    for r in rows:
        print("  %-52s ch %2d lead %-4s frames %4d records %6d per-frame %d..%d short %d -1.0f %d triples %d"
              % (os.path.basename(r["path"]), r["channels"], r["lead_byte"] or "-", r["frames"], r["records"],
                 r["records_min"], r["records_max"], r["short_frames"], r["weight_minus_one"], r["middle_triples"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
