#!/usr/bin/env python3
"""Name the gap bodies the map has no row for, from the supplied XDK libraries (PLAN 4.2, U40).

`inventory/gap_heads.csv` is the census of bodies that sit in `.text` gaps: the boundary rule decodes them, but
nothing names them, so they are not map rows and the library matcher never sees them -- it scans
`symbols/xbox_functions.csv`, and by definition these are the bodies that file lacks.

This tool closes that loop. It hands the unnamed bodies to `tools/xdk-lib-match.py` as a temporary function list,
keeps whatever the masked body comparison accepts, and writes `inventory/gap_lib_names.csv` as the receipt. With
`--apply` it also writes the two rows each add needs -- an `add` in `symbols/xbe_boundaries.csv` and the creation
row in `symbols/xbe_overrides.csv` -- because `tools/symbol-boundaries.py` refuses an add that has no override, and
`XbePost.java` builds a function from the override file, so one without the other is invisible to a fresh import.

    python3 tools/gap-lib-names.py --libs ~/sg1/work/xdk/libs-all        # artifact only
    python3 tools/gap-lib-names.py --libs ~/sg1/work/xdk/libs-all --apply
    make boundaries symbols                                              # apply, then rebuild the map

The libraries are never committed (a leaked SDK), so `--check` validates the committed artifact on its own terms and
does not need them.
"""

import argparse
import csv
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAP = os.path.join(ROOT, "inventory", "gap_heads.csv")
ART = os.path.join(ROOT, "inventory", "gap_lib_names.csv")
MATCHER = os.path.join(ROOT, "tools", "xdk-lib-match.py")
BOUNDARIES = os.path.join(ROOT, "symbols", "xbe_boundaries.csv")
OVERRIDES = os.path.join(ROOT, "symbols", "xbe_overrides.csv")
MAP = os.path.join(ROOT, "symbols", "xbox_functions.csv")
FIELDS = ["address", "name", "size", "lib", "member"]


def bodies(path):
    """The census rows that decode as a body and carry a size."""
    out = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("verdict") == "body" and row.get("size"):
                out.append(row)
    return out


def match(args):
    """Run the library matcher over the gap bodies and return its accepted rows."""
    rows = bodies(GAP)
    if not rows:
        return []
    handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
    with handle as fh:
        w = csv.writer(fh)
        w.writerow(["address", "name", "size", "module"])
        for row in rows:
            w.writerow([row["head_va"], "FUN_%08x" % int(row["head_va"], 16), row["size"], ""])
    tmp_in = handle.name
    tmp_out = tmp_in + ".out"
    cmd = [sys.executable, MATCHER, "--libs", args.libs, "--functions", tmp_in, "-o", tmp_out]
    if args.xbe:
        cmd += ["--xbe", args.xbe]
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        os.unlink(tmp_in)
        sys.exit("gap-lib-names: the matcher failed (%d): %s" % (done.returncode, done.stderr.strip()[-400:]))
    for line in done.stdout.strip().splitlines()[-4:]:
        print("  matcher: %s" % line.strip(), file=sys.stderr)
    hits = list(csv.DictReader(open(tmp_out, newline="")))
    os.unlink(tmp_in)
    os.unlink(tmp_out)
    return hits


def write_art(hits, path):
    hits = sorted(hits, key=lambda r: int(r["address"], 16))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for hit in hits:
            w.writerow({k: hit[k] for k in FIELDS})
    print("wrote %s: %d name(s)" % (os.path.relpath(path, ROOT), len(hits)))


