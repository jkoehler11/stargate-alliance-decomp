#!/usr/bin/env python3
"""Locate the class record's array head by walking the element grid back from the block start, with no group.

`tools/class-record-array-search.py` locates the head from the block side, but only through the record's closing group
(a names array, a reference, a config name and a version-gated list): a head is a position `p` whose element walk is
followed by a group that lands exactly on `block_start`. That is an exact test, and it answers on 1,616 of the 1,626
rows the gap-front census speaks on -- but **none** of the 521 silent rows that carry a `class_tail_run.csv` row
parses as such a group, so 565 located rows have no head at all.

Those rows are not unreachable, because the block start itself is an anchor: the tags of the class's defaults stream
begin there, so the record's last element must *end* there (or a few bytes before it, if a group is present but does
not parse as names-ref-name-list). So the array can be read backwards:

    block_start  <-  end of the last element
                 <-  one element back at a time:  reference (1-5 bytes, in the package's tables) + two dwords
                 <-  a compact count `p` in [prefix + 42, prefix + 42 + 40) whose decode ends where the walk is and
                     whose value equals the number of elements walked

The walk is the one `tools/class-record-walkback.py` already runs over this record's elements (a compact object
reference plus two dwords, so the stride is 9 to 13 bytes); what is new is the anchor -- the block start instead of a
dword run. The tool tries every start in `[block_start - 13, block_start]`, because a closing group costs 6-11 bytes,
and reports every `(start offset, head, count)` it finds rather than picking one by preference. A start 1 byte past
the block start is the null: a variable-stride walk is cheap to fit, so a rule that finds a head there as often as at
the block start has no discriminating power.

The same walk is run over a widened window as well, `[block_start - 40, block_start]`, so every row carries both
answers: a start further back is outside the closing group's 6-11 bytes, and the widened null -- a start past its own
far end -- is the control for whether the extra room is doing work or just fitting noise.

The control is the 1,399 rows the group parse resolved uniquely: the walk must reproduce that same head.

    python3 tools/class-record-array-blind.py --dump $SG1_DUMP --class AmbientCreatures.TransientAmbientPawn
    python3 tools/class-record-array-blind.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-array-blind.py --report
    python3 tools/class-record-array-blind.py --check          # synthetic, no dump, writes nothing

Reads the packages and two committed tables; no boot, no Ghidra round, no property name or value decoded.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402


def _load(name, mod):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, mod + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


crf = _load("crf", "class-record-fields")
ps = _load("ps", "prop-stream")

DOC_OFFSET = 42          # prefix + 42: the shipped walk's position, i.e. the front
WINDOW = 40              # how far past it a head may sit
BACK = 13                # how far behind the block start the last element may end
WIDE = 40                # the same window widened: three times the group's 6-11 bytes, inside the 64-byte trailer
MAX_COUNT = 4096
MAX_REF = 5
DWORDS = 8               # the two raw dwords every element carries after its reference
SEARCH_ARTIFACT = "class_record_array_search.csv"
ARTIFACT = "class_record_array_blind.csv"
COLUMNS = ["package", "class", "payload", "prefix_end", "block_start", "search_cands", "known_head",
           "known_residue", "census_head", "heads", "offs", "head", "off", "count", "depth", "head_off",
           "max_depth", "counts_seen", "stream_close", "null_heads", "matched", "census_matched",
           "heads_z16", "head_z16", "census_z16",
           "wide_heads", "wide_z16", "wide_head", "wide_off", "wide_count", "wide_head_off", "wide_null",
           "wide_census_matched", "verdict"]


def in_tables(v, nimp, nex):
    return v == 0 or 0 < v <= nex or (v < 0 and -v <= nimp)


def step_back(data, s, nimp, nex):
    """Every element boundary the bytes just before `s` could be: the reference must decode to exactly its width."""
    out = []
    for w in range(1, MAX_REF + 1):
        e = s - DWORDS - w
        if e < 0:
            continue
        try:
            v, e2 = crf.read_index(data, e)
        except IndexError:
            continue
        if e2 != s - DWORDS:          # the decode must consume exactly w bytes, i.e. the reference ends at the dwords
            continue
        if not in_tables(v, nimp, nex):
            continue
        out.append(e)
    return out


ZEROS = 16               # the front's sixteen zero bytes, measured by the gap-front census


def window_heads(data, s, walked, prefix_end):
    """A head is a compact count in the window whose decode ends at `s` and whose value is the elements walked."""
    out = []
    if walked < 1:
        return out
    for p in range(prefix_end + DOC_OFFSET, min(prefix_end + DOC_OFFSET + WINDOW, s)):
        try:
            n, e = crf.read_index(data, p)
        except IndexError:
            continue
        if e == s and 1 <= n <= MAX_COUNT and n == walked:
            out.append((p, n, int(data[max(0, p - ZEROS):p] == bytes(ZEROS))))
    return out


def window_counts(data, prefix_end, s):
    """Every compact value in the window whose decode ends at `s`, whatever its value: a count field, or not."""
    out = []
    for p in range(prefix_end + DOC_OFFSET, min(prefix_end + DOC_OFFSET + WINDOW, s)):
        try:
            n, e = crf.read_index(data, p)
        except IndexError:
            continue
        if e == s:
            out.append((p, n))
    return out


def walk(data, start, prefix_end, nimp, nex):
    """Walk the element grid back from `start`; return every head, the deepest depth, and the counts seen."""
    heads, saw = [], set()
    max_depth, seen, stack = 0, {}, [(start, 0)]
    while stack:
        s, walked = stack.pop()
        if walked > max_depth:
            max_depth = walked
        if walked and prefix_end + DOC_OFFSET < s < prefix_end + DOC_OFFSET + WINDOW + MAX_REF + 1:
            for p, n, z in window_heads(data, s, walked, prefix_end):
                heads.append((p, n, z))
            for p, n in window_counts(data, prefix_end, s):
                saw.add((p, n))
        if seen.get(s, -1) >= walked:
            continue
        seen[s] = walked
        if s > prefix_end + DOC_OFFSET:
            for e in step_back(data, s, nimp, nex):
                if e >= prefix_end + DOC_OFFSET:
                    stack.append((e, walked + 1))
    return heads, max_depth, saw


def scan(data, block, prefix_end, nimp, nex, back=BACK):
    """Every start in [block - back, block] -- a closing group costs 6-11 bytes -- plus one byte past the block."""
    found = []
    max_depth = 0
    saw = set()
    for off in range(0, back + 1):
        s = block - off
        if s - DWORDS - MAX_REF < prefix_end + DOC_OFFSET:
            continue
        heads, depth, counts = walk(data, s, prefix_end, nimp, nex)
        max_depth = max(max_depth, depth)
        saw |= counts
        for p, n, z in heads:
            if (off, p) not in [(o, q) for o, q, _n, _z in found]:
                found.append((off, p, n, z))
    null_heads, null_depth, _ = walk(data, block + 1, prefix_end, nimp, nex)
    return found, max_depth, len(saw), len(null_heads), null_depth


def measure(data, prefix_end, block, nnames, nimp, nex, pkg):
    row = {"payload": len(data), "prefix_end": prefix_end, "block_start": block,
           "heads": 0, "offs": "", "head": "", "off": "", "count": "", "depth": "", "head_off": "",
           "max_depth": "", "counts_seen": "", "stream_close": "", "null_heads": "", "matched": "",
           "heads_z16": "", "head_z16": "", "census_z16": "", "wide_heads": "", "wide_z16": "",
           "wide_head": "", "wide_off": "", "wide_count": "", "wide_head_off": "", "wide_null": "",
           "wide_census_matched": "", "verdict": ""}
    found, max_depth, counts_seen, null_heads, _ = scan(data, block, prefix_end, nimp, nex)
    row["counts_seen"] = counts_seen
    row["max_depth"] = max_depth
    row["null_heads"] = null_heads
    try:
        _props, send, reason = ps.read_block(data[block:], pkg, False)
        row["stream_close"] = int(reason == "None" and block + send == len(data))
    except Exception:
        row["stream_close"] = 0
    wide, _wd, _wc, _wn, _wnull = scan(data, block, prefix_end, nimp, nex, WIDE)
    wide_null = len(walk(data, block + WIDE + 1, prefix_end, nimp, nex)[0])
    row["_wide"] = wide
    row["wide_heads"] = len(wide)
    row["wide_z16"] = sum(z for _o, _p, _n, z in wide)
    row["wide_null"] = wide_null
    if wide:
        wpick = next((f for f in wide if f[3]), wide[0])
        row.update({"wide_head": wpick[1], "wide_off": wpick[0], "wide_count": wpick[2],
                    "wide_head_off": wpick[1] - prefix_end - DOC_OFFSET})
    row["heads"] = len(found)
    row["heads_z16"] = sum(z for _o, _p, _n, z in found)
    if not found:
        row["verdict"] = "no-head"
        return row, found
    row["offs"] = " ".join(str(o) for o, _p, _n, _z in found)
    pick = next((f for f in found if f[3]), found[0])   # a head whose sixteen-byte front is there, else the first
    row.update({"head": pick[1], "off": pick[0], "count": pick[2], "head_z16": pick[3],
                "depth": pick[2], "head_off": pick[1] - prefix_end - DOC_OFFSET})
    if row["heads_z16"] == 1:
        row["verdict"] = "unique"
    elif len(found) == 1:
        row["verdict"] = "sole"
    else:
        row["verdict"] = "ambiguous"
    return row, found


def read_search():
    with open(os.path.join(ROOT, "inventory", SEARCH_ARTIFACT), newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def sweep(args):
    ref = read_search()
    by_pkg = collections.defaultdict(list)
    for r in ref:
        if r["block_start"] and int(r["block_start"]) > 0:
            by_pkg[r["package"]].append(r)
    rows = []
    for name in sorted(by_pkg):
        pkg = Package(os.path.join(args.dump, "System", name + ".u"))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        nnames, nimp, nex = len(pkg.names), len(pkg.imports), len(pkg.exports)
        exps = {}
        for e in pkg.exports:
            if pkg.class_name_of(e) == "Class":
                exps.setdefault(pkg.name(e["name"]), e)
        for r in by_pkg[name]:
            e = exps.get(r["class"])
            if e is None:
                continue
            data = pkg.data[e["offset"]:e["offset"] + e["size"]]
            head = int(r["head"]) if r.get("head") else None
            row, found = measure(data, int(r["prefix_end"]), int(r["block_start"]),
                                 nnames, nimp, nex, pkg)
            cens = int(r["census_head"]) if r.get("census_head") else None
            row.update({"package": name, "class": r["class"], "search_cands": r["cands"],
                        "known_head": "" if head is None else head,
                        "known_residue": r.get("residue", ""),
                        "census_head": "" if cens is None else cens})
            row.setdefault("census_matched", "")
            row.setdefault("census_z16", "")
            wide = row.get("_wide") or []
            row["wide_census_matched"] = "" if cens is None else int(any(p == cens for _o, p, _n, _z in wide))
            if cens is not None:
                row["census_matched"] = int(any(p == cens for _o, p, _n, _z in found))
                row["census_z16"] = int(any(p == cens and z for _o, p, _n, z in found))
            if head is not None:
                row["matched"] = int(any(p == head for _o, p, _n, _z in found))
                if row["verdict"] == "no-head":
                    row["verdict"] = "no-head"
                elif row["matched"]:
                    row["verdict"] = "match"
            rows.append(row)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in COLUMNS})
        print("wrote %s: %d rows" % (path, len(rows)))
    report(rows)
    return rows


def _share(n, d):
    return "%.1f%%" % (100.0 * n / d) if d else "-"


def report(rows):
    allrows = list(rows)
    blind = [r for r in allrows if r["verdict"] in ("no-head", "unique", "sole", "ambiguous")
             and r.get("known_head") in ("", None)]
    control = [r for r in allrows if r.get("known_head") not in ("", None)]
    print("rows: %d; blind (no head from the group parse): %d; control (a known head): %d"
          % (len(allrows), len(blind), len(control)))
    for label, rs in (("control", control), ("blind", blind)):
        if not rs:
            continue
        found = [r for r in rs if int(r["heads"])]
        one = [r for r in rs if int(r["heads"]) == 1]
        z16 = [r for r in rs if int(r["heads_z16"] or 0)]
        z16one = [r for r in rs if int(r["heads_z16"] or 0) == 1]
        print("  %s: n=%d; a head on %d (%s), exactly one candidate on %d; a head whose sixteen-byte front is "
              "there on %d (%s), exactly one such on %d"
              % (label, len(rs), len(found), _share(len(found), len(rs)), len(one),
                 len(z16), _share(len(z16), len(rs)), len(z16one)))
        if found:
            print("    start offsets that worked: %s"
                  % collections.Counter(o for r in found for o in r["offs"].split()).most_common(8))
            print("    walk depth at the head: median %s; heads per row: %s"
                  % (_median([int(r["depth"]) for r in found]), collections.Counter(int(r["heads"]) for r in found).most_common(5)))
        if label == "control":
            print("    the group parse's head is reproduced on %d of %d (%s)"
                  % (sum(int(r["matched"] or 0) for r in rs), len(rs),
                     _share(sum(int(r["matched"] or 0) for r in rs), len(rs))))
            cens = [r for r in rs if r.get("census_head") not in ("", None)]
            print("    the gap-front census head is reproduced on %d of %d (%s)"
                  % (sum(int(r["census_matched"] or 0) for r in cens), len(cens),
                     _share(sum(int(r["census_matched"] or 0) for r in cens), len(cens))))
            print("    the census head carries the sixteen-byte front on %d of %d (%s)"
                  % (sum(int(r["census_z16"] or 0) for r in cens), len(cens),
                     _share(sum(int(r["census_z16"] or 0) for r in cens), len(cens))))
            print("    head offset past prefix+%d, on the census-matched rows: %s"
                  % (DOC_OFFSET, collections.Counter(int(r["census_head"]) - int(r["prefix_end"]) - DOC_OFFSET
                                                    for r in rs
                                                    if int(r.get("census_matched") or 0)).most_common(8)))
            print("    sixteen-byte fronts per row: %s"
                  % collections.Counter(int(r["heads_z16"]) for r in rs).most_common(6))
            print("    widened to %d bytes: the census head is reproduced on %d of %d (%s)"
                  % (WIDE, sum(int(r["wide_census_matched"] or 0) for r in cens), len(cens),
                     _share(sum(int(r["wide_census_matched"] or 0) for r in cens), len(cens))))
        if label == "blind":
            print("    head offset past prefix+%d on the unique ones: %s"
                  % (DOC_OFFSET, collections.Counter(int(r["head_off"]) for r in rs
                                                    if r["verdict"] == "unique").most_common(8)))
            print("    counts on the unique ones: %s"
                  % collections.Counter(int(r["count"]) for r in rs
                                        if r["verdict"] == "unique").most_common(6))
        wide_found = [r for r in rs if int(r["wide_heads"] or 0)]
        wide_z16 = [r for r in rs if int(r["wide_z16"] or 0)]
        gained = [r for r in rs if int(r["wide_z16"] or 0) and not int(r["heads_z16"] or 0)]
        print("    widened to %d bytes: a head on %d, with the front on %d, and %d rows gain the front that the "
              "%d-byte window did not have" % (WIDE, len(wide_found), len(wide_z16), len(gained), BACK))
    nulls = [r for r in allrows if int(r["null_heads"] or 0)]
    print("  the null (a start one byte past the block) finds a head on %d of %d (%s)"
          % (len(nulls), len(allrows), _share(len(nulls), len(allrows))))
    wide_nulls = [r for r in allrows if int(r["wide_null"] or 0)]
    print("  the widened window's null (a start past its own far end) finds a head on %d of %d (%s)"
          % (len(wide_nulls), len(allrows), _share(len(wide_nulls), len(allrows))))
    closes = [r for r in allrows if int(r["stream_close"] or 0)]
    print("  the defaults stream closes on the payload end at the block start on %d of %d (%s)"
          % (len(closes), len(allrows), _share(len(closes), len(allrows))))
    for v in ("match", "unique", "sole", "ambiguous", "no-head"):
        n = sum(1 for r in allrows if r["verdict"] == v)
        if n:
            print("  verdict %-10s %d" % (v, n))


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return "-"
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2.0


def one(args):
    ref = read_search()
    r = next((x for x in ref if x["package"] == args.package and x["class"] == args.cls), None)
    if not r:
        print("no located row for %s.%s" % (args.package, args.cls))
        return 1
    pkg = Package(os.path.join(args.dump, "System", args.package + ".u"))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    for e in pkg.exports:
        if pkg.class_name_of(e) == "Class" and pkg.name(e["name"]) == args.cls:
            data = pkg.data[e["offset"]:e["offset"] + e["size"]]
            row, found = measure(data, int(r["prefix_end"]), int(r["block_start"]),
                                 len(pkg.names), len(pkg.imports), len(pkg.exports), pkg)
            for k in COLUMNS:
                if k in row:
                    print("%-12s %s" % (k, row[k]))
            print("heads:")
            for o, p, n, z in found:
                print("  start -%-2d head %d count %d depth %d front16 %d" % (o, p, n, n, z))
            return 0
    print("class not found")
    return 1


def self_check():
    """Synthetic: the head is only reachable by walking back from the block start, with and without a group."""

    def compact(v):
        v = abs(v)
        out, more = v & 0x3F, v >> 6
        if not more:
            return bytes([out])
        return bytes([0x40 | out]) + compact(more)

    prefix = compact(110) + b"\x00" + compact(5)
    prelude = bytes(range(1, 43))
    front = bytes([2, 0, 0, 4])
    zeros = bytes(16)
    elems = b"".join(compact(ref) + b"\x01\x00\x00\x00\x02\x00\x00\x00" for ref in (1, 2, 3))
    array = compact(3) + elems
    head = len(prefix) + DOC_OFFSET + len(front) + len(zeros)

    # no group: the array ends where the tags begin
    payload = prefix + prelude + front + zeros + array
    row, found = measure(payload, len(prefix), len(payload), 64, 64, 4096, None)
    assert any(p == head and n == 3 and z for _o, p, n, z in found), found
    assert int(row["off"] or 0) in range(0, BACK + 1), row
    assert row["verdict"] == "unique", row
    assert row["matched"] == "", row

    # a group of 7 bytes between the array and the tags: the walk must start 7 bytes back
    group = compact(2) + compact(1) + compact(2) + compact(3) + compact(7) + compact(0)
    payload2 = prefix + prelude + front + zeros + array + group
    row2, found2 = measure(payload2, len(prefix), len(payload2), 64, 64, 4096, None)
    assert any(p == head and n == 3 for _o, p, n, _z in found2), found2
    assert any(o == len(group) and p == head for o, p, _n, _z in found2), found2

    # nothing to find: the count says 4 and only three elements are there
    payload3 = prefix + prelude + bytes(30) + compact(4) + elems
    row3, found3 = measure(payload3, len(prefix), len(payload3), 64, 64, 4096, None)
    assert not any(n == 3 for _o, _p, n, _z in found3), found3
    assert int(row3["counts_seen"] or 0) >= 1, row3

    print("self-check ok: the head is found by walking back from the block start with no group, the same head at a "
          "7-byte group offset, and a short prelude yields no count-3 head")
    print("  (null: %s, stream close: %s)" % (row["null_heads"], row["stream_close"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", help="dump root (the directory holding System/)")
    ap.add_argument("--class", dest="cls", help="one class, with --package")
    ap.add_argument("--package", default="", help="package of --class")
    ap.add_argument("--sweep", action="store_true", help="walk every located class")
    ap.add_argument("-o", "--out", help="artifact directory")
    ap.add_argument("--report", action="store_true", help="summarise the committed artifact")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.report:
        with open(os.path.join(ROOT, "inventory", ARTIFACT), newline="", encoding="utf-8") as fh:
            report(list(csv.DictReader(fh)))
        return 0
    if not args.dump:
        ap.error("--dump is required (or --check / --report)")
    if args.cls:
        if "." in args.cls and not args.package:
            args.package, args.cls = args.cls.split(".", 1)
        return one(args)
    if args.sweep:
        sweep(args)
        return 0
    ap.error("pick --sweep, --class or --report")


if __name__ == "__main__":
    sys.exit(main())
