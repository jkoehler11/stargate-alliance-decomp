#!/usr/bin/env python3
"""Read the class record's 12-byte-record array at the position its writer puts it in, and walk past its tail.

The code says where the count is, and it is not a field of the record before the body. `UClass::Serialize`
(`0x0003a800`) is the only caller of `FUN_0003d900`, the array's writer: at `0x3a8a6` it passes the archive and
the class object's own member at `+0x4d0`, and `FUN_0003d900` writes that array in place -- first `FUN_000637e0`
on the `TArray`'s `Num` field, so the count is at the array's head, an `FCompactIndex` whenever either archive
flag is set, which a cooked save always is; then one element per unit, an object reference through archive vtable
`+0x18` and two raw dwords. There is no `push count; call operator new` in that path, so a build-side allocation
cannot be where the count is written, and the earlier walks were asking the question the writer answers
differently: the count is the array's *first* field, adjacent to its first element, and it counts the whole array.

That fixes the array's position too. `UClass::Serialize` writes `UStruct::Serialize` first (the record's 3-6 byte
prefix, measured) and then, unconditionally, `UState::Serialize`'s 22 bytes and five dwords -- so the array's head
is at `prefix_end + 42` on every class, not wherever a scan likes. The bytes after it are fields, not the end of
the record: `FUN_0003d160` on `+0x4dc` (a `TArray<FName>`: a compact count then two compact indices per element)
and, on version > `0x62`, the same again on `+0x4f4`. The version gate means the number of name arrays (`k`) is
0, 1 or 2, and until now the walk assumed both:

    prefix_end + 42:  [compact count c] [c x (compact reference, dword, dword)] [k name arrays]

so the tool reads the array at that position for every `k` and reports where the walk lands against the block
start. A walk that lands **exactly** on it is the field order confirming itself from the bytes and the array
sized from the record; a walk that falls short leaves a residue, and the residue's own distribution is the next
field's width.

Two controls, because eleven-byte elements fit almost anything:

* the same walk one byte later (`prefix_end + 43`): if the position is the writer's, this must do worse.
* the same grammar from *every* byte position of the middle (`heads`, with `ctl_heads` = the same aimed one byte
  short of the block start). It is reported as a census, not as a locator: a free scan finds heads everywhere,
  which is why the three earlier readings could not settle this from the bytes alone.

    python3 tools/class-record-array.py --dump $SG1_DUMP --class AmbientCreatures.HerdAnimal
    python3 tools/class-record-array.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-array.py --report
    python3 tools/class-record-array.py --check

Reads the packages and the committed prefix table; no boot, no value decoded.
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
mrm = _load("class-record-middle")

ROOT = os.path.dirname(HERE)
STATE_BYTES = crf.STATE_BYTES                 # UState::Serialize, 22 bytes at +0x80
CLASS_DWORDS = crf.CLASS_DWORDS               # five dwords at +0x498..+0x4ac
DOC_OFFSET = STATE_BYTES + 4 * CLASS_DWORDS   # where UClass::Serialize's own tail starts: +0x4d0
ALT_OFFSET = STATE_BYTES                      # the same array with the dword group *after* it, i.e. the position
                                              # a reading that swaps the two fields would use
MAX_COUNT = 4096                              # a 12-byte-record array larger than this is not a class tail
MAX_NAMES = 4096                              # same bound for a name array
MAX_REF = 5                                   # the writer's own bound for a compact object reference
TAILS = (0, 1, 2)                             # name arrays after the record array: version-gated, so all are tried
ARTIFACT = "class_record_array.csv"
COLUMNS = ["package", "class", "payload", "prefix_end", "block_start", "middle",
           "doc_count", "doc_k", "doc_end", "doc_residue", "doc_refs_ok", "doc_exact", "doc_ctl_exact",
           "alt_count", "alt_exact",
           "heads", "head_at", "head_off", "count", "tails", "refs_ok",
           "run_units", "run_inside", "ctl_heads"]


def read_count(buf, p):
    """A compact count at `p`, or None when the bytes run out or the value cannot be an array's size."""
    if p < 0 or p >= len(buf):
        return None
    try:
        v, e = crf.read_index(buf, p)
    except IndexError:
        return None
    if v < 0 or v > MAX_COUNT or e > len(buf):
        return None
    return v, e


