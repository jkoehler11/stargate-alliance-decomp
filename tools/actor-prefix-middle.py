#!/usr/bin/env python3
"""The record's four middle bytes are a code, not a reference: they repeat across maps and name nothing.

`actor_prefix_record.csv` reads the 13-byte front in front of an actor's property block as

    [class index][the same index again][8 x 0xff][4 bytes][0x81]

and `docs/ACTOR_PROPERTIES.md` leaves those four bytes open: they vary per instance, taking 1,011 distinct values
over the corpus. This tool reads them. The question a serialiser's reader would ask first is *which field is this*,
and the four bytes spend every candidate answer:

* not one field -- the little-endian dword exceeds the map file's own size on the same share of blocks as it does
  anything else, and never equals an export's offset, an export's serial size or the block's own length;
* not the class again, and not the block's first property name, and not the class name, and not the actor's `Tag`
  value -- each of those four readings lands within a couple of percent of the *control*, which is the same test run
  with a pseudo-random name index of the same magnitude (`in_block_control`, from the export's own ordinal, so the
  control measures what a same-sized index hits in the same block by chance);
* not per-instance data either: a class carries several of them (`StaticMeshActor` 448 blocks of one value and 72 of
  another in a single map) and one value is carried by many classes, so the four bytes are neither an identity nor a
  class constant;
* and not a name index wherever they could look like one, because **the same value appears in many maps and resolves
  to a different name in each** -- the name tables of two `.unr` files are different tables, so a code that recurs
  across them is a code of the build, not an index into the package it was read from.

What is left is the honest description: a small enumeration, drawn per instance, that means something to whatever
serialises the level's actor stream, and that the payload alone cannot say which -- that needs the writer, and the
writer is not the actor's own `AActor::Serialize` (measured in #598: it stores no class reference at all).

    python3 tools/actor-prefix-middle.py DUMP/Maps -o inventory     # needs the dump
    python3 tools/actor-prefix-middle.py --check                     # dump-free: shape, self-test, joins

Rows are tagged by `kind`, because this artifact answers three questions in one table:

* `class` -- a class's `records` (blocks carrying the record), the `distinct` values on it, the commonest
  (`top_value`/`top_count`), how many blocks hold `00 00 00 00` (`all_zero`), how many have equal halves
  (`halves_equal`) or a half above `0x7f` (`above_0x7f`), and the four reference readings with their control
  (`as_name` both halves below the map's name count, `in_block`, `in_block_control`, `is_class`, `is_tag`). `verdict`
  is `constant` when a class carries a single value, `repeats` when its values repeat, `distinct` when every block
  has its own.
* `value` -- one middle value: `records`, the `distinct` maps it appears in, and the commonest name its first half
  resolves to there (`top_value`/`top_count`), which is what makes the cross-map test readable.
* `census` -- the run-level counts the page quotes (`records`, `distinct_values`, `maps`, `values_in_many_maps`,
  and one `hit:<reading>` row per refused reading with its denominator), so `--write-doc` and the committed CSV render
  the same numbers.
"""
import argparse
import collections
import csv
import importlib.util
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import checkcsv  # noqa: E402
from ue2pkg import Package  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

COLUMNS = ["kind", "name", "records", "distinct", "names", "top_value", "top_count", "all_zero",
           "halves_equal", "above_0x7f", "as_name", "in_block", "in_block_control", "is_class", "is_tag",
           "verdict"]

MANY_MAPS = 10        # a value in at least this many maps is a code of the build, not of one package
WIDE = 0x80           # a half at or above this is outside the compact one-byte band


def loader(name, filename):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def maps_in(path):
    return sorted(f for f in os.listdir(path) if f.lower().endswith(".unr"))


def halves(four):
    """The four bytes as the two little-endian `u16` a writer of two small fields would emit."""
    a, b = struct.unpack("<HH", four)
    return a, b


def dword(four):
    return struct.unpack("<I", four)[0]


