#!/usr/bin/env python3
"""The engine's reserved names: which `FName` indices `default.xbe` creates before any package loads.

`docs/systems/STATE_NATIVES.md` ends on three open questions that all read the *name* table rather than the state
code: which name the default entry label `FName 100` is, which events the setter fires at indices 316 and 317, and
-- indirectly -- whether a bare `GotoState('X')` can land on a state whose entry byte carries another name.  The
answer is not in the packages: a package's name table is per-package and the global one (`GNames`, `0x006be0d8`) is
built at run time.  What the image carries is the *seed*: `FName::StaticInit` (`0x5c080`) zeroes the name hash
(`0x006ba0c8`, 4096 dwords), sets the initialised flag (`0x006be0c8`) and then, one entry at a time, allocates an
`FNameEntry` of `2*len+0xe` bytes, writes a **name index into the entry's first dword**, copies the text and hands
it to the append routine (`0x5be10`) -- which grows `GNames` to cover that index, **nulling the slots it skipped**,
and stores the entry there.  So every reserved index is an immediate in the image, and each name is a wide literal
beside it.

This tool reads that seed.  One row per name: its index, the name, the instruction that writes the index, the call
that appends the entry.  Nothing here publishes script or a decompiled body -- a row is an index, an identifier and
two addresses.

    python3 tools/gname-seed.py --xbe DUMP/default.xbe -o inventory/gname_seed.csv \\
        --write-doc docs/systems/STATE_NATIVES.md
    python3 tools/gname-seed.py --check-doc docs/systems/STATE_NATIVES.md   # no dump, no write
    python3 tools/gname-seed.py --check                                    # no dump, no write
    python3 tools/gname-seed.py --xbe DUMP/default.xbe --verify            # re-read the rows from the image

`--check` is the committed-CSV half (shape, ordering, the three slots the state machine uses, the run table), which
is what the per-commit gate can answer without the dump; `--verify` re-derives every row from `default.xbe`.
"""
import argparse
import csv
import os
import re
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(ROOT, "inventory", "gname_seed.csv")
DOC_PATH = os.path.join(ROOT, "docs", "systems", "STATE_NATIVES.md")
MARK_BEGIN = "<!-- generated:gname-seed -->"
MARK_END = "<!-- /generated:gname-seed -->"

FIELDS = ["index", "name", "run", "store", "append", "shape"]

# The three globals and the two routines the read is built on.  `--verify` re-reads the append routine and fails if
# it no longer touches `GNames`, so a wrong address cannot quietly produce a plausible table.
GNAMES = 0x006BE0D8
NAME_HASH = 0x006BA0C8
INIT_FLAG = 0x006BE0C8
APPEND = 0x5BE10
CTOR = 0x5BDC0

# The slots the state machine reads: the default entry label `execGotoState` passes when the call names none (100),
# and the two events `state_set` fires around the switch (317 on the old state before it, 316 on the new one after).
SLOTS = [(100, "Begin", "the default entry label `execGotoState` passes (0x487af: `mov eax,0x64`)"),
         (317, "EndState", "the event `state_set` fires on the old state before the switch (0x48f73)"),
         (316, "BeginState", "the event `state_set` fires on the new state after it (0x49073)")]


def die(msg):
    sys.exit("gname-seed: " + msg)


