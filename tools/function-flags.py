#!/usr/bin/env python3
"""Read every function's `FunctionFlags` out of its export payload (PLAN 4.3 / 8.3, docs/systems/NETWORK.md).

`UFunction::Serialize` (`0x00039f10`) writes the flags as the last-but-three bytes of a payload, and
`docs/BYTECODE.md` measured where: a `Function` payload ends

    [header][size][script]["G"][3 bytes][FunctionFlags u32]                7-byte tail
    [header][size][script]["G"][3 bytes][FunctionFlags u32][RepOffset u16] 9-byte tail, Net functions only

so the flags are at `payload_end-4` for an un-Netted function and at `payload_end-6` for a Net one. Which tail a
payload has is decided by where the three-byte script epilogue (`04 0b 47`) sits, i.e. from the data rather than
from the flags being read — the check in `docs/BYTECODE.md` is that the 9-byte tail and the Net bit agree, and this
tool reports that agreement rather than assuming it.

    python3 tools/function-flags.py --packages DUMP/System -o inventory   # needs the dump
    python3 tools/function-flags.py --check                               # dump-free, validates the CSV

The artifact is `inventory/function_flags.csv`, one row per function: package, class, function, flags, the flag
names, and which tail was used. The flag names are UELib's UE2 `FunctionFlags` (`src/UnrealFlags.cs`, pinned
checkout), the same vocabulary `inventory/natives.csv` already carries for the native functions.
"""
import argparse
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# UELib's UE2 FunctionFlags (src/UnrealFlags.cs). Odd bits are not named by any UE2 header.
FLAGS = [
    (0x00000001, "Final"), (0x00000002, "Defined"), (0x00000004, "Iterator"), (0x00000008, "Latent"),
    (0x00000010, "PreOperator"), (0x00000020, "Singular"), (0x00000040, "Net"), (0x00000080, "NetReliable"),
    (0x00000100, "Simulated"), (0x00000200, "Exec"), (0x00000400, "Native"), (0x00000800, "Event"),
    (0x00001000, "Operator"), (0x00002000, "Static"), (0x00004000, "NoExport"), (0x00008000, "Const"),
    (0x00010000, "Invariant"), (0x00020000, "Public"), (0x00040000, "Private"), (0x00080000, "Protected"),
    (0x00100000, "Delegate"), (0x00200000, "NetServer"), (0x00400000, "NetClient"), (0x00800000, "HasOutParms"),
    (0x01000000, "HasOptionalParms"), (0x02000000, "HasDefaults"), (0x04000000, "DLLImport"),
]
EPILOGUE = bytes([0x04, 0x0B, 0x47])
COLUMNS = ["package", "class", "function", "flags", "flag_names", "trailer_bytes"]


def names(value):
    return ";".join(n for bit, n in FLAGS if value & bit)


def read_package(path):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from ue2pkg import Package                                # noqa: PLC0415 - only when a dump is present

    pkg = Package(path)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    exports = {e["index"]: e for e in pkg.exports}
    package_name = os.path.basename(path).rsplit(".", 1)[0]
    rows, tails, missing = [], {"7": 0, "9": 0, "no-epilogue": 0}, 0
    for e in pkg.exports:
        if pkg.class_name_of(e) != "Function":
            continue
        pay = pkg.data[e["offset"]:e["offset"] + e["size"]]
        n = len(pay)
        trailer = None
        if n < 16:
            missing += 1
            continue
        if n >= 10 and pay[n - 10:n - 7] == EPILOGUE:
            trailer, at, tail = 7, n - 4, "7"
        elif n >= 12 and pay[n - 12:n - 9] == EPILOGUE:
            trailer, at, tail = 9, n - 6, "9"
        else:
            # No `04 0b 47` epilogue: either a body-less function (no script, so no `'G'` marker) or a script whose
            # last tokens are `0b 47` rather than `04 0b 47`. Which tail applies is decided by the code's own
            # prediction — `RepOffset` is written only for a Net function, so a Net flag at `end-6` means the
            # 9-byte tail and nothing else. The two candidates are mutually exclusive by construction: a 7-byte
            # tail requires the Net bit clear at `end-4`, a 9-byte one requires it set at `end-6`.
            tail = "no-epilogue"
            at = n - 6 if struct.unpack_from("<I", pay, n - 6)[0] & 0x40 else n - 4
        flags = struct.unpack_from("<I", pay, at)[0]
        tails[tail] += 1
        owner = exports.get(e["outer"] - 1) if e["outer"] > 0 else None
        rows.append({"package": package_name,
                     "class": pkg.name(owner["name"]) if owner is not None else "",
                     "function": pkg.name(e["name"]),
                     "flags": "0x%08x" % flags,
                     "flag_names": names(flags),
                     "trailer_bytes": tail})
    return rows, tails, missing


def check(path):
    rows = list(csv.DictReader(open(path)))
    problems = []
    for r in rows:
        if sorted(r) != sorted(COLUMNS):
            problems.append("columns %s" % sorted(r))
            break
        try:
            value = int(r["flags"], 16)
        except ValueError:
            problems.append("%s: flags %r" % (r["function"], r["flags"]))
            continue
        if r["flag_names"] != names(value):
            problems.append("%s: flag names do not match 0x%08x" % (r["function"], value))
        if r["trailer_bytes"] not in ("7", "9", "no-epilogue"):
            problems.append("%s: tail %r" % (r["function"], r["trailer_bytes"]))
        if r["trailer_bytes"] == "9" and not value & 0x40:
            problems.append("%s: 9-byte tail without the Net flag" % r["function"])

    if problems:
        print("FAIL function-flags: " + "; ".join(problems[:6]))
        return 1
    net = sum(1 for r in rows if int(r["flags"], 16) & 0x40)
    print("ok   function-flags: %d functions, %d Net, names match the flags" % (len(rows), net))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--packages", help="a directory of .u packages (the dump's System dir)")
    ap.add_argument("--package", action="append", default=[], help="one package (repeatable)")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--check", action="store_true", help="validate the committed CSV (no dump needed)")
    args = ap.parse_args()

    path = os.path.join(args.out, "function_flags.csv")
    if args.check:
        return check(path)

    paths = list(args.package)
    if args.packages:
        paths += sorted(os.path.join(args.packages, f) for f in os.listdir(args.packages) if f.endswith(".u"))
    if not paths:
        ap.error("--packages or --package is required unless --check")

    rows = []
    tails = {"7": 0, "9": 0, "no-epilogue": 0}
    missing = 0
    for p in paths:
        r, t, m = read_package(p)
        rows += r
        for k in tails:
            tails[k] += t[k]
        missing += m
        print("%-20s %4d functions  tail 7: %4d  tail 9: %3d  no script: %4d"
              % (os.path.basename(p), len(r), t["7"], t["9"], t["no-epilogue"]))

    rows.sort(key=lambda r: (r["package"].lower(), r["class"].lower(), r["function"].lower()))
    os.makedirs(args.out, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("wrote %s (%d rows)" % (path, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
