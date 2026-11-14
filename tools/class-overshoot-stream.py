#!/usr/bin/env python3
"""Read the defaults stream from the located block start, on the rows whose array overshoots it.

`inventory/class_record_overshoot.csv` bounds the problem: on **95 of 549** located rows the record array
`inventory/class_record_array.csv` finds ends *past* the block start, and on **40** of those the locator's own
head census has no head that closes on the block start either -- so on those 40 nothing in the record stream
vouches for where the defaults buffer begins. The one witness left is the defaults stream itself: a property block
parses 1-4 bytes inside the region on the rows the tiling census measured, but a parse read *from the block
start* either terminates on the payload's last byte or it does not.

So this tool is a witness, not a locator: it re-reads each overshoot row from the dump and parses
`prop-stream.read_block` at `block_start`, reporting

* whether the parse terminates on a `None` name and whether it lands exactly on the payload's last byte
  (`trailer = payload - (block_start + end)`, 0 is the block start explaining the whole buffer);
* how many of the parsed tags name a property the class (or an ancestor) declares, against how many do not --
  a start inside the record array lands on indices that resolve to unrelated names;
* the same parse at `block_start + 1 .. + 4`, which is the base rate: a stream that closes from a start *and* from
  its neighbours is a property of the bytes rather than of the position, the failure the tiling census found;
* the split between the 40 rows with no head and the 55 that have one, and the join to the record side
  (`group_verdict`), so a row confirmed by both the block stream and the record is separable from one confirmed
  by neither.

    python3 tools/class-overshoot-stream.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-overshoot-stream.py --report
    python3 tools/class-overshoot-stream.py --check          # synthetic payloads, no dump, writes nothing

Reads the packages; no boot, no Ghidra round, no property value decoded.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ue2pkg import Package  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = "class_record_overshoot.csv"
ARTIFACT = "class_overshoot_stream.csv"
CORPUS_SOURCE = "class_record_bound.csv"     # every located block start in the corpus
CORPUS_ARTIFACT = "class_blockstart_stream.csv"
CORPUS_COLUMNS = ["package", "class", "payload", "block_start", "array_end", "overshoot", "heads", "declared",
                  "reason", "props", "end", "trailer", "known", "unknown", "ctl_closes", "ctl_max_known", "verdict"]
COLUMNS = ["package", "class", "payload", "block_start", "array_end", "overshoot", "heads", "group_verdict",
           "declared", "reason", "props", "end", "trailer", "known", "unknown", "ctl_closes", "ctl_known",
           "ctl_max_known", "verdict"]


def load_prop_stream():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prop-stream.py")
    spec = importlib.util.spec_from_file_location("prop_stream", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def declared_closure():
    """class -> the set of property names it and its ancestors declare, from the committed inventories."""
    declared, supers = collections.defaultdict(set), {}
    path = os.path.join(ROOT, "inventory", "class_properties.csv")
    if os.path.isfile(path):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                declared[r["class"]].add(r["property"])
    path = os.path.join(ROOT, "inventory", "classes.csv")
    if os.path.isfile(path):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                supers[r["class"]] = r["super"]
    out = {}
    for cls in declared:
        names, cur, seen = set(), cls, set()
        while cur and cur not in seen:
            seen.add(cur)
            names |= declared.get(cur, set())
            cur = supers.get(cur)
        out[cls] = names
    return out


def parse_at(data, pkg, mod, start):
    """`read_block` at one offset: (reason, props, end) or None when the reader raises."""
    if start < 0 or start >= len(data):
        return None
    try:
        props, end, reason = mod.read_block(data[start:], pkg, False)
    except Exception:
        return None
    return reason, props, end


def verdict(reason, props, end, payload, start):
    """What one parse says about the start it was read from."""
    if reason is None:
        return "unparsed"
    if reason != "None":
        return "overrun" if reason == "overrun" else "unterminated"
    if not props:
        return "empty"
    if start + end == payload:
        return "closes"
    if start + end > payload:
        return "ends-past"
    return "ends-short"


def measure(data, pkg, mod, start, declared):
    """One row: the parse at `start`, its trailer, its declared names, and the four later-start controls."""
    out = {"reason": "", "props": "", "end": "", "trailer": "", "known": "", "unknown": "", "ctl_closes": "",
           "ctl_known": "", "ctl_max_known": "", "declared": len(declared), "verdict": "unparsed"}
    got = parse_at(data, pkg, mod, start)
    if got is None:
        return out
    reason, props, end = got
    out.update({"reason": reason, "props": len(props), "end": end, "trailer": len(data) - (start + end),
                "known": sum(1 for p in props if p["name"] in declared),
                "unknown": sum(1 for p in props if p["name"] not in declared),
                "verdict": verdict(reason, props, end, len(data), start)})
    closes = known = best = 0
    for k in range(1, 5):
        ctl = parse_at(data, pkg, mod, start + k)
        if ctl is None:
            continue
        creason, cprops, cend = ctl
        if creason == "None" and start + k + cend == len(data) and cprops:
            closes += 1
            cknown = sum(1 for p in cprops if p["name"] in declared)
            known += cknown
            best = max(best, cknown)
    out["ctl_closes"] = closes
    out["ctl_known"] = known
    out["ctl_max_known"] = best if closes else ""
    return out


def sweep(args):
    declared = declared_closure()
    mod = load_prop_stream()
    source = list(csv.DictReader(open(os.path.join(ROOT, "inventory", SOURCE), newline="", encoding="utf-8")))
    by_pkg = collections.defaultdict(list)
    for r in source:
        by_pkg[r["package"]].append(r)
    rows, missing = [], 0
    for name, rs in sorted(by_pkg.items()):
        path = os.path.join(args.dump, "System", name + ".u")
        if not os.path.isfile(path):
            missing += len(rs)
            continue
        pkg = Package(path)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        by_class = {}
        for e in pkg.exports:
            if pkg.class_name_of(e) == "Class":
                by_class.setdefault(pkg.name(e["name"]), e)
        for r in rs:
            exp = by_class.get(r["class"])
            if exp is None:
                missing += 1
                continue
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            start = int(r["block_start"])
            if start <= 0 or start >= len(data):
                missing += 1
                continue
            row = {"package": name, "class": r["class"], "payload": len(data), "block_start": start,
                   "array_end": r.get("array_end", ""), "overshoot": r.get("overshoot", ""),
                   "heads": r.get("heads", ""), "group_verdict": r.get("group_verdict", ""),
                   "declared": len(declared.get(r["class"], ()))}
            row.update(measure(data, pkg, mod, start, declared.get(r["class"], set())))
            row["payload_matches"] = int(str(len(data)) == str(r.get("payload", "")))
            rows.append(row)
    rows.sort(key=lambda r: (r["verdict"], r["package"], r["class"]))
    if args.out:
        outdir = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
        os.makedirs(outdir, exist_ok=True)
        path = os.path.join(outdir, ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            for row in rows:
                w.writerow({c: row.get(c, "") for c in COLUMNS})
        print("wrote %s: %d rows" % (path, len(rows)))
    if missing:
        print("  %d row(s) without an export in the dump" % missing)
    return rows


def sweep_corpus(args):
    """The same parse over every located block start, so the 95 overshoot rows can be read against the corpus."""
    declared = declared_closure()
    mod = load_prop_stream()
    ends = {}
    for r in csv.DictReader(open(os.path.join(ROOT, "inventory", "class_record_array.csv"), newline="",
                                 encoding="utf-8")):
        ends[(r["package"], r["class"])] = r.get("array_end", "")
    source = [r for r in csv.DictReader(open(os.path.join(ROOT, "inventory", CORPUS_SOURCE), newline="",
                                            encoding="utf-8")) if r.get("block_start")]
    by_pkg = collections.defaultdict(list)
    for r in source:
        by_pkg[r["package"]].append(r)
    rows, missing = [], 0
    for name, rs in sorted(by_pkg.items()):
        path = os.path.join(args.dump, "System", name + ".u")
        if not os.path.isfile(path):
            missing += len(rs)
            continue
        pkg = Package(path)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        by_class = {}
        for e in pkg.exports:
            if pkg.class_name_of(e) == "Class":
                by_class.setdefault(pkg.name(e["name"]), e)
        for r in rs:
            exp = by_class.get(r["class"])
            if exp is None:
                missing += 1
                continue
            data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            start = int(r["block_start"])
            if start <= 0 or start >= len(data):
                missing += 1
                continue
            array_end = ends.get((name, r["class"]), "")
            row = {"package": name, "class": r["class"], "payload": len(data), "block_start": start,
                   "array_end": array_end,
                   "overshoot": (int(array_end) - start) if array_end not in ("", None) else "",
                   "heads": r.get("heads", ""), "declared": len(declared.get(r["class"], ()))}
            row.update(measure(data, pkg, mod, start, declared.get(r["class"], set())))
            row["payload_matches"] = int(str(len(data)) == str(r.get("payload", "")))
            rows.append(row)
    rows.sort(key=lambda r: (r["verdict"], r["package"], r["class"]))
    if args.out:
        outdir = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
        os.makedirs(outdir, exist_ok=True)
        path = os.path.join(outdir, CORPUS_ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=CORPUS_COLUMNS)
            w.writeheader()
            for row in rows:
                w.writerow({c: row.get(c, "") for c in CORPUS_COLUMNS})
        print("wrote %s: %d rows" % (path, len(rows)))
    if missing:
        print("  %d row(s) without an export in the dump" % missing)
    return rows


def report_corpus(rows=None):
    if rows is None:
        path = os.path.join(ROOT, "inventory", CORPUS_ARTIFACT)
        if not os.path.isfile(path):
            print("FAIL class-overshoot-stream: no corpus rows -- run --corpus -o inventory first")
            return 1
        rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    if not rows:
        print("FAIL class-overshoot-stream: the corpus artifact is empty")
        return 1
    closes = [r for r in rows if r["verdict"] == "closes"]
    print("%d located block starts re-read from the dump: the defaults stream terminates on the payload's last "
          "byte on %d (%.1f%%)" % (len(rows), len(closes), 100.0 * len(closes) / len(rows)))
    print("  verdicts: %s" % ", ".join("%s:%d" % kv for kv in
                                       collections.Counter(r["verdict"] for r in rows).most_common()))
    short = [r for r in rows if r["verdict"] in ("ends-short", "ends-past")]
    if short:
        buckets = collections.Counter()
        for r in short:
            t = int(r["trailer"])
            buckets["1-8" if 1 <= t <= 8 else "9-16" if t <= 16 else "17-32" if t <= 32 else "33-64" if t <= 64
                    else ">64" if t > 64 else "<0"] += 1
        print("  where it stops short (%d rows), the trailer in bytes: %s; mode %s"
              % (len(short), ", ".join("%s:%d" % kv for kv in buckets.most_common()),
                 collections.Counter(int(r["trailer"]) for r in short if 0 < int(r["trailer"]) <= 64).most_common(4)))
    past = [r for r in rows if r.get("overshoot") not in ("", None)]
    for label, group in (("the array ends at or before the block", [r for r in past if int(r["overshoot"]) <= 0]),
                         ("the array ends past the block", [r for r in past if int(r["overshoot"]) > 0])):
        got = [r for r in group if r["verdict"] == "closes"]
        print("  %s (%d rows): the stream closes on %d (%.1f%%)"
              % (label, len(group), len(got), 100.0 * len(got) / max(1, len(group))))
    later = sum(int(r["ctl_closes"]) for r in rows if r["ctl_closes"] != "")
    print("  control, the same parse at block_start + 1..4: %d of the %d later starts close (%.1f%%)"
          % (later, 4 * len(rows), 100.0 * later / max(1, 4 * len(rows))))
    informative = [r for r in rows if int(r["declared"] or 0) > 0 and r["ctl_max_known"] != ""]
    if informative:
        print("  of the %d rows whose class declares properties and whose neighbour closes, the block start names "
              "more declared tags on %d, the same on %d, fewer on %d"
              % (len(informative),
                 sum(1 for r in informative if int(r["known"]) > int(r["ctl_max_known"])),
                 sum(1 for r in informative if int(r["known"]) == int(r["ctl_max_known"])),
                 sum(1 for r in informative if int(r["known"]) < int(r["ctl_max_known"]))))
    known = [r for r in closes if int(r["known"]) > 0]
    print("  the closing parses name a declared property on %d of %d; %d name none at all"
          % (len(known), len(closes), sum(1 for r in closes if int(r["known"]) == 0)))
    return 0


def report(rows=None):
    if rows is None:
        rows = read_artifact()
    if not rows:
        print("FAIL class-overshoot-stream: no rows -- run --sweep -o inventory first")
        return 1
    headless = [r for r in rows if not r.get("heads") or int(r["heads"]) == 0]
    withhead = [r for r in rows if r.get("heads") and int(r["heads"]) > 0]
    print("%d overshoot rows re-read from the dump; %d have no head that closes on the block start, %d have one"
          % (len(rows), len(headless), len(withhead)))
    for label, group in (("no head", headless), ("with a head", withhead)):
        closes = [r for r in group if r["verdict"] == "closes"]
        print("  %s (%d rows): the stream read from the block start closes on the payload end on %d (%.1f%%)"
              % (label, len(group), len(closes), 100.0 * len(closes) / max(1, len(group))))
        print("    verdicts: %s" % ", ".join("%s:%d" % kv for kv in
                                             collections.Counter(r["verdict"] for r in group).most_common()))
        if closes:
            print("    on those, the parse carries %s tags and names a declared property %d times against %d not"
                  % (", ".join("%s:%d" % kv for kv in collections.Counter(r["props"] for r in closes).most_common(4)),
                     sum(int(r["known"]) for r in closes), sum(int(r["unknown"]) for r in closes)))
    short = [r for r in rows if r["verdict"] in ("ends-short", "ends-past")]
    if short:
        print("  where it does not land: %s ends short of the payload, leaving the parse's trailer in bytes %s"
              % (len(short), ", ".join("%s.%s:%s" % (r["package"], r["class"], r["trailer"]) for r in short)))
    ctl = collections.Counter(int(r["ctl_closes"]) if r["ctl_closes"] != "" else -1 for r in rows)
    print("  control, the same parse at block_start + 1..4: how many of the four also close: %s"
          % ", ".join("%s:%d" % kv for kv in sorted(ctl.items())))
    later = sum(int(r["ctl_closes"]) for r in rows if r["ctl_closes"] != "")
    print("    base rate over the 380 later starts: %d close (%.1f%%)"
          % (later, 100.0 * later / max(1, 4 * len(rows))))
    strict = [r for r in rows if r["verdict"] == "closes" and int(r["ctl_closes"] or 0) == 0]
    better = [r for r in rows if r["verdict"] == "closes" and r["ctl_max_known"] != ""
              and int(r["known"]) > int(r["ctl_max_known"])]
    level = [r for r in rows if r["verdict"] == "closes" and r["ctl_max_known"] != ""
             and int(r["known"]) <= int(r["ctl_max_known"])]
    print("    rows where it closes from the block start and from none of the four neighbours: %d of %d"
          % (len(strict), len(rows)))
    informative = [r for r in rows if int(r["declared"] or 0) > 0 and r["ctl_max_known"] != ""]
    print("    rows where a neighbour also closes: the block start names more declared properties on %d, "
          "no more on %d" % (len(better), len(level)))
    print("      restricted to the %d rows whose class declares properties: %d name more, %d the same, behind %d"
          % (len(informative),
             sum(1 for r in informative if int(r["known"]) > int(r["ctl_max_known"])),
             sum(1 for r in informative if int(r["known"]) == int(r["ctl_max_known"])),
             sum(1 for r in informative if int(r["known"]) < int(r["ctl_max_known"]))))
    blind = [r for r in rows if int(r["declared"] or 0) == 0]
    if blind:
        print("      %d rows name no declared property at all, so the name test is silent there: %s"
              % (len(blind), ", ".join("%s.%s" % (r["package"], r["class"]) for r in blind[:4]) + " ..."))
    grouped = [r for r in rows if r.get("group_verdict")]
    record = [r for r in grouped if r.get("group_verdict") == "names-ref-name-list"]
    both = [r for r in record if r["verdict"] == "closes"]
    print("  the record side parses on %d of %d rows; the block stream closes on %d of those"
          % (len(record), len(grouped), len(both)))
    neither = [r for r in grouped if r.get("group_verdict") != "names-ref-name-list"
               and r["verdict"] != "closes"]
    print("    rows confirmed by neither the record nor the block stream: %d (%s)"
          % (len(neither), ", ".join("%s.%s" % (r["package"], r["class"]) for r in neither[:6]) or "none"))
    bad = [r for r in rows if r.get("payload_matches") is not None and str(r.get("payload_matches")) == "0"]
    if bad:
        print("  %d row(s) whose payload size disagrees with the locator artifact: %s"
              % (len(bad), ", ".join("%s.%s" % (r["package"], r["class"]) for r in bad[:5])))
    return 0


def read_artifact():
    path = os.path.join(ROOT, "inventory", ARTIFACT)
    if not os.path.isfile(path):
        return []
    return list(csv.DictReader(open(path, newline="", encoding="utf-8")))


class FakePkg(object):
    """A name table of three declared names and one that is not, for the synthetic parse."""

    NAMES = ["None", "Health", "Ammo", "Other"]

    def name(self, i):
        return self.NAMES[i] if 0 <= i < len(self.NAMES) else "?"


def selftest():
    """A block that closes from its own start only: one close, one short end, one overrun, one neighbour."""
    bad = 0
    pkg, declared = FakePkg(), {"Health", "Ammo"}

    class Mod(object):
        @staticmethod
        def read_block(buf, p, want_values=False):
            # tag stream: [0x01 name][0x02 4-byte value] ... terminating name index 0
            off, props = 0, []
            while off < len(buf):
                name = buf[off]
                off += 1
                if name == 0:
                    return props, off, "None"
                if name >= len(FakePkg.NAMES):
                    return props, off, "overrun"
                if off + 2 > len(buf):
                    return props, off, "overrun"
                props.append({"name": FakePkg.NAMES[name], "type": "Float"})
                off += 2
            return props, off, "limit"

    mod = Mod()
    # a body with a terminator at offset 5: a parse from 2 ends at 6 == payload
    closes = bytes([9, 9, 1, 7, 7, 0])
    got = measure(closes, pkg, mod, 2, declared)
    if got["verdict"] != "closes" or got["known"] != 1 or got["trailer"] != 0 or got["ctl_closes"] != 0:
        print("FAIL class-overshoot-stream: a closing parse answered %s" % (got,))
        bad += 1
    # the same stream with two bytes after its terminator: the parse ends short of the payload
    short = bytes([9, 9, 1, 7, 7, 0, 5, 5])
    got = measure(short, pkg, mod, 2, declared)
    if got["verdict"] != "ends-short" or got["trailer"] != 2:
        print("FAIL class-overshoot-stream: a terminator two bytes early answered %s" % (got,))
        bad += 1
    # a start whose tag has no room for its value runs off the end without a terminator
    got = measure(bytes([9, 1, 7]), pkg, mod, 1, declared)
    if got["verdict"] != "overrun":
        print("FAIL class-overshoot-stream: an unterminated parse answered %s" % got["verdict"])
        bad += 1
    # an out-of-range start is refused rather than raising
    if measure(closes, pkg, mod, 99, declared)["verdict"] != "unparsed":
        print("FAIL class-overshoot-stream: an out-of-range start was not refused")
        bad += 1
    # verdict() names the four shapes directly
    for want, args in (("closes", ("None", [1], 3, 5, 2)),
                       ("ends-short", ("None", [1], 2, 5, 2)),
                       ("ends-past", ("None", [1], 4, 5, 2)),
                       ("unterminated", ("limit", [1], 5, 5, 0)),
                       ("empty", ("None", [], 0, 5, 5))):
        if verdict(*args) != want:
            print("FAIL class-overshoot-stream: verdict answered %s, expected %s" % (verdict(*args), want))
            bad += 1
    if bad:
        return 1
    print("ok   class-overshoot-stream: a closing parse, a short end, an overrun, an out-of-range start "
          "and five verdict shapes")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", default=os.environ.get("SG1_DUMP", ""))
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--corpus", action="store_true")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--report-corpus", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return selftest()
    if args.sweep:
        if not args.dump:
            print("FAIL class-overshoot-stream: --sweep needs --dump")
            return 1
        return report(sweep(args))
    if args.report:
        return report()
    if args.corpus:
        if not args.dump:
            print("FAIL class-overshoot-stream: --corpus needs --dump")
            return 1
        return report_corpus(sweep_corpus(args))
    if args.report_corpus:
        return report_corpus()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
