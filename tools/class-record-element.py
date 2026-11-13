#!/usr/bin/env python3
"""The class record's 12-byte element, measured from its writer, and what sits at the array's own tail.

`FUN_0003d900` is the only writer of the array `UClass::Serialize` puts at `+0x4d0`, and its body fixes the element
span twice: both the `Num` and the `Max` word are scaled by twelve before the archive's `+0x14` call
(`LEA r,[r+r*2]; SHL r,2`), and the per-element address is `base + i*12` (`LEA EDX,[EBP+EBP*2]; LEA EAX,[EAX+EDX*4]`).
Inside one element the writer makes three separate archive calls over the three dwords at `+0`, `+4`, `+8`:

    PUSH elem+8 / PUSH elem+4 / PUSH elem / CALL [archive+0x18]   ; the object-reference method
    PUSH ret / CALL 0x00037110                                    ; Serialize(ptr, 4) -- raw dword
    PUSH ret / CALL 0x000370e0                                    ; Serialize(ptr, 4) -- raw dword

so the element is **twelve bytes in memory** and **`ref + 8` bytes on disk**, nine to thirteen wide by the
reference's compact width -- never a fixed twelve. The two trailing writes are not a second codec: `0x00037110` and
`0x000370e0` are the same four-byte primitive (`Serialize(ptr,4)` through archive vtable `+4`, behind a byte-clock
bump), and only their call sites differ.

With that span fixed, the record's residue can be asked about from the bytes instead of guessed: on every class whose
record the array parses (`inventory/class_record_array.csv`), this tool restarts the *same* grammar at the array's
bare tail and tests whether a second array of the same shape plus the fields `UClass::Serialize` still writes -- up to
two name arrays and the config `FName` -- lands exactly on the defaults start. It does on **297 of 549** classes
against **71 of 10,335** positions of the same grammar started inside the following 40 bytes, so the tail position is
a structure and not a free scan finding heads. Two readings remain, and the artifact carries the columns that separate
them: a second array of the writer's shape (which `UClass::Serialize`'s body does not contain -- it has one call to
`FUN_0003d900`), or a record whose 12-byte-record array begins one field later than `prefix_end + 42` on that family.
The `first_zero` / `second_zero` columns are the evidence for the second reading: on the zone family the first parse
is two all-null records while the second carries live references (an export index and an import index).

    python3 tools/class-record-element.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-element.py --class WaterZone --package GamePlay --dump $SG1_DUMP
    python3 tools/class-record-element.py --report
    python3 tools/class-record-element.py --check

Reads the packages and one committed CSV; no boot, no Ghidra, no value decoded.
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
ARTIFACT = "class_record_element.csv"
SOURCE = "class_record_array.csv"          # the array's own measurement: where it starts and where its tail is
ELEMENT = 12                               # `FUN_0003d900`: Num and Max scaled by twelve, address base + i*12
TRAILING = ELEMENT - 4                     # the reference is not raw: two 4-byte writes follow it
MAX_COUNT = 4096
MAX_REF = 5
WINDOW = 40                                # the control's window: every byte of the next 40, same grammar
STATE_BYTES = 22                           # `UState::Serialize` (0x000394c0): 8 + 8 + 2 + 4
CLASS_DWORDS = 5                           # the five dwords `UClass::Serialize` writes after `UState`
DOC_OFFSET = STATE_BYTES + 4 * CLASS_DWORDS
COLUMNS = ["package", "class", "prefix_end", "block_start", "array_end", "first_count", "first_zero",
           "second_count", "second_end", "second_refs_ok", "second_zero", "exact", "tail_orders",
           "ctl_positions", "ctl_exact"]


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


def read_count(buf, p):
    if p < 0 or p >= len(buf):
        return None
    try:
        v, e = read_index(buf, p)
    except IndexError:
        return None
    if v < 0 or v > MAX_COUNT or e > len(buf):
        return None
    return v, e


def walk_array(buf, p):
    """The writer's element stream at `p`: a compact count, then count x (compact reference, TRAILING raw bytes)."""
    got = read_count(buf, p)
    if not got:
        return None
    count, o = got
    if count < 1:
        return None
    refs, widths = [], []
    for _ in range(count):
        if o >= len(buf):
            return None
        try:
            v, e = read_index(buf, o)
        except IndexError:
            return None
        if e - o > MAX_REF or e + TRAILING > len(buf):
            return None
        widths.append(e - o)
        refs.append(v)
        o = e + TRAILING
    return {"count": count, "widths": widths, "refs": refs, "end": o}


