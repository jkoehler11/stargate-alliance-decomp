#!/usr/bin/env python3
"""Measure where a class export stops being a record: the two anchors in the class record's prefix.

A class export's payload is a **class record followed by the tagged default-property stream** (`docs/NATIVE_PROPERTIES.md`
§6), and the defaults locator (`tools/class-defaults.py`) only ever searched for the stream -- so a class whose
payload holds no stream at all is reported there as `empty-block`, which is a reason and not a measurement. This
tool measures the split directly, with two anchors that come from the package's own export table rather than from a
guessed field order:

* **`Super` is the first field, as a compact index.** Every export row carries its own `super`, and the value the
  payload opens with must be the same number (`-n` = import `n`, positive = export). That is checked per class, so
  the claim is falsifiable on all 2,319 of them instead of on a sample.
* **`ScriptText` follows it**, as a compact index of the `TextBuffer` export that is outered to the class itself: an
  Xbox or PC cook strips the field chain (`Children` is `0` -- `docs/NATIVE_PROPERTIES.md` §9 measured the runtime
  rebuilding the chain from `ObjObjects`) but keeps the script *text*, so the buffer is one export away.

The record's end is then measured, not derived: for a class whose defaults block the locator did find, the block's
start **is** the record's length, and the distribution of that length is the window a payload has to fall in before
"there is no stream here" becomes an honest reading rather than a parse failure. The one obvious way to finish the
109 -- read their trailing byte as a terminator and call the record `payload-1` -- is measured here and **rejected**:
every one of the 2,319 class payloads ends in `00`, and a one-byte empty tag stream (a name index of 0) parses at
`payload-1` on 2,319 of 2,319, including the 2,209 whose record end a located block already fixes. The pair is
saturated, so it locates no end and those payloads stay bounded by the window. Every row carries both, so the next
session can see the control instead of re-deriving the guess.

    python3 tools/class-record.py DUMP/System --class Engine.Sound
    python3 tools/class-record.py DUMP/System --sweep -o inventory

Nothing here reads a defaults value: names, indices and byte counts only, and the only bytes printed are the
payload's own four-byte tail, which is structure, not content.
"""
import argparse
import collections
import csv
import importlib.util
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_prop_stream = None

# The record length is not one number: 2,189 located blocks start anywhere from 8 to 13,665 bytes after the prefix,
# with a median of 96 and a p90 of 401 (a class record carries the class's function table, so a function-rich class
# has a long one). This bound sits between the p5 (87) and the median, so a payload at or below it is short enough
# for a record *alone* to account for every byte -- which makes "no stream" a reading rather than a parse failure.
# It is a window, not a rule: a payload inside it is `record-only` only when both anchors agree and no block was
# located, and a payload above it is `long-no-block` -- bytes the record does not explain, which is still an open miss.
RECORD_WINDOW = 100


def read_index(buf, off):
    """FCompactIndex: sign in bit 7 of the first byte, 6 value bits, then 7 bits per continuation byte."""
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


def find_index(buf, want, limit=12):
    """Offsets in the first `limit` bytes where a compact index decodes to `want`."""
    out = []
    for off in range(0, min(len(buf), limit)):
        try:
            value, end = read_index(buf, off)
        except IndexError:
            break
        if value == want:
            out.append((off, end))
    return out


def textbuffer_of(pkg):
    """class export index -> the index of the `TextBuffer` export outered to it (the class's script text)."""
    out = {}
    for k, exp in enumerate(pkg.exports, start=1):
        if pkg.class_name_of(exp) == "TextBuffer" and exp["outer"] > 0 and exp["size"] > 0:
            out.setdefault(exp["outer"], k)
    return out


def function_counts(pkg):
    """class export index -> how many `Function` exports it owns (the table the record is mostly made of)."""
    out = collections.Counter()
    for exp in pkg.exports:
        if pkg.class_name_of(exp) == "Function" and exp["outer"] > 0:
            outer = pkg.exports[exp["outer"] - 1]
            if pkg.class_name_of(outer) == "Class":
                out[exp["outer"]] += 1
    return out


