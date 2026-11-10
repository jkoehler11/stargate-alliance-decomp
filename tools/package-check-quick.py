#!/usr/bin/env python3
"""Reproduce the `Packages.md5` **quick** check digest (PLAN 6.2).

`Packages.md5` records two values per package: the header GUID, which pairs the record with a
file, and a lowercase digest that is neither `md5(file)` nor any framing of the file's bytes.
It is the game's own *quick* pass, `0x00121cf0`, and this tool rebuilds the buffer it hashes:

    header dwords 0x00..0x34        tag, version|licensee, flags, the three count/offset pairs,
                                   and the GUID -- thirteen dwords, raw little-endian
    generation entries              `gen_count` records of 8 bytes at 0x38; the count itself is
                                   *not* hashed
    per name entry, 8 bytes         the entry's flags dword, then the first two UTF-16 units
                                   of the name

so the digest covers the header and the name table and nothing else. That is why a rebuilt
package keeps its record: the writer copies the header, and copying payloads cannot move the
name table.

    python3 tools/package-check-quick.py DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15 -o inventory
    python3 tools/package-check-quick.py --check          # synthetic self-test, no dump
    python3 tools/package-check-quick.py --walk PKG.u     # per-name record bytes

Evidence for the layout is in `docs/PACKAGE_CHECK.md`: the reader at `0x00062110` fills the
entry struct as `flags` at `+0x04` and the wide name at `+0x0C`, and `0x00121cf0` appends
`[entry+0x04]` (four bytes) then the words at `+0x0C` and `+0x0E`.

`--check` answers without the dump and writes nothing, so `make ci` keeps the framing honest.
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
HEADER_END = 0x34          # the thirteen dwords the transform appends end here (GUID included)
GEN_COUNT_OFF = 0x34
GEN_ENTRY_OFF = 0x38
GUID_OFF = 0x24
RECORD_RE = re.compile(rb"\x00\x21([0-9A-F]{32})\x00\x01\x21([0-9a-f]{32})\x00")
PACKAGE_GLOBS = ("*.u", "*.unr", "*.uax", "*.utx", "*.usx", "*.ukx", "*.sgs")


def check_records(blob):
    """The check file's (uppercase GUID, lowercase digest) pairs, in file order."""
    return [(m.group(1).decode(), m.group(2).decode()) for m in RECORD_RE.finditer(blob)]


def header_guid(data):
    if len(data) < GUID_OFF + 16 or struct.unpack_from("<I", data, 0)[0] != TAG:
        return None
    return "".join("%08X" % v for v in struct.unpack_from("<IIII", data, GUID_OFF))


def read_names(data):
    """(name, flags, offset) per name-table entry: length byte (counts the NUL), chars, flags."""
    (version, _lic, _flags, name_count, name_offset, _ec, _eo, _ic, _io) = struct.unpack_from(
        "<HHIIIIIII", data, 4)
    out, off = [], name_offset
    for _ in range(name_count):
        if off >= len(data):
            raise ValueError("name table runs past EOF at %#x" % off)
        length = data[off]
        if length == 0 or length > 0x7F:
            raise ValueError("length %d at %#x is not a compact name length" % (length, off))
        start = off
        off += 1
        raw = data[off:off + length]
        off += length
        if len(raw) != length:
            raise ValueError("truncated name at %#x" % start)
        name = raw.rstrip(b"\0")
        flags = struct.unpack_from("<I", data, off)[0]
        off += 4
        out.append((name, flags, start))
    return out, (version, name_count, name_offset, off)


def name_record(name, flags):
    """The eight bytes `0x00121cf0` appends for one entry: flags, then the first two wide units."""
    wide = name.decode("latin1")
    wide = (wide + "\0\0")[:2]
    return struct.pack("<I", flags) + struct.pack("<HH", ord(wide[0]), ord(wide[1]))


def digest_buffer(data):
    """The exact byte string the quick pass hashes, plus a census of what went into it."""
    if struct.unpack_from("<I", data, 0)[0] != TAG:
        raise ValueError("not a UE2 package")
    gen_count = struct.unpack_from("<I", data, GEN_COUNT_OFF)[0]
    if gen_count > 64:
        raise ValueError("generation count %d at %#x is not credible" % (gen_count, GEN_COUNT_OFF))
    names, hdr = read_names(data)
    buf = bytearray(data[:HEADER_END])
    buf += data[GEN_ENTRY_OFF:GEN_ENTRY_OFF + 8 * gen_count]
    for name, flags, _off in names:
        buf += name_record(name, flags)
    return bytes(buf), {"gen_count": gen_count, "names": len(names), "version": hdr[0],
                        "name_end": hdr[3]}


def quick_digest(data):
    """(digest, buffer length, census) for one package's bytes."""
    buf, census = digest_buffer(data)
    return hashlib.md5(buf).hexdigest(), len(buf), census


def scan(root):
    """(relative path, header GUID, bytes) for every package under `root`."""
    out = []
    for ext in PACKAGE_GLOBS:
        for path in glob.glob(os.path.join(root, "**", ext), recursive=True):
            with open(path, "rb") as fh:
                data = fh.read()
            guid = header_guid(data)
            if guid is None:
                continue
            out.append((os.path.relpath(path, root), guid, data))
    return sorted(out)