def walk_names(buf, p):
    """One name array: a compact count, then that many (name, number) pairs of compact indices."""
    got = read_count(buf, p)
    if not got:
        return None
    n, o = got
    for _ in range(n):
        for _ in range(2):
            if o >= len(buf):
                return None
            try:
                _, e = read_index(buf, o)
            except IndexError:
                return None
            o = e
    return {"count": n, "end": o}


def _tail_walks(buf, p, steps):
    """Every end the writer's remaining fields produce from `p`, one step at a time. The config name at
    `+0x4b0`/`+0x4b4` is two 4-byte fields written through two *different* archive methods (`+0x18` and `+0x1c`
    in `UClass::Serialize`'s body), so its disk width is tried both ways: eight raw bytes, and two compact indices."""
    if not steps:
        yield p
        return
    step, rest = steps[0], steps[1:]
    if step == "names":
        nm = walk_names(buf, p)
        if nm:
            yield from _tail_walks(buf, nm["end"], rest)
    else:
        if p + 8 <= len(buf):
            yield from _tail_walks(buf, p + 8, rest)
        try:
            _, a = read_index(buf, p)
            _, b = read_index(buf, a)
        except IndexError:
            b = None
        if b is not None:
            yield from _tail_walks(buf, b, rest)


def tail_ends(buf, p):
    """Every end the writer's remaining fields produce from `p`: a name array, the config name, a name array, each
    optional and in the source's order (names before or after the config name)."""
    ends = set()
    for names_first in (False, True):
        for fname in (False, True):
            for names_second in (False, True):
                steps = []
                if names_first:
                    steps.append("names")
                if fname:
                    steps.append("fname")
                if names_second and (names_first or fname):
                    steps.append("names")
                if not steps:
                    continue                                   # an empty tail is not a field list
                ends.update(_tail_walks(buf, p, steps))
    return sorted(ends)


def tail_exact(buf, p, block):
    """How many of the writer's field orders land exactly on the defaults start."""
    return tail_ends(buf, p).count(block)


def in_tables(v, nimp, nex):
    return v == 0 or (v > 0 and v <= nex) or (v < 0 and -v <= nimp)


def read_source(path=None):
    """The array's own measurement: (package, class) -> (prefix_end, block_start, array_end, first_count)."""
    path = path or os.path.join(ROOT, "inventory", SOURCE)
    out = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                out[(r["package"], r["class"])] = (int(r["prefix_end"]), int(r["block_start"]),
                                                   int(r["array_end"]), int(r["doc_count"]))
            except (KeyError, TypeError, ValueError):
                continue
    return out


def measure(data, prefix_end, block, array_end, first_count, nimp, nex):
    row = {"prefix_end": prefix_end, "block_start": block, "array_end": array_end, "first_count": first_count,
           "exact": 0, "ctl_positions": 0, "ctl_exact": 0}
    first = walk_array(data, prefix_end + DOC_OFFSET)
    row["first_zero"] = int(bool(first) and not any(first["refs"]))
    second = walk_array(data, array_end)
    if not second:
        return row
    row["second_count"] = second["count"]
    row["second_end"] = second["end"]
    row["second_refs_ok"] = int(all(in_tables(v, nimp, nex) for v in second["refs"]))
    row["second_zero"] = int(not any(second["refs"]))
    hits = tail_exact(data, second["end"], block)
    row["exact"] = int(hits > 0)
    row["tail_orders"] = hits
    ctl_p = ctl_e = 0
    for p in range(array_end + 1, min(block, array_end + 1 + WINDOW)):
        third = walk_array(data, p)
        ctl_p += 1
        if third and tail_exact(data, third["end"], block):
            ctl_e += 1
    row["ctl_positions"] = ctl_p
    row["ctl_exact"] = ctl_e
    return row


def payload(pkg, cls):
    for exp in pkg.exports:
        if pkg.class_name_of(exp) == "Class" and pkg.name(exp["name"]) == cls:
            return pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    return None


def sweep(args):
    dump = args.dump
    src = read_source()
    by_pkg = collections.defaultdict(list)
    for (pkg_name, cls), vals in src.items():
        by_pkg[pkg_name].append((cls, vals))
    rows = []
    for pkg_name in sorted(by_pkg):
        path = os.path.join(dump, "System", pkg_name + ".u")
        if not os.path.exists(path):
            print("missing package %s" % pkg_name, file=sys.stderr)
            continue
        pkg = Package(path)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        nimp, nex = len(pkg.imports), len(pkg.exports)
        cache = {}
        for cls, (prefix_end, block, array_end, first_count) in sorted(by_pkg[pkg_name]):
            if cls not in cache:
                cache[cls] = payload(pkg, cls)
            data = cache[cls]
            if not data:
                continue
            row = {"package": pkg_name, "class": cls}
            row.update(measure(data, prefix_end, block, array_end, first_count, nimp, nex))
            rows.append(row)
        print("%-18s %4d rows" % (pkg_name, len(by_pkg[pkg_name])), flush=True)
    out = os.path.join(args.out, ARTIFACT)
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in COLUMNS})
    print("wrote %s (%d rows)" % (out, len(rows)))
    return 0


