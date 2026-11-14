#!/usr/bin/env python3
"""Who calls a function: census one address or symbol name from the Ghidra evidence export.

An unnamed helper is usually the reason a whole module reads as `FUN_*` soup, and the way to name it
is to look at who calls it and with what. This prints the call sites and groups callers by the
attribution they carry (assert-derived `<Module>_<File>`, or unnamed).

Input is the export written by tools/ghidra/ghidra_scripts/ExportEvidence.java, so run `make ghidra`
first. `--target` takes an address (`0x00058a30`) or a final Ghidra name.

**An address is compared as an integer, never as text.** The export spells every address with eight
hex digits (`0x0005a990`); comparing the argument's text against that column answers `0 call sites`
for `--target 0x5a990` while the row is right there in `calls.tsv` -- a zero that reads as "nothing
calls it". That false zero was measured on `0x0005a990` (`Core_UnMisc`, one site at `0x00012b7d` in
`Core_UnObjBas`) and is why `--check` drives both spellings.

A zero is also only ever a statement about the export: Ghidra records a call where its analysis gave
the caller a body, so an unanalysed region contributes no rows. When the answer matters, cross-check
it against the bytes with `tools/vtable-reach.py --va <addr>` (an `E8 rel32` scan of `.text`), which
needs no export. This tool says which of the two it answered.

Usage:
    call-census.py --target FUN_00058a30 [--evidence build/ghidra/evidence] [--names]
    call-census.py --check          # synthetic export, no dump, writes nothing
"""
import argparse
import collections
import csv
import os
import re
import sys
import tempfile

MODULES = {"Core", "Engine", "Gui", "IpDrv", "D3DDrv", "XboxDrv", "XboxAudio", "XboxLive",
           "Gameplay", "Fire", "stargategame", "stargatevehicles"}
NAMED = re.compile(r"^([A-Za-z][A-Za-z0-9]*)_([A-Za-z0-9_]+)$")


def norm(addr):
    """`0x5a990`, `0x0005A990` and `0x0005a990` are one address: compare integers, print one form."""
    return "0x%08x" % int(addr, 16)


def read(path, columns):
    rows = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            parts += [""] * (columns - len(parts))
            rows.append(parts[:columns])
    return rows


def load(evidence):
    """(address -> (name, size), [(caller, site, callee)]) with every address normalised."""
    funcs = {}
    for a, n, s in ((r[0], r[1], r[2]) for r in read(os.path.join(evidence, "funcs.tsv"), 6)):
        try:
            funcs[norm(a)] = (n, int(s))
        except ValueError:
            continue
    calls = []
    for caller, site, callee, _n in read(os.path.join(evidence, "calls.tsv"), 4):
        try:
            calls.append((norm(caller), norm(site), norm(callee)))
        except ValueError:
            continue
    return funcs, calls


def resolve(funcs, spec):
    """An address or a final function name -> one normalised address."""
    if spec.lower().startswith("0x"):
        return norm(spec)
    matches = [a for a, (n, _s) in funcs.items() if n == spec]
    if len(matches) != 1:
        raise ValueError("target %r matched %d functions -- pass an address" % (spec, len(matches)))
    return matches[0]


def census(evidence, spec):
    """The answer for one target: its address, name, and every `E8` site the export recorded."""
    funcs, calls = load(evidence)
    target = resolve(funcs, spec)
    hits = sorted((caller, site) for caller, site, callee in calls if callee == target)
    return {"target": target, "name": funcs.get(target, ("?", 0))[0], "sites": hits,
             "evidence": evidence}


def attribution(funcs, rows):
    by_attr = collections.Counter()
    for caller in {c for c, _s in rows}:
        name = funcs.get(caller, ("", 0))[0]
        m = NAMED.match(name)
        by_attr[m.group(1) + "/" + m.group(2) if m and m.group(1) in MODULES
                else (name if not name.startswith(("FUN_", "Native_")) else "unnamed")] += 1
    return by_attr


def report(result, names):
    funcs, _calls = load(result["evidence"])
    hits = result["sites"]
    print("%s (%s): %d call sites from %d distinct callers"
          % (result["target"], result["name"], len(hits), len({c for c, _s in hits})))
    if not hits:
        print("  zero is a statement about the export (no analysed caller records an E8 to this "
              "address), not about the image -- cross-check with vtable-reach.py --va")
    for label, count in attribution(funcs, hits).most_common(10):
        print("  %-28s %d" % (label, count))
    if names:
        for caller, site in hits:
            print("  %s -> %s" % (site, funcs.get(caller, (caller, 0))[0]))


def selftest():
    """A synthetic export: one call, spelled the way the real one is, reached three ways."""
    failures = []

    def want(label, got, expect):
        if got != expect:
            failures.append("%s: %r != %r" % (label, got, expect))

    tmp = tempfile.mkdtemp(prefix="call-census-check.")
    with open(os.path.join(tmp, "funcs.tsv"), "w") as fh:
        fh.write("# test export\n")
        fh.write("0x00012620\tCore_UnObjBas\t18359\t0\t633\t0\n")
        fh.write("0x0005a990\tCore_UnMisc\t3181\t1\t27\t0\n")
    with open(os.path.join(tmp, "calls.tsv"), "w") as fh:
        fh.write("0x00012620\t0x00012b7d\t0x0005a990\tCore_UnMisc\n")
        fh.write("0x00099999\t0x000999aa\t0x00011111\tFUN_00011111\n")

    want("eight-digit spelling", census(tmp, "0x0005a990")["sites"],
         [("0x00012620", "0x00012b7d")])
    want("short spelling", census(tmp, "0x5a990")["sites"], [("0x00012620", "0x00012b7d")])
    want("upper-case spelling", census(tmp, "0x0005A990")["sites"],
         [("0x00012620", "0x00012b7d")])
    want("name resolves", census(tmp, "Core_UnMisc")["sites"], [("0x00012620", "0x00012b7d")])
    want("a different address", census(tmp, "0x5a994")["sites"], [])
    want("normalised target", census(tmp, "0x5a990")["target"], "0x0005a990")
    want("name of the target", census(tmp, "0x5a990")["name"], "Core_UnMisc")
    funcs, _calls = load(tmp)
    want("attribution", attribution(funcs, census(tmp, "0x5a990")["sites"]), {"Core/UnObjBas": 1})
    try:
        resolve(funcs, "FUN_nope")
        failures.append("an unknown name did not raise")
    except ValueError:
        pass
    for name in ("funcs.tsv", "calls.tsv"):
        os.unlink(os.path.join(tmp, name))
    os.rmdir(tmp)
    if failures:
        for line in failures:
            print("FAIL call-census: " + line)
        return 1
    print("ok   call-census: 9 assertions, no dump, no writes")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", help="address or final function name")
    ap.add_argument("--evidence", default=os.path.join("build", "ghidra", "evidence"))
    ap.add_argument("--names", action="store_true", help="list every call site")
    ap.add_argument("--check", action="store_true", help="synthetic export, no dump, writes nothing")
    args = ap.parse_args()

    if args.check:
        return selftest()
    if not args.target:
        ap.error("--target is required (or --check)")

    for name in ("funcs.tsv", "calls.tsv"):
        if not os.path.isfile(os.path.join(args.evidence, name)):
            sys.exit("%s missing -- run `make ghidra` first" % os.path.join(args.evidence, name))
    try:
        report(census(args.evidence, args.target), args.names)
    except ValueError as exc:
        sys.exit(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
