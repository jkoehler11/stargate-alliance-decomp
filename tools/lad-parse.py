#!/usr/bin/env python3
"""Walk a `.lad` from byte 0 under the loader's own schema (docs/UNKNOWNS.md U4).

`tools/lad-frames.py`, `lad-tracks.py` and `lad-keys.py` each recognise a section by its shape and stop when
the shape stops holding. This tool does not recognise anything: it replays the order in which the loader
(`FUN_000bce40`, called from `LIPSincLoadDBFromDisk` through `FUN_000b9d50`) serialises its object, and the
file has to end exactly where the walk ends or the file is refused.

Every scalar here is one of the loader's own leaf serialisers, which the image spells out:

    FUN_00037110  4 raw bytes          FUN_00037040  1 raw byte
    FUN_00037080  2 raw bytes          FUN_000637e0  the 7-bit count (bit 0x40 = a second byte, << 6)
    FUN_00058850  a string: count, then count bytes (a negative count means 2-byte characters)
    FUN_00035b60  an array of strings  FUN_000bc470  an array of {u32, array of 32-byte pose records}
    FUN_000bcb60  an array of clips    FUN_000bcd30  an array of tracks

so the file is:

    lad      := u32 version, u32 version2, names, frames, clips, tracks, u32, string x3
    names    := count, count x string                       (channel table; the names are bone names)
    frames   := count, count x { u32, count, count x pose } (the pose block: one record per channel)
    clips    := count, count x { u32, bones, frames, string }
    tracks   := count, count x { u32, string, string, u32, f32, f32, u32 groups, groups x key-group, 0x00 }

The trailing u32 and three strings are the file's last 4 + 37 bytes (`2`, `BN-Head`, `undefined`, `undefined`).

    tools/lad-parse.py FILE.lad [...]                      # one line per file
    tools/lad-parse.py --walk FILE.lad                     # the section map, with offsets and counts
    tools/lad-parse.py --dir "$SG1_DUMP/.../LIPSincData" -o inventory
    tools/lad-parse.py --check                             # dump-free, validates the committed CSV

It measures the container; it does not claim what a value means.
"""
import argparse
import csv
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CSV = "lipsinc_parse.csv"
COLUMNS = ["path", "bytes", "version", "version2", "channels", "names_bytes", "frames", "frame_records",
           "clips", "clip_names", "clip_bones", "clip_frames", "tracks", "groups", "keys",
           "tail_offset", "tail_left", "trailing"]
GROUPS = 26
POSE = 32
MARKER = b"\x02\x00\x00\x00"
TERMINATOR = 0x00


class Refused(ValueError):
    """A file the loader's schema cannot read end to end."""


