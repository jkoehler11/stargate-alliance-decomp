#!/usr/bin/env python3
"""Find live objects by their *own* name over the whole `GObjObjects` array, one page per page.

The census names every object's *class*; this answers the other direction: which object of this guest is named
`GameHud`, and what class object does it point at. That is what a read of a class object needs, because a class
object's address is an allocation and moves between boots -- the `+0x24` pointer a previous boot reported is not
evidence for this one.

    python3 tools/xemu/findcls.py --port 12345 --xbe $SG1_DUMP/default.xbe --want GameHud --want UITextBox
    python3 tools/xemu/findcls.py --port 12345 --at 0x006be0d8 --grep GameHud,UITextBox --csv $WORK/objects.csv

Reads are per 64 KiB page that holds an object (the same choice `names.py --census` makes): per-object reads cost a
stub round-trip each, and this walks ~128k objects.

**One walk, every name.** The walk is the expensive half (a loaded `M00` is ~350 pages over the stub, minutes), so
`--grep` takes a comma list and `--csv` writes every named object the walk saw: a second question about a different
name is then a local grep, not a second walk.
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


def name_of(table, idx):
    """The text of one FName index, or None.

    names.Names answers through __getitem__ and has no text method, so the hasattr guard this tool shipped with was
    always false and every object was skipped: the walk read 48,622 headers over a loaded M00 and named none of
    them. Read the index through the interface the table actually has.
    """
    try:
        return table[idx]
    except (IOError, OSError, IndexError):
        return None


def needles_of(args):
    """Every substring to match: `--grep A,B --grep C` is three needles, lower-cased."""
    out = []
    for chunk in args.grep or []:
        out.extend(n.strip().lower() for n in chunk.split(",") if n.strip())
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--xbe", default=None)
    ap.add_argument("--at", type=lambda v: int(v, 0), default=None,
                    help="GNames array address, as tools/xemu/find_names.py measured it on this boot")
    ap.add_argument("--objects", type=lambda v: int(v, 0), default=names.OBJ_ARRAY, help="GObjObjects address")
    ap.add_argument("--want", action="append", default=[], metavar="NAME", help="object name to report")
    ap.add_argument("--grep", action="append", default=[], metavar="SUBSTR[,SUBSTR]",
                    help="substring match on the object name instead of --want; repeatable, and a value may be a "
                         "comma list, so one walk answers several name questions")
    ap.add_argument("--csv", default=None,
                    help="write index,address,class,name for every named object the walk saw, so a later question "
                         "about another name is a local grep instead of a second walk")
    ap.add_argument("--limit", type=int, default=40, help="rows per name")
    args = ap.parse_args()
    if args.at is None:
        ap.error("--at is required: pass the GNames address find_names.py measured on this boot")
    needles = needles_of(args)

    guest = names.Guest(args.host, args.port)
    try:
        data, num = names.name_table(guest, args)
        # FNameEntry records are packed into a handful of pages, so resolving ~49k objects
        # costs one page read per entry page -- but the walk's own page reads dominate.
        nm = names.Names(guest, data, num)
        odata, onum = names.object_array(guest, args)
        ptrs = [(i, p) for i, p in enumerate(struct.unpack("<%dI" % onum, guest.read(odata, onum * 4)))
                if names.plausible_object(p)]
        span = 0x10000
        pages = {}
        for i, p in ptrs:
            pages.setdefault(p & ~(span - 1), []).append((i, p))
        print("walking %d of %d object(s) over %d page(s)" % (len(ptrs), onum, len(pages)), flush=True)
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
        print("read %d header(s), %d unreadable" % (len(rows), missing), flush=True)
        seen = {}
        csv_rows = []
        kinds = {}

        def kind_of(klass):
            """A class object's own name, cached: hundreds of objects share one class pointer."""
            if klass not in kinds:
                kinds[klass] = name_of(nm, guest.u32(klass + names.NAME_OFF))
            return kinds[klass]

        for i, p, klass, idx in rows:
            oname = name_of(nm, idx)
            if not oname:
                continue
            hit = oname in args.want or (needles and any(n in oname.lower() for n in needles))
            if args.csv:
                csv_rows.append((i, p, kind_of(klass) or "", oname))
            if not hit:
                continue
            if seen.get(oname, 0) >= args.limit:
                continue
            seen[oname] = seen.get(oname, 0) + 1
            kind = kind_of(klass)
            print("  name `%s` at %#010x index %d -> class object %#010x%s"
                  % (oname, p, i, klass, " (%s)" % kind if kind else ""))
        if args.csv:
            with open(args.csv, "w") as fh:
                fh.write("index,address,class,name\n")
                for i, p, kind, oname in csv_rows:
                    fh.write("%d,%#010x,%s,%s\n" % (i, p, kind, oname.replace(",", " ")))
            print("wrote %d named object(s) to %s" % (len(csv_rows), args.csv))
        for w in args.want:
            if w not in seen:
                print("  no object named `%s`" % w)
    finally:
        guest.close()


if __name__ == "__main__":
    sys.exit(main())