def walk_array(buf, p):
    """The record array at `p` as the writer streams it: a count, then count x (reference, dword, dword)."""
    got = read_count(buf, p)
    if not got:
        return None
    count, o = got
    if count < 1:
        return None
    widths, refs = [], []
    for _ in range(count):
        if o >= len(buf):
            return None
        try:
            v, e = crf.read_index(buf, o)
        except IndexError:
            return None
        if e - o > MAX_REF or e + 8 > len(buf):
            return None
        widths.append(e - o)
        refs.append(v)
        o = e + 8
    return {"count": count, "widths": widths, "refs": refs, "end": o}


def walk_names(buf, p):
    """One `TArray<FName>` at `p`: a compact count, then that many (name, number) pairs."""
    got = read_count(buf, p)
    if not got:
        return None
    n, o = got
    if n > MAX_NAMES:
        return None
    for _ in range(n):
        for _ in range(2):
            if o >= len(buf):
                return None
            try:
                _, e = crf.read_index(buf, o)
            except IndexError:
                return None
            o = e
    return {"count": n, "end": o}


def walk(buf, p, tails):
    """The array at `p` followed by `tails` name arrays; the end offset, or None if the grammar runs out."""
    arr = walk_array(buf, p)
    if not arr:
        return None
    o = arr["end"]
    elems = []
    for _ in range(tails):
        nm = walk_names(buf, o)
        if not nm:
            return None
        elems.append(nm["count"])
        o = nm["end"]
    return {"count": arr["count"], "widths": arr["widths"], "refs": arr["refs"], "tails": tails,
            "tail_elems": elems, "end": o}


def in_tables(v, nimp, nex):
    return v == 0 or (v > 0 and v <= nex) or (v < 0 and -v <= nimp)


def best_walk(buf, p, end):
    """The array at `p` with the `k` whose walk lands nearest `end`, preferring an exact landing."""
    best = None
    for t in TAILS:
        w = walk(buf, p, t)
        if not w:
            continue
        w["residue"] = end - w["end"]
        if best is None or (w["residue"] == 0 and best["residue"] != 0) \
                or (abs(w["residue"]) < abs(best["residue"])) \
                or (abs(w["residue"]) == abs(best["residue"]) and w["tails"] < best["tails"]):
            best = w
    return best


def free_heads(buf, start, end):
    """Every head in [start, end) whose array-plus-tail walk lands exactly on `end` -- a census, not a locator."""
    out = []
    for p in range(start, end):
        w = walk_array(buf, p)
        if not w:
            continue
        for t in TAILS:
            if t:
                nm_end = w["end"]
                ok = True
                for _ in range(t):
                    nm = walk_names(buf, nm_end)
                    if not nm:
                        ok = False
                        break
                    nm_end = nm["end"]
                if not ok or nm_end != end:
                    continue
            elif w["end"] != end:
                continue
            out.append(p)
            break
    return out


