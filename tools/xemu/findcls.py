#!/usr/bin/env python3
"""Find live objects by their *own* name over the whole `GObjObjects` array, one page per page.

The census names every object's *class*; this answers the other direction: which object of this guest is named
`GameHud`, and what class object does it point at. That is what a read of a class object needs, because a class
object's address is an allocation and moves between boots -- the `+0x24` pointer a previous boot reported is not
evidence for this one.

    python3 tools/xemu/findcls.py --port 12345 --xbe $SG1_DUMP/default.xbe --want GameHud --want UITextBox

Reads are per 64 KiB page that holds an object (the same choice `names.py --census` makes): per-object reads cost a
stub round trip each, and this walks ~128k objects.
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("SG1_CLONE", os.path.expanduser("~/sg1/agent/sg1clone"))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "tools", "xemu"))
import names  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--xbe", default=None)
    ap.add_argument("--at", type=lambda v: int(v, 0), default=0x006BE0D8, help="GNames array address")
    ap.add_argument("--objects", type=lambda v: int(v, 0), default=names.OBJ_ARRAY, help="GObjObjects address")
    ap.add_argument("--want", action="append", default=[], metavar="NAME", help="object name to report")
    ap.add_argument("--grep", default=None, help="substring match on the object name instead of --want")
    ap.add_argument("--limit", type=int, default=40, help="rows per name")
    args = ap.parse_args()

    guest = names.Guest(args.host, args.port)
    try:
        data, num = names.name_table(guest, args)
        nm = names.Names(guest, data, num)
        odata, onum = names.object_array(guest, args)
        ptrs = [(i, p) for i, p in enumerate(struct.unpack("<%dI" % onum, guest.read(odata, onum * 4)))
                if names.plausible_object(p)]
        span = 0x10000
        pages = {}
        for i, p in ptrs:
            pages.setdefault(p & ~(span - 1), []).append((i, p))
        print("walking %d of %d object(s) over %d page(s)" % (len(ptrs), onum, len(pages)))
        rows = []
        missing = 0
        for page in sorted(pages):
            buf = None
            for _ in range(3):
                try:
                    buf = guest.read(page, span)
                    break
                except IOError:
                    continue
            if buf is None:
                missing += len(pages[page])
                continue
            for i, p in pages[page]:
                off = p - page
                if off < 0 or off + names.NAME_OFF + 4 > len(buf):
                    missing += 1
                    continue
                rows.append((i, p,
                             struct.unpack_from("<I", buf, off + names.CLASS_OFF)[0],
                             struct.unpack_from("<I", buf, off + names.NAME_OFF)[0]))
        print("read %d header(s), %d unreadable" % (len(rows), missing))
        seen = {}
        for i, p, klass, idx in rows:
            oname = nm.text(idx) if hasattr(nm, "text") else None
            if not oname:
                continue
            hit = oname in args.want or (args.grep and args.grep.lower() in oname.lower())
            if not hit:
                continue
            if seen.get(oname, 0) >= args.limit:
                continue
            seen[oname] = seen.get(oname, 0) + 1
            try:
                kind = nm.text(struct.unpack("<I", guest.read(klass + names.NAME_OFF, 4))[0])
            except (IOError, OSError):
                kind = None
            print("  name `%s` at %#010x index %d -> class object %#010x%s"
                  % (oname, p, i, klass, " (%s)" % kind if kind else ""))
        for w in args.want:
            if w not in seen:
                print("  no object named `%s`" % w)
    finally:
        guest.close()


if __name__ == "__main__":
    sys.exit(main())
