#!/usr/bin/env python3
"""Is each native body a function start? The disassembler's answer, per address (PLAN 3.9).

`inventory/native_targets.csv` names a body for **883 distinct addresses**, and 274 of them have no row
in `symbols/xbox_functions.csv`. That is expected rather than suspicious -- a native's only reference is
the registration table in `.data`, so a call-graph census never makes a function out of it -- but
"expected" is not "measured", and the difference is a denominator: `docs/PATH.md` rests its session
estimate on 14,957 functions, and if the 274 are real function starts the number is wrong by 1.8%.

So this tool classifies every address on two independent axes:

* **the census**, from committed files only: does the address equal the entry point of a function in
  `symbols/xbox_functions.csv`, or fall inside one, or outside every function's `[address, address+size)`
  range? (Measured: 609 equal an entry, 0 fall inside a range, 274 fall outside all of them, so the
  question is not one of Ghidra having folded them into a neighbour.)
* **the image**, from `default.xbe`: what byte sits immediately before the address (a function boundary
  in this build is `0xCC` padding or the previous body's `ret`), what the first bytes look like as a
  prologue, and which section the address is in.

The third axis is the only one that can settle it, and it is Ghidra's own answer rather than a byte
pattern: `tools/ghidra/ghidra_scripts/NativeCensus.java` creates a function at each address and reports
whether the disassembler accepted it, the size of the body it produced, its instruction count and
whether that body ends on a flow terminator. Ghidra runs on the desktop (3-6 minutes for the import), so
its verdicts arrive separately:

    python3 tools/native-census.py --dump "$SG1_DUMP" -o inventory
    python3 tools/native-census.py --xbe default.xbe --verdicts $WORK/native-census/verdicts.csv -o inventory
    python3 tools/native-census.py --check        # the committed table, no dump

Without `--verdicts` the disassembler columns are carried over from the committed table, so `make native-census`
refreshes the cheap half without pretending the expensive half was re-measured.
    python3 tools/native-census.py --selftest     # classifier fixtures, no dump

`inventory/native_census.csv` holds one row per distinct body address. `--check` re-derives the census
column from the two committed tables (the third column of evidence needs the image, which CI does not
have) and refuses a table whose rows or split no longer reproduce.
"""
import argparse
import bisect
import collections
import csv
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

TARGETS = os.path.join(ROOT, "inventory", "native_targets.csv")
FUNCTIONS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
BODIES = os.path.join(ROOT, "inventory", "native_bodies.csv")
OUT = os.path.join(ROOT, "inventory", "native_census.csv")

COLS = ["body_va", "names", "records", "classes", "section", "census", "boundary", "prologue",
        "first_bytes", "disassembler", "body_bytes", "instructions", "ends_terminal"]

# MSVC 7.1 openings seen at this build's real entries, longest first: the order is the classifier.
PROLOGUES = (
    (b"\x8b\xff", "hotpatch"),          # mov edi, edi
    (b"\x55\x8b\xec", "frame"),         # push ebp; mov ebp, esp
    (b"\x64\xa1", "seh"),               # mov eax, fs:[0]
    (b"\x8b\x44\x24", "arg-load"),      # mov eax, [esp+n]
    (b"\x8b\x4c\x24", "arg-load"),
    (b"\x8b\x54\x24", "arg-load"),
    (b"\x81\xec", "alloc"),             # sub esp, imm32
    (b"\x83\xec", "alloc"),             # sub esp, imm8
    (b"\xc2", "empty-return"),          # ret imm16: a body that is only its own return
    (b"\x6a", "push-imm"),
    (b"\x68", "push-imm"),
    (b"\x51", "push-reg"),              # push ecx (the common first register)
    (b"\x53", "push-reg"),
    (b"\x55", "push-reg"),
    (b"\x56", "push-reg"),
    (b"\x57", "push-reg"),
    (b"\xb8", "mov-eax"),
)

BOUNDARIES = {0xCC: "int3", 0x00: "zero", 0xC3: "return", 0xC2: "return", 0x90: "nop"}


def prologue(first):
    """Name the prologue shape from the first bytes, or `other`."""
    for pat, name in PROLOGUES:
        if first.startswith(pat):
            return name
    return "other"


def boundary(byte):
    """What the byte before an address says about it: padding, a `ret`, or data."""
    return BOUNDARIES.get(byte, "data") if byte is not None else "unreadable"


def census_table(rows, funcs):
    """[(start, end, name)] sorted by start, from the committed symbol map."""
    out = [(int(f["address"], 16), int(f["address"], 16) + int(f["size"] or 0), f["name"]) for f in funcs]
    out.sort()
    return out


def classify(va, table, starts):
    """`census-start`, `inside` or `outside` — the whole containment question, from committed files."""
    i = bisect.bisect_right(starts, va) - 1
    if i >= 0 and table[i][0] <= va < table[i][1]:
        return "census-start" if table[i][0] == va else "inside"
    return "outside"