def measure(data, prefix_end, block_start, nimp, nex):
    row = {"payload": len(data), "prefix_end": prefix_end, "block_start": block_start,
           "middle": block_start - prefix_end,
           "doc_count": "", "doc_k": "", "doc_end": "", "doc_residue": "", "doc_refs_ok": 0,
           "doc_exact": 0, "doc_ctl_exact": 0, "alt_count": "", "alt_exact": 0,
           "heads": 0, "head_at": "", "head_off": "", "count": "", "tails": "", "refs_ok": 0,
           "run_units": "", "run_inside": "", "ctl_heads": 0}
    if block_start <= prefix_end:
        return row
    # 1. the documented position: prefix, UState's 22 bytes, five dwords, then this array
    doc = best_walk(data, prefix_end + DOC_OFFSET, block_start)
    if doc:
        row.update({"doc_count": doc["count"], "doc_k": doc["tails"], "doc_end": doc["end"],
                    "doc_residue": doc["residue"], "doc_exact": int(doc["residue"] == 0),
                    "doc_refs_ok": 1 if all(in_tables(v, nimp, nex) for v in doc["refs"]) else 0})
    alt = best_walk(data, prefix_end + ALT_OFFSET, block_start)
    if alt:
        row.update({"alt_count": alt["count"], "alt_exact": int(alt["residue"] == 0)})
    ctl = best_walk(data, prefix_end + DOC_OFFSET + 1, block_start)
    row["doc_ctl_exact"] = int(bool(ctl) and ctl["residue"] == 0)
    # 2. the free census, and the same grammar aimed one byte short
    heads = free_heads(data, prefix_end, block_start)
    row["heads"] = len(heads)
    if heads:
        p = min(heads)
        best = best_walk(data, p, block_start)
        row.update({"head_at": p, "head_off": p - prefix_end, "count": best["count"],
                    "tails": best["tails"],
                    "refs_ok": 1 if all(in_tables(v, nimp, nex) for v in best["refs"]) else 0})
    row["ctl_heads"] = len(free_heads(data, prefix_end, block_start - 1))
    # 3. the stride-9 run the three previous notes measured, and whether it sits inside the documented array
    units, run_start, shift, _ = mrm.longest_run(data[prefix_end:block_start])
    if units >= mrm.MIN_RUN:
        row["run_units"] = units
        if row["doc_count"] != "":
            run_end = run_start + units * mrm.UNIT
            # the run lies inside the array's own element span, in middle coordinates
            row["run_inside"] = int(DOC_OFFSET + len(_compact(data, prefix_end + DOC_OFFSET)) <= run_start
                                    and run_end <= int(row["doc_end"]) - prefix_end)
    return row


def _compact(buf, p):
    try:
        _, e = crf.read_index(buf, p)
    except IndexError:
        return b""
    return buf[p:e]


def sweep(args):
    prefixes = crf.read_prefixes()
    rows = []
    for fn in sorted(os.listdir(os.path.join(args.dump, "System"))):
        if not fn.endswith(".u"):
            continue
        pkg = Package(os.path.join(args.dump, "System", fn))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        name = fn[:-2]
        nimp, nex = len(pkg.imports), len(pkg.exports)
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
            row = measure(data, start, block, nimp, nex)
            row.update({"package": name, "class": key[1]})
            rows.append(row)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
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
    print("classes with a located block start: %d" % n)
    doc = [r for r in rows if r["doc_count"] not in ("", None)]
    exact = [r for r in doc if int(r["doc_exact"])]
    print("the array at the documented position (prefix + %d) walks to the block start exactly: %d of %d (%.1f%%)"
          % (DOC_OFFSET, len(exact), len(doc), _pct(len(exact), len(doc))))
    print("  with every element reference inside the package's tables: %d"
          % sum(int(r["doc_refs_ok"]) for r in exact))
    print("the same array with the dword group moved after it (prefix + %d): a walk on %d (%.1f%%), exact %d"
          % (ALT_OFFSET, sum(1 for r in doc if r["alt_count"] not in ("", None)),
             _pct(sum(1 for r in doc if r["alt_count"] not in ("", None)), len(doc)), sum(int(r["alt_exact"]) for r in doc)))
    print("the same walk one byte later (prefix + %d): %d of %d (%.1f%%)"
          % (DOC_OFFSET + 1, sum(int(r["doc_ctl_exact"]) for r in doc), len(doc),
             _pct(sum(int(r["doc_ctl_exact"]) for r in doc), len(doc))))
    if doc:
        print("  name arrays after the array on the exact rows: %s"
              % collections.Counter(str(r["doc_k"]) for r in exact).most_common(4))
        print("  the count on the exact rows: median %d, most common %s"
              % (statistics.median(_ints(exact, "doc_count")) if exact else 0,
                 collections.Counter(int(r["doc_count"]) for r in exact).most_common(6)))
        res = _ints(doc, "doc_residue")
        print("  where the walk stops, relative to the block start: median %d, most common %s"
              % (statistics.median(res), collections.Counter(res).most_common(10)))
        print("  name arrays on all rows (nearest landing): %s"
              % collections.Counter(str(r["doc_k"]) for r in doc).most_common(4))
    head = [r for r in rows if int(r["heads"])]
    print("the same grammar from every byte position of the middle: a head on %d of %d (%.1f%%); aimed one byte "
          "short of the block start, %d (%.1f%%)"
          % (len(head), n, _pct(len(head), n), sum(1 for r in rows if int(r["ctl_heads"])),
             _pct(sum(1 for r in rows if int(r["ctl_heads"])), n)))
    runs = [r for r in rows if r["run_units"] not in ("", None)]
    if runs:
        print("the stride-%d run: %d classes; the documented array's walk reaching it on %d"
              % (mrm.UNIT, len(runs), sum(1 for r in runs if int(r["run_inside"] or 0))))
        inside = [r for r in runs if int(r["doc_exact"])]
        print("  of the %d exact rows, %d also hold the run" % (len(exact), len(inside)))


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
        row = measure(data, start, block, len(pkg.imports), len(pkg.exports))
        for k in COLUMNS:
            print("%-16s %s" % (k, row.get(k, "")))
        for t in TAILS:
            w = walk(data, start + DOC_OFFSET, t)
            if w:
                print("prefix+%d, k=%d: count %d, widths %s, end %d (residue %d), refs %s"
                      % (DOC_OFFSET, t, w["count"], collections.Counter(w["widths"]).most_common(3),
                         w["end"], block - w["end"], w["refs"][:8]))
        return 0
    print("class not found")
    return 1


