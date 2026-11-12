#!/usr/bin/env python3
"""Find a shipped function's bytecode inside a guest RAM dump, and measure the copy's extent (U13, PLAN 3.7).

`tools/uscript-dis.py --sizefield` measures the dword 16 bytes before every script as the script's length with every
compact index operand widened to four bytes: exact in **11,485 of 11,602** walked payloads (98.99%). The residue's
commonest shape is **`+4`** -- the field counts four bytes the file does not have. Two explanations survive a
file-side reading, and only a guest copy separates them:

  (a) the memory image is the widened model (`StreamPlaylist.AddStream` is 196 bytes) and the field counts four bytes
      *beyond* the script's end -- something the loader appends, or a field that is not this extent at all;
  (b) the memory image is 200 bytes, i.e. two operands are widened there that the opcode table does not model.

The instrument is a pattern search with wildcards, not another guess about the file. The loader rewrites a compact
index operand into a **four-byte slot**, so opcodes and fixed operands are byte-exact and every index operand is four
`??` bytes (`docs/BYTECODE.md`; measured on `StargateHUD.DrawOrderTarget`, `b8 2a` in the package and `a0 1e 26 01`
in the guest). The copy is found from a prefix anchor, its start is fixed by that match, and the epilogue's own
position in the dump then says how long the guest copy is: `196` confirms the model and leaves the field counting
four bytes past the end, `200` means two widenings the table does not have -- and the first disagreement's offset and
operand kind name which tokens they are.

    python3 tools/xemu/script-ram.py RAMBIN --dump DUMP --pkg Engine.u --class StreamPlaylist --function AddStream

A dump is guest data: it stays on the desktop, is never committed, and this prints offsets, operand kinds and counts,
the rule the other `tools/xemu` readers follow.
"""
import argparse
import collections
import importlib.util
import os
import sys

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EPILOGUE = bytes([0x04, 0x0B, 0x47])
NEAR = 24               # how far past the model's own end the copy's epilogue may sit


