#!/usr/bin/env python3
"""One committed table of mission loads: `missions.sh` run directories merged (PLAN 7.2).

`tools/xemu/missions.sh` writes one row per index into its OUTDIR's `missions.tsv`, and **truncates that
file on every invocation** -- so a run's result exists only inside its own directory and the campaign-wide
picture has lived in `docs/XEMU.md` prose. This merges any number of run directories into one CSV whose
verdicts are the runs', not a summary's, and names each index from the disc instead of from a frame.

A PASS is a **load**, not a mission: `missions.sh` asserts a repaint, "not the BIOS screen" and "not the
boot frame", so a map that loads into a cutscene passes (measured: index 41 is a first-person scene,
70 a cutscene). The name comes from `map-index.table()`, the page's own ASCII order, which is what the
index counts -- reading a name off a highlight frame is the error `docs/XEMU.md` records twice.

    python3 tools/xemu/map-loads.py --iso $WORK/sg1.iso $WORK/mapback -o inventory/map_loads.csv
    python3 tools/xemu/map-loads.py --check          # synthetic, no dump and no disc

Columns: `index,name,result,attempts,runs,detail`, one row per measured index, sorted by index.

* **PASS wins over FAIL**, because a recorded failure is input (a press the guest swallowed) and is
  measured non-deterministic: 4 of 31 indices needed a second attempt in the 2026-11-04 sweep, and two
  indices that failed twice passed on a later invocation. A row that never passed keeps the last
  failure's reason in `detail` (`BIOS screen`, `main menu`, `frame gate`, or blank for a boot that never
  produced a frame), so the verdict says which failure it was.
* `attempts` sums the boots spent across runs and `runs` counts the invocations that reported the index,
  so a single flaky boot does not look like a single-boot map.
* An index outside the disc's table, or a status that is not PASS/FAIL, is an error rather than a row:
  the merge would otherwise record a page that does not exist.
* An index outside the **campaign** range is a row like any other -- the page offers 73 entries and 0-9,
  71, 72 are not campaign maps -- but the generated summary names those rows in their own line, so a
  table longer than its own count reads as deliberate rather than as drift.
* The page has **two** generated blocks: the table (`DOC_MARK`) and the Status paragraph
  (`DOC_STATUS_MARK`), because a hand-written total went stale the first time a merge added a row -- the
  page said "63 rows over 77 boots" beside a table of 67. `--write-doc` writes both and `--check-doc`
  compares both, so no number on that page is typed twice.
"""
import argparse
import csv
import importlib.util
import io
import os
import re
import shutil
import sys
import tempfile

TSV = "missions.tsv"
COLUMNS = ["index", "name", "result", "attempts", "runs", "detail"]
DOC_MARK = ("<!-- generated:map-loads -->", "<!-- /generated:map-loads -->")
DOC_STATUS_MARK = ("<!-- generated:map-loads-status -->", "<!-- /generated:map-loads-status -->")
CAMPAIGN = (10, 70)
ATTEMPTS = re.compile(r"^(\d+) attempts?$")


def map_table(iso):
    """[(index, name)] from the disc, through map-index.py (same ASCII order the page uses)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "map-index.py")
    spec = importlib.util.spec_from_file_location("sg1_map_index", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.table(iso)


def read_run(directory):
    """[(index, status, attempts, detail)] from one run directory's missions.tsv."""
    path = os.path.join(directory, TSV)
    out = []
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                sys.exit("%s: short row %r" % (path, line))
            status = parts[1].strip().upper()
            if status not in ("PASS", "FAIL"):
                sys.exit("%s: status %r is neither PASS nor FAIL" % (path, parts[1]))
            attempts, detail = 1, ""
            for field in parts[3:]:
                hit = ATTEMPTS.match(field.strip())
                if hit:
                    attempts = int(hit.group(1))
                elif field.strip():
                    detail = field.strip()
            out.append((int(parts[0]), status, attempts, detail))
    return out


def merge(directories, names):
    """[(index, name, result, attempts, runs, detail)] -- one row per measured index."""
    known = dict(names)
    acc = {}
    for directory in directories:
        if not os.path.exists(os.path.join(directory, TSV)):
            sys.stderr.write("skip %s: no %s\n" % (directory, TSV))
            continue
        for index, status, attempts, detail in read_run(directory):
            if index not in known:
                sys.exit("%s: index %d is not in the disc's table (%d entries)"
                         % (directory, index, len(known)))
            row = acc.setdefault(index, {"result": "FAIL", "attempts": 0, "runs": 0, "detail": ""})
            row["attempts"] += attempts
            row["runs"] += 1
            if status == "PASS":
                row["result"], row["detail"] = "PASS", ""
            elif row["result"] != "PASS" and detail:
                row["detail"] = detail
    return [(index, known[index], acc[index]["result"], acc[index]["attempts"], acc[index]["runs"],
             acc[index]["detail"]) for index in sorted(acc)]


