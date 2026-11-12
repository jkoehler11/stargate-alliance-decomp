#!/usr/bin/env python3
"""Measure what sits in front of a class defaults start: the record's tail, read with the field order.

`UClass::Serialize` (`0x0003a800`, `docs/NATIVE_VM.md`) writes five dwords, a `TArray` of 12-byte records, a config
`FName` and two `TArray<dword>`s *before* the defaults buffer, and `docs/CLASS_DEFAULTS.md` left the next step as
"read the block backwards from the payload's terminator". That route needs the byte in front of a known block start
to be one of those fields, so this tool measures it on the 2,209 starts the locator already fixes -- the control --
and on the 545 candidates that do not account for themselves -- the residue:

* **`last_u32`** and **`u32_is_count`**: is the four bytes before the start a plausible `TArray` count
  (`0 <= c <= 4096` with the elements in bounds)? If the last field written were a `TArray<dword>` whose count is a
  32-bit integer, this would hold on every control. It does not (`1` of `1,664` fully-declared classes), so the
  "decode the arrays backwards" step has no count to start from.
* **`pre_byte`** and **`tail12_zero`**: the byte immediately before the start, and whether the twelve bytes before it
  are all zero -- the shape a compact count of an empty array, then a second empty array and an empty `FName` number
  would leave. Counted, not assumed.
* **`stride`** / **`repeats`**: the smallest `k` in 4..24 for which the four bytes before the start repeat `k` bytes
  earlier, and how many times they repeat. A start that landed inside a table of fixed-size records shows this; a
  start that is a record's end does not.
* **`walk`** / **`walk_len`** and **`epilogue`**: whether the bytes in front of the start are script bytecode --
  a clean token walk (the image's own operand table, `tools/uscript-dis.py`) that ends exactly on the start -- and
  whether the payload carries the script epilogue `04 0b 47` at all. A class record holds the class's script *text*
  reference, not its bytecode (that lives in each `Function` export), so both are controls for "the record's tail is
  bytecode", and a class that answers neither has a tail made of something else.
* **`run_start`**: the offset the periodic run reaches when it is walked back -- the candidate true start if the
  residue is a table read too late. It is scored, not trusted: the tool re-parses at every offset in the run and keeps
  a start only when the block runs to the payload's last byte with no invented name and at least one declared
  property, which is the same rule the locator's misses already failed.

    python3 tools/class-tail.py DUMP/System --class AmbientCreatures.Bird
    python3 tools/class-tail.py DUMP/System --sweep -o inventory
    python3 tools/class-tail.py --report
    python3 tools/class-tail.py --check

Nothing here reads a defaults value: offsets, counts and the payload's own bytes only.
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
MAX_STRIDE = 24
COUNT_LIMIT = 4096


def prop_stream():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prop-stream.py")
    spec = importlib.util.spec_from_file_location("prop_stream", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def dword(d, off):
    if off < 0 or off + 4 > len(d):
        return None
    return struct.unpack_from("<I", d, off)[0]


def u32_is_count(d, start):
    """A `TArray<dword>` count would sit at start-4: 0 <= c <= COUNT_LIMIT, elements in bounds."""
    c = dword(d, start - 4)
    if c is None or c > COUNT_LIMIT:
        return False
    return start - 4 - 4 * c >= 0


def stride(d, start, maxk=MAX_STRIDE):
    """(k, repeats): the smallest stride at which the four bytes before `start` repeat, and how often."""
    anchor = dword(d, start - 4)
    if not anchor:
        # A zero dword repeats at every stride; zeros are not evidence of a fixed-size table.
        return 0, 0
    for k in range(4, maxk + 1):
        if start - k - 4 < 0:
            break
        if dword(d, start - k - 4) != anchor:
            continue
        n = 1
        while start - (n + 1) * k - 4 >= 0 and dword(d, start - (n + 1) * k - 4) == anchor:
            n += 1
        return k, n + 1
    return 0, 0


def decode(d, start, fname=2):
    """Walk the tail backwards in `UClass::Serialize`'s order with compact counts.

    Layout read forwards: two `TArray<dword>`s, the config `FName`, a `TArray` of 12-byte records, then the five
    dwords. So a walk back from a block start reads an element count as the byte in front of the elements it counts
    (a compact index, so `00` for an empty array), skips that many elements, and repeats. `None` is a refusal: a
    count byte at or above `0x40` needs a second byte, which this reading does not model.
    """
    if start > len(d):
        return None
    p, counts = start, []
    for _ in range(2):
        if p < 1:
            return None
        c = d[p - 1]
        if c >= 0x40:
            return None
        p -= 1 + 4 * c
        if p < 0:
            return None
        counts.append(c)
    if p < fname:
        return None
    p -= fname
    if p < 1:
        return None
    c = d[p - 1]
    if c >= 0x40:
        return None
    p -= 1 + 12 * c
    if p < 20:
        return None
    return counts, c, p, fname


def token_table():
    """The image's operand table, loaded lazily: `--check` must not need the inventory."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uscript-dis.py")
    spec = importlib.util.spec_from_file_location("uscript_dis", path)
    mod = importlib.util.module_from_spec(spec)
    argv = sys.argv
    sys.argv = [path]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = argv
    return mod, mod.Table()