class Reader(object):
    def __init__(self, data, path=""):
        self.d = data
        self.p = 0
        self.path = path

    def need(self, n):
        if self.p + n > len(self.d):
            raise Refused("%s: %d bytes needed at %#x, file ends at %#x"
                          % (self.path, n, self.p, len(self.d)))

    def u32(self):
        self.need(4)
        v = struct.unpack_from("<I", self.d, self.p)[0]
        self.p += 4
        return v

    def f32(self):
        self.need(4)
        v = struct.unpack_from("<f", self.d, self.p)[0]
        self.p += 4
        return v

    def i32(self):
        self.need(4)
        v = struct.unpack_from("<i", self.d, self.p)[0]
        self.p += 4
        return v

    def f64(self):
        self.need(8)
        v = struct.unpack_from("<d", self.d, self.p)[0]
        self.p += 8
        return v

    def count(self):
        """The 7-bit count: bit 0x40 means a second byte follows, contributing << 6; bit 0x80 is a sign."""
        self.need(1)
        b = self.d[self.p]
        val = b & 0x3F
        neg = b & 0x80
        self.p += 1
        if b & 0x40:
            shift = 6
            while True:
                self.need(1)
                b = self.d[self.p]
                self.p += 1
                val |= (b & 0x7F) << shift
                shift += 7
                if not (b & 0x80):
                    break
        return -val if neg else val

    def string(self):
        n = self.count()
        wide = n < 0
        if wide:
            n = -n
        self.need(n * (2 if wide else 1))
        raw = self.d[self.p:self.p + n * (2 if wide else 1)]
        self.p += n * (2 if wide else 1)
        try:
            return raw.decode("utf-16-le" if wide else "latin-1")
        except UnicodeDecodeError:
            raise Refused("%s: string at %#x is not text" % (self.path, self.p))

    def strings(self):
        return [self.string() for _ in range(self.count())]

    def poses(self):
        """`FUN_000bc470`: {u32, array of 32-byte records} per element; every record is one channel or bone."""
        frames = self.count()
        records = 0
        per = []
        for _ in range(frames):
            self.u32()
            n = self.count()
            if n < 0:
                raise Refused("%s: negative pose count at %#x" % (self.path, self.p))
            self.need(n * POSE)
            self.p += n * POSE
            records += n
            per.append(n)
        return frames, records, per

    def key_block(self):
        groups = self.count()
        keys = 0
        for _ in range(groups):
            if self.u32() != 2:
                raise Refused("%s: no group marker at %#x" % (self.path, self.p - 4))
            if self.d[self.p] != 0:
                self.need(1)
                raise Refused("%s: group flag at %#x" % (self.path, self.p))
            self.p += 1
            n = self.count()
            keys += n
            for _ in range(n):
                if self.u32() != 2:
                    raise Refused("%s: no key marker at %#x" % (self.path, self.p - 4))
                self.i32()
                self.f64()
        self.need(1)
        if self.d[self.p] != TERMINATOR:
            raise Refused("%s: block does not end with 0x00 at %#x" % (self.path, self.p))
        self.p += 1
        return groups, keys


def walk_bytes(data, path=""):
    r = Reader(data, path)
    out = {"path": path, "bytes": len(data)}
    out["version"] = r.u32()
    out["version2"] = r.u32()
    names = r.strings()
    out["channels"] = len(names)
    out["names_bytes"] = r.p
    out["frames"], out["frame_records"], per = r.poses()
    out["_frame_per"] = sorted(set(per))
    out["_frames_end"] = r.p
    clips = []
    for _ in range(r.count()):
        clip = {"u32": r.u32(), "bones": r.strings()}
        clip["frames"], clip["records"], clip["per"] = r.poses()
        clip["name"] = r.string()
        clip["_end"] = r.p
        clips.append(clip)
    tracks = []
    for _ in range(r.count()):
        track = {"u32": r.u32(), "name": r.string(), "full": r.string(), "one": r.u32(),
                 "start": r.f32(), "end": r.f32()}
        track["groups"], track["keys"] = r.key_block()
        tracks.append(track)
    out["_clips"] = clips
    out["_tracks"] = tracks
    out["clips"] = len(clips)
    out["clip_names"] = ";".join(c["name"].rstrip("\0") for c in clips)
    out["clip_bones"] = ";".join(str(len(c["bones"])) for c in clips)
    out["clip_frames"] = ";".join(str(c["frames"]) for c in clips)
    out["_clip_records"] = sum(c["records"] for c in clips)
    out["tracks"] = len(tracks)
    out["groups"] = sum(t["groups"] for t in tracks)
    out["keys"] = sum(t["keys"] for t in tracks)
    out["tail_offset"] = r.p
    tail_u32 = r.u32()
    trailing = [r.string() for _ in range(3)]
    out["_tail_u32"] = tail_u32
    out["trailing"] = ";".join(s.rstrip("\0") for s in trailing)
    out["tail_left"] = len(data) - r.p
    out["_consumed"] = r.p
    if r.p != len(data):
        raise Refused("%s: walk ends at %#x, file is %#x (%d bytes left)"
                      % (path, r.p, len(data), len(data) - r.p))
    return out