def render(rows):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([str(v) for v in row])
    return buf.getvalue()


def ranges(values):
    """[10, 11, 13, 15, 16] -> "10-11, 13, 15-16" -- a missing set is unreadable as a list of sixty numbers."""
    out, start, prev = [], None, None
    for value in values:
        if start is None:
            start = prev = value
        elif value == prev + 1:
            prev = value
        else:
            out.append(str(start) if start == prev else "%d-%d" % (start, prev))
            start = prev = value
    if start is not None:
        out.append(str(start) if start == prev else "%d-%d" % (start, prev))
    return ", ".join(out)


def report(rows, campaign=CAMPAIGN):
    """One line for stdout: how much of the campaign range this table covers, and which indices do not."""
    measured = {row[0] for row in rows}
    lo, hi = campaign
    missing = [i for i in range(lo, hi + 1) if i not in measured]
    count = sum(1 for i in range(lo, hi + 1) if i in measured)
    passed = sum(1 for row in rows if row[2] == "PASS")
    print("ok   %d row(s): %d PASS / %d FAIL; campaign %d-%d has %d of %d entries measured"
          % (len(rows), passed, len(rows) - passed, lo, hi, count, hi - lo + 1))
    if missing:
        print("     unmeasured campaign indices: %s" % ranges(missing))


def read_csv(path):
    """The rows back out of the committed artifact: [(index, name, result, attempts, runs, detail)]."""
    if not os.path.exists(path):
        sys.exit("map-loads: no %s (run the merge once with a disc and -o %s)" % (path, path))
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out.append((int(row["index"]), row["name"], row["result"], int(row["attempts"]),
                        int(row["runs"]), row["detail"]))
    return sorted(out)


def emit(rows, args, text):
    """Write the CSV and/or the page's blocks; `--check-doc` compares instead of writing."""
    path = args.write_doc or args.check_doc
    if path:
        blocks = ((DOC_MARK, doc_block(rows)), (DOC_STATUS_MARK, status_block(rows)))
        current = open(path, encoding="utf-8").read()
        fresh = current
        for marks, block in blocks:
            fresh = replace_block(fresh, marks, block)
        if args.check_doc:
            if fresh != current:
                print("FAIL %s: a generated block is stale" % path)
                for marks, _ in blocks:
                    was = current.split(marks[0], 1)[1].split(marks[1], 1)[0]
                    new = fresh.split(marks[0], 1)[1].split(marks[1], 1)[0]
                    if was == new:
                        continue
                    old, new = was.strip().splitlines(), new.strip().splitlines()
                    print("  block %s: %d lines are %d" % (marks[0], len(old), len(new)))
                    for i in range(max(len(old), len(new))):
                        a = old[i] if i < len(old) else "<none>"
                        b = new[i] if i < len(new) else "<none>"
                        if a != b:
                            print("  line %d: have %r, computed %r" % (i + 1, a, b))
                            break
                    break
                return 1
            print("ok   map-loads-doc: %s matches its rows" % path)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(fresh)
            print("ok   map-loads-doc: wrote %s" % path)
        if args.check_doc and not args.out:
            return 0
    if args.out and text is not None:
        with open(args.out, "w", newline="") as fh:
            fh.write(text)
    elif text is not None:
        sys.stdout.write(text)
    report(rows)
    return 0


