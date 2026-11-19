#!/usr/bin/env python3
"""Name image functions from a locally supplied set of static libraries (PLAN 4.2, U9).

A COFF lib function and its linked copy differ only where the linker patched an
operand. Masking the lib's relocation offsets on both sides therefore leaves a
byte-exact body, which names the image function without any signature database --
the library archives *are* the database, and they never enter this repository.

    python3 tools/xdk-lib-match.py --libs ~/sdk/xbox/lib --xbe $SG1_DUMP/.../default.xbe \
        --functions symbols/xbox_functions.csv -o inventory/xdk_lib_names.csv --apply

`--apply` appends the names to `symbols/xbe_overrides.csv` (so a later Ghidra
import reproduces them) and applies them to `symbols/xbox_functions.csv`
names-only. `--check` validates the committed artifact without libs or dump.
`--verify-calls` measures the matches against the libraries' own call graph.

Quality rule, because identical short bodies are the failure mode: a match is
trusted only when the body is at least `--min-len` bytes *and* carries at least
one relocation (`--min-len-norel` bytes when it carries none), and when no other
image address claims the same name.
"""

import argparse
import collections
import csv
import hashlib
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from cofflib import (CoffObject, CoffError, REL_WIDTH, masked,  # noqa: E402
                     parse_archive, trim_padding)

ARTIFACT = os.path.join("inventory", "xdk_lib_names.csv")
OVERRIDES = os.path.join("symbols", "xbe_overrides.csv")
FUNCTIONS = os.path.join("symbols", "xbox_functions.csv")
PAD = (0xCC, 0x90, 0x00)
COLUMNS = ["address", "name", "size", "lib", "member", "section"]


# --------------------------------------------------------------------------- index

def load_index(libdir, verbose=True):
    """((len, first2) -> [entry], (len, masked) -> {names})"""
    index, sig_names = {}, {}
    stats = collections.Counter()
    for root, _dirs, files in os.walk(libdir):
        for fn in sorted(files):
            if not fn.lower().endswith(".lib"):
                continue
            data = open(os.path.join(root, fn), "rb").read()
            try:
                members = parse_archive(data)
            except CoffError:
                stats["libs_skipped"] += 1
                continue
            libname = os.path.splitext(fn)[0]
            stats["libs"] += 1
            for member, blob in members:
                try:
                    obj = CoffObject(member, blob)
                except CoffError:
                    stats["objects_skipped"] += 1
                    continue
                stats["objects"] += 1
                for name, body, masks in obj.functions_full():
                    code = trim_padding(body)
                    if len(code) < 3:
                        continue
                    # a reloc's operand is masked; its target is kept for --verify-calls
                    loose = [(off, w) for off, w, _sym, _t in masks]
                    m = masked(code, loose)
                    sig = (len(m), m)
                    sig_names.setdefault(sig, set()).add(name)
                    index.setdefault((len(m), bytes(m[:2])), []).append({
                        "name": name, "lib": libname, "member": member,
                        "body": code, "masks": loose, "masked": m,
                        "relocs": masks, "sig": sig,
                    })
                    stats["functions"] += 1
    if verbose:
        print("index: %(libs)d libs, %(objects)d objects, %(functions)d functions"
              % stats, file=sys.stderr)
    return index, sig_names


def candidate_keys(length, first_bytes):
    """Lookups that tolerate a relocation operand inside the first two bytes."""
    body = first_bytes
    keys = [(length, bytes(body[:2]))]
    if len(body) >= 1:
        keys.append((length, b"\x00" + bytes(body[1:2])))
        keys.append((length, bytes(body[0:1]) + b"\x00"))
    keys.append((length, b"\x00\x00"))
    return keys


def match_function(index, body):
    """Best (name, entry) for one image body, or (None, None)."""
    body = trim_padding(body)
    best = None
    for cand_len in range(len(body), max(len(body) - 32, 2) - 1, -1):
        prefix = body[:cand_len]
        for key in candidate_keys(cand_len, prefix):
            for entry in index.get(key, ()):
                if masked(prefix, entry["masks"]) != entry["masked"]:
                    continue
                tail = body[cand_len:]
                if any(b not in PAD for b in tail):
                    continue  # the image body continues past the lib function
                score = len(entry["masks"])
                if best is None or score < best[0]:
                    best = (score, entry)
                break
    if best is None:
        return None, None
    return best[1]["name"], best[1]


