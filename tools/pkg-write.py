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
import csv
import os
import shutil
import struct
import sys
import tempfile

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


def region_order(pkg):
    """The order the regions occupy on disk, read from the header's own offsets.

    `--layout` found `names < payloads < imports < exports` on all 18 shipped packages, but that is a measurement,
    not a guarantee: the order is derived here so a package that lays its tables out differently still writes.
    """
    payload_start = min(e["offset"] for e in pkg.exports) if pkg.exports else pkg.import_offset
    return [name for _off, name in sorted([(pkg.name_offset, "names"), (pkg.import_offset, "imports"),
                                           (pkg.export_offset, "exports"), (payload_start, "payloads")])]


def relayout(pkg, export_index, delta, fill=0xCC):
    """Re-lay the whole file with one export's payload resized by `delta` bytes; negative shrinks.

    Every region is placed again in `region_order`, at a position computed from the lengths of the regions before
    it -- never by shifting an existing offset, which is what made the first attempt at this fail. Payload positions
    feed the export table and the export table's own length can change with them (a compact index byte, or a byte
    more per offset once one crosses 0x3FFF), so a table that sits before the payloads makes the layout circular:
    the loop iterates until the offsets stop moving. That fixed point is the whole difficulty of the mode.

    Returns (bytes, info). The resized payload's *contents* are not meaningful (fill bytes appended, or a truncated
    tail on a shrink) -- this proves the container, not the export.
    """
    exports = [dict(e) for e in pkg.exports]
    target = next((e for e in exports if e["index"] == export_index), None)
    if target is None:
        raise SystemExit("no export %d in %s" % (export_index, pkg.path))
    size_before = target["size"]
    new_size = size_before + delta
    if new_size < 0:
        raise SystemExit("export %d of %s is %d bytes; cannot shrink by %d"
                         % (export_index, pkg.path, target["size"], -delta))
    head = pkg.data[:pkg.name_offset]
    names = encode_names(pkg.names)
    imports = encode_imports(pkg.imports)
    order = sorted(pkg.exports, key=lambda e: e["offset"])
    blobs = {}
    for e in order:
        blob = pkg.data[e["offset"]:e["offset"] + e["size"]]
        if e["index"] == export_index:
            blob = blob + bytes([fill]) * delta if delta >= 0 else blob[:new_size]
        blobs[e["index"]] = blob

    layout = region_order(pkg)
    offsets = dict((e["index"], e["offset"]) for e in exports)
    total, starts = 0, {}
    for _round in range(64):
        for e in exports:
            e["size"] = len(blobs[e["index"]])
            e["offset"] = offsets[e["index"]]
        table = encode_exports(exports)
        pos, moved, starts = len(head), {}, {}
        for region in layout:
            starts[region] = pos
            if region == "payloads":
                for e in order:
                    moved[e["index"]] = pos
                    pos += len(blobs[e["index"]])
            elif region == "names":
                pos += len(names)
            elif region == "imports":
                pos += len(imports)
            else:
                pos += len(table)
        total = pos
        if moved == offsets:
            break
        offsets = moved
    else:
        raise SystemExit("%s: layout did not settle after 64 rounds" % pkg.path)

    out = bytearray(head)
    for region in layout:
        if region == "names":
            out += names
        elif region == "imports":
            out += imports
        elif region == "exports":
            out += table
        else:
            for e in order:
                out += blobs[e["index"]]
    if len(out) != total:
        raise SystemExit("%s: assembled %d bytes but the layout computed %d" % (pkg.path, len(out), total))
    # The header carries the table offsets, so moving a region means rewriting them: name_offset (0x10),
    # export_offset (0x18), import_offset (0x20). This is the step the first attempt at this mode missed --
    # it relocated the data and left the header pointing at the old layout.
    if len(out) < 0x24:
        raise SystemExit("%s: header is too short to carry the table offsets" % pkg.path)
    struct.pack_into("<I", out, 0x10, starts.get("names", pkg.name_offset))
    struct.pack_into("<I", out, 0x18, starts["exports"])
    struct.pack_into("<I", out, 0x20, starts["imports"])
    return bytes(out), {"index": export_index, "size_before": size_before, "size_after": new_size,
                        "file_before": len(pkg.data), "file_after": total}