class Image:
    """`default.xbe` plus a window disassembler and a wide-string reader."""

    def __init__(self, path):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import xbe as X

        self.x = X.Xbe(path)
        self.text = [s for s in self.x.sections if s.name == ".text"][0]

    def dis(self, va, size):
        blob = self.x.read_va(va, size)
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as fh:
            fh.write(blob)
            path = fh.name
        try:
            out = subprocess.run(
                ["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                 "--adjust-vma", hex(va), path],
                capture_output=True, text=True, check=True).stdout
        finally:
            os.unlink(path)
        rows = []
        for line in out.splitlines():
            m = re.match(r"^\s+([0-9a-f]+):\s+((?:[0-9a-f]{2} )+)\s*(\S+)\s*(.*)$", line)
            if m:
                rows.append({"va": int(m.group(1), 16),
                             "ins": (m.group(3) + " " + " ".join(m.group(4).split())).strip()})
        return rows

    def calls_to(self, target):
        """Every `call rel32` site that lands on `target`, as VAs -- the append routine's callers."""
        blob = self.x.data[self.text.foff:self.text.foff + self.text.fsize]
        out = []
        for i in range(len(blob) - 5):
            if blob[i] != 0xE8:
                continue
            rel = struct.unpack_from("<i", blob, i + 1)[0]
            va = self.text.va + i
            if (va + 5 + rel) & 0xFFFFFFFF == target:
                out.append(va)
        return out

    def wide(self, va, limit=120):
        """The UTF-16LE identifier at `va`, or `None` -- the seed's names are all plain identifiers."""
        for s in self.x.sections:
            if s.va <= va < s.va + s.vsize:
                raw = self.x.data[s.foff + (va - s.va):s.foff + (va - s.va) + limit]
                text = raw.decode("utf-16-le", "ignore").split("\x00")[0]
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", text or ""):
                    return text
                return None
        return None

    def append_routine_reads_gnames(self):
        """The append routine must be the one that indexes `GNames` -- a wrong address has to fail here."""
        body = self.dis(APPEND, 0xC0)
        hits = [r for r in body if ("ds:0x%x" % GNAMES) in r["ins"]
                or ("0x%x" % GNAMES) in r["ins"]]
        return hits


def resolve_reg(pre, reg, at):
    """The constant `reg` holds at `at`, from the last write before it -- an immediate or a `xor r,r` zero."""
    for row in reversed([r for r in pre if r["va"] < at]):
        ins = row["ins"]
        m = re.match(r"^mov %s,(0x[0-9a-f]+)$" % reg, ins)
        if m:
            return int(m.group(1), 16)
        if re.match(r"^xor %s,%s$" % (reg, reg), ins):
            return 0
        if re.match(r"^(mov|lea|pop|call|xor|add|sub|or|and) %s\b" % reg, ins):
            return None
    return None


def read_site(img, site):
    """`(index, name, store_va, shape)` for one append call, or `None` when the block does not resolve."""
    pre = [r for r in img.dis(max(img.text.va, site - 0xC0), 0xC5) if r["va"] < site]
    # Shape 2: the entry comes back from the constructor, so the index is its second argument -- the push before
    # the name push.  Shape 1: the block allocates the entry inline and stores the index itself.
    ctor_at = None
    for row in reversed(pre):
        if row["ins"].startswith("call 0x%x" % CTOR):
            ctor_at = row["va"]
            break
    if ctor_at is not None:
        args = [r for r in pre if r["va"] < ctor_at and r["ins"].startswith("push 0x")][-2:]
        name = img.wide(int(args[-1]["ins"].split()[-1], 16)) if len(args) == 2 else None
        index = int(args[-2]["ins"].split()[-1], 16) if len(args) == 2 else None
        if name is None or index is None or index > 0xFFFF:
            return None
        return index, name, args[-2]["va"], "ctor"
    obj = None
    for row in reversed(pre):
        m = re.match(r"^push (e\w+)$", row["ins"])
        if m:
            obj = m.group(1)
            break
    if not obj:
        return None
    index = None
    store = None
    for row in reversed(pre):
        m = re.match(r"^mov DWORD PTR \[%s(?:\+0x0)?\],(0x[0-9a-f]+|e\w+)$" % obj, row["ins"])
        if not m:
            continue
        src = m.group(1)
        index = int(src, 16) if src.startswith("0x") else resolve_reg(pre, src, row["va"])
        store = row["va"]
        break
    name = None
    for row in reversed(pre):
        m = re.match(r"^push (0x[0-9a-f]+)$", row["ins"])
        if not m:
            continue
        cand = img.wide(int(m.group(1), 16))
        if cand:
            name = cand
            break
    if index is None or name is None:
        return None
    return index, name, store, "inline"


def seed_rows(img):
    """Every reserved name, sorted by index, numbered by the contiguous run it belongs to."""
    sites = img.calls_to(APPEND)
    if not sites:
        die("no call site of the append routine at 0x%x" % APPEND)
    if not img.append_routine_reads_gnames():
        die("0x%x does not index GNames (0x%x): the address is wrong" % (APPEND, GNAMES))
    out = []
    seen = {}
    for site in sites:
        got = read_site(img, site)
        if not got:
            continue
        index, name, store, shape = got
        if index in seen:
            die("index %d read twice: %s at 0x%x and %s at 0x%x"
                % (index, seen[index][0], seen[index][1], name, site))
        seen[index] = (name, site)
        out.append({"index": index, "name": name, "store": "0x%06x" % store, "append": "0x%06x" % site,
                    "shape": shape})
    out.sort(key=lambda r: r["index"])
    run = 0
    for i, row in enumerate(out):
        if i and row["index"] != out[i - 1]["index"] + 1:
            run += 1
        row["run"] = run
    return out


def runs_of(rows):
    """`[(first index, last index, count, first name, last name)]` over the sorted rows."""
    out = []
    for row in rows:
        if out and int(row["index"]) == out[-1][1] + 1 and int(row["run"]) == out[-1][5]:
            first, _last, n, fname, _lname, run = out[-1]
            out[-1] = (first, int(row["index"]), n + 1, fname, row["name"], run)
        else:
            out.append((int(row["index"]), int(row["index"]), 1, row["name"], row["name"], int(row["run"])))
    return out


def census(rows):
    runs = runs_of(rows)
    last = max(int(r["index"]) for r in rows)
    return {"names": len(rows), "runs": len(runs), "highest": last,
            "nulls": last + 1 - len(rows), "ctor": len([r for r in rows if r["shape"] == "ctor"])}


def doc_block(rows):
    c = census(rows)
    by_index = {r["index"]: r["name"] for r in rows}
    lines = []
    lines.append("%d names are reserved before the first package loads, in %d contiguous runs between index 0 and "
                 "%d, so %d of those slots stay null (`%d` of the entries are built by the constructor at `0x%x`, "
                 "the rest inline)."
                 % (c["names"], c["runs"], c["highest"], c["nulls"], c["ctor"], CTOR))
    lines.append("")
    lines.append("| run | indices | names | first | last |")
    lines.append("| ---: | --- | ---: | --- | --- |")
    for first, last, n, fname, lname, run in runs_of(rows):
        span = "%d" % first if first == last else "%d-%d" % (first, last)
        lines.append("| %d | %s | %d | `%s` | `%s` |" % (run + 1, span, n, fname, lname))
    lines.append("")
    lines.append("| index | name | what the state machine does with it | read at |")
    lines.append("| ---: | --- | --- | --- |")
    for index, want, what in SLOTS:
        row = next((r for r in rows if int(r["index"]) == index), None)
        if not row:
            die("slot %d is not in the seed" % index)
        if row["name"] != want:
            die("slot %d is `%s`, not `%s`" % (index, row["name"], want))
        lines.append("| %d | `%s` | %s | `%s` |" % (index, row["name"], what, row["store"]))
    return "\n".join(lines) + "\n"


def write_doc(path, rows):
    if not os.path.exists(path):
        die("%s: no such page" % path)
    text = open(path, encoding="utf-8").read()
    if MARK_BEGIN not in text or MARK_END not in text:
        die("%s: no %s / %s markers" % (path, MARK_BEGIN, MARK_END))
    head, rest = text.split(MARK_BEGIN, 1)
    _old, tail = rest.split(MARK_END, 1)
    block = doc_block(rows)
    open(path, "w", encoding="utf-8").write(head + MARK_BEGIN + "\n" + block + MARK_END + tail)
    return len(block)


def check_doc(path, rows):
    text = open(path, encoding="utf-8").read()
    if MARK_BEGIN not in text or MARK_END not in text:
        die("%s: no %s / %s markers" % (path, MARK_BEGIN, MARK_END))
    got = text.split(MARK_BEGIN, 1)[1].split(MARK_END, 1)[0]
    want = "\n" + doc_block(rows)
    if got != want:
        die("FAIL check-doc: %s's generated block is stale (regenerate it with --write-doc)" % path)


def check(rows):
    """The committed-CSV half: shape, ordering and the claims the page makes, with no dump and no writes."""
    problems = []
    if not rows:
        die("FAIL check: %s is empty" % CSV_PATH)
    indices = [int(r["index"]) for r in rows]
    if indices != sorted(indices):
        problems.append("rows are not sorted by index")
    if len(set(indices)) != len(indices):
        problems.append("an index appears twice")
    if any(not r["name"] for r in rows):
        problems.append("a row carries no name")
    if len({r["name"].lower() for r in rows}) != len(rows):
        problems.append("two rows carry the same name")
    by_index = {int(r["index"]): r["name"] for r in rows}
    for index, want, _what in SLOTS:
        if by_index.get(index) != want:
            problems.append("slot %d reads %r, expected %r" % (index, by_index.get(index), want))
    run_ids = sorted({int(r["run"]) for r in rows})
    if run_ids != list(range(len(run_ids))):
        problems.append("run numbers are not 0..n")
    for prev, row in zip(rows, rows[1:]):
        if int(row["run"]) == int(prev["run"]) and int(row["index"]) != int(prev["index"]) + 1:
            problems.append("run %s is not contiguous at index %s" % (row["run"], row["index"]))
        if int(row["run"]) != int(prev["run"]) and int(row["index"]) == int(prev["index"]) + 1:
            problems.append("index %s starts a new run but continues the last" % row["index"])
    if problems:
        die("FAIL check: %s: %s" % (CSV_PATH, "; ".join(problems[:6])))
    c = census(rows)
    print("gname-seed: %d rows, %d runs, highest index %d, %d null slots, %d by the constructor"
          % (c["names"], c["runs"], c["highest"], c["nulls"], c["ctor"]))


def verify(img, rows):
    """Re-read every committed row from the image: the index at its store, the name at its append call."""
    fresh = seed_rows(img)
    by_index = {r["index"]: r for r in fresh}
    bad = 0
    for row in rows:
        got = by_index.get(int(row["index"]))
        if not got or got["name"] != row["name"] or got["append"] != row["append"]:
            print("FAIL %s: committed %s at %s, image says %s"
                  % (row["index"], row["name"], row["append"],
                     "%s at %s" % (got["name"], got["append"]) if got else "nothing"))
            bad += 1
    extra = sorted(set(by_index) - {int(r["index"]) for r in rows})
    if extra:
        print("FAIL %d index(es) in the image are not committed: %s" % (len(extra), extra[:8]))
        bad += len(extra)
    if bad:
        die("%d row(s) disagree with the image" % bad)
    print("gname-seed: %d rows re-read from the image, 0 disagreements" % len(rows))


def read_csv(path):
    if not os.path.exists(path):
        return []
    return list(csv.DictReader(open(path, encoding="utf-8")))


def render(rows):
    import io
    fh = io.StringIO()
    w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore", lineterminator="\r\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in FIELDS})
    return fh.getvalue()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe", help="the dump's default.xbe")
    ap.add_argument("-o", "--out", help="write the CSV here")
    ap.add_argument("--write-doc", metavar="PATH", help="regenerate the page's block between its markers")
    ap.add_argument("--check-doc", metavar="PATH", help="fail if the page's block is stale (in `make ci`)")
    ap.add_argument("--from-csv", metavar="PATH", default=CSV_PATH, help="the committed rows (default: inventory)")
    ap.add_argument("--verify", action="store_true", help="re-read every committed row from the image")
    ap.add_argument("--report", action="store_true", help="print the census of the committed rows")
    ap.add_argument("--check", action="store_true", help="the committed-CSV gate; needs no dump, writes nothing")
    args = ap.parse_args()

    rows = read_csv(args.from_csv)
    if args.check:
        check(rows)
        return
    if args.report:
        c = census(rows)
        print("names %d, runs %d, highest %d, nulls %d" % (c["names"], c["runs"], c["highest"], c["nulls"]))
        for index, name, _what in SLOTS:
            print("  %4d %s" % (index, name))
        return
    if args.check_doc:
        check_doc(args.check_doc, rows)
        return
    if not args.xbe:
        die("--xbe is required unless --check/--report/--check-doc")
    img = Image(args.xbe)
    fresh = seed_rows(img)
    if args.verify:
        verify(img, rows or fresh)
        return
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="") as fh:
            fh.write(render(fresh))
    if args.write_doc:
        write_doc(args.write_doc, fresh)
    c = census(fresh)
    print("gname-seed: %d names in %d runs, %d null slots, highest index %d"
          % (c["names"], c["runs"], c["nulls"], c["highest"]))


if __name__ == "__main__":
    main()