def tag_value(props):
    """The block's `Tag` as text: a `Name` arrives decoded, a `Str` arrives as bytes."""
    for p in props:
        if p["name"] != "Tag":
            continue
        v = p.get("value")
        if isinstance(v, bytes):
            try:
                return v.decode("utf-16-le").rstrip("\x00")
            except Exception:
                return ""
        if isinstance(v, str):
            return v
    return ""


def control_index(ordinal, name_count):
    """A name index of the same magnitude that cannot be data: a scramble of the export's ordinal in the same band.

    The band matters: early name-table entries are the names every class references, so a control drawn from the
    whole table would understate what a small index hits by chance and make any small value look like a reading.
    """
    band = min(WIDE, max(name_count, 1))
    return (ordinal * 37 + 11) % band


def compute(maps_dir):
    mod, rec = loader("prop_stream", "prop-stream.py"), loader("apr", "actor-prefix-record.py")
    stat = collections.defaultdict(collections.Counter)
    per_class_values = collections.defaultdict(collections.Counter)
    order = []
    val_records = collections.Counter()
    val_maps = collections.defaultdict(set)
    val_names = collections.defaultdict(collections.Counter)
    hits = collections.Counter()
    maps = maps_in(maps_dir)
    file_sizes = {}

    for fname in maps:
        path = os.path.join(maps_dir, fname)
        file_sizes[fname] = os.path.getsize(path)
        pkg = Package(path)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        ncount = pkg.name_count

        def name_of(i):
            return pkg.name(i) if 0 <= i < ncount else "?"

        for exp in pkg.exports:
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            length, matched = rec.record(data, exp["class"])
            if not matched:
                continue
            props, used, reason = mod.read_block(data[length:], pkg, True)
            if reason != "None" or length + used != len(data):
                continue
            four = data[length - 5:length - 1]
            a, b = halves(four)
            cls = pkg.class_name_of(exp)
            if cls not in stat:
                order.append(cls)
            st = stat[cls]
            st["records"] += 1
            key = "%d,%d" % (a, b)
            per_class_values[cls][key] += 1
            val_records[key] += 1
            val_maps[key].add(fname)
            val_names[key][name_of(a)] += 1

            names = [p["name"] for p in props]
            tag = tag_value(props)
            ctl_name = name_of(control_index(exp["index"], ncount))
            # the four reference readings, and the control beside the one they can be compared against
            read = {
                "as_name": a < ncount and b < ncount,
                "in_block": name_of(a) in names or name_of(b) in names,
                "is_class": name_of(a) == cls or name_of(b) == cls,
                "is_tag": bool(tag) and (name_of(a) == tag or name_of(b) == tag),
                "dword_is_offset": dword(four) in (exp["offset"], exp["offset"] + exp["size"], len(data), used),
                "dword_under_file": dword(four) < file_sizes[fname],
                "halves_equal": a == b,
                "all_zero": four == b"\x00\x00\x00\x00",
                "above_0x7f": a >= WIDE or b >= WIDE,
            }
            if read["in_block"]:
                st["in_block"] += 1
                hits["in_block"] += 1
            if ctl_name in names:
                st["in_block_control"] += 1
                hits["in_block_control"] += 1
            for k in ("as_name", "is_class", "is_tag", "halves_equal", "all_zero", "above_0x7f"):
                if read[k]:
                    st[k] += 1
                    hits[k] += 1
            for k in ("dword_is_offset", "dword_under_file"):
                if read[k]:
                    hits[k] += 1

    rows = []
    for cls in sorted(order, key=lambda c: (-stat[c]["records"], c)):
        st = stat[cls]
        pairs = sorted(per_class_values[cls].items(), key=lambda kv: (-kv[1], kv[0]))
        distinct = len(pairs)
        top_value, top_count = (pairs[0] if pairs else ("", 0))
        if distinct <= 1:
            verdict = "constant"
        elif distinct < st["records"]:
            verdict = "repeats"
        else:
            verdict = "distinct"
        rows.append(["class", cls, st["records"], distinct, "", top_value, top_count, st["all_zero"],
                     st["halves_equal"], st["above_0x7f"], st["as_name"], st["in_block"],
                     st["in_block_control"], st["is_class"], st["is_tag"], verdict])

    for key, cnt in sorted(val_records.items(), key=lambda kv: (-kv[1], kv[0]))[:24]:
        top_name, top_n = val_names[key].most_common(1)[0]
        rows.append(["value", key, cnt, len(val_maps[key]), len(val_names[key]), top_name, top_n, "", "", "", "",
                     "", "", "", "", "value"])

    records = sum(r[2] for r in rows if r[0] == "class")
    many = sum(1 for k in val_maps if len(val_maps[k]) >= MANY_MAPS)
    rows.append(["census", "records", records, records, "", "", "", "", "", "", "", "", "", "", "", "census"])
    rows.append(["census", "maps", len(maps), len(maps), "", "", "", "", "", "", "", "", "", "", "", "census"])
    rows.append(["census", "distinct_values", len(val_records), records, "", "", "", "", "", "", "", "", "", "", "",
                 "census"])
    rows.append(["census", "values_in_many_maps", many, len(val_records), "", "", "", "", "", "", "", "", "", "", "",
                 "census"])
    rows.append(["census", "classes", len(order), len(order), "", "", "", "", "", "", "", "", "", "", "", "census"])
    for k in ("as_name", "in_block", "in_block_control", "is_class", "is_tag", "halves_equal", "all_zero",
              "above_0x7f", "dword_is_offset", "dword_under_file"):
        rows.append(["census", "hit:" + k, hits[k], records, "", "", "", "", "", "", "", "", "", "", "", "census"])
    return rows


