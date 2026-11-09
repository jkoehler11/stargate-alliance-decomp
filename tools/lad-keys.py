#!/usr/bin/env python3
"""Decode a `.lad` track's key block: 26 groups of (time, value) keys (docs/UNKNOWNS.md U4).

`tools/lad-tracks.py` located the track table and stopped at each header: the key bytes between one
`<Map>.<Animation>` record and the next were counted, not read, because 26 keys spread over 269 bytes in one
track and 19,568 in another rule out a fixed key struct. They are not 26 keys -- they are **26 groups**, each
with its own key count:

    block   := group x 26, then 0x00
    group   := u32 2, u8 0, varint count, count x key
    key     := u32 2, i32 time, f64 value

`26` is the byte the track header already carried and this document used to call a key count; it is the group
count, and it holds in all 2,231 blocks. The varint sets bit `0x40` in its first byte when a second byte
follows and contributes `<< 6`, so a group of 69 keys opens `45 01`. Times are non-decreasing inside every
group -- that is the test that says the middle field is time -- and the block's last byte is `0x00`.

    tools/lad-keys.py FILE.lad [...]                       # one line per file
    tools/lad-keys.py --dir "$SG1_DUMP/.../LIPSincData" -o inventory
    tools/lad-keys.py --walk TRACK FILE.lad                # one track's groups, with byte offsets
    tools/lad-keys.py --check                              # dump-free, validates the committed CSV

It measures counts and ranges; it does not claim what a value means.
"""
import argparse
import csv
import importlib.util
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CSV = "lipsinc_keys.csv"
COLUMNS = ["path", "track", "block_bytes", "groups", "keys", "empty_groups", "min_keys", "max_keys",
           "time_min", "time_max", "value_min", "value_max"]
MARKER = b"\x02\x00\x00\x00"
GROUPS = 26
TERMINATOR = 0x00


