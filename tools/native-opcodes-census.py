#!/usr/bin/env python3
"""The native-call census against the opcode census: two counts of the same tokens must agree.

`inventory/native_opcodes.csv` (`uscript-dis.py --native-calls`) says how many **sites** each native-call opcode
carries, and `inventory/opcode_usage.csv` (`--usage`) says how many **tokens** the walked corpus writes for that
opcode. They are the same population read by two passes of the same walker, so they must agree opcode by opcode --
and a census generated from one table version cannot agree with a usage file generated from another.

    python3 tools/native-opcodes-census.py            # verify the committed pair, print the totals
    python3 tools/native-opcodes-census.py --check    # synthetic pair: the rules below, no tree writes

What it asserts, per row:

* a **direct** row (`0x70..0xff`) carries its index in the opcode itself;
* an **extended** row (`0x60..0x6f`) carries it as two bytes: `(opcode - 0x60) * 256 + operand`;
* the row's `sites` equal `opcode_usage.csv`'s `tokens` for that opcode, and a used opcode has a row (one row per
  opcode in the direct band, one per operand in the extended band);
* the row's `off_chain_receiver` histogram plus its `off_chain_untyped` count is its `off_chain` count, and
  `off_chain_resolved` is at most the histogram -- the receiver read places sites *inside* the off-chain population,
  so a histogram that claims more sites than the row has, an untyped count that does not close the row, or a
  placement count above the histogram, is a column nothing measured.

Why it exists (measured 2026-11-15): the committed census was written 2026-11-11 and the table moved twice on
2026-11-14 (`Case` read its value twice; the `StructCmpEq` reference is a compact index), which changed how 87
payloads parse. Re-run against the same dump the census reads **49,100** sites where the file said **48,422**, calls
**121** direct opcodes where it said 120, and holds **two rows the current walk cannot produce** (`0x86`'s two sites,
and the two "recorded, not dropped" tokens `0x67 0x08` / `0x6e 0x43`, both phantom tokens of a desynchronised
parse) while missing **two real ones** (`0x8a`, `0xc4`). The re-run reconciles with `opcode_usage.csv` exactly --
44,811 direct + 4,289 extended = 49,100 = that file's band plus high-band tokens, on every opcode -- and the stale
file reconciled with nothing, which is what this gate would have said.
"""
import argparse
import csv
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CENSUS = os.path.join("inventory", "native_opcodes.csv")
USAGE = os.path.join("inventory", "opcode_usage.csv")
COLUMNS = ["opcode", "operand", "index", "sites", "off_chain", "off_chain_receiver",
           "off_chain_resolved", "off_chain_untyped"]

DIRECT = (0x70, 0x100)
EXTENDED = (0x60, 0x70)


def read(path, columns):
    if not os.path.exists(path):
        raise SystemExit("native-opcodes-census: %s is missing" % path)
    with open(path, newline="", encoding="utf-8") as fh:
        r = csv.DictReader(fh)
        missing = [c for c in columns if c not in (r.fieldnames or [])]
        if missing:
            raise SystemExit("native-opcodes-census: %s has no %s column" % (path, ", ".join(missing)))
        return list(r)


def tokens_of(path):
    """{opcode: tokens} for every opcode the corpus writes, from the usage census."""
    out = {}
    for r in read(path, ["opcode", "tokens"]):
        op = r["opcode"].strip()
        if op.startswith("0x") and r["tokens"].strip().isdigit():
            out[int(op, 16)] = int(r["tokens"])
    return out


def reconcile(rows, usage):
    """The failures as strings: index arithmetic, and the two censuses' per-opcode counts."""
    bad = []
    by_op = {}
    for r in rows:
        op = int(r["opcode"], 16)
        where = "row %s/%s" % (r["opcode"], r["operand"] or "-")
        if op in range(*DIRECT) and r["operand"]:
            bad.append("%s: a direct-band opcode carries no operand byte" % where)
        elif op in range(*EXTENDED) and not r["operand"]:
            bad.append("%s: an extended-band opcode needs its operand byte" % where)
        elif not (op in range(*DIRECT) or op in range(*EXTENDED)):
            bad.append("%s: opcode outside both native-call bands" % where)
        want = op if op in range(*DIRECT) else (op - EXTENDED[0]) * 256 + int(r["operand"], 16)
        if int(r["index"]) != want:
            bad.append("%s: index is %s, the arithmetic gives %d" % (where, r["index"], want))
        by_op[op] = by_op.get(op, 0) + int(r["sites"])
        off = int(r["off_chain"] or 0)
        recv = (r.get("off_chain_receiver") or "").strip()
        try:
            hist = sum(int(x.rsplit(":", 1)[1]) for x in recv.split(";") if x.strip())
        except (IndexError, ValueError):
            bad.append("%s: the receiver column %r is not name:count;..." % (where, recv))
            hist = None
        if hist is not None:
            untyped = int(r.get("off_chain_untyped") or 0)
            if hist + untyped != off:
                bad.append("%s: the receiver histogram counts %d site(s) and %d untyped, off_chain says %d"
                           % (where, hist, untyped, off))
            placed = int(r.get("off_chain_resolved") or 0)
            if placed > hist:
                bad.append("%s: %d receiver placement(s) inside a %d-site histogram" % (where, placed, hist))
            if not off and (recv or placed or untyped):
                bad.append("%s: a row with no off-chain site carries a receiver" % where)
    for op, sites in sorted(by_op.items()):
        if usage.get(op, 0) != sites:
            bad.append("0x%02x: the census counts %d site(s), opcode_usage.csv %d token(s)"
                       % (op, sites, usage.get(op, 0)))
    for op in sorted(usage):
        if op in range(*DIRECT) or op in range(*EXTENDED):
            if op not in by_op:
                bad.append("0x%02x: opcode_usage.csv counts %d token(s), the census has no row"
                           % (op, usage[op]))
    return bad