def selftest():
    """The readers the census rests on: the halves, the control, and the tag text."""
    problems = []
    if halves(b"\x30\x00\x30\x00") != (48, 48):
        problems.append("selftest: 30 00 30 00 does not read as the halves 48,48")
    if halves(b"\x6e\x00\x00\x00") != (110, 0):
        problems.append("selftest: 6e 00 00 00 does not read as the halves 110,0")
    if dword(b"\x30\x00\x30\x00") != 3145776:
        problems.append("selftest: the dword of 30 00 30 00 is not 3145776")
    if dword(b"\x00\x00\xcf\xf3") <= 0xFFFF:
        problems.append("selftest: a wide half does not lift the dword above the half band")
    if control_index(0, 0) != 0:
        problems.append("selftest: the control divides by an empty name table")
    if control_index(7, 4485) == control_index(8, 4485):
        problems.append("selftest: the control is constant across ordinals")
    if not (0 <= control_index(3983, 164) < 164):
        problems.append("selftest: the control leaves the name table")
    got = tag_value([{"name": "Tag", "value": "LevelInfo"}, {"name": "Other", "value": "x"}])
    if got != "LevelInfo":
        problems.append("selftest: a decoded Name tag does not read as text")
    got = tag_value([{"name": "Tag", "value": "Lev".encode("utf-16-le")}])
    if got != "Lev":
        problems.append("selftest: a UTF-16 tag does not read as text")
    if tag_value([{"name": "Other", "value": "x"}]) != "":
        problems.append("selftest: a block with no Tag does not read as empty")
    return problems


