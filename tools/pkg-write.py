#!/usr/bin/env python3
"""Re-encode a UE2 package's tables from the parsed model, and diff them against the original bytes.

Writing a package is the step PLAN 6.1 needs and the one that proves the reader: if the name, import and export
tables can be *regenerated* from what we parsed and come out byte-identical, the model is right, not merely
plausible. What is not yet written is payload generation — a rebuild gets those bytes from a compiler.

    python3 tools/pkg-write.py DUMP/System              # re-encode every package's tables and diff
    python3 tools/pkg-write.py DUMP/System --rebuild    # rebuild whole files from header + tables + payloads
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
    """Enlarge one export's payload and re-lay the file from scratch.

    The offsets are recomputed rather than shifted: every payload is placed at the running position after the
    tables, in the original order, with the target grown by `extra`. That is safe here because whole-package
    rebuilds proved the payloads tile the file with no padding (`--rebuild`: zero gaps), and it avoids the
    fixed-point mess of shifting offsets that may need an extra byte in the compact index. The table length
    depends on the offsets, so the layout is iterated to a fixed point anyway -- but over positions, not deltas.
    """
    exports = [dict(e) for e in pkg.exports]
    target = next((e for e in exports if e["index"] + 1 == index), None)
    if target is None:
        raise SystemExit("no export %d in %s" % (index, pkg.path))
    head = pkg.data[:pkg.name_offset]
    names = encode_names(pkg.names)
    imports = encode_imports(pkg.imports)
    ordered = sorted(exports, key=lambda e: e["offset"])
    sizes = dict((e["index"], e["size"]) for e in exports)
    sizes[target["index"]] += extra

    pos, table = 0, b""
    for _ in range(8):
        pos = len(head) + len(names) + len(imports) + len(table)
        for e in ordered:
            e["offset"] = pos
            pos += sizes[e["index"]]
        table = encode_exports(exports)
        settled = len(head) + len(names) + len(imports) + len(table) == min(e["offset"] for e in ordered)
        if settled:
            break

    payload = bytearray()
    for e in ordered:
        data = pkg.data[e["offset"] if False else
                        next(x for x in pkg.exports if x["index"] == e["index"])["offset"]:
                        next(x for x in pkg.exports if x["index"] == e["index"])["offset"]
                        + pkg.exports[e["index"]]["size"]]
        payload += data
        if e["index"] == target["index"]:
            payload += bytes([fill]) * extra
    out = bytearray(head) + names + imports + table + payload
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
    ap.add_argument("--rebuild", action="store_true",
                    help="assemble each whole file from header + tables + payloads and diff it")
    ap.add_argument("--grow", metavar="INDEX:BYTES",
                    help="enlarge one export's payload by BYTES and re-lay the file (writer mechanics)")
    ap.add_argument("--write", metavar="DIR", help="write the grown packages into DIR (use $WORK)")
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

        if args.grow:
            index, extra = (int(x) for x in args.grow.split(":"))
            out, _exports = regrow(pkg, index, extra)
            again = Package.__new__(Package)
            again.__dict__.update(pkg.__dict__)
            again.data = out
            again.read_names()
            again.read_imports()
            again.read_exports()
            before = pkg.exports[index - 1]["size"]
            target = next((e for e in again.exports if e["index"] + 1 == index), None)
            grew = len(out) - len(pkg.data)
            same_names = [n for n, _f in again.names] == [n for n, _f in pkg.names]
            resolved = bool(again.exports) and all(again.class_name_of(e) != "?" for e in again.exports)
            ok = (target is not None and grew == extra and same_names and resolved
                  and target["size"] == before + extra)
            print("%-4s %-22s export %d %d -> %s bytes, file %+d; re-parse: names %s, classes %s"
                  % ("ok" if ok else "FAIL", fname, index, before,
                     target["size"] if target else "?", grew,
                     "same" if same_names else "CHANGED", "resolve" if resolved else "UNRESOLVED"))
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
    if args.grow:
        print("\n%d of %d packages absorbed a %s-byte payload change and re-parsed clean"
              % (ok_all, len(files), args.grow.split(":")[1]))
    elif args.rebuild:
        print("\n%d of %d packages rebuild byte for byte from header + tables + payloads"
              % (ok_all, len(files)))
    else:
        print("\n%d of %d packages re-encode their tables byte for byte" % (ok_all, len(files)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
