#!/usr/bin/env python3
"""The 123 `GNatives` slots no store instruction names are registered by a per-entry stub (U7).

`tools/gnatives-writes.py` reads slots written by an absolute store (`C7 05` / `89 <modrm>`, **233** of the 356
live slots) and `tools/gnatives-records.py` explains the storeless ones through `{pool name, body}` records in
`.data` (**113** of the remaining 123). Both leave a tail: the question was never answered for *how* a slot gets
filled when neither shape applies, and `docs/UNKNOWNS.md`'s U7 carried the 10 slots that neither names.

The image has a third shape and it is the whole of the storeless set, not a residue: a **per-entry registration
stub** in `.text` --

    68 <&body in .data>      push offset <the dword that carries the body>
    68 <index>               push <the slot index, as a literal>
    E8 <rel32>               call <the registrar at 0x46c60>
    83 C4 08                 add  esp, 8
    A2 <flag>                mov  byte [flag], al

The write happens **inside the registrar**, reached through a pushed index -- so a scan for `mov [abs], imm` cannot
see it, and the index is not lost: it is the second `push`. There are **123** of these stubs, every one of them
names a different live slot, **123 of 123 agree** with the value the guest has in the table, and their slot set is
**disjoint** from the 233 store sites: 233 + 123 = **356**, every live slot.

    gnatives-stubs.py --xbe DUMP/default.xbe -o inventory     # the artifact
    gnatives-stubs.py --check                                 # dump-free, in `make ci`

The registrar the stubs call is `0x46c60`, and it is four instructions of policy, read here rather than assumed:
it fills all 4,096 slots with the sentinel `0x000404a0` once (`[0x635560]` guards it, `rep stosd`), returns **without
storing** for an index of `-1`, and otherwise does `mov [eax*4+0x6313e8], edx` with `edx = [&body]`. That is the
fill the store scan cannot see. The image also holds **467** stubs of the same shape that push `-1` instead of an
index; their bodies are 467 distinct `.text` addresses, **none** of which is a live slot value -- the registrar
returns for `-1`, so those natives reach the table by the name path (`tools/native-binder.py`), not by this call.

A stub is recognised syntactically (`push imm`, `push imm`, `call rel32` with the first immediate inside `.data`)
and then held to three semantic rules: the pushed index must be a plausible slot number, the pointed-at dword must
be a `.text` address, and the call target must be the registrar the majority of the candidates reach. The four
candidates the image also holds that fail those rules are printed as rejects rather than silently dropped.
"""
import argparse
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from xbe import Xbe  # noqa: E402

COLUMNS = ["stub", "index", "body_pointer", "body", "live_address", "name", "agrees", "registrar"]
INDEX_MAX = 0x1000          # 3,971 is the highest live slot; anything near a VA is not an index
STUB_BYTES = 15             # 5 + 5 + 5: push imm32, push imm32, call rel32


def find_stubs(blob, text_va, data_lo, data_hi):
    """Every `push imm32(->.data); push imm32(< INDEX_MAX); call rel32` in `blob`, in address order.

    Purely syntactic: the three semantic filters (plausible index, `.text` body, the majority registrar) run in
    `classify`, so a candidate the build does not actually use is reported instead of vanishing.
    """
    out = []
    for i in range(0, len(blob) - STUB_BYTES + 1):
        if blob[i] != 0x68:
            continue
        ptr = struct.unpack_from("<I", blob, i + 1)[0]
        if not (data_lo <= ptr < data_hi):
            continue
        if blob[i + 5] != 0x68:
            continue
        index = struct.unpack_from("<I", blob, i + 6)[0]
        if blob[i + 10] != 0xE8:
            continue
        rel = struct.unpack_from("<i", blob, i + 11)[0]
        out.append({"stub": text_va + i, "body_pointer": ptr, "index": index,
                    "registrar": text_va + i + STUB_BYTES + rel})
    return out


def find_auto_stubs(blob, text_va, data_lo, data_hi):
    """The same shape with `6a ff` (`push -1`) where the index goes: registrations the registrar discards."""
    out = []
    for i in range(0, len(blob) - 12 + 1):
        if blob[i] != 0x68:
            continue
        ptr = struct.unpack_from("<I", blob, i + 1)[0]
        if not (data_lo <= ptr < data_hi):
            continue
        if blob[i + 5:i + 7] != b"\x6a\xff" or blob[i + 7] != 0xE8:
            continue
        rel = struct.unpack_from("<i", blob, i + 8)[0]
        out.append({"stub": text_va + i, "body_pointer": ptr, "registrar": text_va + i + 12 + rel})
    return out


