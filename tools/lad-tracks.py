#!/usr/bin/env python3
"""Walk a `.lad` body past its pose frames: the clip section and the track table (docs/UNKNOWNS.md U4).

`tools/lad-frames.py` measured the body's first part: a frame count, then that many frames of one 32-byte record per
channel -- 20 frames in all eight files. It left 96-99% of the big `Controllers/` packs unread and reported it as
`coverage_bytes`. This tool walks what follows and finds two further parts:

* a **clip section** (present in the four 19-channel packs): a count, then that many clips, each `u32 2`, a bone count,
  that many `BN-` names, a frame count with the same frame layout as the pose section, and a **clip name** --
  `carter_happy`, `tealc_yelling`, `oneill_squint`.
* a **track table** (present in seven of the eight files): a sequence of records named `<Map>.<Animation>` --
  `AlkeshBomber.ca_firing_ge_s_02` -- each with a constant header (`u32 2`, the two names, `u32 1`, `160.0`, `220.0`,
  and a key count of **26**) followed by a block of key bytes.

The clip walk is strict: every clip must parse or the section is refused. The track table is *located*, not walked --
each record's header is a ten-field signature and the bytes between one header and the next are counted as an unread
key block, with the number of `u32 2` markers inside it reported so the next reader has the shape. What the key bytes
mean is U4's remaining half; this tool measures where they start and stop.

    tools/lad-tracks.py FILE.lad [...]                          # one line per file
    tools/lad-tracks.py --dir "$SG1_DUMP/.../LIPSincData" -o inventory
    tools/lad-tracks.py --check                                 # dump-free, validates the committed CSV

It measures; it does not interpret.
"""
import argparse
import csv
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV = "lipsinc_tracks.csv"
COLUMNS = ["path", "bytes", "channels", "pose_frames", "pose_records", "clips", "clip_names", "clip_bones",
           "clip_frames", "tracks", "maps", "unique_names", "gap_bytes", "block_min", "block_max", "block_median",
           "markers_min", "markers_max", "key_count", "trailer_names"]
RECORD = 32
FRAME_HEADER = 5
TRACK_KEY_COUNT = 26
TRACK_TIMES = (160.0, 220.0)
MARKER = b"\x02\x00\x00\x00"


def read_name(d, off, limit=0x50):
    """A length byte (counting the NUL) then the string -- the same codec as the channel table."""
    if off >= len(d):
        raise ValueError("name past the end at %#x" % off)
    n = d[off]
    if n < 2 or n > limit or off + 1 + n > len(d):
        raise ValueError("length %d at %#x is not a name length" % (n, off))
    raw = d[off + 1:off + 1 + n]
    if not raw.endswith(b"\0"):
        raise ValueError("name at %#x is not NUL-terminated" % off)
    text = raw[:-1].decode("latin1")
    if not all(32 <= ord(c) < 127 for c in text):
        raise ValueError("non-printable name at %#x" % off)
    return text, off + 1 + n


def trailer_start(d, table_end):
    """Offset of the three-name trailer, walked backwards; the same probe as lad-frames.py."""
    probe, names = len(d), []
    while len(names) < 3:
        for start in range(max(table_end, probe - 0x40), probe):
            try:
                text, end = read_name(d, start, 0x40)
            except (ValueError, IndexError):
                continue
            if end == probe:
                names.insert(0, text)
                probe = start
                break
        else:
            raise ValueError("no three-name trailer")
    return probe, names


def frame_section(d, start, end, channels):
    """`u8 frames` then that many frames of `channels` 32-byte records -- strict, nothing skipped."""
    if start >= end:
        raise ValueError("no room for a frame count")
    nframes = d[start]
    i = start + 1
    frames = []
    for _ in range(nframes):
        if i + FRAME_HEADER > end or struct.unpack_from("<I", d, i)[0] != 2:
            raise ValueError("no frame header at %#x" % i)
        count = d[i + 4]
        if count != channels:
            raise ValueError("a frame holds %d records for %d channels at %#x" % (count, channels, i))
        i += FRAME_HEADER
        for _ in range(count):
            if i + RECORD > end or struct.unpack_from("<I", d, i)[0] != 2:
                raise ValueError("a record at %#x does not open with 2" % i)
            i += RECORD
        frames.append(count)
    return nframes, frames, i


