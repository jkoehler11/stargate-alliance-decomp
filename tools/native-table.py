#!/usr/bin/env python3
"""Every native body's address, read from the image's own registration table (docs/UNKNOWNS.md U1 and U5).

The binder (`0x0003a030`) resolves a `native` declaration that serialises no index by *name*: it formats
`int%sexec%s` and stores what a lookup returns. `tools/native-binder.py` proved the names ship in the image as one
wide-string pool, but a name is not an address. This tool finds the map those names are looked up in: the C++ side
registers each exec body in an 8-byte `{name, body}` record -- a pointer into the pool followed by a pointer into
`.text` -- and the records sit in per-class blocks in the initialised part of `.data`. There are **1,104** of them
over **203** blocks, covering **883** distinct names, so every name the pool carries has a body, and 769 of the 797
declarations in `inventory/natives.csv` (including 508 of the 523 index-less ones) now have an address with no boot.

    python3 tools/native-table.py DUMP/default.xbe -o inventory
    python3 tools/native-table.py DUMP/default.xbe           # summary only
    python3 tools/native-table.py --check                    # the committed CSV only; no dump

The cross-check that makes this a measurement rather than a pattern match: for every declaration that *does* carry
an index, `inventory/gnatives_slots.csv` holds the body the runtime actually installed in `GNatives` (16 KB dumped
over the xemu GDB stub at the main menu). The table written here must give the same address, and it does for every
one it names -- 261 of 261, zero disagreements. The same lookup is what gives U5 its two addresses:
`intAGameInfoexecSavePackage` is `0x298bb0` and `intAGameInfoexecLoadSaveGame` is `0x149370`.
"""
import argparse
import array
import bisect
import csv
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from xbe import Xbe  # noqa: E402

NATIVES = os.path.join(ROOT, "inventory", "natives.csv")
SLOTS = os.path.join(ROOT, "inventory", "gnatives_slots.csv")
FUNCTIONS = os.path.join(ROOT, "inventory", "function_modules.csv")
OUT = os.path.join(ROOT, "inventory", "native_targets.csv")

BINDER = re.compile(r"int([AU][A-Za-z0-9_]+?)exec([A-Za-z0-9_]+)")
WORD = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
BINDER_RE = re.compile(r"^int([AU])([A-Za-z0-9_]+)exec([A-Za-z0-9_]+)$")
FIELDS = ["block", "block_va", "record_va", "name", "class", "function", "body",
          "module", "declared", "token", "runtime_index", "runtime_address", "agrees"]


