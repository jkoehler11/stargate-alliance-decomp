#!/usr/bin/env python3
"""Join every row of `inventory/natives.csv` to the `Function` payload it names (PLAN 3.9, UNKNOWNS U3).

`inventory/natives.csv` is the *record* half of the native story: one row per native function over 92 classes,
each carrying a `native_token` and a `function_flags` name list. `tools/native-reconcile.py` compares that
artifact with the class text, so a row the artifact states wrongly is checked against text it does not appear
in and lands in `inventory/native_residual.csv` as a gap in the *text* -- which is how
`UPreview.UPreviewMapListClient.Created` and `UPreview.UPreviewSkinnedButton.SetRenderInfo` came to be read as
"the record is right, the editor's text is missing". Nothing re-read the payload.

`UFunction::Serialize` writes the two claims next to each other, after the script's last opcode:

    [0b 47][native_token u16][pad u8][FunctionFlags u32]     -- 7-byte tail, un-Netted
    [0b 47][native_token u16][pad u8][FunctionFlags u32][RepOffset u16]

so the flags are the payload's last dword of the 7-byte tail (`tools/function-flags.py` reads them there and its
`docs/BYTECODE.md` section measured the splitter) and the token is the `u16` four bytes in front of them. This
tool joins the two halves per row and reports where the artifact and the record disagree -- the check the
residual table assumed.

    python3 tools/native-record-check.py --packages DUMP/System -o inventory   # needs the dump
    python3 tools/native-record-check.py --check                               # dump-free, validates the CSV
    python3 tools/native-record-check.py --selftest                            # the reader on fixtures

The artifact is `inventory/native_record_check.csv`, one row per `natives.csv` row: the artifact's token and flag
word beside the record's, and a verdict of `agree`, `token-differs`, `flags-differ`, `both-differ` or
`no-record` (the package carries no `Function` export of that name under that class).
"""
import argparse
import csv
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

COLUMNS = ["package", "class", "function", "csv_token", "record_token",
           "csv_flags", "record_flags", "record_native", "records", "verdict"]
NATIVES = os.path.join(ROOT, "inventory", "natives.csv")
EPILOGUE = bytes([0x0B, 0x47])


def read_flags(pay):
    """The payload's `FunctionFlags` dword, at the offset `tools/function-flags.py` measured.

    A `Net` function carries a `RepOffset` after the flags, so the flags sat two bytes earlier; the Net bit is
    read at the 7-byte position to decide, which is what makes the two candidates mutually exclusive.
    """
    n = len(pay)
    if n < 12:
        return None
    if pay[n - 12:n - 9] == b"\x04" + EPILOGUE:          # 9-byte tail: epilogue, then RepOffset at the end
        return n - 6
    if struct.unpack_from("<I", pay, n - 6)[0] & 0x40:
        return n - 6
    return n - 4


def read_record(pay):
    """`(token, flags)` from a `Function` payload, or `(None, None)` when the tail is not there."""
    at = read_flags(pay)
    if at is None or at < 6:
        return None, None
    flags = struct.unpack_from("<I", pay, at)[0]
    token = struct.unpack_from("<H", pay, at - 3)[0]
    return token, flags


def csv_flags(text):
    """Sum the `0x<hex>:Name;` tokens of `natives.csv`'s flag column; unnamed bits appear as bare `0x<hex>`."""
    total = 0
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        total |= int(part.split(":", 1)[0], 16)
    return total


def verdict(csv_tok, csv_flag, rec_tok, rec_flag, records):
    if records == 0:
        return "no-record"
    if csv_tok == rec_tok and csv_flag == rec_flag:
        return "agree"
    if csv_tok != rec_tok and csv_flag != rec_flag:
        return "both-differ"
    return "token-differs" if csv_tok != rec_tok else "flags-differ"


def join(packages_dir, natives_path=NATIVES):
    from ue2pkg import Package                                   # noqa: PLC0415 - only with a dump

    rows = list(csv.DictReader(open(natives_path, newline="", encoding="utf-8")))
    by_package = {}
    for r in rows:
        by_package.setdefault(r["package"], []).append(r)
    out = []
    for package_name in sorted(by_package):
        path = os.path.join(packages_dir, package_name + ".u")
        if not os.path.exists(path):
            for r in by_package[package_name]:
                out.append({"package": package_name, "class": r["class"], "function": r["function"],
                            "csv_token": r["native_token"], "record_token": "", "csv_flags":
                            "0x%08x" % csv_flags(r["function_flags"]), "record_flags": "",
                            "record_native": "0", "records": "0", "verdict": "no-package"})
            continue
        pkg = Package(path)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        exports = {e["index"]: e for e in pkg.exports}
        found = {}
        for e in pkg.exports:
            if pkg.class_name_of(e) != "Function":
                continue
            outer = exports.get(e["outer"] - 1) if e["outer"] > 0 else None
            key = ((pkg.name(outer["name"]) if outer else "").lower(), pkg.name(e["name"]).lower())
            found.setdefault(key, []).append(e)
        for r in by_package[package_name]:
            key = (r["class"].lower(), r["function"].lower())
            hits = found.get(key, [])
            first_flag = last_flag = None
            for e in hits:
                tok, flg = read_record(pkg.data[e["offset"]:e["offset"] + e["size"]])
                if flg is None:
                    continue
                if first_flag is None:
                    first_flag = (tok, flg)
                last_flag = (tok, flg)
            tok_flg = first_flag or (None, None)
            rec_tok, rec_flag = tok_flg
            cflag = csv_flags(r["function_flags"])
            out.append({"package": package_name, "class": r["class"], "function": r["function"],
                        "csv_token": r["native_token"],
                        "record_token": "" if rec_tok is None else str(rec_tok),
                        "csv_flags": "0x%08x" % cflag,
                        "record_flags": "" if rec_flag is None else "0x%08x" % rec_flag,
                        "record_native": "1" if (rec_flag or 0) & 0x400 else "0",
                        "records": str(len(hits)),
                        "verdict": verdict(int(r["native_token"]), cflag, rec_tok, rec_flag, len(hits))})
    return out


