#!/usr/bin/env python3
"""The map page's index -> name table, computed from the disc instead of read off frames (PLAN 7.2).

`GUIController.GetMapList` enumerates every `.unr` behind the `[Core.System] Paths` globs and sorts the result
(`0x000f8ab0`) before appending it to the page's array, so a map's index is its position in **ASCII order over the file
names** -- which is computable from the disc image. The measured anchors (docs/XEMU.md) are checked by `--verify`:
index 0 `CTF-Abydos`, 10 `M00`, 31 `M12F`, 35 `M13D`, 40 `M15B`, 72 `SGCTF-AbydosVsSGC`, 73 entries in all.

    python3 tools/xemu/map-index.py --iso $WORK/sg1.iso                # "0\tCTF-Abydos" ... one line per entry
    python3 tools/xemu/map-index.py --iso $WORK/sg1.iso --name M15C    # 41
    python3 tools/xemu/map-index.py --iso $WORK/sg1.iso --verify       # the anchors above, re-derived
    python3 tools/xemu/map-index.py --rotate 2                         # today's two indices for the nightly

The campaign range is **10-70** (61 maps). The page has 73 entries, but 0-9 are the multiplayer maps and `Entry` and
71-72 are `MainMenu` and `SGCTF-AbydosVsSGC`, which are not missions; `--verify` checks that every index in the range
looks like a campaign map (`M00`..`M21D`) and that the two after it do not. `--rotate N` walks the range with stride
`N` from a day-numbered start, so a nightly that loads N maps a night covers all 61 in ceil(61/N) nights -- with N=2
that is 31 nights, and because 2 is coprime with 61 no index is skipped by the wrap.

The walk is the XDVDFS directory tree (left child, node, right child), which is the order the disc stores names in
sorted form; the tool still sorts explicitly so a hand-built ISO cannot quietly reorder the table.
"""
import argparse
import os
import re
import struct
import sys
import time

SEC = 2048
MAGIC = b"MICROSOFT*XBOX*MEDIA"
ANCHORS = {0: "CTF-Abydos", 10: "M00", 31: "M12F", 35: "M13D", 40: "M15B", 72: "SGCTF-AbydosVsSGC"}
COUNT = 73
CAMPAIGN_LO, CAMPAIGN_HI = 10, 70          # first and last campaign entries on the page
CAMPAIGN = CAMPAIGN_HI - CAMPAIGN_LO + 1   # 61
CAMPAIGN_NAME = re.compile(r"^M[0-9]{2}[A-Za-z]?$")


class Disc:
    def __init__(self, path):
        self.f = open(path, "rb")
        self.f.seek(32 * SEC)
        vd = self.f.read(SEC)
        if vd[:20] != MAGIC:
            sys.exit("%s: not an Xbox disc image (volume descriptor %r)" % (path, vd[:20]))
        self.root_sector, self.root_size = struct.unpack("<II", vd[0x14:0x1C])

    def _read(self, sector, size):
        self.f.seek(sector * SEC)
        return self.f.read(size)

    def _entries(self, sector, size):
        """{offset_in_dir: (left, right, start, size, attrs, name)} for one directory."""
        blob = self._read(sector, size)
        out, off = {}, 0
        while off + 14 <= len(blob):
            left, right, start, fsize = struct.unpack("<HHII", blob[off:off + 12])
            attrs, nlen = blob[off + 12], blob[off + 13]
            if left == 0 and right == 0 and nlen == 0:
                break
            out[off] = (left * 4, right * 4, start, fsize, attrs,
                        blob[off + 14:off + 14 + nlen].decode("latin-1"))
            off = (off + 14 + nlen + 3) & ~3
        return out

    def files(self):
        """Every file path on the disc, in on-disc tree order."""
        acc = []

        def walk(sector, size, prefix, depth=0):
            if depth > 32:
                return
            ents = self._entries(sector, size)
            seen = set()

            def visit(off):
                if off in seen or off not in ents:
                    return
                seen.add(off)
                left, right, start, fsize, attrs, name = ents[off]
                visit(left)
                if attrs & 0x10:
                    walk(start, fsize, prefix + name + "/", depth + 1)
                else:
                    acc.append(prefix + name)
                visit(right)

            visit(0)

        walk(self.root_sector, self.root_size, "")
        return acc


def table(iso):
    """[(index, stem)] -- ASCII order over the names, extension stripped for display."""
    paths = [p for p in Disc(iso).files() if p.lower().endswith(".unr")]
    names = sorted(os.path.splitext(os.path.basename(p))[0] for p in paths)
    return list(enumerate(names))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--iso", default=os.environ.get("SG1_ISO"), help="disc image (the XISO, not the dump directory)")
    ap.add_argument("--name", help="print only this map's index")
    ap.add_argument("--verify", action="store_true", help="check the anchors measured from the running guest")
    ap.add_argument("--rotate", type=int, metavar="N",
                    help="print N campaign indices for one day of a rotating schedule")
    ap.add_argument("--day", type=int, help="day number for --rotate (default: day of year)")
    args = ap.parse_args()
    if args.rotate:
        if args.rotate < 1:
            sys.exit("--rotate needs a positive count")
        day = args.day if args.day is not None else int(time.strftime("%j"))
        start = (day * args.rotate) % CAMPAIGN
        print(" ".join(str(CAMPAIGN_LO + (start + k) % CAMPAIGN) for k in range(args.rotate)))
        return 0

    if not args.iso:
        sys.exit("give --iso, or set SG1_ISO")
    rows = table(args.iso)

    if args.verify:
        bad = []
        if len(rows) != COUNT:
            bad.append("entry count is %d, measured %d" % (len(rows), COUNT))
        for idx, name in sorted(ANCHORS.items()):
            got = rows[idx][1] if idx < len(rows) else None
            if got != name:
                bad.append("index %d is %r, measured %r" % (idx, got, name))
        for idx in range(CAMPAIGN_LO, CAMPAIGN_HI + 1):
            got = rows[idx][1] if idx < len(rows) else None
            if not got or not CAMPAIGN_NAME.match(got):
                bad.append("index %d is %r, not a campaign map" % (idx, got))
        for idx in range(CAMPAIGN_HI + 1, len(rows)):
            got = rows[idx][1]
            if CAMPAIGN_NAME.match(got):
                bad.append("index %d is %r, a campaign map outside the rotated range" % (idx, got))
        if bad:
            for line in bad:
                print("FAIL " + line)
            return 1
        print("ok   %d entries, %d anchors: %s" % (len(rows), len(ANCHORS),
                                                   ", ".join("%d=%s" % kv for kv in sorted(ANCHORS.items()))))
        return 0

    if args.name:
        want = os.path.splitext(args.name)[0].lower()
        hits = [i for i, n in rows if n.lower() == want]
        if not hits:
            sys.exit("%s: no map by that name in %s" % (args.name, args.iso))
        print(hits[0])
        return 0

    for i, name in rows:
        print("%d\t%s" % (i, name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
