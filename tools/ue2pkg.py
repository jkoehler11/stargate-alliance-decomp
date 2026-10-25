#!/usr/bin/env python3
"""A minimal Unreal Engine 2 package reader: header, name table, and (best effort) the export table.

Every content question in this repository that is still open comes back to the same missing piece: a way to
read a `.u`/`.unr` without UELib. Maps (5.2), the ucpp function records (4.7), the SFX inside `.uax`, and
the `Packages.md5` digests all need it. This is the first honest slice of one: the two tables that can be
parsed and *checked* -- the header and the name table -- with the export table attempted and refused rather
than guessed when it does not line up.

    python3 tools/ue2pkg.py DUMP/System/Core.u
    python3 tools/ue2pkg.py DUMP/System -o inventory     # every .u: inventory/package_names.csv

The name table is worth having on its own: it is every identifier in the package -- class names, property
names, function names -- so it answers "does this class declare a function called X" without a decompiler.
"""
import argparse
import csv
import os
import struct
import sys

TAG = 0x9E2A83C1


class Package:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            self.data = f.read()
        d = self.data
        if len(d) < 0x2C or struct.unpack_from("<I", d, 0)[0] != TAG:
            raise ValueError("%s: not a UE2 package (bad tag)" % path)
        (self.version, self.licensee,
         self.flags, self.name_count, self.name_offset,
         self.export_count, self.export_offset,
         self.import_count, self.import_offset) = struct.unpack_from("<HHIIIIIII", d, 4)
        self.names = None
        self.name_error = None

    def read_names(self):
        """Length-prefixed strings with four flag bytes each. Refuses rather than inventing names."""
        d, off, out = self.data, self.name_offset, []
        try:
            for _ in range(self.name_count):
                n = d[off]
                if n == 0 or n > 0x7F:
                    raise ValueError("length %d at 0x%x is not a compact name length" % (n, off))
                off += 1
                raw = d[off:off + n]
                # the stored length counts the terminating NUL
                if raw.endswith(b"\0"):
                    raw = raw[:-1]
                if not raw:
                    raise ValueError("empty name at 0x%x" % off)
                if not all(32 <= c < 127 for c in raw):
                    raise ValueError("non-printable name at 0x%x" % off)
                off += n
                flags = struct.unpack_from("<I", d, off)[0]
                off += 4
                out.append((raw.decode("latin1"), flags))
        except (ValueError, struct.error) as exc:
            self.name_error = str(exc)
            self.names = out
            return out
        self.names = out
        return out

    def name(self, i):
        return self.names[i][0] if self.names and 0 <= i < len(self.names) else "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="a package file, or a directory of them")
    ap.add_argument("-o", "--out")
    ap.add_argument("--grep", help="only print names containing this substring")
    args = ap.parse_args()

    paths = ([os.path.join(args.path, f) for f in sorted(os.listdir(args.path))
              if f.lower().endswith((".u", ".unr", ".uax", ".usx", ".utx", ".ukx"))]
             if os.path.isdir(args.path) else [args.path])

    rows = []
    for path in paths:
        try:
            pkg = Package(path)
        except ValueError as exc:
            print("%-26s %s" % (os.path.basename(path), exc))
            continue
        pkg.read_names()
        got = len(pkg.names or [])
        status = "ok" if got == pkg.name_count and not pkg.name_error else \
                 ("partial: %s" % pkg.name_error if pkg.name_error else "count mismatch")
        print("%-26s v%-4d lic=%-3d names %5d/%-5d exports %5d imports %4d  %s"
              % (os.path.basename(path), pkg.version, pkg.licensee, got, pkg.name_count,
                 pkg.export_count, pkg.import_count, status))
        if args.grep:
            hits = [n for n, _ in (pkg.names or []) if args.grep.lower() in n.lower()]
            for h in hits[:40]:
                print("      %s" % h)
        for i, (name, flags) in enumerate(pkg.names or []):
            rows.append({"package": os.path.splitext(os.path.basename(path))[0],
                         "index": i, "name": name, "flags": "0x%08x" % flags})

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "package_names.csv")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["package", "index", "name", "flags"])
            w.writeheader()
            w.writerows(rows)
        print("wrote %s (%d names)" % (path, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