def classify(candidates, read_dword, text_lo, text_hi):
    """Attach each candidate's body; return (stubs, rejects, registrar).

    `read_dword` is a callable VA -> int, so `--check` can drive this with a dict instead of an image.
    """
    bodies = {}
    for c in candidates:
        try:
            bodies[c["stub"]] = read_dword(c["body_pointer"])
        except Exception:
            bodies[c["stub"]] = None
    plausible = [c for c in candidates
                 if c["index"] < INDEX_MAX and bodies[c["stub"]] is not None
                 and text_lo <= bodies[c["stub"]] < text_hi]
    counts = {}
    for c in plausible:
        counts[c["registrar"]] = counts.get(c["registrar"], 0) + 1
    registrar = max(counts, key=counts.get) if counts else None
    stubs, rejects = [], []
    for c in candidates:
        body = bodies[c["stub"]]
        why = None
        if c["index"] >= INDEX_MAX:
            why = "index %d is not a slot number" % c["index"]
        elif body is None:
            why = "body pointer is unreadable"
        elif not (text_lo <= body < text_hi):
            why = "body %#010x is not in .text" % body
        elif c["registrar"] != registrar:
            why = "calls %#010x, not the registrar %#010x" % (c["registrar"], registrar)
        c = dict(c, body=body)
        (rejects if why else stubs).append((c, why) if why else c)
    return stubs, rejects, registrar


def read_csv(path, columns):
    with open(path, newline="", encoding="utf-8") as fh:
        r = csv.DictReader(fh)
        missing = [c for c in columns if c not in (r.fieldnames or [])]
        if missing:
            raise SystemExit("gnatives-stubs: %s has no %s column" % (path, ", ".join(missing)))
        return list(r)


