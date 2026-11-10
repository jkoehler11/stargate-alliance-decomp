#!/usr/bin/env python3
"""What the library sections can say about themselves (PLAN 4.2, docs/UNKNOWNS.md U9).

`docs/IMPORTS.md` sizes 4.2's signature gap: five library sections plus `XNET` and `XPP` hold **3,109
functions and 565 KB** of statically linked Xbox SDK code, 22 of them named. 4.2 would name the rest from
MSVC/Xbox-SDK signature data this repository may not carry, so the question this tool answers is the one
that decides whether *any* of it can be named from the image alone: **does a library body carry its own
evidence?**

Every body is read in place -- no decompile -- and four readings are taken:

* **string operands** -- a 32-bit VA inside the body that lands on a printable ASCII string. Two counts, because
  they are not the same measurement: `str_dwords` is any dword (so a data table's pointer counts), and
  `push_str` is the strict form, a dword immediately preceded by `0x68` (`push imm32`), which is how a
  literal reaches an assert or a log call.
* **a source path** -- a string operand matching a build-tree path (`...\\d3d8\\device.cpp`). This is the
  evidence that names a translation unit in the engine modules, so its presence or absence is the answer.
* **a wide literal** -- a dword landing on a UTF-16LE run of printable ASCII (MSVC's `L"..."`).
* **direct calls** -- `E8 rel32` targets resolved against the function map: a call to a known body, a call
  that leaves the section (the library calling back into `.text`), and a call that lands on no known body.

    library-bodies.py XBE -o inventory/library_bodies.csv --write-doc docs/LIBRARIES.md
    library-bodies.py --report                  # the generated tables, to stdout
    library-bodies.py --check                   # synthetic image, no dump, writes nothing
"""
import argparse
import csv
import os
import re
import shutil
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(ROOT, "inventory", "library_bodies.csv")
SYMBOLS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
DOC = os.path.join(ROOT, "docs", "LIBRARIES.md")
DOC_MARK = ("<!-- generated:library-bodies -->", "<!-- /generated:library-bodies -->")

# The library sections the function map attributes bodies in (docs/IMPORTS.md). `XON_RD` and `DOLBY`
# hold code too, but the map names no body in either; the report says so in prose instead of printing a
# row of zeroes, and the page's extents come from `xbe.py ... sections`.
SECTIONS = ["XNET", "XONLINE", "D3D", "D3DX", "XGRPH", "DSOUND", "XPP"]

COLUMNS = ["section", "address", "name", "size", "named", "thunk", "str_dwords", "push_str", "paths",
           "wide", "text", "calls", "call_known", "call_out", "call_unknown", "strings"]

PATH = re.compile(r"(?:[A-Za-z]:)?[\\/][^\x00\"]{2,200}\.(?:cpp|hpp|cxx|h|c|asm)$", re.I)
ADDR_NAME = re.compile(r"^(?:thunk_)?FUN_[0-9a-f]{8}$")


def die(msg):
    sys.stderr.write(msg.rstrip() + "\n")
    sys.exit(1)


# -- the image, real or synthetic ------------------------------------------
class Sec:
    __slots__ = ("name", "va", "vsize", "foff", "fsize")

    def __init__(self, name, va, vsize, foff, fsize):
        self.name, self.va, self.vsize, self.foff, self.fsize = name, va, vsize, foff, fsize

    def contains(self, va):
        return self.va <= va < self.va + self.vsize


class Image:
    def __init__(self, data, base, sections):
        self.data, self.base, self.sections = data, base, sections

    @classmethod
    def from_xbe(cls, path):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from xbe import Xbe  # noqa: PLC0415 - only when a dump is given
        x = Xbe(path)
        return cls(x.data, x.base, [Sec(s.name, s.va, s.vsize, s.foff, s.fsize) for s in x.sections])

    def section_for(self, va):
        for s in self.sections:
            if s.contains(va):
                return s
        return None

    def offset(self, va):
        s = self.section_for(va)
        if s is None:
            return None
        off = s.foff + (va - s.va)
        return off if 0 <= off < len(self.data) else None

    def read(self, va, size):
        off = self.offset(va)
        if off is None:
            return b""
        return self.data[off:off + size]


def cstr_at(img, va, limit=240):
    off = img.offset(va)
    if off is None:
        return None
    end = img.data.find(b"\x00", off, min(off + limit, len(img.data)))
    if end < 0 or end - off < 4:
        return None
    raw = img.data[off:end]
    if any(b < 0x20 or b > 0x7e for b in raw):
        return None
    return raw.decode("ascii", "replace")


