#!/usr/bin/env python3
"""Measure a class record's prefix with the field order `UStruct::Serialize` itself writes.

`docs/NATIVE_VM.md` reads the reader (`0x000392b0`) as: base first, then `Super`, then a walk of `Children`,
then `FriendlyName` (which it asserts is not `NAME_None`), then the source line and text position, then the script
as a size plus that many bytes. The stream-side attempts so far took that order and failed, and the reason given was
that the two references are not compact indices. This tool measures the prefix directly instead, field by field:

* **`super`** -- a compact index at offset 0, compared with the class export's own `super` in the package's export
  table. Falsifiable on every class: a compact decode that lands on the export's super everywhere is not a guess.
* **the field between the two references** -- decoded the same way and required to be **one zero byte**: a null
  object reference. An `FName` there could not be null (the reader asserts `FriendlyName != NAME_None`), so the
  value of this field decides *which* body field the byte belongs to, and it is measured on all classes.
* **`script`** -- a compact index that must resolve, through the package's export table, to the `TextBuffer`
  export outered to this class (`tools/class-record.py` measures the same anchor from the artifact side).

What it adds over that locator is the **widths**: `super_len` and `script_len` are the number of bytes the compact
codec consumed, so the row says how many bytes each reference occupies in this build (`00` is one byte, `6e 01` is
two) rather than assuming four. The prefix's end is then a number, and `middle` is the distance from it to the
defaults block's start where the locator found one -- the part of the record the prefix does not explain.

The last column is the other half of the same question: `tail_fits` scans **every** offset of the record for the
field list `UClass::Serialize` writes *before* the defaults (five dwords, a `TArray` of 12-byte records, a config
`FName`, two `TArray<dword>`s) and reports the offsets where that list ends exactly on the block start. Requiring
the walk to land on a known start is what makes a zero an answer rather than a parse failure.

    python3 tools/class-prefix.py DUMP/System --class AmbientCreatures.HerdAnimal
    python3 tools/class-prefix.py DUMP/System --sweep -o inventory
    python3 tools/class-prefix.py --report
    python3 tools/class-prefix.py --check

Nothing here reads a defaults value: indices, lengths and offsets only.
"""
import argparse
import collections
import csv
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_COUNT = 4096
TAIL_FIELDS = 5          # dwords before the 12-byte record array
RECORD_STRIDE = 12


def read_index(buf, off):
    """UE1/UE2 `FCompactIndex`: bit 7 is the sign, bit 6 means "more bytes", 6 bits then 7 per byte."""
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


def dword(buf, off):
    if off < 0 or off + 4 > len(buf):
        return None
    return struct.unpack_from("<I", buf, off)[0]


def textbuffer_of(pkg):
    """class export index -> index of the `TextBuffer` export outered to it (the class's script text)."""
    out = {}
    for k, exp in enumerate(pkg.exports, start=1):
        if pkg.class_name_of(exp) == "TextBuffer" and exp["outer"] > 0 and exp["size"] > 0:
            out.setdefault(exp["outer"], k)
    return out


def prefix(data):
    """The three references, decoded: ({field: value}, end, ok) -- what the bytes say, not what they should say."""
    out = {"super_at": 0, "super_len": "", "super_value": "", "null_at": "", "null_len": "",
           "null_value": "", "script_at": "", "script_len": "", "script_value": "", "prefix_end": ""}
    try:
        value, end = read_index(data, 0)
    except IndexError:
        return out, "", 0
    out["super_value"], out["super_len"] = value, end
    try:
        nvalue, nend = read_index(data, end)
    except IndexError:
        return out, "", 0
    out["null_at"], out["null_value"], out["null_len"] = end, nvalue, nend - end
    try:
        svalue, send = read_index(data, nend)
    except IndexError:
        return out, "", 0
    out["script_at"], out["script_value"], out["script_len"] = nend, svalue, send - nend
    out["prefix_end"] = send
    return out, send, 1


