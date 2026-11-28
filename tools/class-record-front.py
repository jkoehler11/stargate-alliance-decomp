#!/usr/bin/env python3
"""What the three to five bytes in front of the class record's array can and cannot be.

The forward walk of the writer's order closes on 1,626 of 2,204 located class payloads, and on 1,482 of them it
steps over a gap of 19 to 21 bytes made of a 3-5 byte front and a sixteen-zero run
(`docs/NATIVE_VM.md`, *The gap's last sixteen bytes are zero*). The zone run is the four dwords `UStruct::Serialize`
writes (`0x370e0` at `+0x5c`, two `0x37110` at `+0x58` and `+0x54`, the script count at `+0x4c`); the front is
written by no body in the chain (`0x38dc0`, `0x392b0`, `0x394c0`, `0x3a800`).

So the front is identified by what it can be, against controls. Four readings are tested on every class, and each
one is also run on three same-length windows that a real field would not be found in:

* **a name** -- every value a valid index into the package's own name table;
* **a property tag** -- the first value resolving to a name the class itself declares as a `Property` export;
* **an object reference** -- every value either a positive export index or a negative import index of the package;
* **a compact index of any width** -- whether any value needed more than one byte, which a name or export index
  above 63 must.

    python3 tools/class-record-front.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-front.py --report
    python3 tools/class-record-front.py --dump $SG1_DUMP --class AmbientCreatures.Bird
    python3 tools/class-record-front.py --dump $SG1_DUMP --grammar -o inventory
    python3 tools/class-record-front.py --fields-report
    python3 tools/class-record-front.py --check

`--grammar` is the second half of the question. Four readings are a base rate because a 3-5 byte window of a
zero-rich payload can look like anything; the writer's own field order is falsifiable instead: `UStruct::Serialize`
(`0x000392b0`) writes one compact and the `FriendlyName` it asserts is not `NAME_None`, then `+0x5c`, `+0x58`, `+0x54`
and the script, and `UState::Serialize` (`0x000394c0`) adds 8 + 8 + 2 + 4 bytes before `UClass`'s five dwords
(`0x0003a800`). Walked from the prefix's script reference over the 1,185 anchored fronts, that list has to end exactly
on the array's head, and each field's *value* is then a statement about the build rather than about the payload.

Reads the packages and the committed gap census; no boot, no value decoded, no game data written.
"""
import argparse
import collections
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402

ROOT = os.path.dirname(HERE)
GAP_ARTIFACT = "class_record_gap_front.csv"
ARTIFACT = "class_record_front.csv"
DOC_OFFSET = 42          # UState's 22 bytes and the five dwords: where the walk steps over its gap
TAIL_BYTES = 16          # the four dwords the struct body writes, at the gap's own end
COLUMNS = ["package", "class", "gap_len", "front_len", "front_hex", "values", "value_count",
           "all_single_byte", "names_resolved", "declared_first", "refs_in_tables",
           "refs_nonzero", "refs_nonzero_in_tables",
           "ctl_shift_names", "ctl_shift_declared", "ctl_shift_refs",
           "ctl_before_names", "ctl_before_declared", "ctl_before_refs",
           "ctl_block_names", "ctl_block_declared", "ctl_block_refs"]


def read_index(buf, off):
    """UE2 `FCompactIndex`: bit 7 is the sign, bit 6 means a byte follows, 6 bits then 7 per byte."""
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


def parse_front(buf):
    """The window as compact values: `(values, widths)`, or `(None, None)` if a width overruns it."""
    vals, widths, o = [], [], 0
    while o < len(buf):
        try:
            v, e = read_index(buf, o)
        except IndexError:
            return None, None
        if e <= o or e > len(buf):
            return None, None
        vals.append(v)
        widths.append(e - o)
        o = e
    return vals, widths


FIELDS_ARTIFACT = "class_record_front_fields.csv"
ANCHOR_ARTIFACT = "class_record_group_" "anchor.csv"   # split: a name in this file is credited as this tool's output
TAIL_DWORDS = 16         # `+0x4a0`, `+0x4a4`, `+0x4a8`, `+0x4ac` -- `UClass`'s four cleared dwords
FIELDS_COLUMNS = ["package", "class", "payload", "prefix_end", "head", "front_len", "x", "x_len",
                  "name", "name_len", "name_matches", "dword_5c", "dword_58", "dword_54", "script_len",
                  "span_80", "span_88", "span_94", "dword_90", "flags", "span_tail", "ends", "status",
                  "ctl_shift", "ctl_shift_name", "ctl_head", "ctl_offsets"]