def walk(path):
    with open(path, "rb") as fh:
        data = fh.read()
    names, hdr = read_names(data)
    print("%s: version %d, %d names at %#x, table ends %#x"
          % (os.path.basename(path), hdr[0], hdr[1], hdr[2], hdr[3]))
    print("gen count %d: %s" % (struct.unpack_from("<I", data, GEN_COUNT_OFF)[0],
                                data[GEN_ENTRY_OFF:GEN_ENTRY_OFF + 8].hex()))
    for name, flags, off in names:
        print("  %#08x  flags %#010x  %-32s  record %s"
              % (off, flags, name.decode("latin1"), name_record(name, flags).hex()))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", help="the dump's game directory (holds System/Packages.md5)")
    ap.add_argument("-o", "--out", help="directory for inventory/package_check_quick.csv")
    ap.add_argument("--walk", metavar="PKG", help="print the per-name record bytes for one package")
    ap.add_argument("--check", action="store_true", help="synthetic self-test on built bytes, no dump")
    args = ap.parse_args()

    if args.check:
        return self_test()
    if args.walk:
        return walk(args.walk)
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
    for rel, guid, data in files:
        by_guid.setdefault(guid, []).append((rel, data))

    rows, paired, reproduced, errors = [], 0, 0, 0
    shared, shared_all = 0, 0
    for index, (guid, digest) in enumerate(records):
        candidates = by_guid.get(guid, [])
        if candidates:
            paired += 1
        matches, first = [], None
        for rel, data in candidates:
            try:
                got, buflen, census = quick_digest(data)
            except (ValueError, struct.error):
                errors += 1
                continue
            if first is None:
                first = (rel, got, buflen)
            if got == digest:
                matches.append(rel)
        if first is None:
            rows.append({"record": index, "guid": guid, "digest": digest, "file": "",
                         "computed": "", "match": "", "same_guid": 0, "matches": 0,
                         "buffer_bytes": ""})
            continue
        rel, got, buflen = (matches[0], digest, first[2]) if matches else first
        hit = bool(matches)
        reproduced += 1 if hit else 0
        if len(candidates) > 1:
            shared += 1
            if len(matches) == len(candidates):
                shared_all += 1
        rows.append({"record": index, "guid": guid, "digest": digest, "file": rel,
                     "computed": got, "match": "1" if hit else "0",
                     "same_guid": len(candidates), "matches": len(matches),
                     "buffer_bytes": buflen})

    print("Packages.md5: %d records, %d package files with a UE2 header" % (len(records), len(files)))
    print("  records whose GUID a shipped package carries: %d" % paired)
    print("  records whose quick digest is reproduced:     %d of %d"
          % (reproduced, len(records)))
    if shared:
        print("  records whose GUID several shipped files carry: %d "
              "(every copy reproduces the same digest: %d)" % (shared, shared_all))
    if errors:
        print("  files refused as unreadable name tables: %d" % errors)
    if reproduced == len(records) == paired:
        print("  -> the check is header + name table: a rebuilt package reproduces its record")
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "package_check_quick.csv")
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["record", "guid", "digest", "file", "computed",
                                               "match", "same_guid", "matches", "buffer_bytes"],
                               lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        print("wrote %s (%d rows)" % (path, len(rows)))
    return 0 if reproduced == len(records) else 1


def quick_digest(data):
    buf, census = digest_buffer(data)
    return hashlib.md5(buf).hexdigest(), len(buf), census


def synthetic_package():
    """A three-name package built field by field, and the buffer the transform must hash."""
    names = [(b"Actor", 0x00000000), (b"Movement", 0x00080001), (b"X", 0x00000002)]
    name_blob = bytearray()
    name_offset = 0x100
    for name, flags in names:
        name_blob += bytes([len(name) + 1]) + name + b"\0"
        name_blob += struct.pack("<I", flags)
    header = bytearray(HEADER_END)
    struct.pack_into("<I", header, 0x00, TAG)
    struct.pack_into("<HH", header, 0x04, 130, 4)
    struct.pack_into("<I", header, 0x08, 0x00000001)
    struct.pack_into("<I", header, 0x0C, len(names))
    struct.pack_into("<I", header, 0x10, name_offset)
    struct.pack_into("<I", header, 0x14, 0)
    struct.pack_into("<I", header, 0x18, 0x200)
    struct.pack_into("<I", header, 0x1C, 2)
    struct.pack_into("<I", header, 0x20, 0x210)
    struct.pack_into("<IIII", header, GUID_OFF, 0x11223344, 0x55667788, 0x99AABBCC, 0xDDEEFF00)
    gen = struct.pack("<II", 1, 0)          # one generation record: export count, name count
    data = bytearray()
    data += header
    data += struct.pack("<I", 1)
    data += gen
    data += bytes(name_offset - len(data))
    data += name_blob
    expected = bytearray(header)
    expected += gen
    # flags then the first two wide units, written out one field at a time
    for name, flags in names:
        two = (name + b"\0\0")[:2]
        expected += struct.pack("<I", flags)
        expected += struct.pack("<H", two[0])
        expected += struct.pack("<H", two[1])
    return bytes(data), bytes(expected)


def self_test():
    data, expected = synthetic_package()
    buf, census = digest_buffer(data)
    ok = True
    if buf != expected:
        print("FAIL: transform buffer differs from the hand-built one (%d vs %d bytes)"
              % (len(buf), len(expected)))
        ok = False
    if census["names"] != 3 or census["gen_count"] != 1:
        print("FAIL: census %r" % census)
        ok = False
    got = hashlib.md5(buf).hexdigest()
    if got != hashlib.md5(expected).hexdigest():
        print("FAIL: digest %s" % got)
        ok = False
    if check_records(b"\x00\x21" + b"A" * 32 + b"\x00\x01\x21" + b"a" * 32 + b"\x00") != [
            ("A" * 32, "a" * 32)]:
        print("FAIL: record regex")
        ok = False
    print("ok: synthetic package -- %d-byte buffer, digest %s" % (len(buf), got))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
