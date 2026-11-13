#!/usr/bin/env python3
"""Where the class record's middle is written, measured from the writer's own order rather than from its field table.

The record between the prefix and the defaults block has been parsed in several orders and no reading closed it. The
order is not a guess: `UClass::Serialize` (`0x0003a800`) is straight-line code and its calls come in one sequence,
which its listing and decompiled form agree on:

    FUN_000394c0(archive)                     ; UState::Serialize
    FUN_000370e0(archive, +0x498)             ; one dword, `Serialize(ptr,4)` on the archive proxy
    FUN_000370e0(archive, +0x4a0 .. +0x4ac)   ; four dwords -> 20 bytes
    FUN_0003d900(archive, +0x4d0)             ; count, then count x (compact reference, dword, dword)
    FUN_0003d160(archive, +0x4dc)             ; TArray<FName>: a compact count, one compact field per element
    archive->slot[+0x18](&+0x4b0)             ; one object reference, `0x00038bc0`
    archive->slot[+0x1c](&+0x4b4)             ; one FName, `0x00038bf0`
    FUN_0003d160(archive, +0x4f4)             ; the second name array, version > 0x62 only
    slot[+0x98](...)                          ; the defaults buffer

The pair at `+0x4b0`/`+0x4b4` is therefore written **after** both arrays, not in front of them, and the two slots
have the same on-disk width -- they differ only in which proxy map they consult before the write:

    0x00038bc0   mov edx,[arg]; mov edx,[edx]; mov ecx,[ecx+0x4c]; mov eax,[ecx]; push edx
                 call [eax+0x24]                     ; proxy->MapObject(object)
                 push &value; push archive; call 0x637e0   ; one FCompactIndex
    0x00038bf0   the same, `call [eax+0x20]`         ; proxy->MapName(name)

Both end in `FUN_000637e0`, so the reference field and the name field use the same encoding: the question "which
encoding is the middle's first compact value" cannot be settled from a width, and the order of the writer's calls is
what settles it.

So the tool walks the writer's order and asks where, if anywhere, it lands exactly on the defaults block start:

    [count c][c x (compact reference, 8 bytes)] [count n][n x compact] [reference] [name] [count m][m x compact]

It does that from `prefix_end + 42` -- the field table's position, 22 bytes of `UState::Serialize` plus the five
dwords -- and then from every one of the next 32 byte positions, because the field table's own sum is the thing on
trial. Each class answers for itself; the controls are the readings this replaces (the pair in front of the arrays,
the two name arrays adjacent) and the same order with the version-gated last array dropped.

    python3 tools/class-record-order.py --dump $SG1_DUMP --class AmbientCreatures.HerdAnimal
    python3 tools/class-record-order.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-order.py --report
    python3 tools/class-record-order.py --check

Reads the packages and the committed prefix table; no boot, no Ghidra round, no value decoded.
"""
import argparse
import collections
import csv
import importlib.util
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402