def score(window, names_n, nexports, nimps, declared, name_of):
    """The four readings on one window, plus the values themselves."""
    vals, widths = parse_front(window)
    out = {"values": "", "value_count": 0, "all_single_byte": 0, "names_resolved": 0,
           "declared_first": 0, "refs_in_tables": 0, "refs_nonzero": 0, "refs_nonzero_in_tables": 0}
    if not vals:
        return out
    out["values"] = " ".join(str(v) for v in vals)
    out["value_count"] = len(vals)
    out["all_single_byte"] = int(all(w == 1 for w in widths))
    out["names_resolved"] = int(all(0 <= v < names_n for v in vals))
    first = vals[0]
    nm = name_of(first) if 0 <= first < names_n else ""
    out["declared_first"] = int(bool(nm) and nm in declared)
    out["refs_in_tables"] = sum(1 for v in vals
                                if (v == 0) or (0 < v <= nexports) or (0 > v >= -nimps))
    out["refs_nonzero"] = sum(1 for v in vals if v != 0)
    out["refs_nonzero_in_tables"] = sum(1 for v in vals
                                        if (0 < v <= nexports) or (0 > v >= -nimps))
    return out


def gap_rows():
    path = os.path.join(ROOT, "inventory", GAP_ARTIFACT)
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def property_names(pkg):
    """Per class export index, the names of the `Property` exports whose Outer is that class."""
    props = collections.defaultdict(set)
    for exp in pkg.exports:
        if pkg.class_name_of(exp).endswith("Property"):
            props[exp["outer"]].add(pkg.name(exp["name"]))
    return props


def measure_package(pkgname, rows, dump, out):
    pkg = Package(os.path.join(dump, "System", pkgname + ".u"))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    nnames, nexports, nimps = len(pkg.names), len(pkg.exports), len(pkg.imports)
    name_of = lambda i: pkg.name(i)  # noqa: E731
    props = property_names(pkg)
    index = {}
    for k, exp in enumerate(pkg.exports, start=1):
        index.setdefault(pkg.name(exp["name"]), (k, exp))
    for r in rows:
        row = {"package": pkgname, "class": r["class"], "gap_len": r["gap_len"],
               "front_len": "", "front_hex": "", "values": "", "value_count": 0,
               "all_single_byte": 0, "names_resolved": 0, "declared_first": 0, "refs_in_tables": 0,
               "refs_nonzero": 0, "refs_nonzero_in_tables": 0,
               "ctl_shift_names": 0, "ctl_shift_declared": 0, "ctl_shift_refs": 0,
               "ctl_before_names": 0, "ctl_before_declared": 0, "ctl_before_refs": 0,
               "ctl_block_names": 0, "ctl_block_declared": 0, "ctl_block_refs": 0}
        out.append(row)
        if r["class"] not in index or not r["gap_len"]:
            continue
        flen = int(r["gap_len"]) - TAIL_BYTES
        if flen <= 0:
            continue
        k, exp = index[r["class"]]
        declared = props.get(k, set())
        data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
        start = int(r["prefix_end"]) + DOC_OFFSET
        block = int(r["block_start"])
        if start + flen > len(data):
            continue
        row["front_len"] = flen
        row["front_hex"] = data[start:start + flen].hex(" ")
        got = score(data[start:start + flen], nnames, nexports, nimps, declared, name_of)
        row.update(got)
        # controls: the same-length window one byte later inside the same gap, one window earlier, and the
        # bytes immediately before the defaults block
        for prefix, pos in (("ctl_shift", start + 1), ("ctl_before", start - flen), ("ctl_block", block - flen)):
            if not (0 <= pos and pos + flen <= len(data)):
                continue
            c = score(data[pos:pos + flen], nnames, nexports, nimps, declared, name_of)
            row[prefix + "_names"] = c["names_resolved"]
            row[prefix + "_declared"] = c["declared_first"]
            row[prefix + "_refs"] = c["refs_in_tables"]
    return out