def tail_fits(data, end):
    """Offsets where the `UClass::Serialize` field list ends exactly on `end`, as (offset, counts, fname).

    The list is five dwords, a `count` + `count` x 12-byte record array, the config `FName`, then two
    `count` + `count` x 4-byte arrays. The counts are tried as 32-bit dwords and as compact indices, and the `FName`
    as the two-dword pair and the compact pair, because those are the readings the body's two virtual calls leave
    open. Every offset is tried, so alignment is not assumed; a walk that overruns `end` is refused.
    """
    hits = []
    strides = (RECORD_STRIDE, 4, 4)      # the two dword arrays carry the config FName between them
    for off in range(0, max(0, end)):
        for counts, fname in (("u32", 8), ("u32", 2), ("compact", 8), ("compact", 2)):
            o, ok = off + 4 * TAIL_FIELDS, True
            if o > end:
                continue
            for i, stride in enumerate(strides):
                if i == 1:
                    o += fname            # the config name sits between the two dword arrays
                if counts == "u32":
                    n = dword(data, o)
                    if n is None or n > MAX_COUNT:
                        ok = False
                        break
                    o += 4 + stride * n
                else:
                    try:
                        n, o = read_index(data, o)
                    except IndexError:
                        ok = False
                        break
                    if n < 0 or n > MAX_COUNT:
                        ok = False
                        break
                    o += stride * n
                if o > end:
                    ok = False
                    break
            if ok and o == end:
                hits.append((off, counts, fname))
    return hits


def sweep(system, out):
    base = os.path.join(ROOT, "inventory")
    shapes = {}
    path = os.path.join(base, "class_records.csv")
    if os.path.isfile(path):
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                shapes[(r["package"], r["class"])] = r
    rows = []
    for pkg_name, pkg in walk(system):
        tb = textbuffer_of(pkg)
        for k, exp in enumerate(pkg.exports, start=1):
            if pkg.class_name_of(exp) != "Class":
                continue
            cls = pkg.name(exp["name"])
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            p, end, ok = prefix(data)
            sh = shapes.get((pkg_name, cls), {})
            start = sh.get("block_start", "")
            start = int(start) if str(start).isdigit() else ""
            row = {
                "package": pkg_name,
                "class": cls,
                "payload": len(data),
                "shape": sh.get("shape", ""),
                "block_start": start,
                "super_index": exp["super"],
                "super_ok": int(ok and p["super_value"] == exp["super"]),
                "super_len": p["super_len"],
                "null_len": p["null_len"],
                "null_is_zero": int(ok and p["null_value"] == 0),
                "script_at": p["script_at"],
                "script_ok": int(bool(tb.get(k)) and p["script_value"] == tb.get(k)),
                "script_len": p["script_len"],
                "prefix_end": p["prefix_end"],
                "middle": (start - p["prefix_end"]) if (start and p["prefix_end"] != "") else "",
                "tail_fits": "",
                "tail_at": "",
                "tail_fits_ctl": "",
            }
            if start and start > p["prefix_end"]:
                hits = tail_fits(data, start)
                row["tail_fits"] = len(hits)
                row["tail_at"] = hits[0][0] if hits else ""
                # the same walk against a shifted end: a fit that is real should not survive it
                row["tail_fits_ctl"] = len(tail_fits(data, start - 8)) if start - 8 > p["prefix_end"] else ""
            rows.append(row)
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "class_prefixes.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("wrote %s: %d rows" % (path, len(rows)))
    report(rows)
    return 0