def walk(path):
    with open(path, "rb") as f:
        return walk_bytes(f.read(), path)


def row(rec):
    return {k: rec.get(k, "") for k in COLUMNS}


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def synthetic():
    """A body the schema must read to its last byte: two channels, one clip, one track, 26 groups."""
    out = bytearray(struct.pack("<II", 2, 2))
    names = [b"BN-Jaw", b"BN-Head"]
    out += bytes([len(names)])
    for n in names:
        out += bytes([len(n) + 1]) + n + b"\0"
    out += bytes([2])                                            # two pose frames
    for _ in range(2):
        out += struct.pack("<I", 2) + bytes([len(names)])
        for _ in names:
            out += struct.pack("<I", 2) + struct.pack("<3f", 1.0, 2.0, 3.0)
            out += bytes(12) + struct.pack("<f", -1.0)
    out += bytes([1])                                            # one clip
    out += struct.pack("<I", 2) + bytes([2])
    for n in names:
        out += bytes([len(n) + 1]) + n + b"\0"
    out += bytes([20])                                           # 20 frames, one record each
    for _ in range(20):
        out += struct.pack("<I", 2) + bytes([1]) + struct.pack("<I", 2)
        out += struct.pack("<3f", 1.0, 2.0, 3.0) + bytes(12) + struct.pack("<f", -1.0)
    out += bytes([len(b"test_clip") + 1]) + b"test_clip\0"
    out += bytes([1])                                            # one track
    out += struct.pack("<I", 2)
    out += bytes([len(b"test_clip") + 1]) + b"test_clip\0"
    out += bytes([len(b"Map.test_clip") + 1]) + b"Map.test_clip\0"
    out += struct.pack("<I", 1) + struct.pack("<ff", 160.0, 220.0) + bytes([GROUPS])
    blk = bytearray()
    for g in range(GROUPS):
        blk += struct.pack("<I", 2) + bytes([0])
        if g == 0:                                               # 69 keys: a two-byte count
            blk += bytes([(69 & 0x3F) | 0x40, 69 >> 6])
            n = 69
        else:
            blk += bytes([0])
            n = 0
        for k in range(n):
            blk += struct.pack("<I", 2) + struct.pack("<i", k * 10) + struct.pack("<d", 0.85)
    blk += bytes([TERMINATOR])
    out += blk
    out += struct.pack("<I", 2)
    for s in (b"BN-Head", b"undefined", b"undefined"):
        out += bytes([len(s) + 1]) + s + b"\0"
    return bytes(out)


def check():
    """Dump-free: the committed CSV keeps the invariants, and a synthetic body proves the walk."""
    path = os.path.join(ROOT, "inventory", CSV)
    if not os.path.exists(path):
        print("FAIL lipsinc-parse: %s is missing" % path)
        return 1
    rows = read_csv(path)
    if not rows:
        print("FAIL lipsinc-parse: %s is empty" % path)
        return 1
    bad = []
    for r in rows:
        name = r["path"]
        try:
            if int(r["tail_left"]) != 0:
                bad.append("%s: %s bytes left after the walk" % (name, r["tail_left"]))
            if int(r["frame_records"]) != int(r["frames"]) * int(r["channels"]):
                bad.append("%s: %s records for %s frames of %s channels"
                           % (name, r["frame_records"], r["frames"], r["channels"]))
            if int(r["clips"]) != len([x for x in r["clip_bones"].split(";") if x]):
                bad.append("%s: %s clips but %r bone lists" % (name, r["clips"], r["clip_bones"]))
            if int(r["tracks"]) and int(r["groups"]) != int(r["tracks"]) * GROUPS:
                bad.append("%s: %s groups for %s tracks" % (name, r["groups"], r["tracks"]))
            if int(r["version"]) != 2:
                bad.append("%s: version %s" % (name, r["version"]))
        except ValueError as exc:
            bad.append("%s: unreadable row (%s)" % (name, exc))
    try:
        rec = walk_bytes(synthetic(), "synthetic")
    except Refused as exc:
        bad.append("selftest: %s" % exc)
    else:
        if (rec["channels"], rec["frames"], rec["frame_records"], rec["clips"], rec["tracks"],
                rec["groups"], rec["keys"], rec["tail_left"]) != (2, 2, 4, 1, 1, GROUPS, 69, 0):
            bad.append("selftest: read %r" % ({k: rec[k] for k in
                                               ("channels", "frames", "frame_records", "clips", "tracks",
                                                "groups", "keys", "tail_left")},))
    if bad:
        for b in bad[:20]:
            print("FAIL lipsinc-parse: " + b)
        return 1
    print("ok   lipsinc-parse: %d files, %d channels, %d pose frames, %d clips, %d tracks, %d keys, "
          "all walked to the last byte"
          % (len(rows), sum(int(r["channels"]) for r in rows), sum(int(r["frames"]) for r in rows),
             sum(int(r["clips"]) for r in rows), sum(int(r["tracks"]) for r in rows),
             sum(int(r["keys"]) for r in rows)))
    return 0