def load_targets(path):
    """{va: {names: [...], records: n, classes: [...]}} over every registration record with a body."""
    bodies = collections.OrderedDict()
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            body = (r.get("body") or "").strip()
            if not body:
                continue
            va = int(body, 16)
            e = bodies.setdefault(va, {"names": [], "records": 0, "classes": []})
            e["records"] += 1
            name = (r.get("name") or "").strip()
            if name and name not in e["names"]:
                e["names"].append(name)
            cls = (r.get("class") or "").strip()
            if cls and cls not in e["classes"]:
                e["classes"].append(cls)
    return bodies


def load_verdicts(path):
    """{va: {column: value}} from NativeCensus.java; the image-side axis, optional."""
    out = {}
    if not path or not os.path.isfile(path):
        return out
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            out[int(r["body_va"], 16)] = r
    return out


def build(bodies, table, starts, xbe, verdicts):
    rows = []
    for va in sorted(bodies):
        e = bodies[va]
        first = prev = None
        section = ""
        if xbe is not None:
            sec = xbe.section_for(va)
            section = sec.name if sec else "(none)"
            try:
                first = xbe.read_va(va, 8)
            except Exception:                        # a page hole: the address is still classified
                first = b""
            try:
                prev = xbe.read_va(va - 1, 1)[0]
            except Exception:
                prev = None
        v = verdicts.get(va, {})
        row = collections.OrderedDict()
        row["body_va"] = "0x%08x" % va
        row["names"] = " ".join(e["names"])
        row["records"] = e["records"]
        row["classes"] = " ".join(e["classes"])
        row["section"] = section
        row["census"] = classify(va, table, starts)
        row["boundary"] = boundary(prev) if xbe is not None else ""
        row["prologue"] = prologue(first or b"") if xbe is not None else ""
        row["first_bytes"] = (first or b"").hex()
        row["disassembler"] = v.get("verdict", "")
        row["body_bytes"] = v.get("body_bytes", "")
        row["instructions"] = v.get("instructions", "")
        row["ends_terminal"] = v.get("ends_terminal", "")
        rows.append(row)
    return rows


