#!/usr/bin/env python3
"""Where the checkpoint loader looks for a `.sgs`, and where the disc keeps them (U5, `docs/systems/CHECKPOINTS.md`)

`docs/systems/CHECKPOINTS.md` records that `loadgame c_m02b_secondsymbol.sgs` in `M02B` does not give back a
playable level, and names the open question as *whether the shipped files resolve at all* -- a bare name typed at a
console has to become a path somewhere. This reads that path out of the image: no boot, no decompiled output.

The loader `0x00134d40` builds its path from three joins, all of them visible in its own listing:

    FUN_0001b260(L"defaultdevice")            ; 0x00134dc5 / 0x00134e2b, the device name
    FUN_001be450(out, <device>, <profile>)    ; 0x00134df2 / 0x00134e4b, device -> profile
    FUN_00036c90(buf, L"%s%s", <prefix>, name) ; 0x00134ea2, prefix + the console's own name

`FUN_001be350` is where the device name becomes a directory. It `wcsicmp`s its argument (`FUN_00036d00`, site
`0x001be389`) against the literal at `0x00508cd4`, and when that matches it asks the file manager -- the singleton at
`[0x005bf514]`, its vtable's slot `+0x38` -- for the device path, then appends `"\\"` (`0x004c8b94`), `L"Profiles"`
(`0x00515650`) and another `"\\"`. **The slot answers the empty string**: `0x00034210` is 33 bytes whose whole body
constructs `FString(out, "")` from the empty literal at `0x004c85ac` and returns its argument. So the prefix the
loader composes is `\\Profiles\\<profile>\\` -- rootless, and the device name contributes no characters at all.

The profile segment is the loader's third join: the literal `L"Auto"` (`0x004dc798`) when the name starts with
`TRAVEL_` (`0x00508f5c`, `wcsnicmp` `FUN_00036dc0` at `0x00134dab`), otherwise the FString at `+0x4c` of the object
at `+0xf4c` -- the active profile. The shipped tree agrees with that shape exactly: `System/Profiles/<profile>/`.
The one segment the loader does not spell is the leading `System\\`, which is the file manager's own base directory
and not something the loader names.

    python3 tools/save-path-compose.py --xbe $DUMP/default.xbe --dump $DUMP -o inventory/save_path_compose.csv
    python3 tools/save-path-compose.py --write-doc docs/systems/CHECKPOINTS.md
    python3 tools/save-path-compose.py --check-doc docs/systems/CHECKPOINTS.md
    python3 tools/save-path-compose.py --check      # synthetic bytes, no dump, no writes

`--check` drives the wide-string decode, the literal scan, the device-name compare, the composition and the ini
reader on synthetic input, and asserts the rendered tables against a fixture, so `make ci` holds the contract
without the dump. `--write-doc`/`--check-doc` render and compare the page from the committed CSV alone.
"""

import argparse
import csv
import importlib.util
import os
import struct
import sys

COLUMNS = ["kind", "key", "value", "detail"]
BLOCK_MARK = ("<!-- generated:save-path -->", "<!-- /generated:save-path -->")
CSV_PATH = os.path.join("inventory", "save_path_compose.csv")
BODY_NAMES = os.path.join("inventory", "body_names.csv")

# The chain, as addresses. Every literal is read back by address and compared with the text the listing implies --
# an address that points at a different string is a moved image, and the tool says so instead of printing a guess.
LOADER = 0x00134D40
PATH_HELPER = 0x001BE350
PATH_JOIN = 0x001BE450
DEVICE_COMPARE = 0x00036D00
NAME_COMPARE = 0x00036DC0
FM_GLOBAL = 0x005BF514
FM_DEVICE_SLOT = 0x38
DEVICE_GETTER = 0x00034210
DEVICE_GETTER_SIZE = 33
EMPTY_LITERAL = 0x004C85AC

LITERALS = [
    ("defaultdevice", 0x00508CD4, "defaultdevice"),
    ("Profiles", 0x00515650, "Profiles"),
    ("separator", 0x004C8B94, "\\"),
    ("Auto", 0x004DC798, "Auto"),
    ("TRAVEL_", 0x00508F5C, "TRAVEL_"),
    ("join", 0x004D491C, "%s%s"),
    ("empty", EMPTY_LITERAL, ""),
]
# The save/profile vocabulary the image is censused for, and what each would have to spell to name the shipped tree.
VOCABULARY = ["Profiles", "DefaultProfile", "Saves", "System\\", "\\System"]
SAVE_EXTENSIONS = (".sgs", ".sgp")


