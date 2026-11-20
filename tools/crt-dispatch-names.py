#!/usr/bin/env python3
"""Name the build's CRT dispatch stubs from the table entry they hand `__cintrindisp1`.

MSVC compiles the single-argument transcendental intrinsics (`exp`, `sinh`, `cosh`, `tanh`, ...) to a
ten-byte stub when the CRT is linked with its own implementation: `mov edx, <entry>; jmp
__cintrindisp1`. The runtime helper takes the argument table in `edx` and dispatches on it, so the
stub itself carries no string -- the *entry* does, and it is a length-prefixed name:

    ba ac d3 5c 00     mov edx, 0x005cd3ac        ; entry: 03 "exp", flags, four code pointers
    e9 38 47 00 00     jmp 0x003ee17a             ; __cintrindisp1

That makes the name a measurement, not a guess: the build's own data says which function the stub is.
This tool finds every stub in the image, reads the name out of the entry it passes, and reports the row
the map has (or the gap it does not):

    python3 tools/crt-dispatch-names.py --xbe <dump>/default.xbe -o inventory
    python3 tools/crt-dispatch-names.py --check                # no dump, no writes
    python3 tools/crt-dispatch-names.py --emit-boundaries      # the `rename` rows for xbe_boundaries.csv
    python3 tools/crt-dispatch-names.py --selftest             # synthetic stubs and refusals

`inventory/crt_dispatch_stubs.csv` is the artifact: one row per stub, with the entry address, the name
the entry carries, the helper it jumps to and the map row that reaches it (if any). A stub no map row
starts at is a boundary gap, not an absence of evidence, and the artifact says so instead of dropping
it -- adding a row for it moves the map's function count and belongs to the boundary thread.
"""
import argparse
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

MAP = os.path.join(ROOT, "symbols", "xbox_functions.csv")
THUNKS = os.path.join(ROOT, "inventory", "thunk_targets.csv")
CSV_PATH = os.path.join(ROOT, "inventory", "crt_dispatch_stubs.csv")
COLUMNS = ["address", "name", "size", "table", "entry", "tail", "thunk", "in_map"]
HELPER = "__cintrindisp1"
STUB_SIZE = 10
DATA_SECTION_NAMES = (".rdata", ".data", ".data1")


def read_map(path=MAP):
    out = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            out[int(r["address"], 16)] = (r["name"], int(r["size"]))
    return out


def read_csv(path=CSV_PATH):
    if not os.path.exists(path):
        raise SystemExit("%s: missing (run tools/crt-dispatch-names.py with the dump)" % path)
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if list(rows[0].keys()) != COLUMNS:
        raise SystemExit("%s: columns %s != %s" % (path, list(rows[0].keys()), COLUMNS))
    return rows


def entry_name(xbe, table):
    """The length-prefixed name the dispatch entry carries, or "" when it does not carry one."""
    raw = xbe.read_va(table, 16) or b""
    if not raw:
        return ""
    n = raw[0]
    if not 2 <= n <= 12 or len(raw) < n + 2:
        return ""
    name = raw[1:1 + n]
    if not all(32 < b < 127 for b in name) or raw[1 + n] != 0:
        return ""
    return name.decode("ascii")


def decode_stub(body, va):
    """(table, tail) for `BA imm32; E9 rel32`, or None."""
    if len(body) < STUB_SIZE or body[0] != 0xBA or body[5] != 0xE9:
        return None
    table = struct.unpack_from("<I", body, 1)[0]
    rel = struct.unpack_from("<i", body, 6)[0]
    return table, va + STUB_SIZE + rel


def measure(xbe_path):
    from xbe import Xbe  # noqa: E402  (dump-side, so --check runs without it)

    xbe = Xbe(xbe_path)
    rows = read_map()
    thunks = {}
    if os.path.exists(THUNKS):
        for r in csv.DictReader(open(THUNKS, newline="")):
            thunks.setdefault(int(r["target"], 16) if r["target"] else 0, []).append(r["address"])
    code = [s for s in xbe.sections
            if not s.name.startswith("$$") and s.name not in DATA_SECTION_NAMES]
    out = []
    for sec in code:
        blob = xbe.data[sec.foff:sec.foff + min(sec.fsize, sec.vsize)]
        i = blob.find(b"\xba")
        while i >= 0 and i + STUB_SIZE <= len(blob):
            got = decode_stub(blob[i:i + STUB_SIZE], sec.va + i)
            if got:
                table, tail = got
                tail_name = rows.get(tail, ("", 0))[0]
                if tail_name != HELPER:
                    i = blob.find(b"\xba", i + 1)
                    continue
                sec_t = xbe.section_for(table)
                if sec_t is None or sec_t.name not in DATA_SECTION_NAMES:
                    i = blob.find(b"\xba", i + 1)
                    continue
                try:
                    name = entry_name(xbe, table)
                except Exception:
                    name = ""
                if name:
                    va = sec.va + i
                    have = rows.get(va)
                    out.append({
                        "address": "0x%08x" % va,
                        "name": have[0] if have else "",
                        "size": str(have[1] if have else STUB_SIZE),
                        "table": "0x%08x" % table,
                        "entry": name,
                        "tail": "0x%08x" % tail,
                        "thunk": ",".join(thunks.get(va, [])),
                        "in_map": "yes" if have else "no",
                    })
            i = blob.find(b"\xba", i + 1)
    out.sort(key=lambda r: int(r["address"], 16))
    return out


