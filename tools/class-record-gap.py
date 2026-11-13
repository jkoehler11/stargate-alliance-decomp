#!/usr/bin/env python3
"""What the gap after the class record's array is made of: a second array, then the name list.

`tools/class-record-element.py` closes 291 records by restarting the writer's own array grammar at the located
array's tail and then walking `UClass::Serialize`'s remaining fields to the defaults start. `docs/NATIVE_VM.md`
(the section *The reader writes nothing between the last field and the defaults*) reads the same bytes as the
`+0x4dc` name array plus the config `FName`, because the field walk's leftover is 20 bytes on 190 classes and
`first_count` is 2 on every one of them -- and says that split is not measured.

This tool measures it, on every class whose array the reader locates (`inventory/class_record_array.csv`), by
decomposing the gap instead of naming its fields:

* **the second array** -- `FUN_0003d900`'s grammar again, started at the located array's own tail: a compact count
  and, per record, one compact reference plus eight raw bytes. Its count, its byte length (`second_len`), whether
  every reference lands inside the package's object tables, and whether its count matches the first array's;
* **the rest** -- the bytes from that array's end to the defaults start, their length and their leading byte;
  read as the writer's *other* array (`FUN_0003d160`: a count and that many `(compact, compact)` pairs) it is
  `list_count` / `list_len` / `list_exact`, and `rest == 0` is a record that carries no list field at all;
* **the config-name reading, side by side** -- the 8 bytes immediately in front of the list are what the "name
  array + 8-byte config `FName`" reading calls the config: two int32s. The artifact carries them (`cfg_a`, `cfg_b`)
  and `cfg_ok` says whether they are *semantically* an `FName` (the first a name index inside the table, the second
  a duplicate counter of at most 64) -- the test the structural split cannot make;
* **which member it is** -- the two arrays' reference lists side by side (`first_refs`, `second_refs`):
  identical lists mean the writer packed one member twice, disjoint ones two members, and `ref_shared`
  counts the values in common; `first_kinds` / `second_kinds` name the class of each referenced object, so
  "a copy" and "a second class-reference list" are different rows rather than different opinions;
* **the reader's own field order** -- the gap re-read as `+0x4dc` names, the config `FName`, then `+0x4f4`
  names (`docs/NATIVE_VM.md`, *Only one call site writes the record array, and its reader stages the second member
  under that call*): `FUN_0003d160`'s file element is a compact pair, so all three are one stream of that shape.
  `nm_exact` says it closes on the defaults start, `nm_ctl_exact` how many shifted starts do;
* **the control** -- the same array grammar started at each of the next 8 bytes, counting the starts that parse and
  the ones whose end is exactly the defaults start. A grammar that fits the real position and the shifted ones
  about equally describes the arithmetic, not the record.

    python3 tools/class-record-gap.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-gap.py --dump $SG1_DUMP --class GamePlay.LavaZone
    python3 tools/class-record-gap.py --report
    python3 tools/class-record-gap.py --check

Reads the packages and one committed artifact; no boot, no Ghidra, no value decoded and no name published.
"""
import argparse
import collections
import csv
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402

ROOT = os.path.dirname(HERE)
ARTIFACT = "class_record_gap.csv"
SOURCE = "class_record_array.csv"        # the located array: its tail and the defaults start, per class
STATE_BYTES = 22                         # `UState::Serialize`: 8 + 8 + 2 + 4
CLASS_DWORDS = 5                         # the five dwords `UClass::Serialize` writes after `UState`
DOC_OFFSET = STATE_BYTES + 4 * CLASS_DWORDS
TRAILING = 8                             # the reference is compact, the two dwords after it are raw
MAX_COUNT = 4096
MAX_REF = 5
MAX_NUMBER = 64                          # an `FName` duplicate counter; larger is not a name
WINDOW = 8                               # the control's start offsets, 1..8 past the array's tail
COLUMNS = ["package", "class", "payload", "prefix_end", "block_start", "array_end", "gap", "shape",
           "first_count", "second_count", "second_len", "second_widths", "second_refs_ok", "second_zero",
           "ctl_parsed", "ctl_exact", "rest", "rest_lead", "list_count", "list_len", "list_exact",
           "cfg_a", "cfg_b", "cfg_ok",
           "first_refs", "second_refs", "ref_same", "ref_same_set", "ref_shared", "array_same",
           "first_kinds", "second_kinds",
           "nm_mid_count", "nm_mid_len", "nm_cfg_len", "nm_cfg_name", "nm_list_count", "nm_list_len",
           "nm_exact", "nm_ctl_exact", "nm_shape", "nm_mid_rest"]


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