def measure(pkg, exp, class_index, buffers, block_start=None, functions=0):
    """One class export -> the record's measured prefix plus the payload's tail."""
    data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    row = {"super_index": "", "super_matches": "", "scripttext_index": "", "scripttext_at": "",
           "record_bytes": "", "tail": ""}
    if not data:
        return row
    row["super_index"], end = read_index(data, 0)
    row["super_matches"] = 1 if row["super_index"] == exp["super"] else 0
    buf = buffers.get(class_index)
    if buf is not None:
        row["scripttext_index"] = buf
        # The buffer is referenced *after* Super and the (zero) Children byte, so search from just past Super rather
        # than from 0 -- otherwise the Super of a class whose super is the same number as its buffer, which cannot
        # happen for an index above the import count, would still be a safe miss.
        hits = [o for o, _e in find_index(data[end:], buf, limit=8)]
        row["scripttext_at"] = end + hits[0] if hits else ""
    if block_start is not None:
        row["record_bytes"] = block_start
    row["functions"] = functions
    row["tail"] = data[-4:].hex() if len(data) >= 4 else data.hex()
    row["last_byte"] = data[-1:].hex()
    # Does one byte at the payload's end parse as a complete, empty tag stream? A single zero index byte does, so the
    # answer is 1 wherever the payload ends in 00 -- which is every class payload, so the column is a control and not
    # an instrument: keep it beside `block_start`, where the true end is known, to show that.
    try:
        props, _end, reason = load_prop_stream().read_block(data[-1:], pkg, False)
        row["empty_at_end"] = 1 if (reason == "None" and not props) else 0
    except Exception:
        row["empty_at_end"] = 0
    row["payload"] = len(data)
    return row


def tail_class(payload, has_block):
    """`located`, `record-only` or `long-no-block`: the payload's own shape, before any defaults reason."""
    if has_block:
        return "located"
    return "record-only" if payload <= RECORD_WINDOW else "long-no-block"


def base_rows(outdir):
    """package/class -> (block_start, defaults) from the committed defaults artifact; empty when absent."""
    path = os.path.join(ROOT, "inventory", "class_defaults_per_class.csv")
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[(r["package"], r["class"])] = (r["block_start"], r["defaults"])
    return out


def walk(system_dir):
    """Yield (package, class, export index, Package, export) for every class export in the directory."""
    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        yield fname[:-2], pkg


def sweep(system_dir, outdir):
    load_prop_stream()
    base = base_rows(outdir)
    rows, reasons = [], collections.Counter()
    for pkg_name, pkg in walk(system_dir):
        buffers = textbuffer_of(pkg)
        functions = function_counts(pkg)
        for k, exp in enumerate(pkg.exports, start=1):
            if pkg.class_name_of(exp) != "Class":
                continue
            cls = pkg.name(exp["name"])
            start, defaults = base.get((pkg_name, cls), ("", ""))
            m = measure(pkg, exp, k, buffers, int(start) if start else None, functions.get(k, 0))
            has_block = bool(start) and defaults not in ("", "0")
            shape = tail_class(m["payload"], has_block)
            reasons[shape] += 1
            rows.append([pkg_name, cls, m["payload"], m["super_index"], m["super_matches"],
                         m["scripttext_index"], m["scripttext_at"], m["record_bytes"], shape, start, m["tail"],
                         m["functions"], m["last_byte"], m["empty_at_end"]])
    path = os.path.join(outdir, "class_records.csv")
    os.makedirs(outdir, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["package", "class", "payload", "super_index", "super_matches", "scripttext_index",
                    "scripttext_at", "record_bytes", "shape", "block_start", "tail", "functions",
                    "last_byte", "empty_at_end"])
        w.writerows(rows)
    print("%d class exports: %s" % (len(rows), ", ".join("%s %d" % (k, v) for k, v in reasons.most_common())))
    print("wrote %s" % path)
    return rows


def read_reasons(outdir):
    """(package, class) -> reason from the committed misses artifact; empty when absent."""
    path = os.path.join(outdir, "class_defaults_misses.csv")
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[(r["package"], r["class"])] = r["reason"]
    return out


def at_share(values, share):
    """Nearest-rank value of a sorted list at `share`; 0 for an empty list."""
    return values[min(len(values) - 1, int(round(share * (len(values) - 1))))] if values else 0