def doc_block(rows, campaign=CAMPAIGN):
    """The generated part of docs/MAP_LOADS.md: the counts, the table, and what is not measured yet."""
    lo, hi = campaign
    measured = {row[0] for row in rows}
    passed = sum(1 for row in rows if row[2] == "PASS")
    boots = sum(row[3] for row in rows)
    missing = [i for i in range(lo, hi + 1) if i not in measured]
    if missing:
        summary = ("**%d rows, %d PASS / %d FAIL, %d boots:** %d of the %d campaign entries (%d-%d) have been "
                   "loaded; %d still unmeasured."
                   % (len(rows), passed, len(rows) - passed, boots, hi - lo + 1 - len(missing), hi - lo + 1,
                      lo, hi, len(missing)))
    else:
        summary = ("**%d rows, %d PASS / %d FAIL, %d boots:** all %d campaign entries (%d-%d) have been loaded."
                   % (len(rows), passed, len(rows) - passed, boots, hi - lo + 1, lo, hi))
    lines = ["", summary, ""]
    if missing:
        lines += ["Not measured: `%s`." % ranges(missing), ""]
    outside = [row for row in rows if not lo <= row[0] <= hi]
    if outside:
        # A row outside the campaign range still belongs to the list, so the summary has to say so: without
        # it a page whose table ends at index 72 under a line that accounts for 61 rows reads as drift.
        lines += ["Outside the campaign range: %s."
                  % ", ".join("%d `%s` (%s)" % (row[0], row[1], row[2]) for row in outside), ""]
    lines += ["| index | map | result | attempts | runs | detail |",
              "|---:|---|---|---:|---:|---|"]
    for index, name, result, attempts, runs, detail in rows:
        lines.append("| %d | `%s` | %s | %d | %d | %s |"
                     % (index, name, result, attempts, runs, detail or ""))
    return "\n".join(lines)


def status_block(rows, campaign=CAMPAIGN):
    """The page's Status paragraph, computed from the rows.

    It used to be prose, and its totals went stale on the first merge that added a row: the page said
    "63 rows over 77 boots" beside a table that said 67. A number that can drift from the artifact it
    describes is not a status, so the paragraph lives between its own markers and `--check-doc` holds it.
    """
    lo, hi = campaign
    measured = {row[0] for row in rows}
    passed = sum(1 for row in rows if row[2] == "PASS")
    boots = sum(row[3] for row in rows)
    in_range = [row for row in rows if lo <= row[0] <= hi]
    missing = [i for i in range(lo, hi + 1) if i not in measured]
    outside = [row for row in rows if not lo <= row[0] <= hi]
    lines = ["",
             "**%d rows over %d boots, %d PASS and %d FAIL.**" % (len(rows), boots, passed, len(rows) - passed),
             ""]
    if missing:
        lines += ["The campaign range (%d-%d) has **%d** entr%s with no row: `%s`."
                  % (lo, hi, len(missing), "y" if len(missing) == 1 else "ies", ranges(missing)), ""]
    else:
        inside_boots = sum(row[3] for row in in_range)
        rest = ("; the other %d rows are the page's entries outside that range (%d boots)"
                % (len(outside), sum(row[3] for row in outside))) if outside else ""
        lines += ["All **%d** campaign entries (%d-%d) have a row, over %d of the boots%s."
                  % (hi - lo + 1, lo, hi, inside_boots, rest), ""]
    lines += ["\"Complete\" here means coverage, not playability: a `PASS` row is a load, and the two questions "
              "under \"What is left\" are what a row still cannot answer.", ""]
    return "\n".join(lines)


def replace_block(text, marks, block):
    begin, end = marks
    i, j = text.find(begin), text.find(end)
    if i < 0 or j <= i:
        sys.exit("document has no %s ... %s block" % marks)
    return text[:i + len(begin)] + block + "\n" + text[j:]


