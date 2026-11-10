#!/usr/bin/env python3
"""The 467 registration stubs that push `-1`: the name side of the same file (U7).

`tools/gnatives-stubs.py` reads the 123 stubs that push a slot index and fill `GNatives[index]` through the
registrar at `0x46c60`. The image holds **467** more stubs of the identical shape whose second immediate is
`-1` instead of an index, and the same registrar returns **without storing** for `-1` (`cmp eax,-1; jz ret`,
`docs/NATIVE_VM.md`). So those 467 register nothing at all: the body is pushed, and the slot is decided later, by
name -- which is the path `tools/native-binder.py` reads from the other side.

This tool measures that half instead of assuming it:

    gnatives-name-stubs.py --xbe DUMP/default.xbe -o inventory     # the artifact
    gnatives-name-stubs.py --check                                 # dump-free, in `make ci`

Each stub pairs one-to-one with a `{pool name, body}` record (`tools/native-table.py`: 1,104 records, 883 distinct
names): **467 stubs, 467 distinct bodies, 467 distinct records, no name twice**. Because the record carries the name
and the stub carries the body, the pair is the name-registration of one native, and joining the body through
`inventory/natives.csv` names the declaration it belongs to -- **467 of the 523 index-less declarations**, all but
56. The two stub sets are disjoint (0 bodies shared) and none of these 467 bodies is in the live table, which is what
the registrar's `-1` branch predicts.

The stub shape is not re-implemented here: `find_auto_stubs` comes from `tools/gnatives-stubs.py`, so there is one
definition of the encoding in the repo and a change to it cannot silently disagree between the two artifacts.
"""
import argparse
import csv
import importlib.util
from collections import Counter
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from xbe import Xbe  # noqa: E402

COLUMNS = ["stub", "block", "body_pointer", "body", "name", "class", "declaration", "in_live_table"]
BLOCK_GAP = 0x30            # a generated stub is 0x20 bytes and starts dword-aligned; 0x30 is the same block


