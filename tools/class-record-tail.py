#!/usr/bin/env python3
"""What the bytes after the located record array actually are: a count and name/reference pairs, not `FName`s.

`tools/class-record-element.py` closes the record on 291 classes by restarting the array grammar at the located
array's tail and then walking `UClass::Serialize`'s remaining fields -- up to two name arrays and the config name --
to the defaults start. `docs/NATIVE_VM.md` reads those fields as `TArray<FName>`: a compact count, then two compact
indices per element, because `FUN_0003d160` writes each element through archive vtable **`+0x1c`, the name method**.

This tool measures the bytes instead of naming them, on every row where a second array parses (`second_count` in the
element table):

  * the tail's **width** (`block_start - second_end`), its first compact value, and the pairs a two-index element
    yields -- the integers only, no names;
  * whether an element's **second value is negative**. An `FName` number is an unsigned count, so a negative second
    value rules the two-`TArray<FName>` reading out for that element; the artifact carries `negatives` and
    `negative_at` so the claim is re-checkable from the CSV;
  * the same bytes read the other way -- the leading value as the **first name's index** and the rest as a bare
    `(index, number)` sequence -- so "count or first index" is a measurement (`fname_pairs`, `fname_exact`) rather
    than a reading. A seven-byte tail holds three two-byte names and one byte over, so this reading cannot close
    where the count reading does;
  * what each pair's **first value names**, as a token rather than a string -- the package's own name (`pkg`),
    `Core`, `System` or `other` -- so "the list is package-level content" is a column, not a reading of a name;
  * whether each pair's **second value fits the archive's reference encoding** (0 null, negative = import
    `-v-1`, positive = export `v-1`), and when it is negative *which import it names* (`neg_target`), with the
    import one index earlier as the control (`neg_shift`) -- the CSV alone then answers "one object, or chance";
  * the **trailing** bytes after the last pair, which are what a name array's count cannot consume;
  * the tail's own digest, so *identical across classes* is a measured fact: the report groups the rows by package
    and prints how much of each package shares one tail, which is what separates package-level content from class
    data.

    python3 tools/class-record-tail.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-tail.py --class WaterZone --package GamePlay --dump $SG1_DUMP
    python3 tools/class-record-tail.py --report
    python3 tools/class-record-tail.py --check

Reads the packages and one committed CSV; no boot, no Ghidra, no value decoded and no name published.
"""
import argparse
import collections
import csv
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402

ROOT = os.path.dirname(HERE)
ARTIFACT = "class_record_tail.csv"
SOURCE = "class_record_element.csv"          # the array's tail and the defaults start, per class
MAX_COUNT = 4096
MAX_ELEMENT = 8                              # a pair of compact indices is at most 4 bytes each
COLUMNS = ["package", "class", "exact", "tail_start", "block_start", "width", "count", "pairs", "negatives",
           "negative_at", "trail", "names_ok", "ctl_hits", "fname_pairs", "fname_exact", "refs", "neg_target",
           "neg_shift", "names_kind", "def_exact", "earlier_exact", "hash"]
DEFAULTS_LIB = "prop-stream.py"              # the property-tag parser `class-defaults.py` already uses


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