# ---------------------------------------------------------------- wide strings, exactly

def wide_literals(data, min_len=4):
    """(file_offset, text) per NUL-separated UTF-16LE run of printable ASCII, one char per 16-bit unit.

    Decoding one unit at a time is deliberate: a whole-buffer `decode("utf-16-le")` shifts every later offset once a
    surrogate pair appears, and this project has already shipped 769 addresses that were 2,970 bytes short that way.
    """
    out = []
    i = 0
    n = len(data)
    while i + 1 < n:
        j = i
        chars = []
        while j + 1 < n and data[j:j + 2] != b"\x00\x00":
            unit = data[j] | (data[j + 1] << 8)
            chars.append(unit)
            j += 2
        if len(chars) >= min_len and all(32 <= c < 127 for c in chars):
            out.append((i, "".join(chr(c) for c in chars)))
            i = j + 2
        else:
            i += 2
    return out


def wide_at(data, va_to_offset, va, limit=200):
    try:
        off = va_to_offset(va)
    except Exception:
        return None
    raw = data[off:off + limit]
    chars = []
    for k in range(0, len(raw) - 1, 2):
        unit = raw[k] | (raw[k + 1] << 8)
        if unit == 0:
            break
        if not 32 <= unit < 127:
            return None
        chars.append(chr(unit))
    return "".join(chars)


def dword_refs(data, offset_to_va, target, text_lo=None, text_hi=None):
    """Every dword in the image that stores `target`, as (site_va, headline) where headline is push/mov/other."""
    pat = struct.pack("<I", target)
    hits = []
    at = data.find(pat)
    while at >= 0:
        va = offset_to_va(at)
        if va is not None:
            first = data[at - 1] if at else 0
            if text_lo is not None and text_hi is not None and not (text_lo <= va < text_hi):
                kind = "data"
            elif first == 0x68:
                kind = "push"
            elif first in (0xB8, 0xB9, 0xBA, 0xBB, 0xBE, 0xBF):
                kind = "mov"
            else:
                kind = "other"
            hits.append((va, kind))
        at = data.find(pat, at + 1)
    return hits