def show(path):
    rec = walk(path)
    print("%s: %d bytes" % (path, rec["bytes"]))
    print("  %#06x  u32 version %d, u32 %d" % (0, rec["version"], rec["version2"]))
    print("  %#06x  channel table: %d strings, ends %#x" % (8, rec["channels"], rec["names_bytes"]))
    print("  %#06x  pose block: %d frames x %s records of %d bytes = %d, ends %#x"
          % (rec["names_bytes"], rec["frames"], "/".join(str(x) for x in rec["_frame_per"]),
             POSE, rec["frame_records"], rec["_frames_end"]))
    for i, c in enumerate(rec["_clips"]):
        print("           clip %d %-18r u32 %d  bones %d  frames %d x %s  ends %#x"
              % (i, c["name"].rstrip("\0"), c["u32"], len(c["bones"]), c["frames"],
                 "/".join(str(x) for x in sorted(set(c["per"]))), c["_end"]))
    print("           clips %d, tracks %d, groups %d, keys %d, tail %#x"
          % (rec["clips"], rec["tracks"], rec["groups"], rec["keys"], rec["tail_offset"]))
    print("  %#06x  tail: u32 %d, strings %s" % (rec["tail_offset"], rec["_tail_u32"], rec["trailing"]))
    print("  end of file at %#x, %d bytes left" % (rec["_consumed"], rec["tail_left"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dir", help="a directory of .lad files (searched recursively)")
    ap.add_argument("-o", "--out", help="inventory directory to write %s into" % CSV)
    ap.add_argument("--walk", metavar="FILE", help="print one file's section map")
    ap.add_argument("--check", action="store_true", help="dump-free: validate the committed CSV")
    args = ap.parse_args(argv)
    if args.check:
        return check()
    if args.walk:
        return show(args.walk)
    paths = list(args.files)
    if args.dir:
        for root, _, files in os.walk(args.dir):
            paths += [os.path.join(root, f) for f in files if f.lower().endswith(".lad")]
    if not paths:
        ap.error("no .lad files given")
    rows, failed = [], 0
    for p in sorted(paths):
        try:
            rec = walk(p)
        except Refused as exc:
            print("REFUSED %s" % exc)
            failed += 1
            continue
        if args.dir:
            rec["path"] = os.path.relpath(p, args.dir)
        rows.append(row(rec))
        print("%-34s %7d bytes  ch %2d  frames %2d x %-2s  clips %d  tracks %3d  groups %4d  keys %6d"
              % (rec["path"], rec["bytes"], rec["channels"], rec["frames"],
                 "/".join(str(x) for x in rec["_frame_per"]), rec["clips"], rec["tracks"],
                 rec["groups"], rec["keys"]))
    if args.out and rows:
        dest = os.path.join(args.out, CSV)
        with open(dest, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
        print("wrote %s (%d rows)" % (dest, len(rows)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