def check(outdir):
    """Shape, the row sums, the join with the record census, and the readings' denominators."""
    path = os.path.join(outdir, "actor_prefix_middle.csv")
    problems = selftest() + checkcsv.shape(path, COLUMNS, key="name")
    if problems:
        return problems
    matched = {}
    rpath = os.path.join(outdir, "actor_prefix_record.csv")
    if os.path.isfile(rpath):
        with open(rpath, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                matched[r["class"]] = int(r["matched"])
    else:
        problems.append("actor_prefix_record.csv is missing")
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    census = {r["name"]: r for r in rows if r["kind"] == "census"}
    classes = [r for r in rows if r["kind"] == "class"]
    if not classes:
        problems.append("no class rows")
    for r in classes:
        try:
            rec, distinct = int(r["records"]), int(r["distinct"])
            counts = [int(r[c]) for c in ("all_zero", "halves_equal", "above_0x7f", "as_name", "in_block",
                                          "in_block_control", "is_class", "is_tag")]
        except ValueError:
            problems.append("%s: a count is not a number" % r["name"])
            continue
        if rec < 1:
            problems.append("%s: a class row with no records" % r["name"])
        if not 1 <= distinct <= rec:
            problems.append("%s: %d distinct value(s) over %d record(s)" % (r["name"], distinct, rec))
        if any(c < 0 or c > rec for c in counts):
            problems.append("%s: a reading counts more than the class's records" % r["name"])
        if r["name"] in matched and matched[r["name"]] != rec:
            problems.append("%s: %d record(s) against the record census's %d matched"
                            % (r["name"], rec, matched[r["name"]]))
        if r["name"] not in matched:
            problems.append("%s: not in actor_prefix_record.csv" % r["name"])
        want = "constant" if distinct == 1 else ("repeats" if distinct < rec else "distinct")
        if r["verdict"] != want:
            problems.append("%s: verdict %s with %d distinct over %d record(s)"
                            % (r["name"], r["verdict"], distinct, rec))
        if int(r["top_count"]) > rec:
            problems.append("%s: the commonest value counts more than the class's records" % r["name"])
    total = sum(int(r["records"]) for r in classes)
    if "records" in census and int(census["records"]["records"]) != total:
        problems.append("census records %s against the class rows' %d"
                        % (census["records"]["records"], total))
    values = [r for r in rows if r["kind"] == "value"]
    if "distinct_values" in census:
        if int(census["distinct_values"]["records"]) < len(values):
            problems.append("census distinct_values is below the value rows it carries")
        if values and max(int(v["records"]) for v in values) > total:
            problems.append("a value row counts more records than the census")
    for r in values:
        if not 1 <= int(r["names"]) <= int(r["records"]):
            problems.append("value %s: %s distinct name(s) over %s record(s)"
                            % (r["name"], r["names"], r["records"]))
        if int(r["distinct"]) < 1 or int(r["distinct"]) > int(census.get("maps", {"records": 0})["records"]):
            problems.append("value %s: %s map(s)" % (r["name"], r["distinct"]))
        if int(r["records"]) > total:
            problems.append("value %s: more records than the census" % r["name"])
    for key, r in census.items():
        try:
            hit, den = int(r["records"]), int(r["distinct"])
        except ValueError:
            problems.append("census %s: a count is not a number" % key)
            continue
        if hit > den:
            problems.append("census %s: %d hit(s) over %d record(s)" % (key, hit, den))
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("maps_dir", nargs="?", help="the dump's Maps directory (not needed for --check)")
    ap.add_argument("-o", "--out", help="directory for the CSV (default: inventory/)")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    outdir = args.out or os.path.join(ROOT, "inventory")

    if args.check and not args.maps_dir:
        problems = check(outdir)
        return checkcsv.report("actor-prefix-middle", problems,
                               "actor_prefix_middle.csv is the shape this tool writes, every class row's readings "
                               "fit its record count, the record totals agree with actor_prefix_record.csv's "
                               "matched column, each verdict follows its own distinct/records pair and every "
                               "census hit fits its denominator (shape only: needs the dump)")

    if not args.maps_dir:
        ap.error("the Maps directory is required to rebuild the artifact")

    rows = compute(args.maps_dir)
    path = os.path.join(outdir, "actor_prefix_middle.csv")
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        w.writerow(COLUMNS)
        w.writerows(rows)
    census = {r[1]: r for r in rows if r[0] == "census"}
    print("wrote %s (%d rows)" % (path, len(rows)))
    print("%s record(s) over %s map(s), %s distinct value(s), %s in %d+ maps"
          % (census["records"][2], census["maps"][2], census["distinct_values"][2],
             census["values_in_many_maps"][2], MANY_MAPS))
    for k in ("as_name", "in_block", "in_block_control", "is_class", "is_tag", "dword_is_offset"):
        r = census["hit:" + k]
        print("  %-18s %6d / %6d (%.1f%%)" % (k, r[2], r[3], 100.0 * r[2] / max(r[3], 1)))


if __name__ == "__main__":
    sys.exit(main())
