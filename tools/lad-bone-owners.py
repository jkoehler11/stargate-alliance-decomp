#!/usr/bin/env python3
"""Which shipped package names each `.lad` channel as a bone (docs/UNKNOWNS.md U4, the runtime half).

`tools/lad-bones.py` measured the channel table as the format's only ordered bone list. The runtime says what
those names are for: `FUN_000b4930` (the controller's linkup, reached from `LIPSincLinkBones`) compares each
channel name against the mesh's bone names with `_wcsicmp` and writes the matching **mesh bone index** into a
dword array on the controller, one entry per channel, and a controller is only "valid" when every channel
matched (`docs/formats/lipsinc-runtime.md`). So a channel name is a *bone* name, and the mesh that carries it
is a package.

This tool measures the other side of that comparison: it reads every `.lad` channel table, then scans every
UE2 package in the dump's name table for those names, and writes one row per distinct name with the number of
packages that declare it and the first few owners.

    tools/lad-bone-owners.py --dump "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15" -o inventory
    tools/lad-bone-owners.py --check                 # dump-free, validates the committed CSV

It is a name census, not a skeleton reconstruction: a name in a package's name table says the package mentions
the bone, not that it is the mesh the game binds to that character.
"""
import argparse
import csv
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CSV = "lipsinc_bone_owners.csv"
BONES_CSV = "lipsinc_bones.csv"
COLUMNS = ["name", "files", "file_count", "packages", "owners"]
PACKAGE_EXT = (".u", ".unr", ".utx", ".ukx", ".usx", ".uax")
OWNERS = 5                            # how many package names to record per bone


def channel_names(path):
    """A `.lad`'s channel table: u8 count at 8, then count bare strings (u8 length including the NUL)."""
    with open(path, "rb") as f:
        d = f.read(0x2000)
    count, off, out = d[8], 9, []
    for _ in range(count):
        n = d[off]
        off += 1
        raw = d[off:off + n]
        if raw.endswith(b"\0"):
            raw = raw[:-1]
        out.append(raw.decode("latin1"))
        off += n
    return out


def package_names(path):
    """The name table of a UE2 package, or None when the file is not one (tag + a 120..130 version)."""
    try:
        with open(path, "rb") as f:
            d = f.read(1 << 20)               # the name table sits near the front
    except OSError:
        return None
    if len(d) < 0x2C or struct.unpack_from("<I", d, 0)[0] != 0x9E2A83C1:
        return None
    version, _lic, _flags, count, off = struct.unpack_from("<HHIII", d, 4)
    if not 120 <= version <= 130:
        return None
    names = []
    try:
        for _ in range(count):
            n = d[off]
            off += 1
            if not 0 < n <= 0x7F:
                return None
            raw = d[off:off + n]
            off += n
            if raw.endswith(b"\0"):
                raw = raw[:-1]
            names.append(raw.decode("latin1").lower())
            off += 4                       # four flag bytes per name
    except (IndexError, struct.error):
        return None
    return names


def collect(dump, out):
    lad_dir = os.path.join(dump, "System", "LIPSincData")
    files = []
    for root, _dirs, names in os.walk(lad_dir):
        for n in sorted(names):
            if n.lower().endswith(".lad"):
                files.append(os.path.join(root, n))
    files.sort()
    if not files:
        print("no .lad files under %s" % lad_dir, file=sys.stderr)
        return 2
    seen = {}                              # bone name -> list of .lad paths (relative to LIPSincData)
    for path in files:
        rel = os.path.relpath(path, lad_dir)
        for name in channel_names(path):
            seen.setdefault(name, []).append(rel)
    # one pass over the corpus, building name -> [owners]
    index = {}
    scanned = 0
    for root, _dirs, names in os.walk(dump):
        for n in sorted(names):
            if not n.lower().endswith(PACKAGE_EXT):
                continue
            names_in = package_names(os.path.join(root, n))
            if names_in is None:
                continue
            scanned += 1
            keep = set(names_in)
            for bone in seen:
                if bone.lower() in keep:
                    index.setdefault(bone, []).append(os.path.relpath(os.path.join(root, n), dump))
    rows = []
    for bone in sorted(seen):
        owners = sorted(index.get(bone, []))
        rows.append({
            "name": bone,
            "files": ";".join(seen[bone]),
            "file_count": len(seen[bone]),
            "packages": len(owners),
            "owners": ";".join(owners[:OWNERS]),
        })
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, CSV), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print("%d bone names from %d .lad files against %d package files -> %s"
          % (len(rows), len(files), scanned, os.path.join(out, CSV)))
    return 0


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def check(inventory=None):
    inv = inventory or os.path.join(ROOT, "inventory")
    rows = read_csv(os.path.join(inv, CSV))
    bones = read_csv(os.path.join(inv, BONES_CSV))
    bad = []
    expected = sorted({r["name"] for r in bones})
    got = sorted(r["name"] for r in rows)
    if got != expected:
        bad.append("the CSV's names disagree with %s: %d rows, %d distinct channels"
                   % (BONES_CSV, len(got), len(expected)))
    for r in rows:
        files = [f for f in r["files"].split(";") if f]
        if len(files) != int(r["file_count"]):
            bad.append("%s: file_count %s but %d files listed" % (r["name"], r["file_count"], len(files)))
        if int(r["packages"]) < len([o for o in r["owners"].split(";") if o]):
            bad.append("%s: more owners listed than packages counted" % r["name"])
        if int(r["packages"]) > 0 and not r["owners"]:
            bad.append("%s: %s packages but no owner named" % (r["name"], r["packages"]))
    for b in bad:
        print("FAIL " + b)
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dump", help="the game directory (the parent of System/)")
    ap.add_argument("-o", "--out", help="write %s here" % CSV)
    ap.add_argument("--check", action="store_true", help="dump-free validation of the committed CSV")
    ap.add_argument("--show", help="print the committed CSV (or the one under this directory)")
    args = ap.parse_args()
    if args.check:
        return check(args.out)
    if args.show:
        for r in read_csv(os.path.join(args.show, CSV)):
            print("%-16s files=%d packages=%2d %s"
                  % (r["name"], int(r["file_count"]), int(r["packages"]), r["owners"]))
        return 0
    if not args.dump or not args.out:
        ap.error("--dump and -o are required (or --check)")
    return collect(args.dump, args.out)


if __name__ == "__main__":
    sys.exit(main())
