#!/usr/bin/env python3
"""What the two `Core` stat initializers do: register counters, or increment them?

The audio module's eight counters are registered at device creation by
`XboxAudio_RegisterCounters` (`0x0002e900`): it builds two `FString`s and calls `0x002530f0` with the
engine's counter block in `ecx`, storing the returned handle in a field of the audio subsystem. The
last audio bundle read the two `Core` bodies that also reference that block (`0x00253950`, 10,539 B and
`0x002b0770`, 4,294 B) as "the increment". This tool reads them for what they are -- a straight-line
sequence of the same registration call, one per stat, with the group and stat name pushed as wide
literals -- so the claim can be checked rather than inherited:

    body            bytes   registrations  groups
    0x00253950      10539   145            Frame, Render, ...
    0x002b0770       4294   ...

An *increment* would be arithmetic on a handle (`inc [reg+disp]`, `add [reg+disp],v`). The census
records every `add`/`inc`/`sub`/`mov [r+d],reg` that lands on a stat entry inside each body, and the
count is in the body row's `note`, so "these bodies register" is a measurement and not a reading.

    python3 tools/stat-registry.py --xbe DUMP/default.xbe -o inventory/stat_registry.csv
    python3 tools/stat-registry.py --check      # no dump: the artifact against the map and itself
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
sys.path.insert(0, os.path.join(ROOT, "tools"))

MAP = os.path.join(ROOT, "symbols", "xbox_functions.csv")
OUT = os.path.join(ROOT, "inventory", "stat_registry.csv")

BLOCK = 0x007b5dd8          # the engine's counter block (`ecx` at registration)
REGISTER = 0x002530f0       # the method that appends a stat and returns its handle
BODIES = [
    (0x00253950, 10539),
    (0x002b0770, 4294),
    (0x0002e900, 691),      # XboxAudio_RegisterCounters, the control
]
CODE_SECTIONS = (".text", "XNET", "XONLINE", "D3D", "D3DX", "XGRPH", "DSOUND", "XPP")
DATA_SECTIONS = (".rdata", ".data", "DOLBY", ".data1")

HEADER = ["kind", "body", "body_name", "site", "seq", "group", "name", "store", "note"]


def read_map():
    rows = []
    with open(MAP, newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append((int(row["address"], 16), row["name"], int(row["size"] or 0), row["module"].strip()))
    return sorted(rows)


def objdump(blob, base):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
        fh.write(blob)
        temp = fh.name
    try:
        out = subprocess.run(["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                              "--adjust-vma=%d" % base, temp],
                             capture_output=True, text=True).stdout
    finally:
        os.unlink(temp)
    insns = []
    for line in out.splitlines():
        if line.count("\t") < 2:
            continue
        head, rest = line.split("\t", 1)
        try:
            va = int(head.strip().rstrip(":"), 16)
        except ValueError:
            continue
        text = re.sub(r"\s+", " ", rest.split("\t")[-1].strip())
        if text and not text.startswith("."):
            insns.append((va, text))
    insns.sort()
    return insns


def build(args):
    from xbe import Xbe  # noqa: PLC0415

    xbe = Xbe(args.xbe)
    table = read_map()
    by_va = {va: name for va, name, _s, _m in table}

    def body_named(va):
        for start, name, size, _m in table:
            if start <= va < start + size:
                return name
        return "FUN_%08x" % va

    def wide(va, n=96):
        try:
            b = xbe.read_va(va, n)
        except (KeyError, IndexError):
            return None
        out = []
        for i in range(0, len(b) - 1, 2):
            c = b[i] | (b[i + 1] << 8)
            if c == 0:
                break
            if not (32 <= c < 127):
                return None
            out.append(chr(c))
        return "".join(out) if len(out) >= 2 else None

    rows = []
    for va, size in BODIES:
        insns = objdump(xbe.read_va(va, size), va)
        lits = []            # (site, text) of each wide-literal push, in program order
        calls = []           # (site, index into insns)
        for i, (a, t) in enumerate(insns):
            m = re.search(r"push\s+0x([0-9a-f]+)$", t)
            if m:
                s = wide(int(m.group(1), 16))
                if s:
                    lits.append((a, i, s))
            if re.match(r"call\s+0x%x$" % REGISTER, t):
                calls.append((a, i))
        # one registration per call: the literals pushed since the previous call are its group and name
        prev_i = -1
        seq = 0
        for site, ci in calls:
            pair = [s for _a, i, s in lits if prev_i < i < ci]
            store = ""
            for a, t in insns[ci + 1:ci + 12]:
                if re.search(r"mov\s+DWORD PTR \[e[a-z]{2}\+0x[0-9a-f]+\],eax", t):
                    store = re.search(r"\[(e[a-z]{2}\+0x[0-9a-f]+)\]", t).group(1)
                    break
                if t.startswith("ret") or t.startswith("call"):
                    break
            seq += 1
            rows.append({
                "kind": "registration", "body": "0x%08x" % va, "body_name": body_named(va),
                "site": "0x%08x" % site, "seq": seq,
                "group": pair[-2] if len(pair) >= 2 else (pair[0] if pair else ""),
                "name": pair[-1] if pair else "",
                "store": store,
                "note": "" if len(pair) >= 2 else "one literal between calls",
            })
            prev_i = ci
        arith = 0
        for a, t in insns:
            if re.search(r"(add|sub|inc|dec)\s+DWORD PTR \[e[a-z]{2}\+0x[0-9a-f]+\]", t):
                arith += 1
        rows.append({
            "kind": "body", "body": "0x%08x" % va, "body_name": body_named(va),
            "site": "0x%08x" % va, "seq": len(calls),
            "group": "", "name": "", "store": "",
            "note": "%d registrations, %d block references, %d add/inc/sub on a [reg+disp] field"
                    % (len(calls), sum(1 for _a, t in insns if "0x7b5d" in t or "0x7b5e" in t), arith),
        })
    return rows


def write(rows, path):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def check(path):
    table = read_map()
    by_name = {n: (va, size) for va, n, size, _m in table}
    rows = list(csv.DictReader(open(path, newline="")))
    bad = []
    bodies = [r for r in rows if r["kind"] == "body"]
    if len(bodies) != len(BODIES):
        bad.append("%d body rows, expected %d" % (len(bodies), len(BODIES)))
    for b in bodies:
        regs = [r for r in rows if r["kind"] == "registration" and r["body"] == b["body"]]
        if len(regs) != int(b["seq"]):
            bad.append("%s: %d registration rows, body row says %s" % (b["body"], len(regs), b["seq"]))
        if b["body_name"] not in by_name:
            bad.append("%s: %s is not in the map" % (b["body"], b["body_name"]))
        for r in regs:
            if not r["group"] or not r["name"]:
                bad.append("%s seq %s has an empty group or name" % (b["body"], r["seq"]))
    audio = next((b for b in bodies if b["body"] == "0x0002e900"), None)
    if audio and int(audio["seq"]) != 8:
        bad.append("the audio registration body has %s calls, expected 8" % audio["seq"])
    if bad:
        for b in bad:
            print("FAIL stat-registry: %s" % b)
        return 1
    print("ok stat-registry: %d bodies, %d registrations, audio body has 8"
          % (len(bodies), len([r for r in rows if r["kind"] == "registration"])))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out", default=OUT)
    ap.add_argument("--check", nargs="?", const=OUT, help="validate an artifact without the dump")
    args = ap.parse_args()
    if args.check:
        return check(args.check)
    if not args.xbe:
        ap.error("--xbe is required unless --check is given")
    rows = build(args)
    write(rows, args.out)
    for r in rows:
        if r["kind"] == "body":
            print("%s %-44s %s" % (r["body"], r["body_name"], r["note"]))
        else:
            print("    %2s %-14s %-16s %s" % (r["seq"], r["group"], r["name"], r["store"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