def _load(mod):
    spec = importlib.util.spec_from_file_location(mod, os.path.join(HERE, mod + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


crf = _load("class-record-fields")

ROOT = os.path.dirname(HERE)
DOC_OFFSET = crf.STATE_BYTES + 4 * crf.CLASS_DWORDS    # 42: where the field table puts the array's count
HEAD_SPAN = 32                                          # the offset sweep around it
MAX_COUNT = 4096
MAX_REF = 5
ARTIFACT = "class_record_order.csv"
COLUMNS = ["package", "class", "payload", "prefix_end", "block_start", "middle",
           "first_width", "first_value", "head_fit", "head_fits", "head_off",
           "arr_count", "names_count", "ref_width", "ref_value", "name_width", "name_value",
           "last_count", "end", "residue", "exact",
           "ref_null", "ref_in_tables", "name_in_names",
           "ctl_pair_fit", "ctl_names_fit", "ctl_nolast_fit"]


def walk_array(buf, p):
    """`FUN_0003d900`: a compact count, then count x (compact reference, dword, dword)."""
    if p < 0 or p >= len(buf):
        return None
    try:
        count, o = crf.read_index(buf, p)
    except IndexError:
        return None
    if count < 0 or count > MAX_COUNT or o + 9 * count > len(buf) + 8:
        return None
    widths = []
    for _ in range(count):
        if o >= len(buf):
            return None
        try:
            _, e = crf.read_index(buf, o)
        except IndexError:
            return None
        if e - o > MAX_REF or e + 8 > len(buf):
            return None
        widths.append(e - o)
        o = e + 8
    return {"count": count, "widths": widths, "end": o}


def walk_names(buf, p):
    """`FUN_0003d160`: a compact count, then that many one-field names."""
    if p < 0 or p >= len(buf):
        return None
    try:
        n, o = crf.read_index(buf, p)
    except IndexError:
        return None
    if n < 0 or n > MAX_COUNT or o + n > len(buf) + 1:
        return None
    for _ in range(n):
        if o >= len(buf):
            return None
        try:
            _, e = crf.read_index(buf, o)
        except IndexError:
            return None
        o = e
    return {"count": n, "end": o}


def walk_field(buf, p):
    """One compact field: both `0x38bc0` and `0x38bf0` end in `FUN_000637e0`."""
    if p < 0 or p >= len(buf):
        return None
    try:
        v, e = crf.read_index(buf, p)
    except IndexError:
        return None
    if e - p > MAX_REF:
        return None
    return {"value": v, "width": e - p, "end": e}


def walk_writer(buf, p, last=True):
    """The writer's order from the array onward."""
    arr = walk_array(buf, p)
    if not arr:
        return None
    nm = walk_names(buf, arr["end"])
    if not nm:
        return None
    ref = walk_field(buf, nm["end"])
    if not ref:
        return None
    name = walk_field(buf, ref["end"])
    if not name:
        return None
    o = name["end"]
    last_count = ""
    if last:
        lastw = walk_names(buf, o)
        if not lastw:
            return None
        last_count = lastw["count"]
        o = lastw["end"]
    return {"arr": arr, "names_count": nm["count"], "ref": ref, "name": name,
            "last_count": last_count, "end": o}


def walk_pair_first(buf, p):
    """The reading this replaces: reference and name in front of both arrays."""
    ref = walk_field(buf, p)
    if not ref:
        return None
    name = walk_field(buf, ref["end"])
    if not name:
        return None
    arr = walk_array(buf, name["end"])
    if not arr:
        return None
    nm = walk_names(buf, arr["end"])
    if not nm:
        return None
    lastw = walk_names(buf, nm["end"])
    if not lastw:
        return None
    return lastw["end"]


def walk_names_first(buf, p):
    """A variant with the two name arrays adjacent: array, names, names, reference, name."""
    arr = walk_array(buf, p)
    if not arr:
        return None
    n1 = walk_names(buf, arr["end"])
    if not n1:
        return None
    n2 = walk_names(buf, n1["end"])
    if not n2:
        return None
    ref = walk_field(buf, n2["end"])
    if not ref:
        return None
    name = walk_field(buf, ref["end"])
    if not name:
        return None
    return name["end"]


def fit_offset(walk, data, prefix_end, block_start):
    """The first of the `HEAD_SPAN` offsets whose walk lands exactly on the block start, or None."""
    for h in range(HEAD_SPAN):
        end = walk(data, prefix_end + DOC_OFFSET + h)
        if end == block_start:
            return h
    return None


def measure(data, prefix_end, block_start):
    row = {"payload": len(data), "prefix_end": prefix_end, "block_start": block_start,
           "middle": block_start - prefix_end, "first_width": "", "first_value": "",
           "head_fit": "", "head_fits": "", "head_off": "",
           "arr_count": "", "names_count": "", "ref_width": "", "ref_value": "", "name_width": "",
           "name_value": "", "last_count": "", "end": "", "residue": "", "exact": 0,
           "ref_null": 0, "ref_in_tables": 0, "name_in_names": 0,
           "ctl_pair_fit": "", "ctl_names_fit": "", "ctl_nolast_fit": ""}
    if block_start <= prefix_end or prefix_end + DOC_OFFSET >= len(data):
        return row
    # the compact value the field table calls the array's count, sized on its own
    w, b = 1, data[prefix_end + DOC_OFFSET]
    while b & 0x40 and w < MAX_REF:
        b = data[prefix_end + DOC_OFFSET + w]
        w += 1
    row["first_width"] = w
    try:
        row["first_value"] = crf.read_index(data, prefix_end + DOC_OFFSET)[0]
    except IndexError:
        row["first_width"] = ""
    fits = []
    for h in range(HEAD_SPAN):
        w = walk_writer(data, prefix_end + DOC_OFFSET + h)
        if w and w["end"] == block_start:
            fits.append(h)
    row["head_fits"] = len(fits)
    best = fits[0] if fits else 0
    if fits:
        row["head_fit"] = fits[0]
        row["head_off"] = DOC_OFFSET + fits[0]
    w = walk_writer(data, prefix_end + DOC_OFFSET + best, last=True)
    if w:
        row.update({"arr_count": w["arr"]["count"], "names_count": w["names_count"],
                    "ref_width": w["ref"]["width"], "ref_value": w["ref"]["value"],
                    "name_width": w["name"]["width"], "name_value": w["name"]["value"],
                    "last_count": w["last_count"], "end": w["end"],
                    "residue": block_start - w["end"], "exact": int(w["end"] == block_start)})
    row["ctl_pair_fit"] = fit_offset(walk_pair_first, data, prefix_end, block_start)
    row["ctl_names_fit"] = fit_offset(walk_names_first, data, prefix_end, block_start)
    row["ctl_nolast_fit"] = fit_offset(lambda b, p: (walk_writer(b, p, last=False) or {}).get("end"),
                                       data, prefix_end, block_start)
    return row


def finish(row, nimp, nex, nnames):
    """The value checks, which need the package's tables and are not part of the walk."""
    row["ref_null"] = int(row["ref_value"] == 0) if row["ref_value"] != "" else 0
    if row["ref_value"] != "":
        v = int(row["ref_value"])
        row["ref_in_tables"] = int(v == 0 or (v > 0 and v <= nex) or (v < 0 and -v <= nimp))
    if row["name_value"] != "":
        row["name_in_names"] = int(0 <= int(row["name_value"]) < nnames)
    return row


def classes(args):
    prefixes = crf.read_prefixes()
    for fn in sorted(os.listdir(os.path.join(args.dump, "System"))):
        if not fn.endswith(".u"):
            continue
        pkg = Package(os.path.join(args.dump, "System", fn))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        name = fn[:-2]
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            key = (name, pkg.name(exp["name"]))
            if key not in prefixes:
                continue
            start, block = prefixes[key]
            if block <= 0:
                continue
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            yield key, data, start, block, len(pkg.imports), len(pkg.exports), len(pkg.names)


def sweep(args):
    rows = []
    for (pkg_name, cls), data, start, block, nimp, nex, nnames in classes(args):
        row = finish(measure(data, start, block), nimp, nex, nnames)
        row.update({"package": pkg_name, "class": cls})
        rows.append(row)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            wr = csv.DictWriter(fh, fieldnames=COLUMNS)
            wr.writeheader()
            wr.writerows(rows)
        print("wrote %s: %d rows" % (path, len(rows)))
    report(rows)
    return 0


def _ints(rows, key):
    return [int(r[key]) for r in rows if r[key] not in ("", None)]


def _pct(n, d):
    return 100.0 * n / d if d else 0.0


def report(rows):
    rows = [r for r in rows if int(r.get("block_start") or 0) > 0]
    n = len(rows)
    at0 = [r for r in rows if r["end"] not in ("", None) and int(r["head_fit"] or -1) == 0]
    fitted = [r for r in rows if r["head_fit"] not in ("", None)]
    print("classes with a located block start: %d" % n)
    print("the writer's order lands exactly on the defaults start at the field table's own offset (%d): %d (%.1f%%)"
          % (DOC_OFFSET, len(at0), _pct(len(at0), n)))
    print("it lands exactly somewhere in +0..+%d: %d (%.1f%%); offsets that close: %s"
          % (HEAD_SPAN - 1, len(fitted), _pct(len(fitted), n),
             collections.Counter(int(r["head_fit"]) for r in fitted).most_common(8)))
    if fitted:
        mode = collections.Counter(int(r["head_fit"]) for r in fitted).most_common(1)[0][0]
        at = [r for r in fitted if int(r["head_fit"]) == mode]
        print("  at the modal offset (+%d -> %d bytes after the prefix): %d classes, and one offset closes on "
              "%d of them" % (mode, DOC_OFFSET + mode, len(at), sum(1 for r in at if int(r["head_fits"]) == 1)))
        print("  their middle: median %d; array count %s; name count %s; last count %s"
              % (statistics.median(_ints(at, "middle")),
                 collections.Counter(int(r["arr_count"]) for r in at).most_common(5),
                 collections.Counter(int(r["names_count"]) for r in at).most_common(4),
                 collections.Counter(int(r["last_count"]) for r in at).most_common(4)))
        print("  the pair: reference width %s (null %d of %d, inside the package's tables %d), name width %s "
              "(a valid name index %d)"
              % (collections.Counter(int(r["ref_width"]) for r in at).most_common(3),
                 sum(int(r["ref_null"]) for r in at), len(at), sum(int(r["ref_in_tables"]) for r in at),
                 collections.Counter(int(r["name_width"]) for r in at).most_common(3),
                 sum(int(r["name_in_names"]) for r in at)))
    print("controls, classes where any of the %d offsets closes: pair in front of the arrays %d, the two name "
          "arrays adjacent %d, the version-gated last array dropped %d"
          % (HEAD_SPAN, sum(1 for r in rows if r["ctl_pair_fit"] not in ("", None)),
             sum(1 for r in rows if r["ctl_names_fit"] not in ("", None)),
             sum(1 for r in rows if r["ctl_nolast_fit"] not in ("", None))))
    near = [r for r in rows if abs(int(r["block_start"]) - int(r["end"] or 0)) <= 64]
    if near:
        print("  near misses (residue within 64 bytes): %s"
              % collections.Counter(_ints(near, "residue")).most_common(8))
    fw = collections.Counter(int(r["first_width"]) for r in rows if r["first_width"] != "")
    print("the compact value at the field table's position is %d..%d bytes wide: %s"
          % (min(fw) if fw else 0, max(fw) if fw else 0, fw.most_common(5)))


def one(args):
    pkg = Package(os.path.join(args.dump, "System", args.package + ".u"))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    prefixes = crf.read_prefixes()
    key = (args.package, args.cls)
    if key not in prefixes:
        print("no prefix row for %s" % (key,))
        return 1
    start, block = prefixes[key]
    for exp in pkg.exports:
        if pkg.class_name_of(exp) != "Class" or pkg.name(exp["name"]) != args.cls:
            continue
        data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
        row = finish(measure(data, start, block), len(pkg.imports), len(pkg.exports), len(pkg.names))
        for k in COLUMNS:
            print("%-16s %s" % (k, row.get(k, "")))
        best = int(row["head_fit"] or 0)
        w = walk_writer(data, start + DOC_OFFSET + best, last=True)
        if w:
            print("detail at +%d: array count %d (reference widths %s), names %d, ref %d (%d bytes), name %d "
                  "(%d bytes), last names %s, end %d of %d"
                  % (DOC_OFFSET + best, w["arr"]["count"],
                     collections.Counter(w["arr"]["widths"]).most_common(4), w["names_count"],
                     w["ref"]["value"], w["ref"]["width"], w["name"]["value"], w["name"]["width"],
                     w["last_count"], w["end"], block))
        return 0
    print("class not found")
    return 1


def self_check():
    """A synthetic record in the writer's order closes at its own offset; every control reading must not."""

    def compact(v):
        v = abs(v)
        out, more = v & 0x3F, v >> 6
        if not more:
            return bytes([out])
        return bytes([0x40 | out]) + compact(more)

    def element(ref, d1, d2):
        return compact(ref) + d1 + d2

    a, b = b"\x11\x22\x33\x44", b"\x55\x66\x77\x88"
    prelude = bytes(range(1, crf.STATE_BYTES + 1)) + b"\x01\x00\x00\x00" * crf.CLASS_DWORDS
    elems = b"".join(element(ref, a, b) for ref in (0x01, 0x02, 0x07, 300))
    names = compact(2) + compact(3) + compact(0)
    last = compact(1) + compact(0)
    body = compact(4) + elems + names + compact(0) + compact(5) + last
    prefix = compact(110) + b"\x00" + compact(5)
    mid = len(prefix)
    block = len(prefix) + len(prelude) + len(body)
    payload = prefix + prelude + body + bytes(64)

    row = finish(measure(payload, mid, block), 64, 4096, 900)
    assert int(row["head_fit"]) == 0 and int(row["head_off"]) == DOC_OFFSET, row
    assert int(row["arr_count"]) == 4 and int(row["names_count"]) == 2 and int(row["last_count"]) == 1, row
    assert int(row["ref_width"]) == 1 and int(row["ref_null"]) == 1, row
    assert int(row["name_width"]) == 1 and int(row["name_value"]) == 5 and int(row["name_in_names"]) == 1, row
    assert row["ctl_pair_fit"] is None and row["ctl_names_fit"] is None, row

    # 19 bytes in front of the array: the walk still closes, at +19 -- the offset sweep is what finds it
    row2 = measure(prefix + prelude + bytes(19) + body + bytes(64), mid, block + 19)
    assert int(row2["head_fit"]) == 19 and int(row2["residue"]) == 0, row2

    # the pair in front of the arrays walks, but never to the block start
    swapped = compact(0) + compact(5) + compact(4) + elems + names + last
    row3 = measure(prefix + prelude + swapped + bytes(64), mid,
                   len(prefix) + len(prelude) + len(swapped))
    assert int(row3["exact"]) == 0 and int(row3["head_fit"] or -1) != 0, row3

    # a reference wider than five bytes is not a reference, so the walk refuses instead of reading on
    wide = compact(4) + elems + names + b"\x40" * 6
    row4 = measure(prefix + prelude + wide + bytes(64), mid, len(prefix) + len(prelude) + len(wide) + 1)
    assert row4["end"] == "" and int(row4["exact"]) == 0, row4
    print("self-check ok: the writer's order (array, name array, reference, name, name array) closes at the field "
          "table's offset, still closes when 19 unaccounted bytes sit in front of the array -- at +19 --, the "
          "pair-in-front reading never lands on the block start, and a six-byte reference is refused")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", help="dump root (the directory holding System/)")
    ap.add_argument("--class", dest="cls", help="one class, with --package")
    ap.add_argument("--package", default="", help="package of --class")
    ap.add_argument("--sweep", action="store_true", help="measure every located class")
    ap.add_argument("-o", "--out", help="artifact directory")
    ap.add_argument("--report", action="store_true", help="print the committed artifact's tables")
    ap.add_argument("--check", action="store_true", help="self-test, no dump")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.report:
        with open(os.path.join(ROOT, "inventory", ARTIFACT), newline="", encoding="utf-8") as fh:
            report(list(csv.DictReader(fh)))
        return 0
    if not args.dump:
        ap.error("--dump is required (or --check)")
    if args.cls:
        if "." in args.cls and not args.package:
            args.package, args.cls = args.cls.split(".", 1)
        return one(args)
    if args.sweep:
        return sweep(args)
    ap.error("pick --sweep, --class or --report")


if __name__ == "__main__":
    sys.exit(main())