def _tracks_module():
    """lad-tracks.py owns the walk to the track table; import it rather than re-deriving it."""
    spec = importlib.util.spec_from_file_location("lad_tracks", os.path.join(HERE, "lad-tracks.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def varint_count(d, p, end):
    """A 7-bit count: bit 0x40 in the first byte means a second byte follows, contributing `<< 6`."""
    if p + 1 > end:
        raise ValueError("no count byte at %#x" % p)
    b = d[p]
    if b & 0x40:
        if p + 2 > end:
            raise ValueError("truncated count at %#x" % p)
        return (b & 0x3F) | (d[p + 1] << 6), 2
    return b, 1


def key_block(d, start, end):
    """Walk one track's key block strictly; every group and key must parse or the whole block is refused."""
    groups, p = [], start
    for _ in range(GROUPS):
        if p + 6 > end or d[p:p + 4] != MARKER:
            raise ValueError("no group marker at %#x" % p)
        flag = d[p + 4]
        if flag != 0:
            raise ValueError("group flag %#x at %#x" % (flag, p))
        count, n = varint_count(d, p + 5, end)
        q = p + 5 + n
        keys = []
        for _ in range(count):
            if q + 16 > end or d[q:q + 4] != MARKER:
                raise ValueError("no key marker at %#x" % q)
            keys.append((struct.unpack_from("<i", d, q + 4)[0], struct.unpack_from("<d", d, q + 8)[0]))
            q += 16
        groups.append(keys)
        p = q
    if p + 1 > end or d[p] != TERMINATOR:
        raise ValueError("block does not end with 0x00 at %#x" % p)
    return groups, p + 1


def measure_track(d, rec):
    """One track record's key block as aggregates: group and key counts, time and value ranges."""
    start, end = rec["keys_offset"], rec["keys_offset"] + rec["block"]
    groups, stop = key_block(d, start, end)
    times = [t for g in groups for t, _ in g]
    values = [v for g in groups for _, v in g]
    for g in groups:                                    # the invariant that makes the middle field time
        gt = [t for t, _ in g]
        if any(a > b for a, b in zip(gt, gt[1:])):
            raise ValueError("time runs backwards inside a group")
    sizes = [len(g) for g in groups]
    return {
        "track": rec["full"],
        "block_bytes": rec["block"],
        "groups": len(groups),
        "keys": len(times),
        "empty_groups": sizes.count(0),
        "min_keys": min(sizes),
        "max_keys": max(sizes),
        "time_min": min(times) if times else "",
        "time_max": max(times) if times else "",
        "value_min": "%.6g" % min(values) if values else "",
        "value_max": "%.6g" % max(values) if values else "",
        "left_bytes": (end - stop),
        "_groups": groups,
    }


def measure(path):
    lt = _tracks_module()
    d = open(path, "rb").read()
    rows = lt.measure(path)
    out = []
    for rec in rows["_tracks"]:
        try:
            got = measure_track(d, rec)
        except ValueError as exc:
            got = {"track": rec["full"], "block_bytes": rec["block"], "groups": 0, "keys": 0,
                   "empty_groups": "", "min_keys": "", "max_keys": "", "time_min": "", "time_max": "",
                   "value_min": "", "value_max": "", "left_bytes": "", "error": str(exc), "_groups": []}
        got["path"] = path
        out.append(got)
    return out


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def synthetic():
    """A body whose key block the walk must read exactly: two groups, one with a two-byte count."""
    out = bytearray(struct.pack("<II", 2, 2) + bytes([2]))
    for name in ("BN-Jaw", "BN-Head"):
        out += bytes([len(name) + 1]) + name.encode() + b"\0"
    body = bytearray(b"\x01")
    body += struct.pack("<I", 2) + bytes([2])
    for _ in range(2):
        body += struct.pack("<I", 2) + struct.pack("<7f", 0, 0, 0, 0, 0, 0, -1.0)
    body += b"\x01"                                              # one clip
    body += struct.pack("<I", 2) + bytes([2])
    for bone in (b"BN-Jaw", b"BN-Head"):
        body += bytes([len(bone) + 1]) + bone + b"\0"
    body += b"\x01"
    body += struct.pack("<I", 2) + bytes([2])
    for _ in range(2):
        body += struct.pack("<I", 2) + struct.pack("<7f", 1, 2, 3, 0, 0, 0, -1.0)
    name = b"test_clip"
    body += bytes([len(name) + 1]) + name + b"\0"
    body += b"\x00\x01"                                          # the two bytes before the first track header
    full = b"Map.test_clip"
    body += struct.pack("<I", 2) + bytes([len(name) + 1]) + name + b"\0"
    body += bytes([len(full) + 1]) + full + b"\0"
    body += struct.pack("<I", 1) + struct.pack("<ff", 160.0, 220.0) + bytes([GROUPS])
    keys = bytearray()
    for g in range(GROUPS):
        keys += struct.pack("<I", 2) + bytes([0])
        if g == 0:                                               # 69 keys: the two-byte count
            keys += bytes([(69 & 0x3F) | 0x40, 69 >> 6])
            count = 69
        else:
            keys += bytes([0])
            count = 0
        for k in range(count):
            keys += struct.pack("<I", 2) + struct.pack("<i", k * 10) + struct.pack("<d", 0.85)
    keys += bytes([TERMINATOR])
    body += keys
    for nm in ("BN-Head", "undefined", "undefined"):
        body += bytes([len(nm) + 1]) + nm.encode() + b"\0"
    return bytes(out) + bytes(body)


def measure_bytes(d):
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".lad", delete=False) as f:
        f.write(d)
        name = f.name
    try:
        return measure(name)
    finally:
        os.unlink(name)


def check():
    """Dump-free: the committed CSV has to keep the block's invariants, and a synthetic body proves the walk."""
    path = os.path.join(ROOT, "inventory", CSV)
    rows = read_csv(path)
    if not rows:
        print("FAIL lipsinc-keys: %s is empty" % path)
        return 1
    bad = []
    for r in rows:
        if int(r["groups"]) != GROUPS:
            bad.append("%s %s: %s groups" % (r["path"], r["track"], r["groups"]))
        if int(r["keys"]) and int(r["keys"]) > int(r["groups"]) * int(r["max_keys"]):
            bad.append("%s %s: %s keys over %s groups of at most %s"
                       % (r["path"], r["track"], r["keys"], r["groups"], r["max_keys"]))
        if int(r["keys"]) and int(r["empty_groups"]) > int(r["groups"]):
            bad.append("%s %s: %s empty groups of %s" % (r["path"], r["track"], r["empty_groups"], r["groups"]))
        if int(r["keys"]) and int(r["time_min"]) > int(r["time_max"]):
            bad.append("%s %s: time %s..%s" % (r["path"], r["track"], r["time_min"], r["time_max"]))
    synth = synthetic()
    rows2 = measure_bytes(synth)
    if len(rows2) != 1:
        bad.append("selftest: %d tracks" % len(rows2))
    else:
        s = rows2[0]
        if s["groups"] != GROUPS or s["keys"] != 69 or s["max_keys"] != 69 or s["time_max"] != 680:
            bad.append("selftest: read %r groups / %r keys / max %r / time %r"
                       % (s["groups"], s["keys"], s["max_keys"], s["time_max"]))
        if s["value_max"] != "0.85":
            bad.append("selftest: value %r" % (s["value_max"],))
    if bad:
        for b in bad[:20]:
            print("FAIL lipsinc-keys: " + b)
        return 1
    print("ok   lipsinc-keys: %d tracks, %d groups, %d keys, blocks %d..%d bytes"
          % (len(rows), sum(int(r["groups"]) for r in rows), sum(int(r["keys"]) for r in rows),
             min(int(r["block_bytes"]) for r in rows), max(int(r["block_bytes"]) for r in rows)))
    return 0


def walk(path, want, limit=None):
    lt = _tracks_module()
    d = open(path, "rb").read()
    rows = lt.measure(path)
    for rec in rows["_tracks"]:
        if rec["full"] != want:
            continue
        groups, stop = key_block(d, rec["keys_offset"], rec["keys_offset"] + rec["block"])
        print("%s: block %d bytes at %#x, %d groups, ends at +%d with 0x00"
              % (rec["full"], rec["block"], rec["keys_offset"], len(groups), stop - rec["keys_offset"]))
        for i, g in enumerate(groups):
            print("  group %2d: %3d keys%s" % (i, len(g), "" if not g else
                                               "  time %d..%d  value %s"
                                               % (g[0][0], g[-1][0],
                                                  " ".join("%.4g" % v for _, v in g[:3]))))
        return 0
    print("no track named %r in %s" % (want, path))
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dir", help="directory of .lad files (walked recursively)")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--walk", metavar="TRACK", help="print one track's groups and key ranges")
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
    if args.walk:
        if len(files) != 1:
            ap.error("--walk takes exactly one .lad file")
        return walk(files[0], args.walk)
    rows = []
    for f in sorted(files):
        rows += measure(f)
    if args.dir:
        root = os.path.abspath(args.dir)
        for r in rows:
            r["path"] = os.path.relpath(os.path.abspath(r["path"]), root)
    os.makedirs(args.out, exist_ok=True)
    dest = os.path.join(args.out, CSV)
    with open(dest, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in COLUMNS})
    refusals = [r for r in rows if not r["groups"]]
    print("wrote %s (%d tracks, %d keys, %d refused)"
          % (dest, len(rows), sum(r["keys"] for r in rows), len(refusals)))
    for r in refusals[:10]:
        print("  refused %s %s: %s" % (r["path"], r["track"], r.get("error")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