def load_xbe(path):
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("xbe", os.path.join(here, "xbe.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Xbe(path)


def body_names(path=BODY_NAMES):
    """address -> (name, size) for the committed function map, so an xref site can be attributed without Ghidra."""
    table = {}
    if not os.path.exists(path):
        return table
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            if not row.get("size"):
                continue
            try:
                table[int(row["address"], 16)] = (row["name"], int(row["size"]))
            except (KeyError, ValueError):
                continue
    return table


def containing(table, va):
    for start, (name, size) in table.items():
        if start <= va < start + size:
            return name
    return ""


# ---------------------------------------------------------------- the composition

def compose(profile, name):
    """What the loader's three joins produce once the device segment answers ""."""
    return "\\Profiles\\" + profile + "\\" + name


def split_shipped(relpath):
    """`System/Profiles/<profile>/<file>` -> (profile, name); anything else -> ("", basename)."""
    parts = relpath.replace("\\", "/").split("/")
    return (parts[-2] if len(parts) >= 2 else ""), parts[-1]


def savepath_of(text):
    """The `SavePath=` value of `[Core.System]`, or None."""
    section = ""
    value = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section == "core.system" and "=" in line:
            key, _, val = line.partition("=")
            if key.strip().lower() == "savepath":
                value = val.strip()
    return value


# ---------------------------------------------------------------- the measurement

def measure(xbe_path, dump):
    xbe = load_xbe(xbe_path)
    data = xbe.data
    text = [s for s in xbe.sections if s.executable]
    text_lo = min(s.va for s in text) if text else None
    text_hi = max(s.va + s.vsize for s in text) if text else None
    va_to_offset = xbe.va_to_offset
    rows = []

    for key, va, expect in LITERALS:
        got = wide_at(data, va_to_offset, va)
        verdict = "ok" if got == expect else ("absent" if got is None else "MISMATCH")
        rows.append({
            "kind": "literal",
            "key": key,
            "value": "%#010x" % va,
            "detail": '%s "%s"%s' % (verdict, (got if got is not None else "").replace("\\", "\\\\"),
                                     "" if verdict == "ok" else " (expected %r)" % expect),
        })

    # helper -> singleton -> vtable slot -> the device getter, resolved from the image rather than assumed.
    try:
        singleton = struct.unpack_from("<I", xbe.read_va(FM_GLOBAL, 4))[0]
        vtable = struct.unpack_from("<I", xbe.read_va(singleton, 4))[0]
        getter = struct.unpack_from("<I", xbe.read_va(vtable + FM_DEVICE_SLOT, 4))[0]
    except Exception:
        singleton = vtable = getter = 0
    push_empty = False
    if getter:
        body = xbe.read_va(getter, DEVICE_GETTER_SIZE)
        needle = b"\x68" + struct.pack("<I", EMPTY_LITERAL)
        push_empty = needle in body
    rows.append({"kind": "chain", "key": "singleton", "value": "%#010x" % singleton,
                 "detail": "file manager, `[%#010x]` -> vtable `%#010x`, slot `+0x%02x`" % (FM_GLOBAL, vtable, FM_DEVICE_SLOT)})
    rows.append({"kind": "chain", "key": "device_getter", "value": "%#010x" % getter,
                 "detail": "%d bytes; %s the empty literal at `%#010x`%s" % (
                     DEVICE_GETTER_SIZE, "constructs" if push_empty else "does NOT construct", EMPTY_LITERAL,
                     "" if getter == DEVICE_GETTER else " (expected %#010x)" % DEVICE_GETTER)})
    rows.append({"kind": "chain", "key": "helper", "value": "%#010x" % PATH_HELPER,
                 "detail": "device name -> `<device>\\Profiles\\`; compares at `%#010x`" % DEVICE_COMPARE})
    rows.append({"kind": "chain", "key": "join", "value": "%#010x" % PATH_JOIN,
                 "detail": "`<helper> + profile + \\`, called twice from the loader `%#010x`" % LOADER})
    rows.append({"kind": "composed", "key": "prefix", "value": "\\Profiles\\",
                 "detail": "the device segment is empty, so the device name contributes no characters"})

    names = body_names()
    for site, kind in dword_refs(data, xbe.offset_to_va, 0x00508CD4, text_lo, text_hi):
        holder = containing(names, site)
        rows.append({"kind": "site", "key": "%#010x" % site, "value": kind,
                     "detail": "`%s`" % holder if holder else "no committed function spans it"})

    profile_dirs = set()
    for dirpath, _dirnames, filenames in os.walk(dump):
        for filename in sorted(filenames):
            if not filename.lower().endswith(SAVE_EXTENSIONS):
                continue
            full = os.path.join(dirpath, filename)
            rel = os.path.relpath(full, dump).replace(os.sep, "/")
            profile, name = split_shipped(rel)
            profile_dirs.add(profile)
            rows.append({"kind": "file", "key": name, "value": rel,
                         "detail": "%d bytes; loader composes `%s`" % (os.path.getsize(full), compose(profile, name))})
    for profile in sorted(p for p in profile_dirs if p):
        on_disc = [d for d in os.listdir(os.path.join(dump, "System", "Profiles"))] if os.path.isdir(
            os.path.join(dump, "System", "Profiles")) else []
        rows.append({"kind": "profile", "key": profile,
                     "value": "System/Profiles/%s" % profile,
                     "detail": "loader composes `\\Profiles\\%s\\`%s" % (
                         profile, "" if profile in on_disc else " (no such directory on the disc)")})

    for ini in sorted(f for f in os.listdir(os.path.join(dump, "System")) if f.lower().endswith(".ini")):
        with open(os.path.join(dump, "System", ini), errors="replace") as fh:
            value = savepath_of(fh.read())
        if value is None:
            continue
        target = os.path.normpath(os.path.join(dump, "System", value.replace("\\", "/")))
        rows.append({"kind": "ini", "key": ini, "value": value,
                     "detail": "`[Core.System] SavePath` -> `%s`: %s" % (
                         os.path.relpath(target, dump), "present" if os.path.isdir(target) else "not on the disc")})

    save_dirs = []
    for dirpath, dirnames, _files in os.walk(dump):
        for name in dirnames:
            if name.lower() in ("save", "saves"):
                save_dirs.append(os.path.relpath(os.path.join(dirpath, name), dump).replace(os.sep, "/"))
    rows.append({"kind": "dir", "key": "save", "value": str(len(save_dirs)),
                 "detail": "directories named Save/Saves: %s" % (", ".join(sorted(save_dirs)) or "none")})

    literals = wide_literals(data)
    for word in VOCABULARY:
        hits = [va for off, text_ in literals if word in text_ for va in [xbe.offset_to_va(off)] if va]
        rows.append({"kind": "word", "key": word, "value": str(len(hits)),
                     "detail": "wide literals containing it: %s" % (", ".join("%#010x" % h for h in hits[:6]) or "none")})
    slashy = [t for _off, t in literals if "\\" in t]
    rows.append({"kind": "word", "key": "backslash", "value": str(len(slashy)),
                 "detail": "of %d wide literals of 4+ characters carry a `\\`" % len(literals)})
    return rows


# ---------------------------------------------------------------- the page

def render(rows):
    def table(kind, headers, pick):
        lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
        for row in rows:
            if row["kind"] == kind:
                lines.append("| " + " | ".join(pick(row)) + " |")
        return lines

    out = []
    out += ["<!-- generated:save-path -->",
            "**The chain, read out of the image.** `%#010x` is the helper the loader calls with the device name; the"
            " device it asks for is the file manager's, and that getter answers the empty string."
            % PATH_HELPER, ""]
    out += table("chain", ["step", "address", "what it is"], lambda r: [r["key"], "`%s`" % r["value"], r["detail"]])
    out += ["", "**The literals the composition is made of**, each read back at its address:", ""]
    out += table("literal", ["literal", "address", "read back"], lambda r: ["`%s`" % r["key"], "`%s`" % r["value"], r["detail"]])
    out += ["", "**The device name**, at every site that pushes or stores it:", ""]
    out += table("site", ["site", "kind", "where"], lambda r: ["`%s`" % r["key"], r["value"], r["detail"]])
    out += ["", "**What the loader composes against what the disc holds** (`\\Profiles\\<profile>\\<name>`):", ""]
    out += table("file", ["file", "on the disc", "bytes", "loader composes"],
                 lambda r: ["`%s`" % r["key"], "`%s`" % r["value"], r["detail"].split(" ")[0],
                            r["detail"].split("loader composes ")[-1]])
    out += ["", "**The profiles the disc carries** -- the loader's middle segment:", ""]
    out += table("profile", ["profile", "on the disc", "loader composes"],
                 lambda r: ["`%s`" % r["key"], "`%s`" % r["value"], "`\\Profiles\\%s\\`" % r["key"]])
    out += ["", "**Save-directory vocabulary.** The ini's configured save directory, the directories that exist, and how"
                " often each word appears in the image's wide strings:", ""]
    out += table("ini", ["ini", "SavePath", "resolves to"], lambda r: ["`%s`" % r["key"], "`%s`" % r["value"], r["detail"]])
    out += table("dir", ["question", "count", "detail"], lambda r: [r["key"], r["value"], r["detail"]])
    out += table("word", ["word", "wide literals", "where"], lambda r: ["`%s`" % r["key"], r["value"], r["detail"]])
    out += ["<!-- /generated:save-path -->"]
    return "\n".join(out)


def splice(path, block):
    with open(path) as fh:
        text = fh.read()
    head, tail = BLOCK_MARK
    if head not in text or tail not in text:
        sys.exit("%s: markers %s .. %s not found" % (path, head, tail))
    before, rest = text.split(head, 1)
    _old, after = rest.split(tail, 1)
    return before + block + after


def write_doc(path, rows):
    new = splice(path, render(rows))
    with open(path, "w") as fh:
        fh.write(new)
    print("ok   save-path: wrote the generated block of %s" % path)


def check_doc(path, rows):
    new = splice(path, render(rows))
    with open(path) as fh:
        old = fh.read()
    if new != old:
        for n, (a, b) in enumerate(zip(new.splitlines(), old.splitlines()), 1):
            if a != b:
                print("FAIL save-path: %s line %d\n  page:  %r\n  table: %r" % (path, n, b, a))
                return 1
        print("FAIL save-path: %s length %d vs %d" % (path, len(new.splitlines()), len(old.splitlines())))
        return 1
    print("ok   save-path: %s matches the committed table" % path)
    return 0


# ---------------------------------------------------------------- the dry check

def selftest():
    failures = []
    ASSERTIONS = [0]

    def want(label, got, expect):
        ASSERTIONS[0] += 1
        if got != expect:
            failures.append("%s: %r != %r" % (label, got, expect))

    blob = ("abcd".encode("utf-16-le") + b"\x00\x00" + "cde\\".encode("utf-16-le") + b"\x00\x00")
    want("wide_literals", [t for _o, t in wide_literals(blob)], ["abcd", "cde\\"])
    want("wide_literals offset", wide_literals(blob)[1][0], 10)
    want("compose", compose("DefaultProfile", "C_M02B_Secondsymbol.sgs"),
         "\\Profiles\\DefaultProfile\\C_M02B_Secondsymbol.sgs")
    want("split_shipped", split_shipped("System/Profiles/sean/sean.sgp"), ("sean", "sean.sgp"))
    want("savepath_of", savepath_of("[Core.System]\r\nPaths=..\\System\\*.u\r\nSavePath=..\\Save\r\n"),
         "..\\Save")
    want("savepath_of absent", savepath_of("[Core.System]\nPaths=x\n"), None)
    # a dword reference is a push only when 0x68 sits in front of the immediate
    img = bytearray(b"\x90" * 8) + b"\x68" + struct.pack("<I", 0xDEADBEEF) + b"\x00" * 4
    want("dword_refs push", dword_refs(bytes(img), lambda off: 0x1000 + off, 0xDEADBEEF, 0x1000, 0x2000),
         [(0x1009, "push")])
    rows = [
        {"kind": "chain", "key": "device_getter", "value": "0x00034210", "detail": "33 bytes"},
        {"kind": "file", "key": "a.sgs", "value": "System/Profiles/P/a.sgs", "detail": "1 bytes; loader composes `x`"},
        {"kind": "word", "key": "Saves", "value": "1", "detail": "wide literals containing it: 0x1"},
    ]
    import re
    page = render(rows)
    want("render markers", (page.splitlines()[0], page.splitlines()[-1]), BLOCK_MARK)
    want("composed cell", "`System/Profiles/P/a.sgs`" in page, True)
    want("chain row once", page.count("device_getter"), 1)
    want("file row once", page.count("`a.sgs`"), 1)
    lines = page.splitlines()
    heads = [n for n, line in enumerate(lines) if line.startswith("| ") and (n == 0 or not lines[n - 1].startswith("|"))]
    want("every table has a header", all(n + 1 < len(lines) and lines[n + 1].startswith("|---|") for n in heads), True)
    want("no unfilled format", bool(re.search(r"%[sd]", page)), False)
    for line in page.splitlines():
        if line.startswith("|") and line.count("|") < 3:
            failures.append("ragged row: %r" % line)
    src = open(os.path.abspath(__file__)).read()
    for name in ("--check-doc", "--write-doc", "--check"):
        if name not in src:
            failures.append("docstring lost %s" % name)
    if failures:
        for line in failures:
            print("FAIL save-path: " + line)
        return 1
    print("ok   save-path: %d assertions, no dump" % ASSERTIONS[0])
    return 0


# ---------------------------------------------------------------- the CLI

def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("--dump")
    ap.add_argument("-o", "--out", default=CSV_PATH)
    ap.add_argument("--from-csv", default=CSV_PATH)
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    if args.check:
        return selftest()

    if args.write_doc or args.check_doc:
        rows = read_csv(args.from_csv)
        if args.check_doc:
            return check_doc(args.check_doc, rows)
        write_doc(args.write_doc, rows)
        return 0

    if not (args.xbe and args.dump):
        ap.error("--xbe and --dump are needed to measure (or --check / --write-doc with --from-csv)")
    rows = measure(args.xbe, args.dump)
    with open(args.out, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print("ok   save-path: %d rows -> %s" % (len(rows), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