def clip_section(d, start, end, channels):
    """`u8 clips` then that many clips: bones, one frame section, and the clip's name.

    Returns (clips, stop) where each clip is a (name, bones, frames) triple, or None if the bytes here are not a clip
    section. The walk is strict on purpose: a bone name that is not in the channel table, or a frame that does not hold
    exactly the clip's bone count, refuses the whole section rather than consuming a guess.
    """
    if start >= end or not 1 <= d[start] <= 32:
        return None
    count = d[start]
    p, clips = start + 1, []
    try:
        for _ in range(count):
            if struct.unpack_from("<I", d, p)[0] != 2:
                return None
            bones_n = d[p + 4]
            if not 1 <= bones_n <= channels:
                return None
            q = p + 5
            bones = []
            for _ in range(bones_n):
                name, q = read_name(d, q)
                if not name.startswith("BN-"):
                    return None
                bones.append(name)
            nframes, frames, q = frame_section(d, q, end, bones_n)
            name, q = read_name(d, q)
            clips.append((name, bones, nframes))
            p = q
    except (ValueError, IndexError, struct.error):
        return None
    return clips, p


def track_at(d, p, end):
    """A track record's header: ten fields that all eight files agree on, or None."""
    if p + 16 > end or struct.unpack_from("<I", d, p)[0] != 2:
        return None
    try:
        short, q = read_name(d, p + 4)
        full, q = read_name(d, q)
    except (ValueError, IndexError):
        return None
    if "." not in full:
        return None
    owner, name = full.split(".", 1)
    if not owner or not name or owner != owner.strip() or " " in full:
        return None
    if q + 13 > end:
        return None
    one = struct.unpack_from("<I", d, q)[0]
    t1, t2 = struct.unpack_from("<ff", d, q + 4)
    keys = d[q + 12]
    if one != 1 or abs(t1 - TRACK_TIMES[0]) > 1e-4 or abs(t2 - TRACK_TIMES[1]) > 1e-4:
        return None
    if keys != TRACK_KEY_COUNT:
        return None
    return {"short": short, "full": full, "owner": owner, "name": name, "keys_offset": q + 13}


def track_table(d, start, end):
    """Locate the track records and measure the key block between one header and the next."""
    tracks, scan = [], start
    while scan + 16 <= end:
        hit = d.find(MARKER, scan)
        if hit < 0:
            break
        rec = track_at(d, hit, end)
        if rec is None:
            scan = hit + 1
            continue
        rec["offset"] = hit
        tracks.append(rec)
        scan = hit + len(MARKER)
    for k, rec in enumerate(tracks):
        stop = tracks[k + 1]["offset"] if k + 1 < len(tracks) else end
        rec["block"] = stop - rec["keys_offset"]
        rec["markers"] = d.count(MARKER, rec["keys_offset"], stop)
    gap = (tracks[0]["offset"] - start) if tracks else (end - start)
    return tracks, gap