def check():
    """Synthetic merge: no disc, no dump, nothing written to the tree."""
    names = [(i, n) for i, n in enumerate(["CTF-Abydos", "Entry", "M00", "M01A", "M15C"])]
    root = tempfile.mkdtemp(prefix="map-loads-check-")
    try:
        first, second = os.path.join(root, "a"), os.path.join(root, "b")
        for path in (first, second):
            os.makedirs(path)
        with open(os.path.join(first, TSV), "w") as fh:
            fh.write("2\tFAIL\t/a/run.log\t2 attempts\n")          # never loaded, two boots
            fh.write("3\tPASS\t/a/02-mission.png\t1 attempts\n")   # loaded first time
        with open(os.path.join(second, TSV), "w") as fh:
            fh.write("2\tPASS\t/b/02-mission.png\t1 attempts\n")   # the same index, retried later
            fh.write("4\tFAIL\t/b/halt.log\t1 attempts\tBIOS screen\n")
            fh.write("4\tFAIL\t/b/run.log\t2 attempts\n")          # last failure's reason is kept
        want_rows = [(2, "M00", "PASS", 3, 2, ""),
                     (3, "M01A", "PASS", 1, 1, ""),
                     (4, "M15C", "FAIL", 3, 2, "BIOS screen")]
        got = render(merge([first, second], names))
        want = render(want_rows)
        if got != want:
            print("FAIL merge: got\n%s\nwant\n%s" % (got, want))
            return 1
        chk = os.path.join(root, "names")
        os.makedirs(chk)
        with open(os.path.join(chk, TSV), "w") as fh:
            fh.write("99\tPASS\t/x/02-mission.png\t1 attempts\n")
        try:
            merge([chk], names)
        except SystemExit:
            pass
        else:
            print("FAIL an index outside the disc's table was accepted")
            return 1
        with open(os.path.join(chk, TSV), "w") as fh:
            fh.write("2\tMAYBE\t/x/02-mission.png\t1 attempts\n")
        try:
            merge([chk], names)
        except SystemExit:
            pass
        else:
            print("FAIL an unknown status was accepted")
            return 1
        complete = "".join(doc_block(want_rows, campaign=(2, 4)))
        if "all 3 campaign entries (2-4) have been loaded" not in complete or "unmeasured" in complete:
            print("FAIL doc_block does not say the range is complete when it is:\n%s" % complete)
            return 1
        gapped = "".join(doc_block(want_rows[1:], campaign=(2, 4)))
        if "2 of the 3 campaign entries (2-4) have been loaded; 1 still unmeasured" not in gapped:
            print("FAIL doc_block does not name the unmeasured index:\n%s" % gapped)
            return 1
        mixed = "".join(doc_block(want_rows, campaign=(2, 3)))
        if "Outside the campaign range: 4 `M15C` (FAIL)." not in mixed:
            print("FAIL doc_block does not account for a row outside the campaign range:\n%s" % mixed)
            return 1
        done = "".join(status_block(want_rows, campaign=(2, 4)))
        if ("3 rows over 7 boots, 2 PASS and 1 FAIL" not in done
                or "All **3** campaign entries (2-4) have a row" not in done):
            print("FAIL status_block does not carry the rows' own totals:\n%s" % done)
            return 1
        short = "".join(status_block(want_rows[1:], campaign=(2, 4)))
        if "has **1** entry with no row: `2`" not in short:
            print("FAIL status_block does not name a campaign entry with no row:\n%s" % short)
            return 1
        page = "head\n%s\nOLD-TABLE\n%s\nmid\n%s\nOLD-STATUS\n%s\ntail\n" % (DOC_MARK + DOC_STATUS_MARK)
        fresh = replace_block(replace_block(page, DOC_MARK, "TABLE"), DOC_STATUS_MARK, "STATUS")
        if ("TABLE" not in fresh or "STATUS" not in fresh or "OLD-" in fresh
                or fresh.count("head") != 1 or fresh.count("mid") != 1 or fresh.count("tail") != 1):
            print("FAIL replace_block does not swap both blocks in place:\n%s" % fresh)
            return 1
        print("ok   map-loads: a PASS from a later run wins, attempts add up, an unknown index or "
              "status is refused, the summary line knows a complete range from a gapped one and "
              "names a row outside it, the status paragraph carries the rows' own totals, and both "
              "generated blocks swap in place")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs", nargs="*", help="missions.sh output directories to merge")
    ap.add_argument("--iso", default=os.environ.get("SG1_ISO"),
                    help="disc image, for the index -> name table (the XISO, not the dump directory)")
    ap.add_argument("-o", "--out", help="write the CSV here (default: stdout)")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; needs no disc")
    ap.add_argument("--write-doc", metavar="PATH", help="regenerate the page's generated blocks")
    ap.add_argument("--check-doc", metavar="PATH", help="fail if the page's blocks are stale (in `make ci`)")
    ap.add_argument("--from-csv", metavar="PATH", default="inventory/map_loads.csv",
                    help="with --check-doc/--write-doc and no run directories: read this artifact instead "
                         "of a run (that is what makes the doc gate work without the disc)")
    args = ap.parse_args()

    if args.check:
        return check()
    if (args.write_doc or args.check_doc) and not args.runs:
        rows = read_csv(args.from_csv)
        if not rows:
            sys.exit("map-loads: %s holds no rows" % args.from_csv)
        return emit(rows, args, None)
    if not args.iso:
        sys.exit("map-loads: pass --iso <disc image> (or SG1_ISO); the name column comes from the disc")
    if not args.runs:
        sys.exit("map-loads: pass at least one missions.sh output directory")

    names = map_table(args.iso)
    rows = merge(args.runs, names)
    if not rows:
        sys.exit("map-loads: no %s in %s" % (TSV, ", ".join(args.runs)))
    return emit(rows, args, render(rows))


if __name__ == "__main__":
    sys.exit(main())
