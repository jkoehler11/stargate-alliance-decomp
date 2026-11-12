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

**`--slots` reads the extent slot by slot**, which is what the `--sizefield` residue needs. The model widens *every*
index operand, so a copy can only come out shorter than the model in one way: a slot the loader left at its file
width. `--slots` aligns the model's spans to the guest bytes (an index operand free to be 1, 2 or 4 bytes) and names
the slots whose guest width equals their file width -- `-3` is one such slot, `-6` is two, `+9` is three one-byte
slots where the model counts four. With `--residue CSV` (from `uscript-dis.py --sizefield --residue-csv`) it reads
every residue row against each RAM image given and reports how many copies are the field's own number:

    python3 tools/uscript-dis.py --sizefield DUMP/System --residue-csv $WORK/residue.csv
    python3 tools/xemu/script-ram.py $WORK/*/ram.bin --dump DUMP --residue $WORK/residue.csv
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


def candidates(blob, pat, runs_n=8, limit=300):
    """Every start a literal run of the model puts forward, longest runs first.

    One anchor is not enough for a long script: `DisplayDebug` is 1,605 spans and its first three-byte literal run
    occurs in thousands of places, so a single-anchor search answered 15 bytes of agreement and looked like a
    missing copy. The alignment below is what decides, so this only has to *propose* starts.
    """
    found = set()
    for a, b in sorted(runs(pat), key=lambda r: -(r[1] - r[0]))[:runs_n]:
        needle = bytes(pat[a:b])
        if len(needle) < 3:
            continue
        pos = blob.find(needle)
        seen = 0
        while pos >= 0 and seen < limit:
            if pos - a >= 0:
                found.add(pos - a)
            seen += 1
            pos = blob.find(needle, pos + 1)
    return sorted(found)


def align(blob, at, body, spans, beam_width=8):
    """Longest alignment of the model's spans to guest bytes, an index operand free to be 1, 2 or 4 bytes.

    `(spans matched, guest length, slots, complete)`, where a slot is `(file offset, file width, guest width, op)` for
    every index operand the alignment consumed. The loader's widening is what the model predicts, so a slot whose
    guest width equals its *file* width is one the loader did **not** widen -- and that, not a rule about opcodes, is
    what a `--sizefield` residue row is: `-3` is one such slot (four predicted, one present), `-6` is two, and a
    `+9` row is three slots that are one byte where the model counts four.
    """
    best = (0, at, [], False)
    live = [(0, at, [])]
    while live:
        nxt = {}
        for i, g, path in live:
            if i >= len(spans):
                nxt.setdefault((len(spans), g), path)
                continue
            a, b, kind, op = spans[i]
            width = b - a
            if kind == "index":
                for w in (4, 1, 2):
                    nxt.setdefault((i + 1, g + w), path + [(a, width, w, kind, op)])
            elif blob[g:g + width] == body[a:b]:
                nxt.setdefault((i + 1, g + width), path + [(a, width, width, kind, op)])
        if not nxt:
            break
        live = [(i, g, p) for (i, g), p in sorted(nxt.items(), key=lambda kv: (-kv[0][0], kv[0][1]))[:beam_width]]
        if live[0][0] > best[0]:
            best = (live[0][0], live[0][1] - at, live[0][2], live[0][0] == len(spans))
        done = [x for x in live if x[0] == len(spans)]
        if done:
            top = sorted(done, key=lambda x: -len(x[2]))[0]
            return (len(spans), top[1] - at, top[2], True)
    return best


def narrow_slots(path):
    """The slots the guest did not widen: `(file offset, width, op)`, one per slot the loader left compact."""
    return [(a, width, op) for a, width, guest, kind, op in path if kind == "index" and width == guest]


def copy_for(blob, body, spans):
    """The longest alignment of one script's model in one dump: `(at, matched, guest length, path, complete)`."""
    pat, _marks = model(body, spans)
    best = (None, 0, 0, [], False)
    for at in candidates(blob, pat):
        matched, length, path, complete = align(blob, at, body, spans)
        if complete:
            return (at, matched, length, path, True)
        if matched > best[1]:
            best = (at, matched, length, path, False)
    return best


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


def slot_lines(at, matched, length, path, complete, field, body=None, dis=None):
    """The per-slot reading of one copy, as lines. Nothing here is game data: offsets, widths and opcodes."""
    narrow = narrow_slots(path)
    out = ["  copy at %#010x: %d span(s) aligned (%s), guest copy %d bytes, field %d (%+d)"
           % (at or 0, matched, "all" if complete else "some", length, field, field - length)]
    out.append("    %d index slot(s); the guest left %d at its file width: %s"
               % (len([r for r in path if r[3] == "index"]), len(narrow),
                  ", ".join("offset %d width %d op%02x" % row for row in narrow[:10]) or "none"))
    if narrow:
        out.append("    the field accounts for them exactly" if length == field else
                   "    but the field is %+d off the copy, so a slot is not the whole story" % (field - length))
    if body is not None and dis is not None:
        # The one property left that could decide a slot: its own value. A compact index carries `< 0x40` in one
        # byte, so a one-byte slot's value is small by construction -- printing it separates the slots that stay
        # narrow from the one-byte slots that widen.
        out.append("    every index slot, as `offset value width guest`:")
        cells = []
        for a, width, guest, kind, op in path:
            if kind != "index":
                continue
            try:
                value, _nxt = dis.read_index(body, a)
            except (IndexError, dis.struct.error):
                value = -1
            cells.append("%d %d %d%s%d%s" % (a, value, width, "->" if width != guest else "=", guest,
                                             " op%02x" % op))
        out.append("      " + "; ".join(cells))
    return out


def residue_report(csvpath, rams, dump, slots=False):
    """Every `--sizefield` residue row against every guest dump given: does a copy exist, and what explains it?"""
    import csv

    sys.path.insert(0, TOOLS)
    from ue2pkg import Package                                    # noqa: PLC0415 - only for a real package

    dis = _dis()
    table = dis.Table()
    with open(csvpath, newline="") as handle:
        rows = list(csv.DictReader(handle))
    blobs = []
    for ram in rams:
        with open(ram, "rb") as handle:
            blobs.append((os.path.basename(os.path.dirname(ram)) or ram, handle.read()))
    reads, agreed, disagreed, unexplained = 0, 0, [], []
    by_op = collections.Counter()
    per_pkg = {}
    rows_by_pkg = {}
    deepest = {}
    for row in rows:
        pkgname, cls, fn = row["package"], row["class"], row["function"]
        path = os.path.join(dump, "System", pkgname)
        if not os.path.exists(path):
            continue
        found = locate(dis, path, cls, fn)
        if not found:
            print("%-16s %-26s %-30s no located payload" % (pkgname, cls, fn))
            continue
        for body, field in found:
            try:
                spans = dis._spans_with_op(body, table)
            except (dis.Refused, IndexError, dis.struct.error) as exc:
                print("%-16s %-26s %-30s the walk refuses (%s)" % (pkgname, cls, fn, exc))
                continue
            best = (None, 0, 0, [], False)
            where = None
            for name, blob in blobs:
                got = copy_for(blob, body, spans)
                if got[1] > best[1] or got[4]:
                    best, where = got, name
                if got[4]:
                    break
            at, matched, length, aln, complete = best
            reads += 1
            per_pkg[pkgname] = per_pkg.get(pkgname, 0) + (1 if complete else 0)
            rows_by_pkg[pkgname] = rows_by_pkg.get(pkgname, 0) + 1
            deepest[pkgname] = max(deepest.get(pkgname, 0), matched)
            if not complete:
                print("%-16s %-26s %-30s field %-6d no complete copy: best %d of %d span(s) in %s"
                      % (pkgname, cls, fn, field, matched, len(spans), where))
                disagreed.append((pkgname, cls, fn))
                continue
            narrow = narrow_slots(aln)
            for _a, width, op in narrow:
                by_op["op%02x:w%d" % (op, width)] += 1
            print("%-16s %-26s %-30s field %-6d copy %-6d %+3d  %2d narrow slot(s)  %s"
                  % (pkgname, cls, fn, field, length, field - length, len(narrow),
                     " ".join("%d:w%d:op%02x" % row for row in narrow[:6])))
            if slots:
                for line in slot_lines(at, matched, length, aln, complete, field, body, dis):
                    print(line)
            if length == field:
                agreed += 1
            else:
                unexplained.append((pkgname, cls, fn, field, length))
    print("%d residue row(s) read from %s," % (reads, csvpath))
    print("  a guest copy aligns completely: %d;  its length IS the field: %d" % (reads - len(disagreed), agreed))
    print("  no complete copy: %d %s" % (len(disagreed), disagreed[:6]))
    print("  complete copy but the field disagrees: %d %s" % (len(unexplained), unexplained[:6]))
    print("  narrow slots by opcode and file width: %s" % dict(sorted(by_op.items())))
    print("  complete copies by package: %s" % dict(sorted(per_pkg.items())))
    # A package whose bodies no dump holds aligns 0 spans *anywhere*, which is a different negative from a body that
    # aligns hundreds and then diverges: the first is "not resident in these dumps", the second is a real divergence.
    print("  per package: rows / complete copies / deepest alignment in spans: %s"
          % {k: (rows_by_pkg[k], per_pkg.get(k, 0), deepest.get(k, 0)) for k in sorted(rows_by_pkg)})
    return 0 if reads and agreed else 1


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
    # The slot read: a guest that leaves the index at two bytes is a complete alignment two bytes shorter than the
    # model, and the slot it names is the one the loader did not widen.
    matched, length, aln, complete = align(b"\xA5" * 21 + exact + b"\xA5" * 21, 21, body, spans)
    if not complete or length != len(exact) or narrow_slots(aln):
        print("FAIL script-ram: a fully widened copy is %d byte(s) at %d span(s), narrow %r"
              % (length, matched, narrow_slots(aln)))
        return 1
    idx = [s for s in spans if s[2] == "index"]
    if len(idx) != 1:
        print("FAIL script-ram: the synthetic body has %d index operand(s), expected 1" % len(idx))
        return 1
    a0, b0, _kind, op0 = idx[0]
    short = exact[:a0] + exact[a0:a0 + 2] + exact[a0 + 4:]
    matched, length, aln, complete = align(b"\xA5" * 21 + short + b"\xA5" * 21, 21, body, spans)
    want = [(a0, b0 - a0, op0)] if b0 - a0 == 2 else []
    if not complete or length != len(exact) - 2 or narrow_slots(aln) != want:
        print("FAIL script-ram: a two-byte index slot read as %d byte(s), narrow %r, expected %d / %r"
              % (length, narrow_slots(aln), len(exact) - 2, want))
        return 1
    print("ok script-ram: a copy that leaves an index at its file width aligns completely, %d bytes short, and names "
          "the slot" % (len(exact) - length))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ram", nargs="*", help="a `pmemsave` image of guest RAM (several, with --residue)")
    ap.add_argument("--dump", help="the dump root, so the package can be read from it")
    ap.add_argument("--pkg", default="Engine.u", help="package file name under System/ (default Engine.u)")
    ap.add_argument("--class", dest="cls", help="class name, e.g. StreamPlaylist")
    ap.add_argument("--function", help="function name, e.g. AddStream")
    ap.add_argument("--max-hits", type=int, default=6, help="copies to report (default 6)")
    ap.add_argument("--slots", action="store_true",
                    help="align the copy span by span and read each index operand's guest width")
    ap.add_argument("--residue", metavar="CSV",
                    help="every `uscript-dis.py --sizefield --residue-csv` row, read against each RAM image given")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; needs no dump and writes nothing")
    args = ap.parse_args()

    if args.check:
        return selftest()
    if args.residue:
        if not (args.ram and args.dump):
            ap.error("--residue needs at least one RAM image and --dump")
        return residue_report(args.residue, args.ram, args.dump, args.slots)
    if not (args.ram and args.dump and args.cls and args.function):
        ap.error("a search needs RAM, --dump, --class and --function (or --check)")
    args.ram = args.ram[0]

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
        if args.slots:
            at, matched, length, aln, complete = copy_for(blob, body, spans)
            for line in slot_lines(at, matched, length, aln, complete, field, body, dis):
                print(line)
            continue
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
