#!/usr/bin/env python3
"""Which table each `Core.Object` native body is installed into, and the array the installers read.

PLAN 4.3 left one question open after the dispatch table was closed: 102 registered `Object` records are stated
by no declaration, 69 of them by a `GNatives` slot, 13 by the compiler's operator records, and **33 by nothing at
all** -- the primitive conversions (`BoolToByte` ... `VectorToString`). This tool answers where those 33 go, and it
does it from the image's code rather than from a name.

Two things were unknown and both are structural:

* **The bodies are in a second list, not only the `{name, body}` records.** Immediately after `Core.Object`'s
  306-record block (`0x005c1a58`-`0x005c23e8`) sits a `0xffffffff` separator and then a **306-entry dword array at
  `0x005c2404`**, terminated by zeroed dwords. It is the same population from the other side: 303 of its entries are
  the block's bodies, 3 are stubs no record carries (`0x000404b0`, `0x000409a0`, `0x00040a30`) and the block carries
  one body the array does not (`0x00068890`). The array is in **declaration order**, which is why an entry's index is
  not its record's: the block skips the three stubs.
* **Every installer is one small thunk that reads its own entry out of that array.** The registration code is
  `mov ecx,<count>; mov edi,<table>; rep stosd` to fill a table with the default stub, then a per-entry thunk of the
  shape `cmp [table+4*slot],stub; je skip; mov reg,[array+4*index]; mov [table+4*slot],reg`. So the table, the slot
  and the array index are all readable statically, and the value is not an immediate at all -- which is why
  `gnatives-writes.py` reads those stores as "a pointer read from a data table".

Measured: **two** tables are filled this way. `GNatives` (`0x006313e8`, 4096 entries) takes **233** entries and the
second table at **`0x00630fe0`** (255 entries, the same stub fill) takes **33** -- and those 33 are exactly the
conversion records, at slots 57-90 (slot 70 the one the setter leaves a stub). **40 of the array's entries are
installed into neither**, so "no slot and no
table" is a measured property of a named set, not a residue.

    python3 tools/native-fill-tables.py --xbe DUMP/default.xbe -o inventory   # the artifact
    python3 tools/native-fill-tables.py --check                              # dump-free, in `make ci`
    python3 tools/native-fill-tables.py --report                             # the counts, from the artifact

`inventory/native_fill_tables.csv` is one row per array entry: the body, the record that also carries it, and the
table and slot it is installed into (empty when no thunk installs it). The check is dump-free and joins the artifact
against `native_targets.csv` (the records) and `gnatives_write_sites.csv` (the stores), so an array that moves or a
slot that changes hands fails `make ci` rather than the next document.
"""
import argparse
import csv
import importlib.util
import os
import re
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from xbe import Xbe  # noqa: E402

BLOCK_VA = 0x005C1A58          # `Core.Object`'s registration block, block 11 of `native_targets.csv`
BLOCK_RECORDS = 306
RECORD_STRIDE = 8              # `{pool name pointer, body}`
SEPARATOR = 0xFFFFFFFF
GNATIVES = 0x006313E8
DEFAULT_STUB = 0x000404A0
COLUMNS = ["index", "body", "record_index", "record_name", "table", "slot", "store_site"]
# The two fills the array feeds, with the extent each `rep stosd` states.
TABLES = {GNATIVES: 4096, 0x00630FE0: 255}
LINE = re.compile(r"^\s*([0-9a-f]+):\s+((?:[0-9a-f]{2} )+)\s*\t?(.*)$")


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def records(path):
    """`native_targets.csv`'s block-11 rows in record order: the record side of the join."""
    rows = [r for r in read_csv(path) if r.get("block") == "11"]
    rows.sort(key=lambda r: int(r["record_va"], 16))
    return rows