def walk_array(buf, p):
    """`FUN_0003d900`'s element stream: a compact count, then count x (compact reference, 8 raw bytes)."""
    if p < 0 or p >= len(buf):
        return None
    try:
        count, o = read_index(buf, p)
    except IndexError:
        return None
    if count < 1 or count > MAX_COUNT:
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
        refs.append(v)
        widths.append(e - o)
        o = e + TRAILING
    return {"count": count, "refs": refs, "widths": widths, "end": o, "len": o - p}


def walk_pairs(buf, p):
    """`FUN_0003d160`'s other array: a compact count, then count x (compact, compact). A count of zero is empty."""
    if p < 0 or p >= len(buf):
        return None
    try:
        count, o = read_index(buf, p)
    except IndexError:
        return None
    if count < 0 or count > MAX_COUNT:
        return None
    widths = []
    for _ in range(count):
        for _ in range(2):
            if o >= len(buf):
                return None
            try:
                _, e = read_index(buf, o)
            except IndexError:
                return None
            widths.append(e - o)
            o = e
    return {"count": count, "widths": widths, "end": o, "len": o - p}


def walk_name_gap(data, array_end, block, nnames):
    """The same gap in the reader's own field order: `+0x4dc` names, the config name, then `+0x4f4` names.

    `UClass::Serialize` writes three things between the record array's tail and the defaults buffer, in this order:
    the `+0x4dc` array through `FUN_0003d160`, the config name through two archive slots on adjacent members
    (`+0x18` on `+0x4b0`, then `+0x1c` on `+0x4b4`), and the `+0x4f4` array through the same writer -- and
    `FUN_0003d160`'s file element is a compact pair, so the whole gap is one stream of that shape. Closing exactly
    on the defaults start is what distinguishes this split from the twelve-byte second array the same bytes also
    parse as; the shifted starts are the control.
    """
    out = {"nm_mid_count": "", "nm_mid_len": "", "nm_cfg_len": "", "nm_cfg_name": "", "nm_list_count": "",
           "nm_list_len": "", "nm_exact": 0, "nm_ctl_exact": 0, "nm_shape": "", "nm_mid_rest": ""}

    def run(p, stop):
        mid = walk_pairs(data, p)
        if not mid or mid["end"] + 2 > stop:
            return None
        q = mid["end"]
        try:
            name, q = read_index(data, q)
            _number, q = read_index(data, q)
        except IndexError:
            return None
        lst = walk_pairs(data, q)
        if not lst or lst["end"] != stop:
            return None
        return mid, q - mid["end"], name, lst

    alone = walk_pairs(data, array_end)
    if alone and alone["end"] <= block:
        out.update({"nm_mid_count": alone["count"], "nm_mid_len": alone["len"],
                    "nm_mid_rest": block - alone["end"]})
    got = run(array_end, block)
    if got is None:
        out["nm_shape"] = "open"
    else:
        mid, cfg_len, name, lst = got
        out.update({"nm_cfg_len": cfg_len,
                    "nm_cfg_name": int(0 <= name < nnames), "nm_list_count": lst["count"],
                    "nm_list_len": lst["len"], "nm_exact": 1, "nm_shape": "names"})
    for q in range(array_end + 1, array_end + 1 + WINDOW):
        if run(q, block):
            out["nm_ctl_exact"] += 1
    return out


def kind_of(pkg, v):
    """What one array reference points at: an export's own class, an import's class name, or null."""
    if v == 0:
        return "null"
    if v > 0:
        return pkg.class_name_of(pkg.exports[v - 1]) if v <= len(pkg.exports) else "out-of-range"
    i = -v - 1
    if i < len(pkg.imports):
        return "import:" + pkg.name(pkg.imports[i]["class"])
    return "out-of-range"


def kinds(pkg, refs):
    return "+".join("%s:%d" % kv for kv in sorted(collections.Counter(kind_of(pkg, v) for v in refs).items()))