def sweep(args):
    rows = gap_rows()
    by_pkg = collections.defaultdict(list)
    for r in rows:
        by_pkg[r["package"]].append(r)
    out = []
    for pkgname in sorted(by_pkg):
        measure_package(pkgname, by_pkg[pkgname], args.dump, out)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(out)
        print("wrote %s: %d rows" % (path, len(out)))
    report(out)
    return 0


def _pct(n, d):
    return 100.0 * n / d if d else 0.0


def report(rows):
    rows = [r for r in rows if r.get("front_len") not in ("", None)]
    print("gap rows with a front: %d" % len(rows))
    if not rows:
        return
    n = len(rows)
    lens = collections.Counter(int(r["front_len"]) for r in rows)
    print("front lengths: %s" % lens.most_common(6))
    single = sum(int(r["all_single_byte"]) for r in rows)
    vmax = max((int(v) for r in rows for v in r["values"].split()), default=0)
    print("fronts whose every value is one byte: %d (%.1f%%); largest value %d"
          % (single, _pct(single, n), vmax))
    print("fronts whose values are all inside the name table: %d (%.1f%%)" % (sum(int(r["names_resolved"]) for r in rows),
                                                                             _pct(sum(int(r["names_resolved"]) for r in rows), n)))
    print("fronts whose first value names a property the class declares: %d (%.1f%%)"
          % (sum(int(r["declared_first"]) for r in rows), _pct(sum(int(r["declared_first"]) for r in rows), n)))
    refs = sum(int(r["refs_in_tables"]) for r in rows)
    vals = sum(int(r["value_count"]) for r in rows)
    print("front values that are valid object references: %d of %d (%.1f%%)" % (refs, vals, _pct(refs, vals)))
    nz = sum(int(r["refs_nonzero"]) for r in rows)
    nzi = sum(int(r["refs_nonzero_in_tables"]) for r in rows)
    print("of the non-zero values, inside the export/import tables: %d of %d (%.1f%%)" % (nzi, nz, _pct(nzi, nz)))
    for label, key in (("one byte later", "ctl_shift"), ("one window earlier", "ctl_before"),
                       ("before the block", "ctl_block")):
        nm = sum(int(r[key + "_names"]) for r in rows)
        dc = sum(int(r[key + "_declared"]) for r in rows)
        rf = sum(int(r[key + "_refs"]) for r in rows)
        print("  control, %-18s names %d (%.1f%%), declared %d (%.1f%%), references %d of %d (%.1f%%)"
              % (label, nm, _pct(nm, n), dc, _pct(dc, n), rf, vals, _pct(rf, vals)))
    print("most common fronts: %s" % collections.Counter(r["values"] for r in rows).most_common(6))


def one(args):
    if "." in args.cls and not args.package:
        args.package, args.cls = args.cls.split(".", 1)
    if not args.package:
        args.package = "Engine"
    rows = [r for r in gap_rows() if r["package"] == args.package and r["class"] == args.cls]
    if not rows:
        print("no gap row for %s.%s" % (args.package, args.cls))
        return 2
    out = measure_package(args.package, rows, args.dump, [])
    for r in out:
        print("%s.%s gap %s front %s = %s (one byte each %d, names %d, declared %d, refs %d)"
              % (r["package"], r["class"], r["gap_len"], r["front_hex"] or "-", r["values"] or "-",
                 r["all_single_byte"], r["names_resolved"], r["declared_first"], r["refs_in_tables"]))
    return 0


def read_le32(buf, off):
    return int.from_bytes(buf[off:off + 4], "little")