def wstr_at(img, va, limit=120):
    off = img.offset(va)
    if off is None:
        return None
    out = []
    for i in range(limit):
        o = off + 2 * i
        if o + 1 >= len(img.data):
            break
        lo, hi = img.data[o], img.data[o + 1]
        if lo == 0 and hi == 0:
            break
        if hi != 0 or lo < 0x20 or lo > 0x7e:
            return None
        out.append(chr(lo))
    return "".join(out) if len(out) >= 4 else None


# -- one body --------------------------------------------------------------
def scan_body(img, va, size, starts):
    """Four readings for one body: string operands, source paths, wide literals, direct calls."""
    body = img.read(va, size)
    strict, loose, wide = set(), set(), set()
    for i in range(len(body) - 4):
        p = struct.unpack_from("<I", body, i)[0]
        if p < img.base or img.section_for(p) is None:
            continue
        s = cstr_at(img, p)
        if s:
            loose.add(s)
            if i and body[i - 1] == 0x68:
                strict.add(s)
            continue
        w = wstr_at(img, p)
        if w:
            wide.add(w)
    # A pointer into the middle of a string yields its tail (`rror`); keep the longest form only.
    def dedupe(values):
        keep = []
        for s in sorted(values, key=lambda t: (-len(t), t)):
            if not any(k.endswith(s) for k in keep):
                keep.append(s)
        return sorted(keep)

    loose, strict, wide = dedupe(loose), dedupe(strict), dedupe(wide)
    paths = sorted({s for s in loose if PATH.match(s)})
    text = sorted(s for s in loose if s not in set(paths))
    calls = known = out = unknown = 0
    own = img.section_for(va)
    for i in range(len(body) - 5):
        if body[i] != 0xE8:
            continue
        rel = struct.unpack_from("<i", body, i + 1)[0]
        tgt = va + i + 5 + rel
        calls += 1
        if tgt in starts:
            known += 1
            s = img.section_for(tgt)
            if own is not None and s is not None and s.name != own.name:
                out += 1
        elif img.section_for(tgt) is not None:
            unknown += 1
    return {
        "length": size, "str_dwords": len(loose), "push_str": len(strict),
        "paths": paths, "wide": len(wide), "text": text,
        "calls": calls, "call_known": known, "call_out": out, "call_unknown": unknown,
        "strings": loose,
    }


