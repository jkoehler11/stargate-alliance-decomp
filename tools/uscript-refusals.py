#!/usr/bin/env python3
"""Measure the scripts the bytecode walker still refuses, one payload at a time (PLAN 3.7, docs/UNKNOWNS.md U13).

`tools/uscript-dis.py --usage` walks the corpus and prints a census of the opcodes the scripts that *do* walk use;
the 85 payloads that refuse are only a sentence in `docs/BYTECODE.md`. This tool is the other half: it holds every
payload with an epilogue against the table, and for each refusal records **where** the walk stopped, **which opcode**
it stopped on, and **why** -- the four things a fix needs and a count cannot give.

It publishes counts and offsets only. No payload bytes are written, per `AGENTS.md`.

    tools/uscript-refusals.py --dir "$SG1_DUMP/.../System" -o inventory
    tools/uscript-refusals.py --summary "$SG1_DUMP/.../System"      # the per-opcode and per-reason tables
    tools/uscript-refusals.py --candidates "$SG1_DUMP/.../System"   # which single table row would recover most
    tools/uscript-refusals.py --check                               # dump-free, validates the committed CSV
"""
import argparse
import collections
import csv
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CSV = "opcode_refusals.csv"
COLUMNS = ["package", "class", "function", "payload_bytes", "script_bytes", "offset", "rel_offset",
           "opcode", "opcode_name", "category", "reason"]
EPILOGUE = bytes([0x04, 0x0B, 0x47])
OFFSET = re.compile(r"^(0x[0-9a-f]+): (.*)$")
CATEGORIES = {"bad-token": "bad-token", "ran off the end": "ran-off-end",
              "parameter list": "parameter-list", "string": "string", "label table": "label-table",
              "UTF-16": "string", "index of": "index", "wants": "operand-bytes",
              "nesting deeper": "nesting", "is not in the table": "unknown-opcode",
              "unknown pattern": "unknown-pattern", "case operand": "case"}