def load_defaults_reader():
    """`tools/prop-stream.py` by path: `read_block` is the tag parser `class-defaults.py` locates blocks with."""
    import importlib.util
    path = os.path.join(HERE, DEFAULTS_LIB)
    spec = importlib.util.spec_from_file_location("prop_stream", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tag_stream_closes(mod, data, start, pkg):
    """Does the defaults property stream parse from `start` and end exactly on the payload's last byte?"""
    if start < 0 or start >= len(data):
        return 0
    try:
        props, end, reason = mod.read_block(data[start:], pkg, False)
    except Exception:  # noqa: BLE001
        return 0
    if reason != "None" or not props or any(p["name"] == "?" for p in props):
        return 0
    return int(start + end == len(data))


def walk_pairs(buf, p, end):
    """The tail as a count plus `count` x (compact, compact) pairs, and whatever is left over."""
    if p < 0 or p >= len(buf):
        return None
    try:
        count, off = read_index(buf, p)
    except IndexError:
        return None
    if count < 0 or count > MAX_COUNT:
        return None
    pairs = []
    for _ in range(count):
        if off >= end:
            return None
        vals = []
        for _ in range(2):
            if off >= end:
                return None
            try:
                v, e = read_index(buf, off)
            except IndexError:
                return None
            if e - off > MAX_ELEMENT:
                return None
            vals.append(v)
            off = e
        pairs.append(vals)
    if off > end:
        return None
    return {"count": count, "pairs": pairs, "trail": buf[off:end]}


def walk_fnames(buf, p, end):
    """The same bytes as a bare `FName` sequence: the leading value is the first name's index, not a count.

    This is the `(index, number)` shape the name method was *assumed* to write; measured, the archive's name slot
    (`+0x1c`, `tools/archive-slots.py`) writes ONE compact index per element, and a name index is unsigned. The
    reading is kept because it is the competing one the count parse has to beat, and because its failure is what
    says the pair list is not the name arrays: it consumes the tail exactly only when the byte count is even and no
    value straddles `end`. `exact` says it did; `fnames` is how many names the reading got before it ran out.
    """
    fnames, off = [], p
    while off < end:
        try:
            idx, off = read_index(buf, off)
            num, off = read_index(buf, off)
        except IndexError:
            return {"fnames": fnames, "exact": False}
        if off > end:
            return {"fnames": fnames, "exact": False}
        fnames.append([idx, num])
    return {"fnames": fnames, "exact": True}


def resolve_ref(value, imports, nexp):
    """`ue2pkg.class_name_of`'s encoding, applied to a tail field: 0 null, `-v-1` import, `v-1` export.

    `imports` is a list of `(class name, object name)` pairs in import-table order, so a negative value answers
    *which object* it names and not merely that it is in range. A positive value is only range-checked: the export
    table's names are the package's own content and are not carried into the artifact.
    """
    if value == 0:
        return "null"
    if value < 0:
        i = -value - 1
        if imports is None or not (0 <= i < len(imports)):
            return None
        cls, name = imports[i]
        return "%s.%s" % (cls, name)
    return "exp" if 0 <= value - 1 < nexp else None


def measure(data, tail_start, block, nnames, imports=None, nexp=0, classify=None):
    row = {"tail_start": tail_start, "block_start": block, "width": block - tail_start,
           "hash": hashlib.sha1(data[tail_start:block]).hexdigest()[:12]}
    got = walk_pairs(data, tail_start, block)
    if not got:
        row["count"] = ""
        return row
    row["count"] = got["count"]
    row["pairs"] = ";".join("%d:%d" % (a, b) for a, b in got["pairs"])
    neg = [i for i, (_a, b) in enumerate(got["pairs"]) if b < 0]
    row["negatives"] = len(neg)
    row["negative_at"] = ";".join(str(i) for i in neg)
    row["trail"] = got["trail"].hex()
    row["names_ok"] = int(all(0 <= a < nnames for a, _b in got["pairs"]))
    # control: the same pair grammar started one to three bytes later, required to end exactly on the block start
    hits = 0
    for off in (1, 2, 3):
        other = walk_pairs(data, tail_start + off, block)
        if other and not other["trail"]:
            hits += 1
    row["ctl_hits"] = hits
    fname = walk_fnames(data, tail_start, block)
    row["fname_pairs"] = len(fname["fnames"]) if fname else ""
    row["fname_exact"] = int(bool(fname and fname["exact"]))
    refs, target, shift = 0, "", ""
    for _a, b in got["pairs"]:
        where = resolve_ref(b, imports, nexp)
        if where is None:
            continue
        refs += 1
        if b < 0 and not target:
            target = where
            shift = resolve_ref(b + 1, imports, nexp) or ""     # one import earlier: -(i-1)-1 = b+1
    row["refs"] = refs
    row["neg_target"] = target
    row["neg_shift"] = shift
    row["names_kind"] = ";".join(classify(a) for a, _b in got["pairs"]) if classify else ""
    return row


def read_source(path=None):
    """(package, class) -> (second_end, block_start, exact)"""
    path = path or os.path.join(ROOT, "inventory", SOURCE)
    out = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if not r.get("second_count"):
                continue
            try:
                out[(r["package"], r["class"])] = (int(r["second_end"]), int(r["block_start"]), r["exact"])
            except (KeyError, TypeError, ValueError):
                continue
    return out


def payload(pkg, cls):
    for exp in pkg.exports:
        if pkg.class_name_of(exp) == "Class" and pkg.name(exp["name"]) == cls:
            return pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    return None


def sweep(args):
    src = read_source()
    by_pkg = collections.defaultdict(list)
    for (pkg_name, cls), vals in src.items():
        by_pkg[pkg_name].append((cls, vals))
    ps = load_defaults_reader()
    rows = []
    for pkg_name in sorted(by_pkg):
        path = os.path.join(args.dump, "System", pkg_name + ".u")
        if not os.path.exists(path):
            print("missing package %s" % pkg_name, file=sys.stderr)
            continue
        pkg = Package(path)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        nnames = len(pkg.names or [])
        imports = [(pkg.name(im["class"]), pkg.name(im["name"])) for im in (pkg.imports or [])]
        nexp = len(pkg.exports or [])

        def classify(v, names=pkg.names or [], own=pkg_name):
            """The name a first value points at, as a token: no string of the package's own content is written."""
            if not (0 <= v < len(names)):
                return "?"
            text = names[v][0]
            return "pkg" if text == own else ("Core" if text == "Core" else ("System" if text == "System" else "other"))
        cache = {}
        for cls, (start, block, exact) in sorted(by_pkg[pkg_name]):
            if exact != "1":
                continue                    # only the rows whose record closes prove these bytes are the tail
            if cls not in cache:
                cache[cls] = payload(pkg, cls)
            data = cache[cls]
            if not data or block > len(data):
                continue
            row = {"package": pkg_name, "class": cls, "exact": exact}
            row.update(measure(data, start, block, nnames, imports, nexp, classify))
            row["def_exact"] = tag_stream_closes(ps, data, block, pkg)
            row["earlier_exact"] = tag_stream_closes(ps, data, start, pkg)
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
    parsed = [r for r in rows if r["count"]]
    exact = [r for r in rows if r["exact"] == "1"]
    widths = collections.Counter(int(r["width"]) for r in rows)
    counts = collections.Counter(int(r["count"]) for r in parsed)
    neg = [r for r in parsed if int(r["negatives"] or 0) > 0]
    neg_mid = [r for r in neg if r["negative_at"].split(";") == ["1"]]
    neg_vals = collections.Counter()
    for r in parsed:
        for pair in filter(None, (r["pairs"] or "").split(";")):
            _a, b = pair.split(":")
            if int(b) < 0:
                neg_vals[int(b)] += 1
    trail = collections.Counter(r["trail"] for r in rows if r["trail"] != "")
    names_ok = [r for r in parsed if r["names_ok"] == "1"]
    print("rows (the record closes)             %d" % len(rows))
    print("the tail parses as count + pairs     %d" % len(parsed))
    print("a pair's second value is negative    %d (%.1f%%), middle pair %d"
          % (len(neg), 100.0 * len(neg) / max(1, len(parsed)), len(neg_mid)))
    print("distinct negative values             %d %s" % (len(neg_vals), neg_vals.most_common(6)))
    fname_exact = [r for r in rows if r.get("fname_exact") == "1"]
    three = [r for r in parsed if r["count"] == "3"]
    print("leading value as a first name index  %d of %d tails close (%d of the %d three-pair tails)"
          % (len(fname_exact), len(rows), sum(1 for r in fname_exact if r["count"] == "3"), len(three)))
    refs_ok = [r for r in parsed if int(r["refs"] or 0) == int(r["count"])]
    print("every pair's second value is a ref   %d of %d parsed tails" % (len(refs_ok), len(parsed)))
    kinds = collections.Counter(r["names_kind"] for r in parsed if r.get("names_kind"))
    print("pair's first value names             %s" % kinds.most_common(6))
    tgt = collections.Counter(r["neg_target"] for r in parsed if r.get("neg_target"))
    shf = collections.Counter(r["neg_shift"] for r in parsed if r.get("neg_shift"))
    if tgt:
        print("the negative term names              %d of %d rows: %s"
              % (sum(tgt.values()), len(parsed), tgt.most_common(4)))
        print("control, that import one earlier     %d distinct %s" % (len(shf), shf.most_common(4)))
    print("first values inside the name table   %d of %d" % (len(names_ok), len(parsed)))
    print("tail widths                          %s" % widths.most_common(8))
    print("element counts                       %s" % counts.most_common(8))
    print("trailing bytes                       %s" % trail.most_common(6))
    print("control: same grammar 1-3 bytes later %d of %d land on the block start"
          % (sum(1 for r in parsed if int(r["ctl_hits"] or 0) > 0), len(parsed)))
    print("defaults stream closes from block_start    %d of %d"
          % (sum(1 for r in rows if r["def_exact"] == "1"), len(rows)))
    print("defaults stream closes from the tail       %d of %d"
          % (sum(1 for r in rows if r["earlier_exact"] == "1"), len(rows)))
    print("")
    print("%-18s %5s %5s %5s  %s" % ("package", "rows", "tails", "share", "commonest tail"))
    by_pkg = collections.defaultdict(list)
    for r in rows:
        by_pkg[r["package"]].append(r)
    for pkg_name in sorted(by_pkg):
        group = by_pkg[pkg_name]
        seen = collections.Counter(r["hash"] for r in group)
        top, n = seen.most_common(1)[0]
        print("%-18s %5d %5d %4d%%  %s x%d" % (pkg_name, len(group), len(seen), 100 * n // len(group), top, n))
    return 0


def one(args):
    src = read_source()
    key = (args.package, args.cls)
    if key not in src:
        print("no array tail for %s.%s" % key)
        return 1
    pkg = Package(os.path.join(args.dump, "System", args.package + ".u"))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    data = payload(pkg, args.cls)
    if not data:
        print("class not found")
        return 1
    start, block, exact = src[key]
    row = {"package": args.package, "class": args.cls, "exact": exact}
    imports = [(pkg.name(im["class"]), pkg.name(im["name"])) for im in (pkg.imports or [])]
    row.update(measure(data, start, block, len(pkg.names or []), imports, len(pkg.exports or [])))
    for k in COLUMNS:
        print("%-16s %s" % (k, row.get(k, "")))
    print("%-16s %s" % ("bytes", data[start:block].hex()))
    return 0


def self_check():
    """Synthetic tails: a pair list, a negative number, a trailing byte, and the width the caller hands in."""
    body = bytes([0x03, 0x01, 0x04, 0x02, 0x84, 0x03, 0x00])
    got = walk_pairs(body, 0, len(body))
    assert got and got["count"] == 3, got
    assert got["pairs"] == [[1, 4], [2, -4], [3, 0]], got
    assert got["trail"] == b"", "a three-pair tail consumes the whole body"
    row = measure(body, 0, len(body), 5)
    assert row["negatives"] == 1 and row["negative_at"] == "1", row
    assert row["names_ok"] == 1, row
    # the widths the pairs occupy are variable: a two-byte index and a two-byte number are legal
    wide = bytes([0x01, 0x44, 0x1c, 0x41, 0x02])
    w = walk_pairs(wide, 0, len(wide))
    assert w and w["pairs"] == [[0x704, 0x81]], w
    # a tail one byte too wide leaves that byte as trail; one byte too narrow cannot hold the pairs
    short = walk_pairs(body, 0, len(body) - 1)
    assert short is None, "a truncated pair list is not a tail"
    roomy = walk_pairs(body + b"\x00", 0, len(body) + 1)
    assert roomy and roomy["trail"] == b"\x00", roomy
    zero = walk_pairs(bytes([0x00]), 0, 1)
    assert zero and zero["count"] == 0 and zero["pairs"] == [], zero
    # the same seven bytes as a bare FName sequence: three names and one byte over, so it cannot close
    f = walk_fnames(body, 0, len(body))
    assert f and not f["exact"], f
    six = bytes([0x02, 0x01, 0x02, 0x83, 0x03, 0x00])
    f6 = walk_fnames(six, 0, len(six))
    assert f6 and f6["exact"] and len(f6["fnames"]) == 3, f6
    # references: 0 is null, -v-1 is an import, v-1 is an in-range export
    imps = [("Package", "Core"), ("Package", "Engine"), ("Class", "Object"), ("Class", "Object")]
    assert resolve_ref(0, imps, 4) == "null"
    assert resolve_ref(-4, imps, 4) == "Class.Object"
    assert resolve_ref(-3, imps, 4) == "Class.Object"
    assert resolve_ref(4, imps, 4) == "exp"
    assert resolve_ref(9, imps, 4) is None, "an export index outside the table is not a reference"
    assert resolve_ref(-9, imps, 4) is None, "an import index outside the table is not a reference"
    kinds = measure(body, 0, len(body), 5, imps, 4, lambda v: ("pkg" if v == 1 else ("Core" if v == 2 else "other")))
    assert kinds["names_kind"] == "pkg;Core;other", kinds
    ref = measure(body, 0, len(body), 5, imps, 4)
    assert ref["refs"] == 3, ref
    assert ref["neg_target"] == "Class.Object" and ref["neg_shift"] == "Class.Object", ref
    assert ref["fname_exact"] == 0, ref
    print("class-record-tail: synthetic tails pass (pairs, negative value, trail, width, FName reading, references)")
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