def read_symbols(path=SYMBOLS):
    """(rows of the function map, {address: name}, {address: size})."""
    rows, names = [], {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            va = int(r["address"], 16)
            rows.append({"address": va, "name": r["name"], "size": int(r["size"] or 0)})
            names[va] = r["name"]
    return rows, names


def read_bodies(img, sections=SECTIONS, symbols=SYMBOLS):
    rows, names = read_symbols(symbols)
    starts = {r["address"] for r in rows if r["size"] > 0}
    rows.sort(key=lambda r: r["address"])
    out = []
    for r in rows:
        sec = img.section_for(r["address"])
        if sec is None or sec.name not in sections or r["size"] <= 0:
            continue
        scan = scan_body(img, r["address"], r["size"], starts)
        out.append({
            "section": sec.name, "address": "0x%08x" % r["address"], "_va": r["address"],
            "name": r["name"], "size": r["size"],
            "named": 0 if ADDR_NAME.match(r["name"]) else 1,
            "thunk": 1 if r["name"].startswith("thunk_FUN_") else 0,
            "str_dwords": scan["str_dwords"], "push_str": scan["push_str"],
            "paths": len(scan["paths"]), "wide": scan["wide"], "text": len(scan["text"]),
            "calls": scan["calls"], "call_known": scan["call_known"], "call_out": scan["call_out"],
            "call_unknown": scan["call_unknown"],
            "strings": ";".join(scan["strings"])[:2000],
            "_paths": scan["paths"], "_text": scan["text"],
        })
    return out


# -- artifact --------------------------------------------------------------
def write_csv(rows, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


NUMERIC = ("size", "named", "thunk", "str_dwords", "push_str", "paths", "wide", "text", "calls",
           "call_known", "call_out", "call_unknown")


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for c in NUMERIC:
            r[c] = int(r[c] or 0)
        r["_va"] = int(r["address"], 16)
        r["_paths"] = [s for s in []]
        r["_text"] = []
    return rows


# -- report ----------------------------------------------------------------
def report(rows):
    out = ["| section | functions | named | `thunk_` | bytes | any literal | `push` literal | "
           "source path | wide | calls | into the section | leaves it | no known body |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    totals = dict(fns=0, named=0, thunk=0, byt=0, lit=0, push=0, path=0, wide=0, calls=0, known=0,
                  out=0, unk=0)
    for sec in SECTIONS:
        rs = [r for r in rows if r["section"] == sec]
        if not rs:
            out.append("| `%s` | 0 | 0 | 0 | 0 | -- | -- | -- | -- | -- | -- | -- | -- |" % sec)
            continue
        byt = sum(r["size"] for r in rs)
        vals = [len(rs), sum(r["named"] for r in rs), sum(r["thunk"] for r in rs), byt,
                sum(1 for r in rs if r["str_dwords"]), sum(1 for r in rs if r["push_str"]),
                sum(1 for r in rs if r["paths"]), sum(1 for r in rs if r["wide"]),
                sum(r["calls"] for r in rs), sum(r["call_known"] for r in rs),
                sum(r["call_out"] for r in rs), sum(r["call_unknown"] for r in rs)]
        out.append("| `%s` | " % sec + " | ".join(str(v) for v in vals) + " |")
        totals["fns"] += vals[0]; totals["named"] += vals[1]; totals["thunk"] += vals[2]
        totals["byt"] += vals[3]
        totals["lit"] += vals[4]; totals["push"] += vals[5]; totals["path"] += vals[6]
        totals["wide"] += vals[7]; totals["calls"] += vals[8]; totals["known"] += vals[9]
        totals["out"] += vals[10]; totals["unk"] += vals[11]
    out.append("| **all library sections** | **%d** | **%d** | **%d** | **%d** | **%d** | **%d** | **%d** | "
               "**%d** | **%d** | **%d** | **%d** | **%d** |"
               % (totals["fns"], totals["named"], totals["thunk"], totals["byt"], totals["lit"],
                  totals["push"], totals["path"], totals["wide"], totals["calls"], totals["known"],
                  totals["out"], totals["unk"]))
    out.append("")
    out.append("Of the **%d** names the map carries in these sections, **%d** are `thunk_` jump stubs and "
               "**%d** is a real C++ name, so **%d** of the %d bodies is semantically named."
               % (totals["named"] + totals["thunk"], totals["thunk"], totals["named"], totals["named"],
                  totals["fns"]))
    out.append("")
    out.append("**%d of %d** library bodies reference any string at all (**%d** through a `push imm32`, "
               "**%d** only through a data-table dword), **%d** reference a build-tree source path, and "
               "**%d** reference a wide literal. Direct calls resolve to a known body %d times, %d of them "
               "from a body in another section."
               % (totals["lit"], totals["fns"], totals["push"], totals["lit"] - totals["push"],
                  totals["path"], totals["wide"], totals["known"], totals["out"]))

    # The literals themselves, deduplicated across bodies: this is all the vocabulary there is.
    seen = {}
    for r in rows:
        for s in filter(None, (r.get("strings") or "").split(";")):
            if len(s) < 4:
                continue
            e = seen.setdefault(s, {"sections": set(), "bodies": 0})
            e["sections"].add(r["section"])
            e["bodies"] += 1
    out.append("")
    out.append("`XON_RD` and `DOLBY` also hold code (`tools/xbe.py DUMP/default.xbe sections` prints their "
               "extents) but the function map names no body in either, so nothing there was scanned.")
    if seen:
        out.append("The library sections' whole string vocabulary: **%d** distinct strings, top 60 by "
                   "how many bodies reference them." % len(seen))
        out.append("")
        out.append("| literal | sections | bodies |")
        out.append("|---|---|---:|")
        for s in sorted(seen, key=lambda k: (-seen[k]["bodies"], -len(k), k))[:60]:
            e = seen[s]
            out.append("| `%s` | %s | %d |" % (s.replace("|", "\\|"), ", ".join(sorted(e["sections"])),
                                               e["bodies"]))
        rest = len(seen) - 60
        if rest > 0:
            out.append("")
            out.append("*...and %d more distinct strings in `inventory/library_bodies.csv`.*" % rest)
    else:
        out.append("No string operand resolves to a printable string in any library body.")
    return "\n".join(out)


def write_doc(text, path):
    begin, end = DOC_MARK
    doc = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    if begin not in doc or end not in doc:
        die("%s: no %s ... %s block" % (path, begin, end))
    head, rest = doc.split(begin, 1)
    _, tail = rest.split(end, 1)
    new = head + begin + "\n" + text + "\n" + end + tail
    if new != doc:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new)
        return 1
    return 0


# -- the contract ----------------------------------------------------------
def check():
    """Scan a synthetic image and reconcile every reading. No dump, no writes."""
    base = 0x10000
    text = bytearray(b"\xcc" * 0x200)
    # section layout: .text at base, .rdata at base+0x1000
    sections = [Sec(".text", base, 0x200, 0x1000, 0x200), Sec("D3D", base + 0x1000, 0x200, 0x1200, 0x200)]
    data = bytearray(b"\0" * 0x1000) + text + bytearray(b"\0" * 0x200)
    rdata = base + 0x1000          # the start of the D3D section, i.e. file offset 0x1200

    def put_str(off, s):
        data[0x1200 + off:0x1200 + off + len(s)] = s

    put_str(0x00, b"c:\\alienbrainWork\\Game\\GameXBOX\\D3D\\Src\\Device.cpp\0")
    put_str(0x50, b"CreateDevice failed\0")
    # a UTF-16 literal at .rdata+0x90
    wide = "Version".encode("utf-16-le") + b"\0\0"
    data[0x1200 + 0x90:0x1200 + 0x90 + len(wide)] = wide
    # body at base: push <path>; push <text>; call rel32 to base+0x20; mov eax, imm32 wide; ret
    body_va = base + 0x10
    code = bytearray()
    code += b"\x68" + struct.pack("<I", rdata + 0x00)
    code += b"\x68" + struct.pack("<I", rdata + 0x50)
    code += b"\xb8" + struct.pack("<I", rdata + 0x90)
    code += b"\xe8" + struct.pack("<i", (base + 0x1100) - (body_va + len(code) + 5))
    code += b"\xc3"
    data[0x1000 + 0x10:0x1000 + 0x10 + len(code)] = code
    img = Image(bytes(data), base, sections)
    rows = [
        {"address": base + 0x10, "name": "FUN_00010010", "size": len(code)},
        {"address": base + 0x1100, "name": "D3D_Helper", "size": 1},
    ]
    starts = {r["address"] for r in rows}
    scan = scan_body(img, base + 0x10, len(code), starts)
    bad = []
    if scan["str_dwords"] != 2:
        bad.append("str_dwords %d, want 2 (ASCII only: the path and the message)" % scan["str_dwords"])
    if scan["push_str"] != 2:
        bad.append("push_str %d, want 2 (both pushes, not the mov)" % scan["push_str"])
    if len(scan["paths"]) != 1 or not scan["paths"][0].endswith("Device.cpp"):
        bad.append("paths %r" % (scan["paths"],))
    if scan["wide"] != 1:
        bad.append("wide %d, want 1" % scan["wide"])
    if scan["text"] != ["CreateDevice failed"]:
        bad.append("text %r" % (scan["text"],))
    if scan["calls"] != 1 or scan["call_known"] != 1 or scan["call_out"] != 1:
        bad.append("calls %r" % {k: scan[k] for k in ("calls", "call_known", "call_out")})
    # the report must render without a real artifact and name the zero-function section
    try:
        text_out = report([])
    except Exception as exc:  # noqa: BLE001 - the contract is that it cannot raise
        bad.append("report raised %r" % (exc,))
    else:
        if "`XON_RD`" not in text_out or "| `D3D` |" not in text_out:
            bad.append("report did not render a section row")
    if bad:
        for b in bad:
            sys.stderr.write("FAIL %s\n" % b)
        return 1
    print("library-bodies: synthetic body scanned (push/data-table split, path, wide, calls) and an "
          "empty-section row rendered")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xbe", nargs="?", help="default.xbe (omit only for --report/--check)")
    ap.add_argument("-o", "--out", default=CSV_PATH)
    ap.add_argument("--symbols", default=SYMBOLS)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        return check()
    if a.check_doc:
        begin, end = DOC_MARK
        doc = open(a.check_doc, encoding="utf-8").read()
        if begin not in doc or end not in doc:
            die("%s: no %s ... %s block" % (a.check_doc, begin, end))
        got = doc.split(begin, 1)[1].split(end, 1)[0].strip("\n")
        want = report(read_csv(a.out))
        if got != want:
            sys.stderr.write("FAIL %s: the generated block differs from %s "
                             "(run --write-doc %s)\n" % (a.check_doc, a.out, a.check_doc))
            return 1
        print("library-bodies: %s matches %s" % (a.check_doc, os.path.basename(a.out)))
        return 0
    if a.report:
        print(report(read_csv(a.out)))
        return 0
    if not a.xbe:
        die("library-bodies: give default.xbe (or --report / --check)")
    img = Image.from_xbe(a.xbe)
    rows = read_bodies(img, SECTIONS, a.symbols)
    write_csv(rows, a.out)
    text = report(rows)
    if a.write_doc:
        write_doc(text, a.write_doc)
    print("library-bodies: %d bodies in %d sections -> %s" % (len(rows), len(SECTIONS), a.out))
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