def parse_fields(buf):
    """The front decoded in the order `UClass::Serialize` writes it.

    `UState::Serialize` (`0x000394c0`) calls `UStruct::Serialize` (`0x000392b0`) first, and that body's call sites are
    straight-line, so the fields between the prefix's script reference and the array are known before they are read:
    one compact, one `FName` (the slot that asserts `FriendlyName!=NAME_None`), a dword, two dwords, the script's
    length and bytes, 8 + 8 + 2 + 4 bytes of state, then `UClass`'s five dwords (`0x0003a800`). Nothing here guesses a
    width: the two compacts are decoded, the rest are fixed, and the walk has to end **exactly** on the array's head.

    Returns one dict per front; `status` is `close` or `ends+-N` and `ends` is the signed overshoot.
    """
    out = {"x": "", "x_len": "", "name": "", "name_len": "", "dword_5c": "", "dword_58": "", "dword_54": "",
           "script_len": "", "span_80": "", "span_88": "", "span_94": "", "dword_90": "", "flags": "",
           "span_tail": "", "ends": "", "status": ""}

    def need(p, n):
        if p + n > len(buf):
            raise IndexError(p + n)

    try:
        out["x"], p = read_index(buf, 0)
        out["x_len"] = p
        out["name"], after = read_index(buf, p)
        out["name_len"] = after - p
        p = after
        need(p, 4)
        out["dword_5c"] = read_le32(buf, p)
        p += 4
        need(p, 8)
        out["dword_58"] = read_le32(buf, p)
        out["dword_54"] = read_le32(buf, p + 4)
        p += 8
        need(p, 4)
        out["script_len"] = read_le32(buf, p)
        p += 4
        if not 0 <= out["script_len"] <= len(buf):
            out["status"] = "script overruns the front"
            return out
        p += out["script_len"]
        need(p, 8 + 8 + 2 + 4 + 4 + TAIL_DWORDS)
        out["span_80"] = buf[p:p + 8].hex()
        out["span_88"] = buf[p + 8:p + 16].hex()
        out["span_94"] = buf[p + 16:p + 18].hex()
        out["dword_90"] = read_le32(buf, p + 18)
        out["flags"] = read_le32(buf, p + 22)
        out["span_tail"] = buf[p + 26:p + 26 + TAIL_DWORDS].hex()
        p += 26 + TAIL_DWORDS
    except IndexError:
        out["status"] = "overruns the front"
        return out
    out["ends"] = p - len(buf)
    out["status"] = "close" if p == len(buf) else "ends%+d" % (p - len(buf))
    return out


def anchor_rows():
    with open(os.path.join(ROOT, "inventory", ANCHOR_ARTIFACT), newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r["head"] != ""]


def fields_package(pkgname, rows, dump, out):
    pkg = Package(os.path.join(dump, "System", pkgname + ".u"))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    classes = {}
    for exp in pkg.exports:
        if pkg.class_name_of(exp) == "Class":
            classes[pkg.name(exp["name"])] = exp
    for r in rows:
        row = {"package": pkgname, "class": r["class"], "payload": r["payload"], "prefix_end": r["prefix_end"],
               "head": r["head"], "front_len": "", "x": "", "x_len": "", "name": "", "name_len": "",
               "name_matches": 0, "dword_5c": "", "dword_58": "", "dword_54": "", "script_len": "",
               "span_80": "", "span_88": "", "span_94": "", "dword_90": "", "flags": "", "span_tail": "",
               "ends": "", "status": "", "ctl_shift": 0, "ctl_shift_name": 0, "ctl_head": 0, "ctl_offsets": 0}
        out.append(row)
        exp = classes.get(r["class"])
        if exp is None:
            continue
        data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
        pe, head = int(r["prefix_end"]), int(r["prefix_end"]) + int(r["head"])
        front = data[pe:head]
        row["front_len"] = len(front)
        got = parse_fields(front)
        row.update(got)
        row["name_matches"] = int(got["name"] != "" and got["name"] == exp["name"])
        # controls: the same grammar one byte later, that walk's name checked against the export table, and the
        # same-length window at the array's own head
        shifted = parse_fields(front[1:])
        row["ctl_shift"] = int(shifted["status"] == "close")
        row["ctl_shift_name"] = int(shifted["status"] == "close" and shifted["name"] == exp["name"])
        row["ctl_head"] = int(parse_fields(data[head:head + max(len(front), 1)])["status"] == "close")
        row["ctl_offsets"] = sum(1 for o in range(1, len(front)) if parse_fields(front[o:])["status"] == "close")
    return out


def grammar_sweep(args):
    rows = anchor_rows()
    by_pkg = collections.defaultdict(list)
    for r in rows:
        by_pkg[r["package"]].append(r)
    out = []
    for pkgname in sorted(by_pkg):
        fields_package(pkgname, by_pkg[pkgname], args.dump, out)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, FIELDS_ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS_COLUMNS)
            w.writeheader()
            w.writerows(out)
        print("wrote %s: %d rows" % (path, len(out)))
    report_fields(out)
    return 0