def write_csv(path, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def rule_name(entry):
    """The build's name for the stub: the CRT prefix plus the intrinsic the entry names."""
    return "_" + entry


def check(path=CSV_PATH):
    """Reconcile the artifact with the map and with its own naming rule. No dump, no writes."""
    bad = []
    try:
        rows = read_csv(path)
    except SystemExit as exc:
        print("FAIL crt-dispatch-names: %s" % exc)
        return 1
    mp = read_map()
    last = -1
    for r in rows:
        va = int(r["address"], 16)
        if va <= last:
            bad.append("%s: addresses out of order or duplicated" % r["address"])
        last = va
        if not r["entry"]:
            bad.append("%s: no entry name" % r["address"])
            continue
        want = rule_name(r["entry"])
        have = mp.get(va)
        if r["in_map"] == "yes":
            if have is None:
                bad.append("%s: the artifact says the map has a row and it does not" % r["address"])
            elif have[0] != want and have[0] != r["name"]:
                bad.append("%s: the map calls it %s, the rule says %s" % (r["address"], have[0], want))
            elif int(r["size"]) != have[1]:
                bad.append("%s: size %s, the map says %d" % (r["address"], r["size"], have[1]))
        elif have is not None:
            bad.append("%s: the artifact says the map has no row, but it holds %s"
                       % (r["address"], have[0]))
        tail = mp.get(int(r["tail"], 16))
        if tail is None or tail[0] != HELPER:
            bad.append("%s: tail %s is not %s in the map" % (r["address"], r["tail"], HELPER))
        for t in [x for x in r["thunk"].split(",") if x]:
            tr = mp.get(int(t, 16))
            if tr is None or not tr[0].startswith("thunk_"):
                bad.append("%s: thunk %s is not a thunk row" % (r["address"], t))
    if bad:
        for b in bad[:12]:
            print("FAIL crt-dispatch-names: %s" % b)
        if len(bad) > 12:
            print("FAIL crt-dispatch-names: ... and %d more" % (len(bad) - 12))
        return 1
    named = sum(1 for r in rows if r["in_map"] == "yes")
    print("ok   crt-dispatch-names: %d stub(s) (%s), %d in the map, %d a boundary gap"
          % (len(rows), ", ".join("%s %s" % (r["address"], r["entry"]) for r in rows),
             named, len(rows) - named))
    return 0


def emit_boundaries(rows):
    """The `rename` rows this artifact justifies, for symbols/xbe_boundaries.csv."""
    w = csv.writer(sys.stdout, lineterminator="\n")
    mp = read_map()
    for r in rows:
        if r["in_map"] != "yes":
            continue
        va = int(r["address"], 16)
        want = rule_name(r["entry"])
        have = mp.get(va)
        if have is None or have[0] == want:
            continue
        w.writerow([r["address"], "rename", want, "",
                    "`mov edx, %s; jmp %s` in %s bytes, no fall-through; the dispatch entry at %s begins "
                    "with the length-prefixed name %r, and %s is the helper that reads it"
                    % (r["table"], r["tail"], r["size"], r["table"], r["entry"], HELPER)])


def selftest():
    failures = []

    def expect(label, got, want):
        if got != want:
            failures.append("%s: got %r, want %r" % (label, got, want))

    stub = bytes([0xBA, 0xAC, 0xD3, 0x5C, 0x00, 0xE9, 0x38, 0x47, 0x00, 0x00])
    expect("exp stub", decode_stub(stub, 0x3E9A38), (0x5CD3AC, 0x3EE17A))
    expect("a call is not a stub", decode_stub(bytes([0xE8, 1, 0, 0, 0]) + stub, 0x1000), None)
    expect("truncated", decode_stub(stub[:6], 0x1000), None)
    expect("wrong opcode after mov", decode_stub(bytes([0xBA, 1, 0, 0, 0, 0xEB, 0, 0, 0, 0]), 0x1000),
           None)
    expect("name of exp", rule_name("exp"), "_exp")
    expect("name of sinh", rule_name("sinh"), "_sinh")
    if failures:
        for f in failures:
            print("FAIL crt-dispatch-names: %s" % f)
        return 1
    print("ok   crt-dispatch-names: 2 decodings, 2 refusals, 2 rule cases")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe", help="the image, for the stub bytes (write mode)")
    ap.add_argument("-o", "--out", default="inventory", help="where crt_dispatch_stubs.csv goes")
    ap.add_argument("--check", action="store_true", help="reconcile the csv with the map, no dump")
    ap.add_argument("--emit-boundaries", action="store_true",
                    help="print the `rename` rows for symbols/xbe_boundaries.csv")
    ap.add_argument("--selftest", action="store_true", help="synthetic stubs, no dump")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.check:
        return check()
    if a.emit_boundaries:
        emit_boundaries(read_csv())
        return 0
    if not a.xbe:
        ap.error("--xbe is required to measure (or use --check/--selftest)")
    rows = measure(a.xbe)
    write_csv(os.path.join(a.out, "crt_dispatch_stubs.csv"), rows)
    print("wrote %s/crt_dispatch_stubs.csv: %d stub(s) -- %s"
          % (a.out, len(rows), ", ".join("%s %s" % (r["address"], r["entry"]) for r in rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