def split_refs(text):
    return [int(x) for x in text.split("+")] if text else []


def in_tables(v, nimp, nex):
    return v == 0 or (v > 0 and v <= nex) or (v < 0 and -v <= nimp)


def measure(data, prefix_end, block, array_end, nimp, nex, nnames):
    """One class: the gap from the located array's tail, split into a second array and what follows it."""
    row = {"prefix_end": prefix_end, "block_start": block, "array_end": array_end, "gap": block - array_end,
           "shape": "none", "first_count": "", "second_count": "", "second_len": "",
           "second_widths": "", "second_refs_ok": "", "second_zero": "", "ctl_parsed": 0, "ctl_exact": 0,
           "rest": "", "rest_lead": "", "list_count": "", "list_len": "", "list_exact": "",
           "cfg_a": "", "cfg_b": "", "cfg_ok": "",
           "first_refs": "", "second_refs": "", "ref_same": "", "ref_same_set": "", "ref_shared": "",
           "array_same": ""}
    first = walk_array(data, prefix_end + DOC_OFFSET)
    if first:
        row["first_count"] = first["count"]
        row["first_refs"] = "+".join(str(v) for v in first["refs"])
    second = walk_array(data, array_end)
    if second and second["end"] <= block:
        row.update({"shape": "second", "second_count": second["count"], "second_len": second["len"],
                    "second_widths": "+".join(str(w) for w in second["widths"]),
                    "second_refs_ok": int(all(in_tables(v, nimp, nex) for v in second["refs"])),
                    "second_zero": int(not any(second["refs"])),
                    "second_refs": "+".join(str(v) for v in second["refs"])})
        p = second["end"]
        if first:
            # Identical lists mean the writer packed the same member twice; disjoint ones mean two members.
            row["ref_same"] = int(first["refs"] == second["refs"])
            row["ref_same_set"] = int(collections.Counter(first["refs"]) == collections.Counter(second["refs"]))
            row["ref_shared"] = sum((collections.Counter(first["refs"])
                                     & collections.Counter(second["refs"])).values())
            row["array_same"] = int(data[prefix_end + DOC_OFFSET:first["end"]]
                                    == data[array_end:second["end"]])
    else:
        p = array_end
        if walk_array(data, array_end) or walk_pairs(data, array_end):
            row["shape"] = "list-only"
    rest = block - p
    row["rest"] = rest
    if 0 <= rest and p < len(data):
        row["rest_lead"] = data[p]
    pairs = walk_pairs(data, p)
    if pairs and pairs["end"] <= block:
        row.update({"list_count": pairs["count"], "list_len": pairs["len"],
                    "list_exact": int(pairs["end"] == block)})
    # The competing reading: the 8 bytes in front of the list are the config FName, two int32s.
    if rest > 0:
        start = block - rest - 8
        if array_end <= start and start + 8 <= len(data):
            a = int.from_bytes(data[start:start + 4], "little", signed=True)
            b = int.from_bytes(data[start + 4:start + 8], "little", signed=True)
            row["cfg_a"], row["cfg_b"] = a, b
            row["cfg_ok"] = int(0 <= a < nnames and 0 <= b <= MAX_NUMBER)
    row.update(walk_name_gap(data, array_end, block, nnames))
    for q in range(array_end + 1, array_end + 1 + WINDOW):
        got = walk_array(data, q)
        if not got:
            continue
        row["ctl_parsed"] += 1
        if got["end"] == block:
            row["ctl_exact"] += 1
    return row


def payload(pkg, cls):
    for exp in pkg.exports:
        if pkg.class_name_of(exp) == "Class" and pkg.name(exp["name"]) == cls:
            return pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    return None


