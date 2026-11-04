#!/usr/bin/env python3
"""Measure a `.lad` file's record structure without guessing its fields (docs/formats/lipsinc-lad.md, U4).

The file starts with a parsed header (two u32, a channel count and that many length-prefixed bone names). After it
comes the payload, and the payload's records are 32 bytes: a `u32 2`, three floats, a 12-byte tail and a `-1.0f`
terminator. **Matching `-1.0f` alone is not enough to find them** -- the four bytes of `-1.0f` occur by chance inside
float data, so this tool requires the tail as well and reports both counts, which is the difference between a record
census and a byte-pattern census.

    tools/lad-scan.py FILE.lad [...]        # one line per file
    tools/lad-scan.py --json FILE.lad       # machine-readable

It measures; it does not interpret. What the three floats and the twelve tail bytes mean is U4.
"""
import argparse
import json
import struct
import sys
from collections import Counter

SENTINEL = struct.pack("<f", -1.0)


def parse(path):
    with open(path, "rb") as f:
        d = f.read()
    a, b = struct.unpack_from("<II", d, 0)
    n = d[8]
    off = 9
    names = []
    for _ in range(n):
        ln = d[off]
        names.append(d[off + 1:off + 1 + ln].rstrip(b"\0").decode("latin1"))
        off += 1 + ln
    return d, a, b, n, names, off


def scan(path):
    d, a, b, n, names, table_end = parse(path)
    hits = []
    i = d.find(SENTINEL)
    while i >= 0:
        hits.append(i)
        i = d.find(SENTINEL, i + 1)
    # A hit is a record terminator only if the 12 bytes in front of it are a tail this file uses more than once.
    tails = Counter(d[h - 12:h] for h in hits if h >= 12)
    real_tail, real_count = tails.most_common(1)[0] if tails else (b"", 0)
    ends = [h for h in hits if h >= 12 and d[h - 12:h] == real_tail]
    starts = [h - 28 for h in ends]
    gaps = Counter(y - x for x, y in zip(starts, starts[1:]))
    return {
        "path": path,
        "bytes": len(d),
        "header": [a, b],
        "channels": n,
        "bone_names": len(names),
        "table_end": table_end,
        "payload_bytes": len(d) - table_end,
        "sentinel_hits": len(hits),
        "records": len(ends),
        "tail": real_tail.hex(" "),
        "tail_repeats": real_count,
        "strides": dict(sorted(gaps.items())),
        "first_record": starts[0] if starts else None,
        "bytes_before_first": (starts[0] - table_end) if starts else None,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+")
    ap.add_argument("--json", action="store_true", help="print the measurements as JSON")
    args = ap.parse_args()
    rows = [scan(p) for p in args.files]
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    for r in rows:
        print("%s: %d bytes, %d channels, table ends 0x%x, payload %d bytes"
              % (r["path"].rsplit("/", 1)[-1], r["bytes"], r["channels"], r["table_end"], r["payload_bytes"]))
        print("  -1.0f hits %d; with the file's own tail (%s, %dx) %d records, first at table_end+%s"
              % (r["sentinel_hits"], r["tail"], r["tail_repeats"], r["records"], r["bytes_before_first"]))
        print("  strides between record starts: %s" % r["strides"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
