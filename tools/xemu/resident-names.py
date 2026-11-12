#!/usr/bin/env python3
"""Ask a guest RAM image which names are resident, before trusting "no copy found" as "not loaded".

A bytecode copy that cannot be located in a dump has two explanations, and they lead to opposite next steps: the body
is **not in the image** (the package loads later, or never), or it is in the image and the **anchor is wrong**. A
`.u` package puts every class, function and property name in its `FNameEntry` pool whether the scripts run or not --
an entry's wide name sits at `+0x0c` (`docs/NATIVE_VM.md`), so it is present as UTF-16LE and, for the short ones, as
plain ASCII -- so counting a name in the image answers the first question without needing the body at all:

    python3 tools/xemu/resident-names.py RAMBIN --name StargateMenus --name UWindowButton
    python3 tools/xemu/resident-names.py RAMBIN --residue $WORK/residue.csv   # per package, by class name

Names are 7-bit ASCII, so the two counts are a *format* reading, not a guess: a name with only a UTF-16 count is in a
name pool, one with an ASCII count is also in a string literal or a `.ini` list, and zero in both is absent.

`--residue CSV` takes the `package,class,function,...` rows `tools/uscript-dis.py --sizefield --residue-csv` writes
and reports, per package, how many of its rows have a resident class name -- the reading that decides whether a
package belongs in the "not resident" bucket at all.

`--check` builds a small buffer with one ASCII-only, one UTF-16-only and one absent name and drives the counter and the
per-package fold, so `make ci` covers the tool without a dump; nothing is written.
"""
import argparse
import csv
import os
import sys


def counts(raw, name):
    return raw.count(name.encode("ascii")), raw.count(name.encode("utf-16-le"))


def verdict(ascii_n, utf16_n):
    if utf16_n and ascii_n:
        return "resident (name pool + ascii)"
    if utf16_n:
        return "resident (name pool)"
    if ascii_n:
        return "ascii only (literal or ini, not a pool entry)"
    return "absent"


def read_rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if (r.get("class") or "").strip()]


def report_residue(raw, path):
    rows = read_rows(path)
    order, seen = [], {}
    for row in rows:
        pkg = row["package"]
        if pkg not in seen:
            seen[pkg] = {"rows": 0, "resident": 0, "absent": []}
            order.append(pkg)
        seen[pkg]["rows"] += 1
        a, w = counts(raw, row["class"])
        if w:
            seen[pkg]["resident"] += 1
        else:
            seen[pkg]["absent"].append(row["class"])
    for pkg in order:
        got = seen[pkg]
        print("%-18s %2d row(s)  %2d class name(s) resident%s"
              % (pkg, got["rows"], got["resident"],
                 "" if not got["absent"] else "  absent: " + ", ".join(got["absent"])))
    return 0


def check():
    """No dump, no writes: the counter and the per-package fold on a synthetic image."""
    import io
    import tempfile
    pool = "Nothing".encode("utf-16-le") + b"\x00\x00" + b"Alpha" + b"\x00" + "Beta".encode("utf-16-le")
    raw = pool * 3
    got = {"Nothing": counts(raw, "Nothing"), "Alpha": counts(raw, "Alpha"), "Gamma": counts(raw, "Gamma")}
    want = {"Nothing": (0, 3), "Alpha": (3, 0), "Gamma": (0, 0)}
    if got != want:
        print("FAIL --check: counts %r, expected %r" % (got, want))
        return 1
    if verdict(*got["Nothing"]) != "resident (name pool)" or verdict(*got["Gamma"]) != "absent":
        print("FAIL --check: verdicts %r / %r" % (verdict(*got["Nothing"]), verdict(*got["Gamma"])))
        return 1
    csv_text = ("package,class,function\nA.u,Alpha,f\nA.u,Gamma,g\nB.u,Nothing,h\n")
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
        fh.write(csv_text)
        path = fh.name
    try:
        out = io.StringIO()
        keep, sys.stdout = sys.stdout, out
        try:
            report_residue(raw, path)
        finally:
            sys.stdout = keep
    finally:
        os.unlink(path)
    text = out.getvalue()
    # Alpha is ASCII only, so it is not a name-pool entry: A.u has 0 resident, B.u has 1.
    if "A.u" not in text or "absent: Alpha, Gamma" not in text or "1 class name(s) resident" not in text:
        print("FAIL --check: per-package fold printed %r" % text)
        return 1
    if verdict(*got["Alpha"]) != "ascii only (literal or ini, not a pool entry)":
        print("FAIL --check: ascii-only verdict is %r" % verdict(*got["Alpha"]))
        return 1
    print("ok --check: ascii-only, name-pool and absent names, and the per-package fold")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("rambin", nargs="?", help="a 128 MiB guest image (pmemsave)")
    ap.add_argument("--name", action="append", default=[], help="a name to count (repeatable)")
    ap.add_argument("--residue", help="a --sizefield residue CSV to fold per package")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        return check()
    if not args.rambin or not os.path.exists(args.rambin):
        ap.error("give a RAM image, or --check")
    with open(args.rambin, "rb") as fh:
        raw = fh.read()
    for name in args.name:
        a, w = counts(raw, name)
        print("%-24s ascii %-5d utf16 %-5d  %s" % (name, a, w, verdict(a, w)))
    if args.residue:
        return report_residue(raw, args.residue)
    return 0


if __name__ == "__main__":
    sys.exit(main())