def load_prop_stream():
    """`tools/prop-stream.py` is hyphenated, so it is imported by path; loaded once per process."""
    global _prop_stream
    if _prop_stream is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prop-stream.py")
        spec = importlib.util.spec_from_file_location("prop_stream", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _prop_stream = mod
    return _prop_stream


def report(rows, outdir):
    """The numbers the doc quotes, from the artifact rather than from a second walk."""
    shapes = collections.Counter(r[8] for r in rows)
    supers = sum(1 for r in rows if r[4] == "1")
    buffers = sum(1 for r in rows if r[6] != "")
    ats = collections.Counter(int(r[6]) for r in rows if r[6] != "")
    starts = [int(r[7]) for r in rows if r[7] != ""]
    # A candidate that starts at byte 0 or 1 begins inside the two anchors (Super is one or two bytes, then the zero
    # Children byte), so its "record length" is not one; count those and keep them out of the distribution.
    inside = [s for s in starts if s <= 3]
    lengths = sorted(s for s in starts if s > 3)
    short_records = sum(1 for s in lengths if s <= RECORD_WINDOW)
    modes = collections.Counter(lengths).most_common(1)
    print("classes                 %d" % len(rows))
    print("shape                   %s" % ", ".join("%s %d" % (k, v) for k, v in shapes.most_common()))
    print("super anchor agrees     %d of %d" % (supers, len(rows)))
    print("scripttext anchor found %d of %d (%s)"
          % (buffers, len(rows), ", ".join("at +%d %d" % (k, v) for k, v in sorted(ats.items()))))
    print("record lengths          %d blocks after the prefix: %d..%d, median %d, p90 %d"
          % (len(lengths), lengths[0] if lengths else 0, lengths[-1] if lengths else 0,
             at_share(lengths, 0.5), at_share(lengths, 0.9)))
    print("record mode             %d bytes, %d blocks (%d distinct lengths)"
          % (modes[0][0] if modes else 0, modes[0][1] if modes else 0, len(set(lengths))))
    print("short records           %d blocks start at or below %d bytes (%.0f%% of %d)"
          % (short_records, RECORD_WINDOW, 100.0 * short_records / max(len(lengths), 1), len(lengths)))
    print("inside the prefix       %d candidates start at byte 0 or 1, so their length is not a record" % len(inside))
    short = [r for r in rows if r[8] == "record-only"]
    sizes = [int(r[2]) for r in short]
    print("record-only payloads    %d, %d..%d bytes" % (len(short), min(sizes, default=0), max(sizes, default=0)))
    tails = collections.Counter(r[10] for r in short)
    print("record-only tails       %s" % ", ".join("%s %d" % (k, v) for k, v in tails.most_common(5)))
    long_ = [r for r in rows if r[8] == "long-no-block"]
    lsizes = sorted(int(r[2]) for r in long_)
    print("long-no-block payloads  %d, %d..%d bytes, median %d"
          % (len(long_), lsizes[0] if lsizes else 0, lsizes[-1] if lsizes else 0, at_share(lsizes, 0.5)))
    pairs = [(int(r[11]), int(r[7])) for r in rows if r[7] != "" and int(r[7]) > 3]
    if pairs:
        mx = sum(p[0] for p in pairs) / len(pairs)
        my = sum(p[1] for p in pairs) / len(pairs)
        cov = sum((a - mx) * (b - my) for a, b in pairs)
        sx = sum((a - mx) ** 2 for a, _b in pairs) ** 0.5
        sy = sum((b - my) ** 2 for _a, b in pairs) ** 0.5
        print("record vs functions     pearson r %.2f over %d blocks" % (cov / (sx * sy) if sx and sy else 0.0,
                                                                        len(pairs)))
        buckets = ((0, 0, "0"), (1, 5, "1-5"), (6, 20, "6-20"), (21, 60, "21-60"), (61, 10 ** 9, "61+"))
        out = []
        for lo, hi, label in buckets:
            sizes = sorted(b for a, b in pairs if lo <= a <= hi)
            if sizes:
                out.append("%s: median %d (%d)" % (label, at_share(sizes, 0.5), len(sizes)))
        print("record by function count %s" % ", ".join(out))
    reasons = read_reasons(outdir)
    if reasons:
        split = collections.Counter()
        for r in rows:
            reason = reasons.get((r[0], r[1]))
            if reason:
                split[(reason, r[8])] += 1
        print("misses by shape         %s"
              % ", ".join("%s/%s %d" % (a, b, v) for (a, b), v in sorted(split.items())))
    if len(rows[0]) > 13:
        zero = [r for r in rows if r[12] == "00"]
        at_end = [r for r in rows if r[13] == "1"]
        located = [r for r in rows if r[13] == "1" and r[8] == "located"]
        print("trailing byte           %d of %d payloads end in 00" % (len(zero), len(rows)))
        print("empty parse at end      %d of %d, of which %d have their record end fixed by a located block -- "
              "saturated, so it is not a record-end locator" % (len(at_end), len(rows), len(located)))


def selftest():
    """`--check`: the two decoders on synthetic bytes, no dump and no writes."""
    problems = []
    # FCompactIndex round trip: 0, 3, -3, 889, 17288 and the two-byte / three-byte forms.
    cases = {0: "00", 3: "03", -3: "83", 889: "790d", 17288: "488e02", 2066: "5220"}
    for value, hexs in cases.items():
        raw = bytes.fromhex(hexs)
        got, end = read_index(raw, 0)
        if got != value or end != len(raw):
            problems.append("compact %d decoded %d in %d bytes" % (value, got, end))
    body = bytes.fromhex("0348830200")
    hits = [o for o, _e in find_index(body, -3, limit=8)]
    if not hits:
        problems.append("find_index missed -3 in a synthetic record")
    if tail_class(92, False) != "record-only" or tail_class(331, False) != "long-no-block" \
            or tail_class(92, True) != "located":
        problems.append("tail_class mislabels a synthetic payload")
    # `--report` quotes `at_share`, and its first version counted distinct block starts instead of blocks.
    if at_share([8, 92, 92, 200], 0.5) != 92 or at_share([], 0.5) != 0 or at_share([7], 0.9) != 7:
        problems.append("at_share misranks a synthetic list")
    if problems:
        print("FAIL class-record: " + "; ".join(problems))
        return 1
    print("ok   class-record: compact indices, the anchor search and the payload shapes answer without a dump")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("system", nargs="?", help="the dump's System directory")
    ap.add_argument("--class", dest="klass", help="one class, as Package.Class")
    ap.add_argument("--sweep", action="store_true", help="every package -> inventory/class_records.csv")
    ap.add_argument("--report", action="store_true", help="print the totals for a swept artifact")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump")
    ap.add_argument("-o", "--out", default="inventory")
    args = ap.parse_args()

    if args.check:
        return selftest()
    if args.report:
        with open(os.path.join(args.out, "class_records.csv"), newline="") as fh:
            report([r for r in csv.reader(fh)][1:], args.out)
        return 0
    if not args.system:
        ap.error("a System directory, --report or --check is required")
    if args.sweep:
        sweep(args.system, args.out)
        return 0
    if not args.klass:
        ap.error("--class Package.Class, --sweep, --report or --check")
    want_pkg, _, want_cls = args.klass.partition(".")
    for pkg_name, pkg in walk(args.system):
        if pkg_name != want_pkg:
            continue
        buffers = textbuffer_of(pkg)
        functions = function_counts(pkg)
        for k, exp in enumerate(pkg.exports, start=1):
            if pkg.class_name_of(exp) != "Class" or pkg.name(exp["name"]) != want_cls:
                continue
            m = measure(pkg, exp, k, buffers, None, functions.get(k, 0))
            print("%s.%s payload %d" % (pkg_name, want_cls, m["payload"]))
            print("  super            %s (export table %s) %s"
                  % (m["super_index"], exp["super"], "agrees" if m["super_matches"] else "DISAGREES"))
            print("  script text      %s at +%s"
                  % (m["scripttext_index"] or "-", m["scripttext_at"] if m["scripttext_at"] != "" else "-"))
            print("  functions owned  %s" % m["functions"])
            print("  tail             %s" % m["tail"])
            print("  shape            %s" % tail_class(m["payload"], False))
            return 0
    print("FAIL class-record: %s not found" % args.klass)
    return 1


if __name__ == "__main__":
    sys.exit(main())