# --------------------------------------------------------------------------- generate

def generate(args):
    from xbe import Xbe                                        # noqa: PLC0415 - dump reader

    xbe = Xbe(args.xbe)
    index, sig_names = load_index(args.libs)
    rows, rejected = [], collections.Counter()
    with open(args.functions, newline="") as fh:
        for row in csv.DictReader(fh):
            if not row["name"].startswith("FUN_"):
                continue
            addr, size = int(row["address"], 16), int(row["size"])
            body = xbe.read_va(addr, size)
            if not body:
                rejected["no bytes"] += 1
                continue
            name, entry = match_function(index, body)
            if not name:
                rejected["no body match"] += 1
                continue
            if entry["sig"][0] < args.min_len:
                rejected["body under %d B" % args.min_len] += 1
                continue
            if not entry["relocs"] and entry["sig"][0] < args.min_len_norel:
                rejected["no anchor under %d B" % args.min_len_norel] += 1
                continue
            if len(sig_names.get(entry["sig"], {name})) > args.max_ambiguous:
                rejected["signature ambiguous"] += 1
                continue
            sec = xbe.section_for(addr)
            rows.append({"address": row["address"], "name": name, "size": size,
                         "lib": entry["lib"], "member": entry["member"],
                         "section": sec.name if sec else ""})
            entry.setdefault("image", []).append(addr)

    seen = collections.Counter(r["name"] for r in rows)
    dupes = {n for n, c in seen.items() if c > 1}
    if dupes:
        rows = [r for r in rows if r["name"] not in dupes]
        rejected["name claimed twice"] += len(dupes)

    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: int(r["address"], 16)))

    print("named %d functions from %d libraries" % (len(rows), len({r['lib'] for r in rows})))
    for reason, n in rejected.most_common():
        print("  rejected, %-26s %5d" % (reason, n))
    by_lib = collections.Counter(r["lib"] for r in rows)
    print("  " + ", ".join("%s %d" % kv for kv in by_lib.most_common(8)))
    by_sec = collections.Counter(r["section"] or "?" for r in rows)
    print("  " + ", ".join("%s %d" % kv for kv in by_sec.most_common()))

    if args.apply:
        apply_names(rows, args)
    if args.verify_calls:
        verify_calls(args.libs, args.out, args.xbe)
    return 0


def apply_names(rows, args):
    """Append the names to the override file and apply them to the function map, names-only."""
    have = set()
    if os.path.isfile(OVERRIDES):
        with open(OVERRIDES, newline="") as fh:
            have = {r["address"].lower() for r in csv.DictReader(fh)}
    added = 0
    with open(OVERRIDES, "a", newline="") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        for r in rows:
            if r["address"].lower() in have:
                continue
            w.writerow([r["address"], r["name"],
                        "masked body equals %s.lib %s" % (r["lib"], r["member"]),
                        "", "", "", "", "", "", ""])
            added += 1

    renamed = conflicts = 0
    tmp = args.functions + ".tmp"
    with open(args.functions, newline="") as fh, open(tmp, "w", newline="") as out:
        rd = csv.DictReader(fh)
        w = csv.DictWriter(out, fieldnames=rd.fieldnames, lineterminator="\r\n")
        w.writeheader()
        by_addr = {r["address"].lower(): r for r in rows}
        for row in rd:
            hit = by_addr.get(row["address"].lower())
            if hit and row["name"].startswith("FUN_"):
                row["name"] = hit["name"]
                renamed += 1
            elif hit:
                conflicts += 1
            w.writerow(row)
    os.replace(tmp, args.functions)
    print("overrides: +%d rows; function map: %d renamed, %d already named"
          % (added, renamed, conflicts))


# --------------------------------------------------------------------------- verify