def _dis():
    spec = importlib.util.spec_from_file_location("uscript_dis", os.path.join(HERE, "uscript-dis.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def category(reason):
    """Which refusal case this is -- the message's own words, folded to a fixed vocabulary."""
    for needle, name in CATEGORIES.items():
        if needle in reason:
            return name
    return "other"


def scripts(path):
    """Every Function payload whose script starts where `--usage` says it does and ends at its epilogue."""
    dis = _dis()
    from ue2pkg import Package
    files = ([os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith(".u")]
             if os.path.isdir(path) else [path])
    for one in files:
        pkg = Package(one)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        for e in pkg.exports:
            if pkg.class_name_of(e) != "Function":
                continue
            pay = pkg.data[e["offset"]:e["offset"] + e["size"]]
            n = len(pay)
            off, last, ok = 0, None, True
            for _ in range(6):
                try:
                    last, off = dis.read_index(pay, off)
                except IndexError:
                    ok = False
                    break
            if not ok or last != e["name"]:
                continue
            if n >= 10 and pay[n - 10:n - 7] == EPILOGUE:
                end = n - 7
            elif n >= 12 and pay[n - 12:n - 9] == EPILOGUE:
                end = n - 9
            else:
                continue
            start = off + 16
            if start >= end:
                continue
            yield os.path.basename(one), pkg, e, pay, start, end


def measure(path):
    dis = _dis()
    table = dis.Table()
    rows, walked = [], 0
    for package, pkg, e, pay, start, end in scripts(path):
        data = pay[start:end]
        try:
            nodes = dis.parse(data, table)
        except dis.Refused as exc:
            m = OFFSET.match(str(exc))
            if m is None:
                raise ValueError("a refusal with no offset at its front: %r" % str(exc))
            rel = int(m.group(1), 16)
            reason = m.group(2)
            op = data[rel] if 0 <= rel < len(data) else None
            rows.append({
                "package": package,
                "class": _owner(pkg, e),
                "function": pkg.name(e["name"]),
                "payload_bytes": len(pay),
                "script_bytes": len(data),
                "offset": rel + start,
                "rel_offset": rel,
                "opcode": "" if op is None else "0x%02x" % op,
                "opcode_name": "" if op is None else (table.rows[op]["name"] if op in table.rows else "?"),
                "category": category(reason),
                "reason": reason,
            })
            continue
        if sum(x["end"] - x["offset"] for x in nodes) != len(data):
            rows.append({
                "package": package,
                "class": _owner(pkg, e),
                "function": pkg.name(e["name"]),
                "payload_bytes": len(pay),
                "script_bytes": len(data),
                "offset": start,
                "rel_offset": 0,
                "opcode": "",
                "opcode_name": "",
                "category": "short-walk",
                "reason": "the token tree covers %d of %d bytes" % (
                    sum(x["end"] - x["offset"] for x in nodes), len(data)),
            })
            continue
        walked += 1
    return rows, walked


def _owner(pkg, e):
    """The class a function belongs to: its export's `Outer`, named -- the row has to say whose function it is."""
    outer = e.get("outer")
    if not outer or outer < 1 or outer > len(pkg.exports):
        return ""
    return pkg.name(pkg.exports[outer - 1]["name"])


def collect(path, out):
    rows, walked = measure(path)
    rows.sort(key=lambda r: (r["package"], r["class"], r["function"], r["rel_offset"]))
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, CSV), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return rows, walked


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def selftest():
    """The message parser and the categoriser, on messages the walker actually raises."""
    bad = []
    cases = [("0x1f: opcode 0x52 is the walker's bad-token case", 0x1f, "bad-token"),
             ("0x0: parameter list of 02 never reaches 0x16", 0, "parameter-list"),
             ("0x2a: opcode 03 wants 4 operand bytes, 2 left", 0x2a, "operand-bytes"),
             ("0x7f: ran off the end", 0x7f, "ran-off-end")]
    for message, want_off, want_cat in cases:
        m = OFFSET.match(message)
        if m is None or int(m.group(1), 16) != want_off:
            bad.append("offset of %r" % message)
        elif category(m.group(2)) != want_cat:
            bad.append("category of %r is %r, wanted %r" % (message, category(m.group(2)), want_cat))
    if OFFSET.match("no offset here") is not None:
        bad.append("a message without an offset parsed as one")
    return bad


def check():
    """Dump-free: the committed CSV keeps the invariants the census claims."""
    path = os.path.join(ROOT, "inventory", CSV)
    if not os.path.exists(path):
        print("FAIL opcode-refusals: %s is missing" % path)
        return 1
    rows = read_csv(path)
    bad = selftest()
    if not rows:
        print("FAIL opcode-refusals: %s is empty" % path)
        return 1
    table = {}
    tpath = os.path.join(ROOT, "inventory", "opcode_table.csv")
    if os.path.exists(tpath):
        for r in csv.DictReader(open(tpath, encoding="utf-8")):
            table[int(r["opcode"], 16)] = r["name"]
    for r in rows:
        payload, script = int(r["payload_bytes"]), int(r["script_bytes"])
        rel, off = int(r["rel_offset"]), int(r["offset"])
        if not 0 < script <= payload:
            bad.append("%s %s: script %d of payload %d" % (r["package"], r["function"], script, payload))
        if not 0 <= rel <= script:
            bad.append("%s %s: offset %d past a %d-byte script" % (r["package"], r["function"], rel, script))
        if rel != off - (off - rel) or off < rel:
            bad.append("%s %s: the absolute offset precedes the relative one" % (r["package"], r["function"]))
        if r["opcode"]:
            op = int(r["opcode"], 16)
            if op not in table:
                bad.append("%s %s: opcode %s is not in the table" % (r["package"], r["function"], r["opcode"]))
            elif table[op] != r["opcode_name"]:
                bad.append("%s %s: opcode %s is %r in the table, %r in the row"
                           % (r["package"], r["function"], r["opcode"], table[op], r["opcode_name"]))
        if r["category"] not in set(CATEGORIES.values()) | {"short-walk"}:
            bad.append("%s %s: category %r" % (r["package"], r["function"], r["category"]))
    names = collections.Counter((r["package"], r["class"], r["function"]) for r in rows)
    dups = [k for k, n in names.items() if n > 1]
    if dups:
        bad.append("%d function(s) refused twice in one package: %r" % (len(dups), dups[:3]))
    if bad:
        for b in bad[:20]:
            print("FAIL opcode-refusals: " + b)
        return 1
    cats = collections.Counter(r["category"] for r in rows)
    print("ok   opcode-refusals: %d refusals over %d packages, %s"
          % (len(rows), len({r["package"] for r in rows}),
             ", ".join("%s %d" % kv for kv in cats.most_common(4))))
    return 0


def tail_cut(path, cuts=(3, 7, 11)):
    """Does removing the script's last bytes rescue a refusal? The tail hypothesis, measured instead of assumed."""
    dis = _dis()
    table = dis.Table()
    tally = collections.Counter()
    refused = 0
    for _package, _pkg, _e, pay, start, end in scripts(path):
        data = pay[start:end]
        try:
            dis.parse(data, table)
            continue
        except dis.Refused:
            refused += 1
        got = []
        for cut in cuts:
            try:
                nodes = dis.parse(data[:len(data) - cut], table)
                got.append(sum(x["end"] - x["offset"] for x in nodes) == len(data) - cut)
            except dis.Refused:
                got.append(False)
        tally[tuple(got)] += 1
    print("%d refusals; rescued by cutting the last %s bytes: %s"
          % (refused, "/".join(str(c) for c in cuts), [tally[tuple(False if i != k else True
                                                                  for i in range(len(cuts)))]
                                                      for k in range(len(cuts))]))
    print("  pattern (each cut): %s" % (tally.most_common(),))
    return 0


CANDIDATES = ("", "bytes:1", "bytes:2", "bytes:3", "bytes:4", "bytes:5", "bytes:6", "bytes:8", "index",
              "expr", "parms", "labeltable")


def _walks(dis, data, table):
    try:
        nodes = dis.parse(data, table)
    except dis.Refused:
        return False
    return sum(x["end"] - x["offset"] for x in nodes) == len(data)


def candidates(path):
    """Which single table row would recover the most refusals? Measured one variable at a time.

    The walk stops on one opcode, so a wrong width or pattern *there* is the cheapest hypothesis. The candidate
    replaces that row alone (every other row is the committed table) and every payload that stopped on it is walked
    again: a recovery is a payload that now reaches its epilogue. A stop opcode whose best candidate recovers
    nothing is a desync *upstream* of the token -- the stream was already off when it got there -- and no entry in
    this table can fix it, which is the answer that stops the per-opcode route.
    """
    dis = _dis()
    table = dis.Table()
    payloads = []
    for _package, _pkg, _e, pay, start, end in scripts(path):
        data = pay[start:end]
        try:
            dis.parse(data, table)
            continue
        except dis.Refused as exc:
            m = OFFSET.match(str(exc))
        if m is None:
            continue
        rel = int(m.group(1), 16)
        payloads.append((data[rel] if rel < len(data) else None, data))
    stopped = collections.Counter(op for op, _d in payloads if op is not None)
    print("%d refusal(s), %d distinct stop opcode(s)" % (len(payloads), len(stopped)))
    print("%-6s %-22s %-12s %6s %6s %-8s %s"
          % ("op", "name", "row", "stops", "walks", "verdict", "candidates that recover"))
    best = []
    for op, stops in stopped.most_common():
        here = [d for o, d in payloads if o == op]
        row = table.rows[op]
        got = []
        for cand in CANDIDATES:
            if cand == row["pattern"]:
                continue
            trial = dis.Table()
            trial.rows[op] = dict(trial.rows[op], pattern=cand)
            recovered = sum(1 for d in here if _walks(dis, d, trial))
            if recovered:
                got.append((recovered, cand))
        got.sort(key=lambda kv: (-kv[0], kv[1]))
        ties = [c for n, c in got if n == got[0][0]] if got else []
        # A width that makes a payload walk by skipping bytes is not a rule. The tell is that several unrelated
        # patterns -- a fixed width, an `index`, no operand at all -- recover exactly the same payloads, so the
        # stream was already off and any shift of it lands on the epilogue.
        verdict = "none" if not got else ("shift" if len(ties) > 1 else "unique")
        best.append((got[0][0] if got else 0, op, len(here), got, verdict))
        print("%-6s %-22s %-12s %6d %6d %-8s %s"
              % ("0x%02x" % op, row["name"], row["pattern"] or "(none)", stops,
                 got[0][0] if got else 0, verdict,
                 ", ".join("%s %d" % (c or "(none)", n) for n, c in got[:3]) or "-"))
    for verdict in ("shift", "unique", "none"):
        rows_ = [r for r in best if r[4] == verdict]
        print("  %-6s %2d stop opcode(s), %2d refusal(s)%s"
              % (verdict, len(rows_), sum(r[2] for r in rows_),
                 (" -- " + ", ".join("0x%02x %s recovers %d/%d" % (r[1], r[3][0][1] if r[3] else "-", r[0], r[2])
                                     for r in sorted(rows_, key=lambda r: -r[0])[:3])) if rows_ else ""))
    print("%d of %d refusals walk again under some single-row change; %d walk again under no single-row change"
          % (sum(r[0] for r in best), len(payloads), sum(r[2] for r in best if r[4] == "none")))
    return 0


def summary(path):
    rows, walked = measure(path)
    print("%d walk clean, %d refused" % (walked, len(rows)))
    print("%-6s %-24s %6s   %s" % ("op", "name", "count", "categories"))
    per = collections.defaultdict(list)
    for r in rows:
        per[(r["opcode"], r["opcode_name"])].append(r)
    for (op, name), rs in sorted(per.items(), key=lambda kv: -len(kv[1])):
        cats = collections.Counter(x["category"] for x in rs)
        print("%-6s %-24s %6d   %s" % (op or "-", name or "-", len(rs),
                                       ", ".join("%s %d" % kv for kv in cats.most_common())))
    print()
    for pkg, rs in sorted(collections.Counter(r["package"] for r in rows).items()):
        print("  %-16s %d" % (pkg, rs))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", nargs="?", help="a package file or a directory of them")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--summary", action="store_true", help="print the per-opcode tables instead of writing the CSV")
    ap.add_argument("--tail-cut", action="store_true",
                    help="test whether removing the script's last bytes rescues a refusal")
    ap.add_argument("--candidates", action="store_true",
                    help="which single table row would recover the most refusals (one variable at a time)")
    ap.add_argument("--check", action="store_true", help="dump-free: validate the committed CSV")
    args = ap.parse_args()
    if args.check:
        return check()
    if not args.path:
        ap.error("give a package or a directory, or --check")
    if args.tail_cut:
        return tail_cut(args.path)
    if args.candidates:
        return candidates(args.path)
    if args.summary:
        return summary(args.path)
    rows, walked = collect(args.path, args.out)
    print("%d scripts walk clean, %d refused; wrote %s" % (walked, len(rows), os.path.join(args.out, CSV)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
