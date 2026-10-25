#!/usr/bin/env python3
"""Re-encode a UE2 package's tables from the parsed model, and diff them against the original bytes.

Writing a package is the step PLAN 6.1 needs and the one that proves the reader: if the name, import and export
tables can be *regenerated* from what we parsed and come out byte-identical, the model is right, not merely
plausible. What is not yet written is payload generation — a rebuild gets those bytes from a compiler.

    python3 tools/pkg-write.py DUMP/System              # verify every package
    python3 tools/pkg-write.py --selftest               # index codec only, no dump (runs in CI)

The comparison is per table with the exact first differing offset, because "the tables differ" is not information
and "your encoder disagrees at 0x2a975e" is.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import TAG, Package  # noqa: E402


def write_index(value):
    """Inverse of `Package.read_index`: sign in bit 7 of the first byte, continuation in bit 6, then bit 7."""
    negative = value < 0
    v = -value if negative else value
    first = v & 0x3F
    rest = v >> 6
    out = bytearray()
    if rest:
        out.append(first | 0x40 | (0x80 if negative else 0))
        while rest:
            chunk = rest & 0x7F
            rest >>= 7
            out.append(chunk | (0x80 if rest else 0))
    else:
        out.append(first | (0x80 if negative else 0))
    return bytes(out)


def decode_index(buf, off=0):
    """Local copy of the reader's decoder, so the selftest does not depend on a package file."""
    b = buf[off]
    sign, cont, val = b & 0x80, b & 0x40, b & 0x3F
    off += 1
    shift = 6
    while cont and shift < 32:
        b = buf[off]
        off += 1
        cont = b & 0x80
        val |= (b & 0x7F) << shift
        shift += 7
    return (-val if sign else val), off


def encode_names(names):
    out = bytearray()
    for text, flags in names:
        raw = text.encode("latin1") + b"\0"
        if len(raw) > 0x7F:
            raise ValueError("name too long for a compact length: %r" % text)
        out.append(len(raw))
        out += raw
        out += struct.pack("<I", flags)
    return bytes(out)


def encode_imports(imports):
    out = bytearray()
    for r in imports:
        out += write_index(r["class_package"])
        out += write_index(r["class"])
        out += struct.pack("<I", r["package"])
        out += write_index(r["name"])
    return bytes(out)


def encode_exports(exports):
    out = bytearray()
    for r in exports:
        out += write_index(r["class"])
        out += write_index(r["super"])
        out += struct.pack("<I", r["outer"])
        out += write_index(r["name"])
        out += struct.pack("<I", r["flags"])
        out += write_index(r["size"])
        out += write_index(r["offset"])
    return bytes(out)


def compare(label, want, got):
    if want == got:
        return True, "%s ok (%d bytes)" % (label, len(want))
    n = min(len(want), len(got))
    at = next((i for i in range(n) if want[i] != got[i]), n)
    return False, "%s DIFF at +0x%x (wrote %d bytes, original %d): original %s vs model %s" % (
        label, at, len(got), len(want),
        want[at:at + 8].hex(" ") or "-", got[at:at + 8].hex(" ") or "-")


def selftest():
    values = [0, 1, 0x3F, 0x40, 0x7F, 0x80, 0x3FFFF, 0x40000, -1, -0x3F, -0x8000, 0x7FFFFFF]
    bad = 0
    for v in values:
        enc = write_index(v)
        dec, used = decode_index(enc)
        if dec != v or used != len(enc):
            print("FAIL index %d -> %s -> %d" % (v, enc.hex(" "), dec))
            bad += 1
    if bad:
        return 1
    print("ok   index codec round-trips %d values (including sign and multi-byte)" % len(values))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("system_dir", nargs="?", help="a directory of .u packages")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest or not args.system_dir:
        return selftest()

    files = sorted(f for f in os.listdir(args.system_dir) if f.lower().endswith(".u"))
    ok_all, bad = 0, []
    for fname in files:
        pkg = Package(os.path.join(args.system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        problems = []
        # Each table is regenerated from the parsed model and compared over its own length, so the slice is
        # exactly as long as what we wrote -- no guessing where the original table ends.
        enc_names = encode_names(pkg.names)
        enc_imports = encode_imports(pkg.imports)
        enc_exports = encode_exports(pkg.exports)
        for label, enc, start in (("names", enc_names, pkg.name_offset),
                                  ("imports", enc_imports, pkg.import_offset),
                                  ("exports", enc_exports, pkg.export_offset)):
            want = pkg.data[start:start + len(enc)]
            good, msg = compare("%s %s" % (fname, label), want, enc)
            if not good:
                problems.append(msg)
        if problems:
            bad.append((fname, problems))
        else:
            ok_all += 1
            print("ok   %-22s names %5d imports %3d exports %5d re-encode byte-identical"
                  % (fname, len(pkg.names), len(pkg.imports), len(pkg.exports)))
    for fname, problems in bad:
        print("FAIL %s" % fname)
        for p in problems:
            print("     " + p)
    print("\n%d of %d packages re-encode their tables byte for byte" % (ok_all, len(files)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