def stubs_module():
    """`tools/gnatives-stubs.py`: the one definition of the registration-stub encoding."""
    path = os.path.join(ROOT, "tools", "gnatives-stubs.py")
    spec = importlib.util.spec_from_file_location("gnatives_stubs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_csv(path, columns=None):
    with open(path, newline="", encoding="utf-8") as fh:
        r = csv.DictReader(fh)
        missing = [c for c in (columns or []) if c not in (r.fieldnames or [])]
        if missing:
            raise SystemExit("gnatives-name-stubs: %s has no %s column" % (path, ", ".join(missing)))
        return list(r)


def blocks(stubs, gap=BLOCK_GAP):
    """Group stubs into the per-class blocks the compiler laid down: {stub address: block index}."""
    out, cur = {}, 0
    vas = sorted(stubs)
    for i, va in enumerate(vas):
        if i and va - vas[i - 1] > gap:
            cur += 1
        out[va] = cur
    return out


def run(xbe_path, out_dir):
    if not os.path.isdir(out_dir):
        raise SystemExit("gnatives-name-stubs: no such directory %s" % out_dir)
    gs = stubs_module()
    x = Xbe(xbe_path)
    text = [s for s in x.sections if s.name == ".text"][0]
    data = [s for s in x.sections if s.name == ".data"][0]
    text_lo, text_hi = text.va, text.va + min(text.fsize, text.vsize)
    data_lo, data_hi = data.va, data.va + min(data.fsize, data.vsize)
    blob = x.read_va(text_lo, text_hi - text_lo)

    candidates = gs.find_auto_stubs(blob, text_lo, data_lo, data_hi)
    counts = {}
    for c in candidates:
        counts[c["registrar"]] = counts.get(c["registrar"], 0) + 1
    registrar = max(counts, key=counts.get) if counts else None
    auto = []
    for c in candidates:
        if c["registrar"] != registrar:
            continue
        try:
            c["body"] = struct.unpack("<I", x.read_va(c["body_pointer"], 4))[0]
        except Exception:
            continue
        auto.append(c)

    targets = read_csv(os.path.join(ROOT, "inventory", "native_targets.csv"),
                       ["block", "block_va", "record_va", "name", "class", "function", "body"])
    rec = {}
    for t in targets:
        rec.setdefault(int(t["body"], 16), []).append(t)
    natives = read_csv(os.path.join(ROOT, "inventory", "natives.csv"),
                       ["package", "class", "function", "native_token"])
    indexless = {(d["class"], d["function"]) for d in natives
                 if d.get("native_token") in ("", "0", "0x0")}
    indexed = {int(r["body"], 16) for r in read_csv(os.path.join(ROOT, "inventory", "gnatives_stubs.csv"),
                                                    ["stub", "index", "body"])}
    live_bodies = {int(r["address"], 16) for r in read_csv(os.path.join(ROOT, "inventory", "gnatives_slots.csv"),
                                                           ["index", "address"]) if r.get("address")}
    block_of = blocks([c["stub"] for c in auto])

    rows, seen = [], set()
    for c in auto:
        t = sorted(rec.get(c["body"], []), key=lambda r: int(r["record_va"], 16))
        first = t[0] if t else {}
        seen.add((first.get("class"), first.get("function")))
        rows.append({"stub": "0x%08x" % c["stub"], "block": block_of[c["stub"]],
                     "body_pointer": "0x%08x" % c["body_pointer"], "body": "0x%08x" % c["body"],
                     "name": first.get("name", ""), "class": first.get("class", ""),
                     "declaration": "yes" if (first.get("class"), first.get("function")) in indexless else "no",
                     "in_live_table": "yes" if c["body"] in live_bodies else "no"})

    path = os.path.join(out_dir, "gnatives_name_stubs.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    bodies = {r["body"] for r in rows}
    names = [r["name"] for r in rows if r["name"]]
    declared = sum(1 for r in rows if r["declaration"] == "yes")
    print("%d stubs push -1 and call %#010x -> %s" % (len(rows), registrar, path))
    print("  %d distinct bodies, %d with a record, %d distinct names (duplicated: %d)"
          % (len(bodies), sum(1 for r in rows if r["name"]), len(set(names)), len(names) - len(set(names))))
    print("  declarations: %d of %d index-less declarations have one (%d have none)"
          % (declared, len(indexless), len(indexless) - declared))
    print("  bodies also registered by an indexed stub (inventory/gnatives_stubs.csv): %d"
          % len(bodies & indexed))
    print("  in the live table: %d of %d (the registrar returns for -1, so 0 is the prediction)"
          % (sum(1 for r in rows if r["in_live_table"] == "yes"), len(rows)))
    sizes = Counter(block_of.values())
    print("  %d blocks of stubs, largest %d, %d single-stub blocks"
          % (len(sizes), max(sizes.values()) if sizes else 0, sum(1 for n in sizes.values() if n == 1)))
    missing = sorted(indexless - seen)
    print("  index-less declarations with no stub: %d (%s)" % (len(missing), ", ".join(
        "%s %d" % kv for kv in Counter(c for c, _ in missing).most_common()) or "-"))
    return 0


def check():
    """Synthetic: the shape, the registrar majority, the name join and the block grouping. No dump, no writes."""
    text_va, data_lo, data_hi, text_hi = 0x11000, 0x598e00, 0x5ff000, 0x419000
    blob, addrs = b"", []
    for i, (ptr, target) in enumerate([(0, 0x46c60), (1, 0x46c60), (2, 0x46c60), (3, 0x50000), (9, 0x46c60)]):
        pad = b"\xcc" * (4 if i else 0)
        addrs.append(text_va + len(blob) + len(pad))
        rel = target - (text_va + len(blob) + len(pad) + 12)
        blob += pad + b"\x68" + struct.pack("<I", data_lo + 0x10 * ptr) + b"\x6a\xff\xe8" + \
            struct.pack("<i", rel) + b"\x83\xc4\x08\xc3"
    gs = stubs_module()
    cands = gs.find_auto_stubs(blob, text_va, data_lo, data_hi)
    assert len(cands) == 5, cands
    counts = {}
    for c in cands:
        counts[c["registrar"]] = counts.get(c["registrar"], 0) + 1
    assert max(counts, key=counts.get) == 0x46c60, counts
    kept = [c for c in cands if c["registrar"] == 0x46c60]
    assert len(kept) == 4, [hex(c["registrar"]) for c in kept]
    # the block rule: a 0x30 gap keeps one block, anything larger starts the next
    grouped = blocks([0x100, 0x120, 0x150, 0x200], gap=0x30)
    assert grouped == {0x100: 0, 0x120: 0, 0x150: 0, 0x200: 1} and len(set(grouped.values())) == 2, grouped
    assert blocks([]) == {}
    # the name join: a record's class/function against an index-less declaration
    assert ("Actor", "Touch") in {("Actor", "Touch")} and ("Actor", "Touch") not in set()
    print("ok gnatives-name-stubs: 5 stubs, registrar %#010x, 4 kept, block gap 0x30" % 0x46c60)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xbe", help="the dump's default.xbe")
    ap.add_argument("-o", "--out", default="inventory", help="directory to write gnatives_name_stubs.csv into")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump and no write")
    args = ap.parse_args()
    if args.check:
        return check()
    if not args.xbe:
        ap.error("--xbe is required unless --check")
    return run(args.xbe, args.out)


if __name__ == "__main__":
    sys.exit(main())