def _dis():
    spec = importlib.util.spec_from_file_location("uscript_dis", os.path.join(TOOLS, "uscript-dis.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def locate(dis, pkg_path, want_class, want_function):
    """(body, field) for every Function export of one class, by the alignment `--sizefield` uses.

    The class is the export's `Outer` named through the package, not `class_name_of(outer)`, which answers the
    metaclass (`Class`) for every function in the package.
    """
    sys.path.insert(0, TOOLS)
    from ue2pkg import Package                                   # noqa: PLC0415 - only for a real package

    pkg = Package(pkg_path)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    found = []
    for e in pkg.exports:
        if pkg.class_name_of(e) != "Function" or e["outer"] <= 0:
            continue
        owner = pkg.exports[e["outer"] - 1]
        if pkg.name(owner["name"]).lower() != want_class.lower():
            continue
        if pkg.name(e["name"]).lower() != want_function.lower():
            continue
        pay = pkg.data[e["offset"]:e["offset"] + e["size"]]
        n = len(pay)
        off, last, aligned = 0, None, False
        for _ in range(8):
            try:
                last, off = dis.read_index(pay, off)
            except IndexError:
                break
            if last == e["name"]:
                aligned = True
                break
        if not aligned:
            continue
        if n >= 10 and pay[n - 10:n - 7] == EPILOGUE:
            end = n - 7
        elif n >= 12 and pay[n - 12:n - 9] == EPILOGUE:
            end = n - 9
        else:
            continue
        start = off + 16
        if start > end:
            continue
        found.append((pay[start:end], dis.struct.unpack_from("<I", pay, off + 12)[0]))
    return found


def model(body, spans):
    """The script's memory image: opcodes and fixed operands exact, each index operand four `None` bytes."""
    pat, marks = [], []
    for a, b, kind, op in spans:
        marks.append((a, len(pat), kind, op))
        pat.extend([None] * 4 if kind == "index" else body[a:b])
    return pat, marks


def runs(pat):
    """(start, end) of every maximal run of known bytes."""
    out, start = [], None
    for i, byte in enumerate(pat):
        if byte is None:
            if start is not None:
                out.append((start, i))
                start = None
        elif start is None:
            start = i
    if start is not None:
        out.append((start, len(pat)))
    return out


def matched(blob, at, pat):
    """How many bytes of the model agree at `at` from the model's start, stopping at the first disagreement."""
    for i, byte in enumerate(pat):
        if at + i >= len(blob):
            return i
        if byte is not None and blob[at + i] != byte:
            return i
    return len(pat)


def epilogue_near(blob, at, pat):
    """The copy's epilogue: the first `04 0b 47` at a plausible offset from `at`, and where the model puts it."""
    want = at + len(pat) - 3
    lo = max(at, want - 8)
    for probe in range(lo, min(len(blob), want + NEAR + 1)):
        if blob[probe:probe + 3] == EPILOGUE:
            return probe
    return None


def search(blob, pat, limit=6):
    """Copies of the model in a dump, anchored on the model's first usable literal run.

    The anchor is a prefix run on purpose: a copy that carries *more* bytes than the model still agrees from its
    start, and only a prefix anchor keeps that alignment, so the first disagreement's offset is the thing that names
    where the guest differs. A candidate is kept only when the epilogue also sits near where the model puts it -- two
    points rather than one, because a single short literal run matches thousands of places in a 128 MiB image.
    """
    spans = runs(pat)
    if not spans:
        return [], 0, []
    anchor = next((run for run in spans if run[1] - run[0] >= 3), max(spans, key=lambda r: r[1] - r[0]))
    needle = bytes(pat[anchor[0]:anchor[1]])
    full, partial, seen = [], [], 0
    pos = blob.find(needle)
    while pos >= 0 and seen < 4000:
        at = pos - anchor[0]
        if at >= 0:
            got = matched(blob, at, pat)
            where = epilogue_near(blob, at, pat)
            if where is not None:
                if got == len(pat) and where == at + len(pat) - 3:
                    full.append(at)
                else:
                    partial.append((at, got, where))
        seen += 1
        pos = blob.find(needle, pos + 1)
    partial.sort(key=lambda row: -row[1])
    return full, anchor[1] - anchor[0], partial[:limit]


def kind_at(marks, offset):
    """The operand kind the model expects at a file offset -- names what the copy disagreed with."""
    last = marks[0]
    for row in marks:
        if row[0] > offset:
            break
        last = row
    return "%s of op%02x" % (last[2], last[3])


def selftest():
    """Dump-free: the model must widen an index operand, find the copy it built, and report a longer one as longer."""
    dis = _dis()
    table = dis.Table()
    body = bytes([0x1B, 0x41, 0x02, 0x27, 0x16, 0x04, 0x1E, 0x00, 0x00, 0x80, 0x3F]) + EPILOGUE
    try:
        spans = dis._spans_with_op(body, table)
    except dis.Refused as exc:
        print("FAIL script-ram: the table refuses a synthetic body: %s" % exc)
        return 1
    pat, _marks = model(body, spans)
    if pat.count(None) != 4 or len(pat) != len(body) + 2:
        print("FAIL script-ram: model is %d bytes with %d wildcard(s), expected %d with 4"
              % (len(pat), pat.count(None), len(body) + 2))
        return 1
    exact = bytes(0x5A if byte is None else byte for byte in pat)
    full, anchor, partial = search(b"\xA5" * 21 + exact + b"\xA5" * 21, pat, 4)
    if full != [21] or partial or anchor < 3:
        print("FAIL script-ram: %r full / %r partial (anchor %d), expected [21] / []"
              % (full, partial, anchor))
        return 1
    # A copy two bytes longer at a modelled index operand: still prefix-aligned, so the epilogue 2 bytes late is
    # what says the guest carries bytes the model does not.
    fat = bytes(0x5A if byte is None else byte for byte in pat[:4]) + b"\x11\x11" + exact[4:]
    full, _anchor, partial = search(b"\xA5" * 21 + fat + b"\xA5" * 21, pat, 4)
    if full or not partial or partial[0][2] != 21 + len(pat) - 3 + 2:
        print("FAIL script-ram: a two-byte-longer copy read as %r full / %r partial" % (full, partial))
        return 1
    print("ok script-ram: model widens the index operand; one copy found at the model's length and a two-byte-longer "
          "copy reported as +2")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ram", nargs="?", help="a `pmemsave` image of guest RAM")
    ap.add_argument("--dump", help="the dump root, so the package can be read from it")
    ap.add_argument("--pkg", default="Engine.u", help="package file name under System/ (default Engine.u)")
    ap.add_argument("--class", dest="cls", help="class name, e.g. StreamPlaylist")
    ap.add_argument("--function", help="function name, e.g. AddStream")
    ap.add_argument("--max-hits", type=int, default=6, help="copies to report (default 6)")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; needs no dump and writes nothing")
    args = ap.parse_args()

    if args.check:
        return selftest()
    if not (args.ram and args.dump and args.cls and args.function):
        ap.error("a search needs RAM, --dump, --class and --function (or --check)")

    dis = _dis()
    table = dis.Table()
    system = os.path.join(args.dump, "System")
    path = os.path.join(system, args.pkg) if os.path.isdir(system) else args.pkg
    if not os.path.exists(path):
        print("no such package: %s" % path)
        return 1
    with open(args.ram, "rb") as handle:
        blob = handle.read()
    rows = locate(dis, path, args.cls, args.function)
    if not rows:
        print("%s %s.%s: no Function export with a located epilogue" % (args.pkg, args.cls, args.function))
        return 1
    for body, field in rows:
        try:
            spans = dis._spans_with_op(body, table)
        except (dis.Refused, IndexError, dis.struct.error) as exc:
            print("%s %s.%s: script %d -- the walk refuses (%s), so there is no model"
                  % (args.pkg, args.cls, args.function, len(body), exc))
            continue
        widths = [b - a for a, b, kind, _op in spans if kind == "index"]
        pat, marks = model(body, spans)
        print("%s %s.%s: script %d bytes, %d index operand(s) by width %s"
              % (args.pkg, args.cls, args.function, len(body), len(widths),
                 dict(sorted(collections.Counter(widths).items()))))
        print("  model %d bytes (script + %d widening(s)); field at name_end+12 = %d, predicted %d, %+d"
              % (len(pat), sum(4 - w for w in widths), field, len(pat), field - len(pat)))
        full, anchor, partial = search(blob, pat, args.max_hits)
        print("  search: %d byte(s) of RAM, %d-byte anchor; %d copy(ies) at the model's length, %d near miss(es)"
              % (len(blob), anchor, len(full), len(partial)))
        for at in full:
            print("    copy at %#010x: the whole model matches and the epilogue sits at +%d -- the guest copy is "
                  "%d bytes, so the field's %+d counts bytes past its end"
                  % (at, len(pat) - 3, len(pat), field - len(pat)))
            print("      bytes after the script's end: %s" % blob[at + len(pat):at + len(pat) + 16].hex())
        for at, got, where in partial:
            length = where + 3 - at
            print("    copy at %#010x: %d of %d model byte(s) agree, then %s; epilogue at +%d, so the copy is "
                  "%d bytes (%+d the model)"
                  % (at, got, len(pat), kind_at(marks, got), where - at, length, length - len(pat)))
            lo = max(0, at + got - 4)
            print("      around the disagreement: model %s / guest %s"
                  % (bytes(0x2E if b is None else b for b in pat[max(0, got - 4):got + 8]).hex(),
                     blob[lo:lo + 12].hex()))
            print("      bytes past the model's end: %s" % blob[at + len(pat):at + len(pat) + 16].hex())
    return 0


if __name__ == "__main__":
    sys.exit(main())