def find_array(image, block=BLOCK_VA, count=BLOCK_RECORDS):
    """The array after the record block, found by its separator rather than by a hardcoded offset.

    The block is `count` 8-byte records; the next `0xffffffff` dword after it is the separator the array follows,
    and the array runs while its entries land in `.text`. The extent is then compared with the record count, which
    is the property that makes it the same population.
    """
    end = block + count * RECORD_STRIDE
    for probe in range(end, end + 0x40, 4):
        raw = image.read_va(probe, 4)
        if len(raw) == 4 and struct.unpack("<I", raw)[0] == SEPARATOR:
            start = probe + 4
            break
    else:
        return None, [], 0
    text = [s for s in image.sections if s.name == ".text"][0]
    values = []
    while len(values) < count + 64:
        raw = image.read_va(start + 4 * len(values), 4)
        if len(raw) != 4:
            break
        va = struct.unpack("<I", raw)[0]
        if va == 0:            # the zeroed dwords that terminate the array
            break
        if not text.contains(va):
            break
        values.append(va)
    return start, values, len(values)


def disassemble(image, section_name=".text"):
    section = [s for s in image.sections if s.name == section_name][0]
    raw = image.data[section.foff:section.foff + section.fsize]
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as fh:
        fh.write(raw)
        path = fh.name
    try:
        text = subprocess.run(["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                               "--adjust-vma=0x%x" % section.va, path],
                              capture_output=True, text=True, check=True).stdout
    finally:
        os.unlink(path)
    out = []
    for line in text.splitlines():
        m = LINE.match(line)
        if m:
            out.append((int(m.group(1), 16), m.group(3).strip()))
    return out


def fills(insns):
    """`mov ecx,<n>; mov edi,<table>; rep stosd` -- the stub fill that names a table and its extent."""
    seen = {}
    for k in range(len(insns) - 3):
        site, ins = insns[k]
        m = re.match(r"mov\s+ecx,(0x[0-9a-f]+)$", ins)
        if not m:
            continue
        for j in range(k + 1, min(k + 5, len(insns))):
            m2 = re.match(r"mov\s+edi,(0x[0-9a-f]+)$", insns[j][1])
            if not m2:
                continue
            for h in range(j + 1, min(j + 4, len(insns))):
                if "rep stos" in insns[h][1]:
                    key = (int(m2.group(1), 16), int(m.group(1), 16))
                    seen.setdefault(key, site)
            break
    return {table: count for table, count in seen}


def installs(insns, array_va, entries):
    """Every store into a filled table whose value is an array entry: (table, slot, index, store site)."""
    out = []
    tables = fills(insns)
    for k, (site, ins) in enumerate(insns):
        if not ins.startswith("mov") or "," not in ins:
            continue
        dst = ins.split(None, 1)[1].split(",")[0]
        m = re.search(r"ds:0x([0-9a-f]+)", dst)
        if not m or "[" in dst:
            continue
        d = int(m.group(1), 16)
        for table, count in tables.items():
            if table <= d < table + 4 * count:
                break
        else:
            continue
        index = None
        for j in range(k - 1, max(-1, k - 9), -1):
            src = insns[j][1]
            if not src.startswith("mov") or "," not in src:
                continue
            a, b = src.split(None, 1)[1].split(",", 1)
            mb = re.search(r"ds:0x([0-9a-f]+)", b)
            if "ds:0x" not in a and mb:
                va = int(mb.group(1), 16)
                if array_va <= va < array_va + 4 * len(entries):
                    index = (va - array_va) // 4
                    break
        if index is not None:
            out.append((table, (d - table) // 4, index, site))
    return out, tables


def measure(xbe_path):
    image = Xbe(xbe_path)
    start, entries, count = find_array(image)
    if not entries:
        raise SystemExit("native-fill-tables: no %#x-separated array after the block at %#x"
                         % (SEPARATOR, BLOCK_VA))
    insns = disassemble(image)
    found, tables = installs(insns, start, entries)
    by_index = {}
    for table, slot, index, site in found:
        slot0 = by_index.get(index)
        if slot0 is None or site < slot0[2]:
            by_index[index] = (table, slot, site)
    recs = records(os.path.join(ROOT, "inventory", "native_targets.csv"))
    by_body = {}
    for i, r in enumerate(recs):
        by_body.setdefault(r["body"].lower(), i)
    rows = []
    for index, body in enumerate(entries):
        ri = by_body.get("0x%08x" % body)
        table, slot, site = by_index.get(index, ("", "", ""))
        rows.append({
            "index": index,
            "body": "0x%08x" % body,
            "record_index": ri if ri is not None else "",
            "record_name": recs[ri]["name"] if ri is not None else "",
            "table": "0x%08x" % table if table != "" else "",
            "slot": slot,
            "store_site": "0x%08x" % site if site != "" else "",
        })
    census = {
        "array": start, "entries": len(entries),
        "records": len(recs),
        "array_only": [r["body"] for r in rows if r["record_index"] == ""],
        "record_only": [r["body"] for r in recs if r["body"].lower()
                        not in {"0x%08x" % v for v in entries}],
        "installed": sum(1 for r in rows if r["table"]),
        "uninstalled": [r["index"] for r in rows if not r["table"]],
        "fill_loops": {k: v for k, v in tables.items()},
        "per_table": {},
    }
    for r in rows:
        if r["table"]:
            census["per_table"].setdefault(r["table"], set()).add(r["slot"])
    census["per_table"] = {k: len(v) for k, v in census["per_table"].items()}
    return rows, census


def write(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def render(rows, census):
    lines = [
        "array at 0x%08x: %d entries, %d of them also a record body" % (
            census["array"], census["entries"], census["entries"] - len(census["array_only"])),
        "  array only (%d): %s" % (len(census["array_only"]), " ".join(census["array_only"])),
        "  record only (%d): %s" % (len(census["record_only"]), " ".join(census["record_only"])),
        "installed by a thunk: %d, never installed: %d" % (census["installed"], len(census["uninstalled"])),
    ]
    for table, n in sorted(census["per_table"].items()):
        lines.append("  table %s: %d distinct slots" % (table, n))
    lines.append("  uninstalled indices: %s" % " ".join(str(i) for i in census["uninstalled"]))
    return "\n".join(lines)


def check(path):
    """Dump-free: the artifact against the two committed tables it joins, and its own counts."""
    fails = []
    rows = read_csv(path) if os.path.exists(path) else []
    if not rows:
        return ["%s is missing or empty" % path]
    if [r["index"] for r in rows] != [str(i) for i in range(len(rows))]:
        fails.append("index column is not 0..%d" % (len(rows) - 1))
    if len(rows) != BLOCK_RECORDS:
        fails.append("%d rows, the record block has %d records" % (len(rows), BLOCK_RECORDS))
    recs = records(os.path.join(ROOT, "inventory", "native_targets.csv"))
    by_body = {}
    for i, r in enumerate(recs):
        by_body.setdefault(r["body"].lower(), i)
    for r in rows:
        body = r["body"].lower()
        if not re.match(r"^0x[0-9a-f]{8}$", body):
            fails.append("row %s: body %r is not an address" % (r["index"], r["body"]))
            continue
        ri = by_body.get(body)
        if ri is None and r["record_index"]:
            fails.append("row %s: %s has no record in block 11 but names index %s"
                         % (r["index"], body, r["record_index"]))
        if ri is not None:
            if r["record_index"] != str(ri):
                fails.append("row %s: record_index %r, native_targets.csv puts %s at %d"
                             % (r["index"], r["record_index"], body, ri))
            elif r["record_name"] != recs[ri]["name"]:
                fails.append("row %s: record_name %r, native_targets.csv says %r"
                             % (r["index"], r["record_name"], recs[ri]["name"]))
        if r["table"]:
            if int(r["table"], 16) not in TABLES:
                fails.append("row %s: table %s is not one of the fills" % (r["index"], r["table"]))
            elif not r["slot"].isdigit() or int(r["slot"]) >= TABLES[int(r["table"], 16)]:
                fails.append("row %s: slot %r outside table %s" % (r["index"], r["slot"], r["table"]))
    # The store census is the second instrument: every slot this artifact credits to `GNatives` must be a slot
    # `gnatives_write_sites.csv` records a store at, with the same value.
    sites = {}
    for r in read_csv(os.path.join(ROOT, "inventory", "gnatives_write_sites.csv")):
        if r.get("slot"):
            sites[int(r["slot"])] = (r.get("value") or "").lower()
    gn = [r for r in rows if r["table"].lower() == "0x%08x" % GNATIVES]
    disagree = [r for r in gn if r["slot"].isdigit() and
                sites.get(int(r["slot"])) not in (None, r["body"].lower())]
    if disagree:
        fails.append("FAIL native-fill-tables: %d slot(s) whose store site holds a different value, e.g. %s"
                     % (len(disagree), disagree[0]["slot"]))
    if len(gn) != len({r["slot"] for r in gn}):
        fails.append("two array entries credited to the same GNatives slot")
    counts = {}
    for r in rows:
        counts[r["table"] or "none"] = counts.get(r["table"] or "none", 0) + 1
    if counts.get("0x%08x" % GNATIVES, 0) != 233:
        fails.append("GNatives takes %d entries, the measurement is 233"
                     % counts.get("0x%08x" % GNATIVES, 0))
    if counts.get("0x00630fe0", 0) != 33:
        fails.append("the 0x630fe0 table takes %d entries, the measurement is 33"
                     % counts.get("0x00630fe0", 0))
    if counts.get("none", 0) != 40:
        fails.append("%d entries are installed nowhere, the measurement is 40" % counts.get("none", 0))
    return fails


def selftest():
    """Drive the parsers and the join on synthetic inputs in a temp dir -- no dump, no writes to the tree."""
    fails = []
    insns = [(0x1000, "mov    ecx,0x1000"), (0x1005, "mov    edi,0x6000"),
             (0x100a, "rep stos DWORD PTR es:[edi],eax"),
             (0x1010, "cmp    DWORD PTR ds:0x6000,eax"), (0x1016, "je     0x1030"),
             (0x1018, "mov    eax,ds:0x8004"), (0x101d, "mov    DWORD PTR ds:0x6008,eax"),
             (0x1023, "ret")]
    got = fills(insns)
    if got != {0x6000: 0x1000}:
        fails.append("fills() read %r" % (got,))
    found, tables = installs(insns, 0x8000, [0xAA, 0xBB, 0xCC])
    if found != [(0x6000, 2, 1, 0x101d)]:
        fails.append("installs() read %r" % (found,))
    tmp = tempfile.mkdtemp()
    try:
        rows = [{"index": 0, "body": "0x00040000", "record_index": "", "record_name": "",
                 "table": "", "slot": "", "store_site": ""},
                {"index": 1, "body": "0x00040004", "record_index": "0", "record_name": "intX",
                 "table": "0x00630fe0", "slot": "59", "store_site": "0x1000"}]
        path = os.path.join(tmp, "a.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        # The count assertions must fire on this synthetic table: 2 rows is not 306.
        if not any("rows" in f for f in check(path)):
            fails.append("check() accepted a two-row artifact")
    finally:
        for name in os.listdir(tmp):
            os.unlink(os.path.join(tmp, name))
        os.rmdir(tmp)
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xbe", default="")
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    path = os.path.join(args.out, "native_fill_tables.csv")
    if args.selftest:
        fails = selftest()
        for f in fails:
            print("FAIL native-fill-tables selftest: %s" % f)
        return 1 if fails else 0
    if args.check:
        fails = selftest()
        fails += check(os.path.join(ROOT, "inventory", "native_fill_tables.csv"))
        for f in fails:
            print("FAIL native-fill-tables: %s" % f)
        if not fails:
            rows = read_csv(os.path.join(ROOT, "inventory", "native_fill_tables.csv"))
            counts = {}
            for r in rows:
                counts[r["table"] or "none"] = counts.get(r["table"] or "none", 0) + 1
            print("native-fill-tables: %d entries, %d installed (%s)" % (
                len(rows), sum(n for k, n in counts.items() if k != "none"),
                ", ".join("%s %d" % (k, n) for k, n in sorted(counts.items()))))
        return 1 if fails else 0
    if args.report:
        rows = read_csv(os.path.join(ROOT, "inventory", "native_fill_tables.csv"))
        census = {"array": 0, "entries": len(rows),
                  "array_only": [r["body"] for r in rows if not r["record_index"]],
                  "record_only": [], "installed": sum(1 for r in rows if r["table"]),
                  "uninstalled": [int(r["index"]) for r in rows if not r["table"]],
                  "per_table": {}}
        for r in rows:
            if r["table"]:
                census["per_table"][r["table"]] = census["per_table"].get(r["table"], 0) + 1
        print(render(rows, census))
        return 0
    if not args.xbe:
        print("native-fill-tables: --xbe is required to measure (or --check / --report / --selftest)")
        return 2
    os.makedirs(args.out, exist_ok=True)
    rows, census = measure(args.xbe)
    write(path, rows)
    print(render(rows, census))
    print("wrote %s (%d rows)" % (path, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