def selftest():
    def payload(tok, flg, net=False):
        body = b"\x19\x2a\x2b\x2c" + b"\x04\x0b\x47" + struct.pack("<HBI", tok, 0, flg)
        return body + (b"\x00\x00" if net else b"")

    cases = [(payload(277, 0x00020401), 277, 0x00020401),
             (payload(0, 0x00020002), 0, 0x00020002),
             (payload(3970, 0x00020401, net=True), 3970, 0x00020401)]
    for pay, tok, flg in cases:
        got = read_record(pay)
        assert got == (tok, flg), (got, tok, flg)
    assert csv_flags("0x1:Final;0x400:Native;0x20000:Public;") == 0x20401
    assert csv_flags("0x4:Iterator;0x1000000;") == 0x1000004
    assert verdict(2, 0xcf041c, 0, 0x00020002, 1) == "both-differ"
    assert verdict(0, 0x00020002, 0, 0x00020002, 1) == "agree"
    assert verdict(0, 0, None, None, 0) == "no-record"
    print("ok   native-record-check: %d payload fixtures, flag-column parse, 3 verdicts" % len(cases))
    return 0


def check(path):
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    problems = []
    seen = set()
    for r in rows:
        if sorted(r) != sorted(COLUMNS):
            problems.append("columns %s" % sorted(r))
            break
        key = (r["package"], r["class"], r["function"])
        if key in seen:
            problems.append("duplicate row %s" % (key,))
        seen.add(key)
        try:
            int(r["csv_token"])
            for col in ("csv_flags", "record_flags"):
                if r[col]:
                    int(r[col], 16)
        except ValueError:
            problems.append("%s: unparseable number %r" % (r["function"], r))
        if r["verdict"] not in ("agree", "token-differs", "flags-differ", "both-differ", "no-record",
                                "no-package"):
            problems.append("%s: verdict %r" % (r["function"], r["verdict"]))
        if r["verdict"] == "agree" and r["csv_token"] != r["record_token"]:
            problems.append("%s: agree over differing tokens" % r["function"])
        if r["verdict"] == "agree" and r["csv_flags"] != r["record_flags"]:
            problems.append("%s: agree over differing flags" % r["function"])
        native = "1" if r["record_flags"] and int(r["record_flags"], 16) & 0x400 else "0"
        if r["record_native"] != native:
            problems.append("%s: record_native disagrees with %s" % (r["function"], r["record_flags"]))
    if problems:
        print("FAIL native-record-check: " + "; ".join(problems[:6]))
        return 1
    agree = sum(1 for r in rows if r["verdict"] == "agree")
    native = sum(1 for r in rows if r["record_native"] == "1")
    print("ok   native-record-check: %d rows, %d agree, %d disagree, %d records carry Native"
          % (len(rows), agree, len(rows) - agree, native))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--packages", help="the dump's System directory")
    ap.add_argument("--natives", default=NATIVES)
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--check", action="store_true", help="validate the committed CSV (no dump needed)")
    ap.add_argument("--selftest", action="store_true", help="the reader on fixtures (no dump needed)")
    args = ap.parse_args()
    path = os.path.join(args.out, "native_record_check.csv")
    if args.selftest:
        return selftest()
    if args.check:
        return check(path)
    if not args.packages:
        ap.error("--packages is required unless --check or --selftest")
    rows = join(args.packages, args.natives)
    os.makedirs(args.out, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    counts = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    native = sum(1 for r in rows if r["record_native"] == "1")
    print("%d rows: %s; %d of %d records carry the Native bit"
          % (len(rows), ", ".join("%s %d" % kv for kv in sorted(counts.items())),
             native, sum(1 for r in rows if r["records"] != "0")))
    for r in rows:
        if r["verdict"] != "agree":
            print("  %-10s %s.%s csv(tok=%s flags=%s) record(tok=%s flags=%s)"
                  % (r["verdict"], r["class"], r["function"], r["csv_token"], r["csv_flags"],
                     r["record_token"], r["record_flags"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())