def _share(rows, key, pred):
    return sum(1 for r in rows if r[key] not in ("", None) and pred(r[key]))


def report_fields(rows):
    rows = [r for r in rows if r.get("status")]
    if not rows:
        print("no front rows")
        return
    n = len(rows)
    status = collections.Counter(r["status"] for r in rows)
    print("fronts read in the writer's order: %d" % n)
    print("the walk ends on the array's head: %d (%.1f%%); statuses %s"
          % (status.get("close", 0), _pct(status.get("close", 0), n), status.most_common(5)))
    print("the decoded name is the class's own export name: %d (%.1f%%)"
          % (_share(rows, "name_matches", lambda v: int(v) == 1),
             _pct(_share(rows, "name_matches", lambda v: int(v) == 1), n)))
    closed = [r for r in rows if r["status"] == "close"]
    print("on the %d closing rows:" % len(closed))
    for label, key, want in (("`+0x5c` = 0", "dword_5c", "0"), ("`+0x58` = -1", "dword_58", "4294967295"),
                             ("`+0x54` = -1", "dword_54", "4294967295"), ("script length = 0", "script_len", "0"),
                             ("`+0x88` = eight `0xff`", "span_88", "ffffffffffffffff"),
                             ("`+0x94` = two `0xff`", "span_94", "ffff"),
                             ("`+0x90` = 0", "dword_90", "0"),
                             ("`+0x4a0`..`+0x4ac` = sixteen zero bytes", "span_tail", "00" * TAIL_DWORDS)):
        got = sum(1 for r in closed if str(r[key]) == want)
        print("  %-34s %d of %d" % (label, got, len(closed)))
    flags = collections.Counter(r["flags"] for r in closed)
    with_bits = sum(c for v, c in flags.items() if int(v) & 0x12 == 0x12)
    print("  the flags dword carries both compiler bits (0x12): %d of %d, %d distinct values"
          % (with_bits, len(closed), len(flags)))
    print("  the commonest flags: %s" % [(hex(int(k)), v) for k, v in flags.most_common(6)])
    xvals = collections.Counter(str(r["x"]) for r in closed)
    xw = collections.Counter(int(r["x_len"]) for r in closed if r["x_len"] != "")
    print("  the leading compact: width %s; null on %d of %d, %d distinct values"
          % (xw.most_common(4), xvals.get("0", 0), len(closed), len(xvals)))
    print("  the state's `+0x80` span: %d distinct, commonest %s"
          % (len(set(r["span_80"] for r in closed)), collections.Counter(r["span_80"] for r in closed).most_common(4)))
    print("controls: the same walk one byte later closes %d (naming the class %d); at the array's head %d;"
          % (sum(int(r["ctl_shift"]) for r in rows), sum(int(r["ctl_shift_name"]) for r in rows),
             sum(int(r["ctl_head"]) for r in rows)))
    print("  rows with no other closing offset in the front: %d of %d (other offsets close on %d rows, max %d)"
          % (sum(1 for r in rows if int(r["ctl_offsets"]) == 0), len(rows),
             sum(1 for r in rows if int(r["ctl_offsets"]) > 0), max(int(r["ctl_offsets"]) for r in rows)))
    namew = collections.Counter(int(r["name_len"]) for r in closed if r["name_len"] != "")
    print("the name compact's width: %s" % namew.most_common(4))


def fields_one(args):
    if "." in args.cls and not args.package:
        args.package, args.cls = args.cls.split(".", 1)
    rows = [r for r in anchor_rows() if r["package"] == args.package and r["class"] == args.cls]
    if not rows:
        print("no anchored row for %s.%s" % (args.package, args.cls))
        return 2
    for r in fields_package(args.package, rows, args.dump, []):
        print("%s.%s front %s: %s" % (r["package"], r["class"], r["front_len"], r["status"]))
        for key in FIELDS_COLUMNS[6:]:
            print("  %-12s %s" % (key, r.get(key)))
    return 0


