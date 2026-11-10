#!/usr/bin/env python3
"""Pair every `Packages.md5` record with the package file it describes (PLAN 6.2).

The check file's records carry two 32-hex fields and no filename, so the format page concluded a
digest could not be tied to a file. It can: `0x001219d0` prints a package's first value as
`GUID = [%08X%08X%08X%08X]` straight out of the package header (+0x24), and every record's
uppercase field is exactly that text. So the uppercase field *is* the package's own header GUID,
which gives the pairing, leaves the lowercase field as the digest to reproduce, and shows why no
`md5(file)` search ever matched a lowercase value: the database was written by the quick pass
(`0x00121cf0`, header + name entries), not by the full one (`0x00121cf0`'s sibling `0x001219d0`).

    python3 tools/package-check-pairs.py DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15 -o inventory

Writes `inventory/package_check_pairs.csv` (one row per record, `file` empty when no shipped
package carries that GUID) and prints the census. `--check` runs a synthetic self-test with no dump.
"""
import argparse
import csv
import glob
import hashlib
import os
import re
import struct
import sys

TAG = 0x9E2A83C1
# A record is fixed-layout text: \x00 \x21 <32 upper> \x00 \x01 \x21 <32 lower> \x00
RECORD_RE = re.compile(rb"\x00\x21([0-9A-F]{32})\x00\x01\x21([0-9a-f]{32})\x00")
# Every package kind the dump ships. A .sgs checkpoint is an ordinary UE2 package too.
PACKAGE_GLOBS = ("*.u", "*.unr", "*.uax", "*.utx", "*.usx", "*.ukx", "*.sgs")
GUID_OFF = 0x24  # tag, version|licensee, flags, six count/offset dwords, then the GUID


def check_records(blob):
    """The check file's records as (uppercase, lowercase) hex pairs, in file order."""
    return [(m.group(1).decode(), m.group(2).decode()) for m in RECORD_RE.finditer(blob)]


def header_guid(data):
    """The package header's GUID as the same 32-character uppercase text the commandlet prints."""
    if len(data) < GUID_OFF + 16 or struct.unpack_from("<I", data, 0)[0] != TAG:
        return None
    return "".join("%08X" % v for v in struct.unpack_from("<IIII", data, GUID_OFF))


def scan(root, exts=PACKAGE_GLOBS):
    """Every package under `root` as (relative path, header GUID, md5 of the file as shipped)."""
    out = []
    for ext in exts:
        for path in glob.glob(os.path.join(root, "**", ext), recursive=True):
            with open(path, "rb") as fh:
                data = fh.read()
            guid = header_guid(data)
            if guid is None:
                continue
            out.append((os.path.relpath(path, root), guid, hashlib.md5(data).hexdigest()))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", help="the dump's game directory (holds System/Packages.md5)")
    ap.add_argument("-o", "--out", help="directory for inventory/package_check_pairs.csv")
    ap.add_argument("--check", action="store_true", help="self-test on synthetic bytes, no dump")
    args = ap.parse_args()

    if args.check:
        return self_test()
    if not args.root:
        sys.exit("root is required (the game directory holding System/Packages.md5)")

    md5_path = os.path.join(args.root, "System", "Packages.md5")
    if not os.path.isfile(md5_path):
        print("required: no System/Packages.md5 under %s" % args.root, file=sys.stderr)
        return 2
    with open(md5_path, "rb") as fh:
        records = check_records(fh.read())
    if not records:
        sys.exit("%s holds no check records -- refusing to report an empty census" % md5_path)

    files = scan(args.root)
    by_guid = {}
    for rel, guid, digest in files:
        by_guid.setdefault(guid, []).append(rel)

    rows, paired, digest_hits = [], 0, 0
    for index, (guid, digest) in enumerate(records):
        names = by_guid.get(guid, [])
        if names:
            paired += 1
        if any(f[2] == digest for f in files if f[1] == guid):
            digest_hits += 1
        rows.append({"record": index, "guid": guid, "digest": digest,
                     "file": names[0] if names else "",
                     "same_guid": len(names)})

    distinct_guid = len({g for g, _ in records})
    distinct_digest = len({d for _, d in records})
    print("Packages.md5: %d records, %d distinct GUIDs, %d distinct digests"
          % (len(records), distinct_guid, distinct_digest))
    print("  package files with a UE2 header: %d (%d distinct header GUIDs)"
          % (len(files), len(by_guid)))
    print("  records whose GUID a shipped package carries: %d" % paired)
    print("  records whose digest is md5(file) for one of those files: %d" % digest_hits)
    if digest_hits == 0 and paired:
        print("  -> the GUID field pairs records to files; the digest is not md5(file),")
        print("     which is what the quick pass at 0x00121cf0 (header + names) produces")
    dupes = sorted(((len(v), v[0], g) for g, v in by_guid.items() if len(v) > 1), reverse=True)
    if dupes:
        print("  header GUIDs shared by several files: %d (largest: %d files, e.g. %s)"
              % (len(dupes), dupes[0][0], dupes[0][1]))
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "package_check_pairs.csv")
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["record", "guid", "digest", "file", "same_guid"],
                               lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        print("wrote %s (%d rows)" % (path, len(rows)))
    return 0


def self_test():
    """A synthetic package and one synthetic record: the pairing must survive both."""
    guid = bytes.fromhex("11223344556677889900aabbccddeeff")
    pkg = struct.pack("<I", TAG) + struct.pack("<IHH", 130, 4, 1) + b"\0" * 24 + guid
    pkg += b"\0" * 32
    text = "".join("%08X" % v for v in struct.unpack("<IIII", guid)).encode()
    blob = b"\x00\x21" + text + b"\x00\x01\x21" + b"0" * 32 + b"\x00"
    assert header_guid(pkg) == text.decode(), header_guid(pkg)
    assert check_records(blob) == [(text.decode(), "0" * 32)], check_records(blob)
    assert header_guid(b"not a package") is None
    print("self-test ok: header GUID and check record agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