def walkable(d, start, table, mod, minlen=24, window=96):
    """The longest bytecode run that ends exactly on `start`, or 0.

    A walk that merely does not fail is not evidence: it has to consume the window *exactly* to the start, which is
    what an instruction stream ending where the defaults begin would do.
    """
    if table is None or start - minlen < 0:
        return 0
    best = 0
    for p in range(max(0, start - window), start - minlen + 1):
        try:
            mod.parse(d[p:start], table)
        except Exception:
            continue
        best = start - p
    return best


def tail12_zero(d, start):
    if start < 12:
        return False
    return not any(d[start - 12:start])


EPILOGUE = b"\x04\x0b\x47"


def measure(d, start, table=None, mod=None):
    """Every column for one payload and one candidate start."""
    k, n = stride(d, start)
    dec = None
    for fname in (2, 3):
        dec = decode(d, start, fname)
        if dec is not None:
            break
    return {
        "last_u32": dword(d, start - 4) if start >= 4 else "",
        "u32_is_count": int(u32_is_count(d, start)),
        "pre_byte": "%02x" % d[start - 1] if start >= 1 else "",
        "tail12_zero": int(tail12_zero(d, start)),
        "stride": k,
        "repeats": n,
        "run_start": start - n * k if k else "",
        "decode_ok": int(dec is not None),
        "fname_width": dec[3] if dec else "",
        "count_last": dec[0][0] if dec else "",
        "count_prior": dec[0][1] if dec else "",
        "count_12byte": dec[1] if dec else "",
        "record_end": dec[2] if dec else "",
        "walk_len": walkable(d, start, table, mod),
        "epilogue": int(d.rfind(EPILOGUE, 0, max(0, start - 1)) >= 0),
    }


def reparse(d, mod, pkg, start, declared):
    """A better start inside the periodic run: block to the payload's end, declared names, no invention."""
    for t in range(max(0, start - MAX_STRIDE - 16), start):
        try:
            props, end, reason = mod.read_block(d[t:], pkg, False)
        except Exception:
            continue
        if reason != "None" or t + end != len(d) or not props:
            continue
        if any(p["name"] == "?" for p in props):
            continue
        known = sum(1 for p in props if p["name"] in declared)
        if known:
            return t, len(props), known
    return None


def tables(inv):
    props = collections.defaultdict(set)
    for p in (os.path.join(inv, "class_properties.csv"),):
        if os.path.isfile(p):
            with open(p, newline="") as fh:
                for r in csv.DictReader(fh):
                    props[r["class"]].add(r["property"])
    supers = {}
    p = os.path.join(inv, "classes.csv")
    if os.path.isfile(p):
        with open(p, newline="") as fh:
            for r in csv.DictReader(fh):
                supers[r["class"]] = r["super"]
    return props, supers


def ancestors(cls, props, supers):
    out, seen = set(), set()
    while cls and cls not in seen:
        seen.add(cls)
        out |= props.get(cls, set())
        cls = supers.get(cls)
    return out


def walk(system):
    for name in sorted(os.listdir(system)):
        if not name.endswith(".u"):
            continue
        try:
            pkg = Package(os.path.join(system, name))
            pkg.read_names()
            pkg.read_imports()
            pkg.read_exports()
        except Exception:
            continue
        yield name[:-2], pkg