def wide_text(data):
    """The image as one character per 16-bit unit, so `text[i]` is the unit at file offset `2*i`.

    Decoding the file as UTF-16LE would be the obvious way and is wrong here: a surrogate pair collapses two units
    into one character, so every index after the first pair drifts -- the whole-file decode puts the pool's addresses
    0x3b9..0xb9a short by the middle of `.rdata`, which is why the name pool never reached a real xref. Lone
    surrogates are legal in a `str` and never match the binder pattern, so no replacement is needed.
    """
    units = array.array("H")
    units.frombytes(data[:len(data) // 2 * 2])
    return "".join(map(chr, units))


def binder_pool(x, data):
    """{binder name: VA of its first occurrence} for every name the image carries."""
    text = wide_text(data)
    out = {}
    for m in BINDER.finditer(text):
        if m.start() and text[m.start() - 1] in WORD:
            continue
        if m.end() < len(text) and text[m.end()] in WORD:
            continue
        name = m.group(0)
        if name in out:
            continue
        va = x.offset_to_va(m.start() * 2)
        if va:
            out[name] = va
    return out


def section(x, name):
    for s in x.sections:
        if s.name == name:
            return s
    raise SystemExit("native-table: no %s section" % name)


def body_range(x):
    s = section(x, ".text")
    return s.va, s.va + min(s.fsize, s.vsize)


def table_records(x, pool):
    """[{record_va, name, body}] for every `{pool pointer, text pointer}` record in initialised `.data`."""
    text_lo, text_hi = body_range(x)
    by_va = {va: name for name, va in pool.items()}
    data = section(x, ".data")
    blob = x.data[data.foff:data.foff + min(data.fsize, data.vsize)]
    out = []
    for off in range(0, len(blob) - 8, 4):
        name_va, body = struct.unpack("<II", blob[off:off + 8])
        name = by_va.get(name_va)
        if name and text_lo <= body < text_hi:
            out.append({"record_va": data.va + off, "name": name, "body": body})
    return out


def blocks(records):
    """Group records into the per-class blocks they were laid down in: consecutive 8-byte slots."""
    out = []
    cur = [records[0]]
    for r in records[1:]:
        if r["record_va"] == cur[-1]["record_va"] + 8:
            cur.append(r)
        else:
            out.append(cur)
            cur = [r]
    out.append(cur)
    return out


def read_csv(path):
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def join(records, blocks_):
    """Attach the declaration, the runtime slot and the module to each record."""
    decl = {(d["class"], d["function"]): d for d in read_csv(NATIVES)}
    slots = {(s.get("index") or "").strip(): s for s in read_csv(SLOTS)}
    funcs = {(f["address"] or "").lower(): f for f in read_csv(FUNCTIONS)}
    block_of = {}
    for i, b in enumerate(blocks_):
        for r in b:
            block_of[r["record_va"]] = (i, b[0]["record_va"])
    for r in records:
        m = BINDER_RE.match(r["name"])
        prefix, cls, fn = m.group(1), m.group(2), m.group(3)
        r["block"], r["block_va"] = block_of[r["record_va"]]
        r["block_va"] = "0x%08x" % r["block_va"]
        r["class"], r["function"] = cls, fn
        d = decl.get((cls, fn))
        r["declared"] = "1" if d else "0"
        r["token"] = (d or {}).get("native_token", "") if d else ""
        r["runtime_index"] = r["runtime_address"] = r["agrees"] = ""
        if d and r["token"] not in ("", "0", "0x0"):
            s = slots.get(r["token"]) or slots.get(str(int(r["token"])))
            if s and (s.get("address") or "").strip():
                r["runtime_index"] = r["token"]
                r["runtime_address"] = s["address"].strip()
                r["agrees"] = "yes" if int(s["address"], 16) == r["body"] else "no"
        f = funcs.get("0x%08x" % r["body"])
        r["module"] = (f or {}).get("module", "") if f else ""
        r["record_va"] = "0x%08x" % r["record_va"]
        r["body"] = "0x%08x" % r["body"]
        r["prefix_ok"] = "1" if prefix in ("A", "U") else "0"
    return records


def summary(records, pool, blocks_):
    names, counts = {}, {}
    for r in records:
        names.setdefault(r["name"], set()).add(r["body"])
        counts[r["name"]] = counts.get(r["name"], 0) + 1
    dup = [n for n in counts if counts[n] > 1]
    disagree = [n for n in names if len(names[n]) > 1]
    declared = [r for r in records if r["declared"] == "1"]
    checked = [r for r in records if r["agrees"]]
    entries = [r for r in records if r["module"]]
    decls = read_csv(NATIVES)
    covered = {(r["class"], r["function"]) for r in declared}
    less = [(d["class"], d["function"]) for d in decls if d.get("native_token") in ("", "0", "0x0")]
    tokened = [(d["class"], d["function"]) for d in decls if d.get("native_token") not in ("", "0", "0x0")]
    print("image: %d distinct binder names" % len(pool))
    print("table: %d records in %d blocks, %d distinct names, %d names with more than one record (%d conflict)"
          % (len(records), len(blocks_), len(names), len(dup), len(disagree)))
    print("blocks: %d single-record, largest %d" % (sum(1 for b in blocks_ if len(b) == 1),
                                                     max(len(b) for b in blocks_)))
    print("declarations: %d of %d have a body address (%d of %d without an index; %d of %d with one)"
          % (sum(1 for d in decls if (d["class"], d["function"]) in covered), len(decls),
             sum(1 for k in less if k in covered), len(less),
             sum(1 for k in tokened if k in covered), len(tokened)))
    print("runtime cross-check: %d records carry a runtime slot, %d agree, %d disagree"
          % (len(checked), sum(1 for r in checked if r["agrees"] == "yes"),
             sum(1 for r in checked if r["agrees"] == "no")))
    print("function entries: %d of %d bodies are entries in inventory/function_modules.csv"
          % (len(entries), len(records)))
    funcs = read_csv(FUNCTIONS)
    spans = sorted((int(f["address"], 16), int(f["address"], 16) + int(f["size"])) for f in funcs
                   if (f.get("address") or "").startswith("0x") and (f.get("size") or "").isdigit())
    starts = [lo for lo, _ in spans]
    interior, outside = 0, 0
    for r in records:
        if r["module"]:
            continue
        a = int(r["body"], 16)
        i = bisect.bisect_right(starts, a) - 1
        if i >= 0 and spans[i][0] <= a < spans[i][1]:
            interior += 1
        else:
            outside += 1
    print("  of the rest: %d fall inside a known function, %d are addresses the map has no function at"
          % (interior, outside))
    mods = {}
    for r in entries:
        mods[r["module"]] = mods.get(r["module"], 0) + 1
    print("  top modules: %s" % ", ".join("%s %d" % kv for kv in sorted(mods.items(), key=lambda kv: -kv[1])[:8]))
    return disagree


def write(records):
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS + ["prefix_ok"], extrasaction="ignore")
        w.writeheader()
        w.writerows(records)
    print("wrote %s" % os.path.relpath(OUT, ROOT))


def check():
    """No dump: the committed CSV must be internally consistent and agree with the runtime map it cites."""
    rows = read_csv(OUT)
    if not rows:
        print("native-table: --check needs inventory/native_targets.csv (run with the dump first)")
        return 2
    if list(rows[0].keys())[:len(FIELDS)] != FIELDS:
        print("native-table: FAIL columns are %s" % list(rows[0].keys()))
        return 1
    seen = set()
    for r in rows:
        m = BINDER_RE.match(r["name"])
        if not m:
            print("native-table: FAIL not a binder name: %r" % r["name"])
            return 1
        if (m.group(2), m.group(3)) != (r["class"], r["function"]):
            print("native-table: FAIL %s splits as %s/%s" % (r["name"], r["class"], r["function"]))
            return 1
        if int(r["body"], 16) < 0x11000 or int(r["body"], 16) >= 0x418100:
            print("native-table: FAIL body %s outside .text" % r["body"])
            return 1
        if int(r["record_va"], 16) % 4:
            print("native-table: FAIL record %s is not dword-aligned" % r["record_va"])
            return 1
        if (r["record_va"], r["name"]) in seen:
            print("native-table: FAIL duplicate record %s" % r["record_va"])
            return 1
        seen.add((r["record_va"], r["name"]))
        if r["agrees"] not in ("", "yes", "no"):
            print("native-table: FAIL agrees=%r" % r["agrees"])
            return 1
        if r["agrees"] == "yes" and int(r["runtime_address"], 16) != int(r["body"], 16):
            print("native-table: FAIL %s claims agreement but %s != %s"
                  % (r["name"], r["runtime_address"], r["body"]))
            return 1
        if r["agrees"] == "no":
            print("native-table: FAIL %s disagrees with its runtime slot" % r["name"])
            return 1
    print("ok native-targets: %d records, %d blocks, %d runtime agreements"
          % (len(rows), len({r["block"] for r in rows}), sum(1 for r in rows if r["agrees"] == "yes")))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xbe", nargs="?", help="the dump's default.xbe")
    ap.add_argument("--check", action="store_true", help="verify the committed CSV; needs no dump")
    ap.add_argument("-o", "--out", help="write the table CSV here")
    args = ap.parse_args()
    if args.check:
        return check()
    if not args.xbe:
        sys.exit("native-table: pass DUMP/default.xbe, or --check")
    x = Xbe(args.xbe)
    data = open(args.xbe, "rb").read()
    pool = binder_pool(x, data)
    records = table_records(x, pool)
    if not records:
        sys.exit("native-table: no registration records found")
    bs = blocks(records)
    records = join(records, bs)
    summary(records, pool, bs)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        write(records)
    return 0


if __name__ == "__main__":
    sys.exit(main())