def sample_exports(pkg, count=5, delta=0):
    """Exports worth resizing: the extremes, and the ones sitting on a compact-index boundary.

    A size that crosses 0x3F changes the export entry's width, and an offset that crosses 0x3FFF changes it again --
    those are exactly the cases a naive shift gets wrong, so a sample that misses them proves less than it looks.
    A shrink can only be offered an export that is big enough to lose the bytes.
    """
    ex = [e for e in pkg.exports if e["size"] + delta >= 0]
    if not ex:
        return []
    picks = []
    for cand in (min(ex, key=lambda e: e["size"]), max(ex, key=lambda e: e["size"]),
                 min(ex, key=lambda e: abs(e["size"] - 0x3F)), min(ex, key=lambda e: abs(e["offset"] - 0x3FFF)),
                 ex[-1]):
        if all(cand["index"] != p["index"] for p in picks):
            picks.append(cand)
    return picks[:count]


def _reread(path):
    pkg = Package(path)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    return pkg


def check_resize(pkg, export_index, delta, tmpdir):
    """Re-lay, write, read the result back through the reader, and require the resize to be reversible.

    Growing is reversible in the strict sense: grow a payload, shrink it by the same amount, and the file comes back
    byte-identical. A *shrink* is not -- it discards the payload's tail, and no writer can invent those bytes back --
    so the negative case is held to the container instead: same tables, same lengths, same other payloads, and the
    target's own payload shorter by exactly `delta`. Table diffing cannot see a layout mistake; these can.
    """
    out, info = relayout(pkg, export_index, delta)
    path = os.path.join(tmpdir, "resized.u")
    with open(path, "wb") as fh:
        fh.write(out)
    again = _reread(path)
    problems = []
    if [n for n, _f in again.names] != [n for n, _f in pkg.names]:
        problems.append("name table changed")
    if len(again.imports) != len(pkg.imports):
        problems.append("import count %d -> %d" % (len(pkg.imports), len(again.imports)))
    if len(again.exports) != len(pkg.exports):
        problems.append("export count %d -> %d" % (len(pkg.exports), len(again.exports)))
    else:
        for before, after in zip(pkg.exports, again.exports):
            if (before["class"], before["super"], before["outer"], before["name"], before["flags"]) != \
               (after["class"], after["super"], after["outer"], after["name"], after["flags"]):
                problems.append("export %d's entry changed beyond size/offset" % before["index"])
                break
        else:
            moved = again.exports[export_index]
            if moved["size"] != info["size_after"]:
                problems.append("export %d reads back as %d bytes, wanted %d"
                                % (export_index, moved["size"], info["size_after"]))
    if any(again.class_name_of(e) == "?" for e in again.exports):
        problems.append("a class no longer resolves")
    for e in pkg.exports:
        if e["index"] == export_index:
            continue
        now = again.exports[e["index"]]
        if again.data[now["offset"]:now["offset"] + now["size"]] != pkg.data[e["offset"]:e["offset"] + e["size"]]:
            problems.append("payload %d did not survive the move" % e["index"])
            break
    back, _back_info = relayout(again, export_index, -delta)
    back_path = os.path.join(tmpdir, "roundtrip.u")
    with open(back_path, "wb") as fh:
        fh.write(back)
    if delta >= 0:
        if back != pkg.data:
            at = next((i for i in range(min(len(back), len(pkg.data))) if back[i] != pkg.data[i]),
                      min(len(back), len(pkg.data)))
            problems.append("grow-then-shrink is not byte-identical: first difference at +0x%x" % at)
    else:
        restored = _reread(back_path)
        if len(back) != len(pkg.data):
            problems.append("shrink-then-grow gave %d bytes, original %d" % (len(back), len(pkg.data)))
        elif encode_exports(restored.exports) != encode_exports(pkg.exports):
            problems.append("shrink-then-grow did not restore the export table")
        elif restored.exports[export_index]["size"] != info["size_before"]:
            problems.append("shrink-then-grow restored %d bytes, wanted %d"
                            % (restored.exports[export_index]["size"], info["size_before"]))
    return again, info, problems


def compare(label, want, got):
    if want == got:
        return True, "%s ok (%d bytes)" % (label, len(want))
    n = min(len(want), len(got))
    at = next((i for i in range(n) if want[i] != got[i]), n)
    return False, "%s DIFF at +0x%x (wrote %d bytes, original %d): original %s vs model %s" % (
        label, at, len(got), len(want),
        want[at:at + 8].hex(" ") or "-", got[at:at + 8].hex(" ") or "-")