def run(xbe_path, out_dir):
    if not os.path.isdir(out_dir):
        raise SystemExit("gnatives-stubs: no such directory %s" % out_dir)
    x = Xbe(xbe_path)
    text = [s for s in x.sections if s.name == ".text"][0]
    data = [s for s in x.sections if s.name == ".data"][0]
    text_lo, text_hi = text.va, text.va + min(text.fsize, text.vsize)
    data_lo, data_hi = data.va, data.va + min(data.fsize, data.vsize)
    blob = x.read_va(text_lo, text_hi - text_lo)

    candidates = find_stubs(blob, text_lo, data_lo, data_hi)
    stubs, rejects, registrar = classify(candidates, lambda va: struct.unpack("<I", x.read_va(va, 4))[0],
                                         text_lo, text_hi)

    auto = [a for a in find_auto_stubs(blob, text_lo, data_lo, data_hi) if a["registrar"] == registrar]
    auto_bodies = []
    for a in auto:
        try:
            auto_bodies.append(struct.unpack("<I", x.read_va(a["body_pointer"], 4))[0])
        except Exception:
            pass

    live = read_csv(os.path.join(ROOT, "inventory", "gnatives_slots.csv"),
                    ["index", "address", "name", "evidence", "declarers"])
    records = read_csv(os.path.join(ROOT, "inventory", "gnatives_records.csv"),
                       ["slot", "live_address", "name", "store_site", "record_va", "record_name", "source"])
    by_slot = {int(r["slot"]): r for r in records}
    stored = {int(r["slot"]) for r in records if r["store_site"] == "yes"}

    rows = []
    for s in stubs:
        rec = by_slot.get(s["index"]) or {}
        addr = rec.get("live_address", "")
        ok = "yes" if addr and int(addr, 16) == s["body"] else "no"
        rows.append({"stub": "0x%08x" % s["stub"], "index": s["index"],
                     "body_pointer": "0x%08x" % s["body_pointer"], "body": "0x%08x" % s["body"],
                     "live_address": addr, "name": rec.get("name", ""), "agrees": ok,
                     "registrar": "0x%08x" % s["registrar"]})

    path = os.path.join(out_dir, "gnatives_stubs.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    agree = sum(1 for r in rows if r["agrees"] == "yes")
    covered = {r["index"] for r in rows if r["agrees"] == "yes"}
    live_n = len(live)
    print("%d candidate stubs, %d reject(s); the registrar is %#010x" % (len(candidates), len(rejects), registrar))
    for c, why in rejects:
        print("  REJECT %#010x index=%d %s" % (c["stub"], c["index"], why))
    print("%d stubs -> %s: %d agree with the live table, %d disagree"
          % (len(rows), path, agree, len(rows) - agree))
    print("  slots named by a stub: %d, of which a store instruction also names %d"
          % (len(covered), len(covered & stored)))
    print("  store sites %d + stubs %d = %d of %d live slots (%.1f%%)"
          % (len(stored), len(covered - stored), len(stored) + len(covered - stored), live_n,
             100.0 * (len(stored) + len(covered - stored)) / live_n if live_n else 0.0))
    uncovered = [int(r["slot"]) for r in records if r["slot"] and int(r["slot"]) not in stored | covered]
    print("  live slots by neither instrument: %d%s"
          % (len(uncovered), (" (" + " ".join(map(str, uncovered[:20])) + ")") if uncovered else ""))
    live_values = {int(r["live_address"], 16) for r in records if r["live_address"]}
    print("  %d stubs push -1 (the registrar returns without storing); %d of their %d bodies are a live slot value"
          % (len(auto), sum(1 for b in auto_bodies if b in live_values), len(auto_bodies)))
    tail = [int(r["slot"]) for r in records if r["source"] == "none"]
    print("  the %d slots `gnatives-records` left unnamed: %s" % (len(tail), ", ".join(
        "%d(%s)" % (s, "stub %s" % next((r["stub"] for r in rows if r["index"] == s and r["agrees"] == "yes"),
                                       "missing")) for s in tail)))
    return 0


def check():
    """Synthetic: the shape, the three semantic rules, and the agreement flag. No dump, no writes."""
    text_va, data_lo, data_hi, text_hi = 0x11000, 0x598e00, 0x5ff000, 0x419000
    blob = b""

    def stub(ptr, index, target, pad=0):
        rel = target - (text_va + len(blob) + STUB_BYTES + pad)
        return b"\xcc" * pad + b"\x68" + struct.pack("<I", ptr) + b"\x68" + struct.pack("<I", index) + \
            b"\xe8" + struct.pack("<i", rel) + b"\x83\xc4\x08\xa2\x00\x00\x00\x00\xc3"

    good = [(data_lo + 0x10, 7), (data_lo + 0x14, 8), (data_lo + 0x18, 9)]
    blob += stub(data_lo + 0x04, 5, 0x46c60)             # not followed by a stub push: not a candidate
    blob += stub(good[0][0], good[0][1], 0x46c60)
    blob += stub(good[1][0], good[1][1], 0x46c60)
    blob += stub(good[2][0], good[2][1], 0x46c60)
    bad_index = len(blob)
    blob += stub(data_lo + 0x1c, 0x6c2c50, 0x46c60)      # a VA in the index slot, not a slot number
    bad_body = len(blob)
    blob += stub(data_lo + 0x20, 10, 0x46c60)            # body is not in .text
    other = len(blob)
    blob += stub(data_lo + 0x24, 11, 0x50000)            # a real body, but not the majority registrar

    auto_at = len(blob)
    blob += b"\x68" + struct.pack("<I", data_lo + 0x28) + b"\x6a\xff\xe8" + \
        struct.pack("<i", 0x46c60 - (text_va + len(blob) + 12)) + b"\x83\xc4\x08\xc3"

    memory = {data_lo + 0x10: 0x12000, data_lo + 0x14: 0x12100, data_lo + 0x18: 0x12200,
              data_lo + 0x1c: 0x12300, data_lo + 0x20: 0x1000, data_lo + 0x24: 0x12400,
              data_lo + 0x04: 0x12500, data_lo + 0x28: 0x12600}
    cands = find_stubs(blob, text_va, data_lo, data_hi)
    assert len(cands) == 7, [hex(c["stub"]) for c in cands]
    assert [c["index"] for c in cands][:5] == [5, 7, 8, 9, 0x6c2c50], cands
    stubs, rejects, registrar = classify(cands, memory.__getitem__, text_va, text_hi)
    assert registrar == 0x46c60, hex(registrar)
    assert [s["index"] for s in stubs] == [5, 7, 8, 9], [s["index"] for s in stubs]
    assert [c["stub"] for c, _ in rejects] == [text_va + bad_index, text_va + bad_body, text_va + other], rejects
    assert "not a slot number" in rejects[0][1] and "not in .text" in rejects[1][1], rejects
    assert "not the registrar" in rejects[2][1], rejects[2]
    autos = find_auto_stubs(blob, text_va, data_lo, data_hi)
    assert len(autos) == 1 and autos[0]["stub"] == text_va + auto_at and autos[0]["registrar"] == 0x46c60, autos
    # a candidate whose dword pointer is inside .data but whose body cannot be read is rejected, not fatal
    unreadable = classify([{"stub": 0x11000, "body_pointer": data_lo + 0x30, "index": 1, "registrar": 0x46c60}],
                          lambda va: (_ for _ in ()).throw(KeyError(va)), text_va, text_hi)[1][0][1]
    assert unreadable == "body pointer is unreadable", unreadable
    print("ok gnatives-stubs: 7 candidates, 4 stubs, 3 rejects, registrar %#010x" % registrar)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xbe", help="the dump's default.xbe")
    ap.add_argument("-o", "--out", default="inventory", help="directory to write gnatives_stubs.csv into")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump and no write")
    args = ap.parse_args()
    if args.check:
        return check()
    if not args.xbe:
        ap.error("--xbe is required unless --check")
    return run(args.xbe, args.out)


if __name__ == "__main__":
    sys.exit(main())
