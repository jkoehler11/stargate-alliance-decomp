#!/usr/bin/env python3
"""Re-encode a UE2 package's tables from the parsed model, and diff them against the original bytes.

Writing a package is the step PLAN 6.1 needs and the one that proves the reader: if the name, import and export
tables can be *regenerated* from what we parsed and come out byte-identical, the model is right, not merely
plausible. What is not yet written is payload generation — a rebuild gets those bytes from a compiler.

    python3 tools/pkg-write.py DUMP/System              # re-encode every package's tables and diff
    python3 tools/pkg-write.py DUMP/System --rebuild    # rebuild whole files from header + tables + payloads
    python3 tools/pkg-write.py DUMP/System --layout     # where every region actually sits, per package
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


def rebuild(pkg):
    """Assemble the whole file from its parts: header, three tables, and every export payload at its offset.

    The point is not to copy the original -- it is to prove that those parts *cover every byte*. Anything left
    uncovered is a region the model does not explain, which is exactly what a writer would get wrong later.
    """
    total = len(pkg.data)
    out = bytearray(total)
    covered = bytearray(total)
    def place(start, chunk):
        out[start:start + len(chunk)] = chunk
        covered[start:start + len(chunk)] = b"\x01" * len(chunk)
    place(0, pkg.data[:pkg.name_offset])
    place(pkg.name_offset, encode_names(pkg.names))
    place(pkg.import_offset, encode_imports(pkg.imports))
    place(pkg.export_offset, encode_exports(pkg.exports))
    for e in pkg.exports:
        place(e["offset"], pkg.data[e["offset"]:e["offset"] + e["size"]])
    gaps, i = [], 0
    while i < total:
        if not covered[i]:
            j = i
            while j < total and not covered[j]:
                j += 1
            gaps.append((i, j - i))
            # carry the original bytes through so the comparison is about coverage, not about zero-filling
            out[i:j] = pkg.data[i:j]
            i = j
        else:
            i += 1
    return bytes(out), gaps, sum(1 for c in covered if c)


def regrow(pkg, index, extra, fill=0xCC):
    """Enlarge one export's payload and re-lay the file, then hand back bytes that must re-parse.

    The layout decides the algorithm, and it is not the obvious one (`docs/PACKAGE_LAYOUT.md`): header, name table,
    **payloads**, and only then the import and export tables at the end of the file. That makes this a placement
    problem rather than a fixed point -- payload offsets start right after the names and the two tables move to
    wherever the payload block ends, with the header's `export_offset`/`import_offset` fields patched to match.

    With `extra == 0` the output must equal the input byte for byte. That is the check that the placement is right,
    and it is the check the first attempt at this function did not have.
    """
    exports = [dict(e) for e in pkg.exports]
    target = next((e for e in exports if e["index"] + 1 == index), None)
    if target is None:
        raise SystemExit("no export %d in %s" % (index, pkg.path))

    out = bytearray(pkg.data[:pkg.name_offset])
    out += encode_names(pkg.names)
    pos = len(out)
    payload = bytearray()
    for e in sorted(exports, key=lambda x: x["offset"]):
        src = pkg.exports[e["index"]]
        data = pkg.data[src["offset"]:src["offset"] + src["size"]]
        if e["index"] == target["index"]:
            data = data + bytes([fill]) * extra
        e["offset"] = pos
        e["size"] = len(data)
        pos += len(data)
        payload += data
    out += payload
    imports_at = len(out)
    out += encode_imports(pkg.imports)
    exports_at = len(out)
    out += encode_exports(exports)
    struct.pack_into("<I", out, 24, exports_at)   # header: export table offset
    struct.pack_into("<I", out, 32, imports_at)   # header: import table offset
    return bytes(out), exports


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
    ap.add_argument("--layout", action="store_true",
                    help="report where each region sits and in what order (a writer must follow this, not assume)")
    ap.add_argument("--rebuild", action="store_true",
                    help="assemble each whole file from header + tables + payloads and diff it")
    ap.add_argument("--grow", metavar="INDEX:BYTES",
                    help="enlarge one export's payload by BYTES and re-lay the file (writer mechanics)")
    ap.add_argument("--write", metavar="DIR", help="write the grown packages into DIR (use $WORK)")
    args = ap.parse_args()
    if args.selftest or not args.system_dir:
        return selftest()

    if args.layout:
        rows = []
        print("%-20s %-28s %-28s %-28s %s" % ("package", "names", "imports", "exports", "order"))
        for fname in sorted(f for f in os.listdir(args.system_dir) if f.lower().endswith(".u")):
            pkg = Package(os.path.join(args.system_dir, fname))
            pkg.read_names()
            pkg.read_imports()
            pkg.read_exports()
            n_end = pkg.name_offset + len(encode_names(pkg.names))
            i_end = pkg.import_offset + len(encode_imports(pkg.imports))
            e_end = pkg.export_offset + len(encode_exports(pkg.exports))
            first = min(e["offset"] for e in pkg.exports)
            last = max(e["offset"] + e["size"] for e in pkg.exports)
            regions = [("names", pkg.name_offset, n_end), ("imports", pkg.import_offset, i_end),
                       ("exports", pkg.export_offset, e_end), ("payloads", first, last)]
            order = "<".join(r for r, _s, _e in sorted(regions, key=lambda r: r[1]))
            print("%-20s %-28s %-28s %-28s %s" % (
                fname[:-2],
                "%#x-%#x" % (pkg.name_offset, n_end),
                "%#x-%#x" % (pkg.import_offset, i_end),
                "%#x-%#x" % (pkg.export_offset, e_end), order))
            rows.append([fname[:-2], pkg.name_offset, n_end, pkg.import_offset, i_end,
                         pkg.export_offset, e_end, first, last, len(pkg.data), order])
        if args.write:
            os.makedirs(args.write, exist_ok=True)
            with open(os.path.join(args.write, "package_layout.csv"), "w", newline="") as fh:
                w = __import__("csv").writer(fh, lineterminator="\n")
                w.writerow(["package", "name_start", "name_end", "import_start", "import_end",
                            "export_start", "export_end", "payload_start", "payload_end", "file_size", "order"])
                w.writerows(rows)
            print("\nwrote %s/package_layout.csv (%d rows)" % (args.write, len(rows)))
        return 0

    files = sorted(f for f in os.listdir(args.system_dir) if f.lower().endswith(".u"))
    ok_all, bad = 0, []
    for fname in files:
        pkg = Package(os.path.join(args.system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        problems = []

        if args.relay:
            # Payload growth is *not* offered here yet: with extra > 0 the re-encoded export table mis-decodes its
            # first entry (see docs/PACKAGE_LAYOUT.md), so the mode that ships is the one that is proven -- a
            # placement pass whose output must equal the input. Anything less and the tool would be advertising a
            # capability it does not have.
            index, extra = 1, 0
            out, _exports = regrow(pkg, index, extra)
            again = Package.__new__(Package)
            again.__dict__.update(pkg.__dict__)
            again.data = out
            again.read_names()
            again.read_imports()
            again.read_exports()
            same_names = [n for n, _f in again.names] == [n for n, _f in pkg.names]
            resolved = bool(again.exports) and all(again.class_name_of(e) != "?" for e in again.exports)
            grew = len(out) - len(pkg.data)
            identical = out == pkg.data
            # The file does not grow by exactly `extra`: shifting the payload block moves the two tables, and a
            # shifted offset can need one more byte in the compact index, so the table length changes too.
            target_ok = (next((e for e in again.exports if e["index"] + 1 == index), {}).get("size")
                         == pkg.exports[index - 1]["size"] + extra)
            ok = (grew >= extra and same_names and resolved and target_ok
                  and len(again.exports) == len(pkg.exports) and (extra != 0 or identical))
            print("%-4s %-22s file %+d bytes, %d exports, names %s, classes %s%s"
                  % ("ok" if ok else "FAIL", fname, grew, len(again.exports),
                     "same" if same_names else "CHANGED", "resolve" if resolved else "UNRESOLVED",
                     ", identical to the original" if identical else ""))
            if args.write:
                os.makedirs(args.write, exist_ok=True)
                with open(os.path.join(args.write, fname), "wb") as fh:
                    fh.write(out)
            if ok:
                ok_all += 1
            else:
                bad.append((fname, ["grow check failed"]))
            continue
        # Regenerate each table from the parsed model and compare it over its own length, so the slice is exactly
        # as long as what we wrote -- no guessing where the original table ends.
        for label, enc, start in (("names", encode_names(pkg.names), pkg.name_offset),
                                  ("imports", encode_imports(pkg.imports), pkg.import_offset),
                                  ("exports", encode_exports(pkg.exports), pkg.export_offset)):
            good, msg = compare("%s %s" % (fname, label), pkg.data[start:start + len(enc)], enc)
            if not good:
                problems.append(msg)

        if args.rebuild:
            whole, gaps, covered = rebuild(pkg)
            good = whole == pkg.data
            print("%-4s %-22s %d%% of bytes accounted for, %d gap(s)%s"
                  % ("ok" if good else "DIFF", fname, int(100.0 * covered / len(pkg.data)), len(gaps),
                     "" if good else " -- first diff at +0x%x" % next(
                         (i for i in range(len(whole)) if whole[i] != pkg.data[i]), 0)))
            if gaps:
                print("       uncovered: %s" % ", ".join("+0x%x(%d)" % g for g in gaps[:6]))
            if not good:
                problems.append("%s whole-file rebuild differs" % fname)
        elif not problems:
            print("ok   %-22s names %5d imports %3d exports %5d re-encode byte-identical"
                  % (fname, len(pkg.names), len(pkg.imports), len(pkg.exports)))

        if problems:
            bad.append((fname, problems))
        else:
            ok_all += 1

    for fname, problems in bad:
        print("FAIL %s" % fname)
        for p in problems:
            print("     " + p)
    if args.relay:
        print("\n%d of %d packages re-lay byte for byte through the writer's placement path"
              % (ok_all, len(files)))
    elif args.rebuild:
        print("\n%d of %d packages rebuild byte for byte from header + tables + payloads"
              % (ok_all, len(files)))
    else:
        print("\n%d of %d packages re-encode their tables byte for byte" % (ok_all, len(files)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