def sweep(system, out):
    """One row per class export that has a candidate start; the control and the residue are both written."""
    base = os.path.join(ROOT, "inventory")
    props, supers = tables(base)
    per_class = {}
    p = os.path.join(base, "class_defaults_per_class.csv")
    if os.path.isfile(p):
        with open(p, newline="") as fh:
            for r in csv.DictReader(fh):
                per_class[(r["package"], r["class"])] = r
    shapes = {}
    p = os.path.join(base, "class_records.csv")
    if os.path.isfile(p):
        with open(p, newline="") as fh:
            for r in csv.DictReader(fh):
                shapes[(r["package"], r["class"])] = r
    mod = prop_stream()
    dis, table = token_table()
    rows = []
    for pkg_name, pkg in walk(system):
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            cls = pkg.name(exp["name"])
            key = (pkg_name, cls)
            pc = per_class.get(key)
            sh = shapes.get(key)
            if sh is None or not sh.get("block_start", "").isdigit():
                continue
            start = int(sh["block_start"])
            if start < 1:
                continue
            d = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            m = measure(d, start, table, dis)
            row = {
                "package": pkg_name,
                "class": cls,
                "shape": sh.get("shape", ""),
                "control": int(pc.get("fully_declared", "0") == "1") if pc else 0,
                "block_start": start,
                "payload": len(d),
            }
            row.update(m)
            found = ""
            if m["stride"] and m["repeats"] >= 3:
                hit = reparse(d, mod, pkg, start, ancestors(cls, props, supers))
                if hit:
                    found = "%d:%d:%d" % hit
            row["better_start"] = found
            rows.append(row)
    path = os.path.join(out, "class_tails.csv")
    os.makedirs(out, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("wrote %s: %d rows" % (path, len(rows)))
    report(rows)
    return 0


def report(rows):
    if rows and isinstance(rows[0], list):
        header, rows = rows[0], [dict(zip(rows[0], r)) for r in rows[1:]]
    for name, sel in (("control", [r for r in rows if str(r["control"]) == "1"]),
                      ("residue", [r for r in rows if str(r["control"]) != "1"])):
        if not sel:
            continue
        n = len(sel)
        print("%s: %d candidates" % (name, n))
        print("  last four bytes a plausible TArray count: %d/%d" % (sum(int(r["u32_is_count"]) for r in sel), n))
        print("  twelve bytes before the start all zero : %d/%d" % (sum(int(r["tail12_zero"]) for r in sel), n))
        pre = collections.Counter(r["pre_byte"] for r in sel)
        print("  byte before the start                 : %s" % dict(pre.most_common(5)))
        strides = collections.Counter(int(r["stride"]) for r in sel)
        print("  periodic before the start              : %d/%d (%s)"
              % (sum(v for k, v in strides.items() if k), n, dict(strides.most_common(5))))
        better = [r for r in sel if r["better_start"]]
        print("  a better start inside the run          : %d/%d" % (len(better), n))
        print("  tail arrays decode in field order      : %d/%d" % (sum(int(r["decode_ok"]) for r in sel), n))
        walks = [int(r["walk_len"] or 0) for r in sel]
        print("  a bytecode run ends on the start        : %d/%d (longest run %d bytes)"
              % (sum(1 for w in walks if w), n, max(walks) if walks else 0))
        print("  the payload carries 04 0b 47            : %d/%d" % (sum(int(r["epilogue"]) for r in sel), n))
        ends = collections.Counter(r["record_end"] for r in sel if str(r["decode_ok"]) == "1")
        print("  where the walk lands (offset, top five) : %s" % dict(ends.most_common(5)))


def selftest():
    """Synthetic payloads: the three readings must answer what the bytes say, and nothing may be written."""
    body = bytes.fromhex(
        "0102030405060708"            # 0..7   filler
        "00000000"                    # 8..11  two empty counts and an FName number
        "aa5297fe01000000"            # 12..19 a 9-byte unit
        "00aa5297fe01000000"          # 20..28 the same unit again
        "01"                          # 29     terminator-ish
    )
    start = 29
    m = measure(body, start)
    assert m["stride"] == 9, m
    assert m["repeats"] == 2, m
    assert m["pre_byte"] == "00", m
    assert m["tail12_zero"] == 0, m
    assert m["last_u32"] == 1, m
    assert m["u32_is_count"] == 1, m
    flat = bytes(48)
    m = measure(flat + b"x", 48)
    assert m["last_u32"] == 0 and m["u32_is_count"] == 1, m
    assert m["tail12_zero"] == 1, m
    assert m["stride"] == 0, m
    empty = bytes(32) + b"\x00"
    dec = decode(empty, 32)
    assert dec is not None and dec[0] == [0, 0] and dec[3] in (2, 3), dec
    assert decode(b"\x7f" * 26, 26) is None, "a count byte with the continuation bit must be refused"
    print("class-tail self-test: ok")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("system", nargs="?", help="the dump's System directory")
    ap.add_argument("--class", dest="klass", help="one class, as Package.Class")
    ap.add_argument("--sweep", action="store_true", help="every package -> inventory/class_tails.csv")
    ap.add_argument("--report", action="store_true", help="print the totals for a swept artifact")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump")
    ap.add_argument("-o", "--out", default="inventory")
    args = ap.parse_args()

    if args.check:
        return selftest()
    if args.report:
        with open(os.path.join(args.out, "class_tails.csv"), newline="") as fh:
            report(list(csv.DictReader(fh)))
        return 0
    if not args.system:
        ap.error("a System directory, --report or --check is required")
    if args.sweep:
        return sweep(args.system, args.out)
    if not args.klass:
        ap.error("--class Package.Class, --sweep, --report or --check")
    want_pkg, _, want_cls = args.klass.partition(".")
    base = os.path.join(ROOT, "inventory")
    props, supers = tables(base)
    mod = prop_stream()
    for pkg_name, pkg in walk(args.system):
        if pkg_name != want_pkg:
            continue
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class" or pkg.name(exp["name"]) != want_cls:
                continue
            for sh in csv.DictReader(open(os.path.join(base, "class_records.csv"), newline="")):
                if sh["package"] == pkg_name and sh["class"] == want_cls:
                    start = int(sh["block_start"])
                    d = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
                    m = measure(d, start)
                    print("%s.%s payload %d, candidate start %d" % (pkg_name, want_cls, len(d), start))
                    for k, v in m.items():
                        print("  %-12s %s" % (k, v))
                    if m["stride"] and m["repeats"] >= 3:
                        print("  better_start %s" % (reparse(d, mod, pkg, start,
                                                            ancestors(want_cls, props, supers)),))
                    return 0
    print("FAIL class-tail: %s not found" % args.klass)
    return 1


if __name__ == "__main__":
    sys.exit(main())
