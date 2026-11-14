#!/usr/bin/env python3
"""The closing run: the bytes between a class record's last unit and its defaults block.

#472 measured the distance from the record's last streamed unit to a located block start: **6 to 9 bytes on
1,180 of 2,204 rows**, `02 01 02 83 03 00` on the four `Engine.SubAction*` anchors whose block is known from the
block side. It left the run's grammar open and named the test: read the run on payloads whose block is a known
tag, and join what it parses to `inventory/class_record_gap.csv`'s array counts.

`UClass::Serialize` (`~/sg1/work/module-export/Core/0003a800_Core_UnClass.c`) fixes the field order between the
record array and the defaults buffer, and its listing carries the three call sites:

    0x3a8a6  CALL 0x0003d900   the record array: &+0x4d0 into &+0x4dc, returns the archive
    0x3a8af  CALL 0x0003d160   &+0x4dc written as names: a compact count, then ONE compact index per element
    0x3a8ba  CMP [Ar+4],0x3e   version >= 62:  &+0x4b0 through slot +0x18, then &+0x4b4 through slot +0x1c
    0x3a8f6  CMP [Ar+4],0x63   version >= 99:  &+0x4f4 through 0x0003d160 (count + one compact per element)

`inventory/archive_slots.csv` measures both name slots as **one compact field**, so the group after the record is
exactly: a names array, a reference field, a name field, and a second names array. That is the grammar this tool
tests -- *names, ref, name, list* -- and it is not assumed: a row counts only when the parse ends exactly on the
block start and every name index is inside the package's name table.

The reference field is why the run looked ungrammatical byte by byte: it is written **negative** on most rows (an
import-side reference, biased by the import table), which no name array can explain. The tool reports that sign
separately and joins the two counts it reads to the gap census, which measured the same region from the payload's
other side.

The join has a second half, and it is the one that decides the census. `array_end` -- where `class-record-gap.py`
starts its own parse of this region -- is not the writer's array tail: on the overlap it lands **before the
record's last unit**, or past the block start, and never at the group's start. So the census's `nm_*` block
measures a unit body there, and `--report` prints where each overlapping row's `array_end` sits (`gap_array_end`,
`gap_off` and `gap_pos`) against what the group reads at the position the writer uses.

    python3 tools/class-tail-run.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-tail-run.py --report
    python3 tools/class-tail-run.py --check           # synthetic payloads, no dump, writes nothing

Reads the packages and two committed tables; no boot, no Ghidra round, no property value decoded.
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
UNIT_DWORD = b"\x01\x00\x00\x00"   # a record unit's flag field
UNIT_BODY = 8                      # the flag dword plus the four bytes the writer streams after it
MAX_COUNT = 65536                  # a names array may be long; the run is bounded by the payload anyway
MAX_FIELD = 5                      # one compact field is 1 to 5 bytes
NEAR = 12                          # #472's population: a run no longer than this
ARTIFACT = "class_tail_run.csv"
BOUND_ARTIFACT = "class_record_bound.csv"
GAP_ARTIFACT = "class_record_gap.csv"
COLUMNS = ["package", "class", "payload", "block_start", "last_unit", "run_len", "run_hex", "verdict",
           "names_count", "names_indices", "ref", "ref_kind", "cfg_name", "list_count", "list_indices",
           "gap_mid_count", "gap_list_count", "gap_first_count", "gap_array_end", "gap_off", "gap_pos",
           "mid_match", "list_match"]


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


def walk_names(buf, p, stop, nnames):
    """A compact count, then that many ONE-compact elements (`FUN_0003d160`'s file stream)."""
    if p < 0 or p >= stop:
        return None
    try:
        count, o = read_index(buf, p)
    except IndexError:
        return None
    if count < 0 or count > MAX_COUNT:
        return None
    vals = []
    for _ in range(count):
        if o >= stop:
            return None
        try:
            v, e = read_index(buf, o)
        except IndexError:
            return None
        if e - o > MAX_FIELD:
            return None
        vals.append(v)
        o = e
    return {"count": count, "vals": vals, "end": o, "len": o - p,
            "in_range": all(0 <= v < nnames for v in vals)}


def parse_group(data, start, stop, nnames):
    """The writer's group: names array, reference field, name field, list array -- ending exactly at `stop`."""
    names = walk_names(data, start, stop, nnames)
    if names is None:
        return None
    q = names["end"]
    try:
        ref, q = read_index(data, q)
        name, q = read_index(data, q)
    except IndexError:
        return None
    if q > stop:
        return None
    lst = walk_names(data, q, stop, nnames)
    if lst is None or lst["end"] != stop:
        return None
    return {"names": names, "ref": ref, "name": name, "list": lst,
            "ok": names["in_range"] and lst["in_range"] and 0 <= name < nnames}


def unit_offsets(data, stop):
    out, i = [], data.find(UNIT_DWORD, 0, stop)
    while i >= 0:
        out.append(i)
        i = data.find(UNIT_DWORD, i + 1, stop)
    return out


def read_gap():
    path = os.path.join(ROOT, "inventory", GAP_ARTIFACT)
    if not os.path.exists(path):
        return {}
    return {(r["package"], r["class"]): r for r in csv.DictReader(open(path, newline=""))}


def gap_join(g, last_unit, block):
    """Where the gap census's `array_end` sits relative to the record whose group this is.

    The census locates an array from the payload's other side and starts its `nm_*` parse at that array's tail.
    The record's own closing group starts at `last_unit + UNIT_BODY`, so a census tail that lands before the last
    unit -- or past the block -- is reading bytes the writer never writes an array at.
    """
    out = {"gap_array_end": "", "gap_off": "", "gap_pos": ""}
    raw = (g or {}).get("array_end", "")
    if raw == "":
        return out
    ae = int(raw)
    run = last_unit + UNIT_BODY
    if ae <= last_unit:
        pos = "before-last-unit"
    elif ae < run:
        pos = "inside-last-unit"
    elif ae == run:
        pos = "run-start"
    elif ae < block:
        pos = "inside-run"
    elif ae == block:
        pos = "at-block"
    else:
        pos = "past-block"
    out.update({"gap_array_end": ae, "gap_off": ae - last_unit, "gap_pos": pos})
    return out


def _indices(vals, limit=8):
    return " ".join(str(v) for v in vals[:limit]) + (" +%d" % (len(vals) - limit) if len(vals) > limit else "")


def ref_kind(ref, nimp, nexports):
    """A name index biased by the import table: negative = an import slot, positive = an export one."""
    if ref < 0:
        return "import" if -ref <= nimp else "import-out-of-range"
    return "export" if ref < nexports else "export-out-of-range"


def verdict(got):
    if not got:
        return "no-parse"
    return "names-ref-name-list" if got["ok"] else "names-ref-name-list (out of range)"


def sweep(args):
    rows = []
    gap = read_gap()
    bound = [r for r in csv.DictReader(open(os.path.join(ROOT, "inventory", BOUND_ARTIFACT), newline=""))
             if r["block_start"]]
    by_pkg = collections.defaultdict(list)
    for r in bound:
        by_pkg[r["package"]].append(r)
    for name, rs in by_pkg.items():
        pkg = Package(os.path.join(args.dump, "System", name + ".u"))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        nnames = len(pkg.names)
        nimp = len(pkg.imports)
        for r in rs:
            exp = None
            for e in pkg.exports:
                if pkg.name(e["name"]) == r["class"] and pkg.class_name_of(e) == "Class":
                    exp = e
                    break
            if exp is None:
                continue
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            block = int(r["block_start"])
            if block > len(data) or block <= 0:
                continue
            units = [u for u in unit_offsets(data, block) if u + UNIT_BODY <= block]
            if not units:
                continue
            last = units[-1]
            run = data[last + UNIT_BODY:block]
            got = parse_group(data, last + UNIT_BODY, block, nnames)
            g = gap.get((name, r["class"]), {})
            row = {"package": name, "class": r["class"], "payload": len(data), "block_start": block,
                   "last_unit": last, "run_len": len(run), "run_hex": run.hex(), "verdict": verdict(got),
                   "names_count": got["names"]["count"] if got else "",
                   "names_indices": _indices(got["names"]["vals"]) if got else "",
                   "ref": got["ref"] if got else "",
                   "ref_kind": (ref_kind(got["ref"], nimp, len(pkg.exports)) if got else ""),
                   "cfg_name": got["name"] if got else "",
                   "list_count": got["list"]["count"] if got else "",
                   "list_indices": _indices(got["list"]["vals"]) if got else "",
                   "gap_mid_count": g.get("nm_mid_count", ""), "gap_list_count": g.get("nm_list_count", ""),
                   "gap_first_count": g.get("first_count", "")}
            row.update(gap_join(g, last, block))
            for key, col, gap_col in (("mid_match", "names_count", "gap_mid_count"),
                                      ("list_match", "list_count", "gap_list_count")):
                row[key] = int(row[gap_col] != "" and row[col] != "" and int(row[gap_col]) == int(row[col]))
            rows.append(row)
    rows.sort(key=lambda r: (r["verdict"], r["package"], r["class"]))
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
        print("wrote %s: %d rows" % (path, len(rows)))
    return rows


def report(rows=None):
    if rows is None:
        rows = list(csv.DictReader(open(os.path.join(ROOT, "inventory", ARTIFACT), newline="", encoding="utf-8")))
    fits = [r for r in rows if r["verdict"] == "names-ref-name-list"]
    near = [r for r in rows if int(r["run_len"]) <= NEAR]
    near_fits = [r for r in near if r["verdict"] == "names-ref-name-list"]
    print("%d payloads with a record unit before a located block start" % len(rows))
    for label, pop, pop_fits in (("all rows", rows, fits),
                                 ("run <= %d bytes (#472 population)" % NEAR, near, near_fits)):
        print("  %-38s %5d rows, %5d parse as names-ref-name-list (%.1f%%)"
              % (label, len(pop), len(pop_fits), 100.0 * len(pop_fits) / max(1, len(pop))))
    neg = [r for r in fits if int(r["ref"]) < 0]
    print("\nthe reference field is negative on %d of %d parses (%.1f%%); values %s"
          % (len(neg), len(fits), 100.0 * len(neg) / max(1, len(fits)),
             ", ".join("%d:%d" % kv for kv in collections.Counter(int(r["ref"]) for r in fits).most_common(8))))
    print("config name index inside the name table on %d of %d"
          % (sum(1 for r in fits if int(r["cfg_name"]) >= 0), len(fits)))
    print("\nrun length (all rows / parsing rows):")
    h_all = collections.Counter(int(r["run_len"]) for r in rows)
    h_fit = collections.Counter(int(r["run_len"]) for r in fits)
    for k in sorted(h_all):
        if k <= NEAR or h_fit.get(k):
            print("  %2d bytes  %5d  %5d" % (k, h_all[k], h_fit.get(k, 0)))
    print("\nclosed list (+0x4f4) count over the %d parses:" % len(fits))
    for k, n in collections.Counter(int(r["list_count"]) for r in fits).most_common(8):
        print("  count %-6d %5d rows" % (k, n))
    print("\nthe reference field by what it resolves to: %s"
          % ", ".join("%s %d" % kv for kv in collections.Counter(r["ref_kind"] for r in fits).most_common()))
    both = [r for r in rows if r["gap_mid_count"] != "" or r["gap_list_count"] != ""]
    both_fit = [r for r in both if r["verdict"] == "names-ref-name-list"]
    print("\n%d rows also carry a class_record_gap row, and %d of them parse here; the counts against that census:"
          % (len(both), len(both_fit)))
    for key, col, gap_col in (("names count vs nm_mid_count (+0x4dc)", "mid_match", "gap_mid_count"),
                             ("list count vs nm_list_count (+0x4f4)", "list_match", "gap_list_count")):
        have = sum(1 for r in both if r[gap_col] != "")
        eq = sum(1 for r in both if r[col] == "1")
        print("  %-40s equal on %4d of %4d" % (key, eq, have))
    if both_fit:
        print("  their names counts: %s"
              % ", ".join("%d:%d" % kv for kv in collections.Counter(int(r["names_count"]) for r in both_fit).most_common(8)))
        print("  their gap nm_mid_count: %s"
              % ", ".join("%d:%d" % kv for kv in collections.Counter(int(r["gap_mid_count"]) for r in both_fit
                                                                    if r["gap_mid_count"] != "").most_common(8)))
    # The join's second half: where the census starts its own parse of the same region.
    gap = read_gap()
    pairs = [(r, gap[(r["package"], r["class"])]) for r in rows if (r["package"], r["class"]) in gap]
    tagged = [t for t in ((r, g, gap_join(g, int(r["last_unit"]), int(r["block_start"]))) for r, g in pairs)
              if t[2]["gap_pos"]]
    print("\nwhere the gap census's `array_end` sits, on the %d rows both artifacts carry:" % len(tagged))
    for k, n in collections.Counter(t[2]["gap_pos"] for t in tagged).most_common():
        sub = [t for t in tagged if t[2]["gap_pos"] == k]
        print("  %-17s %4d rows, %4d parse here"
              % (k, n, sum(1 for t in sub if t[0]["verdict"] == "names-ref-name-list")))
    off = collections.Counter(int(t[2]["gap_off"]) for t in tagged if t[2]["gap_pos"] == "before-last-unit")
    print("  before-last-unit offsets (array_end - last_unit): %s"
          % ", ".join("%d:%d" % kv for kv in off.most_common(6)))
    fitting = [t for t in tagged if t[0]["verdict"] == "names-ref-name-list"]
    print("  the census's own nm split closes on %d of the %d that parse here"
          % (sum(1 for t in fitting if t[1].get("nm_exact") == "1"), len(fitting)))
    before = [t for t in fitting if t[2]["gap_pos"] == "before-last-unit"]
    if before:
        print("  on those %d, read where the census reads: nm_mid_count %s, nm_shape %s"
              % (len(before),
                 ", ".join("%s:%d" % kv for kv in
                           collections.Counter(t[1]["nm_mid_count"] for t in before).most_common(4)),
                 ", ".join("%s:%d" % kv for kv in
                           collections.Counter(t[1]["nm_shape"] for t in before).most_common(4))))
        print("  re-derived at the group start (last_unit + %d): names count %s, config name in range %d of %d, "
              "list count %s"
              % (UNIT_BODY,
                 ", ".join("%s:%d" % kv for kv in
                           collections.Counter(t[0]["names_count"] for t in before).most_common(6)),
                 sum(1 for t in before if t[0]["cfg_name"] and int(t[0]["cfg_name"]) >= 0), len(before),
                 ", ".join("%s:%d" % kv for kv in
                           collections.Counter(t[0]["list_count"] for t in before).most_common(4))))
    failed = collections.Counter(r["run_hex"] for r in rows if r["verdict"] == "no-parse")
    print("\ntop runs no grammar consumes (all rows):")
    for h, n in failed.most_common(8):
        print("  %-26s %5d" % (h or "(empty)", n))
    return 0


def selftest():
    """Synthetic payloads: the group's field shapes, a nine-element array, and three refusals."""
    nnames = 64
    cases = [
        ("two names, negative ref, empty list", b"\x02\x01\x02\x83\x03\x00", "names-ref-name-list"),
        ("three names, negative ref, empty list", b"\x03\x01\x04\x02\x84\x03\x00", "names-ref-name-list"),
        ("one name, positive ref, one-element list", b"\x01\x05\x07\x01\x01\x22", "names-ref-name-list"),
        ("a nine-element names array", bytes([9]) + bytes(range(1, 10)) + b"\x86\x16\x01\x22",
         "names-ref-name-list"),
    ]
    bad = 0
    for label, body, want in cases:
        got = verdict(parse_group(body, 0, len(body), nnames))
        if got != want:
            print("FAIL class-tail-run: %s answered %s, expected %s" % (label, got, want))
            bad += 1
    body = b"\x02\x01\x02\x83\x50\x00"
    got = parse_group(body, 0, len(body), nnames)
    if got and got["ok"]:
        print("FAIL class-tail-run: an out-of-range config name was accepted")
        bad += 1
    body = b"\x02\x01\x02\x83\x03\x07"
    if parse_group(body, 0, len(body), nnames):
        print("FAIL class-tail-run: a leftover byte was consumed")
        bad += 1
    body = b"\x02\x83\x03\x01\x02\x00"
    got = parse_group(body, 0, len(body), nnames)
    if got and got["ok"] and got["names"]["vals"] == [1, 2]:
        print("FAIL class-tail-run: a re-ordered group was accepted as the writers")
        bad += 1
    for mark, lu, bs, want in ((58, 70, 78, "before-last-unit"), (75, 70, 78, "inside-last-unit"),
                               (78, 70, 78, "run-start"), (82, 70, 86, "inside-run"),
                               (86, 70, 86, "at-block"), (99, 70, 86, "past-block")):
        got = gap_join({"array_end": str(mark)}, lu, bs)["gap_pos"]
        if got != want:
            print("FAIL class-tail-run: array_end %d with unit %d and block %d answered %s, expected %s"
                  % (mark, lu, bs, got, want))
            bad += 1
    if gap_join({}, 70, 78)["gap_pos"] != "" or gap_join({"array_end": ""}, 70, 78)["gap_off"] != "":
        print("FAIL class-tail-run: a row without a census entry answered a position")
        bad += 1
    if bad:
        return 1
    print("ok   class-tail-run: the writer group, a nine-element array and 3 refusals on synthetic payloads")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default=os.environ.get("SG1_DUMP", ""))
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return selftest()
    if args.sweep:
        if not args.dump:
            print("FAIL class-tail-run: --sweep needs --dump")
            return 1
        return report(sweep(args))
    if args.report:
        return report()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