def verify_calls(libdir, artifact, xbe_path):
    """Do the libraries' own call edges land on the function they name?"""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from xbe import Xbe                                        # noqa: PLC0415
    xbe = Xbe(xbe_path)
    table = {}
    for root, _dirs, files in os.walk(libdir):
        for fn in sorted(files):
            if not fn.lower().endswith(".lib"):
                continue
            lib = os.path.splitext(fn)[0]
            try:
                members = parse_archive(open(os.path.join(root, fn), "rb").read())
            except CoffError:
                continue
            for member, blob in members:
                try:
                    obj = CoffObject(member, blob)
                    for name, _body, rels in obj.functions_full():
                        table.setdefault((lib, member, name), rels)
                except CoffError:
                    continue
    rows = {}
    with open(artifact, newline="") as fh:
        for row in csv.DictReader(fh):
            rows[int(row["address"], 16)] = row
    agree = disagree = unresolved = 0
    bad = []
    for addr, row in rows.items():
        rels = table.get((row["lib"], row["member"], row["name"]))
        if rels is None:
            continue
        body = xbe.read_va(addr, int(row["size"]))
        for off, width, sym, typ in rels:
            if width != 4 or not (typ & 0x20) or off + 4 > len(body):
                continue
            target = addr + off + 4 + struct.unpack("<i", body[off:off + 4])[0]
            hit = rows.get(target)
            if hit is None:
                unresolved += 1
            elif hit["name"] == sym:
                agree += 1
            else:
                disagree += 1
                bad.append((hex(addr), row["name"], sym, hit["name"]))
    total = agree + disagree
    print("call edges: %d resolved, %d agree, %d disagree (%.2f%%), %d targets unnamed"
          % (total, agree, disagree, 100.0 * agree / max(total, 1), unresolved))
    for b in bad[:5]:
        print("  %s %s expects %s, image has %s" % b)


# --------------------------------------------------------------------------- check

def check(args):
    """Validate the committed artifact against the committed map. No libs, no dump."""
    bad = []
    if not os.path.isfile(args.out):
        raise SystemExit("%s missing -- run the generator" % args.out)
    with open(args.out, newline="") as fh:
        rd = csv.DictReader(fh)
        if rd.fieldnames != COLUMNS:
            raise SystemExit("%s columns are %r, expected %r" % (args.out, rd.fieldnames, COLUMNS))
        rows = list(rd)
    names = {}
    with open(args.functions, newline="") as fh:
        for row in csv.DictReader(fh):
            names[row["address"].lower()] = row["name"]
    for row in rows:
        addr = row["address"].lower()
        if addr not in names:
            bad.append("%s is not a function in the map" % row["address"])
        elif names[addr] != row["name"]:
            bad.append("%s is %s in the map, %s here" % (row["address"], names[addr], row["name"]))
        if not row["lib"] or not row["section"]:
            bad.append("%s has no library or section" % row["address"])
        if int(row["size"]) < args.min_len:
            bad.append("%s is under the length rule" % row["address"])
    dupes = [n for n, c in collections.Counter(r["name"] for r in rows).items() if c > 1]
    bad.extend("%s is claimed twice" % n for n in dupes[:5])
    if bad:
        print("xdk-lib-names: %d problem(s)" % len(bad))
        for b in bad[:10]:
            print("  " + b)
        return 1
    print("ok   xdk-lib-names: %d rows, each a named function in the map, no name twice"
          % len(rows))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--libs", help="directory holding the .lib archives (never committed)")
    ap.add_argument("--xbe", help="default.xbe")
    ap.add_argument("--functions", default=FUNCTIONS)
    ap.add_argument("-o", "--out", default=ARTIFACT)
    ap.add_argument("--apply", action="store_true",
                    help="append to symbols/xbe_overrides.csv and rename in the function map")
    ap.add_argument("--verify-calls", action="store_true",
                    help="check the matches against the libraries' own call graph")
    ap.add_argument("--check", action="store_true", help="validate the artifact, dump-free")
    ap.add_argument("--min-len", type=int, default=12)
    ap.add_argument("--min-len-norel", type=int, default=24)
    ap.add_argument("--max-ambiguous", type=int, default=1)
    args = ap.parse_args()

    if args.check:
        return check(args)
    missing = [f for f in ("libs", "xbe") if not getattr(args, f)]
    if missing:
        raise SystemExit("--%s required to generate" % missing[0])
    if not os.path.isdir(args.libs):
        raise SystemExit("no library directory at %s" % args.libs)
    return generate(args)


if __name__ == "__main__":
    sys.exit(main())