def report(rows):
    if not rows:
        print("no rows")
        return
    n = len(rows)
    print("class payloads: %d" % n)
    print("  super decodes at offset 0 to the export's super : %d/%d"
          % (sum(int(r["super_ok"]) for r in rows), n))
    lens = collections.Counter(str(r["super_len"]) for r in rows)
    print("  bytes the super reference occupies             : %s" % dict(lens.most_common(5)))
    print("  the field after it is one zero byte            : %d/%d"
          % (sum(1 for r in rows if str(r["null_is_zero"]) == "1" and str(r["null_len"]) == "1"), n))
    print("  the script reference names the class TextBuffer: %d/%d"
          % (sum(int(r["script_ok"]) for r in rows), n))
    ends = collections.Counter(r["prefix_end"] for r in rows)
    print("  prefix end (offset, top five)                  : %s" % dict(ends.most_common(5)))
    located = [r for r in rows if str(r["block_start"]).isdigit() and str(r["block_start"])]
    if located:
        mid = sorted(int(r["middle"]) for r in located if str(r["middle"]) != "")
        print("  located rows: %d, record bytes the prefix does not explain" % len(located))
        if mid:
            print("    min %d, median %d, p90 %d, max %d"
                  % (mid[0], mid[len(mid) // 2], mid[min(len(mid) - 1, (len(mid) * 9) // 10)], mid[-1]))
            print("    starts that fall inside the prefix (impossible): %d" % sum(1 for m in mid if m < 0))
        fits = [r for r in located if str(r["tail_fits"]).isdigit() and int(r["tail_fits"])]
        ctl = [r for r in located if str(r["tail_fits_ctl"]).isdigit() and int(r["tail_fits_ctl"])]
        print("  the UClass field list ends on the block start   : %d/%d" % (len(fits), len(located)))
        if fits:
            at = collections.Counter(str(r["tail_at"]) for r in fits)
            print("    %d distinct offsets, the commonest %d rows (a scattered answer is a coincidence rate)"
                  % (len(at), at.most_common(1)[0][1]))
        print("  the same walk against an end 8 bytes short      : %d/%d" % (len(ctl), len(located)))


def selftest():
    """Synthetic payloads: the prefix must decode as the bytes say, and nothing may be written."""
    # super = 110 (two bytes), a null reference (one byte), script = 332 (two bytes), then filler
    body = bytes.fromhex("6e01" "00" "4c05" "aabbcc")
    p, end, ok = prefix(body)
    assert ok == 1
    assert p["super_value"] == 110 and p["super_len"] == 2, p
    assert p["null_value"] == 0 and p["null_len"] == 1, p
    assert p["script_value"] == 332 and p["script_len"] == 2, p
    assert end == 5, p
    # a negative index (an import) is one byte and keeps its sign
    p, end, ok = prefix(bytes.fromhex("83" "00" "55" "8302"))
    assert ok == 1 and p["super_value"] == -3 and p["super_len"] == 1, p
    assert p["script_value"] == 16597 and p["script_len"] == 3, p
    # a truncated payload is refused, not guessed
    p, end, ok = prefix(b"\x40")
    assert ok == 0 and end == "", p
    # the tail scan: a field list that ends exactly on `end` must be found, and one that does not must not
    tail = struct.pack("<I", 0) * 5 + struct.pack("<I", 2) + b"\x01" * 24 + b"\x00" * 8 \
        + struct.pack("<I", 1) + b"\x00" * 4 + struct.pack("<I", 0)
    data = b"\x01\x02\x03" + tail + b"\xff"
    end = 3 + len(tail)
    hits = tail_fits(data, end)
    assert any(h[0] == 3 for h in hits), hits
    assert tail_fits(data, 3) == [], "a walk longer than the record must not be reported"
    print("class-prefix self-test: ok")
    return 0


def walk(system):
    for name in sorted(os.listdir(system)):
        if not name.endswith(".u"):
            continue
        try:
            pkg = Package(os.path.join(system, name))
            pkg.read_names()
            pkg.read_imports()
            pkg.read_exports()
        except Exception:
            continue
        yield name[:-2], pkg


def one(system, key):
    want_pkg, _, want_cls = key.partition(".")
    for pkg_name, pkg in walk(system):
        if pkg_name != want_pkg:
            continue
        tb = textbuffer_of(pkg)
        for k, exp in enumerate(pkg.exports, start=1):
            if pkg.class_name_of(exp) != "Class" or pkg.name(exp["name"]) != want_cls:
                continue
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            p, end, ok = prefix(data)
            print("%s.%s payload %d, export super %d" % (pkg_name, want_cls, len(data), exp["super"]))
            for field in ("super_at", "super_value", "super_len", "null_at", "null_value", "null_len",
                          "script_at", "script_value", "script_len", "prefix_end"):
                print("  %-12s %s" % (field, p[field]))
            print("  script target  %s (export %s)" % (pkg.name(pkg.exports[tb[k] - 1]["name"]) if tb.get(k) else "-",
                                                       tb.get(k, "-")))
            print("  first 24 bytes %s" % data[:24].hex(" "))
            return 0
    print("no such class: %s" % key)
    return 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("system", nargs="?", help="the dump's System directory")
    ap.add_argument("--class", dest="klass", help="one class, as Package.Class")
    ap.add_argument("--sweep", action="store_true", help="every package -> inventory/class_prefixes.csv")
    ap.add_argument("--report", action="store_true", help="print the totals for a swept artifact")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump")
    ap.add_argument("-o", "--out", default="inventory")
    args = ap.parse_args()
    if args.check:
        return selftest()
    if args.report:
        with open(os.path.join(args.out, "class_prefixes.csv"), newline="", encoding="utf-8") as fh:
            report(list(csv.DictReader(fh)))
        return 0
    if not args.system:
        ap.error("a System directory, --report or --check is required")
    if args.sweep:
        return sweep(args.system, args.out)
    if not args.klass:
        ap.error("--class Package.Class, --sweep, --report or --check")
    return one(args.system, args.klass)


if __name__ == "__main__":
    sys.exit(main())