def self_check():
    """Synthetic records: an array plus one name array at the documented position, and three variants that must fail."""

    def compact(v):
        v = abs(v)
        out, more = v & 0x3F, v >> 6
        if not more:
            return bytes([out])
        return bytes([0x40 | out]) + compact(more)

    def element(ref, d1, d2):
        return compact(ref) + d1 + d2

    a, b = b"\x11\x22\x33\x44", b"\x55\x66\x77\x88"
    prelude = bytes(range(1, STATE_BYTES + 1)) + b"\x01\x00\x00\x00" * CLASS_DWORDS
    elems = b"".join(element(ref, a, b) for ref in (0x01, 0x02, 0x07, 300))
    names = compact(2) + compact(3) + compact(0) + compact(4) + compact(1)
    body = compact(4) + elems + names
    prefix = compact(110) + b"\x00" + compact(5)
    payload = prefix + prelude + body + bytes(6)
    mid_start = len(prefix)
    block = len(prefix) + len(prelude) + len(body)

    row = measure(payload, mid_start, block, 64, 4096)
    assert int(row["doc_exact"]) == 1 and int(row["doc_count"]) == 4 and int(row["doc_k"]) == 1, row
    assert int(row["doc_refs_ok"]) == 1 and int(row["doc_ctl_exact"]) == 0, row

    # a count of 5 cannot consume the same bytes, so the row is not exact and its residue says so
    wrong = prefix + prelude + compact(5) + elems + names + bytes(6)
    row2 = measure(wrong, mid_start, block, 64, 4096)
    assert not int(row2["doc_exact"]) and int(row2["doc_residue"]) != 0, row2

    # a reference outside the package's tables is reported, not hidden
    wide = prefix + prelude + compact(4) + b"".join(element(ref, a, b) for ref in (9000, 9001, 9002, 9003)) \
        + names + bytes(6)
    row3 = measure(wide, mid_start, block, 64, 4096)
    assert not int(row3["doc_refs_ok"]) and int(row3["doc_count"]) == 4, row3

    # version gate: a record with no name array is `k=0`, and the walk must land on it too
    bare = prefix + prelude + compact(4) + elems
    row4 = measure(bare + bytes(6), mid_start, len(prefix) + len(prelude) + 1 + len(elems), 64, 4096)
    assert int(row4["doc_exact"]) == 1 and int(row4["doc_k"]) == 0, row4
    print("self-check ok: a 4-element array plus one name array is found at prefix+%d with count 4 and k=1 ending "
          "exactly on the block start, one byte later finds nothing, a count of 5 leaves a residue, a walk that "
          "leaves the package's tables is reported, and a record with no name array reads as k=0" % DOC_OFFSET)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", help="dump root (the directory holding System/, Maps/, ...)")
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