def summarise(rows):
    """The numbers a doc quotes: the split, and the evidence behind each outside address."""
    census = collections.Counter(r["census"] for r in rows)
    outside = [r for r in rows if r["census"] == "outside"]
    verdict = collections.Counter(r["disassembler"] or "(not asked)" for r in outside)
    print("%d distinct body addresses: %d are function starts in the census, %d fall inside a function,"
          " %d fall outside every function" % (len(rows), census["census-start"], census["inside"],
                                               census["outside"]))
    for label, col in (("boundary byte before an outside address", "boundary"),
                       ("prologue shape at an outside address", "prologue")):
        c = collections.Counter(r[col] for r in outside)
        print("  %s: %s" % (label, ", ".join("%s %d" % kv for kv in c.most_common())))
    print("  disassembler verdict on the outside addresses: %s"
          % ", ".join("%s %d" % kv for kv in verdict.most_common()))
    accepted = [r for r in outside if r["disassembler"] == "accepted"]
    if accepted:
        term = collections.Counter((r["ends_terminal"] or "(not recorded)") for r in accepted)
        sizes = sorted(int(r["body_bytes"]) for r in accepted if r["body_bytes"].isdigit())
        insn = sorted(int(r["instructions"]) for r in accepted if r["instructions"].isdigit())
        print("  accepted bodies: %s; median %d bytes, median %d instructions"
              % (", ".join("ends-terminator=%s %d" % kv for kv in term.most_common()),
                 sizes[len(sizes) // 2] if sizes else -1, insn[len(insn) // 2] if insn else -1))
    return census, verdict


def run(args):
    bodies = load_targets(args.targets)
    with open(args.functions, newline="") as fh:
        funcs = list(csv.DictReader(fh))
    table = census_table(None, funcs)
    starts = [t[0] for t in table]
    xbe = None
    if args.xbe:
        spec = importlib.util.spec_from_file_location("xbe", os.path.join(HERE, "xbe.py"))
        xbemod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(xbemod)
        xbe = xbemod.Xbe(args.xbe)
    # The image half is a Ghidra round of its own, so a run without `--verdicts` carries the verdicts the
    # committed table already holds instead of blanking the column.
    verdicts = load_verdicts(args.verdicts or (OUT if os.path.isfile(OUT) else None))
    rows = build(bodies, table, starts, xbe, verdicts)
    summarise(rows)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, "native_census.csv")
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLS, lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print("wrote %s (%d rows)" % (path, len(rows)))
    if args.walk:
        for r in rows:
            if r["census"] == "outside":
                print("%-11s %-34s %-8s %-12s %s" % (r["body_va"], r["names"].split(" ")[0][:34],
                                                     r["boundary"], r["prologue"], r["disassembler"]))
    return 0


def check(args):
    """Re-derive the census column from the committed tables and compare it with the committed CSV."""
    if not os.path.isfile(OUT):
        sys.exit("native-census: %s is missing" % os.path.relpath(OUT, ROOT))
    with open(OUT, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if list(rows[0].keys()) != COLS:
        sys.exit("native-census: --check: the table's columns are %r, the tool writes %r"
                 % (list(rows[0].keys()), COLS))
    bodies = load_targets(TARGETS)
    with open(FUNCTIONS, newline="") as fh:
        table = census_table(None, list(csv.DictReader(fh)))
    starts = [t[0] for t in table]
    if len(rows) != len(bodies):
        sys.exit("native-census: --check: %d rows for %d distinct body addresses" % (len(rows), len(bodies)))
    seen = set()
    bad = []
    for r in rows:
        va = int(r["body_va"], 16)
        if va in seen:
            bad.append("%s twice" % r["body_va"])
        seen.add(va)
        if va not in bodies:
            bad.append("%s is not a body in native_targets.csv" % r["body_va"])
            continue
        want = classify(va, table, starts)
        if r["census"] != want:
            bad.append("%s: table says %s, the symbol map says %s" % (r["body_va"], r["census"], want))
        # The image-side columns are blank when the table was built without the XBE; a half-filled
        # column is the failure to catch, not an empty one.
        if bool(r["boundary"]) != bool(r["prologue"]):
            bad.append("%s: one image-side column is filled and the other is not" % r["body_va"])
        if r["disassembler"] and r["disassembler"] not in ("accepted", "existing", "inside", "rejected"):
            bad.append("%s: unknown disassembler verdict %r" % (r["body_va"], r["disassembler"]))
    if bad:
        sys.exit("native-census: --check: " + "; ".join(bad[:6]))
    census = collections.Counter(r["census"] for r in rows)
    verdict = collections.Counter(r["disassembler"] for r in rows if r["census"] == "outside")
    print("native-census: %d body addresses, %d census starts, %d outside every function (%s)"
          % (len(rows), census["census-start"], census["outside"],
             ", ".join("%s %d" % kv for kv in sorted(verdict.items())) or "disassembler not asked"))
    return 0


def selftest():
    cases = 0
    # Containment: an entry, the last byte inside a function, the byte after it, and a gap.
    table = [(0x1000, 0x1010, "a"), (0x1020, 0x1030, "b")]
    starts = [t[0] for t in table]
    assert classify(0x1000, table, starts) == "census-start"
    assert classify(0x100f, table, starts) == "inside"
    assert classify(0x1010, table, starts) == "outside"
    assert classify(0x1024, table, starts) == "inside"
    assert classify(0x0fff, table, starts) == "outside"
    cases += 1
    # Prologues: the order matters, `mov edi,edi` must not read as `push reg`.
    assert prologue(b"\x8b\xff\x55\x8b\xec") == "hotpatch"
    assert prologue(b"\x55\x8b\xec\x83") == "frame"
    assert prologue(b"\x64\xa1\x00\x00") == "seh"
    assert prologue(b"\x8b\x44\x24\x04") == "arg-load"
    assert prologue(b"\x83\xec\x28\x56") == "alloc"
    assert prologue(b"\xc2\x08\x00\xcc") == "empty-return"
    assert prologue(b"\x51\x56\x8b\x74") == "push-reg"
    assert prologue(b"\x0f\x57\xc0\x56") == "other"
    cases += 1
    # Boundaries: the three this build uses, and data.
    assert boundary(0xCC) == "int3" and boundary(0x00) == "zero"
    assert boundary(0xC3) == "return" and boundary(0xC2) == "return"
    assert boundary(0x42) == "data" and boundary(None) == "unreadable"
    cases += 1
    # The row key is the address, and a body named by two registrations is one row with two records.
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as fh:
        fh.write("block,block_va,record_va,name,class,function,body\n")
        fh.write("0,0x100,0x100,intACexecOne,A,One,0x00401000\n")
        fh.write("1,0x110,0x110,intACexecTwo,A,Two,0x00401000\n")
        path = fh.name
    bodies = load_targets(path)
    assert list(bodies) == [0x401000], bodies
    assert bodies[0x401000]["records"] == 2 and len(bodies[0x401000]["names"]) == 2, bodies
    os.unlink(path)
    cases += 1
    print("native-census: selftest passed (%d cases)" % cases)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--targets", default=TARGETS)
    ap.add_argument("--functions", default=FUNCTIONS)
    ap.add_argument("--xbe", help="default.xbe; the image-side columns need it")
    ap.add_argument("--dump", help="dump root, for --xbe's default location")
    ap.add_argument("--verdicts", help="NativeCensus.java's CSV")
    ap.add_argument("-o", "--out", default=None, help="write native_census.csv here")
    ap.add_argument("--walk", action="store_true", help="print the outside addresses")
    ap.add_argument("--check", action="store_true", help="the committed table, no dump")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.check:
        return check(args)
    if not args.xbe and args.dump:
        args.xbe = os.path.join(args.dump, "Stargate_SG-1_-_The_Alliance-2005-12-15", "default.xbe")
    if args.xbe and not os.path.isfile(args.xbe):
        sys.exit("native-census: required: %s is not a file (give --xbe or --dump)" % args.xbe)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