def read_source(path=None):
    """The located array's own measurement: (package, class) -> (prefix_end, block_start, array_end)."""
    path = path or os.path.join(ROOT, "inventory", SOURCE)
    prefixes = {}
    with open(os.path.join(ROOT, "inventory", "class_prefixes.csv"), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["prefix_end"] and r["block_start"]:
                prefixes[(r["package"], r["class"])] = (int(r["prefix_end"]), int(r["block_start"]))
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            key = (r["package"], r["class"])
            if key not in prefixes or not r["array_end"]:
                continue
            prefix_end, block = prefixes[key]
            try:
                out[key] = (prefix_end, block, int(r["array_end"]))
            except ValueError:
                continue
    return out


def sweep(args):
    located = read_source()
    rows = []
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
            if key not in located:
                continue
            prefix_end, block, array_end = located[key]
            data = payload(pkg, key[1])
            if data is None:
                continue
            row = {"package": name, "class": key[1], "payload": len(data)}
            row.update(measure(data, prefix_end, block, array_end, len(pkg.imports), len(pkg.exports),
                               len(pkg.names)))
            if row["first_refs"]:
                row["first_kinds"] = kinds(pkg, split_refs(row["first_refs"]))
            if row["second_refs"]:
                row["second_kinds"] = kinds(pkg, split_refs(row["second_refs"]))
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


def _int(rows, key):
    return [int(r[key]) for r in rows if r[key] not in ("", None)]


def _median(rows, key):
    vals = _int(rows, key)
    return statistics.median(vals) if vals else 0


def report(rows):
    rows = [r for r in rows if r.get("block_start") not in ("", "0", 0)]
    for r in rows:
        for k in ("gap", "array_end", "block_start", "first_count", "second_count", "second_len",
                  "second_refs_ok", "second_zero", "ctl_parsed", "ctl_exact", "rest", "rest_lead",
                  "list_count", "list_len", "list_exact", "cfg_a", "cfg_b", "cfg_ok",
                  "ref_same", "ref_same_set", "ref_shared", "array_same"):
            if r[k] not in ("", None):
                r[k] = int(r[k])
    print("located classes: %d" % len(rows))
    gaps = _int(rows, "gap")
    print("the gap from the array's tail to the defaults start: median %d, mode %s"
          % (statistics.median(gaps), collections.Counter(gaps).most_common(1)))

    second = [r for r in rows if r["shape"] == "second"]
    print("a second array of the writer's grammar at the array's tail: %d of %d (%.1f%%)"
          % (len(second), len(rows), 100.0 * len(second) / len(rows)))
    if second:
        print("  records per array: %s" % collections.Counter(r["second_count"] for r in second).most_common(6))
        print("  byte length: median %d, most common %s"
              % (_median(second, "second_len"),
                 collections.Counter(r["second_len"] for r in second).most_common(4)))
        print("  reference widths (one per record): %s"
              % collections.Counter(r["second_widths"] for r in second).most_common(5))
        both = [r for r in second if r["first_count"] not in ("", None)]
        print("  its count equal to the first array's: %d of %d"
              % (sum(1 for r in both if r["second_count"] == r["first_count"]), len(both)))
        print("  every reference inside the package's object tables: %d of %d"
              % (sum(1 for r in second if r["second_refs_ok"]), len(second)))
        print("  all-null records: %d" % sum(1 for r in second if r["second_zero"]))
        cmp = [r for r in second if r["ref_same"] not in ("", None)]
        print("  its reference list against the first array's: identical %d, same values in another order %d, "
              "sharing one or more values %d, disjoint %d"
              % (sum(1 for r in cmp if r["ref_same"]), sum(1 for r in cmp if r["ref_same_set"]),
                 sum(1 for r in cmp if r["ref_shared"]), sum(1 for r in cmp if r["ref_shared"] == 0)))
        print("  identical bytes over the whole array: %d of %d"
              % (sum(1 for r in cmp if r["array_same"]), len(cmp)))
        for label, key in (("the first array points at", "first_kinds"), ("the second array points at", "second_kinds")):
            total = collections.Counter()
            for r in cmp:
                for part in (r[key] or "").split("+"):
                    if ":" in part:
                        name, n = part.rsplit(":", 1)
                        total[name] += int(n)
            print("  %s: %s" % (label, total.most_common(6)))
    print("no second array parses: %d (a list directly at the tail: %d)"
          % (len(rows) - len(second), sum(1 for r in rows if r["shape"] == "list-only")))
    print("the same grammar started 1..%d bytes later: %d starts parse, %d land on the defaults start"
          % (WINDOW, sum(r["ctl_parsed"] for r in rows if r["ctl_parsed"] not in ("", None)),
             sum(r["ctl_exact"] for r in rows if r["ctl_exact"] not in ("", None))))

    rest = [r for r in rows if r["rest"] not in ("", None)]
    print("the bytes after that array: %s" % collections.Counter(r["rest"] for r in rest).most_common(6))
    print("  no list at all (0 bytes): %d; a single byte: %d (leading byte %s)"
          % (sum(1 for r in rest if r["rest"] == 0), sum(1 for r in rest if r["rest"] == 1),
             collections.Counter(r["rest_lead"] for r in rest if r["rest"] == 1).most_common(4)))
    pairs = [r for r in rest if r["list_exact"] == 1]
    print("  reads as the writer's count-plus-pairs array and closes exactly: %d of %d (%.1f%%)"
          % (len(pairs), len(rest), 100.0 * len(pairs) / len(rest)))
    if pairs:
        print("  pairs per list: %s" % collections.Counter(r["list_count"] for r in pairs).most_common(4))

    cfg = [r for r in rest if r["cfg_a"] not in ("", None)]
    if cfg:
        print("the 8 bytes the config-name reading calls a name: first int32 %s"
              % collections.Counter(r["cfg_a"] for r in cfg).most_common(3))
        print("  both int32s an FName (a name index, a counter <= %d): %d of %d"
              % (MAX_NUMBER, sum(1 for r in cfg if r["cfg_ok"]), len(cfg)))
        big = [r["cfg_b"] for r in cfg if not r["cfg_ok"] and r["cfg_b"] not in ("", None)]
        if big:
            print("  the others: median |a| %d, median |b| %d, largest |b| %d"
                  % (statistics.median([abs(r["cfg_a"]) for r in cfg if not r["cfg_ok"]]),
                     statistics.median([abs(b) for b in big]), max(abs(b) for b in big)))
    names = [r for r in rows if r["nm_exact"] not in ("", None)]
    if names:
        exact = sum(1 for r in names if r["nm_exact"] == 1)
        print("the same gap in the reader's field order (+0x4dc names, the config name, +0x4f4 names): "
              "%d of %d close exactly (%.1f%%)"
              % (exact, len(names), 100.0 * exact / len(names)))
        parsed = [r for r in names if r["nm_mid_count"] not in ("", None)]
        print("  the middle alone as a count-plus-pairs list: %d of %d, count %s, median %d bytes, leaving %s"
              % (len(parsed), len(names),
                 collections.Counter(r["nm_mid_count"] for r in parsed).most_common(4),
                 _median(parsed, "nm_mid_len"), collections.Counter(r["nm_mid_rest"] for r in parsed).most_common(4)))
        print("  the second name array closes on %d; the config's first field is a name index on %d"
              % (sum(1 for r in names if r["nm_list_count"] not in ("", None)),
                 sum(1 for r in names if r["nm_cfg_name"] == 1)))
        print("  the control at 1..%d bytes later closes on %d"
              % (WINDOW, sum(int(r["nm_ctl_exact"] or 0) for r in names)))

    twenty = [r for r in rest if r["gap"] == 27]
    if twenty:
        print("the 27-byte gap rows (the field walk's 20-byte leftover plus its 7-byte list): %d, a second array "
              "of median %d bytes and a list of median %d"
              % (len(twenty), _median(twenty, "second_len"), _median(twenty, "list_len")))


def one(args):
    located = read_source()
    key = (args.package, args.cls)
    pkg = Package(os.path.join(args.dump, "System", args.package + ".u"))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    if key not in located:
        print("%s.%s is not a located class" % key)
        return 1
    prefix_end, block, array_end = located[key]
    data = payload(pkg, key[1])
    if data is None:
        print("%s.%s has no payload" % key)
        return 1
    row = {"package": key[0], "class": key[1], "payload": len(data)}
    row.update(measure(data, prefix_end, block, array_end, len(pkg.imports), len(pkg.exports), len(pkg.names)))
    print("prefix_end %d, array_end %d, block_start %d, gap %d" % (prefix_end, array_end, block, row["gap"]))
    print("array at the tail: count %s, %s bytes, refs ok %s, all-null %s"
          % (row["second_count"], row["second_len"], row["second_refs_ok"], row["second_zero"]))
    print("after it: %s bytes (leading %s), list count %s len %s exact %s"
          % (row["rest"], row["rest_lead"], row["list_count"], row["list_len"], row["list_exact"]))
    print("config reading: %s, %s -> an FName %s" % (row["cfg_a"], row["cfg_b"], bool(row["cfg_ok"])))
    print("gap bytes: %s" % data[array_end:block].hex(" "))
    return 0


def self_check():
    """Synthetic record: a 3-record array, then a 2-record array, then a 3-pair list closing on the block."""
    def compact(v):
        """The reader above, inverted: bit 7 is the sign, bit 6 means a byte follows."""
        out = bytearray([(abs(v) & 0x3F) | (0x80 if v < 0 else 0)])
        rest = abs(v) >> 6
        if rest:
            out[0] |= 0x40
            while rest:
                b, rest = rest & 0x7F, rest >> 7
                out.append(b | (0x80 if rest else 0))
        return bytes(out)

    data = bytes(2) + bytes(STATE_BYTES + 4 * CLASS_DWORDS)
    prefix_end = 2
    data += compact(3) + (compact(1) + b"\x01\x00\x00\x00" + b"\x02\x00\x00\x00") * 3
    array_end = len(data)
    data += compact(2) + (compact(1) + b"\x01\x00\x00\x00" + b"\x02\x00\x00\x00")
    data += (compact(-3) + b"\x03\x00\x00\x00" + b"\x04\x00\x00\x00")
    data += compact(3) + compact(1) + compact(4) + compact(2) + compact(-4) + compact(3) + compact(6)
    block = len(data)
    data += b"\xaa\xbb\xcc\xdd" * 4
    row = measure(data, prefix_end, block, array_end, 40, 4000, 100)
    assert row["first_count"] == 3, row
    assert row["gap"] == block - array_end, row
    assert row["shape"] == "second", row
    assert row["second_count"] == 2 and row["second_len"] == 19, row
    assert row["second_refs_ok"] == 1 and row["second_zero"] == 0, row
    assert row["rest"] == 7, row
    assert row["list_count"] == 3 and row["list_exact"] == 1, row
    assert row["ctl_exact"] == 0, "a shifted start must not land on the block: %r" % row
    assert row["ref_same"] == 0 and row["ref_same_set"] == 0 and row["ref_shared"] == 1, row
    assert row["array_same"] == 0 and row["first_refs"] == "1+1+1", row

    # The same array packed twice: identical values in identical order, byte for byte.
    span = data[prefix_end + DOC_OFFSET:array_end]
    body = data[:array_end] + span
    row = measure(body, prefix_end, len(body), array_end, 40, 4000, 100)
    assert row["second_count"] == 3 and row["second_len"] == len(span), row
    assert row["ref_same"] == 1 and row["ref_same_set"] == 1 and row["array_same"] == 1, row
    assert row["ref_shared"] == 3 and row["rest"] == 0, row

    # An empty list is a zero count byte, and the record still closes on it.
    body = data[:block - 7] + compact(0)
    row = measure(body, prefix_end, len(body), array_end, 40, 4000, 100)
    assert row["rest"] == 1 and row["rest_lead"] == 0, row
    assert row["list_count"] == 0 and row["list_exact"] == 1, row

    # The same gap read in the reader's order: names, the config name, names -- and it closes on the block too.
    body = data[:array_end] + (compact(3) + compact(1) + compact(1) + compact(3) + compact(2)
                               + compact(4) + compact(5)
                               + compact(30) + compact(70)
                               + compact(2) + compact(6) + compact(8) + compact(9) + compact(10))
    row = measure(body, prefix_end, len(body), array_end, 40, 4000, 100)
    assert row["nm_exact"] == 1, row
    assert (row["nm_mid_count"], row["nm_mid_len"], row["nm_cfg_len"], row["nm_list_count"]) == (3, 7, 3, 2), row
    assert row["nm_cfg_name"] == 1 and row["nm_ctl_exact"] == 0, row

    # A record that carries no list field at all.
    body = data[:block - 7]
    row = measure(body, prefix_end, len(body), array_end, 40, 4000, 100)
    assert row["rest"] == 0 and row["list_count"] == "", row
    print("self-check ok: a 2-record array of 19 bytes, a 3-pair list, an empty list, and no list")
    return 0


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
        path = os.path.join(ROOT, "inventory", ARTIFACT)
        with open(path, newline="", encoding="utf-8") as fh:
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