def relay_report(system_dir, out_dir):
    """Re-lay each package through the writer's placement path with nothing resized; the bytes must not change.

    This is the same `relayout` that `--resize` uses, called with a zero delta, and keeping it that one function is
    deliberate: a placement rule that drifts between the "prove it" mode and the "move it" mode would make the proof
    describe code that no longer runs.
    """
    files = sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u"))
    ok_all, bad = 0, []
    for fname in files:
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        if not pkg.exports:
            print("skip %-22s no exports to place" % fname)
            continue
        out, _info = relayout(pkg, pkg.exports[0]["index"], 0)
        identical = out == pkg.data
        at = next((i for i in range(min(len(out), len(pkg.data))) if out[i] != pkg.data[i]),
                  min(len(out), len(pkg.data)))
        print("%-4s %-22s file %+d bytes, %d exports, %s"
              % ("ok" if identical else "FAIL", fname, len(out) - len(pkg.data), len(pkg.exports),
                 "identical to the original" if identical else "differs at +0x%x" % at))
        if out_dir and identical:
            with open(os.path.join(out_dir, fname), "wb") as fh:
                fh.write(out)
        if identical:
            ok_all += 1
        else:
            bad.append(fname)
    for fname in bad:
        print("FAIL %s" % fname)
    print("\n%d of %d packages re-lay through the writer and come out identical" % (ok_all, len(files)))
    return 0 if not bad else 1