def apply(hits):
    """Write the `add` and the creation row each match needs, refusing duplicates."""
    with open(BOUNDARIES, newline="") as fh:
        b_rows = list(csv.DictReader(fh))
    with open(OVERRIDES, newline="") as fh:
        o_rows = list(csv.DictReader(fh))
    with open(MAP, newline="") as fh:
        map_addrs = {row["address"].lower() for row in csv.DictReader(fh)}
    have = {r["address"].lower() for r in b_rows} | {r["address"].lower() for r in o_rows} | map_addrs
    dup = [h["address"] for h in hits if h["address"].lower() in have]
    if dup:
        sys.exit("gap-lib-names: --apply: %d of the population is already a row (%s); regenerate the census "
                 "instead of re-adding it" % (len(dup), ", ".join(dup[:4])))
    for hit in hits:
        ev = "masked body equals %s.lib %s; in a .text gap, so the map had no row to name" % (hit["lib"], hit["member"])
        b_rows.append({"address": hit["address"], "action": "add", "target": hit["name"],
                       "size": hit["size"], "evidence": ev})
        o_rows.append({"address": hit["address"], "name": hit["name"],
                       "evidence": "added to the map by symbols/xbe_boundaries.csv: " + ev,
                       "class": "", "outer": "", "object_size": "", "assert_line": "", "module": hit["lib"],
                       "source_file": "", "function_size": hit["size"]})
    with open(BOUNDARIES, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["address", "action", "target", "size", "evidence"], lineterminator="\n")
        w.writeheader()
        w.writerows(b_rows)
    fields = ["address", "name", "evidence", "class", "outer", "object_size", "assert_line", "module",
              "source_file", "function_size"]
    with open(OVERRIDES, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\r\n")
        w.writeheader()
        w.writerows(o_rows)
    print("applied: +%d add(s) in %s and +%d creation row(s) in %s"
          % (len(hits), os.path.relpath(BOUNDARIES, ROOT), len(hits), os.path.relpath(OVERRIDES, ROOT)))


def check(args):
    """Validate the committed artifact without the libraries: it must still describe real, unnamed gap bodies."""
    if not os.path.isfile(ART):
        print("FAIL gap-lib-names: %s is missing" % os.path.relpath(ART, ROOT))
        return 1
    with open(ART, newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != FIELDS:
            print("FAIL gap-lib-names: columns are %s, expected %s" % (reader.fieldnames, FIELDS))
            return 1
        rows = list(reader)
    census = {row["head_va"].lower(): row for row in bodies(GAP)}
    with open(MAP, newline="") as fh:
        named = {row["address"].lower(): row["name"] for row in csv.DictReader(fh)}
    problems = []
    seen = set()
    for row in rows:
        addr = row["address"].lower()
        if addr in seen:
            problems.append("%s listed twice" % row["address"])
        seen.add(addr)
        if not row["name"] or not row["lib"] or not row["member"]:
            problems.append("%s lacks a name, library or member" % row["address"])
        if int(row["size"]) <= 0:
            problems.append("%s has a non-positive size" % row["address"])
        # An applied body is a map row and has left the census, so the census is only the authority for rows that
        # have not been applied yet -- requiring it for every row fails the check the moment --apply succeeds.
        census_row = census.get(addr)
        if addr not in named:
            if census_row is None:
                problems.append("%s is neither a map row nor a body in the gap census" % row["address"])
            elif census_row["size"] and int(census_row["size"]) != int(row["size"]):
                problems.append("%s size %s disagrees with the census (%s)"
                                % (row["address"], row["size"], census_row["size"]))
        # After `--apply` these bodies *are* rows, so "already a row" is the expected state and the name is what has
        # to agree. Before an apply the same row is still a census body, and the size is what has to agree.
        if addr in named and named[addr] != row["name"]:
            problems.append("%s is a map row named %r, not %r" % (row["address"], named[addr], row["name"]))
    if problems:
        for problem in problems[:10]:
            print("FAIL gap-lib-names: %s" % problem)
        return 1
    print("ok gap-lib-names: %d name(s), each agreeing with its map row or its census body" % len(rows))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--libs", help="directory holding the .lib archives (never committed)")
    ap.add_argument("--xbe", help="default.xbe (defaults to the matcher's own default)")
    ap.add_argument("--apply", action="store_true", help="also write the boundaries and override rows")
    ap.add_argument("--check", action="store_true", help="validate the committed artifact and exit")
    args = ap.parse_args()
    if args.check:
        return check(args)
    if not args.libs:
        ap.error("--libs is required unless --check is given")
    hits = match(args)
    write_art(hits, ART)
    if args.apply:
        apply(hits)
    else:
        print("dry run: %d name(s) would be added; pass --apply to write the rows" % len(hits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