def report(path=None):
    path = path or os.path.join(ROOT, "inventory", ARTIFACT)
    rows = list(csv.DictReader(open(path, newline="")))
    parsed = [r for r in rows if r["second_count"]]
    exact = [r for r in parsed if r["exact"] == "1"]
    ctl_p = sum(int(r["ctl_positions"] or 0) for r in parsed)
    ctl_e = sum(int(r["ctl_exact"] or 0) for r in parsed)
    first_zero = [r for r in rows if r["first_zero"] == "1"]
    print("classes                              %d" % len(rows))
    print("a second array parses at the tail    %d" % len(parsed))
    print("...and the writer's fields close it  %d (%.1f%%)"
          % (len(exact), 100.0 * len(exact) / max(1, len(parsed))))
    print("control: same grammar, next %d bytes  %d of %d (%.1f%%)"
          % (WINDOW, ctl_e, ctl_p, 100.0 * ctl_e / max(1, ctl_p)))
    print("the first parse is all-null records  %d" % len(first_zero))
    print("the second parse is all-null records %d of %d"
          % (sum(1 for r in exact if r["second_zero"] == "1"), len(exact)))
    print("the second parse has in-table refs   %d of %d"
          % (sum(1 for r in parsed if r["second_refs_ok"] == "1"), len(parsed)))
    return 0


def one(args):
    src = read_source()
    key = (args.package, args.cls)
    if key not in src:
        print("no array row for %s.%s" % key)
        return 1
    path = os.path.join(args.dump, "System", args.package + ".u")
    pkg = Package(path)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    data = payload(pkg, args.cls)
    if not data:
        print("class not found")
        return 1
    prefix_end, block, array_end, first_count = src[key]
    row = {"package": args.package, "class": args.cls}
    row.update(measure(data, prefix_end, block, array_end, first_count, len(pkg.imports), len(pkg.exports)))
    for k in COLUMNS:
        print("%-16s %s" % (k, row.get(k, "")))
    return 0


def self_check():
    """Synthetic records: the element span, a wide reference, and a tail that closes only when it is exact."""
    # one element: a one-byte reference, then the two raw dwords the writer streams
    elem = bytes([0x02, 0x01, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00])
    assert len(elem) == 1 + TRAILING, "an element is a compact reference plus two raw dwords"
    body = bytes([0x01]) + elem
    a = walk_array(body, 0)
    assert a and a["count"] == 1 and a["end"] == len(body) and a["refs"] == [2], a
    wide = bytes([0x01, 0x44, 0x1c]) + b"\x00" * TRAILING
    b = walk_array(wide, 0)
    assert b and b["widths"] == [2] and b["end"] == len(wide), b
    assert walk_array(bytes([0x00]), 0) is None, "a count of zero is not an array"
    names = bytes([0x02, 0x01, 0x00, 0x02, 0x00])
    fname = b"\x00" * 8
    buf = body + names + fname
    assert tail_exact(buf, len(body), len(buf)) == 1, "names then the config name must close the gap"
    assert tail_exact(buf, len(body), len(buf) + 1) == 0, "a gap one byte too wide must not close"
    two = body + body + names + fname
    w = walk_array(two, len(body))
    assert w and tail_exact(two, w["end"], len(two)) == 1, "a second array plus the tail fields"
    assert tail_exact(two, w["end"], len(two) - 1) == 0, "the control must not fire without the tail"
    print("class-record-element: synthetic records pass (element span, wide reference, tail order, control)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--class", dest="cls")
    ap.add_argument("--package")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.report:
        return report(os.path.join(args.out, ARTIFACT) if args.out != "inventory" else None)
    if args.cls:
        if not (args.dump and args.package):
            ap.error("--class needs --package and --dump")
        return one(args)
    if args.sweep:
        if not args.dump:
            ap.error("--sweep needs --dump")
        return sweep(args)
    ap.error("nothing to do: --sweep, --report, --class or --check")


if __name__ == "__main__":
    sys.exit(main())