def measure(path):
    d = open(path, "rb").read()
    version, second = struct.unpack_from("<II", d, 0)
    channels, off = d[8], 9
    bones = []
    for _ in range(channels):
        name, off = read_name(d, off, 0x40)
        bones.append(name)
    end, names = trailer_start(d, off)
    pose_frames, frames, stop = frame_section(d, off, end, channels)
    clips = None
    if stop < end:
        got = clip_section(d, stop, end, channels)
        if got is not None:
            clips, stop = got
    tracks, gap = track_table(d, stop, end)
    blocks = [t["block"] for t in tracks]
    marks = [t["markers"] for t in tracks]
    owners = sorted(set(t["owner"] for t in tracks))
    return {
        "path": path,
        "bytes": len(d),
        "channels": channels,
        "pose_frames": pose_frames,
        "pose_records": sum(frames),
        "clips": 0 if clips is None else len(clips),
        "clip_names": "" if not clips else ";".join(c[0] for c in clips),
        "clip_bones": "" if not clips else ";".join(str(len(c[1])) for c in clips),
        "clip_frames": "" if not clips else ";".join(str(c[2]) for c in clips),
        "tracks": len(tracks),
        "maps": ";".join(owners),
        "unique_names": len(set(t["full"] for t in tracks)),
        "gap_bytes": gap,
        "block_min": min(blocks) if blocks else "",
        "block_max": max(blocks) if blocks else "",
        "block_median": sorted(blocks)[len(blocks) // 2] if blocks else "",
        "markers_min": min(marks) if marks else "",
        "markers_max": max(marks) if marks else "",
        "key_count": TRACK_KEY_COUNT if tracks else "",
        "trailer_names": ";".join(names),
        "_clips": clips,
        "_tracks": tracks,
        "_bones": bones,
        "_version": (version, second),
    }


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def synthetic():
    """A body the walk must read exactly: header, one pose frame, one clip, a track header and a key block.

    The three part boundaries are what the test is for: the pose walk must stop at the clip count, the clip walk must
    stop at the track table's first byte, and the track table must end at the trailer.
    """
    out = bytearray(struct.pack("<II", 2, 2) + bytes([2]))
    for name in ("BN-Jaw", "BN-Head"):
        out += bytes([len(name) + 1]) + name.encode() + b"\0"
    body = bytearray(b"\x01")                                     # one pose frame
    body += struct.pack("<I", 2) + bytes([2])                     # frame header: 2 records
    for _ in range(2):
        body += struct.pack("<I", 2) + struct.pack("<7f", 0, 0, 0, 0, 0, 0, -1.0)
    body += bytes([1])                                            # one clip
    body += struct.pack("<I", 2) + bytes([2])                     # its header: 2 bones
    for bone in (b"BN-Jaw", b"BN-Head"):
        body += bytes([len(bone) + 1]) + bone + b"\0"
    body += b"\x02"                                              # the clip's 2 frames
    for _ in range(2):
        body += struct.pack("<I", 2) + bytes([2])
        for _ in range(2):
            body += struct.pack("<I", 2) + struct.pack("<7f", 1, 2, 3, 0, 0, 0, -1.0)
    name = b"test_clip"
    body += bytes([len(name) + 1]) + name + b"\0"
    body += b"\x00\x01"                                           # the two bytes before the first track header
    full = b"Map.test_clip"
    body += struct.pack("<I", 2) + bytes([len(name) + 1]) + name + b"\0"
    body += bytes([len(full) + 1]) + full + b"\0"
    body += struct.pack("<I", 1) + struct.pack("<ff", 160.0, 220.0) + bytes([26])
    keys = bytearray()
    for k in range(3):                                            # a key block that is not decoded
        keys += struct.pack("<I", 2) + struct.pack("<I", k * 100) + struct.pack("<d", 0.0)
    body += keys
    trailer = 0
    for nm in ("BN-Head", "undefined", "undefined"):
        body += bytes([len(nm) + 1]) + nm.encode() + b"\0"
        trailer += len(nm) + 2
    return bytes(out) + bytes(body), trailer


def check():
    """Dump-free: the committed CSV has to describe all three parts, and a synthetic body proves the walk."""
    path = os.path.join(ROOT, "inventory", CSV)
    rows = read_csv(path)
    if not rows:
        print("FAIL lipsinc-tracks: %s is empty" % path)
        return 1
    bad = []
    for r in rows:
        ch, pf = int(r["channels"]), int(r["pose_frames"])
        if pf != 20 or int(r["pose_records"]) != pf * ch:
            bad.append("%s: %d pose frames / %d records for %d channels" % (r["path"], pf,
                                                                           int(r["pose_records"]), ch))
        if int(r["gap_bytes"]) > 8:
            bad.append("%s: %d bytes between the walk and the first track header" % (r["path"], int(r["gap_bytes"])))
        tracks = int(r["tracks"])
        if tracks:
            if int(r["key_count"]) != TRACK_KEY_COUNT:
                bad.append("%s: key count %s" % (r["path"], r["key_count"]))
            if not 0 < int(r["unique_names"]) <= tracks:
                bad.append("%s: %d tracks but %s unique names" % (r["path"], tracks, r["unique_names"]))
            if len(r["maps"].split(";")) != 1 or not r["maps"]:
                bad.append("%s: maps %r" % (r["path"], r["maps"]))
            if not 0 < int(r["block_min"]) <= int(r["block_max"]):
                bad.append("%s: key blocks %s..%s" % (r["path"], r["block_min"], r["block_max"]))
            if int(r["markers_min"]) < 1:
                bad.append("%s: a key block holds no marker" % r["path"])
        else:
            if int(r["gap_bytes"]) > 8:
                bad.append("%s: no tracks and %s bytes unread" % (r["path"], r["gap_bytes"]))
        if int(r["clips"]):
            if int(r["channels"]) != 19:
                bad.append("%s: a clip section in a %s-channel file" % (r["path"], r["channels"]))
            if len(r["clip_names"].split(";")) != int(r["clips"]):
                bad.append("%s: %s clips named %r" % (r["path"], r["clips"], r["clip_names"]))
            if set(r["clip_frames"].split(";")) != {"20"}:
                bad.append("%s: clip frames %r" % (r["path"], r["clip_frames"]))
    synth, trailer = synthetic()
    srow = measure_bytes(synth, "synthetic")
    if srow["pose_frames"] != 1 or srow["pose_records"] != 2:
        bad.append("selftest: pose walk read %r frames / %r records" % (srow["pose_frames"], srow["pose_records"]))
    if srow["clips"] != 1 or srow["clip_names"] != "test_clip" or srow["clip_bones"] != "2":
        bad.append("selftest: clip walk read %r" % (srow["clip_names"],))
    if srow["tracks"] != 1 or srow["gap_bytes"] != 2 or srow["block_min"] != 48:
        bad.append("selftest: track table read %r tracks, gap %r, block %r"
                   % (srow["tracks"], srow["gap_bytes"], srow["block_min"]))
    if srow["trailer_names"] != "BN-Head;undefined;undefined":
        bad.append("selftest: trailer %r" % (srow["trailer_names"],))
    # and a broken clip count has to refuse the section rather than consume it as tracks
    broken = bytearray(synth)
    idx = synth.index(b"test_clip") - 1
    broken[idx] = 0x09
    brow = measure_bytes(bytes(broken), "synthetic-broken")
    if brow["clips"] != 0:
        bad.append("selftest: a broken clip count still read %d clips" % brow["clips"])
    if bad:
        for b in bad:
            print("FAIL lipsinc-tracks: " + b)
        return 1
    print("ok   lipsinc-tracks: %d .lad bodies, %d clips, %d tracks, key blocks of %s..%s bytes"
          % (len(rows), sum(int(r["clips"]) for r in rows), sum(int(r["tracks"]) for r in rows),
             min(int(r["block_min"]) for r in rows if r["block_min"]),
             max(int(r["block_max"]) for r in rows if r["block_max"])))
    return 0


def measure_bytes(d, label):
    """measure() reading from memory, so the self-test does not need the dump on disk."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".lad", delete=False) as f:
        f.write(d)
        name = f.name
    try:
        return measure(name)
    finally:
        os.unlink(name)


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
        print("  %-40s ch %2d pose %2d/%4d clips %d tracks %4d blocks %5s..%-5s gap %d  %s"
              % (os.path.basename(r["path"]), r["channels"], r["pose_frames"], r["pose_records"], r["clips"],
                 r["tracks"], r["block_min"], r["block_max"], r["gap_bytes"], r["clip_names"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