def resize_report(system_dir, delta, export_index, out_dir):
    """Resize a payload in every package and require the file to come back through the reader and round-trip."""
    files = sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u"))
    rows, ok_all, tested = [], 0, 0
    tmpdir = tempfile.mkdtemp(prefix="pkg-resize-")
    try:
        for fname in files:
            pkg = Package(os.path.join(system_dir, fname))
            pkg.read_names()
            pkg.read_imports()
            pkg.read_exports()
            picks = [export_index] if export_index is not None else \
                [e["index"] for e in sample_exports(pkg, delta=delta)]
            if not picks:
                print("skip %-20s no export can absorb %+d bytes" % (fname, delta))
                rows.append([fname[:-2], 0, delta, 0, "n/a", "skip"])
                ok_all += 1
                continue
            passed, problems, last = 0, [], None
            for idx in picks:
                tested += 1
                last, _info, bad = check_resize(pkg, idx, delta, tmpdir)
                if bad:
                    problems.append("export %d: %s" % (idx, "; ".join(bad)))
                else:
                    passed += 1
            rebuilt = "no"
            if last is not None and not problems:
                whole, gaps, _covered = rebuild(last)
                rebuilt = "yes" if whole == last.data and not gaps else "no"
                if rebuilt != "yes":
                    problems.append("the writer's own output does not rebuild from its tables")
            status = "ok" if passed == len(picks) and not problems else "FAIL"
            ok_all += status == "ok"
            if out_dir and status == "ok" and last is not None:
                with open(os.path.join(out_dir, fname), "wb") as fh:
                    fh.write(last.data)
            rows.append([fname[:-2], len(picks), delta, passed, rebuilt, status])
            print("%-4s %-20s %d/%d exports %+d: re-parses, %s%s"
                  % (status, fname, passed, len(picks), delta,
                     "grow-then-shrink is byte-identical" if delta >= 0
                     else "tables and payloads restore on re-grow",
                     "" if not problems else " -- " + problems[0]))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    if out_dir:
        with open(os.path.join(out_dir, "package_relayout.csv"), "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["package", "exports_tested", "delta", "passed", "writer_output_rebuilds", "status"])
            w.writerows(rows)
        print("wrote %s/package_relayout.csv (%d rows)" % (out_dir, len(rows)))
    print("\n%d of %d packages absorbed a %+d-byte payload change and re-parsed clean (%d resizes)"
          % (ok_all, len(files), delta, tested))
    return 0 if ok_all == len(files) else 1


def synthesize(path, sizes):
    """Write a small package of our own making, so the layout solver can be tested with no dump present.

    Nothing here comes from the game: four invented names, one import, one export per size. It goes through the same
    encoders and the same region order the real writer uses and is read back by the same reader, so a codec or
    layout disagreement shows up here without a package from the dump.
    """
    names = [("Alpha", 0), ("Beta", 0), ("Gamma", 0), ("Delta", 0)]
    imports = [{"class_package": 0, "class": 0, "package": 0, "name": 1}]
    enc_names, enc_imports = encode_names(names), encode_imports(imports)
    head_len = 4 + 2 + 2 + 4 * 7 + 32          # the reader's fields, then 32 bytes it does not read
    payload_start = head_len + len(enc_names)
    exports, offset, blobs = [], payload_start, b""
    for i, size in enumerate(sizes):
        exports.append({"index": i, "class": -1, "super": 0, "outer": 0, "name": i,
                        "flags": 0, "size": size, "offset": offset})
        blobs += bytes([0x40 + i]) * size
        offset += size
    import_offset = payload_start + len(blobs)
    export_offset = import_offset + len(enc_imports)
    head = struct.pack("<IHHIIIIIII", TAG, 130, 4, 0, len(names), head_len,
                       len(exports), export_offset, len(imports), import_offset) + b"\0" * 32
    data = head + enc_names + blobs + enc_imports + encode_exports(exports)
    with open(path, "wb") as fh:
        fh.write(data)
    return data


def selftest():
    """The index codec, then the layout solver over synthetic packages -- all of it runs with no dump."""
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

    tmpdir = tempfile.mkdtemp(prefix="pkg-selftest-")
    try:
        # (sizes, export, delta, what the case is for)
        cases = [([63, 10, 200], 0, 1, "a size crossing 0x3F widens the export entry"),
                 ([63, 10, 200], 0, -63, "a shrink back to nothing"),
                 ([100, 5, 7], 1, 3, "a middle payload moving the ones behind it"),
                 ([10, 10, 10], 2, 20000, "offsets crossing 0x3FFF widen every entry behind it"),
                 ([1, 1, 1], 0, 0, "a no-op still has to reproduce the file")]
        for n, (sizes, index, delta, why) in enumerate(cases):
            path = os.path.join(tmpdir, "synthetic%d.u" % n)
            synthesize(path, sizes)
            pkg = Package(path)
            pkg.read_names()
            pkg.read_imports()
            pkg.read_exports()
            if len(pkg.exports) != len(sizes):
                print("FAIL synthetic package reads back %d exports, wrote %d" % (len(pkg.exports), len(sizes)))
                return 1
            before = len(encode_exports(pkg.exports))
            again, info, problems = check_resize(pkg, index, delta, tmpdir)
            if problems:
                print("FAIL synthetic %s: %s" % (why, "; ".join(problems)))
                return 1
            widened = len(encode_exports(again.exports)) != before
            if n == 3 and not widened:
                print("FAIL synthetic: the 20000-byte case was supposed to widen the export table and did not")
                return 1
            print("ok   synthetic relayout export %d %+d (%s): %d -> %d bytes, file %d -> %d%s"
                  % (index, delta, why, info["size_before"], info["size_after"],
                     info["file_before"], info["file_after"],
                     ", export table widened" if widened else ""))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("system_dir", nargs="?", help="a directory of .u packages")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--layout", action="store_true",
                    help="report where each region sits and in what order (a writer must follow this, not assume)")
    ap.add_argument("--rebuild", action="store_true",
                    help="assemble each whole file from header + tables + payloads and diff it")
    ap.add_argument("--relay", action="store_true",
                    help="re-lay every package through the writer's placement path and require byte identity")
    ap.add_argument("--resize", metavar="BYTES", type=int,
                    help="re-lay each package with one payload resized by BYTES (negative shrinks); five exports "
                         "are sampled per package unless --export names one")
    ap.add_argument("--export", metavar="INDEX", type=int,
                    help="0-based export index to resize (default: the sampled set)")
    ap.add_argument("--write", metavar="DIR",
                    help="write the resized packages and package_relayout.csv into DIR (use $WORK)")
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
                w = csv.writer(fh, lineterminator="\n")
                w.writerow(["package", "name_start", "name_end", "import_start", "import_end",
                            "export_start", "export_end", "payload_start", "payload_end", "file_size", "order"])
                w.writerows(rows)
            print("\nwrote %s/package_layout.csv (%d rows)" % (args.write, len(rows)))
        return 0

    if args.relay:
        return relay_report(args.system_dir, args.write)
    if args.resize is not None:
        return resize_report(args.system_dir, args.resize, args.export, args.write)

    files = sorted(f for f in os.listdir(args.system_dir) if f.lower().endswith(".u"))
    ok_all, bad = 0, []
    for fname in files:
        pkg = Package(os.path.join(args.system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        problems = []

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
    if args.rebuild:
        print("\n%d of %d packages rebuild byte for byte from header + tables + payloads"
              % (ok_all, len(files)))
    else:
        print("\n%d of %d packages re-encode their tables byte for byte" % (ok_all, len(files)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