def totals(rows, usage):
    direct = sum(int(r["sites"]) for r in rows if not r["operand"])
    extended = sum(int(r["sites"]) for r in rows if r["operand"])
    band = sum(v for k, v in usage.items() if k in range(*DIRECT))
    high = sum(v for k, v in usage.items() if k in range(*EXTENDED))
    return {"rows": len(rows), "direct_rows": sum(1 for r in rows if not r["operand"]),
            "extended_rows": sum(1 for r in rows if r["operand"]), "direct": direct, "extended": extended,
            "band": band, "high": high, "total": direct + extended, "usage_total": band + high}


def check():
    """A synthetic pair: the arithmetic, one count that disagrees, and one used opcode with no row."""
    tmp = tempfile.mkdtemp(prefix="native-opcodes-")
    try:
        inv = os.path.join(tmp, "inventory")
        os.makedirs(inv)
        with open(os.path.join(inv, "native_opcodes.csv"), "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["opcode", "operand", "index", "sites", "off_chain", "off_chain_receiver",
                        "off_chain_resolved", "off_chain_untyped"] + ["filler"] * 4)
            w.writerow(["0x70", "", 112, 5, 5, "Actor:3;Pawn:1", 4, 1])   # direct: index == opcode
            w.writerow(["0x61", "0x01", 257, 6, 0, "", 0, 0])             # extended: 0x61-0x60 * 256 + 1
        with open(os.path.join(inv, "opcode_usage.csv"), "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["opcode", "name", "tokens"])
            w.writerow(["0x70", "Concat_StringString", 5])
            w.writerow(["0x61", "HighNative1", 6])
            w.writerow(["0x20", "IntConst", 9])                  # below both bands: not this census's business
        bad = []
        rows, usage = read(os.path.join(inv, "native_opcodes.csv"), COLUMNS), tokens_of(
            os.path.join(inv, "opcode_usage.csv"))
        fails = reconcile(rows, usage)
        if fails:
            bad.append("the agreeing pair failed: %r" % fails)
        t = totals(rows, usage)
        if (t["direct"], t["extended"], t["band"], t["high"], t["total"]) != (5, 6, 5, 6, 11):
            bad.append("totals %r" % t)
        wrong = [dict(r) for r in rows]
        wrong[1]["index"] = 258
        if not any("arithmetic" in f for f in reconcile(wrong, usage)):
            bad.append("a wrong extended index was not caught")
        with open(os.path.join(inv, "native_opcodes.csv"), "a", newline="") as fh:
            fh.write("0x86,,134,2,,,,,,,,\n")
        rows2 = read(os.path.join(inv, "native_opcodes.csv"), COLUMNS)
        if not any("0x86" in f and "no row" not in f for f in reconcile(rows2, usage)):
            bad.append("a row with no tokens behind it was not caught")
        # The receiver columns: a placement count above the histogram, and a histogram that does not sum to the
        # off-chain count, are the two shapes a hand edit or a half-written row produces.
        wrong = [dict(r) for r in rows]
        wrong[0]["off_chain_resolved"] = "5"
        if not any("placement(s) inside a" in f for f in reconcile(wrong, usage)):
            bad.append("a placement count above the receiver histogram was not caught")
        wrong = [dict(r) for r in rows]
        wrong[0]["off_chain_receiver"] = "Actor:3;Pawn:9"
        if not any("histogram counts" in f for f in reconcile(wrong, usage)):
            bad.append("a receiver histogram that does not close the off-chain count was not caught")
        if bad:
            print("FAIL native-opcodes-census: " + "; ".join(bad))
            return 1
        print("ok   native-opcodes-census: index arithmetic, both bands, and both directions of a disagreeing count")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--check", action="store_true", help="synthetic pair: no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        return check()
    rows = read(os.path.join(ROOT, CENSUS), COLUMNS)
    usage = tokens_of(os.path.join(ROOT, USAGE))
    fails = reconcile(rows, usage)
    t = totals(rows, usage)
    print("%d rows (%d direct + %d extended), %d + %d = %d sites; opcode_usage.csv %d + %d = %d"
          % (t["rows"], t["direct_rows"], t["extended_rows"], t["direct"], t["extended"], t["total"],
             t["band"], t["high"], t["usage_total"]))
    for one in fails:
        print("FAIL " + one)
    if fails:
        return 1
    print("ok   native-opcodes-census: %d opcodes agree with opcode_usage.csv" % len(
        {int(r["opcode"], 16) for r in rows}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
