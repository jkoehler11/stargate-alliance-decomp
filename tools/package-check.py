#!/usr/bin/env python3
"""Measure `System/Packages.md5` against the packages that actually ship (PLAN 1.4).

The file is not a text `md5sum` list: it is itself an Unreal Engine 2 package (tag 0x9E2A83C1,
version 130, licensee 4) whose exports are 168 `PackageCheckInfo` objects, each carrying a 16-byte
`md5` property. This measures what it contains and what it does *not* match, which is the baseline
PLAN 6.2 needs: a rebuilt package can only satisfy this check once the transform behind those
digests is reproduced.

    python3 tools/package-check.py DUMP/.../System -o inventory

Writes `inventory/package_check.csv` (one row per shipped `.u`) and prints the check file's own
header. Nothing is written when a package's digest cannot be established -- the CSV says `absent`
rather than guessing.
"""
import argparse
import csv
import hashlib
import os
import re
import struct
import sys

TAG = 0x9E2A83C1
IMPORT_RE = re.compile(rb"[ -~]{3,}")


def parse_header(blob):
    if len(blob) < 0x28:
        return None
    tag, version, licensee, flags = struct.unpack_from("<IHHH", blob, 0)
    if tag != TAG:
        return None
    names, name_off, exports, export_off, imports, import_off = struct.unpack_from("<6I", blob, 12)
    return {"version": version, "licensee": licensee, "flags": flags, "names": names,
            "name_offset": name_off, "exports": exports, "export_offset": export_off,
            "imports": imports, "import_offset": import_off}


def name_table(blob, hdr):
    """Compact-index name table: length byte, characters, 4 flag bytes."""
    out, off, end = [], hdr["name_offset"], min(hdr["name_offset"] + 40000, len(blob))
    while off < end and len(out) < hdr["names"]:
        n = blob[off]
        if n == 0:
            break
        off += 1
        raw = blob[off:off + n]
        if not all(32 <= c < 127 for c in raw):
            break
        out.append(raw.decode("latin1"))
        off += n + 4
    return out


def candidates(path):
    """Hash schemes that could plausibly be what the runtime hashed, in order of likelihood."""
    data = open(path, "rb").read()
    schemes = {
        "md5(file)": hashlib.md5(data).digest(),
        "md5(file[4:])": hashlib.md5(data[4:]).digest(),
        "md5(file[16:])": hashlib.md5(data[16:]).digest(),
        "md5(header zeroed)": hashlib.md5(b"\0" * 16 + data[16:]).digest(),
    }
    return len(data), schemes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("system_dir", help="the dump's System/ directory")
    ap.add_argument("-o", "--out", help="directory for inventory/package_check.csv")
    args = ap.parse_args()

    md5_path = os.path.join(args.system_dir, "Packages.md5")
    if not os.path.isfile(md5_path):
        sys.exit("no Packages.md5 in %s" % args.system_dir)
    blob = open(md5_path, "rb").read()
    hdr = parse_header(blob)
    if not hdr:
        sys.exit("%s is not a UE2 package (bad tag) -- refusing to interpret it" % md5_path)

    # The compact name table's entries are length-prefixed strings with four flag bytes, but the
    # table's first bytes are not a name this parser can key on, so the check objects are counted
    # from the names themselves -- which is what the question needs -- and the table offset/count
    # are reported from the header for whoever writes the full reader.
    checks = sorted({m.group(0) for m in re.finditer(rb"PackageCheckInfo\d+", blob)})
    names = sorted(set(IMPORT_RE.findall(blob)))
    print("Packages.md5: %d bytes, tag 0x%08x, version %d, licensee %d"
          % (len(blob), TAG, hdr["version"], hdr["licensee"]))
    print("  name table %d entries at 0x%x, exports %d at 0x%x, imports %d at 0x%x"
          % (hdr["names"], hdr["name_offset"], hdr["exports"],
             hdr["export_offset"], hdr["imports"], hdr["import_offset"]))
    print("  PackageCheckInfo objects named: %d" % len(checks))

    rows, matched = [], 0
    for fname in sorted(f for f in os.listdir(args.system_dir) if f.lower().endswith(".u")):
        size, schemes = candidates(os.path.join(args.system_dir, fname))
        where = None
        for label, digest in schemes.items():
            i = blob.find(digest)
            if i >= 0:
                where = "%s@0x%x" % (label, i)
                matched += 1
                break
        rows.append({"package": fname, "bytes": size,
                     "md5": hashlib.md5(open(os.path.join(args.system_dir, fname), "rb").read()).hexdigest(),
                     "recovered_from": where or "absent",
                     "check_entries": len(checks)})
        print("  %-24s %8d  %s" % (fname, size, where or "absent"))

    print("\nrecovered: %d of %d shipped packages" % (matched, len(rows)))
    if matched == 0:
        print("none of the four hash schemes over the shipped bytes appears in the check file:")
        print("  the digests were produced from something other than these files as shipped,")
        print("  so a rebuild cannot satisfy the check until the transform is measured (PLAN 6.2)")
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "package_check.csv")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["package", "bytes", "md5", "recovered_from", "check_entries"])
            w.writeheader()
            w.writerows(rows)
        print("wrote %s (%d rows)" % (path, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