def self_check():
    """A synthetic payload: a 42-byte prelude, a three-value front, sixteen zeros, then a block."""
    def compact(v):
        assert 0 <= v < 0x40
        return bytes([v])

    prelude = bytes(DOC_OFFSET)
    front = compact(2) + compact(0) + compact(0)
    body = prelude + front + bytes(TAIL_BYTES) + b"\x01\x02\x03"
    names = 8
    got = score(body[DOC_OFFSET:DOC_OFFSET + 3], names, 4, 2, {"Foo"}, lambda i: ["None", "Foo"][i] if i < 2 else "Name%d" % i)
    assert got["values"] == "2 0 0", got
    assert got["all_single_byte"] == 1 and got["names_resolved"] == 1, got
    assert got["declared_first"] == 0, got          # index 2 does not resolve
    assert got["refs_in_tables"] == 3, got          # 2 is an export, 0 is null, 0 is null
    # a window whose first value is the declared property, and one whose value needs two bytes
    got2 = score(b"\x01\x00\x00", names, 4, 2, {"Foo"}, lambda i: ["None", "Foo"][i] if i < 2 else "Name%d" % i)
    assert got2["declared_first"] == 1, got2
    assert score(b"\x40\x01", names, 4, 2, set(), lambda i: "")["all_single_byte"] == 0
    assert score(b"", names, 4, 2, set(), lambda i: "")["values"] == ""
    # a window that runs off the end is refused rather than guessed
    assert score(b"\x40", names, 4, 2, set(), lambda i: "")["values"] == ""
    # the writer-order grammar: one compact, the FriendlyName, then the fields the two bodies name
    tail = bytes(4) + b"\xff" * 8 + bytes(4) + bytes(8) + b"\xff" * 8 + b"\xff\xff" + bytes(4) \
        + (0x12).to_bytes(4, "little") + bytes(TAIL_DWORDS)
    front = compact(0) + compact(1) + tail
    got = parse_fields(front)
    assert got["status"] == "close", got
    assert (got["x"], got["name"], got["flags"]) == (0, 1, 0x12), got
    assert got["span_tail"] == "00" * TAIL_DWORDS and got["span_88"] == "ff" * 8, got
    # one byte long is an overshoot, one byte short is a refusal, and the same walk one byte late never closes
    assert parse_fields(front + b"\x00")["status"] == "ends-1", parse_fields(front + b"\x00")
    assert parse_fields(front[:-1])["status"].startswith("overruns")
    assert parse_fields(front[1:])["status"] != "close"
    # a script length that walks off the front is refused rather than read past it
    bad = compact(0) + compact(1) + bytes(4) + b"\xff" * 8 + (900).to_bytes(4, "little")
    assert "overruns" in parse_fields(bad)["status"], parse_fields(bad)
    print("self-check ok: front values, the declared-name reading, a two-byte value and two refusals;")
    print("               the writer-order walk closes on a synthetic front and refuses a short one")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", help="dump root (the directory holding System/)")
    ap.add_argument("--sweep", action="store_true", help="census every gap row")
    ap.add_argument("-o", "--out", help="artifact directory")
    ap.add_argument("--class", dest="cls", help="one class, `Package.Class` or with --package")
    ap.add_argument("--package", default="", help="package of --class")
    ap.add_argument("--report", action="store_true", help="print the committed artifact's tables")
    ap.add_argument("--grammar", action="store_true", help="read every anchored front in the writer's field order")
    ap.add_argument("--fields-report", action="store_true", help="print the committed front-fields artifact")
    ap.add_argument("--check", action="store_true", help="self-test, no dump")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.report:
        with open(os.path.join(ROOT, "inventory", ARTIFACT), newline="", encoding="utf-8") as fh:
            report(list(csv.DictReader(fh)))
        return 0
    if args.fields_report:
        with open(os.path.join(ROOT, "inventory", FIELDS_ARTIFACT), newline="", encoding="utf-8") as fh:
            report_fields(list(csv.DictReader(fh)))
        return 0
    if not args.dump:
        ap.error("--dump is required (or --check)")
    if args.grammar:
        return fields_one(args) if args.cls else grammar_sweep(args)
    if args.cls:
        return one(args)
    if args.sweep:
        return sweep(args)
    ap.error("pick --sweep, --class or --report")


if __name__ == "__main__":
    sys.exit(main())
